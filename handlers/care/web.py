"""主動關懷 WebUI REST API（R20）。

所有端點都受 ``web_ui/server.py`` 的全域管理員 Session 攔截器保護；本模組
只負責資料轉接與輸入驗證，不自行放寬認證。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.error_report import safe_error_payload
from quart import Blueprint, Response, jsonify, request

from .engine import CareEngine
from .repository import normalize_followup_intervals

logger = logging.getLogger(__name__)

care_bp = Blueprint("care", __name__)

_bot_instance: Any = None
_engine_instance: CareEngine | None = None


def set_bot_instance(bot: Any) -> None:
    """由 Web UI 伺服器啟動時注入 Discord Bot 實例。"""
    global _bot_instance
    _bot_instance = bot


def get_bot_instance() -> Any:
    global _bot_instance
    if _bot_instance is not None:
        return _bot_instance
    try:
        from handlers.care import surfer_server
        if getattr(surfer_server, "_bot_instance", None) is not None:
            return surfer_server._bot_instance
    except Exception:
        pass
    return None


def set_engine(engine: CareEngine) -> None:
    """測試／嵌入用：直接指定引擎。"""
    global _engine_instance
    _engine_instance = engine


def _get_engine() -> CareEngine:
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance
    if _bot_instance is not None:
        cog = getattr(_bot_instance, "get_cog", lambda name: None)("CareCog")
        engine = getattr(cog, "engine", None)
        if engine is not None:
            return engine
        engine = getattr(_bot_instance, "care_engine", None)
        if engine is not None:
            return engine
    from . import get_care_engine

    _engine_instance = get_care_engine()
    return _engine_instance


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@care_bp.route("/api/care/overview", methods=["GET"])
async def care_overview() -> Response | tuple[Response, int] | Any:
    """回饋報表、灰度清單與額度快照。"""
    try:
        engine = _get_engine()
        try:
            days = max(1.0, min(365.0, float(request.args.get("days", 30))))
        except (TypeError, ValueError):
            days = 30.0
        raw_guild = request.args.get("guild_id", "").strip()
        if raw_guild and not raw_guild.isdigit():
            return jsonify({"status": "error", "message": "guild_id 必須是正整數"}), 400
        guild_id = int(raw_guild) if raw_guild else None
        report = await asyncio.to_thread(
            engine.feedback_report, days=days, guild_id=guild_id
        )
        rollouts = await asyncio.to_thread(
            engine.repository.list_guild_rollouts
        )
        report["rollouts"] = [item.to_dict() for item in rollouts]
        return jsonify({"status": "success", "overview": report})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_overview", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/guilds", methods=["GET"])
async def care_guilds() -> Response | tuple[Response, int] | Any:
    try:
        engine = _get_engine()
        rollouts = await asyncio.to_thread(
            engine.repository.list_guild_rollouts
        )
        return jsonify(
            {
                "status": "success",
                "guilds": [item.to_dict() for item in rollouts],
            }
        )
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_guilds", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/guilds", methods=["POST"])
async def care_set_guild() -> Response | tuple[Response, int] | Any:
    try:
        data: dict[str, Any] = await request.get_json() or {}
        raw_guild = str(data.get("guild_id", "")).strip()
        if not raw_guild.isdigit():
            return jsonify({"status": "error", "message": "guild_id 必須是正整數"}), 400
        guild_id = int(raw_guild)
        if guild_id <= 0:
            return jsonify({"status": "error", "message": "guild_id 必須是正整數"}), 400

        enabled = _parse_bool(data.get("enabled", True), default=True)
        try:
            percent = int(data.get("rollout_percent", 100))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "rollout_percent 必須是整數"}), 400
        if not 0 <= percent <= 100:
            return jsonify({"status": "error", "message": "rollout_percent 需介於 0–100"}), 400

        engine = _get_engine()
        record = await asyncio.to_thread(
            engine.repository.set_guild_rollout,
            guild_id,
            enabled=enabled,
            rollout_percent=percent,
        )
        return jsonify(
            {
                "status": "success",
                "guild": record.to_dict(),
                "message": "灰度設定已更新",
            }
        )
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_set_guild", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/status", methods=["GET"])
async def care_status() -> Response | tuple[Response, int] | Any:
    try:
        raw_user = request.args.get("user_id", "").strip()
        if not raw_user.isdigit():
            return jsonify({"status": "error", "message": "缺少有效 user_id"}), 400
        raw_guild = request.args.get("guild_id", "").strip()
        if raw_guild and not raw_guild.isdigit():
            return jsonify({"status": "error", "message": "guild_id 必須是正整數"}), 400
        guild_id = int(raw_guild) if raw_guild else 0
        engine = _get_engine()
        status = await asyncio.to_thread(
            engine.preference_status, int(raw_user), guild_id
        )
        return jsonify({"status": "success", "preference": status})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_status", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/actions", methods=["GET"])
async def care_actions() -> Response | tuple[Response, int] | Any:
    try:
        engine = _get_engine()
        raw_user = request.args.get("user_id", "").strip()
        if raw_user and not raw_user.isdigit():
            return jsonify({"status": "error", "message": "user_id 必須是正整數"}), 400
        user_id = int(raw_user) if raw_user else None
        statuses = tuple(
            item.strip()
            for item in request.args.get("status", "").split(",")
            if item.strip()
        )
        try:
            limit = max(1, min(500, int(request.args.get("limit", 100))))
        except (TypeError, ValueError):
            limit = 100
        actions = await asyncio.to_thread(
            engine.repository.list_actions,
            user_id,
            limit=limit,
            statuses=statuses or None,
        )
        return jsonify({"status": "success", "actions": actions})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_actions", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/actions/<action_id>/response", methods=["POST"])
async def care_record_response(action_id: str) -> Response | tuple[Response, int] | Any:
    try:
        data: dict[str, Any] = await request.get_json() or {}
        responded = _parse_bool(data.get("responded", False), default=False)
        engine = _get_engine()
        updated = await asyncio.to_thread(
            engine.record_response, action_id, responded=responded
        )
        if not updated:
            return jsonify({"status": "error", "message": "找不到該關懷行動"}), 404
        return jsonify(
            {
                "status": "success",
                "action_id": action_id,
                "state": "responded" if responded else "silent",
            }
        )
    except Exception as exc:
        payload = safe_error_payload(
            "handlers.care.web.care_record_response", exc
        )
        return jsonify(payload), 500


@care_bp.route("/api/care/settle", methods=["POST"])
async def care_settle() -> Response | tuple[Response, int] | Any:
    try:
        data: dict[str, Any] = await request.get_json() or {}
        raw_hours = data.get("hours")
        try:
            hours = float(raw_hours) if raw_hours is not None else None
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "hours 必須是數字"}), 400
        engine = _get_engine()
        settled = await asyncio.to_thread(engine.settle_silence, hours=hours)
        return jsonify({"status": "success", "settled": settled})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_settle", exc)
        return jsonify(payload), 500


# ---------------------------------------------------------------------------
# 網活仔衝浪設定與手動觸發 API
# ---------------------------------------------------------------------------

@care_bp.route("/api/care/surfer/config", methods=["GET"])
async def care_surfer_config_get() -> Response | tuple[Response, int] | Any:
    """取得網活仔衝浪設定、實況台 Port 與 Discord 測試頻道狀態資訊。"""
    try:
        engine = _get_engine()
        config = await asyncio.to_thread(engine.repository.get_surfer_config)
        import settings
        surfer_port = int(getattr(settings, "CARE_SURFER_PORT", 8765))
        env_cid = str(getattr(settings, "CARE_SURFER_CHANNEL_ID", 0))

        bot = get_bot_instance()
        sandbox_cid = str(config.sandbox_channel_id or "0").strip()

        channel_info = {
            "id": sandbox_cid,
            "name": "",
            "guild_id": "",
            "guild_name": "",
            "can_send": False,
            "status": "unset" if sandbox_cid in {"0", ""} else "not_found",
            "is_env_default": bool(sandbox_cid == env_cid and env_cid not in {"0", ""}),
        }

        available_channels: list[dict[str, Any]] = []

        if bot is not None:
            guilds = getattr(bot, "guilds", []) or []
            for g in guilds:
                g_channels = []
                for ch in getattr(g, "text_channels", []) or []:
                    try:
                        perms = ch.permissions_for(g.me) if hasattr(g, "me") and g.me else None
                        can_send = bool(perms.send_messages and perms.view_channel) if perms else True
                    except Exception:
                        can_send = True
                    g_channels.append({
                        "id": str(ch.id),
                        "name": str(getattr(ch, "name", "channel")),
                        "can_send": can_send,
                    })
                if g_channels:
                    available_channels.append({
                        "guild_id": str(g.id),
                        "guild_name": str(getattr(g, "name", "伺服器")),
                        "channels": g_channels,
                    })

            if sandbox_cid not in {"0", ""} and sandbox_cid.isdigit():
                target_ch = bot.get_channel(int(sandbox_cid))
                if target_ch:
                    g = getattr(target_ch, "guild", None)
                    perms = target_ch.permissions_for(g.me) if g and hasattr(g, "me") and g.me else None
                    can_send = bool(perms.send_messages and perms.view_channel) if perms else True
                    channel_info = {
                        "id": sandbox_cid,
                        "name": str(getattr(target_ch, "name", "")),
                        "guild_id": str(g.id) if g else "",
                        "guild_name": str(getattr(g, "name", "")) if g else "",
                        "can_send": can_send,
                        "status": "valid" if can_send else "missing_permissions",
                        "is_env_default": bool(sandbox_cid == env_cid and env_cid not in {"0", ""}),
                    }

        from .surfer import _gather_sherry_live_stats
        live_stats = _gather_sherry_live_stats(bot, sandbox_cid)

        return jsonify({
            "status": "success",
            "config": config.to_dict(),
            "port": surfer_port,
            "env_channel_id": env_cid,
            "channel_info": channel_info,
            "available_channels": available_channels,
            "live_stats": live_stats,
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_surfer_config_get", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/surfer/config", methods=["POST"])
async def care_surfer_config_post() -> Response | tuple[Response, int] | Any:
    """更新網活仔衝浪與賽博群友設定。"""
    try:
        data: dict[str, Any] = await request.get_json() or {}
        engine = _get_engine()

        sandbox_cid = data.get("sandbox_channel_id")
        enabled = data.get("enabled")
        if enabled is not None:
            enabled = _parse_bool(enabled, default=False)
        interval = data.get("interval_minutes")
        if interval is not None:
            try:
                interval = max(5, int(interval))
            except (TypeError, ValueError):
                interval = 120

        tag_mode = data.get("tag_mode")
        rss_feeds = data.get("rss_feeds")
        keywords = data.get("search_keywords")

        # 賽博群友擴充欄位
        behavior_mode = data.get("behavior_mode")
        chat_reply = data.get("chat_reply_enabled")
        if chat_reply is not None:
            chat_reply = _parse_bool(chat_reply, default=True)
        interject_th = data.get("interject_threshold")
        if interject_th is not None:
            try:
                interject_th = max(1, int(interject_th))
            except (TypeError, ValueError):
                interject_th = 3
        interject_score = data.get("interject_score_threshold")
        if interject_score is not None:
            try:
                interject_score = max(1, min(10, int(interject_score)))
            except (TypeError, ValueError):
                interject_score = 6
        cooldown = data.get("interject_cooldown_seconds")
        if cooldown is not None:
            try:
                cooldown = max(1, int(cooldown))
            except (TypeError, ValueError):
                cooldown = 20
        active_window = data.get("active_chat_window_seconds")
        if active_window is not None:
            try:
                active_window = max(0, int(active_window))
            except (TypeError, ValueError):
                active_window = 300
        multi_msg = data.get("multi_message_enabled")
        if multi_msg is not None:
            multi_msg = _parse_bool(multi_msg, default=True)
        emoji_reaction = data.get("emoji_reaction_enabled")
        if emoji_reaction is not None:
            emoji_reaction = _parse_bool(emoji_reaction, default=True)
        include_url = data.get("include_url_mode")

        active_start = data.get("active_hour_start")
        if active_start is not None:
            try:
                active_start = max(0, min(23, int(active_start)))
            except (TypeError, ValueError):
                active_start = 9

        active_end = data.get("active_hour_end")
        if active_end is not None:
            try:
                active_end = max(0, min(23, int(active_end)))
            except (TypeError, ValueError):
                active_end = 23

        mind_pace = data.get("mind_pace")
        if mind_pace is not None:
            mind_pace = str(mind_pace).strip().lower()
            if mind_pace not in ("fast", "normal", "chill"):
                mind_pace = "normal"

        # 發言額度（0 = 不限）
        max_hourly = data.get("max_replies_per_hour")
        if max_hourly is not None:
            try:
                max_hourly = max(0, int(max_hourly))
            except (TypeError, ValueError):
                max_hourly = 8
        max_daily = data.get("max_replies_per_day")
        if max_daily is not None:
            try:
                max_daily = max(0, int(max_daily))
            except (TypeError, ValueError):
                max_daily = 60
        proactive_cd = data.get("proactive_cooldown_seconds")
        if proactive_cd is not None:
            try:
                proactive_cd = max(0, int(proactive_cd))
            except (TypeError, ValueError):
                proactive_cd = 120

        followup_enabled = data.get("followup_scan_enabled")
        if followup_enabled is not None:
            followup_enabled = _parse_bool(followup_enabled, default=True)

        followup_intervals = data.get("followup_scan_intervals")
        if followup_intervals is not None:
            if isinstance(followup_intervals, str):
                followup_intervals = [
                    x.strip()
                    for x in followup_intervals.replace("，", ",").split(",")
                    if x.strip().isdigit()
                ][:32]
            elif not isinstance(followup_intervals, list):
                followup_intervals = None
            if followup_intervals is not None:
                followup_intervals = normalize_followup_intervals(followup_intervals)

        updated = await asyncio.to_thread(
            engine.repository.update_surfer_config,
            sandbox_channel_id=sandbox_cid,
            enabled=enabled,
            interval_minutes=interval,
            tag_mode=tag_mode,
            rss_feeds=rss_feeds,
            search_keywords=keywords,
            behavior_mode=behavior_mode,
            chat_reply_enabled=chat_reply,
            interject_threshold=interject_th,
            interject_score_threshold=interject_score,
            interject_cooldown_seconds=cooldown,
            active_chat_window_seconds=active_window,
            multi_message_enabled=multi_msg,
            emoji_reaction_enabled=emoji_reaction,
            include_url_mode=include_url,
            active_hour_start=active_start,
            active_hour_end=active_end,
            mind_pace=mind_pace,
            max_replies_per_hour=max_hourly,
            max_replies_per_day=max_daily,
            proactive_cooldown_seconds=proactive_cd,
            followup_scan_enabled=followup_enabled,
            followup_scan_intervals=followup_intervals,
        )

        return jsonify({
            "status": "success",
            "config": updated.to_dict(),
            "message": "衝浪與賽博群友設定已成功更新",
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_surfer_config_post", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/surfer/mind-tick", methods=["POST"])
async def care_surfer_mind_tick() -> Response | tuple[Response, int] | Any:
    """手動推進心智脈動與需求累積（支援 force_boost 或 force_decay 參數，預設 +25%）。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        raw_val = data.get("force_boost")
        if raw_val is None:
            raw_val = data.get("force_decay", 25.0)
        try:
            force_boost = float(raw_val)
        except (TypeError, ValueError):
            force_boost = 25.0

        try:
            engine = _get_engine()
        except Exception:
            engine = None
        from .surfer import get_care_surfer
        surfer = getattr(engine, "surfer", None) or get_care_surfer()
        bot = get_bot_instance()
        result = await surfer.tick_mind_pulse(bot, force_boost=force_boost, force=True)
        mind_st = result.get("mind_state") if isinstance(result, dict) else {}
        return jsonify({
            "status": "success",
            "boost": force_boost,
            "mind_state": mind_st,
            "result": result,
            "message": f"已推進心智脈動 (無聊度累積 +{force_boost}%)",
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_surfer_mind_tick", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/surfer/trigger", methods=["POST"])
async def care_surfer_trigger() -> Response | tuple[Response, int] | Any:
    """在 Port 8763 手動觸發雪莉自主冒泡／衝浪（支援多模式與 dry_run 預覽）。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        dry_run = _parse_bool(data.get("dry_run", True), default=True)
        mode = str(data.get("mode", "auto")).strip() or "auto"

        from .surfer import get_care_surfer
        surfer = get_care_surfer()
        if surfer.is_busy:
            return jsonify({
                "status": "busy",
                "message": "雪莉目前正在冒泡衝浪中，請稍候再試！",
            }), 409

        bot = get_bot_instance()
        started_at = time.time()
        try:
            result = await asyncio.wait_for(
                surfer.surf_once(bot, dry_run=dry_run, mode=mode, force=True),
                timeout=25.0,
            )
            if not dry_run and isinstance(result, dict) and result.get("posted_message_id"):
                try:
                    from .repository import get_care_repository

                    cid = str(get_care_repository().get_surfer_config().sandbox_channel_id or "")
                    cog = bot.get_cog("CareCog") if bot is not None else None
                    if cid and cog is not None:
                        scanner = getattr(cog, "followup_scanner", None)
                        if scanner is not None:
                            await scanner.on_bot_spoke(
                                cid,
                                bot,
                                speak_timestamp=started_at - 60,
                            )
                        cyber = getattr(cog, "cyber", None)
                        if cyber is not None:
                            await cyber.note_external_bot_speak(
                                cid,
                                sent_at=time.time(),
                                message_id=str(result.get("posted_message_id") or ""),
                            )
                except Exception as exc:
                    logger.debug("手動觸發後啟動追話巡檢失敗: %s", exc)
            return jsonify({
                "status": "success",
                "mode": mode,
                "dry_run": dry_run,
                "result": result,
                "message": f"已以【{mode}】模式完成冒泡（dry_run={dry_run}）：{result.get('message', '')}",
            })
        except asyncio.TimeoutError:
            return jsonify({
                "status": "success",
                "mode": mode,
                "dry_run": dry_run,
                "result": None,
                "message": f"雪莉正在背景以【{mode}】模式深度思考中，實況畫面推播至 Port 8765！",
            })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_surfer_trigger", exc)
        return jsonify(payload), 500


# ---------------------------------------------------------------------------
# 雪莉自我專屬記憶 (Self-Memory) 管理 API
# ---------------------------------------------------------------------------

@care_bp.route("/api/care/self-memory", methods=["GET"])
async def care_self_memory_get() -> Response | tuple[Response, int] | Any:
    """查詢雪莉自我經歷 (episodes) 或衝浪閱讀紀錄 (reading)。"""
    try:
        mem_type = request.args.get("type", "episodes").strip().lower()
        try:
            limit = max(1, min(100, int(request.args.get("limit", 20))))
        except (TypeError, ValueError):
            limit = 20
        try:
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0

        from handlers.memory import self_memory

        if mem_type == "reading":
            items = await asyncio.to_thread(
                self_memory.list_reading_logs,
                limit=limit,
                offset=offset,
            )
        else:
            cat = request.args.get("category", "").strip().lower() or None
            items = await asyncio.to_thread(
                self_memory.list_self_episodes,
                category=cat,
                limit=limit,
                offset=offset,
            )

        return jsonify({
            "status": "success",
            "type": mem_type,
            "items": items,
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_self_memory_get", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/self-memory", methods=["POST"])
async def care_self_memory_post() -> Response | tuple[Response, int] | Any:
    """新增一筆雪莉自我經歷／日記或閱讀觀點。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        mem_type = str(data.get("type", "episode")).strip().lower()

        from handlers.memory import self_memory

        if mem_type == "reading":
            url = str(data.get("url", "")).strip()
            title = str(data.get("title", "")).strip()
            if not url or not title:
                return jsonify({"status": "error", "message": "url 與 title 均為必填"}), 400

            rid = await asyncio.to_thread(
                self_memory.record_reading_item,
                url=url,
                title=title,
                source=str(data.get("source", "manual")),
                summary=str(data.get("summary", "")),
                sherry_opinion=str(data.get("sherry_opinion", "")),
                post_text=str(data.get("post_text", "")),
            )
            if rid is None:
                return jsonify({"status": "error", "message": "該文章或網址已存在於閱讀紀錄中（防重複去重）"}), 409
            return jsonify({"status": "success", "id": rid, "message": "閱讀紀錄已建立"})
        else:
            category = str(data.get("category", "diary")).strip().lower()
            content = str(data.get("content", "")).strip()
            if not content:
                return jsonify({"status": "error", "message": "content 不得為空"}), 400

            eid = await asyncio.to_thread(
                self_memory.add_self_episode,
                category=category,
                content=content,
                tags=data.get("tags") if isinstance(data.get("tags"), list) else [],
                importance=int(data.get("importance", 3)),
            )
            return jsonify({"status": "success", "id": eid, "message": "自我經歷已建立"})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_self_memory_post", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/self-memory", methods=["DELETE"])
async def care_self_memory_delete() -> Response | tuple[Response, int] | Any:
    """刪除自我經歷或衝浪閱讀紀錄。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        item_id = str(data.get("id") or request.args.get("id", "")).strip()
        mem_type = str(data.get("type") or request.args.get("type", "episode")).strip().lower()

        if not item_id:
            return jsonify({"status": "error", "message": "缺少 id 參數"}), 400

        from handlers.memory import self_memory

        if mem_type == "reading":
            ok = await asyncio.to_thread(self_memory.delete_reading_log, item_id)
        else:
            ok = await asyncio.to_thread(self_memory.delete_self_episode, item_id, hard_delete=True)

        if not ok:
            return jsonify({"status": "error", "message": "找不到該記憶條目或刪除失敗"}), 404

        return jsonify({"status": "success", "id": item_id, "message": "記憶已刪除"})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_self_memory_delete", exc)
        return jsonify(payload), 500


# ======================================================================
# 賽博群友 Social Core API（決策帳本／沙盒群像）
# ======================================================================
def _get_social_director() -> Any:
    """取得 SocialDirector：優先沿用 CareCog 的實例，否則用 repository 現建。"""
    bot = get_bot_instance()
    if bot is not None:
        try:
            cog = bot.get_cog("CareCog")
            if cog is not None and hasattr(cog, "social_director"):
                return cog.social_director
        except Exception:
            pass
    from handlers.social.autonomy import SocialDirector

    return SocialDirector(_get_engine().repository)


def _social_roster() -> Any:
    from handlers.social.autonomy.personas import PersonaRoster

    return PersonaRoster(_get_engine().repository)


@care_bp.route("/api/care/social/overview", methods=["GET"])
async def care_social_overview() -> Response | tuple[Response, int] | Any:
    """賽博群友總覽：模式清單、persona roster、最近決策與統計。"""
    try:
        import settings

        engine = _get_engine()
        repository = engine.repository
        hours = float(request.args.get("hours", 24))
        limit = int(request.args.get("limit", 50))

        modes = await asyncio.to_thread(repository.list_channel_modes)
        personas = await asyncio.to_thread(repository.list_personas, None, include_disabled=True)
        decisions = await asyncio.to_thread(repository.list_decisions, limit=limit)
        stats = await asyncio.to_thread(repository.decision_stats, channel_id=None, hours=hours)

        return jsonify({
            "status": "success",
            "sandbox_enabled": bool(getattr(settings, "SOCIAL_SANDBOX_ENABLED", False)),
            "sandbox_compat_mode": bool(getattr(settings, "SOCIAL_SANDBOX_COMPAT_MODE", False)),
            "limits": {
                "max_npcs": int(getattr(settings, "SOCIAL_SANDBOX_MAX_NPCS", 3)),
                "max_consecutive": int(getattr(settings, "SOCIAL_NPC_MAX_CONSECUTIVE", 3)),
                "max_per_round": int(getattr(settings, "SOCIAL_NPC_MAX_PER_ROUND", 2)),
                "scene_max_turns": int(getattr(settings, "SOCIAL_SCENE_MAX_TURNS", 12)),
                "hourly_limit": int(getattr(settings, "SOCIAL_NPC_HOURLY_LIMIT", 20)),
                "daily_limit": int(getattr(settings, "SOCIAL_NPC_DAILY_LIMIT", 120)),
            },
            "modes": [m.to_dict() for m in modes],
            "personas": [p.to_dict() for p in personas],
            "decisions": decisions,
            "stats": stats,
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_overview", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/decisions", methods=["GET"])
async def care_social_decisions() -> Response | tuple[Response, int] | Any:
    """決策帳本查詢：白名單篩選 channel/action/trigger，並附上統計。"""
    try:
        engine = _get_engine()
        repository = engine.repository
        channel_id = str(request.args.get("channel_id", "") or "").strip() or None
        action = str(request.args.get("action", "") or "").strip().upper() or None
        trigger_type = str(request.args.get("trigger_type", "") or "").strip().lower() or None
        limit = int(request.args.get("limit", 100))
        hours = float(request.args.get("hours", 24))
        if action and action not in {"SPEAK", "REACT", "SILENT"}:
            return jsonify({"status": "error", "message": "action 只接受 SPEAK / REACT / SILENT"}), 400

        since = None
        if hours > 0:
            import time

            since = time.time() - hours * 3600

        decisions = await asyncio.to_thread(
            repository.list_decisions,
            channel_id=channel_id,
            action=action,
            trigger_type=trigger_type,
            limit=limit,
            since=since,
        )
        stats = await asyncio.to_thread(repository.decision_stats, channel_id=channel_id, hours=hours)
        return jsonify({
            "status": "success",
            "channel_id": channel_id or "",
            "hours": hours,
            "stats": stats,
            "decisions": decisions,
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_decisions", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/modes", methods=["GET"])
async def care_social_modes_get() -> Response | tuple[Response, int] | Any:
    """列出所有頻道模式設定（無資料列者視為 SOLO）。"""
    try:
        engine = _get_engine()
        modes = await asyncio.to_thread(engine.repository.list_channel_modes)
        return jsonify({"status": "success", "modes": [m.to_dict() for m in modes]})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_modes_get", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/modes", methods=["POST"])
async def care_social_modes_post() -> Response | tuple[Response, int] | Any:
    """設定頻道模式。SANDBOX_GROUP 需要 enabled + allow_webhooks 同時為真才會生效。"""
    try:
        import settings

        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        channel_id = str(data.get("channel_id", "") or "").strip()
        if not channel_id.isdigit():
            return jsonify({"status": "error", "message": "channel_id 必須是數字頻道 ID"}), 400

        mode = str(data.get("mode", "SOLO")).strip().upper()
        if mode not in {"SOLO", "SANDBOX_GROUP"}:
            return jsonify({"status": "error", "message": "mode 只接受 SOLO / SANDBOX_GROUP"}), 400

        enabled = _parse_bool(data.get("enabled"), default=False)
        allow_webhooks = _parse_bool(data.get("allow_webhooks"), default=False)
        if mode == "SANDBOX_GROUP" and enabled and not bool(getattr(settings, "SOCIAL_SANDBOX_ENABLED", False)):
            return jsonify({
                "status": "error",
                "message": "沙盒總開關未開啟（SOCIAL_SANDBOX_ENABLED=0），請先由環境變數開啟。",
            }), 409

        engine = _get_engine()
        record = await asyncio.to_thread(
            engine.repository.upsert_channel_mode,
            channel_id,
            mode=mode,
            enabled=enabled,
            allow_webhooks=allow_webhooks,
            guild_id=str(data.get("guild_id", "") or ""),
            note=str(data.get("note", "") or ""),
        )
        return jsonify({"status": "success", "mode": record.to_dict()})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_modes_post", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/personas", methods=["GET"])
async def care_social_personas_get() -> Response | tuple[Response, int] | Any:
    """列出 persona roster；可帶 channel_id 過濾。"""
    try:
        engine = _get_engine()
        channel_id = str(request.args.get("channel_id", "") or "").strip() or None
        records = await asyncio.to_thread(
            engine.repository.list_personas, channel_id, include_disabled=True
        )
        roster = _social_roster()
        souls_dir = roster.sandbox_soul_dir
        souls: list[str] = []
        try:
            if souls_dir.is_dir():
                souls = sorted(
                    p.name
                    for p in souls_dir.iterdir()
                    if p.suffix.lower() in {".md", ".txt"} and not p.name.lower().startswith("readme")
                )
        except Exception:
            souls = []
        return jsonify({
            "status": "success",
            "personas": [p.to_dict() for p in records],
            "available_souls": souls,
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_personas_get", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/personas", methods=["POST"])
async def care_social_personas_post() -> Response | tuple[Response, int] | Any:
    """新增／更新沙盒 NPC persona（soul 必須位於 SOUL_DIR/sandbox）。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        channel_id = str(data.get("channel_id", "") or "").strip()
        if not channel_id.isdigit():
            return jsonify({"status": "error", "message": "channel_id 必須是數字頻道 ID"}), 400

        from handlers.social.autonomy.personas import PersonaValidationError

        roster = _social_roster()
        try:
            persona = await asyncio.to_thread(
                roster.upsert_npc,
                persona_key=str(data.get("persona_key", "") or ""),
                channel_id=channel_id,
                soul_name=str(data.get("soul_name", "") or ""),
                display_name=str(data.get("display_name", "") or ""),
                guild_id=str(data.get("guild_id", "") or ""),
                avatar_path=str(data.get("avatar_path", "") or ""),
                enabled=_parse_bool(data.get("enabled"), default=True),
                speech_style=str(data.get("speech_style", "") or ""),
            )
        except PersonaValidationError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        return jsonify({"status": "success", "persona": persona.to_dict()})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_personas_post", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/personas/<persona_key>", methods=["DELETE"])
async def care_social_personas_delete(persona_key: str) -> Response | tuple[Response, int] | Any:
    """停用 NPC persona（不刪 webhook，保留歷史訊息 attribution）。"""
    try:
        from handlers.social.autonomy.personas import PersonaValidationError

        roster = _social_roster()
        try:
            ok = await asyncio.to_thread(roster.disable, persona_key)
        except PersonaValidationError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        if not ok:
            return jsonify({"status": "error", "message": "找不到該 persona 或不可停用"}), 404
        return jsonify({"status": "success", "persona_key": persona_key, "enabled": False})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_personas_delete", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/scene", methods=["GET"])
async def care_social_scene() -> Response | tuple[Response, int] | Any:
    """Scene inspector：目前 open scene、最近 turns、房間與心智狀態。"""
    try:
        engine = _get_engine()
        repository = engine.repository
        channel_id = str(request.args.get("channel_id", "") or "").strip()
        if not channel_id.isdigit():
            return jsonify({"status": "error", "message": "channel_id 必須是數字頻道 ID"}), 400

        config = await asyncio.to_thread(repository.get_surfer_config)
        director = _get_social_director()
        mode = director.mode_for(channel_id, config=config)
        scene = await asyncio.to_thread(repository.get_open_scene, channel_id)
        turns = []
        if scene is not None:
            turns = await asyncio.to_thread(repository.list_scene_turns, scene.scene_id, limit=50)
        room = await asyncio.to_thread(repository.get_room, channel_id)
        mind = await asyncio.to_thread(repository.get_social_mind, channel_id)
        return jsonify({
            "status": "success",
            "mode": mode.to_dict(),
            "scene": scene.to_dict() if scene is not None else None,
            "turns": [t.to_dict() for t in turns],
            "room": room.to_dict(),
            "mind": mind.to_dict(),
        })
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_scene", exc)
        return jsonify(payload), 500


@care_bp.route("/api/care/social/kill", methods=["POST"])
async def care_social_kill() -> Response | tuple[Response, int] | Any:
    """L2 kill switch：立刻降回 SOLO、關閉 open scene、停用所有 NPC 並取消待送 turn。"""
    try:
        data: dict[str, Any] = (await request.get_json(silent=True)) or {}
        channel_id = str(data.get("channel_id", "") or "").strip()
        if not channel_id.isdigit():
            return jsonify({"status": "error", "message": "channel_id 必須是數字頻道 ID"}), 400

        bot = get_bot_instance()
        cog = bot.get_cog("CareCog") if bot is not None else None
        if cog is not None and hasattr(cog, "sandbox_runner"):
            result = await asyncio.to_thread(cog.sandbox_runner.kill, channel_id)
        else:
            engine = _get_engine()
            result = await asyncio.to_thread(engine.repository.kill_channel_sandbox, channel_id)
        return jsonify({"status": "success", "channel_id": channel_id, "killed": result})
    except Exception as exc:
        payload = safe_error_payload("handlers.care.web.care_social_kill", exc)
        return jsonify(payload), 500

@care_bp.route("/api/care/cyber/status", methods=["GET"])
async def care_cyber_status() -> Response | tuple[Response, int] | Any:
    """Cyber v2 引擎狀態（唯讀）。"""
    try:
        from .cyber import get_cyber_engine

        engine = get_cyber_engine()
        if engine is not None:
            payload = engine.status_payload()
        else:
            import settings as _settings
            from .cyber.ui_config import get_cyber_ui_config

            ui = get_cyber_ui_config()
            model = str(getattr(_settings, "PRIMARY_MODEL", "") or "").strip()
            payload = {
                "enabled": ui.engine_mode != "off",
                "version": "v2",
                "mode": ui.engine_mode,
                "gateway_enabled": ui.gateway_enabled,
                "owns_mentions": ui.owns_mentions,
                "mention_scope": ui.mention_scope,
                "autonomous_enabled": ui.autonomous_enabled,
                "tool_policy": ",".join(ui.allowed_tools or []),
                "phase": "idle",
                "window_open": False,
                "pending_events": 0,
                "primary_model": {
                    "value": model,
                    "source": "webui",
                    "healthy": bool(model),
                    "error": "" if model else "PRIMARY_MODEL 為空",
                },
            }
        return jsonify({
            "status": "success",
            "cyber_engine": payload,
            "primary_model": payload.get("primary_model", {}),
        })
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_status", exc)), 500


@care_bp.route("/api/care/cyber/trigger", methods=["POST"])
async def care_cyber_trigger() -> Response | tuple[Response, int] | Any:
    """手動觸發 Cyber 回合；預設只回決策，fire 需 confirm=yes。"""
    try:
        from .cyber import get_cyber_engine

        data: dict[str, Any] = await request.get_json(silent=True) or {}
        engine = get_cyber_engine()
        if engine is None:
            return jsonify({"status": "error", "message": "engine_not_loaded"}), 503
        preview = await engine.trigger_preview()
        fired = False
        if bool(data.get("fire")) and data.get("confirm") == "yes":
            fired = await engine.submit_proactive(reason="webui_manual", motivation="手動觸發")
        return jsonify({"status": "success", "fired": fired, **preview})
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_trigger", exc)), 500


@care_bp.route("/api/care/cyber/events", methods=["GET"])
async def care_cyber_events() -> Response | tuple[Response, int] | Any:
    """最近 Cyber 回合事件（不含訊息內容）。"""
    try:
        from .cyber import get_cyber_engine

        engine = get_cyber_engine()
        limit = request.args.get("limit", default=20, type=int)
        events = engine.recent_events(limit) if engine is not None else []
        return jsonify({"status": "success", "events": events})
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_events", exc)), 500


@care_bp.route("/api/care/cyber/autonomy", methods=["GET"])
async def care_cyber_autonomy() -> Response | tuple[Response, int] | Any:
    """最近自主決定（含動機與內心戲，供 WebUI 顯示）。"""
    try:
        from .cyber import get_cyber_engine

        engine = get_cyber_engine()
        limit = request.args.get("limit", default=20, type=int)
        items = engine.recent_autonomy(limit) if engine is not None else []
        return jsonify({"status": "success", "items": items})
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_autonomy", exc)), 500


@care_bp.route("/api/care/cyber/reset", methods=["POST"])
async def care_cyber_reset() -> Response | tuple[Response, int] | Any:
    """重置 Cyber runtime（維運用）。"""
    try:
        from .cyber import get_cyber_engine

        engine = get_cyber_engine()
        if engine is None:
            return jsonify({"status": "error", "message": "engine_not_loaded"}), 503
        await engine.reset()
        return jsonify({"status": "success"})
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_reset", exc)), 500

@care_bp.route("/api/care/cyber/config", methods=["GET"])
async def care_cyber_config_get() -> Response | tuple[Response, int] | Any:
    """取得 One Brain, Two Lanes v2 設定。"""
    try:
        from .cyber.ui_config import get_cyber_ui_config

        cfg = get_cyber_ui_config()
        return jsonify({"status": "success", "config": cfg.to_dict()})
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_config_get", exc)), 500


@care_bp.route("/api/care/cyber/config", methods=["POST"])
async def care_cyber_config_post() -> Response | tuple[Response, int] | Any:
    """更新 v2 設定；engine_mode 會即時套用。"""
    try:
        from .cyber import get_cyber_engine
        from .cyber.ui_config import CyberUIConfig, save_cyber_ui_config

        data: dict[str, Any] = await request.get_json(silent=True) or {}
        cfg = CyberUIConfig.from_dict(data.get("config", data))
        cfg = save_cyber_ui_config(cfg)
        engine = get_cyber_engine()
        applied = None
        if engine is not None:
            applied = await engine.set_mode(cfg.engine_mode)
        return jsonify({
            "status": "success",
            "config": cfg.to_dict(),
            "applied_mode": applied,
        })
    except Exception as exc:
        return jsonify(safe_error_payload("handlers.care.web.care_cyber_config_post", exc)), 500
