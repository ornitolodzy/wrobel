import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ConnectionStatus } from '../../../core/realtime/realtime.service';
import { ChatMessage } from '../../../core/search.models';
import { VerdictTag } from '../../../shared/verdict-tag/verdict-tag';

/**
 * Widok rozmowy:
 * - dymki użytkownika po prawej (pierwszy ma ten sam `view-transition-name` co pasek → płynne przejście),
 * - dymki bota po lewej: miejsce na tag werdyktu + loader albo wyniki.
 */
@Component({
  selector: 'app-chat-thread',
  imports: [VerdictTag, DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chat-thread.html',
  styleUrls: ['./chat-thread.css', './chat-live.css'],
})
export class ChatThread {
  readonly messages = input.required<ChatMessage[]>();
  readonly status = input.required<ConnectionStatus>();
  /** „Usuń zapis z serwera” pod odpowiedzią (id wiadomości bota). */
  readonly remove = output<number>();
  /** Znacznik czasu nad dymkiem, np. 22.11.2026, 14:05 */
  protected readonly STAMP = 'dd.MM.yyyy, HH:mm';
}
