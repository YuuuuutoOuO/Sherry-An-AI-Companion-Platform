import logging
import asyncio

logger = logging.getLogger(__name__)

class VoiceRecorder:
    def __init__(self, vad: object = None) -> None:
        self.vad = vad
        self.queue: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()

    # Note: discord.py does not natively support voice receive well yet.
    # We provide a stub interface for future extension or using discord.ext.voice_recv
    async def get_next_utterance(self) -> tuple[int, bytes] | None:
        # Simulate blocking until voice received
        # return (user_id, audio_bytes)
        try:
            return await self.queue.get()
        except asyncio.CancelledError:
            return None

    def write(self, user_id: int, data: bytes) -> None:
        # This would be called by the audio sink
        pass

    def clear(self) -> None:
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
