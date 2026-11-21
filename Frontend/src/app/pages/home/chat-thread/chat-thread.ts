import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { ConnectionStatus } from '../../../core/realtime/realtime.service';
import { SearchResult } from '../../../core/search.models';

/**
 * Widok rozmowy po wysłaniu zapytania:
 * - po prawej dymek użytkownika (ten sam `view-transition-name` co pasek wyszukiwania → płynne przejście),
 * - po lewej dymek bota (na razie loader + status połączenia).
 */
@Component({
  selector: 'app-chat-thread',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chat-thread.html',
  styleUrl: './chat-thread.css',
})
export class ChatThread {
  readonly query = input.required<string>();
  readonly imageUrl = input<string | null>(null);
  readonly status = input.required<ConnectionStatus>();
  readonly results = input<SearchResult[] | null>(null);
}
