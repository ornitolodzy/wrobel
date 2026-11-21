import { DestroyRef, Injectable, PLATFORM_ID, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { Observable, Subject, filter, map } from 'rxjs';
import { WS_URL } from './ws-url.token';

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

/** Koperta wiadomości — dopasuj do protokołu backendu. */
export interface WsMessage<T = unknown> {
  type: string;
  payload?: T;
}

const MAX_BACKOFF_MS = 10_000;
const MAX_QUEUE = 100;

/**
 * Jedno, współdzielone połączenie WebSocket dla całej aplikacji.
 * - Na serwerze (SSR/prerender) nic nie robi — socket otwierany tylko w przeglądarce.
 * - Auto-reconnect z backoffem wykładniczym + jitter, wznowienie po powrocie sieci.
 * - Wiadomości wysłane przed otwarciem trafiają do kolejki i są wysyłane po połączeniu.
 * - Status jako signal (tani w zoneless), strumień wiadomości jako Observable (bez gubienia burstów).
 */
@Injectable({ providedIn: 'root' })
export class RealtimeService {
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));
  private readonly url = inject(WS_URL);

  private socket: WebSocket | null = null;
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manuallyClosed = false;
  private readonly queue: string[] = [];
  private readonly incoming = new Subject<WsMessage>();

  readonly status = signal<ConnectionStatus>('idle');
  readonly messages$: Observable<WsMessage> = this.incoming.asObservable();

  constructor() {
    if (!this.isBrowser) return;
    const onOnline = () => this.status() !== 'open' && this.reconnectNow();
    window.addEventListener('online', onOnline);
    inject(DestroyRef).onDestroy(() => {
      window.removeEventListener('online', onOnline);
      this.disconnect();
    });
  }

  connect(): void {
    if (!this.isBrowser || !this.url) return;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;

    this.manuallyClosed = false;
    this.status.set(this.attempt === 0 ? 'connecting' : 'reconnecting');

    const ws = new WebSocket(this.url);
    this.socket = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.status.set('open');
      this.flushQueue();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        this.incoming.next(JSON.parse(event.data) as WsMessage);
      } catch {
        console.warn('[ws] nie-JSON wiadomość zignorowana', event.data);
      }
    };

    ws.onclose = () => {
      if (this.socket !== ws) return;
      this.socket = null;
      if (this.manuallyClosed) {
        this.status.set('closed');
      } else {
        this.scheduleReconnect();
      }
    };

    // onerror zawsze kończy się onclose — tam obsługujemy reconnect.
    ws.onerror = () => undefined;
  }

  disconnect(): void {
    this.manuallyClosed = true;
    this.clearReconnectTimer();
    this.socket?.close(1000);
    this.socket = null;
    this.status.set('closed');
  }

  send<T>(type: string, payload?: T): void {
    if (!this.isBrowser) return;
    const data = JSON.stringify({ type, payload } satisfies WsMessage<T>);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(data);
    } else {
      if (this.queue.length >= MAX_QUEUE) this.queue.shift();
      this.queue.push(data);
      this.connect();
    }
  }

  /** Strumień payloadów wiadomości danego typu. */
  on<T>(type: string): Observable<T> {
    return this.messages$.pipe(
      filter((m) => m.type === type),
      map((m) => m.payload as T),
    );
  }

  private flushQueue(): void {
    while (this.queue.length && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(this.queue.shift()!);
    }
  }

  private scheduleReconnect(): void {
    this.status.set('reconnecting');
    this.clearReconnectTimer();
    const base = Math.min(MAX_BACKOFF_MS, 250 * 2 ** this.attempt);
    const delay = base / 2 + Math.random() * (base / 2);
    this.attempt++;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private reconnectNow(): void {
    this.clearReconnectTimer();
    this.connect();
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
