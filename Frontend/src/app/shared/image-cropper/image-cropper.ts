import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterNextRender,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Mode = 'move' | 'nw' | 'ne' | 'sw' | 'se';

interface DragState {
  mode: Mode;
  startX: number;
  startY: number;
  start: Rect;
  boxW: number;
  boxH: number;
}

/** Minimalny rozmiar zaznaczenia (ułamek wymiaru zdjęcia). */
const MIN = 0.08;
/** Dłuższy bok wyniku — wystarczy do wyszukiwania, a mało waży w WebSockecie. */
const MAX_OUTPUT_SIDE = 1280;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Lekki cropper (bez zewnętrznych bibliotek) w natywnym <dialog>.
 * Zaznaczenie trzymane w ułamkach 0..1, więc nie zależy od rozmiaru podglądu.
 * Obsługuje mysz i dotyk (Pointer Events).
 */
@Component({
  selector: 'app-image-cropper',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './image-cropper.html',
  styleUrl: './image-cropper.css',
})
export class ImageCropper {
  /** Object URL zdjęcia źródłowego. */
  readonly src = input.required<string>();
  readonly cropped = output<Blob>();
  readonly cancelled = output<void>();

  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');
  private readonly stage = viewChild.required<ElementRef<HTMLElement>>('stage');
  private readonly img = viewChild.required<ElementRef<HTMLImageElement>>('img');

  protected readonly loaded = signal(false);
  protected readonly rect = signal<Rect>({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 });
  private drag: DragState | null = null;
  private done = false;

  constructor() {
    afterNextRender(() => this.dialog().nativeElement.showModal());
  }

  protected start(event: PointerEvent, mode: Mode): void {
    event.preventDefault();
    event.stopPropagation();
    (event.currentTarget as Element).setPointerCapture(event.pointerId);
    const box = this.stage().nativeElement.getBoundingClientRect();
    this.drag = {
      mode,
      startX: event.clientX,
      startY: event.clientY,
      start: { ...this.rect() },
      boxW: box.width,
      boxH: box.height,
    };
  }

  protected move(event: PointerEvent): void {
    const d = this.drag;
    if (!d) return;
    const dx = (event.clientX - d.startX) / d.boxW;
    const dy = (event.clientY - d.startY) / d.boxH;
    let { x, y, w, h } = d.start;

    if (d.mode === 'move') {
      x = clamp(x + dx, 0, 1 - w);
      y = clamp(y + dy, 0, 1 - h);
    } else {
      const [v, hz] = d.mode;
      if (hz === 'w') {
        const nx = clamp(x + dx, 0, x + w - MIN);
        w = x + w - nx;
        x = nx;
      } else {
        w = clamp(w + dx, MIN, 1 - x);
      }
      if (v === 'n') {
        const ny = clamp(y + dy, 0, y + h - MIN);
        h = y + h - ny;
        y = ny;
      } else {
        h = clamp(h + dy, MIN, 1 - y);
      }
    }
    this.rect.set({ x, y, w, h });
  }

  protected end(): void {
    this.drag = null;
  }

  protected confirm(): void {
    const image = this.img().nativeElement;
    const { x, y, w, h } = this.rect();
    const sx = x * image.naturalWidth;
    const sy = y * image.naturalHeight;
    const sw = w * image.naturalWidth;
    const sh = h * image.naturalHeight;
    const scale = Math.min(1, MAX_OUTPUT_SIDE / Math.max(sw, sh));

    const canvas = document.createElement('canvas');
    canvas.width = Math.round(sw * scale);
    canvas.height = Math.round(sh * scale);
    canvas.getContext('2d')!.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        this.done = true;
        this.dialog().nativeElement.close();
        this.cropped.emit(blob);
      },
      'image/jpeg',
      0.9,
    );
  }

  protected cancel(event?: Event): void {
    event?.preventDefault();
    if (this.done) return;
    this.done = true;
    this.dialog().nativeElement.close();
    this.cancelled.emit();
  }
}
