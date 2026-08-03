import time
from collections import defaultdict
import settings

class RateLimiter:
    def __init__(self):
        self._user_timestamps: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, uid: int) -> bool:
        self.cleanup_expired(uid)
        timestamps = self._user_timestamps[uid]
        if len(timestamps) >= settings.RATE_LIMIT_MAX_REQUESTS:
            return False
        timestamps.append(time.time())
        return True

    def get_remaining_cooldown(self, uid: int) -> float:
        self.cleanup_expired(uid)
        timestamps = self._user_timestamps[uid]
        if not timestamps:
            return 0.0
        return max(0.0, timestamps[0] + settings.RATE_LIMIT_WINDOW_SECONDS - time.time())

    def cleanup_expired(self, uid: int) -> None:
        now = time.time()
        self._user_timestamps[uid] = [
            ts for ts in self._user_timestamps[uid]
            if now - ts < settings.RATE_LIMIT_WINDOW_SECONDS
        ]

rate_limiter = RateLimiter()
