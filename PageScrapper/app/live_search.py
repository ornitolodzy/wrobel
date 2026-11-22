"""Wyszukiwanie na żywo: gdy w bazie wiedzy brak potwierdzonych danych, szukamy w wyszukiwarkach
zaufanych witryn z katalogu (sources.yaml, blok `live_search`), pobieramy najlepiej pasujące strony
i od razu dokładamy je do bazy (tabela documents + RAG pipeline'u), żeby ponowna weryfikacja je znalazła.

Zasady jak przy zwykłym scrapowaniu: tylko witryny z katalogu, robots.txt, publiczne adresy (SSRF),
limity czasu. Pobrana treść to dane niezaufane - trafia do RAG jak każdy inny dokument źródła.
"""

import asyncio
import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.catalog import Catalog, SourceConfig
from app.config import Settings
from app.db import Database, DocumentIn
from app.fetch import FetchError, Fetcher
from app.render import Renderer
from app.schemas import LiveSearchDocument, LiveSearchOut, LiveSearchSource
from app.scrape import scrape_url

logger = logging.getLogger("scraper.live_search")

SEARCH_TIMEOUT_SECONDS = 25.0
FETCH_TIMEOUT_SECONDS = 25.0
MAX_PER_SOURCE = 2
MAX_TERMS = 5
MIN_DOCUMENT_CHARS = 200

# Słowa bez znaczenia dla wyszukiwarki (spójniki, zaimki, słowa pytające, „czy to prawda”...).
_STOPWORDS = frozenset(
    """
    a aby albo ale ani aż bardzo bez bo bowiem był była było byli być będzie będą chce chcą
    co czy czyli dla do dziś dzisiaj gdy gdzie go i ich ile im inne iż ja jak jaka jaki jakie
    jako je jeden jego jej jest jeszcze jeśli już kiedy kto która które który których ku lub
    ma mają mamy może można mu na nad nam nas nie nic nich niej no o od ok on ona one oni ono
    oraz po pod podczas przed przez przy prawda prawdą prawdziwe prawdziwy sam się są ta tak
    taki takie tam te tego tej temu ten też to tu tutaj tych tylko tym u w we według wiem więc
    wszystko wszyscy z za ze że żeby ostatnio niedawno teraz obecnie podobno rzekomo czyżby
    sprawdź sprawdz informacja informacje wiadomość news fake twierdzenie twierdzą mówią
    słyszałem słyszałam napisali piszą wydało wydał wydała the and of to in is that
    sprawie sprawy kwestii temat temacie dotyczący dotyczące dotyczy jakiś jakieś wszystkich
    przeciw przeciwko wobec między około poza obok wśród dzięki mimo zamiast wokół spośród
    nowe nowy nowa nowych nowego nową zasady zasad sposób rzecz rzeczy
    """.split()
)

_VERB_ENDINGS = ("ło", "ła", "ły", "ił", "ał", "ył", "ili", "ali", "yli", "ć", "uje", "ują", "eją")

_WORD = re.compile(r"[0-9A-Za-zÀ-ž][0-9A-Za-zÀ-ž\-]*")


@dataclass
class _Term:
    word: str
    score: float
    position: int

    @property
    def stem(self) -> str:
        """Prefiks słowa: wyszukiwarki WordPressa szukają podciągu (LIKE), a polska odmiana
        zmienia końcówki - „wizy”, „wiz”, „wizami” mają wspólny prefiks „wiz”."""
        w = self.word
        if w.isdigit() or len(w) <= 3:
            return w
        stem = w[:7]
        while len(stem) > 3 and stem[-1] in "aeiouyąęó":
            stem = stem[:-1]
        return stem


def extract_terms(query: str, limit: int = MAX_TERMS) -> list[_Term]:
    """Najważniejsze słowa twierdzenia: nazwy własne, liczby i długie słowa; kolejność jak w zdaniu."""
    terms: dict[str, _Term] = {}
    for pos, match in enumerate(_WORD.finditer(query)):
        raw = match.group(0).strip("-")
        word = raw.lower()
        if len(word) < 3 and not word.isdigit():
            continue
        if word in _STOPWORDS or word in terms:
            continue
        # każde słowo treściowe się liczy (krótkie też: „wiz”, „VAT”), długość tylko trochę
        score = 3 + min(len(word), 12) / 4
        if raw[:1].isupper() and pos > 0:
            score += 4  # nazwa własna w środku zdania
        if raw.isupper() and len(raw) >= 2:
            score += 3  # skrót: MSZ, RCB, NATO
        if any(ch.isdigit() for ch in word):
            score += 3
        if word.endswith(_VERB_ENDINGS):
            score -= 2.5  # czasowniki („wprowadziło”, „ogłosił”) mniej wyróżniają temat niż rzeczowniki
        terms[word] = _Term(word=word, score=score, position=pos)
    best = sorted(terms.values(), key=lambda t: -t.score)[:limit]
    return sorted(best, key=lambda t: t.position)


def _hits(text: str, stems: list[str]) -> int:
    """Ile rdzeni występuje na początku jakiegoś słowa („minist” nie pasuje do „feministyczne”)."""
    lowered = text.lower()
    return sum(1 for s in stems if re.search(r"\b" + re.escape(s), lowered))


@dataclass
class _Candidate:
    url: str
    title: str
    source: SourceConfig
    score: int


@dataclass
class _SourceRun:
    source: SourceConfig
    status: str = "ok"
    message: str | None = None
    candidates: list[_Candidate] = field(default_factory=list)


class LiveSearcher:
    def __init__(
        self,
        catalog: Catalog,
        fetcher: Fetcher,
        renderer: Renderer,
        settings: Settings,
        db: Database,
        rag=None,
    ) -> None:
        self.catalog = catalog
        self.fetcher = fetcher
        self.renderer = renderer
        self.settings = settings
        self.db = db
        self.rag = rag

    def sources(self) -> list[SourceConfig]:
        return [s for s in self.catalog.sources if s.active and s.live_search is not None]

    async def search(self, query: str, max_documents: int = 5) -> LiveSearchOut:
        started = time.perf_counter()
        terms = extract_terms(query)
        stems = [t.stem for t in terms]
        runs = [_SourceRun(source=s) for s in self.sources()]

        if terms:
            await asyncio.gather(*(self._search_source(run, terms) for run in runs))
        else:
            for run in runs:
                run.status, run.message = "skipped", "Brak słów kluczowych w zapytaniu."

        picked = await self._pick(runs, max_documents)
        documents = await asyncio.gather(*(self._fetch_and_store(c, query, stems) for c in picked))

        return LiveSearchOut(
            query=query,
            terms=[t.word for t in terms],
            sources=[
                LiveSearchSource(
                    source_id=r.source.id,
                    name=r.source.name,
                    status=r.status,
                    found=len(r.candidates),
                    message=r.message,
                )
                for r in runs
            ],
            documents=[d for d in documents if d is not None],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---------- wyszukiwanie w witrynach ----------

    async def _search_source(self, run: _SourceRun, terms: list[_Term]) -> None:
        try:
            await asyncio.wait_for(self._collect(run, terms), timeout=SEARCH_TIMEOUT_SECONDS)
            if not run.candidates and run.status == "ok":
                run.status = "no_results"
        except asyncio.TimeoutError:
            run.status, run.message = "error", "Wyszukiwarka nie odpowiedziała w czasie."
        except FetchError as exc:
            blocked = exc.code == "http_error" and ("403" in exc.message or "429" in exc.message)
            run.status = "error"
            run.message = "Witryna blokuje automatyczne zapytania." if blocked else exc.message
        except Exception as exc:  # jedna witryna nie może zepsuć całego wyszukiwania
            logger.exception("Live search failed for %s", run.source.id)
            run.status, run.message = "error", type(exc).__name__

    async def _collect(self, run: _SourceRun, terms: list[_Term]) -> None:
        cfg = run.source.live_search
        assert cfg is not None
        stems = [t.stem for t in terms]

        if cfg.kind == "wp_rest":
            # WordPress łączy słowa przez AND: zaczynamy od 3 najważniejszych, potem luzujemy do 2 i 1
            by_weight = sorted(terms, key=lambda t: -t.score)
            for n in (3, 2, 1):
                if n > len(by_weight):
                    continue
                query_stems = [t.stem for t in by_weight[:n]]
                await self._wp_rest(run, " ".join(query_stems), stems)
                if run.candidates:
                    break
        else:
            url = cfg.url.format(query=quote_plus(" ".join(t.word for t in terms)))
            if not await self.fetcher.allowed_by_robots(url):
                run.status, run.message = "skipped", "robots.txt nie pozwala na wyszukiwanie."
                return
            if cfg.kind == "rendered":
                page = await self.renderer.render(url)
                html_text, base = page.html, page.final_url
            else:
                result = await self.fetcher.get(url, polite=True)
                html_text, base = result.text, result.final_url
            self._links(run, html_text, base, stems)

        run.candidates.sort(key=lambda c: -c.score)
        del run.candidates[cfg.max_results :]

    async def _wp_rest(self, run: _SourceRun, search: str, stems: list[str]) -> None:
        cfg = run.source.live_search
        base = cfg.url.rstrip("/")
        endpoint = f"{base}/wp-json/wp/v2/search"
        if not await self.fetcher.allowed_by_robots(endpoint):
            run.status, run.message = "skipped", "robots.txt nie pozwala na wyszukiwanie."
            return
        result = await self.fetcher.get(
            endpoint, params={"search": search, "per_page": str(cfg.max_results * 2), "type": "post"}, polite=True
        )
        try:
            items = json.loads(result.text)
        except ValueError as exc:
            raise FetchError("bad_response", "Wyszukiwarka zwróciła niepoprawną odpowiedź.") from exc
        host = urlsplit(base).netloc
        seen = {c.url for c in run.candidates}
        for item in items if isinstance(items, list) else []:
            url = str(item.get("url") or "")
            title = html.unescape(str(item.get("title") or "")).replace("\xa0", " ").strip()
            if not url.startswith("http") or urlsplit(url).netloc != host or url in seen or not title:
                continue
            seen.add(url)
            run.candidates.append(_Candidate(url=url, title=title, source=run.source, score=_hits(title, stems)))

    def _links(self, run: _SourceRun, html_text: str, base_url: str, stems: list[str]) -> None:
        cfg = run.source.live_search
        include = re.compile(cfg.link_pattern) if cfg.link_pattern else None
        exclude = re.compile(cfg.exclude_pattern) if cfg.exclude_pattern else None
        host = urlsplit(base_url).netloc
        soup = BeautifulSoup(html_text, "lxml")
        seen: set[str] = set()
        for link in soup.select("a[href]"):
            url = urljoin(base_url, link["href"]).split("#")[0]
            title = " ".join(link.get_text(" ", strip=True).split())[:300]
            if not title or url in seen or urlsplit(url).netloc != host:
                continue
            if (include and not include.search(url)) or (exclude and exclude.search(url)):
                continue
            seen.add(url)
            run.candidates.append(_Candidate(url=url, title=title, source=run.source, score=_hits(title, stems)))

    # ---------- wybór, pobranie i zapis ----------

    async def _pick(self, runs: list[_SourceRun], max_documents: int) -> list[_Candidate]:
        """Najlepsze trafienia z różnych witryn (po kolei z każdej), bez stron, które już mamy w bazie."""
        known = await self.db.existing_urls([c.url for r in runs for c in r.candidates])
        queues = [
            [c for c in r.candidates if c.url not in known][:MAX_PER_SOURCE]
            for r in sorted(runs, key=lambda r: -(r.candidates[0].score if r.candidates else -1))
        ]
        picked: list[_Candidate] = []
        while len(picked) < max_documents and any(queues):
            for queue in queues:
                if queue and len(picked) < max_documents:
                    picked.append(queue.pop(0))
        return picked

    async def _fetch_and_store(self, cand: _Candidate, query: str, stems: list[str]) -> LiveSearchDocument | None:
        try:
            if not await self.fetcher.allowed_by_robots(cand.url):
                return None
            page = await asyncio.wait_for(
                scrape_url(cand.url, "auto", self.fetcher, self.renderer, self.settings),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
        except (FetchError, asyncio.TimeoutError) as exc:
            logger.info("Live search: skipped %s (%s)", cand.url, getattr(exc, "code", "timeout"))
            return None

        # Strona musi faktycznie dotyczyć twierdzenia: słowo kluczowe w tytule i większość w treści
        title = page.title or cand.title
        needed = max(1, (len(stems) + 1) // 2)
        if (
            len(page.text) < MIN_DOCUMENT_CHARS
            or _hits(title, stems) == 0
            or _hits(f"{title}\n{page.text}", stems) < needed
        ):
            logger.info("Live search: %s not relevant enough", cand.url)
            return None

        source = cand.source
        doc = DocumentIn(
            url=page.final_url,
            source_id=source.id,
            source_type=source.source_type,
            title=page.title or cand.title,
            publisher=page.publisher or source.publisher,
            published_at=page.published_at,
            text=page.text,
            language=page.language or "pl",
            extra={"live_search": True, "query": query[:500]},
        )
        outcome, stored = await self.db.upsert(doc)
        ingested = await self.rag.send(stored) if self.rag is not None else False
        return LiveSearchDocument(
            url=stored.url,
            title=stored.title,
            publisher=stored.publisher,
            published_at=stored.published_at,
            source_id=source.id,
            source_name=source.name,
            source_type=source.source_type,
            new=outcome == "new",
            ingested=ingested,
        )
