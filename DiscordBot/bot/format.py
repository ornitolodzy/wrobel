"""Gateway CheckResponse -> Discord embed. Pure functions (unit-tested without Discord).

Discord limits: title 256, description 4096, field name 256 / value 1024, 25 fields,
footer 2048, 6000 characters per embed in total.
"""

from __future__ import annotations

from typing import Any

import discord

VERDICTS: dict[str, tuple[str, str, int]] = {
    # gateway verdict -> (emoji, label, color)
    "confirmed": ("✅", "Prawda — potwierdzone w źródłach", 0x2E7D32),
    "false": ("❌", "Fałsz", 0xC62828),
    "misleading": ("⚠️", "Wprowadza w błąd", 0xEF6C00),
    "insufficient_evidence": ("❔", "Brak wystarczających dowodów", 0x757575),
    "not_verifiable": ("💬", "Nie podlega weryfikacji (opinia, prognoza)", 0x5865F2),
}
STRENGTH = {"strong": "wysoka", "moderate": "średnia", "weak": "niska", "none": "brak"}
SOURCE_TYPES = {"primary": "źródło oficjalne", "factcheck": "fact-check", "secondary": "media / analiza",
                "web": "spoza katalogu"}
COLOR_INFO = 0x5865F2
COLOR_PROBLEM = 0x9E9E9E
FOOTER = "Wróbel · werdykt z jawnych reguł i cytowanych źródeł · sprawdź źródła samodzielnie"
MAX_CLAIMS = 5
MAX_SOURCES = 3
EMBED_BUDGET = 5800  # under the 6000 hard limit, leaves room for footer/title rounding


def cut(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _source_line(n: int, s: dict[str, Any]) -> str:
    title = cut(s.get("title") or s.get("url"), 90).replace("[", "(").replace("]", ")")
    meta = [s.get("publisher") or "", SOURCE_TYPES.get(s.get("source_type") or "", "")]
    if s.get("published_at"):
        meta.append(str(s["published_at"])[:10])
    line = f"**[{n}]** [{title}]({s.get('url')}) — {', '.join(m for m in meta if m)}"
    if s.get("fragment"):
        line += f"\n> „{cut(s['fragment'], 220)}”"
    return line


def _claim_value(c: dict[str, Any]) -> str:
    parts = [cut(c.get("explanation"), 400)]
    strength = STRENGTH.get(c.get("evidence_strength") or "")
    if strength and c.get("verdict") != "not_verifiable":
        parts.append(f"*Siła dowodów: {strength}*")
    sources = c.get("sources") or []
    if sources:
        parts.append("\n".join(_source_line(i, s) for i, s in enumerate(sources[:MAX_SOURCES], 1)))
    value = "\n".join(p for p in parts if p)
    if len(value) > 1024:  # drop quotes first, then cut
        parts[-1] = "\n".join(_source_line(i, {**s, "fragment": None})
                              for i, s in enumerate(sources[:MAX_SOURCES], 1))
        value = "\n".join(p for p in parts if p)
    return value[:1024]


def _footer(data: dict[str, Any]) -> str:
    bits = [FOOTER]
    if data.get("check_id"):
        bits.append(f"id {str(data['check_id'])[:8]}")
    if data.get("duration_ms") is not None:
        bits.append(f"{data['duration_ms'] / 1000:.1f} s")
    return " · ".join(bits)


def result_embed(data: dict[str, Any], asked: str | None = None) -> discord.Embed:
    status = data.get("status")
    if status in ("api_limit", "error"):
        return problem_embed(
            "Nie udało się teraz sprawdzić tej treści"
            if status == "error" else "Serwis weryfikacji jest chwilowo przeciążony",
            "To problem techniczny, **nie ocena prawdziwości treści**. Spróbuj ponownie za chwilę.",
            data)
    if status == "needs_clarification":
        e = discord.Embed(title="🤔 Potrzebuję doprecyzowania", color=COLOR_INFO,
                          description=cut(data.get("clarification_question")
                                          or "Napisz konkretne twierdzenie, które mam sprawdzić.", 1500))
        e.set_footer(text=_footer(data))
        return e

    claims = (data.get("claims") or [])[:MAX_CLAIMS]
    main = VERDICTS.get(claims[0]["verdict"], VERDICTS["insufficient_evidence"]) if claims else VERDICTS["insufficient_evidence"]
    title = f"{main[0]} {main[1]}" if len(claims) == 1 else f"🐦 Wróbel sprawdził {len(claims)} twierdzenia"
    e = discord.Embed(title=cut(title, 256), color=main[2] if len(claims) == 1 else COLOR_INFO)

    desc = []
    page = data.get("page") or {}
    if page.get("final_url"):
        desc.append(f"🔗 Strona: [{cut(page.get('title') or page['final_url'], 120)}]({page['final_url']})")
    if data.get("ocr_text"):
        desc.append(f"🖼️ Tekst z obrazu: „{cut(data['ocr_text'], 300)}”")
    elif asked and not page:
        desc.append(f"Pytanie: „{cut(asked, 300)}”")
    if data.get("summary"):
        desc.append(cut(data["summary"], 1500))
    e.description = cut("\n\n".join(desc), 4000) or None

    used = len(e.title or "") + len(e.description or "")
    for i, c in enumerate(claims, 1):
        emoji, label, _ = VERDICTS.get(c.get("verdict"), VERDICTS["insufficient_evidence"])
        name = cut(f"{emoji} {label}: {c.get('claim')}" if len(claims) > 1 else f"Twierdzenie: {c.get('claim')}", 256)
        value = _claim_value(c) or "—"
        if used + len(name) + len(value) > EMBED_BUDGET:
            e.add_field(name="…", value=f"Pominięto {len(claims) - i + 1} twierdzeń (limit długości wiadomości).",
                        inline=False)
            break
        e.add_field(name=name, value=value, inline=False)
        used += len(name) + len(value)

    notes = list(dict.fromkeys(data.get("limitations") or []))
    if data.get("mock"):
        notes.insert(0, "Tryb testowy (MOCK) — to nie jest prawdziwe sprawdzenie.")
    if notes:
        value = cut(" · ".join(notes), 1000)
        if used + len(value) < EMBED_BUDGET:
            e.add_field(name="Ograniczenia", value=value, inline=False)
    e.set_footer(text=_footer(data))
    return e


def problem_embed(title: str, description: str, data: dict[str, Any] | None = None) -> discord.Embed:
    e = discord.Embed(title=cut(f"⚠️ {title}", 256), description=cut(description, 2000), color=COLOR_PROBLEM)
    if data:
        e.set_footer(text=_footer(data))
    return e


def help_embed() -> discord.Embed:
    e = discord.Embed(
        title="🐦 Wróbel — sprawdzam twierdzenia w wiarygodnych źródłach",
        color=COLOR_INFO,
        description=(
            "Podaj twierdzenie, link albo zrzut ekranu, a sprawdzę je w katalogu źródeł "
            "(komunikaty instytucji, fact-checkerzy) i odpowiem z cytatami.\n\n"
            "**Jak użyć**\n"
            "• `/wrobel tekst: Czy RCB wysłało alert o ataku z powietrza?`\n"
            "• `/wrobel tekst: https://…` — sprawdzam treść strony\n"
            "• `/wrobel obraz: <zrzut ekranu>` — odczytuję tekst z obrazu i go sprawdzam\n"
            "• prawy klik na wiadomości → **Aplikacje → Sprawdź w Wróblu**\n"
            "• wiadomość prywatna do mnie albo wzmianka @Wróbel na kanale\n\n"
            "Werdykty: ✅ prawda · ❌ fałsz · ⚠️ wprowadza w błąd · ❔ brak dowodów · 💬 opinia.\n"
            "Werdykt wynika z jawnych reguł, nie z „opinii” modelu AI. Zawsze sprawdź podlinkowane źródła."
        ),
    )
    e.set_footer(text="Nie wysyłaj danych osobowych — są maskowane, ale lepiej ich nie podawać.")
    return e
