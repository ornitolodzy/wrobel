import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Logo wróbel: logotyp + sygnet wróbla po prawej (SIW §2). */
@Component({
  selector: 'app-logo',
  changeDetection: ChangeDetectionStrategy.OnPush,
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
    }
    .logotyp {
      font-weight: 600;
      letter-spacing: -0.02em;
    }
  `,
})
export class Logo {}
