import base64
import io
import json
import logging
import re
import asyncio
import urllib.parse
from pathlib import Path
import aiohttp
import discord
from discord.ext import commands

import settings
from handlers.bot_brain import BotBrain, InferenceTask
from handlers.social.tool_base import BaseTool, ToolResponseMode
from handlers.social.utils import append_draw_thinking_log
from mod.clients import GeminiImageClient

logger = logging.getLogger(__name__)

class DrawControlView(discord.ui.View):
    def __init__(self, user_query: str, original_author: discord.Member | discord.User):
        super().__init__(timeout=settings.DISCORD_UI_VIEW_TIMEOUT)
        self.user_query = user_query
        self.original_author = original_author

    @discord.ui.button(label="🔄 重畫一張", style=discord.ButtonStyle.primary)
    async def redraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.original_author:
            await interaction.response.send_message("❌ 這是別人要求的畫作喔！", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("好的！我重新畫一張，請稍候...", ephemeral=True)
        from handlers.social.registry import TOOL_LIST
        draw_tool = next((t for t in TOOL_LIST if t.intent_name == "DRAW"), None)
        if draw_tool:
            asyncio.create_task(draw_tool.execute(interaction.client, interaction.message, self.user_query, "", ""))

    @discord.ui.button(label="📝 修改提示詞", style=discord.ButtonStyle.secondary)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.original_author:
            await interaction.response.send_message("❌ 這是別人要求的畫作喔！", ephemeral=True)
            return
            
        # 在 Python 中類別的作用域限制，所以把 Modal 放在方法內
        class PromptModal(discord.ui.Modal, title="修改畫圖提示詞"):
            prompt_input = discord.ui.TextInput(
                label="想畫什麼呢？",
                style=discord.TextStyle.long,
                default=self.user_query,
                required=True
            )
            async def on_submit(self, modal_interaction: discord.Interaction):
                await modal_interaction.response.defer(ephemeral=True)
                await modal_interaction.followup.send("收到新的提示詞！立刻重畫...", ephemeral=True)
                new_query = self.prompt_input.value
                from handlers.social.registry import TOOL_LIST
                draw_tool = next((t for t in TOOL_LIST if t.intent_name == "DRAW"), None)
                if draw_tool:
                    asyncio.create_task(draw_tool.execute(modal_interaction.client, interaction.message, new_query, "", ""))
        
        await interaction.response.send_modal(PromptModal())

class DrawTool(BaseTool):
    def __init__(self):
        # 在初始化時一次性載入 pixiv_tag.json 字典，供角色名 → Danbooru tag 轉換使用
        dict_path = settings.PIXIV_TAG_PATH
        self._pixiv_dict: dict[str, str] = {}
        if dict_path.exists():
            try:
                with open(dict_path, "r", encoding="utf-8") as f:
                    self._pixiv_dict = json.load(f)
                logger.info("DrawTool: 已載入 pixiv_tag.json，共 %d 筆 tag。", len(self._pixiv_dict))
            except Exception as e:
                logger.error("DrawTool: pixiv_tag.json 載入失敗: %s", e)
        self._sorted_pixiv_keys: list[str] = sorted(self._pixiv_dict.keys(), key=len, reverse=True)

    # --- 強制附加的正向提示詞（雲端 + 地端通用）---
    MANDATORY_POSITIVE = [
        "masterpiece",
        "best quality",
        "ultra-detailed",
        "highres",
        "perfect anatomy",
        "perfectly drawn face",
        "anime illustration",
        "cel shading",
        "flat color",
        "vibrant colors",
    ]

    # --- 強制附加的負面提示詞（雲端 + 地端通用）---
    MANDATORY_NEGATIVE = [
        "(worst quality, low quality:1.4)",
        "bad anatomy",
        "bad hands",
        "missing fingers",
        "extra digit",
        "fewer digits",
        "cropped",
        "mutated hands",
        "poorly drawn face",
        "mutation",
        "deformed",
        "ugly",
        "blurry",
        "fused fingers",
        "watermark",
        "text",
        "signature",
    ]

    @property
    def intent_name(self) -> str:
        return "DRAW"

    @property
    def llm_description(self) -> str:
        return "使用者明確要求你畫圖、生成圖片、繪製影像"

    @property
    def response_mode(self) -> ToolResponseMode:
        return ToolResponseMode.DIRECT_UI

    def can_handle_by_regex(self, text: str) -> bool:
        # 明確的繪圖動詞與句型
        return bool(re.search(r"((幫|給)我畫|畫\s*一張|生成\s*一張|幫忙畫|幫我生成|draw me|generate\s+an?)", text, re.IGNORECASE))

    def _extract_danbooru_tags(self, user_query: str) -> list[str]:
        """
        用 pixiv_tag.json 字典掃描使用者輸入，萃取精確的 Danbooru tag。
        採「最長匹配優先」策略，避免短關鍵詞吃掉長關鍵詞。
        例如：「蔚藍檔案星野的泳裝」→ ["hoshino_(blue_archive)", "swimsuit"]
        """
        if not self._pixiv_dict:
            return []

        found_tags: list[str] = []
        scan_text = user_query.lower()

        # 依 key 長度由長到短排序，確保長詞優先匹配
        sorted_keys = self._sorted_pixiv_keys
        for key in sorted_keys:
            if len(key) < 2:
                continue
            if key.lower() in scan_text:
                tag = self._pixiv_dict[key]
                # 避免重複加入同一個 tag
                if tag not in found_tags:
                    found_tags.append(tag)
                # 從掃描文字中移除已匹配的關鍵詞，防止重複命中
                scan_text = scan_text.replace(key.lower(), " ", 1)

        logger.info("DrawTool: 字典萃取到 %d 個 Danbooru tag: %s", len(found_tags), found_tags)
        return found_tags

    async def _translate_to_sd_prompt(self, user_query: str, brain: BotBrain) -> tuple[str, str]:
        """
        將使用者中文描述轉換為二次元動漫風格專用的英文提示詞。
        以畫美少女（2D 二次元）為主要目標，自動補上品質與風格標籤。
        """
        sys_prompt = (
            "你是一個專門為「二次元動漫風格」圖片生成服務的提示詞翻譯機。\n\n"
            "任務：將使用者的中文描述轉換成英文的圖片生成 tag 列表（逗號分隔）。\n\n"
            "【規則】\n"
            "1. 自動在開頭補上品質標籤：masterpiece, best quality, anime style\n"
            "2. 若使用者提到女性角色或未指定性別，加入 1girl\n"
            "3. 若使用者明確提到男性角色，使用 1boy\n"
            "4. 保留所有關鍵細節、服裝、場景、表情、姿勢等修飾詞\n"
            "5. 全部使用小寫 + 底線分隔 (snake_case)\n"
            "6. 直接回傳純 tag 列表，禁止加入任何說明、標點或多餘文字\n\n"
            "範例：\n"
            "使用者說「穿水手服的金髮雙馬尾少女在櫻花樹下微笑」\n"
            "→ masterpiece, best quality, anime style, 1girl, blonde hair, twin tails, sailor uniform, smiling, cherry blossoms, tree, outdoors, day\n\n"
            "若翻譯失敗或不確定，直接回傳使用者原始描述的英文直譯結果（仍保持 tag 格式）。"
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async def on_reply(reply: str):
            if not future.done():
                future.set_result(reply.strip())

        task = InferenceTask(
            priority=settings.PRIORITY_DRAW,
            system_prompt=sys_prompt,
            user_prompt=user_query,
            callback=on_reply,
            model=settings.QUICK_MODEL,
            task_id=f"draw-translate-{id(user_query)}",
        )
        await brain.enqueue_task(task)

        try:
            raw = await asyncio.wait_for(future, timeout=settings.DRAW_RAW_TIMEOUT)
        except asyncio.TimeoutError:
            raw = ""
            logger.warning("SD prompt translation 超時，使用備用提示詞")

        # 確保開頭有基本的品質與風格標籤
        required_prefix = "masterpiece, best quality, anime style"
        if not raw or len(raw.strip()) < 10:
            pos_prompt = f"{required_prefix}, 1girl, cute"
        elif not raw.lower().startswith("masterpiece"):
            pos_prompt = f"{required_prefix}, {raw}"
        else:
            pos_prompt = raw

        return pos_prompt, settings.SD_NEGATIVE_PROMPT_BASE

    async def _expand_prompt(self, user_query: str, brain: BotBrain, seed_tags: list[str] | None = None) -> str:
        """
        前置步驟：將使用者的簡短中文擴寫成豐富的英文繪圖提示詞。
        若有 seed_tags（從 pixiv_tag.json 萃取的精確 Danbooru tag），
        會將其作為 hint 一起送給 LLM，確保角色名與屬性標籤的精確性。
        超時或失敗時回傳原始 user_query 作為備援。
        """
        seed_hint = ""
        if seed_tags:
            seed_hint = (
                f"\n\n【重要提示】以下是從標準 Danbooru tag 資料庫查詢到的精確標籤，"
                f"你必須原封不動地保留這些標籤在輸出中（不要翻譯或改寫它們）：\n"
                f"{', '.join(seed_tags)}"
            )

        sys_prompt = (
            "你現在是一個專業的 AI 繪圖提示詞專家。使用者會輸入一句簡單的中文，請你將它擴寫成豐富的英文提示詞。\n\n"
            "規則：\n"
            "- 必須包含主題、畫風 (如 anime style)、光影、背景細節。\n"
            "- 必須加上高畫質後綴 (masterpiece, best quality, ultra detailed)。\n"
            "- 若有提供精確的 Danbooru tag，必須原封不動地包含在輸出的最前面。\n"
            "- 只輸出英文提示詞字串，不要有任何解釋。"
            + seed_hint
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()

        async def on_reply(reply: str):
            if not future.done():
                future.set_result(reply.strip())

        task = InferenceTask(
            priority=settings.PRIORITY_DRAW,
            system_prompt=sys_prompt,
            user_prompt=f"使用者輸入：{user_query}",
            callback=on_reply,
            model=settings.QUICK_MODEL,
            task_id=f"draw-expand-{id(user_query)}",
        )
        await brain.enqueue_task(task)

        try:
            expanded = await asyncio.wait_for(future, timeout=settings.DRAW_EXPAND_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Prompt 擴寫超時 (10s)，改直接使用自然語言/原始輸入")
            if seed_tags:
                return ", ".join(seed_tags) + f", {user_query}"
            return user_query

        if not expanded or len(expanded.strip()) < 10:
            logger.warning("Prompt 擴寫結果過短，使用 seed_tags 備援")
            if seed_tags:
                return ", ".join(seed_tags) + f", {user_query}"
            return user_query

        logger.info(f"Prompt 擴寫完成: {expanded[:80]}...")
        return expanded

    async def _generate_gemini_image(self, prompt: str) -> bytes | None:
        """
        使用 Google Gemini Nano Banana (gemini-2.5-flash-image) 生成圖片。
        支援自然語言 Prompt 描述。成功回傳圖片 bytes，超時或失敗回傳 None。
        """
        if not settings.GEMINI_API_KEY:
            logger.info("DrawTool: 未設定 GEMINI_API_KEY，跳過 Gemini 圖片生成。")
            return None

        client = GeminiImageClient()
        return await client.generate_image(prompt)

    async def _generate_hf_animagine(self, positive_prompt: str, negative_prompt: str = "") -> bytes | None:
        """
        使用 Hugging Face Inference API 呼叫 Animagine-XL-3.1 生成圖片。
        Animagine-XL-3.1 是專為動漫訓練的 SDXL 模型，對动漫角色（包括蓋藍檔案、原神等）有良好識別。
        需要 HF_TOKEN；https://huggingface.co/settings/tokens
        """
        if not settings.HF_TOKEN:
            logger.info("DrawTool: 未設定 HF_TOKEN，跳過 Animagine 圖片生成。")
            return None

        url = f"https://router.huggingface.co/hf-inference/models/{settings.HF_IMAGE_MODEL}"
        headers = {
            "Authorization": f"Bearer {settings.HF_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "image/png",
        }
        payload = {
            "inputs": positive_prompt,
            "parameters": {
                "negative_prompt": negative_prompt,
                "width": settings.SD_DEFAULT_WIDTH,
                "height": settings.SD_DEFAULT_HEIGHT,
                "num_inference_steps": 28,
                "guidance_scale": 5.0,
            },
        }

        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.post(
                    url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=settings.HF_IMAGE_TIMEOUT_SECONDS)
                ) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("Content-Type", "")
                        if "image" in content_type or "application/octet-stream" in content_type:
                            data = await resp.read()
                            logger.info("DrawTool: HF Animagine 生成成功 (%d bytes)", len(data))
                            return data
                        else:
                            # 可能是 JSON 錯誤訊息
                            err = await resp.text()
                            logger.error("DrawTool: HF 回傳非圖片 content_type=%s body=%s", content_type, err[:200])
                            return None
                    elif resp.status == 503:
                        # 模型正在加載中，可嘗試等待再嘗
                        err = await resp.text()
                        logger.warning("DrawTool: HF 模型初始化中 (503)，跳過: %s", err[:100])
                        return None
                    else:
                        err = await resp.text()
                        logger.error("DrawTool: HF 失敗 HTTP %d: %s", resp.status, err[:200])
                        return None
        except asyncio.TimeoutError:
            logger.warning("DrawTool: HF Animagine 超時 (%ds)", settings.HF_IMAGE_TIMEOUT_SECONDS)
            return None
        except Exception as e:
            logger.error("DrawTool: HF Animagine 連線失敗: %s", e)
            return None

    def _build_animagine_prompt(self, seed_tags: list[str], expanded_prompt: str) -> str:
        """
        针對 Animagine-XL-3.1 的最佳 prompt 格式組裝。
        Animagine 最好的格式：
          [character (game)], [game name], [appearance], [clothing/scene], [quality tags]
        品質標籤的正確用詞是: masterpiece, high score, great score, absurdres
        """
        # Animagine 專屬強化品質標籤
        ANIMAGINE_QUALITY = "masterpiece, high score, great score, absurdres"
        ANIMAGINE_NEGATIVE = (
            "nsfw, lowres, (bad), text, error, fewer, extra, missing, worst quality, "
            "jpeg artifacts, low quality, watermark, unfinished, displeasing, oldest, early, "
            "chromatic aberration, signature, extra digits, artistic error, username, scan, [abstract]"
        )

        # seed_tags 放最前（角色名+遊戲名最重要）
        parts = []
        if seed_tags:
            parts.extend(seed_tags)

        # 再加入 expanded_prompt 的內容（避免與 seed_tags 重複）
        if expanded_prompt:
            existing_lower = {t.lower() for t in parts}
            for tag in expanded_prompt.split(","):
                tag = tag.strip()
                if tag and tag.lower() not in existing_lower:
                    parts.append(tag)
                    existing_lower.add(tag.lower())

        # 加上 Animagine 的品質標籤
        parts.append(ANIMAGINE_QUALITY)
        return ", ".join(parts), ANIMAGINE_NEGATIVE

    async def _generate_pollinations(self, positive_prompt: str, negative_prompt: str = "") -> bytes | None:
        """
        使用 Pollinations.ai 雲端 API 生成圖片（二次元動漫風格）。
        成功回傳圖片 bytes，超時或失敗回傳 None。
        """
        # 截斷過長 prompt，避免 URL 過長導致 404
        MAX_PROMPT_LENGTH = 300
        if len(positive_prompt) > MAX_PROMPT_LENGTH:
            positive_prompt = positive_prompt[:MAX_PROMPT_LENGTH].rsplit(" ", 1)[0]
            logger.warning(f"Pollinations prompt 過長，截斷至 {len(positive_prompt)} 字元")

        encoded_positive = urllib.parse.quote(positive_prompt)
        url = (
            f"{settings.POLLINATIONS_API_URL}/{encoded_positive}"
            f"?width={settings.SD_DEFAULT_WIDTH}"
            f"&height={settings.SD_DEFAULT_HEIGHT}"
            f"&nologo=true"
            f"&model={settings.POLLINATIONS_MODEL}"
        )
        # 附加負面提示詞
        if negative_prompt:
            encoded_negative = urllib.parse.quote(negative_prompt)
            url += f"&negative={encoded_negative}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=settings.POLLINATIONS_TIMEOUT_SECONDS)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logger.info(f"Pollinations.ai 生成成功 ({len(data)} bytes)")
                        return data
                    else:
                        logger.error(f"Pollinations.ai 錯誤: HTTP {resp.status}")
                        return None
        except asyncio.TimeoutError:
            logger.warning(f"Pollinations.ai 超時 ({settings.POLLINATIONS_TIMEOUT_SECONDS}s)")
            return None
        except Exception as e:
            logger.error(f"Pollinations.ai 連線失敗: {e}")
            return None

    async def _generate_image_local(self, positive_prompt: str, negative_prompt: str) -> bytes | None:
        """
        使用本地 Stable Diffusion WebUI 生成圖片（備援方案）。
        """
        payload = {
            "prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "width": settings.SD_DEFAULT_WIDTH,
            "height": settings.SD_DEFAULT_HEIGHT,
            "steps": settings.SD_DEFAULT_STEPS,
            "cfg_scale": settings.SD_DEFAULT_CFG_SCALE,
            "sampler_name": settings.SD_DEFAULT_SAMPLER,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.SD_WEBUI_URL}/sdapi/v1/txt2img",
                    json=payload,
                    timeout=settings.SD_TIMEOUT_SECONDS
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        image_b64 = data["images"][0]
                        return base64.b64decode(image_b64)
                    else:
                        logger.error(f"本地 SD WebUI 錯誤: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"連線本地 SD WebUI 失敗: {e}")
            return None

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        *,
        silent: bool = False,
        **kwargs: object,
    ) -> str | None:
        """畫圖工具核心。

        Args:
            silent: 若為 True，不直接發送確認訊息，執行完成後回傳結果狀態字串。

        Returns:
            silent=False 時回傳 None；silent=True 時回傳 str 狀態摘要。
        """
        brain: BotBrain = bot.get_cog("BotBrain")
        if not brain:
            return "BotBrain 未就緒。" if silent else None

        # 狀態機檢查
        if getattr(brain, "_voice_mode_active", False):
            msg = "名偵探現在正在用語音講話，無法分心畫圖喔！"
            if silent: return msg
            await message.reply(msg, mention_author=True)
            return None

        if not silent:
            await message.reply("好的，名偵探現在來畫圖了！請等我一下...", mention_author=True)

        brain._sd_generating = True
        # 廿別「需要記錄到 log 」的中間變數
        _seed_tags: list[str] = []
        _expanded_prompt: str = ""
        _final_prompt: str = ""
        _neg_str: str = ""
        _source_label: str = "failed"
        _success: bool = False
        try:
            # 1) 前置：字典掃描，從 pixiv_tag.json 萃取精確的 Danbooru tag（角色名、屬性等）
            _seed_tags = self._extract_danbooru_tags(user_query)
            if _seed_tags:
                logger.info("DrawTool: 字典命中，seed_tags=%s", _seed_tags)

            # 2) LLM 擴寫提示詞，seed_tags 作為精確錨點傳入
            en_prompt = await self._expand_prompt(user_query, brain, seed_tags=_seed_tags)
            _expanded_prompt = en_prompt

            # 3) 拼裝強制正向提示詞（前置 + 擴寫內容，去重）
            _neg_str = ", ".join(self.MANDATORY_NEGATIVE)
            en_lower = en_prompt.lower()
            missing_tags = [t for t in self.MANDATORY_POSITIVE if t.lower() not in en_lower]
            if missing_tags:
                en_prompt = ", ".join(missing_tags) + ", " + en_prompt
            _final_prompt = en_prompt

            # =====================================================================
            # 圖片生成四層降級策略：
            #   1. Google Gemini Nano Banana (gemini-2.5-flash-image) → 自然語言首選
            #   2. HF Animagine-XL-3.1 → 動漫角色識別備援
            #   3. Pollinations flux-anime → 免費雲端備援
            #   4. 本地 SD WebUI → 最後防線
            # =====================================================================

            img_bytes: bytes | None = None

            # --- 層級 1: Google Gemini Nano Banana (gemini-2.5-flash-image) ---
            if settings.USE_ONLINE_IMAGE_MODEL and settings.GEMINI_API_KEY:
                logger.info("DrawTool: [1/4] 嘗試 Google Gemini Nano Banana (%s)", settings.GEMINI_IMAGE_MODEL)
                img_bytes = await self._generate_gemini_image(_final_prompt)
                if img_bytes:
                    _source_label = f"Gemini ({settings.GEMINI_IMAGE_MODEL})"

            # --- 層級 2: HF Animagine-XL-3.1 ---
            if settings.USE_ONLINE_IMAGE_MODEL and not img_bytes and settings.HF_TOKEN:
                animagine_prompt, animagine_neg = self._build_animagine_prompt(_seed_tags, en_prompt)
                logger.info("DrawTool: [2/4] 嘗試 HF Animagine-XL-3.1")
                img_bytes = await self._generate_hf_animagine(animagine_prompt, animagine_neg)
                if img_bytes:
                    _source_label = "Animagine-XL-3.1"
                    _final_prompt = animagine_prompt  # 記錄實際送出的 prompt
                    _neg_str = animagine_neg

            # --- 層級 3: Pollinations flux-anime ---
            if settings.USE_ONLINE_IMAGE_MODEL and not img_bytes:
                logger.info("DrawTool: [3/4] 嘗試 Pollinations %s", settings.POLLINATIONS_MODEL)
                img_bytes = await self._generate_pollinations(_final_prompt, _neg_str)
                if img_bytes:
                    _source_label = f"Pollinations ({settings.POLLINATIONS_MODEL})"

            if img_bytes:
                _success = True
                file = discord.File(fp=io.BytesIO(img_bytes), filename="generated.png")
                view = DrawControlView(user_query=user_query, original_author=message.author)
                if silent:
                    await message.reply(file=file, view=view, mention_author=True)
                    return f"畫圖完成！來源：{_source_label}"
                await message.reply("畫好了！你看！", file=file, view=view, mention_author=True)
                logger.info("DrawTool: 畫圖成功，來源: %s", _source_label)
                return None

            # --- 層級 4: 本地 SD WebUI ---
            if not silent:
                await message.reply("抱歉剛剛沒有靈感...讓我用另一種方式試試看！", mention_author=True)
            logger.info("DrawTool: [4/4] 嘗試本地 SD WebUI")
            pos_prompt, neg_prompt = await self._translate_to_sd_prompt(user_query, brain)
            _final_prompt = pos_prompt
            _neg_str = neg_prompt
            _source_label = "Local SD"
            img_bytes = await self._generate_image_local(pos_prompt, neg_prompt)

            if img_bytes:
                _success = True
                file = discord.File(fp=io.BytesIO(img_bytes), filename="generated.png")
                view = DrawControlView(user_query=user_query, original_author=message.author)
                if silent:
                    await message.reply(file=file, view=view, mention_author=True)
                    return f"畫圖完成！來源：本地 SD WebUI"
                await message.reply("畫好了！你看！", file=file, view=view, mention_author=True)
            else:
                _source_label = "failed"
                err_msg = "嗚嗚，畫筆好像壞掉了，畫不出來..."
                if silent: return err_msg
                await message.reply(err_msg, mention_author=True)

        finally:
            brain._sd_generating = False
            # 無論成否都記錄完整的提示詞資訊
            append_draw_thinking_log(
                user_query=user_query,
                seed_tags=_seed_tags,
                expanded_prompt=_expanded_prompt,
                final_prompt=_final_prompt,
                negative_prompt=_neg_str,
                source=_source_label,
                success=_success,
                model_used=settings.HF_IMAGE_MODEL if _source_label == "Animagine-XL-3.1" else "",
            )
