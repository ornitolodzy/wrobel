/** Kontrakt wiadomości wyszukiwania po WebSockecie — dopasuj do backendu. */

export interface SearchResult {
  id: string;
  title: string;
  url: string;
  snippet?: string;
  source?: string;
  date?: string;
  /** Werdykt tego twierdzenia (gdy w pytaniu było kilka twierdzeń). */
  verdict?: Verdict;
  /** Siła przesłanek po polsku (wysoka / średnia / niska). */
  strength?: string;
  /** Wszystkie źródła twierdzenia, z linkiem do artykułu i cytatem. */
  sources?: SourceLink[];
}

export interface SourceLink {
  url: string;
  host: string;
  title: string;
  publisher?: string;
  date?: string;
  kind?: string;
  quote?: string;
}

/** Werdykt weryfikacji informacji (tag w dymku bota). */
export type Verdict = 'true' | 'false' | 'misleading' | 'unverified' | 'unverifiable';

/** Klient -> serwer: `{ type: 'search', payload: SearchRequestPayload }` */
export interface SearchRequestPayload {
  query: string;
  /** Docięte zdjęcie jako data URL (image/jpeg, max 1280px). */
  image?: string;
}

/** Serwer -> klient: `{ type: 'search:results', payload: SearchResultsPayload }` */
export interface SearchResultsPayload {
  query: string;
  results: SearchResult[];
  verdict?: Verdict;
  /** Id sprawdzenia w API Gateway (do wyszukiwania na żywo). */
  checkId?: string;
  /** W bazie brak potwierdzonych danych -> można spróbować wyszukać na żywo. */
  liveEligible?: boolean;
  /** Wynik wyszukiwania na żywo (gdy ta odpowiedź jest już po nim). */
  live?: LiveSearchView;
  /** Ograniczenia odpowiedzi do pokazania pod wynikiem. */
  notes?: string[];
  /** Pętla informacji: stan sprawy moderatora (rekomendacja / przekazanie). */
  caseNotes?: string[];
  /** Tekst odczytany ze zdjęcia (OCR), żeby użytkownik widział, co sprawdzono. */
  ocrText?: string;
  /** Zapisy sprawdzenia w API Gateway (z wyszukiwaniem na żywo: dwa), do usunięcia. */
  checkIds?: string[];
}

/** Stan wyszukiwania na żywo w zaufanych witrynach (gdy w bazie brak potwierdzonych danych). */
export interface LiveSearchView {
  state: 'searching' | 'done' | 'failed';
  message?: string;
  /** Strony znalezione na żywo i dołączone do bazy wiedzy. */
  documents?: SourceLink[];
  /** Nowe źródła pozwoliły ocenić twierdzenie. */
  improved?: boolean;
}

// ---------- Model rozmowy (tylko front) ----------

export interface UserMessage {
  id: number;
  role: 'user';
  text: string;
  /** Object URL dociętego zdjęcia (zwalniany przy resecie rozmowy). */
  imageUrl: string | null;
  /** Kiedy użytkownik wysłał pytanie (znacznik nad dymkiem). */
  sentAt: Date;
}

export interface BotMessage {
  id: number;
  role: 'bot';
  /** Zapytanie, na które odpowiada — do dopasowania odpowiedzi z WebSocketu. */
  query: string;
  /** `null` = czekamy na odpowiedź (loader). */
  results: SearchResult[] | null;
  verdict: Verdict | null;
  /** Kiedy przyszła odpowiedź; `null` = jeszcze czekamy. */
  answeredAt: Date | null;
  checkId?: string;
  live?: LiveSearchView;
  notes?: string[];
  caseNotes?: string[];
  ocrText?: string;
  /** Zapisy po stronie serwera; `deleted` po kliknięciu „Usuń zapis”. */
  checkIds?: string[];
  deleted?: 'pending' | 'done' | 'failed';
}

export type ChatMessage = UserMessage | BotMessage;
