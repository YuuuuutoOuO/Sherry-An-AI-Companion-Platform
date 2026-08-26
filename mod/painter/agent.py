"""
mod/painter/agent.py
====================
Painter Agent coordination, prompt composition, LLM response parsing,
and persistence to ./data/painting/*.jpg.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from mod.painter import config
from mod.painter.canvas import VirtualCanvas
from mod.painter.interpreter import CommandInterpreter
from mod.painter.models import ArtStyle, BrushType, DrawingCommand, PaintingMetadata, ShapeType
from mod.painter.palette import PaletteManager

logger = logging.getLogger(__name__)

# Global tracker for live/in-progress painting (used by Web UI)
_current_live_painting: dict[str, Any] = {
    "is_drawing": False,
    "prompt": "",
    "style": "",
    "progress": 0.0,
    "current_step": 0,
    "total_steps": 0,
    "started_at": 0.0,
}


def get_live_painting_status() -> dict[str, Any]:
    """Retrieve the current in-progress drawing status."""
    return dict(_current_live_painting)


# ---------------------------------------------------------------------------
# Prompt Engineering for LLM Artist Agent
# ---------------------------------------------------------------------------

_SYSTEM_PAINTER_PROMPT = """[SYSTEM: SHERRY DIGITAL CANVAS ARTIST & SEMANTIC VECTOR DSL EMITTER]
妳是天才美少女 AI 畫家「雪莉」，精通數位繪畫、場景佈局與高階藝術積木！
妳的任務是將使用者的靈感與指令，轉化為一幅美麗動人的高階向量畫作指令序列。

### 畫布規格與座標系：
- 畫布尺寸：800 x 800 像素，座標原點 (0,0) 位於左上角，(800, 800) 為右下角。
- 圖層劃分：
  * 'bg'：背景層 (底色、宣紙紋理、天空/漸層/遠景山水)
  * 'main'：主體層 (核心人物、建築骨架、主要物件)
  * 'fg'：前景細節層 (花瓣、落葉、光斑、簽名落款 stamp)
  * 'fx'：特效層 (高光、螢火蟲、流星雨、櫻吹雪)

### 🎨 高階語意藝術積木庫 (High-Level Semantic DSL)：
1. draw_character: { "type": "anime_girl"|"cat"|"chibi", "outfit": "school_swimsuit" (死庫水)|"kimono" (和服)|"sailor" (水手服)|"casual" (洋裝), "pose": "sitting" (坐姿/抱膝)|"standing" (立姿)|"kneeling"|"beach", "hair_color": "#...", "skin_color": "#FFE0BD", "outfit_color": "#...", "x": 400, "y": 550, "scale": 1.0, "layer": "main" }
2. draw_architecture: { "type": "torii_gate" (正統雙橫樑朱紅鳥居)|"japanese_pagoda" (五重塔)|"castle" (城堡)|"cozy_cottage" (小屋)|"city_skyline" (賽博天際線), "x": 400, "y": 680, "width": 360, "height": 340, "color_main": "#D90429", "color_roof": "#1A1A24", "layer": "main" }
3. draw_landscape: { "type": "cherry_tree" (櫻花繁茂古樹)|"bamboo_grove" (節節竹林)|"ocean_waves" (浮世繪翻湧海浪)|"misty_mountains" (三層雲霧山巒)|"starry_galaxy" (璀璨銀河旋臂), "x": 400, "y": 500, "scale": 1.0, "color": "#...", "accent_color": "#...", "layer": "bg" }
4. draw_weather_fx: { "type": "sakura_blizzard" (櫻吹雪飄落)|"shooting_stars" (流星雨劃過)|"fireflies" (螢火蟲光斑)|"sunbeams" (晨曦日芒)|"sparkles" (魔法星塵), "count": 30, "color": "#...", "opacity": 0.8, "layer": "fx" }
5. draw_object: { "type": "lantern" (日式和風提燈)|"boat" (湖面小船), "x": 400, "y": 400, "scale": 1.0, "color": "#...", "layer": "main" }

### 🖌️ 基礎幾何與筆刷工具 (Low-Level Primitives，可自由搭配)：
6. set_background: { "color": "#RRGGBB", "texture": "rice_paper"|"watercolor_grain"|"cyber_grid"|"rough_paper"|"plain" }
7. wash: { "color": "#RRGGBB", "opacity": 0.3, "direction": "vertical"|"radial"|"uniform", "layer": "bg" }
8. shape: { "shape_type": "moon"|"cloud"|"circle"|"rect"|"star"|"mountain", "coords": [...], "fill_color": "#...", "layer": "bg"|"main" }
9. stroke: { "brush": "ink_wash"|"watercolor"|"pencil"|"crayon"|"airbrush", "size": 8, "color": "#...", "points": [[x1, y1], [x2, y2]...], "layer": "main" }
10. splatter: { "x": 400, "y": 400, "radius": 35, "count": 15, "color": "#...", "opacity": 0.7, "layer": "fg" }
11. stamp: { "x": 710, "y": 710, "text": "雪莉", "size": 44, "layer": "fg" }

### 構圖建議：
- 若主題包含人物（如「美少女」、「死庫水」）：請使用 set_background (海灘/夕陽) + draw_landscape (海浪/山巒) + draw_character (人物主體) + draw_weather_fx (光斑/日芒) + stamp。
- 若主題包含建築（如「日本鳥居」）：請使用 set_background (夜空) + draw_landscape (銀河/遠山) + draw_architecture (鳥居) + draw_weather_fx (流星/櫻花) + stamp。
- 靈活調用高階積木，搭配 5 到 15 個指令，即可創造出驚豔無比的完整插畫！

### 輸出格式：
妳必須以繁體中文先輸出一小段俏皮的雪莉創作心得，並緊接著輸出 JSON 程式碼區塊 ```json [ ... ] ```。
範例（美少女死庫水在沙灘）：
```json
[
  {"action": "set_background", "params": {"color": "#FFE5B4", "texture": "watercolor_grain"}},
  {"action": "wash", "params": {"color": "#E76F51", "opacity": 0.35, "direction": "vertical", "layer": "bg"}},
  {"action": "shape", "params": {"shape_type": "moon", "coords": [650, 160, 45], "fill_color": "#FFF8DC", "layer": "bg"}},
  {"action": "draw_landscape", "params": {"type": "ocean_waves", "color": "#1D3557", "accent_color": "#FFFFFF", "layer": "bg"}},
  {"action": "draw_character", "params": {"type": "anime_girl", "outfit": "school_swimsuit", "pose": "sitting", "hair_color": "#3D3A45", "outfit_color": "#1D3557", "trim_color": "#FFFFFF", "x": 400, "y": 620, "scale": 1.1, "layer": "main"}},
  {"action": "draw_weather_fx", "params": {"type": "sunbeams", "count": 20, "layer": "fx"}},
  {"action": "stamp", "params": {"x": 710, "y": 710, "text": "雪莉", "size": 44, "layer": "fg"}}
]
```
"""

_SYSTEM_REFLECTION_PROMPT = """你是由 Google Gemma 驅動的頂級 AI 畫家與藝術評論家「雪莉 (Sherry)」。
目前正在進行【畫作第二階段：自我視覺審視與精修 (Critique & Refine Pass)】。
妳已經繪製好了畫作的中間草圖（已作為多模態 Vision 圖片傳入給妳觀察）。

### 妳的任務：
1. 仔細觀察妳剛才畫的中間草圖：
   - 審視人物神韻、建築比例、山水留白與整體色彩氛圍。
   - 評估是否需要追加粒子特效 (draw_weather_fx: sakura_blizzard / sparkles / fireflies)、大氣暈染 (wash)、光斑 (splatter) 或精緻線條修飾 (stroke)。
2. 以專業畫家眼光，輸出 4 到 15 個精修指令來完善這幅畫作。
3. 如果尚未蓋章，請務必在前景 (fg) 圖層使用 stamp 蓋上朱紅方印「雪莉」！

### 輸出格式：
妳必須先輸出一小段俏皮的雪莉自我審視講評，並緊接著輸出 JSON 程式碼區塊 ```json [ ... ] ```。
"""

MAX_PAINTING_TIMEOUT_SEC: float = 120.0  # Hard 2-minute ceiling protection


class PainterAgent:
    """Agent that orchestrates prompt synthesis, LLM interaction, drawing and storage."""

    @classmethod
    def build_user_prompt(cls, content: str, style: ArtStyle, has_image: bool = False) -> str:
        """Create the user prompt for the LLM artist with dynamic composition & lighting perturbation.
        
        Args:
            content: Subject or theme to paint.
            style: Chosen ArtStyle.
            has_image: True if a reference image is attached.
            
        Returns:
            Formatted user prompt string.
        """
        import random
        palette = PaletteManager.get_palette_for_style(style)
        color_examples = ", ".join(c.hex_code for c in palette[:6])

        composition_angles = [
            "三分法構圖，焦點突出，雅緻留白",
            "全景廣角視角，畫面深遠、空間層次開闊",
            "特寫聚焦核心主體，筆觸生動細膩",
            "對角線動態透視構圖，視覺張力強烈",
            "前景特寫與遠景虛化交融，層次豐富分明",
            "由低向高的仰望透視，雄渾壯闊",
        ]
        lighting_angles = [
            "柔和晨曦初露，光影溫潤清澈",
            "靜謐月光漫射，冷暖對比鮮明",
            "強烈側光斜射，明暗立體感飽滿",
            "暮色晚霞微光，氛圍夢幻浪漫",
            "夜幕星辰點點，環境微光流轉",
        ]
        comp_hint = random.choice(composition_angles)
        light_hint = random.choice(lighting_angles)
        seed_id = f"#{random.randint(10000, 99999)}"

        if has_image:
            return (
                f"【多模態視覺臨摹/創作模式】(靈感標籤: {seed_id})\n"
                f"請仔細觀察我傳給妳的參考圖片，分析其主體輪廓、構圖空間、光影分佈與主要色彩！\n"
                f"使用者主題需求：「{content}」\n"
                f"指定轉化風格：【{style.value}】\n"
                f"建議美學氛圍：{comp_hint}、{light_hint}\n"
                f"推薦配色參考：{color_examples}\n"
                f"請靈活調用高階藝術積木 (draw_character / draw_architecture / draw_landscape / draw_weather_fx) 與基礎筆刷，完成一幅高還原度又極具藝術感的畫作！"
            )

        return (
            f"請為我繪製一幅主題為「{content}」的畫作！(靈感標籤: {seed_id})\n"
            f"指定風格：【{style.value}】\n"
            f"建議美學視角：{comp_hint}、{light_hint}\n"
            f"推薦配色參考：{color_examples}\n"
            f"請發揮妳的藝術美感，調用高階藝術積木 (draw_character / draw_architecture / draw_landscape / draw_weather_fx) 與筆刷工具，完成一幅令人驚豔的畫作吧！"
        )

    @classmethod
    def build_reflection_user_prompt(
        cls, content: str, style: ArtStyle, draft_commentary: str = ""
    ) -> str:
        """Create user prompt for the reflection and critique pass.
        
        Args:
            content: Subject or theme to paint.
            style: Chosen ArtStyle.
            draft_commentary: Stage 1 draft commentary text.
            
        Returns:
            Formatted reflection user prompt.
        """
        palette = PaletteManager.get_palette_for_style(style)
        color_examples = ", ".join(c.hex_code for c in palette[:6])
        return (
            f"【第二階段：畫家自我視覺審視與精修】\n"
            f"請看著傳入的中間畫布草圖！\n"
            f"原定創作主題：「{content}」\n"
            f"畫作風格：【{style.value}】\n"
            f"第一階段構思心得：{draft_commentary or '（初稿完成）'}\n"
            f"推薦調色參考：{color_examples}\n"
            f"請仔細審視畫面，指出可以加強的層次與細節，並輸出第二批精修繪圖指令（加強光影、主體細化、氛圍飛濺、最後落款雪莉印章）！"
        )

    @classmethod
    def parse_llm_response(cls, response_text: str) -> tuple[str, list[dict[str, Any]]]:
        """Extract commentary dialogue and JSON command list from LLM output.
        
        Args:
            response_text: Raw output string from LLM.
            
        Returns:
            Tuple of (commentary_text, command_dicts_list).
        """
        if not response_text:
            return "雪莉為你畫好囉！", []

        commentary = ""
        commands: list[dict[str, Any]] = []

        # 1. Look for ```json ... ``` code blocks
        json_pattern = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response_text, re.IGNORECASE)
        if json_pattern:
            json_str = json_pattern.group(1).strip()
            commentary = response_text[: json_pattern.start()].strip() + " " + response_text[json_pattern.end() :].strip()
            commands = cls._relaxed_json_parse(json_str)
        else:
            commands = cls._relaxed_json_parse(response_text)
            arr_match = re.search(r"\[\s*\{", response_text)
            if arr_match:
                commentary = response_text[: arr_match.start()].strip()

        if not commentary:
            commentary = "✨ 雪莉為你畫好這幅作品囉！快來欣賞吧～"

        return commentary.strip(), commands

    @staticmethod
    def _relaxed_json_parse(raw: str) -> list[dict[str, Any]]:
        """Attempt regex recovery for imperfect JSON from small LLMs."""
        if not raw:
            return []

        cleaned = raw.strip()
        for _ in range(5):
            new_cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict) and "action" in item]
            if isinstance(parsed, dict) and "commands" in parsed and isinstance(parsed["commands"], list):
                return [item for item in parsed["commands"] if isinstance(item, dict) and "action" in item]
        except Exception:
            pass

        arr_match = re.search(r"\[\s*\{[\s\S]*\}\s*\]", cleaned)
        if arr_match:
            try:
                parsed_arr = json.loads(arr_match.group(0))
                if isinstance(parsed_arr, list):
                    return [item for item in parsed_arr if isinstance(item, dict) and "action" in item]
            except Exception:
                pass

        return []

    @classmethod
    def generate_procedural_fallback(cls, content: str, style: ArtStyle) -> list[dict[str, Any]]:
        """Generate high-aesthetic semantic procedural commands leveraging the High-Level Vector DSL.
        
        Args:
            content: Prompt theme.
            style: Chosen art style.
            
        Returns:
            List of valid drawing command dicts.
        """
        import random
        palette = PaletteManager.get_palette_for_style(style)
        colors = [c.hex_code for c in palette]
        if len(colors) < 4:
            colors = ["#0F0F0F", "#264653", "#2A6E55", "#E63946", "#F8F5EC"]

        c_bg = colors[-1]
        c_main1 = colors[0]
        c_main2 = colors[1] if len(colors) > 1 else "#333333"
        c_accent1 = colors[2] if len(colors) > 2 else "#E63946"
        c_accent2 = colors[3] if len(colors) > 3 else "#DAA520"

        texture = "rice_paper" if style == ArtStyle.INK_WASH else "plain"
        if style == ArtStyle.NEON:
            texture = "cyber_grid"
        elif style == ArtStyle.WATERCOLOR:
            texture = "watercolor_grain"
        elif style == ArtStyle.CRAYON:
            texture = "rough_paper"

        commands: list[dict[str, Any]] = [
            {"action": "set_background", "params": {"color": c_bg, "texture": texture}},
            {"action": "wash", "params": {"color": c_main2, "opacity": random.uniform(0.18, 0.32), "direction": "vertical", "layer": "bg"}},
        ]

        text_lower = content.lower()

        # Celestial Sky
        if any(w in text_lower for w in ["月", "moon", "夜", "星", "night", "sky", "鳥居", "水墨"]):
            mx = random.randint(180, 620)
            my = random.randint(120, 220)
            mr = random.randint(38, 52)
            commands.append({"action": "shape", "params": {"shape_type": "moon", "coords": [mx, my, mr], "fill_color": random.choice(["#FFFDD0", "#FFE4B5", "#F0F8FF", "#FFF8DC"]), "layer": "bg"}})

        if any(w in text_lower for w in ["星", "star", "夜", "night", "宇宙", "galaxy"]):
            commands.append({"action": "draw_landscape", "params": {"type": "starry_galaxy", "color": "#3A0CA3", "accent_color": "#FFFFFF", "layer": "bg"}})
            commands.append({"action": "draw_weather_fx", "params": {"type": "shooting_stars", "count": 24, "layer": "fx"}})

        # ── Subject 1: Character (Anime Girl / Swimsuit / Kimono) ────────
        if any(w in text_lower for w in ["少女", "girl", "死庫水", "泳裝", "swimsuit", "和服", "kimono", "妹", "人", "女"]):
            outfit = "school_swimsuit" if any(w in text_lower for w in ["死庫水", "泳裝", "swimsuit"]) else ("kimono" if "和服" in text_lower or "kimono" in text_lower else "casual")
            pose = "sitting" if any(w in text_lower for w in ["坐", "sit", "沙灘", "beach", "死庫水"]) else "standing"
            # Background support (beach/ocean if swimsuit)
            if outfit == "school_swimsuit":
                commands.append({"action": "draw_landscape", "params": {"type": "ocean_waves", "color": "#1D3557", "accent_color": "#FFFFFF", "layer": "bg"}})
                commands.append({"action": "draw_weather_fx", "params": {"type": "sunbeams", "count": 15, "layer": "fx"}})
            else:
                commands.append({"action": "draw_landscape", "params": {"type": "cherry_tree", "scale": 0.9, "color": c_main1, "accent_color": "#FFB7B2", "layer": "bg"}})
                commands.append({"action": "draw_weather_fx", "params": {"type": "sakura_blizzard", "count": 30, "color": "#FFB7B2", "layer": "fx"}})

            commands.append({"action": "draw_character", "params": {
                "type": "anime_girl",
                "outfit": outfit,
                "pose": pose,
                "hair_color": random.choice(["#2B2D42", "#4A4E69", "#DDA15E", "#E63946", "#6D6875"]),
                "outfit_color": "#1D3557" if outfit == "school_swimsuit" else c_main1,
                "trim_color": "#FFFFFF",
                "x": 400,
                "y": 620,
                "scale": 1.1,
                "layer": "main"
            }})

        # ── Subject 2: Architecture (Torii Gate / Pagoda / Castle) ──────
        elif any(w in text_lower for w in ["鳥居", "torii", "寺", "塔", "pagoda", "城", "castle", "建築"]):
            arch_type = "torii_gate" if any(w in text_lower for w in ["鳥居", "torii"]) else ("japanese_pagoda" if any(w in text_lower for w in ["塔", "寺", "pagoda"]) else "castle")
            commands.append({"action": "draw_landscape", "params": {"type": "misty_mountains", "color": c_main2, "accent_color": "#4A5568", "layer": "bg"}})
            commands.append({"action": "draw_architecture", "params": {
                "type": arch_type,
                "x": 400,
                "y": 720,
                "width": 380,
                "height": 360,
                "color_main": "#D90429" if arch_type == "torii_gate" else c_main1,
                "color_roof": "#1A1A24",
                "layer": "main"
            }})
            commands.append({"action": "draw_weather_fx", "params": {"type": "fireflies" if "夜" in text_lower or "星" in text_lower else "sakura_blizzard", "count": 28, "layer": "fx"}})

        # ── Subject 3: Nature / Landscapes (Sakura / Bamboo / Waves) ────
        elif any(w in text_lower for w in ["櫻", "sakura", "花", "樹", "竹", "bamboo", "海", "wave"]):
            if any(w in text_lower for w in ["竹", "bamboo"]):
                commands.append({"action": "draw_landscape", "params": {"type": "bamboo_grove", "scale": 1.1, "color": "#2D6A4F", "accent_color": "#52B788", "layer": "bg"}})
                commands.append({"action": "draw_weather_fx", "params": {"type": "fireflies", "count": 30, "color": "#D8F3DC", "layer": "fx"}})
            elif any(w in text_lower for w in ["海", "wave", "浪"]):
                commands.append({"action": "draw_landscape", "params": {"type": "ocean_waves", "color": "#1D3557", "accent_color": "#F1FAEE", "layer": "bg"}})
                commands.append({"action": "draw_object", "params": {"type": "boat", "x": 380, "y": 520, "scale": 0.9, "color": "#E63946", "layer": "main"}})
            else:
                commands.append({"action": "draw_landscape", "params": {"type": "cherry_tree", "scale": 1.15, "color": c_main1, "accent_color": "#FFB7B2", "layer": "bg"}})
                commands.append({"action": "draw_weather_fx", "params": {"type": "sakura_blizzard", "count": 35, "color": "#FFC6FF", "layer": "fx"}})

        # ── Subject 4: Cat / Pet / Animal ─────────────────────────────
        elif any(w in text_lower for w in ["貓", "cat", "喵", "狗", "兔"]):
            commands.append({"action": "draw_landscape", "params": {"type": "misty_mountains", "color": c_main2, "accent_color": "#718096", "layer": "bg"}})
            commands.append({"action": "draw_character", "params": {
                "type": "cat",
                "hair_color": c_main1,
                "x": 400,
                "y": 620,
                "scale": 1.2,
                "layer": "main"
            }})
            commands.append({"action": "draw_weather_fx", "params": {"type": "fireflies", "count": 25, "layer": "fx"}})

        # ── Default Fallback ──────────────────────────────────────────
        else:
            commands.append({"action": "draw_landscape", "params": {"type": "misty_mountains", "color": c_main2, "accent_color": "#718096", "layer": "bg"}})
            commands.append({"action": "draw_landscape", "params": {"type": "cherry_tree", "scale": 1.0, "color": c_main1, "accent_color": "#FFB7B2", "layer": "bg"}})
            commands.append({"action": "draw_weather_fx", "params": {"type": "sakura_blizzard", "count": 30, "layer": "fx"}})

        # Splatter Atmosphere & Red Seal Stamp
        commands.append({"action": "splatter", "params": {
            "x": 400 + random.randint(-40, 40),
            "y": 500 + random.randint(-50, 50),
            "radius": 35,
            "count": 14,
            "color": c_accent1,
            "opacity": 0.6,
            "layer": "fg"
        }})
        commands.append({"action": "stamp", "params": {"x": 710, "y": 710, "text": "雪莉", "size": 44, "layer": "fg"}})

        return commands

    @classmethod
    async def paint_and_save(
        cls,
        prompt: str,
        style: ArtStyle,
        commands: list[dict[str, Any]],
        author_id: str = "0",
        author_name: str = "User",
        width: int = 800,
        height: int = 800,
        existing_canvas: VirtualCanvas | None = None,
        stage: str = "finalizing",
        custom_duration: float | None = None,
        custom_command_count: int | None = None,
    ) -> tuple[VirtualCanvas, PaintingMetadata, Path]:
        """Execute drawing commands, composite layers, and persist to ./data/painting/*.jpg.
        
        Args:
            prompt: User prompt description.
            style: ArtStyle.
            commands: List of drawing command dictionaries.
            author_id: Discord User ID or web user.
            author_name: Discord user name or web user.
            width: Canvas width.
            height: Canvas height.
            existing_canvas: Optional existing VirtualCanvas to continue drawing on.
            stage: Current painting stage name.
            custom_duration: Optional pre-calculated duration in seconds.
            custom_command_count: Optional pre-calculated total commands count.
            
        Returns:
            Tuple of (VirtualCanvas instance, PaintingMetadata record, Path to saved .jpg file).
        """
        start_t = time.perf_counter()
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        short_id = f"{int(time.time() * 1000) % 1000000:06d}"
        filename_base = f"paint_{timestamp_str}_{style.value}_{short_id}"
        jpg_filename = f"{filename_base}.jpg"
        json_filename = f"{filename_base}.json"

        # Update global live tracker
        _current_live_painting.update({
            "is_drawing": True,
            "prompt": prompt,
            "style": style.value,
            "progress": 0.0,
            "current_step": 0,
            "total_steps": len(commands),
            "started_at": time.time(),
            "stage": stage,
        })

        # Initialize Canvas & Interpreter
        canvas = existing_canvas if existing_canvas is not None else VirtualCanvas(width=width, height=height, style=style)
        interpreter = CommandInterpreter(canvas)

        def _step_callback(current: int, total: int) -> None:
            if total > 0:
                _current_live_painting["current_step"] = current
                _current_live_painting["progress"] = round(current / total, 2)

        # Run interpretation
        if commands:
            interpreter.execute_batch(commands, on_step=_step_callback)
        
        measured_duration = round(time.perf_counter() - start_t, 2)
        final_duration = custom_duration if custom_duration is not None else max(0.01, measured_duration)
        final_command_count = custom_command_count if custom_command_count is not None else len(interpreter.executed_commands)

        # Ensure destination directory exists
        painting_dir = config.PAINTING_DIR
        painting_dir.mkdir(parents=True, exist_ok=True)
        jpg_path = painting_dir / jpg_filename
        json_path = painting_dir / json_filename

        # Save high quality JPEG
        canvas.save_to_disk(jpg_path, format_name="JPEG", quality=92)

        # Build & Save Metadata
        meta = PaintingMetadata(
            painting_id=filename_base,
            prompt=prompt,
            style=style.value,
            author_id=author_id,
            author_name=author_name,
            created_at=now.isoformat(),
            command_count=final_command_count,
            filename=jpg_filename,
            duration_sec=final_duration,
            width=width,
            height=height,
            tags=[style.value, "ai_painter", f"stage_{stage}"],
        )

        meta_dict = {
            "painting_id": meta.painting_id,
            "prompt": meta.prompt,
            "style": meta.style,
            "author_id": meta.author_id,
            "author_name": meta.author_name,
            "created_at": meta.created_at,
            "command_count": meta.command_count,
            "filename": meta.filename,
            "duration_sec": meta.duration_sec,
            "width": meta.width,
            "height": meta.height,
            "tags": meta.tags,
            "commands": [
                {"action": cmd.action, "params": cmd.params} for cmd in interpreter.executed_commands
            ],
        }

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(meta_dict, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("PainterAgent: Failed to save painting metadata JSON: %s", exc)

        _current_live_painting["is_drawing"] = False
        _current_live_painting["progress"] = 1.0

        logger.info(
            "PainterAgent: Completed painting '%s' [%s] in %.2fs -> %s",
            prompt, style.value, final_duration, jpg_path
        )
        return canvas, meta, jpg_path
