import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-footer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <a href="https://hacknation.pl" target="_blank" rel="noopener">HackNation 2026</a>
    <a class="msz" href="https://www.gov.pl/web/dyplomacja" target="_blank" rel="noopener">
      Ministerstwo Spraw Zagranicznych
    </a>
  `,
  styles: `
    :host {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 24px;
      padding: 12px 24px;
      background-color: var(--prawdowid-bg-footer);
      font-size: var(--prawdowid-fs-meta);
    }

    /* Mobile: zamiast paska tylko szary napis HackNation 2026 na środku */
    @media (max-width: 600px) {
      :host {
        justify-content: center;
        background-color: transparent;
        padding: 12px 16px calc(16px + env(safe-area-inset-bottom));
      }
      a {
        color: var(--prawdowid-border-light);
      }
      .msz {
        display: none;
      }
    }
  `,
})
export class Footer {}
