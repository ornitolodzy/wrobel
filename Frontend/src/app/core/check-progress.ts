import { ProgressView, Verdict } from './search.models';

/** Werdykty z API Gateway (zdarzenie "verdicts") -> tag w dymku. Te same co w gateway-results.ts. */
const VERDICTS: Record<string, Verdict> = {
  confirmed: 'true',
  false: 'false',
  misleading: 'misleading',
  insufficient_evidence: 'unverified',
  not_verifiable: 'unverifiable',
};

/** Co robi teraz system — po etapie pipeline'u (zdarzenie "step"). */
const STEPS: Record<string, string> = {
  normalize: 'Analizuję pytanie…',
  extract_claims: 'Szukam twierdzeń do sprawdzenia…',
  build_queries: 'Przygotowuję wyszukiwanie…',
  retrieve: 'Szukam źródeł w bazie wiedzy…',
  select_context: 'Wybieram najlepsze źródła…',
  judge_stance: 'Czytam źródła i porównuję je z twierdzeniami…',
  validate_citations: 'Sprawdzam cytaty…',
  decide: 'Oceniam przesłanki…',
  compose: 'Piszę odpowiedź…',
};

export const INITIAL_PROGRESS: ProgressView = { headline: 'Szukam odpowiedzi…', threads: [] };

/** 2 wątki, 5 wątków, 22 wątki */
export function threadsLabel(n: number): string {
  const last = n % 10;
  const teen = n % 100 >= 12 && n % 100 <= 14;
  if (n === 1) return '1 wątek';
  return `${n} ${last >= 2 && last <= 4 && !teen ? 'wątki' : 'wątków'}`;
}

/**
 * Zdarzenie postępu z /api/v1/verify/*\/stream -> nowy stan widoku. Czysta funkcja: nieznane
 * zdarzenia nic nie zmieniają, więc nowsze API nie psuje starszego frontu.
 */
export function applyProgress(state: ProgressView, event: any): ProgressView {
  const threads = state.threads;
  const many = threads.length > 1;
  switch (event?.stage) {
    case 'received':
      return { ...state, headline: 'Szukam odpowiedzi…' };
    case 'ocr':
      return { ...state, headline: 'Odczytuję tekst ze zdjęcia…' };
    case 'ocr_done':
      return {
        ...state,
        headline: event.chars ? 'Odczytałem tekst ze zdjęcia, szukam w nim twierdzeń…' : state.headline,
      };
    case 'page':
      return { ...state, headline: 'Pobieram treść strony…' };
    case 'step':
      // przy kilku wątkach nagłówek mówi o wątkach, nie o technicznym kroku
      if (many && event.step !== 'compose') return state;
      return { ...state, headline: STEPS[event.step] ?? state.headline };
    case 'claims': {
      const list: any[] = Array.isArray(event.claims) ? event.claims : [];
      const next = list
        .filter((c) => typeof c?.text === 'string' && c.text.trim())
        .map((c) => ({ id: String(c.id), text: c.text.trim(), state: 'pending' as const }));
      if (!next.length) return state;
      return {
        headline:
          next.length > 1
            ? `To pytanie ma ${threadsLabel(next.length)}. Sprawdzam każdy osobno…`
            : 'Szukam źródeł…',
        threads: next,
      };
    }
    case 'evidence': {
      const items: any[] = Array.isArray(event.items) ? event.items : [];
      const sources = new Map(items.map((it) => [String(it.claim_id), Number(it.sources) || 0]));
      return {
        ...state,
        threads: threads.map((t) =>
          sources.has(t.id)
            ? {
                ...t,
                sources: sources.get(t.id),
                // bez źródeł nie ma czego czytać: wątek jest już rozpatrzony
                state: sources.get(t.id) ? t.state : 'checked',
              }
            : t,
        ),
      };
    }
    case 'claim_checked': {
      const next = threads.map((t) =>
        t.id === String(event.claim_id) ? { ...t, state: 'checked' as const } : t,
      );
      // liczone z listy: wątki bez źródeł nie przechodzą przez czytanie, ale też są rozpatrzone
      const done = next.filter((t) => t.state !== 'pending').length;
      return {
        headline: many
          ? `Sprawdzone wątki: ${done} z ${next.length}${done < next.length ? '…' : ''}`
          : state.headline,
        threads: next,
      };
    }
    case 'verdicts': {
      const items: any[] = Array.isArray(event.items) ? event.items : [];
      const verdicts = new Map(items.map((it) => [String(it.claim_id), VERDICTS[it.verdict]]));
      return {
        headline: many ? 'Mam wyniki wszystkich wątków, piszę odpowiedź…' : 'Mam wynik, piszę odpowiedź…',
        threads: threads.map((t) =>
          verdicts.has(t.id) ? { ...t, state: 'done', verdict: verdicts.get(t.id) } : t,
        ),
      };
    }
    default:
      return state;
  }
}

/**
 * Czyta odpowiedź NDJSON (jedna linia = jedno zdarzenie). Wywołuje onEvent dla każdej linii
 * i zwraca linię {"type": "result"} albo null, gdy strumień urwał się przed wynikiem.
 */
export async function readNdjson(
  response: Response,
  onEvent: (event: any) => void,
): Promise<any | null> {
  if (!response.body) return null;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: any = null;
  const handle = (line: string) => {
    if (!line.trim()) return;
    let event: any;
    try {
      event = JSON.parse(line);
    } catch {
      return;
    }
    if (event?.type === 'result') result = event;
    else onEvent(event);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      handle(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  handle(buffer + decoder.decode());
  return result;
}
