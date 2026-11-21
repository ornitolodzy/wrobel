/** Kontrakt wiadomości wyszukiwania po WebSockecie — dopasuj do backendu. */

export interface SearchResult {
  id: string;
  title: string;
  url: string;
  snippet?: string;
  source?: string;
  date?: string;
}

/** Werdykt weryfikacji informacji (tag w dymku bota). */
export type Verdict = 'true' | 'false' | 'unverified';

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
}

// ---------- Model rozmowy (tylko front) ----------

export interface UserMessage {
  id: number;
  role: 'user';
  text: string;
  /** Object URL dociętego zdjęcia (zwalniany przy resecie rozmowy). */
  imageUrl: string | null;
}

export interface BotMessage {
  id: number;
  role: 'bot';
  /** Zapytanie, na które odpowiada — do dopasowania odpowiedzi z WebSocketu. */
  query: string;
  /** `null` = czekamy na odpowiedź (loader). */
  results: SearchResult[] | null;
  verdict: Verdict | null;
}

export type ChatMessage = UserMessage | BotMessage;
