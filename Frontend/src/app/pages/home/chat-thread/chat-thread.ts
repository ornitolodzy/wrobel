import { ChangeDetectionStrategy, Component, input } from '@angular/core';
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
  imports: [VerdictTag],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chat-thread.html',
  styleUrl: './chat-thread.css',
})
export class ChatThread {
  readonly messages = input.required<ChatMessage[]>();
  readonly status = input.required<ConnectionStatus>();
}
