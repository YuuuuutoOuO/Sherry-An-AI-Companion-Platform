"""handlers/care/followup_scanner.py
===================================
賽博群友自主回覆後的漸進退避掃描追蹤器 (Follow-up Backoff Scanner)。

當雪莉在測試頻道自主發言或回覆後，依序以 [1, 3, 5, 7, 10] 分鐘的階梯間隔回頭巡視頻道：
- 若檢測到人類新訊息回應：即時理解並生成回覆接話，發言後重啟新一輪階梯追蹤。
- 若無人回應：按階梯遞增退避等待（1m -> 3m -> 5m -> 7m -> 10m），直到雪莉進行下一次自主發言或達到上限。
- 支援與即時 on_message 協同：若群友在等待掃描期間直接送出訊息且已即時處理，自動取消排程避免重覆回覆。

設計重點：
1. 同一則人類訊息只會被評估一次（掃描游標），避免大腦選擇沉默後在每個階梯重複燒 LLM。
2. 所有設定讀取都丢到 worker thread，且每個階梯都會重新確認開關，關閉後立即停止。
3. 接話回覆仍走 SocialDirector 的決策／額度／冷卻／帳本，不另開後門。
4. 群像模式 (SANDBOX_GROUP) 由 SandboxGroupRunner 的 turn 排程負責，掃描器自動讓路。
"""

from __future__ import annotations

import asyncio
from datetime import datetime as dt, timedelta, timezone
import logging
import time
from typing import Any

import discord
import settings

# 共用同一份活躍時段判定與階梯正規化，避免兩套實作語意漂移。
from handlers.care.repository import is_within_active_hours, normalize_followup_intervals

logger = logging.getLogger(__name__)
_TZ_TW = timezone(timedelta(hours=8))

# 走完階梯後仍會以最大間隔低頻巡檢；預設最多等 12 小時。
DEFAULT_MAX_WAIT_SECONDS: float = 12 * 60 * 60


def _seconds_until_active(now: dt, start: int, end: int) -> float:
    """計算距離下一個活躍時段開始還有幾秒（用於休眠時段先睡再繼續巡檢）。"""
    s = int(start) % 24
    e = int(end) % 24
    if s == e:
        return 0.0
    for offset in range(1, 25):
        if is_within_active_hours((now.hour + offset) % 24, s, e):
            target = (now + timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
            return max(60.0, (target - now).total_seconds())
    return 3600.0


def _current_task() -> asyncio.Task[Any] | None:
    """安全取得目前 task；沒有 event loop 時回傳 None。"""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class FollowupScanner:
    """管理頻道自主回覆後漸進式退避巡檢與接話的非同步調度器。"""

    def __init__(self, engine: Any = None) -> None:
        self.engine = engine
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_speak_times: dict[str, float] = {}

    def cancel(self, channel_id: int | str) -> None:
        """取消指定頻道的待執行掃描任務（不會取消呼叫者自己）。"""
        cid = str(channel_id)
        task = self._tasks.pop(cid, None)
        if task and not task.done() and task is not _current_task():
            task.cancel()
            logger.debug("[FollowupScanner] 已取消頻道 %s 的排程巡視任務。", cid)

    def cancel_all(self) -> None:
        """取消所有頻道的巡視任務（Cog 卸載或關閉功能時使用）。"""
        current = _current_task()
        for _cid, task in list(self._tasks.items()):
            if task and not task.done() and task is not current:
                task.cancel()
        self._tasks.clear()
        self._last_speak_times.clear()

    async def on_bot_spoke(
        self,
        channel_id: int | str,
        bot: Any,
        *,
        speak_timestamp: float | None = None,
    ) -> None:
        """當雪莉在指定頻道發言完成時觸發，啟動漸進式退避巡檢排程。"""
        cid = str(channel_id)
        try:
            from handlers.care.cyber import get_cyber_engine
            from handlers.care.cyber.ui_config import get_cyber_ui_config

            cyber = get_cyber_engine()
            if cyber is not None and cyber.mode == "live" and get_cyber_ui_config().gateway_enabled:
                return
        except Exception:
            pass
        repository = getattr(self.engine, "repository", None)
        if not self.engine or repository is None:
            return

        try:
            config = await asyncio.to_thread(repository.get_surfer_config)
        except Exception as exc:
            logger.debug("[FollowupScanner] 讀取設定失敗: %s", exc)
            return

        if not config.enabled or not config.chat_reply_enabled or not config.followup_scan_enabled:
            self.cancel(cid)
            return

        sandbox_cid = str(config.sandbox_channel_id or "0").strip()
        if cid != sandbox_cid or cid in ("0", ""):
            return

        # 群像模式由 SandboxGroupRunner 的 turn 排程負責，避免雙軌重複回覆。
        director = getattr(self.engine, "director", None)
        if director is not None:
            try:
                mode = director.mode_for(cid, config=config)
                if getattr(mode, "is_group", False) is True:
                    self.cancel(cid)
                    logger.debug("[FollowupScanner] 頻道 %s 為群像模式，交給 turn 排程處理。", cid)
                    return
            except Exception as exc:
                logger.debug("[FollowupScanner] 群像模式判定失敗，沿用掃描器: %s", exc)

        now_ts = time.time() if speak_timestamp is None else float(speak_timestamp)
        self._last_speak_times[cid] = now_ts

        # 取消舊的排程，重啟新一輪 ladder
        self.cancel(cid)

        task = asyncio.create_task(
            self._run_ladder(cid, bot, now_ts),
            name=f"followup-scan-{cid}-{int(now_ts)}",
        )
        self._tasks[cid] = task
        intervals = normalize_followup_intervals(config.followup_scan_intervals)
        logger.info(
            "[FollowupScanner] 已為頻道 %s 啟動自主發言後漸進巡檢 (首輪將在 %d 分鐘後掃描，階梯序列: %s)",
            cid,
            intervals[0],
            intervals,
        )

    async def _run_ladder(
        self,
        channel_id: str,
        bot: Any,
        speak_timestamp: float,
        *,
        max_wait_seconds: float | None = None,
    ) -> None:
        """漸進階梯掃描主迴圈。

        走完設定的階梯後，會以最大間隔持續低頻巡檢（預設最多 12 小時），
        像真人一樣「隔一段時間回來看有沒有人回」：
        - 有人回 → 立刻接話，並重新開始一輪階梯。
        - 沒人回 → 安靜等待，直到下一次主動開話題或超過等待上限。
        """
        try:
            config = await asyncio.to_thread(self.engine.repository.get_surfer_config)
            intervals = normalize_followup_intervals(config.followup_scan_intervals)
            max_interval = intervals[-1]

            # 掃描游標：只評估游標之後的人類訊息，避免同一則訊息被每個階梯重複評估。
            scan_cursor = float(speak_timestamp)
            wait_budget = (
                DEFAULT_MAX_WAIT_SECONDS
                if max_wait_seconds is None
                else max(0.0, float(max_wait_seconds))
            )
            waited = 0.0
            step_idx = 0

            while True:
                step_idx += 1
                in_ladder = step_idx <= len(intervals)
                minutes = intervals[step_idx - 1] if in_ladder else max_interval
                wait_seconds = max(5, int(minutes * 60))

                if in_ladder:
                    logger.debug(
                        "[FollowupScanner] 頻道 %s 進入階梯 %d/%d，等待 %d 秒後巡檢...",
                        channel_id,
                        step_idx,
                        len(intervals),
                        wait_seconds,
                    )
                else:
                    logger.debug(
                        "[FollowupScanner] 頻道 %s 進入長時間等待，每 %d 分鐘回來看一次...",
                        channel_id,
                        minutes,
                    )

                await asyncio.sleep(wait_seconds)
                waited += wait_seconds

                # 1. 檢查是否已被新發言取代或取消
                current_last_speak = self._last_speak_times.get(channel_id, 0.0)
                if abs(current_last_speak - speak_timestamp) > 0.5:
                    logger.debug("[FollowupScanner] 頻道 %s 已有更新的發言記錄，終止此輪排程。", channel_id)
                    return

                # 2. 每次醒來都重新確認開關（WebUI 關閉後不可繼續潛伏巡檢）
                config = await asyncio.to_thread(self.engine.repository.get_surfer_config)
                if not config.enabled or not config.chat_reply_enabled or not config.followup_scan_enabled:
                    logger.info("[FollowupScanner] 頻道 %s 功能已關閉，停止巡檢。", channel_id)
                    return

                # 3. 活躍時段檢查（防半夜擾民）：休眠時段先睡到下次活躍，不直接放棄。
                now_dt = dt.now(_TZ_TW)
                if not is_within_active_hours(now_dt.hour, config.active_hour_start, config.active_hour_end):
                    sleep_for = _seconds_until_active(
                        now_dt, config.active_hour_start, config.active_hour_end
                    )
                    logger.debug(
                        "[FollowupScanner] 頻道 %s 目前處於休眠時段，先睡 %d 秒等活躍時段。",
                        channel_id,
                        int(sleep_for),
                    )
                    await asyncio.sleep(sleep_for)
                    waited += sleep_for
                    if waited >= wait_budget:
                        logger.info("[FollowupScanner] 頻道 %s 等待已達上限，結束本輪巡檢。", channel_id)
                        return
                    continue

                # 4. 取得 Discord 頻道實例
                channel = bot.get_channel(int(channel_id))
                if not channel and hasattr(bot, "fetch_channel"):
                    try:
                        channel = await bot.fetch_channel(int(channel_id))
                    except Exception:
                        channel = None

                if not channel or not hasattr(channel, "history"):
                    logger.debug("[FollowupScanner] 無法取得頻道實例 (%s)，結束巡檢。", channel_id)
                    return

                # 5. 掃描游標之後的新訊息
                try:
                    human_messages: list[discord.Message] = []
                    bot_user_id = getattr(bot.user, "id", None) if getattr(bot, "user", None) else None

                    async for m in channel.history(limit=25):
                        # 如果該訊息時間在雪莉最後發言時間之後
                        m_ts = m.created_at.timestamp()
                        if m_ts <= scan_cursor + 0.1:
                            continue
                        if not m.author.bot and (bot_user_id is None or m.author.id != bot_user_id):
                            human_messages.append(m)

                    if human_messages:
                        # 依發送時間由舊至新排序
                        human_messages.sort(key=lambda msg: msg.created_at.timestamp())
                        latest_human_msg = human_messages[-1]

                        # 先推進游標：即使大腦決定沉默，也不會在下一次巡檢重複評估同一則訊息。
                        scan_cursor = latest_human_msg.created_at.timestamp()

                        logger.info(
                            "[FollowupScanner] 頻道 %s 偵測到 %d 則群友回應（最新來自 %s），雪莉開始接話回覆...",
                            channel_id,
                            len(human_messages),
                            latest_human_msg.author.display_name,
                        )

                        # 執行接話生成與發送
                        acted = await self._generate_and_reply(channel, latest_human_msg, config, bot)
                        if acted:
                            # 發言成功後，更新時間戳並啟動新一輪 ladder（由 on_bot_spoke 處理）
                            return
                    else:
                        if in_ladder:
                            logger.info(
                                "[FollowupScanner] 頻道 %s 無新回應，雪莉保持安靜（第 %d/%d 階梯）",
                                channel_id,
                                step_idx,
                                len(intervals),
                            )
                        else:
                            logger.debug(
                                "[FollowupScanner] 頻道 %s 仍無新回應，繼續等待下一次巡檢。",
                                channel_id,
                            )

                except Exception as exc:
                    logger.warning("[FollowupScanner] 頻道 %s 巡檢掃描時發生異常: %s", channel_id, exc)

                # 6. 等待上限：避免任務永遠佔著排程（下一次主動發言會重新啟動新一輪）
                if waited >= wait_budget:
                    logger.info(
                        "[FollowupScanner] 頻道 %s 已持續等待 %d 分鐘仍無新回應，結束本輪巡檢。",
                        channel_id,
                        int(waited // 60),
                    )
                    return
        except asyncio.CancelledError:
            logger.debug("[FollowupScanner] 頻道 %s 的巡檢任務已取消。", channel_id)
        except Exception as exc:
            logger.error("[FollowupScanner] 階梯巡檢迴圈發生未預期錯誤: %s", exc, exc_info=True)
        finally:
            if self._tasks.get(channel_id) is _current_task():
                self._tasks.pop(channel_id, None)

    async def _generate_and_reply(
        self,
        channel: Any,
        target_message: discord.Message,
        config: Any,
        bot: Any,
    ) -> bool:
        """調用大腦為巡檢發現的群友訊息生成接話回覆。"""
        try:
            from handlers.care.surfer import (
                _configured_max_bubbles,
                add_token_consumption,
                clean_cyber_bubble,
                record_proactive_action_memory,
                send_cyber_bubbles,
                split_into_bubbles,
                update_bot_speak_timestamp,
            )
            from handlers.social.autonomy import SocialEvent, TriggerType, TurnScheduler
            from handlers.social.brain import BrainIntent, build_conversation_context, think
            from handlers.social.persona import build_dynamic_persona

            cid = str(channel.id)
            guild_id = str(getattr(getattr(channel, "guild", None), "id", "") or "")
            event = SocialEvent(
                kind="followup_scan",
                channel_id=cid,
                guild_id=guild_id,
                trigger_type=TriggerType.HUMAN_MESSAGE,
                message_id=str(getattr(target_message, "id", "") or ""),
                content=str(getattr(target_message, "content", "") or ""),
                author_id=str(getattr(target_message.author, "id", "")),
                author_name=getattr(target_message.author, "display_name", "") or "群友",
                is_human=True,
                created_at=time.time(),
            )

            # 巡檢接話本質上是回覆人類訊息，與熱聊路徑一致：免插話冷卻，但仍受額度與帳本約束。
            director = getattr(self.engine, "director", None)
            if director is None:
                logger.warning("[FollowupScanner] 缺少 SocialDirector，放棄本次巡檢回覆。")
                return False

            decision = director.decide(
                event,
                config,
                room=await asyncio.to_thread(self.engine.repository.get_room, cid),
                persona_key="sherry",
                ignore_cooldown=True,
                bypass_budget=True,
            )
            if not decision.allowed:
                logger.info(
                    "[FollowupScanner] 頻道 %s 未通過發話閘門（%s），保持安靜。",
                    cid,
                    ", ".join(decision.gate.reasons) or "blocked",
                )
                return False

            context = await build_conversation_context(
                channel,
                focus_message=target_message,
                bot=bot,
                limit=10,
            )
            script = context.render_transcript()
            guild_id_int = getattr(getattr(channel, "guild", None), "id", None)
            head_persona, tail_lore = build_dynamic_persona(
                script,
                "CHAT",
                channel_id=channel.id,
                user_query=target_message.content,
                bot=bot,
                guild_id=guild_id_int,
            )

            async def _ask(extra_block: str, suffix: str) -> Any:
                return await think(
                    bot,
                    intent=BrainIntent.REPLY,
                    context=context,
                    head_persona=head_persona,
                    tail_lore=tail_lore,
                    to_sherry=True,
                    continuation=True,
                    model=getattr(
                        settings,
                        "PRIMARY_MODEL",
                        "freetoken:mistralai/Mixtral-8x7B-Instruct-v0.1",
                    ),
                    max_tokens=900,
                    priority=getattr(settings, "PRIORITY_INTERJECT", 2),
                    task_id=f"followup-reply-{channel.id}-{int(time.time())}-{suffix}",
                    extra_block=extra_block,
                )

            def _to_bubbles(text: str) -> list[str]:
                items = (
                    split_into_bubbles(text, max_bubbles=_configured_max_bubbles())
                    if config.multi_message_enabled
                    else [clean_cyber_bubble(text)]
                )
                return [b for b in items if b.strip()]

            result = await _ask("", "a")

            if not result.should_speak or not result.reply:
                director.record_silent(
                    event,
                    mode=decision.mode.mode,
                    persona_key="sherry",
                    reasons=[f"brain_{getattr(result, 'decision', 'silent')}"],
                    gate=decision.gate,
                )
                logger.info("[FollowupScanner] 頻道 %s 大腦決定本次巡檢不發言。", cid)
                return False

            bubbles = _to_bubbles(result.reply)

            # 硬性防重複：若開頭與上一句太像，要求大腦換個說法重寫一次（只重試一次）。
            previous_self = (context.recent_self_lines(limit=1) or [""])[0]
            if bubbles and previous_self and TurnScheduler.is_too_similar(bubbles[0], previous_self):
                logger.info("[FollowupScanner] 頻道 %s 開頭與上一句過於相似，要求大腦重寫。", cid)
                retry = await _ask(
                    f"【硬性避免重複】妳上一句是「{previous_self}」。這次開頭務必換個說法，不可沿用相同開頭。",
                    "retry",
                )
                if retry.should_speak and retry.reply:
                    retry_bubbles = _to_bubbles(retry.reply)
                    if retry_bubbles:
                        result = retry
                        bubbles = retry_bubbles

            if not bubbles:
                director.record_silent(
                    event,
                    mode=decision.mode.mode,
                    persona_key="sherry",
                    reasons=["empty_bubbles"],
                    gate=decision.gate,
                )
                return False

            sent_msgs = await send_cyber_bubbles(channel, bubbles, reply_to=target_message)
            if not sent_msgs:
                director.record_silent(
                    event,
                    mode=decision.mode.mode,
                    persona_key="sherry",
                    reasons=["send_failed"],
                    gate=decision.gate,
                )
                return False

            now_ts = time.time()
            update_bot_speak_timestamp(channel.id, ts=now_ts, target_author_id=target_message.author.id)
            add_token_consumption(180)

            director.record_speak(
                event,
                mode=decision.mode.mode,
                persona_key="sherry",
                text="\n".join(bubbles),
                gate=decision.gate,
            )
            try:
                director.apply_cooldown(channel.id, config, proactive=False)
            except Exception as exc:
                logger.debug("[FollowupScanner] 更新回覆冷卻失敗: %s", exc)

            # 記錄主動動作記憶
            record_proactive_action_memory(
                summary=f"階梯巡視接話：{bubbles[0][:60]}",
                channel_id=str(channel.id),
                user_id=target_message.author.id,
                user_name=getattr(target_message.author, "display_name", "群友"),
                detail={"bubbles": bubbles, "thought": result.thought},
            )

            # 重新啟動新一輪巡視
            await self.on_bot_spoke(channel.id, bot, speak_timestamp=now_ts)
            return True

        except Exception as exc:
            logger.error("[FollowupScanner] 生成巡檢回覆失敗: %s", exc, exc_info=True)
            return False
