import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { Verdict } from '../../core/search.models';

const LABELS: Record<Verdict, string> = {
  true: 'Prawdziwe',
  false: 'Fałszywe',
  misleading: 'Wprowadza w błąd',
  unverified: 'Niepotwierdzone',
  unverifiable: 'Nieweryfikowalne',
};

/** Owalny tag werdyktu w dymku bota. Kolory: tokeny --prawdowid-verdict-* w styles.css. */
@Component({
  selector: 'app-verdict-tag',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '[attr.data-verdict]': 'verdict()' },
  template: `<span class="dot" aria-hidden="true"></span>{{ label() }}`,
  styles: `
    :host {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 10px;
      border-radius: var(--prawdowid-radius-pill);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    :host([data-verdict='true']) {
      color: var(--prawdowid-verdict-true);
      background: var(--prawdowid-verdict-true-bg);
    }
    :host([data-verdict='false']) {
      color: var(--prawdowid-verdict-false);
      background: var(--prawdowid-verdict-false-bg);
    }
    :host([data-verdict='misleading']) {
      color: var(--prawdowid-verdict-misleading);
      background: var(--prawdowid-verdict-misleading-bg);
    }
    :host([data-verdict='unverified']),
    :host([data-verdict='unverifiable']) {
      color: var(--prawdowid-verdict-unverified);
      background: var(--prawdowid-verdict-unverified-bg);
    }
    .dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
    }
  `,
})
export class VerdictTag {
  readonly verdict = input.required<Verdict>();
  protected readonly label = computed(() => LABELS[this.verdict()]);
}
