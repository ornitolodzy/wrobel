import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * Logo wróbel: logotyp + sygnet wróbla po prawej (SIW §2).
 * `animated` — co kilka sekund ptak podskakuje. Animacja działa na całym obrazku
 * (transform), więc nie zależy od rysunku i przetrwa podmianę logo.png.
 */
@Component({
  selector: 'app-logo',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[class.animated]': 'animated()' },
  template: `
    <span class="logotyp">wróbel</span>
    <img class="sygnet" src="/logo.png" width="192" height="192" alt="" fetchpriority="high" />
  `,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      gap: 0.3em;
      font-size: var(--prawdowid-fs-logo);
      line-height: 1;
      color: var(--prawdowid-text-primary);
    }
    .sygnet {
      width: 1.2em;
      height: 1.2em;
      flex: none;
      transform-origin: 50% 100%;
    }
    :host(.animated) .sygnet {
      animation: hop 5.5s ease-in-out 1.2s infinite;
    }
    .logotyp {
      font-weight: 600;
      letter-spacing: -0.02em;
    }
    /* przysiad -> podskok z lekkim obrotem -> lądowanie, potem długa pauza */
    @keyframes hop {
      0%,
      14%,
      100% {
        transform: none;
      }
      3% {
        transform: scale(1.06, 0.92);
      }
      6.5% {
        transform: translateY(-16%) rotate(-7deg) scale(0.97, 1.04);
      }
      10% {
        transform: scale(1.04, 0.95);
      }
      12% {
        transform: none;
      }
    }
  `,
})
export class Logo {
  readonly animated = input(false);
}
