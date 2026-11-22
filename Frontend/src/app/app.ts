import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { DiscordFab } from './layout/discord-fab/discord-fab';
import { Footer } from './layout/footer/footer';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, Footer, DiscordFab],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main>
      <router-outlet />
    </main>
    <app-footer />
    <app-discord-fab />
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 100dvh;
    }
    main {
      flex: 1;
      display: flex;
      flex-direction: column;
    }
  `,
})
export class App {}
