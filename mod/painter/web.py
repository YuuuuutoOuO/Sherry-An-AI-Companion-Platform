"""
mod.painter.web
===============
Quart Blueprint and Web routes for Sherry Digital Painter & Art Gallery Mod.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quart import Blueprint, Response, jsonify, request, send_file

from mod.painter import config
from mod.painter.models import ArtStyle
from mod.painter.agent import PainterAgent, get_live_painting_status
from mod.painter.agent import _SYSTEM_PAINTER_PROMPT

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

painter_bp = Blueprint(
    "painter",
    __name__,
    template_folder=str(_TEMPLATE_DIR),
    static_folder=str(_STATIC_DIR),
)

_bot_instance: Any = None


def set_bot_instance(bot: Any) -> None:
    """Set the Discord Bot instance for live Cog and BotBrain access."""
    global _bot_instance
    _bot_instance = bot


def _get_bot_instance() -> Any:
    """Retrieve Bot instance from local global or host server.py."""
    global _bot_instance
    if _bot_instance is not None:
        return _bot_instance
    try:
        from web_ui.server import _bot_instance as server_bot
        if server_bot is not None:
            _bot_instance = server_bot
            return server_bot
    except Exception:
        pass
    return None


@painter_bp.route("/view", methods=["GET"])
async def get_view() -> Response | tuple[Response, int] | Any:
    """Return the HTML snippet for Painter view."""
    html_file = _TEMPLATE_DIR / "painter.html"
    if html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        return Response(content, mimetype="text/html")
    return Response("<div class='view hidden' id='view-painter'><h3>畫畫工作室視圖遺失</h3></div>", mimetype="text/html")


@painter_bp.route("/api/painter/paintings", methods=["GET"])
@painter_bp.route("/api/paintings", methods=["GET"])
@painter_bp.route("/paintings", methods=["GET"])
async def list_paintings() -> Response | tuple[Response, int] | Any:
    """List all saved paintings in ./data/painting/*.jpg with metadata."""
    painting_dir = config.PAINTING_DIR
    if not painting_dir.exists():
        return jsonify({"paintings": [], "total": 0})

    paintings: list[dict[str, Any]] = []
    for jpg_file in sorted(painting_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True):
        json_file = jpg_file.with_suffix(".json")
        meta_data: dict[str, Any] = {}
        if json_file.exists():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
            except Exception:
                pass

        stat = jpg_file.stat()
        created_iso = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        paintings.append({
            "filename": jpg_file.name,
            "url": f"/api/painter/paintings/{jpg_file.name}",
            "prompt": meta_data.get("prompt", jpg_file.stem),
            "style": meta_data.get("style", "freestyle"),
            "author_name": meta_data.get("author_name", "雪莉"),
            "created_at": meta_data.get("created_at", created_iso),
            "duration_sec": meta_data.get("duration_sec", 0.0),
            "command_count": meta_data.get("command_count", 0),
            "size_bytes": stat.st_size,
        })

    return jsonify({"paintings": paintings, "total": len(paintings)})


@painter_bp.route("/api/painter/paintings/<path:filename>", methods=["GET"])
@painter_bp.route("/api/paintings/<path:filename>", methods=["GET"])
@painter_bp.route("/paintings/<path:filename>", methods=["GET"])
async def get_painting_file(filename: str) -> Response | tuple[Response, int] | Any:
    """Serve a painting image file."""
    clean_name = Path(filename).name
    if not (clean_name.endswith(".jpg") or clean_name.endswith(".jpeg") or clean_name.endswith(".png")):
        return jsonify({"error": "Invalid image extension"}), 400

    painting_dir = config.PAINTING_DIR
    target_path = painting_dir / clean_name
    if not target_path.exists():
        return jsonify({"error": "Painting not found"}), 404

    return await send_file(target_path, mimetype="image/jpeg")


@painter_bp.route("/api/painter/status", methods=["GET"])
@painter_bp.route("/status", methods=["GET"])
async def get_painter_live_status() -> Response | tuple[Response, int] | Any:
    """Get current in-progress live drawing status."""
    return jsonify(get_live_painting_status())


@painter_bp.route("/api/painter/draw", methods=["POST"])
@painter_bp.route("/draw", methods=["POST"])
async def api_painter_draw() -> Response | tuple[Response, int] | Any:
    """Trigger Sherry to paint a canvas via Web UI API."""
    import base64
    from mod.painter.agent import _SYSTEM_REFLECTION_PROMPT, MAX_PAINTING_TIMEOUT_SEC
    from mod.painter.canvas import VirtualCanvas
    from mod.painter.interpreter import CommandInterpreter

    start_time = time.time()
    body = await request.get_json(silent=True) or {}
    prompt = str(body.get("prompt", "")).strip()
    style_str = str(body.get("style", "freestyle")).strip()
    image_b64 = body.get("image_b64") or body.get("image") or None
    refine = bool(body.get("refine", False))

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400

    style = ArtStyle.from_str(style_str)
    commands_list: list[dict[str, Any]] = []
    commentary = ""
    images_list = [image_b64] if image_b64 and isinstance(image_b64, str) else None

    bot = _get_bot_instance()
    brain = bot.get_cog("BotBrain") if bot else None

    # ── Stage 1: Draft Pass ──────────────────────────────────────────
    if brain:
        user_prompt = PainterAgent.build_user_prompt(prompt, style, has_image=bool(images_list))
        loop = asyncio.get_running_loop()
        future1: asyncio.Future[str] = loop.create_future()

        async def _on_reply1(reply: str) -> None:
            if not future1.done():
                future1.set_result(reply)

        from handlers.bot_brain import InferenceTask
        task1 = InferenceTask(
            priority=10,
            system_prompt=_SYSTEM_PAINTER_PROMPT,
            user_prompt=user_prompt,
            callback=_on_reply1,
            task_id=f"webdraw-s1-{int(time.time())}",
            images=images_list,
        )
        await brain.enqueue_task(task1)
        try:
            raw_reply1 = await asyncio.wait_for(future1, timeout=50.0)
            commentary, commands_list = PainterAgent.parse_llm_response(raw_reply1)
        except Exception as exc:
            logger.warning("api_painter_draw: Stage 1 LLM fallback: %s", exc)

    if not commands_list:
        commands_list = PainterAgent.generate_procedural_fallback(prompt, style)
        if not commentary:
            commentary = f"✨ 雪莉以【{style.value}】為你完成了這幅畫作！"

    # Initialize canvas and execute Stage 1
    canvas = VirtualCanvas(width=800, height=800, style=style)
    interpreter = CommandInterpreter(canvas)
    interpreter.execute_batch(commands_list)

    # ── Stage 2: Self-Critique & Refine Pass ─────────────────────────
    elapsed = time.time() - start_time
    remaining_budget = MAX_PAINTING_TIMEOUT_SEC - elapsed

    if refine and brain and remaining_budget > 15.0:
        try:
            draft_bytes = canvas.export_bytes(format_name="JPEG", quality=85)
            draft_b64 = base64.b64encode(draft_bytes).decode("utf-8")
            refine_prompt = PainterAgent.build_reflection_user_prompt(prompt, style, draft_commentary=commentary)

            loop = asyncio.get_running_loop()
            future2: asyncio.Future[str] = loop.create_future()

            async def _on_reply2(reply: str) -> None:
                if not future2.done():
                    future2.set_result(reply)

            from handlers.bot_brain import InferenceTask
            stage2_timeout = min(45.0, remaining_budget - 5.0)
            task2 = InferenceTask(
                priority=10,
                system_prompt=_SYSTEM_REFLECTION_PROMPT,
                user_prompt=refine_prompt,
                callback=_on_reply2,
                task_id=f"webdraw-s2-{int(time.time())}",
                images=[draft_b64],
            )
            await brain.enqueue_task(task2)

            raw_reply2 = await asyncio.wait_for(future2, timeout=stage2_timeout)
            commentary2, commands_list2 = PainterAgent.parse_llm_response(raw_reply2)
            if commands_list2:
                interpreter.execute_batch(commands_list2)
                if commentary2:
                    commentary = f"{commentary}\n\n🔍 **雪莉自我審視精修講評**：\n{commentary2}"
        except asyncio.TimeoutError:
            logger.warning("api_painter_draw: Stage 2 self-critique timed out (120s guard). Finalizing with draft.")
        except Exception as exc:
            logger.warning("api_painter_draw: Stage 2 error: %s. Finalizing with draft.", exc)

    # ── Save Final Canvas ──────────────────────────────────────────
    total_duration = round(time.time() - start_time, 2)
    total_command_count = len(interpreter.executed_commands)

    canvas, meta, jpg_path = await PainterAgent.paint_and_save(
        prompt=prompt,
        style=style,
        commands=[],
        author_id="web_console",
        author_name="Console User",
        existing_canvas=canvas,
        stage="refined" if refine else "single_pass",
        custom_duration=total_duration,
        custom_command_count=total_command_count,
    )

    return jsonify({
        "success": True,
        "filename": meta.filename,
        "url": f"/api/painter/paintings/{meta.filename}",
        "prompt": meta.prompt,
        "style": meta.style,
        "commentary": commentary,
        "command_count": meta.command_count,
        "duration_sec": meta.duration_sec,
        "refined": refine,
    })
