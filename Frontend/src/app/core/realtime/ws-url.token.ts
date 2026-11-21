import { InjectionToken, PLATFORM_ID, inject } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';

/**
 * Adres serwera WebSocket.
 * Domyślnie: ten sam host co strona, ścieżka `/ws` (ws:// lub wss:// zależnie od protokołu).
 * Nadpisz w app.config.ts: `{ provide: WS_URL, useValue: 'wss://api.example.com/ws' }`.
 */
export const WS_URL = new InjectionToken<string>('WS_URL', {
  providedIn: 'root',
  factory: () => {
    if (!isPlatformBrowser(inject(PLATFORM_ID))) return '';
    const { protocol, host } = window.location;
    return `${protocol === 'https:' ? 'wss' : 'ws'}://${host}/ws`;
  },
});
