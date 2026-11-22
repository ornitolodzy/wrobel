import { ChangeDetectionStrategy, Component } from '@angular/core';
import { DISCORD_BOT_URL } from '../../core/links';

/**
 * Pływający przycisk w prawym dolnym rogu: przenosi do bota Wróbel na Discordzie (kanał 2).
 * Kolory z palety SIW; po najechaniu kolor Discorda (blurple), żeby było jasne, dokąd prowadzi.
 */
@Component({
  selector: 'app-discord-fab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <a
      class="fab"
      [href]="url"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Wróbel na Discordzie (otwiera się w nowej karcie)"
    >
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M20.317 4.37a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.865-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.74 19.74 0 0 0 3.677 4.37a.07.07 0 0 0-.032.028C.533 9.046-.32 13.58.099 18.058a.082.082 0 0 0 .031.056 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.1 13.1 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .078-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.128 12.3 12.3 0 0 1-1.873.891.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.84 19.84 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.182 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z"
        />
      </svg>
      <span class="label">Wróbel na Discordzie</span>
    </a>
  `,
  styles: `
    :host {
      position: fixed;
      z-index: 10;
      right: calc(24px + env(safe-area-inset-right));
      bottom: calc(64px + env(safe-area-inset-bottom)); /* nad paskiem stopki (~44 px) */
    }

    .fab {
      position: relative;
      display: grid;
      place-items: center;
      width: 52px;
      height: 52px;
      border-radius: var(--prawdowid-radius-pill);
      color: var(--prawdowid-text-on-accent);
      background-color: var(--prawdowid-accent-main);
      box-shadow: var(--prawdowid-shadow-raised);
      transition:
        background-color 200ms ease,
        transform 200ms cubic-bezier(0.22, 1, 0.36, 1),
        box-shadow 200ms ease;
    }

    .fab:hover,
    .fab:focus-visible {
      background-color: #5865f2; /* Discord blurple */
      color: #fff;
      transform: translateY(-2px);
    }

    .fab:active {
      transform: translateY(0) scale(0.96);
    }

    .fab:focus-visible {
      outline: none;
      box-shadow:
        var(--prawdowid-shadow-raised),
        var(--prawdowid-focus-glow),
        0 0 0 2px var(--prawdowid-bg-canvas),
        0 0 0 4px #5865f2;
    }

    svg {
      width: 26px;
      height: 26px;
      fill: currentColor;
    }

    /* Podpis wysuwa się w lewo przy najechaniu / fokusie z klawiatury */
    .label {
      position: absolute;
      right: calc(100% + 10px);
      top: 50%;
      padding: 6px 12px;
      border-radius: var(--prawdowid-radius-pill);
      white-space: nowrap;
      font-size: var(--prawdowid-fs-meta);
      font-weight: 500;
      color: var(--prawdowid-text-on-accent);
      background-color: var(--prawdowid-text-primary);
      box-shadow: var(--prawdowid-shadow-soft);
      opacity: 0;
      transform: translate(6px, -50%);
      pointer-events: none;
      transition:
        opacity 160ms ease,
        transform 160ms ease;
    }

    .fab:hover .label,
    .fab:focus-visible .label {
      opacity: 1;
      transform: translate(0, -50%);
    }

    /* Mobile: mniejszy, bez podpisu, w prawym dolnym rogu w pasku stopki (na wysokości
       „HackNation 2026”); composer czatu stoi nad stopką, więc nic się nie nakłada */
    @media (max-width: 600px) {
      :host {
        right: calc(16px + env(safe-area-inset-right));
        bottom: calc((var(--prawdowid-footer-h-mobile) - 44px) / 2 + env(safe-area-inset-bottom));
      }
      .fab {
        width: 44px;
        height: 44px;
      }
      svg {
        width: 22px;
        height: 22px;
      }
      .label {
        display: none;
      }
    }

    @media (prefers-reduced-motion: reduce) {
      .fab,
      .label {
        transition: none;
      }
      .fab:hover,
      .fab:focus-visible {
        transform: none;
      }
    }

    @media print {
      :host {
        display: none;
      }
    }
  `,
})
export class DiscordFab {
  protected readonly url = DISCORD_BOT_URL;
}
