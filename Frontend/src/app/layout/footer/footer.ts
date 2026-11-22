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
    /* Nad polem czatu (z-index 2): composer wysuwa się spod stopki, nie nad nią */
    :host {
      position: relative;
      z-index: 3;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 24px;
      padding: 12px 24px;
      background-color: var(--prawdowid-bg-footer);
      font-size: var(--prawdowid-fs-meta);
    }

    /* Mobile: zamiast paska tylko szary napis HackNation 2026 na środku. Stopka przyklejona
       do dołu ekranu; w jej pasku, w prawym rogu, siedzi FAB Discorda. */
    @media (max-width: 600px) {
      :host {
        position: sticky;
        bottom: 0;
        box-sizing: border-box;
        height: calc(var(--prawdowid-footer-h-mobile) + env(safe-area-inset-bottom));
        align-items: center;
        justify-content: center;
        background-color: var(--prawdowid-bg-canvas);
        padding: 0 16px env(safe-area-inset-bottom);
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
