import {
  LiveSearchView,
  SearchResult,
  SearchResultsPayload,
  SourceLink,
  Verdict,
} from './search.models';

/** Werdykty API Gateway (CheckResponse.claims[].verdict) -> tag w dymku bota. */
const VERDICTS: Record<string, Verdict> = {
  confirmed: 'true',
  false: 'false',
  misleading: 'misleading',
  insufficient_evidence: 'unverified',
  not_verifiable: 'unverifiable',
};

const STRENGTH: Record<string, string> = { strong: 'wysoka', moderate: 'średnia', weak: 'niska' };

const SOURCE_KINDS: Record<string, string> = {
  primary: 'źródło urzędowe',
  factcheck: 'fact-checking',
  secondary: 'źródło wtórne',
  web: 'strona spoza katalogu',
};

/** Odpowiedź POST /api/v1/verify/* -> wyniki dla czatu (ten sam kształt co `search:results` z WebSocketu). */
export function fromGateway(query: string, body: any): SearchResultsPayload {
  const claims: any[] = Array.isArray(body?.claims) ? body.claims : [];
  const results: SearchResult[] = claims.map((claim, index) => {
    const sources = toSources(claim.sources);
    return {
      id: `${body.check_id ?? 'check'}-${index}`,
      title: claim.claim ?? 'Wynik weryfikacji',
      url: sources[0]?.url ?? '#',
      snippet: claim.explanation ?? undefined,
      source: sources[0]?.publisher,
      verdict: VERDICTS[claim.verdict],
      strength: STRENGTH[claim.evidence_strength],
      sources,
    };
  });
  if (!results.length && (body?.clarification_question || body?.error?.message)) {
    results.push({
      id: body.check_id ?? 'info',
      title: body.clarification_question ? 'Potrzebne doprecyzowanie' : 'Nie udało się sprawdzić',
      url: '#',
      snippet: body.clarification_question ?? body.error.message,
    });
  }
  // Jedno twierdzenie: werdykt na górze dymka. Kilka: tag przy każdym twierdzeniu.
  const verdict = results.length === 1 ? results[0].verdict : undefined;
  const liveEligible =
    body?.status === 'completed' &&
    !body?.mock &&
    !body?.live_search &&
    typeof body?.check_id === 'string' &&
    claims.some((c) => c?.verdict === 'insufficient_evidence');
  const checkId = typeof body?.check_id === 'string' ? body.check_id : undefined;
  const previous = body?.live_search?.previous_check_id;
  return {
    query,
    results,
    verdict,
    checkId,
    liveEligible,
    live: body?.live_search ? toLive(body.live_search) : undefined,
    notes: notesOf(body, claims),
    caseNotes: (Array.isArray(body?.cases) ? body.cases : [])
      .map((c: any) => (typeof c?.message === 'string' ? c.message : ''))
      .filter((m: string) => !!m),
    ocrText: typeof body?.ocr_text === 'string' && body.ocr_text.trim() ? body.ocr_text.trim() : undefined,
    checkIds: [checkId, typeof previous === 'string' ? previous : undefined].filter(
      (id): id is string => !!id,
    ),
  };
}

/**
 * Ograniczenia odpowiedzi (wymóg MVP: „czytelna informacja o ograniczeniach”): tryb testowy,
 * ostrzeżenia całego sprawdzenia i poszczególnych twierdzeń (np. materiał archiwalny), bez powtórzeń.
 */
function notesOf(body: any, claims: any[]): string[] {
  const notes: string[] = [];
  if (body?.mock) notes.push('Tryb testowy: to nie jest prawdziwe sprawdzenie.');
  for (const n of [
    ...(Array.isArray(body?.limitations) ? body.limitations : []),
    ...claims.flatMap((c) => (Array.isArray(c?.limitations) ? c.limitations : [])),
  ]) {
    if (typeof n === 'string' && n.trim() && !notes.includes(n.trim())) notes.push(n.trim());
  }
  return notes;
}

/** CheckResponse.live_search -> notka pod wynikiem. */
function toLive(raw: any): LiveSearchView {
  return {
    state: 'done',
    message: typeof raw?.message === 'string' ? raw.message : undefined,
    documents: toSources(raw?.documents),
    improved: !!raw?.improved,
  };
}

/** Same zapytanie będące linkiem -> /verify/url (scrapper pobiera stronę). */
export function asUrl(query: string): string | null {
  if (!/^https?:\/\/\S+$/i.test(query)) return null;
  try {
    return new URL(query).href;
  } catch {
    return null;
  }
}

/** Link tylko http(s): URL źródła pochodzi z danych pobranych ze stron. */
function toSources(raw: unknown): SourceLink[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((s: any): SourceLink[] => {
    let parsed: URL;
    try {
      parsed = new URL(s?.url);
    } catch {
      return [];
    }
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return [];
    return [
      {
        url: parsed.href,
        host: parsed.hostname.replace(/^www\./, ''),
        title: s.title || parsed.href,
        publisher: s.publisher ?? undefined,
        date: typeof s.published_at === 'string' ? s.published_at.slice(0, 10) : undefined,
        kind: SOURCE_KINDS[s.source_type],
        quote: s.fragment ?? undefined,
      },
    ];
  });
}
