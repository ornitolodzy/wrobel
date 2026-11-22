"""Generuje zrzuty ekranu postów do scenariusza F (multimodalność): tekst + data.

Obrazy są w repo (eval/images/), skrypt służy do ich odtworzenia:
    python eval/make_images.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "images"
FONTS = ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"]

POSTS = {
    "M01_rcb_zloty_stok.png": ("Gmina Złoty Stok · 19.11.2026, 10:12",
                               "PILNE! Alert RCB: woda z wodociągu Złoty Stok nie nadaje się "
                               "do celów spożywczych w miejscowościach Złoty Stok, Płonica i Błotnica. "
                               "Śledźcie komunikaty!"),
    "M02_pfizer.png": ("Zdrowie bez cenzury · 10.11.2026, 21:40",
                       "Pfizer opublikował nową listę 49 efektów ubocznych szczepionek przeciw COVID-19! "
                       "Media milczą. Udostępnij, zanim usuną!"),
    "M03_leki.png": ("Wiadomości24 · 20.11.2026, 08:05",
                     "Od 1 grudnia wszystkie leki na receptę będą w Polsce darmowe "
                     "- zapowiedziało Ministerstwo Zdrowia."),
}


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("Brak fontu TTF z polskimi znakami (DejaVuSans albo Arial).")


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=f) <= width:
            line = candidate
        else:
            lines.append(line)
            line = word
    return lines + [line]


def render(header: str, body: str, path: Path) -> None:
    img = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 20, 880, 400), radius=24, outline=(210, 210, 210), width=2)
    d.ellipse((50, 50, 110, 110), fill=(70, 110, 180))
    d.text((130, 62), header, font=font(26), fill=(90, 90, 90))
    y = 140
    for line in wrap(d, body, font(34), 780):
        d.text((50, y), line, font=font(34), fill=(20, 20, 20))
        y += 48
    img.save(path)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, (header, body) in POSTS.items():
        render(header, body, OUT / name)
        print("zapisano", OUT / name)
