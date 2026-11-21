import ipaddress
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException, status

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".corp")
_ALLOWED_PORTS = {None, 80, 443}

_IMAGE_SIGNATURES: list[tuple[str, bytes, int]] = [
    ("image/jpeg", b"\xff\xd8\xff", 0),
    ("image/png", b"\x89PNG\r\n\x1a\n", 0),
    ("image/webp", b"WEBP", 8),
]


def _bad_input(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


def validate_text(text: str, max_length: int) -> str:
    text = text.strip()
    if len(text) < 3:
        raise _bad_input("Treść jest za krótka.")
    if len(text) > max_length:
        raise _bad_input(f"Treść jest za długa (maks. {max_length} znaków).")
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")


def validate_public_url(raw: str, max_length: int) -> str:
    url = raw.strip()
    if len(url) > max_length:
        raise _bad_input(f"Adres URL jest za długi (maks. {max_length} znaków).")

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise _bad_input("Niepoprawny adres URL.") from exc

    if parts.scheme.lower() not in {"http", "https"}:
        raise _bad_input("Dozwolone są tylko adresy http:// i https://.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise _bad_input("Adres URL nie może zawierać danych logowania.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise _bad_input("Adres URL nie zawiera domeny.")
    if port not in _ALLOWED_PORTS:
        raise _bad_input("Dozwolone są tylko standardowe porty 80 i 443.")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise _bad_input("Adresy lokalne i wewnętrzne są niedozwolone.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise _bad_input("Adres URL musi zawierać pełną domenę.") from None
    else:
        if not ip.is_global:
            raise _bad_input("Adresy IP z sieci prywatnych/zastrzeżonych są niedozwolone.")

    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def detect_image_type(data: bytes) -> str | None:
    for mime, signature, offset in _IMAGE_SIGNATURES:
        if data[offset : offset + len(signature)] == signature:
            if mime == "image/webp" and not data.startswith(b"RIFF"):
                continue
            return mime
    return None


def validate_image(data: bytes, max_bytes: int) -> str:
    if not data:
        raise _bad_input("Przesłany plik jest pusty.")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Plik jest za duży (maks. {max_bytes // (1024 * 1024)} MB).",
        )
    mime = detect_image_type(data)
    if mime is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Nieobsługiwany format pliku. Dozwolone: JPEG, PNG, WEBP.",
        )
    return mime
