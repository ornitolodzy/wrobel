"""Example client for the OCR service (requires ``httpx``).

python examples/ocr_client.py screenshot.png
OCR_URL=http://ocr:8000 OCR_API_KEY=secret python examples/ocr_client.py shot.png --text
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class OcrError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"[{status}] {code}: {message}")
        self.status = status
        self.code = code


@dataclass(frozen=True)
class OcrBlock:
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float


@dataclass(frozen=True)
class OcrResult:
    text: str
    lang: str | None
    blocks: list[OcrBlock]
    engine: str
    duration_ms: int


class OcrClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 60.0,
        retries: int = 3,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self._retries = retries

    def __enter__(self) -> OcrClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def ocr(self, image: bytes | Path, langs: str = "pl,en", output: str = "blocks") -> OcrResult:
        data = image.read_bytes() if isinstance(image, Path) else image
        for attempt in range(self._retries + 1):
            response = self._http.post(
                "/v1/ocr",
                params={"langs": langs, "output": output},
                files={"file": ("image", data)},
            )
            # 503 = model still loading or server busy: back off and retry
            if response.status_code == 503 and attempt < self._retries:
                time.sleep(float(response.headers.get("Retry-After", "2")))
                continue
            return self._parse(response)
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(response: httpx.Response) -> OcrResult:
        body: dict[str, Any] = response.json()
        if response.status_code != 200:
            error = body.get("error", {})
            raise OcrError(response.status_code, error.get("code", "?"), error.get("message", ""))
        return OcrResult(
            text=body["text"],
            lang=body["lang"],
            blocks=[
                OcrBlock(b["text"], tuple(b["bbox"]), b["confidence"])  # type: ignore[arg-type]
                for b in body.get("blocks") or []
            ],
            engine=body["engine"],
            duration_ms=body["duration_ms"],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR a screenshot")
    parser.add_argument("image", type=Path)
    parser.add_argument("--langs", default="pl,en")
    parser.add_argument("--text", action="store_true", help="print only the text")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1250/cp852

    url = os.environ.get("OCR_URL", "http://localhost:8000")
    with OcrClient(url, api_key=os.environ.get("OCR_API_KEY")) as client:
        try:
            result = client.ocr(args.image, langs=args.langs)
        except OcrError as exc:
            print(exc, file=sys.stderr)
            return 1
    if args.text:
        print(result.text)
        return 0
    print(f"engine={result.engine} lang={result.lang} {result.duration_ms} ms")
    for block in result.blocks:
        print(f"{block.confidence:.2f} {block.bbox} {block.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
