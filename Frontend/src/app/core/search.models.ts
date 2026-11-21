/** Kontrakt wiadomości wyszukiwania po WebSockecie — dopasuj do backendu. */

export interface SearchResult {
  id: string;
  title: string;
  url: string;
  snippet?: string;
  source?: string;
  date?: string;
}

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
}
