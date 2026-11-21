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
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Logo } from '../../shared/logo/logo';
import { ImageCropper } from '../../shared/image-cropper/image-cropper';
import { ChatThread } from './chat-thread/chat-thread';
import { RealtimeService } from '../../core/realtime/realtime.service';
import {
  SearchRequestPayload,
  SearchResult,
  SearchResultsPayload,
} from '../../core/search.models';

interface AttachedImage {
  blob: Blob;
  url: string;
}

/**
 * idle    — ekran startowy (logo + pasek na środku)
 * sending — „Szukaj” wysuwa się w prawo pod krawędź paska
 * chat    — logo w rogu, pasek jako dymek po prawej, dymek bota po lewej
 */
type Phase = 'idle' | 'sending' | 'chat';

const MAX_FILE_BYTES = 25 * 1024 * 1024;
const HINT_MS = 3500;
/** Czas wysuwania przycisku „Szukaj” — musi pasować do transition w home.css. */
const SLIDE_OUT_MS = 260;

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => Promise<void>) => unknown;
};

@Component({
  selector: 'app-home',
  imports: [Logo, ImageCropper, ChatThread],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './home.html',
  styleUrl: './home.css',
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
  protected readonly loading = signal(false);
  protected readonly results = signal<SearchResult[] | null>(null);

  /** Object URL zdjęcia czekającego na docięcie (otwiera dialog). */
  protected readonly cropSrc = signal<string | null>(null);
  /** Docięte zdjęcie dołączone do zapytania. */
  protected readonly image = signal<AttachedImage | null>(null);
  protected readonly dragging = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly hint = signal<string | null>(null);
  protected readonly ONE_IMAGE_HINT = 'Możesz dodać tylko jedno zdjęcie. Usuń obecne, aby dodać inne.';

  private dragDepth = 0;
  private hintTimer: ReturnType<typeof setTimeout> | undefined;
  private slideTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    this.realtime
      .on<SearchResultsPayload>('search:results')
      .pipe(takeUntilDestroyed())
      .subscribe((payload) => {
        if (payload.query !== this.query().trim()) return; // odpowiedź na stare zapytanie
        this.results.set(payload.results);
        this.loading.set(false);
      });

    inject(DestroyRef).onDestroy(() => {
      clearTimeout(this.hintTimer);
      clearTimeout(this.slideTimer);
      this.revoke(this.cropSrc());
      this.revoke(this.image()?.url);
    });
  }

  protected onInput(event: Event): void {
    this.query.set((event.target as HTMLInputElement).value);
  }

  protected search(event: SubmitEvent): void {
    event.preventDefault();
    if (this.phase() !== 'idle') return;
    const q = this.query().trim();
    const img = this.image();
    if (!q && !img) return;

    this.query.set(q);
    this.loading.set(true);
    this.results.set(null);
    this.error.set(null);
    this.hint.set(null);
    void this.sendSearch(q, img);

    // 1) „Szukaj” chowa się pod krawędź paska, 2) przelot logo i paska do układu rozmowy
    this.phase.set('sending');
    const delay = this.animationsEnabled() ? SLIDE_OUT_MS : 0;
    this.slideTimer = setTimeout(() => this.morph(() => this.phase.set('chat')), delay);
  }

  /** Klik w logo w trybie rozmowy — powrót do ekranu startowego (animacja w drugą stronę). */
  protected reset(event: Event): void {
    event.preventDefault();
    if (this.phase() === 'idle') return;
    clearTimeout(this.slideTimer);
    this.morph(() => {
      this.phase.set('idle');
      this.query.set('');
      this.removeImage();
      this.results.set(null);
      this.loading.set(false);
    });
  }

  private async sendSearch(query: string, img: AttachedImage | null): Promise<void> {
    const payload: SearchRequestPayload = { query };
    if (img) payload.image = await blobToDataUrl(img.blob);
    this.realtime.send('search', payload);
  }

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
    if (this.cropSrc() || this.phase() !== 'idle') return;
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
    if (this.phase() !== 'idle') return;
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
    clearTimeout(this.hintTimer);
    this.hint.set(null);
  }

  protected showOneImageHint(): void {
    clearTimeout(this.hintTimer);
    this.hint.set(this.ONE_IMAGE_HINT);
    this.hintTimer = setTimeout(() => this.hint.set(null), HINT_MS);
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
