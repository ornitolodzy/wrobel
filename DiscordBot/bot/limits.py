"""Per-user sliding-window limit. The gateway rate-limits by IP, and every Discord user reaches
it from the bot's single IP, so without this one person could use up the limit for everyone."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class UserLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def hit(self, user_id: int) -> int:
        """0 = allowed (and counted); otherwise seconds to wait."""
        now = self.clock()
        window = self._hits[user_id]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return max(1, int(60 - (now - window[0])) + 1)
        window.append(now)
        if len(self._hits) > 10_000:  # forget idle users
            for uid in [u for u, w in self._hits.items() if not w or now - w[-1] >= 60]:
                del self._hits[uid]
        return 0
