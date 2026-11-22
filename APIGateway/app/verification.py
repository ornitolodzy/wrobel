import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import status
from fastapi.responses import JSONResponse

from app.db import Check, Database, utcnow
from app.deps import Client
from app.pii import mask_pii
from app.runtime_config import RuntimeConfigStore
from app.schemas import (
    CheckResponse,
    CheckStatus,
    ErrorInfo,
    InputType,
    LiveSearchDocumentInfo,
    LiveSearchInfo,
    LiveSearchSourceInfo,
    LlmVerifyResponse,
    ModelInfo,
    OcrResponse,
    PageInfo,
    Verdict,
)
from app.services import Downstream, DownstreamError, PageUnreadable, ProgressListener

if TYPE_CHECKING:
    from app.cases import CaseDesk

logger = logging.getLogger("gateway.verification")

_MAX_PAGE_CHARS = 50_000

_HTTP_STATUS = {
    CheckStatus.completed: status.HTTP_200_OK,
    CheckStatus.needs_clarification: status.HTTP_200_OK,
    CheckStatus.api_limit: status.HTTP_503_SERVICE_UNAVAILABLE,
    CheckStatus.error: status.HTTP_502_BAD_GATEWAY,
}


class CheckRunner:
    def __init__(self, db: Database, downstream: Downstream, runtime: RuntimeConfigStore, client: Client,
                 cases: "CaseDesk | None" = None) -> None:
        self.db = db
        self.downstream = downstream
        self.runtime = runtime
        self.client = client
        self.cases = cases
        # Set by the streaming routes: receives progress events while the check runs.
        self.progress: ProgressListener | None = None

    async def _progress(self, stage: str, **data: Any) -> None:
        if self.progress is not None:
            await self.progress({"stage": stage, **data})

    async def verify_claim(self, text: str) -> JSONResponse:
        masked, pii_types = mask_pii(text)
        return await self._run(
            input_type=InputType.claim,
            input_for_log=masked,
            content={"text": masked},
            pii_types=pii_types,
        )

    async def verify_url(self, url: str) -> JSONResponse:
        masked, pii_types = mask_pii(url)
        return await self._run(
            input_type=InputType.url,
            input_for_log=masked,
            content={"url": url},
            pii_types=pii_types,
            page_url=url,
        )

    async def verify_image(self, filename: str, data: bytes, mime: str) -> JSONResponse:
        return await self._run(
            input_type=InputType.image,
            input_for_log=f"[obraz] {filename} ({mime}, {len(data)} B)",
            content=None,
            pii_types=[],
            image=(filename, data, mime),
        )

    async def verify_live(self, check: Check, max_documents: int, timeout: float) -> JSONResponse:
        """Drugie podejście do sprawdzenia bez potwierdzonych danych: PageScrapper szuka na żywo
        w wyszukiwarkach zaufanych witryn, dokłada znalezione strony do bazy wiedzy, a my weryfikujemy
        te same treści jeszcze raz. Bez nowych stron zwracamy poprzedni wynik (bez drugiego wywołania LLM)."""
        prev = CheckResponse.model_validate(check.response)
        weak = [c.claim for c in prev.claims if c.verdict == Verdict.insufficient_evidence]
        if prev.status != CheckStatus.completed or prev.mock or not weak:
            raise LiveSearchNotApplicable(
                "Wyszukiwanie na żywo dotyczy tylko sprawdzeń zakończonych brakiem potwierdzonych danych."
            )
        input_type = prev.input_type
        if input_type == InputType.image and not prev.ocr_text:
            raise LiveSearchNotApplicable("Brak odczytanego tekstu ze zdjęcia do wyszukania.")

        try:
            data = await self.downstream.live_search(" ".join(weak), max_documents, timeout)
        except DownstreamError as exc:
            info = LiveSearchInfo(previous_check_id=prev.check_id, message=f"Wyszukiwanie na żywo nie powiodło się: {exc.message}")
            return self._unchanged(prev, info)

        info = _live_info(prev.check_id, data)
        if not info.documents:
            return self._unchanged(prev, info)

        info.reverified = True
        if input_type == InputType.url:
            return await self._run(
                input_type=input_type,
                input_for_log=check.input_masked,
                content={"url": check.input_masked},
                pii_types=[],
                page_url=check.input_masked,
                live_search=info,
                prior=prev,
            )
        content = {"ocr_text": prev.ocr_text} if input_type == InputType.image else {"text": check.input_masked}
        return await self._run(
            input_type=input_type,
            input_for_log=check.input_masked,
            content=content,
            pii_types=[],
            live_search=info,
            ocr_text=prev.ocr_text,
            prior=prev,
        )

    def _unchanged(self, prev: CheckResponse, info: LiveSearchInfo) -> JSONResponse:
        response = prev.model_copy(update={"live_search": info})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.model_dump(mode="json"),
            headers={"X-Check-Id": prev.check_id},
        )

    async def _run(
        self,
        *,
        input_type: InputType,
        input_for_log: str,
        content: dict[str, Any] | None,
        pii_types: list[str],
        image: tuple[str, bytes, str] | None = None,
        page_url: str | None = None,
        live_search: LiveSearchInfo | None = None,
        ocr_text: str | None = None,
        prior: CheckResponse | None = None,
    ) -> JSONResponse:
        check_id = str(uuid.uuid4())
        created_at = utcnow()
        started = time.perf_counter()
        cfg = self.runtime.get()
        resolved = self.runtime.resolve_options()
        model = ModelInfo(provider=cfg.llm_provider, name=cfg.llm_model)

        result: LlmVerifyResponse | None = None
        usage: dict[str, Any] | None = None
        mock = False
        error: DownstreamError | None = None
        ocr_masked: str | None = ocr_text
        page_info: PageInfo | None = None
        extra_limitations: list[str] = []

        try:
            if page_url is not None:
                await self._progress("page", url=page_url)
                try:
                    page = await self.downstream.scrape(page_url)
                except PageUnreadable as exc:
                    result = LlmVerifyResponse(status="needs_clarification", clarification_question=exc.message)
                else:
                    if page is None:
                        extra_limitations.append("Treść strony nie została pobrana - PageScrapper nie jest podłączony.")
                    else:
                        page_text, page_pii = mask_pii(page.text[:_MAX_PAGE_CHARS])
                        pii_types = sorted(set(pii_types) | set(page_pii))
                        page_info = PageInfo.model_validate(page.model_dump())
                        content = {
                            "url": page_url,
                            "text": page_text,
                            "title": page.title,
                            "publisher": page.publisher,
                            "published_at": page.published_at,
                        }

            if image is not None:
                await self._progress("ocr")
                ocr: OcrResponse = await self.downstream.ocr(*image)
                mock = ocr.mock
                extra_limitations.extend(ocr.limitations)
                ocr_masked, ocr_pii = mask_pii(ocr.text.strip())
                pii_types = sorted(set(pii_types) | set(ocr_pii))
                content = {"ocr_text": ocr_masked}
                await self._progress("ocr_done", chars=len(ocr_masked))
                if not ocr_masked and not ocr.mock:
                    result = LlmVerifyResponse(
                        status="needs_clarification",
                        clarification_question=(
                            "Nie udało się odczytać tekstu z obrazu. Prześlij wyraźniejsze zdjęcie "
                            "albo wpisz treść twierdzenia."
                        ),
                    )

            if result is None:
                payload = {
                    "check_id": check_id,
                    "input_type": input_type.value,
                    "content": content,
                    "options": resolved.model_dump(),
                    "model": model.model_dump(),
                }
                result, usage, llm_mock = await self.downstream.verify(payload, progress=self.progress)
                mock = mock or llm_mock
        except DownstreamError as exc:
            error = exc

        duration_ms = int((time.perf_counter() - started) * 1000)

        if error is not None:
            response = CheckResponse(
                check_id=check_id,
                status=error.status,
                input_type=input_type,
                channel=self.client.name,
                created_at=created_at,
                duration_ms=duration_ms,
                limitations=[
                    "Sprawdzenie nie zostało wykonane z powodu problemu technicznego. "
                    "To nie jest ocena prawdziwości treści."
                ],
                ocr_text=ocr_masked,
                page=page_info,
                model=model,
                pii_masked=bool(pii_types),
                error=ErrorInfo(code=error.code, message=error.message),
                live_search=live_search,
            )
        else:
            assert result is not None
            if live_search is not None:
                result, live_search = _live_outcome(live_search, result, prior)
            response = CheckResponse(
                check_id=check_id,
                status=CheckStatus(result.status),
                input_type=input_type,
                channel=self.client.name,
                created_at=created_at,
                duration_ms=duration_ms,
                claims=result.claims,
                summary=result.summary,
                limitations=result.limitations + [lim for lim in extra_limitations if lim not in result.limitations],
                clarification_question=result.clarification_question,
                ocr_text=ocr_masked,
                page=page_info,
                model=model,
                mock=mock,
                pii_masked=bool(pii_types),
                live_search=live_search,
            )

        if self.cases is not None and error is None:
            try:  # pętla informacji nie może zepsuć samego sprawdzenia
                response.cases = await self.cases.register(response, prior.check_id if prior else None)
            except Exception:
                logger.exception("Nie udało się zapisać sprawy moderatora dla %s", check_id)

        await self._persist(response, input_for_log, error, usage, pii_types)

        return JSONResponse(
            status_code=_HTTP_STATUS[response.status],
            content=response.model_dump(mode="json"),
            headers={"X-Check-Id": check_id},
        )

    async def _persist(
        self,
        response: CheckResponse,
        input_for_log: str,
        error: DownstreamError | None,
        usage: dict[str, Any] | None,
        pii_types: list[str],
    ) -> None:
        await self.db.save_check(
            Check(
                id=response.check_id,
                created_at=response.created_at,
                client=self.client.name,
                channel=response.channel,
                input_type=response.input_type.value,
                input_masked=input_for_log,
                status=response.status.value,
                duration_ms=response.duration_ms,
                response=response.model_dump(mode="json"),
            )
        )

        if pii_types:
            await self.db.log_event("pii_masked", client=self.client.name, check_id=response.check_id, detail={"types": pii_types})

        if error is not None:
            await self.db.log_event(
                "downstream_error",
                client=self.client.name,
                check_id=response.check_id,
                detail={"service": error.service, "code": error.code, "status": error.status.value},
            )
            return

        security = (usage or {}).get("security") or {}
        if security.get("injection_suspected") or security.get("injection_in_sources"):
            # Scenariusz E: polecenia w treści zignorowane (potraktowane jako dane) i odnotowane.
            await self.db.log_event(
                "prompt_injection",
                client=self.client.name,
                check_id=response.check_id,
                detail={
                    "in_input": bool(security.get("injection_suspected")),
                    "in_sources": bool(security.get("injection_in_sources")),
                    "input_type": response.input_type.value,
                    "verdicts": [c.verdict.value for c in response.claims],
                },
            )

        verdicts = [c.verdict.value for c in response.claims]
        await self.db.log_event(
            "check_completed",
            client=self.client.name,
            check_id=response.check_id,
            detail={
                "status": response.status.value,
                "input_type": response.input_type.value,
                "duration_ms": response.duration_ms,
                "model": response.model.model_dump() if response.model else None,
                "verdicts": verdicts,
                "mock": response.mock,
                "usage": usage,
            },
        )
        if any(v == Verdict.insufficient_evidence.value for v in verdicts) and not response.mock:
            await self.db.log_event("abstained", client=self.client.name, check_id=response.check_id)


class LiveSearchNotApplicable(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _live_info(previous_check_id: str, data: dict[str, Any]) -> LiveSearchInfo:
    sources = [
        LiveSearchSourceInfo(name=s.get("name", "?"), status=s.get("status", "error"), found=s.get("found", 0))
        for s in data.get("sources", [])
        if isinstance(s, dict)
    ]
    documents = [
        LiveSearchDocumentInfo(
            title=d.get("title"),
            url=d["url"],
            publisher=d.get("publisher"),
            source_name=d.get("source_name"),
            published_at=d.get("published_at"),
        )
        for d in data.get("documents", [])
        if isinstance(d, dict) and d.get("url") and d.get("ingested", True)
    ]
    searched = ", ".join(s.name for s in sources if s.status in ("ok", "no_results"))
    failed = ", ".join(s.name for s in sources if s.status == "error")
    if not searched:
        message = "Nie udało się przeszukać na żywo żadnej z zaufanych witryn."
    elif documents:
        message = (
            f"W bazie nie było potwierdzonych danych, więc przeszukano na żywo: {searched}. "
            f"Dołączone nowe strony: {len(documents)}."
        )
    else:
        message = f"Przeszukano na żywo: {searched} - brak nowych materiałów na ten temat."
    if failed:
        # Awaria źródła to nie „brak artykułu”: mówimy, czego nie dało się sprawdzić.
        message += f" Nie mogę teraz sprawdzić: {failed} (błąd dostępu), więc brak wyniku stamtąd nic nie znaczy."
    return LiveSearchInfo(
        previous_check_id=previous_check_id,
        terms=[str(t) for t in data.get("terms", [])],
        sources=sources,
        documents=documents,
        message=message,
    )


# Werdykty, które coś rozstrzygają: po wyszukiwaniu na żywo wymagamy dla nich co najmniej średnich przesłanek.
_DECISIVE = {Verdict.confirmed, Verdict.false, Verdict.misleading}
_STRONG_ENOUGH = {"moderate", "strong"}

WEAK_LIVE_NOTE = (
    "Strony znalezione na żywo dają tylko słabe przesłanki, więc wynik się nie zmienia. "
    "Możesz je przejrzeć samodzielnie."
)


def _live_outcome(
    info: LiveSearchInfo, result: LlmVerifyResponse, prior: CheckResponse | None
) -> tuple[LlmVerifyResponse, LiveSearchInfo]:
    """Bezpiecznik: strony dociągnięte na żywo to świeże, nieprzejrzane źródła, a mały model potrafi
    źle odczytać stanowisko tekstu. Rozstrzygający werdykt (prawda / fałsz / manipulacja) zastępuje
    „brak danych” tylko przy co najmniej średnich przesłankach; inaczej zostaje poprzedni wynik."""
    weak = [
        c for c in result.claims
        if c.verdict in _DECISIVE and (c.evidence_strength or "none") not in _STRONG_ENOUGH
    ]
    if result.status == "completed" and weak and prior is not None:
        kept = LlmVerifyResponse(
            status="completed",
            claims=prior.claims,
            summary=prior.summary,
            limitations=prior.limitations + [WEAK_LIVE_NOTE],
            clarification_question=prior.clarification_question,
        )
        return kept, info.model_copy(update={"improved": False, "message": f"{info.message} {WEAK_LIVE_NOTE}"})
    improved = any(c.verdict != Verdict.insufficient_evidence for c in result.claims)
    suffix = "Nowe źródła pozwoliły ocenić twierdzenie." if improved else "Nadal nie wystarczają do oceny."
    return result, info.model_copy(update={"improved": improved, "message": f"{info.message} {suffix}"})
