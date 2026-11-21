import time
import uuid
from typing import Any

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
    LlmVerifyResponse,
    ModelInfo,
    OcrResponse,
    Verdict,
)
from app.services import Downstream, DownstreamError

_HTTP_STATUS = {
    CheckStatus.completed: status.HTTP_200_OK,
    CheckStatus.needs_clarification: status.HTTP_200_OK,
    CheckStatus.api_limit: status.HTTP_503_SERVICE_UNAVAILABLE,
    CheckStatus.error: status.HTTP_502_BAD_GATEWAY,
}


class CheckRunner:
    def __init__(self, db: Database, downstream: Downstream, runtime: RuntimeConfigStore, client: Client) -> None:
        self.db = db
        self.downstream = downstream
        self.runtime = runtime
        self.client = client

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
        )

    async def verify_image(self, filename: str, data: bytes, mime: str) -> JSONResponse:
        return await self._run(
            input_type=InputType.image,
            input_for_log=f"[obraz] {filename} ({mime}, {len(data)} B)",
            content=None,
            pii_types=[],
            image=(filename, data, mime),
        )

    async def _run(
        self,
        *,
        input_type: InputType,
        input_for_log: str,
        content: dict[str, Any] | None,
        pii_types: list[str],
        image: tuple[str, bytes, str] | None = None,
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
        ocr_masked: str | None = None
        extra_limitations: list[str] = []

        try:
            if image is not None:
                ocr: OcrResponse = await self.downstream.ocr(*image)
                mock = ocr.mock
                extra_limitations.extend(ocr.limitations)
                ocr_masked, ocr_pii = mask_pii(ocr.text.strip())
                pii_types = sorted(set(pii_types) | set(ocr_pii))
                content = {"ocr_text": ocr_masked}
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
                result, usage, llm_mock = await self.downstream.verify(payload)
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
                model=model,
                pii_masked=bool(pii_types),
                error=ErrorInfo(code=error.code, message=error.message),
            )
        else:
            assert result is not None
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
                model=model,
                mock=mock,
                pii_masked=bool(pii_types),
            )

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
