import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.runtime_config import RuntimeConfigStore
from app.schemas import (
    CheckStatus,
    ClaimResult,
    LlmVerifyResponse,
    OcrResponse,
    ScrapedPage,
    ServiceHealth,
    Verdict,
)

logger = logging.getLogger("gateway.services")

# Receives progress events of a running check ({"stage": "claims", ...}); used by the /stream routes.
ProgressListener = Callable[[dict[str, Any]], Awaitable[None]]

MOCK_NOTE = "Odpowiedź testowa (tryb MOCK) – serwis weryfikacji nie jest podłączony. To NIE jest wynik sprawdzenia."


class DownstreamError(Exception):
    def __init__(self, service: str, code: str, message: str, status: CheckStatus = CheckStatus.error) -> None:
        super().__init__(message)
        self.service = service
        self.code = code
        self.message = message
        self.status = status


class PageUnreadable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Downstream:
    def __init__(self, settings: Settings, runtime: RuntimeConfigStore, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.runtime = runtime
        self.client = httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, transport=transport)

    async def close(self) -> None:
        await self.client.aclose()

    @property
    def services(self) -> dict[str, str]:
        return {
            "llm": self.settings.llm_service_url.rstrip("/"),
            "page_scrapper": self.settings.page_scrapper_url.rstrip("/"),
            "image_recognition": self.settings.image_recognition_url.rstrip("/"),
        }

    def is_mock(self, service: str) -> bool:
        return self.runtime.get().mock_mode or not self.services[service]

    async def _post(self, service: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.services[service]}{path}"
        try:
            resp = await self.client.post(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise DownstreamError(service, "timeout", f"Serwis '{service}' nie odpowiedział w wyznaczonym czasie.") from exc
        except httpx.RequestError as exc:
            raise DownstreamError(service, "service_unavailable", f"Serwis '{service}' jest niedostępny.") from exc

        if resp.status_code in (402, 429):
            raise DownstreamError(
                service, "api_limit", f"Serwis '{service}' zgłosił limit lub brak dostępu do API.", CheckStatus.api_limit
            )
        if resp.status_code >= 400:
            logger.warning("%s %s -> %s: %s", service, path, resp.status_code, resp.text[:500])
            raise DownstreamError(service, "upstream_error", f"Serwis '{service}' zwrócił błąd {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise DownstreamError(service, "bad_response", f"Serwis '{service}' zwrócił niepoprawną odpowiedź.") from exc

    async def verify(
        self, payload: dict[str, Any], progress: ProgressListener | None = None
    ) -> tuple[LlmVerifyResponse, dict[str, Any] | None, bool]:
        if self.is_mock("llm"):
            return self._mock_verify(payload), None, True

        data = None
        if progress is not None:
            data = await self._verify_stream(payload, progress)
        if data is None:
            data = await self._post("llm", "/verify", json=payload)
        usage = data.pop("usage", None) if isinstance(data, dict) else None
        security = data.pop("security", None) if isinstance(data, dict) else None
        if security:
            # Flagi bezpieczeństwa idą do audytu razem z użyciem modeli (nie do odpowiedzi).
            usage = {**(usage or {}), "security": security}
        try:
            return LlmVerifyResponse.model_validate(data), usage, False
        except ValidationError as exc:
            logger.warning("Niepoprawny kontrakt odpowiedzi LLM: %s", exc)
            raise DownstreamError("llm", "bad_response", "Serwis weryfikacji zwrócił odpowiedź w niepoprawnym formacie.") from exc

    async def _verify_stream(self, payload: dict[str, Any], progress: ProgressListener) -> Any:
        """/verify/stream of the pipeline: forwards progress events, returns the final body.
        None = the pipeline has no streaming endpoint (older version): the caller uses /verify."""
        url = f"{self.services['llm']}/verify/stream"
        try:
            async with self.client.stream("POST", url, json=payload) as resp:
                if resp.status_code in (404, 405):
                    return None
                if resp.status_code >= 400:
                    raise DownstreamError("llm", "upstream_error", f"Serwis 'llm' zwrócił błąd {resp.status_code}.")
                async for raw in resp.aiter_lines():
                    if not raw.strip():
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get("type") == "progress":
                        event.pop("type", None)
                        await progress(event)
                    elif event.get("type") == "result":
                        code = int(event.get("status", 502))
                        if code in (402, 429):
                            raise DownstreamError(
                                "llm", "api_limit", "Serwis 'llm' zgłosił limit lub brak dostępu do API.", CheckStatus.api_limit
                            )
                        if code >= 400:
                            logger.warning("llm /verify/stream -> %s: %s", code, str(event.get("body"))[:500])
                            raise DownstreamError("llm", "upstream_error", f"Serwis 'llm' zwrócił błąd {code}.")
                        return event.get("body")
        except httpx.TimeoutException as exc:
            raise DownstreamError("llm", "timeout", "Serwis 'llm' nie odpowiedział w wyznaczonym czasie.") from exc
        except httpx.RequestError as exc:
            raise DownstreamError("llm", "service_unavailable", "Serwis 'llm' jest niedostępny.") from exc
        raise DownstreamError("llm", "bad_response", "Serwis weryfikacji przerwał odpowiedź przed wynikiem.")

    async def ocr(self, filename: str, content: bytes, mime: str) -> OcrResponse:
        if self.is_mock("image_recognition"):
            return OcrResponse(
                text="",
                limitations=["Tryb MOCK – serwis rozpoznawania tekstu z obrazu nie jest podłączony, obraz nie został odczytany."],
                mock=True,
            )
        data = await self._post(
            "image_recognition", "/v1/ocr", files={"file": (filename, content, mime)}
        )
        try:
            if isinstance(data, dict):
                blocks = data.get("blocks")
                confidence = None
                if isinstance(blocks, list) and blocks:
                    values = [block.get("confidence") for block in blocks if isinstance(block, dict)]
                    values = [value for value in values if isinstance(value, (int, float))]
                    if values:
                        confidence = sum(values) / len(values)
                data = {
                    "text": data.get("text", ""),
                    "confidence": confidence,
                    "language": data.get("lang"),
                    "limitations": [],
                    "mock": False,
                }
            return OcrResponse.model_validate(data)
        except ValidationError as exc:
            raise DownstreamError("image_recognition", "bad_response", "Serwis OCR zwrócił odpowiedź w niepoprawnym formacie.") from exc

    async def scrape(self, url: str) -> ScrapedPage | None:
        if self.is_mock("page_scrapper"):
            return None
        target = f"{self.services['page_scrapper']}/scrape"
        try:
            resp = await self.client.post(target, json={"url": url})
        except httpx.TimeoutException as exc:
            raise DownstreamError("page_scrapper", "timeout", "Serwis 'page_scrapper' nie odpowiedział w wyznaczonym czasie.") from exc
        except httpx.RequestError as exc:
            raise DownstreamError("page_scrapper", "service_unavailable", "Serwis 'page_scrapper' jest niedostępny.") from exc

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict) and detail.get("message"):
                raise PageUnreadable(detail.get("code", "unreadable"), detail["message"])
            raise DownstreamError("page_scrapper", "upstream_error", f"Serwis 'page_scrapper' zwrócił błąd {resp.status_code}.")
        try:
            return ScrapedPage.model_validate(resp.json())
        except (ValueError, ValidationError) as exc:
            raise DownstreamError("page_scrapper", "bad_response", "Serwis 'page_scrapper' zwrócił odpowiedź w niepoprawnym formacie.") from exc

    async def live_search(self, query: str, max_documents: int, timeout: float) -> dict[str, Any]:
        """PageScrapper szuka twierdzenia w wyszukiwarkach zaufanych witryn i dokłada strony do bazy wiedzy."""
        if self.is_mock("page_scrapper"):
            raise DownstreamError("page_scrapper", "not_configured", "PageScrapper nie jest podłączony.")
        data = await self._post(
            "page_scrapper",
            "/live-search",
            json={"query": query[:2000], "max_documents": max_documents},
            timeout=timeout,
        )
        if not isinstance(data, dict):
            raise DownstreamError("page_scrapper", "bad_response", "Serwis 'page_scrapper' zwrócił niepoprawną odpowiedź.")
        return data

    async def scraper_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.is_mock("page_scrapper"):
            raise DownstreamError("page_scrapper", "not_configured", "PageScrapper nie jest podłączony.")
        try:
            resp = await self.client.get(f"{self.services['page_scrapper']}{path}", params=params)
        except httpx.TimeoutException as exc:
            raise DownstreamError("page_scrapper", "timeout", "Serwis 'page_scrapper' nie odpowiedział w wyznaczonym czasie.") from exc
        except httpx.RequestError as exc:
            raise DownstreamError("page_scrapper", "service_unavailable", "Serwis 'page_scrapper' jest niedostępny.") from exc
        if resp.status_code == 404:
            raise DownstreamError("page_scrapper", "not_found", "Nie znaleziono.")
        if resp.status_code >= 400:
            raise DownstreamError("page_scrapper", "upstream_error", f"Serwis 'page_scrapper' zwrócił błąd {resp.status_code}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise DownstreamError("page_scrapper", "bad_response", "Serwis 'page_scrapper' zwrócił niepoprawną odpowiedź.") from exc

    async def scrap_all(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.is_mock("page_scrapper"):
            return {"status": "accepted", "mock": True, "detail": "Tryb MOCK – PageScrapper nie jest podłączony."}
        data = await self._post("page_scrapper", "/scrap/all", json=body)
        return data if isinstance(data, dict) else {"result": data}

    async def health(self) -> list[ServiceHealth]:
        result: list[ServiceHealth] = []
        for name, base in self.services.items():
            if self.is_mock(name):
                result.append(ServiceHealth(name=name, configured=bool(base), status="mock"))
                continue
            try:
                health_path = "/healthz" if name == "image_recognition" else "/health"
                resp = await self.client.get(f"{base}{health_path}", timeout=3.0)
                ok = resp.status_code < 400
                result.append(
                    ServiceHealth(name=name, configured=True, status="ok" if ok else "down", detail=None if ok else f"HTTP {resp.status_code}")
                )
            except httpx.HTTPError as exc:
                result.append(ServiceHealth(name=name, configured=True, status="down", detail=type(exc).__name__))
        return result

    @staticmethod
    def _mock_verify(payload: dict[str, Any]) -> LlmVerifyResponse:
        content = payload.get("content", {})
        claim = content.get("text") or content.get("url") or content.get("ocr_text") or "(brak treści)"
        return LlmVerifyResponse(
            status="completed",
            claims=[
                ClaimResult(
                    claim=claim[:300],
                    verdict=Verdict.insufficient_evidence,
                    explanation=MOCK_NOTE,
                    evidence_strength="none",
                    strength_rationale="Nie przeszukano żadnych źródeł.",
                    sources=[],
                    limitations=[MOCK_NOTE],
                )
            ],
            summary=MOCK_NOTE,
            limitations=[MOCK_NOTE],
        )
