from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .model import PrimaryModelClient

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_VALID_CATEGORIES = {"diary", "interest", "reflection", "activity"}


@dataclass(slots=True)
class TurnRecord:
    channel_id: str
    transcript: str
    reply: str
    reason: str
    created_at: float = field(default_factory=time.time)


class MemoryConsolidator:
    """背景記憶整併；不阻塞回覆路徑，失敗即丟棄。"""

    def __init__(
        self,
        *,
        client: PrimaryModelClient,
        min_turns: int = 8,
        max_interval: float = 600.0,
        max_buffer: int = 24,
    ) -> None:
        self.client = client
        self.min_turns = max(2, int(min_turns))
        self.max_interval = max(60.0, float(max_interval))
        self.max_buffer = max(self.min_turns, int(max_buffer))
        self._records: list[TurnRecord] = []
        self._last_run_at = time.time()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def observe_turn(self, *, channel_id: str, transcript: str, reply: str, reason: str) -> None:
        if not reply.strip():
            return
        self._records.append(TurnRecord(channel_id, transcript, reply, reason))
        if len(self._records) > self.max_buffer:
            self._records = self._records[-self.max_buffer:]
        if self._should_run():
            self.schedule()

    def _should_run(self) -> bool:
        if self._running or not self._records:
            return False
        if len(self._records) >= self.min_turns:
            return True
        if self._last_run_at <= 0 or len(self._records) < 2:
            return False
        return (time.time() - self._last_run_at) >= self.max_interval

    def schedule(self) -> None:
        if self._running or not self._records:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._running = True
        self._task = loop.create_task(self._run(), name="cyber-v2-memory")

    async def _run(self) -> None:
        import settings

        records = self._records[: self.max_buffer]
        self._records = self._records[len(records):]
        try:
            prompt = self._build_prompt(records)
            result = await self.client.generate(
                system_prompt=(
                    "你是雪莉的記憶整併器。只輸出嚴格 JSON，不要 markdown、不要解釋。"
                    "只記錄對話中真正發生的事與雪莉自己的想法，不要編造。"
                ),
                user_prompt=prompt,
                max_tokens=600,
                timeout=60.0,
                priority=int(getattr(settings, "PRIORITY_VIBE_UPDATE", 5)),
                task_id=f"cyber-memory-{int(time.time())}",
            )
            if not result.ok:
                return
            payload = self._parse_json(result.text)
            if payload:
                await asyncio.to_thread(self._write, payload, records)
            self._last_run_at = time.time()
        except Exception as exc:
            logger.debug("[CyberV2] 記憶整併失敗（丟棄）: %s", exc)
        finally:
            self._running = False
            self._task = None
            if self._should_run():
                self.schedule()

    def _build_prompt(self, records: list[TurnRecord]) -> str:
        chunks: list[str] = []
        for idx, record in enumerate(records[-8:], start=1):
            chunks.append(
                f"### 回合 {idx}\n"
                f"對話：\n{record.transcript[:1200]}\n"
                f"雪莉回覆：{record.reply[:400]}\n"
                f"原因：{record.reason}"
            )
        return (
            "請根據以下回合，輸出 JSON：\n"
            '{"actions":[{"summary":"第一人稱一句話","importance":1-5}],'
            '"episodes":[{"category":"diary|interest|reflection|activity","content":"第一人稱，80字內","tags":["關鍵字"]}],'
            '"interests":["最多3個具體關鍵字"]}\n'
            "規則：actions 最多 3 筆、episodes 最多 3 筆、interests 最多 3 個；"
            "沒有值得記的就給空陣列。\n\n"
            + "\n\n".join(chunks)
        )

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        cleaned = _JSON_FENCE.sub("", str(text or "").strip())
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, payload: dict[str, Any], records: list[TurnRecord]) -> None:
        from handlers.memory.self_memory import add_self_episode, record_self_action

        channel_id = records[-1].channel_id if records else ""
        actions = payload.get("actions")
        if isinstance(actions, list):
            for item in actions[:3]:
                if not isinstance(item, dict):
                    continue
                summary = str(item.get("summary") or "").strip()
                if len(summary) < 4:
                    continue
                try:
                    importance = max(1, min(5, int(item.get("importance", 3))))
                except (TypeError, ValueError):
                    importance = 3
                record_self_action(
                    tool="CYBER_FRIEND",
                    summary=summary[:200],
                    channel_id=channel_id,
                    detail={"source": "cyber_v2_memory"},
                    importance=importance,
                )

        episodes = payload.get("episodes")
        if isinstance(episodes, list):
            for item in episodes[:3]:
                if not isinstance(item, dict):
                    continue
                category = str(item.get("category") or "reflection").strip().lower()
                if category not in _VALID_CATEGORIES:
                    category = "reflection"
                content = str(item.get("content") or "").strip()
                if len(content) < 4:
                    continue
                tags = item.get("tags")
                tag_list = [str(tag).strip() for tag in tags if str(tag).strip()][:6] if isinstance(tags, list) else []
                add_self_episode(category=category, content=content[:200], tags=tag_list, importance=3)

        interests = payload.get("interests")
        if isinstance(interests, list):
            for interest in interests[:3]:
                text = str(interest or "").strip()
                if len(text) < 2:
                    continue
                add_self_episode(category="interest", content=text[:80], tags=[text[:30]], importance=2)
