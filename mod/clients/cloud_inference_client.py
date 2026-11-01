"""mod/clients/cloud_inference_client.py
=======================================
雲端推論與多模態分析客戶端。

封裝 Google Gemini (google-genai SDK) 與非同步網路請求，提供：
1. 長文結構化摘要 (summarize_text)
2. 專業繁體中文翻譯 (translate_text)
3. 多模態圖像理解與錯誤診斷 (analyze_image)
4. 網頁與 YouTube 內容深度摘要 (summarize_url)

所有對外 HTTP 請求均設定 DEFAULT_TIMEOUT_SEC = 8，
所有同步 SDK 調用皆透過 asyncio.to_thread 確保事件迴圈非阻塞。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional
import aiohttp

import settings

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    APIError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC: int = 8
DEFAULT_LLM_TIMEOUT_SEC: float = 30.0


class CloudInferenceClient:
    """雲端模型推論與多模態分析客戶端。"""

    def __init__(self, api_key: str = "", default_model: str = "gemini-3.5-flash-lite") -> None:
        """初始化雲端推論客戶端。

        Args:
            api_key: Google Gemini API 金鑰。預設讀取 settings.GEMINI_API_KEY。
            default_model: 預設模型名稱（當設定未指定時之後備，採用 gemini-3.5-flash-lite）。
        """
        self._custom_api_key = api_key
        self._default_model = default_model
        self._client_instance: Any = None
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def api_key(self) -> str:
        """取得當前有效的 API Key。"""
        return self._custom_api_key or getattr(settings, "GEMINI_API_KEY", "")

    def get_resolved_model_name(self) -> str:
        """動態讀取 settings.CLOUD_SEARCH_MODEL 並解析 Google SDK 相容名稱。

        若設定為非 Google 體系模型 (如 deepseek:)，則回退至預設之 Google 原生模型作為 Google SDK 兜底。

        Returns:
            str: 適用於 Google SDK 的模型名稱，如 'gemini-3.5-flash-lite'。
        """
        raw_model: str = getattr(settings, "CLOUD_SEARCH_MODEL", "").strip()
        if not raw_model:
            return self._default_model

        if raw_model.startswith("gemini:"):
            return raw_model[len("gemini:"):].strip()
        elif ":" in raw_model:
            parts = raw_model.split(":", 1)
            prefix = parts[0].lower()
            if prefix not in ("gemini", "google"):
                return self._default_model
            return parts[1].strip()
        return raw_model

    def get_candidate_models(self) -> list[str]:
        """取得模型嘗試順序清單（首選模型 + 備援模型序列）。

        Returns:
            list[str]: 依序嘗試的模型名稱清單。
        """
        fallback_pool = [
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.6-flash",
        ]
        primary = self.get_resolved_model_name()
        candidates = [primary]
        for m in fallback_pool:
            if m not in candidates:
                candidates.append(m)
        return candidates

    def _get_genai_client(self) -> Any:
        """取得或建立 google-genai Client 實例。

        Raises:
            RuntimeError: 當 google-genai 未安裝或缺少 API 金鑰時。
        """
        if not _GENAI_AVAILABLE or genai is None:
            raise RuntimeError("未安裝 google-genai 套件，請執行 pip install google-genai>=1.0.0")

        key = self.api_key
        if not key:
            raise RuntimeError("尚未設定 GEMINI_API_KEY，請在 .env 或 Web UI 設定中配置！")

        # 快取複用的 Client 實例，避免每請求重建 SDK 客戶端（初始化成本高）
        if self._client_instance is None:
            self._client_instance = genai.Client(api_key=key)
        return self._client_instance

    async def _get_session(self) -> aiohttp.ClientSession:
        """取得 (或惰性建立) 供所有 HTTP 請求複用的 aiohttp ClientSession。

        Returns:
            aiohttp.ClientSession: 共享之非同步 HTTP 工作階段（具備連線池）。
        """
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SEC)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def aclose(self) -> None:
        """釋放共享資源（GenAI Client 無需手動關閉，僅需關閉 aiohttp Session）。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # 同步內部執行核心 (執行於執行緒池)
    # ------------------------------------------------------------------

    def _call_generate_sync(
        self,
        contents: list[Any] | str,
        model_name: str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """同步呼叫 Gemini generate_content。

        Args:
            contents: 提示詞字串或包含 Part/文字的內容清單。
            model_name: 模型名稱。
            system_instruction: 系統提示詞。

        Returns:
            str: 生成之回覆文字。
        """
        client = self._get_genai_client()
        config_args: dict[str, Any] = {}
        if system_instruction:
            config_args["system_instruction"] = system_instruction

        if types is not None:
            config_args["safety_settings"] = [
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
            ]

        config = types.GenerateContentConfig(**config_args) if (config_args and types is not None) else None

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
        return (response.text or "").strip()

    async def _generate_async(
        self,
        contents: list[Any] | str,
        system_instruction: Optional[str] = None,
    ) -> str:
        """非同步安全執行生成，具備 503 尖峰退避與多模型自動降級備援機制。

        Args:
            contents: 提示詞或內容物件清單。
            system_instruction: 系統提示詞。

        Returns:
            str: 生成成果。
        """
        raw_model: str = getattr(settings, "CLOUD_SEARCH_MODEL", "").strip()
        # 若設定了非 Google 體系模型 (如 deepseek:、openai: 等)，純文字任務優先透過雪莉大腦客戶端 OllamaClient 執行
        if raw_model and isinstance(contents, str):
            prefix = raw_model.split(":", 1)[0].lower() if ":" in raw_model else ""
            if prefix and prefix not in ("gemini", "google"):
                try:
                    from handlers.bot_brain.client import OllamaClient
                    ollama_client = OllamaClient()
                    sys_prompt = system_instruction or "你是專業的繁體中文 AI 助理。"
                    try:
                        result = await asyncio.wait_for(
                            ollama_client.chat(model=raw_model, system_prompt=sys_prompt, user_prompt=contents),
                            timeout=DEFAULT_LLM_TIMEOUT_SEC,
                        )
                        if result and result.strip():
                            return result.strip()
                    finally:
                        await ollama_client.close()
                except Exception as exc:
                    logger.warning(
                        "CloudInferenceClient: 設定模型 %s 呼叫未果 (%s)，自動切換至 Google 原生模型梯隊支援...",
                        raw_model,
                        exc,
                    )

        candidates = self.get_candidate_models()
        last_exception: Optional[Exception] = None

        for idx, model in enumerate(candidates):
            for attempt in range(2):  # 每個模型最多嘗試 2 次 (含重試)
                try:
                    logger.info("CloudInferenceClient: 呼叫雲端模型 %s (第 %d/2 次嘗試)", model, attempt + 1)
                    return await asyncio.wait_for(
                        asyncio.to_thread(self._call_generate_sync, contents, model, system_instruction),
                        timeout=DEFAULT_LLM_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    last_exception = TimeoutError(f"雲端模型呼叫逾時（超過 {int(DEFAULT_LLM_TIMEOUT_SEC)} 秒）")
                    logger.warning("CloudInferenceClient: 模型 %s 呼叫逾時，準備切換備援模型", model)
                    break
                except Exception as exc:
                    last_exception = exc
                    err_text = str(exc).lower()
                    # 辨識 503 (過載) / 429 (限流) 等暫態服務端負載問題
                    is_not_found = any(
                        keyword in err_text
                        for keyword in ("404", "not_found", "not found", "no longer available")
                    )
                    if is_not_found:
                        # 嘗試從 Google 錯誤訊息解析官方推薦新模型 (如 "use models/gemini-3.6-flash")
                        match = re.search(r"use models/(gemini-[\w\.\-]+)", str(exc))
                        if match:
                            suggested_model = match.group(1)
                            if suggested_model not in candidates:
                                logger.info(
                                    "CloudInferenceClient: 偵測到 Google 推薦新模型 '%s'，動態插入備援隊列",
                                    suggested_model,
                                )
                                candidates.insert(idx + 1, suggested_model)

                        logger.warning(
                            "CloudInferenceClient: 模型 %s 已下線或不存在 (404 Not Found)，自動切換至備援模型...",
                            model,
                        )
                        break

                    is_transient = any(
                        keyword in err_text
                        for keyword in ("503", "unavailable", "high demand", "429", "resource_exhausted")
                    )
                    if is_transient:
                        if attempt == 0:
                            wait_sec = 1.0 + (0.5 * idx)
                            logger.warning(
                                "CloudInferenceClient: 模型 %s 遭遇尖峰負載 (503/429)，等待 %.1f 秒後原模型重試...",
                                model,
                                wait_sec,
                            )
                            await asyncio.sleep(wait_sec)
                            continue
                        else:
                            logger.warning(
                                "CloudInferenceClient: 模型 %s 連續尖峰過載，自動降級至下一個備援模型...",
                                model,
                            )
                            break
                    else:
                        logger.error("CloudInferenceClient: 模型推論遭遇非暫態錯誤: %s", exc)
                        raise exc

        # 若全部候選模型均失敗
        if last_exception:
            logger.error("CloudInferenceClient: 所有候選模型均已嘗試，最終異常: %s", last_exception)
            err_str = str(last_exception).lower()
            if any(k in err_str for k in ("503", "high demand", "unavailable")):
                raise RuntimeError(
                    "Google 雲端模型伺服器目前處於全球尖峰超載（503 UNAVAILABLE）。"
                    "雪莉已為您自動嘗試重試與備援模型，建議您稍候 1~2 分鐘再次嘗試！"
                )
            raise last_exception

        raise RuntimeError("未能從任何雲端模型取得回覆。")

    # ------------------------------------------------------------------
    # 公開業務方法 (四大核心任務)
    # ------------------------------------------------------------------

    async def summarize_text(
        self,
        text: str = "",
        image_bytes: bytes = b"",
        mime_type: str = "image/png",
        image_url: str = "",
    ) -> str:
        """全能多模態摘要：自動識別純文字、網址/YouTube 與圖片，產出結構化繁中摘要。

        Args:
            text: 目標原始訊息文本。
            image_bytes: 圖片二進位資料。
            mime_type: 圖片 MIME 類型（如 image/jpeg, image/png）。
            image_url: 圖片遠端下載網址（若未提供 image_bytes 則由 Client 抓取）。

        Returns:
            str: 繁體中文結構化摘要重點或多模態診斷分析。
        """
        clean_text = text.strip()

        # 1. 多模態圖像處理（若有附加圖片）
        if image_bytes or image_url:
            if not image_bytes and image_url:
                image_bytes, mime_type = await self._fetch_image_async(image_url)

            if image_bytes:
                part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                sys_prompt = (
                    "你是雪莉（Sherry）的多模態理解與視覺分析中樞。請深入解析使用者提供的圖片與伴隨文字：\n"
                    "- 若是「程式報錯或系統日誌截圖」：指出具體錯誤原因與建議修復步驟。\n"
                    "- 若是「圖表、資訊圖或數據圖」：總結關鍵數值趨勢與重要含意。\n"
                    "- 若是「插畫、照片或日常圖片」：生動描繪畫面主體細節、意境與亮點。\n"
                    "請一律以親切清晰的繁體中文（台灣用語）條列整理，結構美觀。"
                )
                prompt_text = clean_text or "請詳細分析這張圖片的重點內容、圖意與細節。"
                contents: list[Any] = [part, prompt_text]
                return await self._generate_async(contents, system_instruction=sys_prompt)

        # 2. 網址 / YouTube 影片摘要（若訊息中含有 URL）
        if "http://" in clean_text or "https://" in clean_text:
            return await self.summarize_url(clean_text)

        # 3. 純文字訊息摘要
        if not clean_text:
            return "⚠️ 目標訊息無可分析之文字、網址或圖片內容。"

        sys_prompt = (
            "你是雪莉（Sherry）的雲端思維中樞。你的任務是閱讀使用者提供的 Discord 訊息，"
            "提煉其核心精華。請使用親切、專業、精準的繁體中文（台灣習慣用語）回答，"
            "格式要求：\n"
            "1. 核心結論（1~2 句話）\n"
            "2. 重點條列（3~5 點）\n"
            "3. 關鍵備註或行動建議（若無可略過）"
        )
        prompt = f"請摘要以下 Discord 訊息內容：\n\n{clean_text}"
        return await self._generate_async(prompt, system_instruction=sys_prompt)


    async def translate_text(self, text: str, target_lang: str = "繁體中文") -> str:
        """將目標文字訊息翻譯為指定語言（預設繁體中文）。

        Args:
            text: 待翻譯文字。
            target_lang: 目標語言名稱。

        Returns:
            str: 翻譯成果。
        """
        if not text.strip():
            return "⚠️ 目標訊息無可翻譯之文字內容。"

        sys_prompt = (
            f"你是專業的高級即時口筆譯員。請將使用者提供的訊息準確翻譯為【{target_lang}】。"
            "要求：符合台灣本地用語習慣、保留專有名詞與 Emoji 表情、語氣自然通順、不遺漏細節。"
            "請直接輸出翻譯後成果，無需贅述前言。"
        )
        prompt = f"請翻譯以下內容：\n\n{text}"
        return await self._generate_async(prompt, system_instruction=sys_prompt)

    async def _fetch_image_async(self, image_url: str) -> tuple[bytes, str]:
        """非同步下載圖片二進位資料。"""
        try:
            session = await self._get_session()
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
                    data = await resp.read()
                    return data, content_type
                logger.warning("下載圖片失敗 HTTP %d: %s", resp.status, image_url)
                return b"", "image/png"
        except Exception as exc:
            logger.warning("非同步擷取圖片失敗: %s", exc)
            return b"", "image/png"

    async def analyze_image(
        self,
        image_bytes: bytes = b"",
        mime_type: str = "image/png",
        user_prompt: str = "",
        image_url: str = "",
    ) -> str:
        """對圖片進行多模態視覺辨識、程式報錯截圖診斷與圖表分析。

        Args:
            image_bytes: 圖片二進位資料。
            mime_type: 圖片 MIME 類型（如 image/jpeg, image/png）。
            user_prompt: 附加在圖片旁的說明或額外問題。
            image_url: 圖片遠端下載網址（若未提供 image_bytes 則由 Client 抓取）。

        Returns:
            str: 視覺分析結果。
        """
        if not image_bytes and image_url:
            image_bytes, mime_type = await self._fetch_image_async(image_url)

        if not image_bytes:
            return "⚠️ 未能取得有效之圖片數據進行分析。"

        sys_prompt = (
            "你是雪莉（Sherry）的視覺理解中樞。請深入解析使用者提供的圖片：\n"
            "- 若是「程式報錯或系統日誌截圖」：指出具體錯誤原因與建議修復步驟。\n"
            "- 若是「圖表、資訊圖或數據圖」：總結關鍵數值趨勢與含意。\n"
            "- 若是「插畫、照片或日常圖片」：生動描繪畫面主體細節與氛圍。\n"
            "請一律以親切清晰的繁體中文條列說明。"
        )
        prompt_text = user_prompt.strip() or "請詳細分析這張圖片的內容與關鍵細節。"

        # VISION_MODEL 可能被設為本地模型（ollama:/lms:/vllm:/freetoken:）。
        # 這些後端用 OpenAI-compatible 圖片格式，走 OllamaClient 統一路由，
        # 不要硬性塞進 Gemini SDK，否則本地視覺模型設定形同虛設。
        vision_model = str(getattr(settings, "VISION_MODEL", "") or "").strip()
        if vision_model and not vision_model.lower().startswith(("gemini:", "gemini-", "models/gemini", "google:")):
            try:
                import base64

                from handlers.bot_brain.client import OllamaClient

                client = OllamaClient()
                try:
                    return await client.chat(
                        model=vision_model,
                        system_prompt=sys_prompt,
                        user_prompt=prompt_text,
                        images=[base64.b64encode(image_bytes).decode("ascii")],
                        timeout=180,
                    )
                finally:
                    await client.close()
            except Exception as exc:
                logger.warning("視覺模型 %s 分析失敗，回退 Gemini: %s", vision_model, exc)
                if not getattr(settings, "GEMINI_API_KEY", ""):
                    return f"⚠️ 視覺模型 {vision_model} 呼叫失敗：{exc}"

        part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        contents = [part, prompt_text]
        return await self._generate_async(contents, system_instruction=sys_prompt)

    async def summarize_url(self, raw_content: str) -> str:
        """從文字中萃取 URL，並依類型（YouTube / 一般網頁）進行內容深度摘要。

        Args:
            raw_content: 包含 URL 的原始訊息字串。

        Returns:
            str: 網址內容深度摘要。
        """
        url_match = re.search(r"https?://[^\s<>\"'()]+", raw_content)
        if not url_match:
            return "⚠️ 未在訊息中找到任何有效的網址 (http/https)！"

        target_url = url_match.group(0).rstrip(".,;!?:")
        logger.info("CloudInferenceClient: 識別出目標網址: %s", target_url)

        # 1. 判斷是否為 YouTube 影片
        if any(domain in target_url.lower() for domain in ("youtube.com", "youtu.be")):
            return await self._summarize_youtube_url(target_url)

        # 2. 一般網頁處理
        return await self._summarize_webpage_url(target_url)

    async def _summarize_youtube_url(self, yt_url: str) -> str:
        """處理 YouTube 影片摘要。"""
        # 嘗試利用 yt-dlp 抓取影片元數據與大綱
        metadata_text = await self._fetch_youtube_metadata_async(yt_url)

        sys_prompt = (
            "你是雪莉（Sherry）的 YouTube 影音摘要助理。請針對該影片進行深入剖析：\n"
            "1. 影片主旨與背景核心\n"
            "2. 關鍵段落與論點梳理（條列重點）\n"
            "3. 核心收穫與總結\n"
            "請一律使用繁體中文，格式清晰美觀。"
        )

        prompt = (
            f"目標 YouTube 影片：{yt_url}\n\n"
            f"擷取到的影片相關資訊：\n{metadata_text}\n\n"
            "請根據以上資訊與你對該影片的理解，進行詳細繁體中文摘要。"
        )
        return await self._generate_async(prompt, system_instruction=sys_prompt)

    async def _fetch_youtube_metadata_async(self, yt_url: str) -> str:
        """非同步透過 yt-dlp 擷取 YouTube 影片簡介與章節資訊。"""
        def _extract_sync() -> str:
            try:
                import yt_dlp
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "extract_flat": "in_playlist",
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(yt_url, download=False)
                    if not info:
                        return f"影片連結: {yt_url}"
                    title = info.get("title", "未知標題")
                    desc = info.get("description", "")[:1500]
                    duration = info.get("duration_string", "未知")
                    uploader = info.get("uploader", "未知頻道")
                    tags = ", ".join(info.get("tags", [])[:8])
                    return f"【標題】{title}\n【頻道】{uploader}\n【時長】{duration}\n【標籤】{tags}\n【簡介】\n{desc}"
            except Exception as e:
                logger.warning("yt-dlp 擷取影片元數據失敗: %s", e)
                return f"影片連結: {yt_url}"

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_extract_sync),
                timeout=float(DEFAULT_TIMEOUT_SEC),
            )
        except Exception:
            return f"影片連結: {yt_url}"

    async def _summarize_webpage_url(self, web_url: str) -> str:
        """非同步下載一般網頁並提取純文字進行摘要。"""
        content_text = ""
        page_title = ""

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            }
            session = await self._get_session()
            async with session.get(web_url, headers=headers) as resp:
                if resp.status != 200:
                    return f"⚠️ 無法讀取網頁內容 (HTTP {resp.status})：{web_url}"
                html = await resp.text(errors="replace")

            # 簡易提取 Title 與 Body 純文字
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
            if title_match:
                page_title = title_match.group(1).strip()

            # 清理常見無關標籤 (script, style, noscript)
            clean_html = re.sub(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
            raw_text = re.sub(r"<[^>]+>", " ", clean_html)
            # 壓縮多餘空白
            content_text = re.sub(r"\s+", " ", raw_text).strip()[:10000]

        except Exception as exc:
            logger.warning("CloudInferenceClient: 下載網頁失敗: %s", exc)
            return f"❌ 擷取網頁內容時發生錯誤：{exc}"

        if not content_text:
            return f"⚠️ 網頁未能解析出足夠之有效文字：{web_url}"

        sys_prompt = (
            "你是雪莉（Sherry）的網頁閱讀專家。請為使用者提煉目標網頁的文章重點：\n"
            "1. 網頁主題與核心主旨\n"
            "2. 關鍵段落重點整理（條列 3~5 點）\n"
            "3. 總結觀點或結論\n"
            "請一律以流暢易讀的繁體中文呈現。"
        )
        prompt = (
            f"目標網址：{web_url}\n"
            f"網頁標題：{page_title}\n\n"
            f"網頁內文節錄：\n{content_text}\n\n"
            "請提煉並整理這篇網頁的重點內容："
        )
        return await self._generate_async(prompt, system_instruction=sys_prompt)

    async def extend_ask(
        self,
        target_text: str,
        question: str = "",
        context_history: str = "",
        user_name: str = "",
    ) -> str:
        """針對指定訊息進行延伸追問，融入雪莉完整人設、上下文脈絡與使用者提問。

        Args:
            target_text: 目標原始訊息文本。
            question: 使用者在 Modal 表單中輸入的延伸問題。
            context_history: 頻道歷史對話紀錄摘要或劇本。
            user_name: 提問者暱稱。

        Returns:
            str: 雪莉風格之智能回答。
        """
        if not target_text.strip() and not question.strip():
            return "⚠️ 目標訊息與延伸問題皆為空，無法進行解答！"

        clean_question = question.strip() or "請針對這則訊息提供進一步的詳細說明與看法。"
        asker_info = f"使用者 [{user_name}]" if user_name else "使用者"

        sys_prompt = (
            "你是「雪莉（Sherry）」，一位活潑、機智、親切且有自己鮮明個性的 AI 夥伴。\n"
            "使用者正在對群組或頻道中的某則訊息進行「延伸追問」。\n"
            "請遵守以下原則回答：\n"
            "1. 語氣維持雪莉一貫的親切生動、略帶幽默，使用自然流暢的繁體中文（台灣用語習慣）。\n"
            "2. 結合原訊息與當前的延伸問題進行針對性解答，不要給予答非所問或刻板機械式的回答。\n"
            "3. 若有提供歷史對話上下文，請結合整體背景理解情境。\n"
            "4. 格式排版保持清晰舒適，適度使用 Emoji 或條列輔助閱讀。"
        )

        history_section = f"【頻道近期對話脈絡】：\n{context_history.strip()}\n\n" if context_history.strip() else ""
        prompt = (
            f"{history_section}"
            f"【目標參考訊息】：\n{target_text.strip()}\n\n"
            f"🎯 【{asker_info} 延伸發問】：\n{clean_question}\n\n"
            f"請以雪莉的身份與語氣回答："
        )
        return await self._generate_async(prompt, system_instruction=sys_prompt)

