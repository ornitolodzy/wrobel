import {
  ChangeDetectionStrategy,
  Component,
  DOCUMENT,
  DestroyRef,
  Injector,
  afterNextRender,
  inject,
  signal,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Logo } from '../../shared/logo/logo';
import { ImageCropper } from '../../shared/image-cropper/image-cropper';
import { ChatThread } from './chat-thread/chat-thread';
import { RealtimeService } from '../../core/realtime/realtime.service';
import {
  BotMessage,
  ChatMessage,
  SearchRequestPayload,
  SearchResultsPayload,
} from '../../core/search.models';

interface AttachedImage {
  blob: Blob;
  url: string;
}

/**
 * idle    — ekran startowy (logo + pasek na środku)
 * sending — „Szukaj” wysuwa się w prawo pod krawędź paska
 * chat    — logo w rogu, rozmowa, pole na kolejne pytanie na dole
 */
type Phase = 'idle' | 'sending' | 'chat';

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const HINT_MS = 3500;
/** Czas wysuwania przycisku „Szukaj” — musi pasować do transition w home.css. */
const SLIDE_OUT_MS = 260;

/** Przykłady wypisywane w pustym polu wyszukiwania. */
const EXAMPLES = [
  'Czy MSZ wydało komunikat w sprawie wiz?',
  'Sprawdź, czy to zdjęcie jest prawdziwe…',
  'Czy ten cytat ministra jest autentyczny?',
  'Kiedy odbyło się ostatnie posiedzenie Rady UE?',
];
const INITIAL_PLACEHOLDER = 'Wpisz zapytanie…';

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => Promise<void>) => unknown;
};

@Component({
  selector: 'app-home',
  imports: [Logo, ImageCropper, ChatThread, NgTemplateOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './home.html',
  styleUrls: ['./home.css', './home-chat.css'],
  host: {
    '(document:paste)': 'onPaste($event)',
    '(document:dragenter)': 'onDragEnter($event)',
    '(document:dragover)': 'onDragOver($event)',
    '(document:dragleave)': 'onDragLeave()',
    '(document:drop)': 'onDrop($event)',
  },
})
export class Home {
  private readonly realtime = inject(RealtimeService);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT) as ViewTransitionDocument;

  protected readonly phase = signal<Phase>('idle');
  protected readonly status = this.realtime.status;
  protected readonly query = signal('');
  protected readonly messages = signal<ChatMessage[]>([]);

  /** Object URL zdjęcia czekającego na docięcie (otwiera dialog). */
  protected readonly cropSrc = signal<string | null>(null);
  /** Docięte zdjęcie dołączone do następnego zapytania. */
  protected readonly image = signal<AttachedImage | null>(null);
  protected readonly dragging = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly hint = signal<string | null>(null);
  protected readonly ONE_IMAGE_HINT = 'Możesz dodać tylko jedno zdjęcie. Usuń obecne, aby dodać inne.';

  /** Placeholder pola startowego — „pisany na żywo” z listy przykładów. */
  protected readonly typed = signal(INITIAL_PLACEHOLDER);

  private nextId = 0;
  private dragDepth = 0;
  private hintTimer: ReturnType<typeof setTimeout> | undefined;
  private slideTimer: ReturnType<typeof setTimeout> | undefined;
  private typeTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    this.realtime
      .on<SearchResultsPayload>('search:results')
      .pipe(takeUntilDestroyed())
      .subscribe((payload) => this.applyResults(payload));

    afterNextRender(() => this.startTypewriter());

    inject(DestroyRef).onDestroy(() => {
      clearTimeout(this.hintTimer);
      clearTimeout(this.slideTimer);
      clearTimeout(this.typeTimer);
      this.revoke(this.cropSrc());
      this.revoke(this.image()?.url);
      this.revokeMessageImages();
    });
  }

  protected onInput(event: Event): void {
    this.query.set((event.target as HTMLInputElement).value);
  }

  protected search(event: SubmitEvent): void {
    event.preventDefault();
    const phase = this.phase();
    if (phase === 'sending') return;
    const q = this.query().trim();
    const img = this.image();
    if (!q && !img) return;

    this.error.set(null);
    this.clearHint();
    void this.sendSearch(q, img);

    // Kolejne pytanie w trakcie rozmowy — dopisz do wątku i przewiń w dół
    if (phase === 'chat') {
      this.addExchange(q, img);
      afterNextRender(() => this.scrollToBottom(), { injector: this.injector });
      return;
    }

    // Pierwsze pytanie: 1) „Szukaj” chowa się pod krawędź paska, 2) przelot do układu rozmowy
    clearTimeout(this.typeTimer);
    this.phase.set('sending');
    const delay = this.animationsEnabled() ? SLIDE_OUT_MS : 0;
    this.slideTimer = setTimeout(
      () =>
        this.morph(() => {
          this.addExchange(q, img);
          this.phase.set('chat');
        }),
      delay,
    );
  }

  /** Klik w logo w trybie rozmowy — powrót do ekranu startowego (animacja w drugą stronę). */
  protected reset(event: Event): void {
    event.preventDefault();
    if (this.phase() === 'idle') return;
    clearTimeout(this.slideTimer);
    this.morph(() => {
      this.revokeMessageImages();
      this.messages.set([]);
      this.phase.set('idle');
      this.query.set('');
      this.removeImage();
    });
    this.startTypewriter();
  }

  /** Dodaje do wątku pytanie użytkownika i oczekujący dymek bota; czyści pole. */
  private addExchange(text: string, img: AttachedImage | null): void {
    this.messages.update((list) => [
      ...list,
      { id: ++this.nextId, role: 'user', text, imageUrl: img?.url ?? null },
      { id: ++this.nextId, role: 'bot', query: text, results: null, verdict: null },
    ]);
    // URL zdjęcia należy teraz do wiadomości — nie zwalniamy go tutaj
    this.image.set(null);
    this.query.set('');
  }

  private applyResults(payload: SearchResultsPayload): void {
    this.messages.update((list) => {
      const i = list.findIndex(
        (m) => m.role === 'bot' && m.results === null && m.query === payload.query,
      );
      if (i < 0) return list; // odpowiedź na zapytanie, którego już nie ma
      const next = [...list];
      next[i] = {
        ...(list[i] as BotMessage),
        results: payload.results,
        verdict: payload.verdict ?? null,
      };
      return next;
    });
  }

  private async sendSearch(query: string, img: AttachedImage | null): Promise<void> {
    const payload: SearchRequestPayload = { query };
    if (img) payload.image = await blobToDataUrl(img.blob);
    this.realtime.send('search', payload);
  }

  private scrollToBottom(): void {
    const view = this.document.defaultView;
    view?.scrollTo({
      top: this.document.documentElement.scrollHeight,
      behavior: this.animationsEnabled() ? 'smooth' : 'auto',
    });
  }

  // ---------- Placeholder „pisany na żywo” ----------

  private startTypewriter(): void {
    clearTimeout(this.typeTimer);
    if (!this.animationsEnabled()) {
      this.typed.set(EXAMPLES[0]);
      return;
    }
    let example = -1;
    let text = this.typed();
    let deleting = true;

    const step = () => {
      // pauza, gdy placeholder i tak nie jest widoczny (wpisany tekst / ukryta karta)
      if (this.phase() !== 'idle') return;
      if (this.query() || this.document.hidden) {
        this.typeTimer = setTimeout(step, 600);
        return;
      }
      let wait: number;
      if (deleting) {
        text = text.slice(0, -1);
        wait = 22;
        if (!text) {
          deleting = false;
          example = (example + 1) % EXAMPLES.length;
          wait = 350;
        }
      } else {
        const target = EXAMPLES[example];
        text = target.slice(0, text.length + 1);
        wait = 40 + Math.random() * 45;
        if (text === target) {
          deleting = true;
          wait = 2200;
        }
      }
      this.typed.set(text);
      this.typeTimer = setTimeout(step, wait);
    };

    this.typeTimer = setTimeout(step, 1800);
  }

  // ---------- View Transitions ----------

  /**
   * Zmiana układu przez View Transitions API: przeglądarka sama animuje elementy
   * z tym samym `view-transition-name` (logo, pasek -> dymek) między starą a nową pozycją.
   * Bez wsparcia API (lub przy prefers-reduced-motion) — zmiana natychmiastowa.
   */
  private morph(update: () => void): void {
    if (typeof this.document.startViewTransition !== 'function' || !this.animationsEnabled()) {
      update();
      return;
    }
    this.document.startViewTransition(
      () =>
        new Promise<void>((resolve) => {
          update();
          afterNextRender(() => resolve(), { injector: this.injector });
        }),
    );
  }

  private animationsEnabled(): boolean {
    return !this.document.defaultView?.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  // ---------- Wybór zdjęcia: plik / wklejenie / przeciągnięcie ----------

  protected onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = ''; // pozwala wybrać ten sam plik ponownie
    if (file) this.openCropper(file);
  }

  protected onPaste(event: ClipboardEvent): void {
    if (this.cropSrc() || this.phase() === 'sending') return;
    const file = firstImage(event.clipboardData);
    if (!file) return; // zwykły tekst — normalne wklejanie
    event.preventDefault();
    this.openCropper(file);
  }

  protected onDragEnter(event: DragEvent): void {
    if (!hasFiles(event)) return;
    this.dragDepth++;
    this.dragging.set(true);
  }

  protected onDragOver(event: DragEvent): void {
    if (!hasFiles(event)) return;
    event.preventDefault(); // bez tego przeglądarka otworzy plik zamiast wywołać drop
    event.dataTransfer!.dropEffect = 'copy';
  }

  protected onDragLeave(): void {
    this.dragDepth = Math.max(0, this.dragDepth - 1);
    if (this.dragDepth === 0) this.dragging.set(false);
  }

  protected onDrop(event: DragEvent): void {
    if (!hasFiles(event)) return;
    event.preventDefault();
    this.dragDepth = 0;
    this.dragging.set(false);
    if (this.phase() === 'sending') return;
    const file = firstImage(event.dataTransfer);
    if (file) this.openCropper(file);
    else this.error.set('To nie jest plik graficzny.');
  }

  // ---------- Cropper ----------

  protected onCropped(blob: Blob): void {
    this.closeCropper();
    this.revoke(this.image()?.url);
    this.image.set({ blob, url: URL.createObjectURL(blob) });
  }

  protected closeCropper(): void {
    this.revoke(this.cropSrc());
    this.cropSrc.set(null);
  }

  protected removeImage(): void {
    this.revoke(this.image()?.url);
    this.image.set(null);
    this.clearHint();
  }

  protected showOneImageHint(): void {
    clearTimeout(this.hintTimer);
    this.hint.set(this.ONE_IMAGE_HINT);
    this.hintTimer = setTimeout(() => this.hint.set(null), HINT_MS);
  }

  private clearHint(): void {
    clearTimeout(this.hintTimer);
    this.hint.set(null);
  }

  private openCropper(file: File): void {
    this.error.set(null);
    if (this.image()) {
      this.showOneImageHint();
      return;
    }
    if (!file.type.startsWith('image/')) {
      this.error.set('To nie jest plik graficzny.');
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      this.error.set('Zdjęcie jest za duże (maks. 25 MB).');
      return;
    }
    this.revoke(this.cropSrc());
    this.cropSrc.set(URL.createObjectURL(file));
  }

  private revokeMessageImages(): void {
    for (const m of this.messages()) {
      if (m.role === 'user') this.revoke(m.imageUrl);
    }
  }

  private revoke(url: string | null | undefined): void {
    if (url) URL.revokeObjectURL(url);
  }
}

function hasFiles(event: DragEvent): boolean {
  return !!event.dataTransfer?.types.includes('Files');
}

function firstImage(data: DataTransfer | null): File | null {
  if (!data) return null;
  for (const file of Array.from(data.files)) {
    if (file.type.startsWith('image/')) return file;
  }
  return null;
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}
