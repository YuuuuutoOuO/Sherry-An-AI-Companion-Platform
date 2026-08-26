"""
deep_search_engine.py (mod/deep_search/engine)
==============================================
多輪自主深度研究推演引擎核心。
協調規劃、搜尋、精讀、反思與最終綜合報告生成。
遵循 Clean Architecture，無任何 Discord / Web UI / SQLite 外部依賴。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any

from mod.deep_search import config
from mod.deep_search.domain.interfaces import (
    IDeepSearchEngine,
    ILLMClient,
    IScraperClient,
    ISearchClient,
    ISessionRepository,
)
from mod.deep_search.domain.models import (
    DeepResearchReport,
    ResearchProgress,
    ResearchSession,
    SearchResultItem,
    SourceDoc,
    ThoughtLogEntry,
)
from mod.deep_search.engine.prompts import (
    FINAL_SYNTHESIS_SYSTEM_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    READING_SYSTEM_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


class DeepSearchEngine(IDeepSearchEngine):
    """多輪自主深度研究引擎實作。"""

    def __init__(
        self,
        search_client: ISearchClient,
        scraper_client: IScraperClient,
        llm_client: ILLMClient,
        session_repository: ISessionRepository,
    ) -> None:
        self._search = search_client
        self._scraper = scraper_client
        self._llm = llm_client
        self._repo = session_repository

    async def run_research(
        self,
        topic: str,
        model: str,
        max_rounds: int = 4,
        session_id: str | None = None,
        progress_callback: Callable[[ResearchProgress], Any] | None = None,
    ) -> ResearchSession:
        """啟動並執行完整的多輪自主深度研究迴圈。

        Args:
            topic: 研究核心主題。
            model: 使用之 LLM 模型。
            max_rounds: 研究子專題輪數。
            session_id: 自訂或指定之 session_id。
            progress_callback: 即時進度通知回呼。

        Returns:
            ResearchSession: 完整研究專案成果。
        """
        start_time = time.time()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        target_model = model or config.DEFAULT_MODEL
        actual_session_id = session_id or f"rs-{int(time.time())}"

        # 1. 建立初始 Session
        session = ResearchSession(
            session_id=actual_session_id,
            topic=topic.strip(),
            model=target_model,
            status="RUNNING",
            progress_pct=5,
            current_step="正在進行主題深度拆解與研究綱領規劃...",
            max_rounds=max_rounds,
            current_round=0,
            started_at=now_str,
        )
        self._repo.save_session(session)
        await self._notify_progress(session, progress_callback)

        thought_logs: list[ThoughtLogEntry] = []
        collected_sources: list[SourceDoc] = []
        dimension_notes: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        try:
            # -------------------------------------------------------------
            # 階段 1：研究綱領與維度規劃 (Planning Phase)
            # -------------------------------------------------------------
            logger.info("[deep_search.engine] 階段 1: 規劃研究綱領 (topic='%s', max_rounds=%d)", topic, max_rounds)
            planning_user_prompt = (
                f"【研究主題】：{topic}\n"
                f"【指定探討維度數量】：請嚴格拆解出正好 {max_rounds} 個核心探討維度與精準搜尋關鍵字。"
            )
            plan_raw = await self._llm.generate(
                model=target_model,
                system_prompt=PLANNING_SYSTEM_PROMPT,
                user_prompt=planning_user_prompt,
                temperature=0.2,
                max_tokens=2048,
                timeout=120.0,
            )

            dimensions = self._parse_planning_json(plan_raw, topic)
            if not dimensions:
                # 防呆預設維度 (依 max_rounds 動態生成)
                default_candidates = [
                    {
                        "dimension_title": "核心機制與技術原理",
                        "key_questions": f"{topic} 的運作機制與基礎架構為何？",
                        "search_queries": [topic, f"{topic} 原理 架構"],
                    },
                    {
                        "dimension_title": "現況實務與關鍵數據應用",
                        "key_questions": f"{topic} 的最新發展與產業應用現況如何？",
                        "search_queries": [f"{topic} 應用 最新進展 2026", f"{topic} 行情 趨勢 數據"],
                    },
                    {
                        "dimension_title": "關鍵爭議與瓶頸限制",
                        "key_questions": f"{topic} 面臨哪些技術瓶頸、爭議與潛在風險？",
                        "search_queries": [f"{topic} 挑戰 瓶頸 風險"],
                    },
                    {
                        "dimension_title": "未來演進趨勢與戰略啟示",
                        "key_questions": f"{topic} 未來演進方向與重大戰略啟示為何？",
                        "search_queries": [f"{topic} 未來趨勢 預測 展望"],
                    },
                ]
                dimensions = default_candidates[:max_rounds]

            # 嚴格控制維度數量不大於使用者指定之 max_rounds
            dimensions = dimensions[:max_rounds]

            plan_thought_content = f"已完成研究架構規劃，共拆解出 {len(dimensions)} 個探討維度：\n" + "\n".join(
                f"{i+1}. **{d.get('dimension_title')}**：{d.get('key_questions')}"
                for i, d in enumerate(dimensions)
            )
            thought_logs.append(
                ThoughtLogEntry(
                    timestamp=self._current_time_str(),
                    phase="PLANNING",
                    title="📐 規劃研究綱領與核心假說",
                    content=plan_thought_content,
                    round_index=0,
                )
            )

            session = self._clone_session_with(
                session,
                progress_pct=15,
                current_step=f"已制定 {len(dimensions)} 個探討維度，開始深入檢索與文獻精讀...",
                thought_logs=tuple(thought_logs),
            )
            self._repo.save_session(session)
            await self._notify_progress(session, progress_callback)

            # -------------------------------------------------------------
            # 階段 2：多輪子專題鑽研迴圈 (Multi-round Exploration)
            # -------------------------------------------------------------
            total_dims = len(dimensions)
            for round_idx in range(total_dims):
                dim_info = dimensions[round_idx]
                dim_title = dim_info.get("dimension_title", f"維度 {round_idx + 1}")
                key_questions = dim_info.get("key_questions", "")
                search_queries = dim_info.get("search_queries", [topic])

                logger.info(
                    "[deep_search.engine] 輪次 %d/%d: 鑽研維度 '%s'",
                    round_idx + 1,
                    total_dims,
                    dim_title,
                )

                # 計算進度百分比 (15% ~ 80%)
                base_pct = 15 + int((round_idx / total_dims) * 65)

                # A. 搜尋檢索
                session = self._clone_session_with(
                    session,
                    current_round=round_idx + 1,
                    progress_pct=base_pct,
                    current_step=f"[{round_idx+1}/{total_dims}] 正在連網檢索維度「{dim_title}」...",
                )
                self._repo.save_session(session)
                await self._notify_progress(session, progress_callback)

                round_results: list[SearchResultItem] = []
                for q in search_queries[: config.QUERIES_PER_ROUND]:
                    res_items = await self._search.search(
                        query=q, max_results=config.MAX_RESULTS_PER_QUERY
                    )
                    round_results.extend(res_items)

                # 記錄搜尋思考
                search_thought = f"針對維度【{dim_title}】執行關鍵字檢索：`{', '.join(search_queries)}`。\n檢索返回 {len(round_results)} 筆候選資料來源。"
                thought_logs.append(
                    ThoughtLogEntry(
                        timestamp=self._current_time_str(),
                        phase="SEARCHING",
                        title=f"🔍 檢索文獻: {dim_title}",
                        content=search_thought,
                        round_index=round_idx + 1,
                    )
                )

                # B. 網頁擷取與去噪
                round_scraped_docs: list[SourceDoc] = []
                for res in round_results:
                    if res.url in seen_urls:
                        continue
                    if len(round_scraped_docs) >= config.MAX_PAGES_TO_SCRAPE_PER_ROUND:
                        break

                    seen_urls.add(res.url)
                    doc = await self._scraper.scrape(url=res.url, snippet=res.snippet)
                    if doc and doc.content:
                        round_scraped_docs.append(doc)
                        collected_sources.append(doc)

                # C. 文獻精讀與事實萃取
                reading_notes = ""
                if round_scraped_docs:
                    combined_docs_text = "\n\n".join(
                        f"【文獻來源 {i+1}】標題：{d.title}\n網址：{d.url}\n內文：\n{d.content}"
                        for i, d in enumerate(round_scraped_docs)
                    )
                    reading_prompt = (
                        f"【當前研究焦點】：{dim_title}\n"
                        f"【核心探討問題】：{key_questions}\n\n"
                        f"【參考文獻內容】：\n{combined_docs_text[:12000]}"
                    )
                    reading_notes = await self._llm.generate(
                        model=target_model,
                        system_prompt=READING_SYSTEM_PROMPT,
                        user_prompt=reading_prompt,
                        temperature=0.2,
                        max_tokens=2048,
                        timeout=180.0,
                    )
                else:
                    reading_notes = "未能自公開網路取得該維度之完整文獻，將依據檢索摘要與理論模型進行推演。"

                thought_logs.append(
                    ThoughtLogEntry(
                        timestamp=self._current_time_str(),
                        phase="READING",
                        title=f"📖 文獻精讀與要點萃取: {dim_title}",
                        content=reading_notes,
                        round_index=round_idx + 1,
                    )
                )

                # D. 批判性反思與假說驗證
                reflection_prompt = (
                    f"【研究維度】：{dim_title}\n"
                    f"【待回答核心問題】：{key_questions}\n\n"
                    f"【已萃取之文獻事實筆記】：\n{reading_notes}\n\n"
                    "請進行批判性反思與邏輯推演，提煉本維度的核心洞察與尚未解明之盲點。"
                )
                reflection_text = await self._llm.generate(
                    model=target_model,
                    system_prompt=REFLECTION_SYSTEM_PROMPT,
                    user_prompt=reflection_prompt,
                    temperature=0.3,
                    max_tokens=2048,
                    timeout=180.0,
                )

                thought_logs.append(
                    ThoughtLogEntry(
                        timestamp=self._current_time_str(),
                        phase="REFLECTING",
                        title=f"🧠 批判性反思與假說驗證: {dim_title}",
                        content=reflection_text,
                        round_index=round_idx + 1,
                    )
                )

                dimension_notes.append({
                    "title": dim_title,
                    "questions": key_questions,
                    "notes": reading_notes,
                    "reflection": reflection_text,
                })

                session = self._clone_session_with(
                    session,
                    progress_pct=min(80, base_pct + int(65 / total_dims)),
                    thought_logs=tuple(thought_logs),
                    sources=tuple(collected_sources),
                )
                self._repo.save_session(session)
                await self._notify_progress(session, progress_callback)

            # -------------------------------------------------------------
            # 階段 3：論文級宏觀專題大報告合成 (Final Synthesis Phase)
            # -------------------------------------------------------------
            logger.info("[deep_search.engine] 階段 3: 生成最終宏觀研究報告...")
            session = self._clone_session_with(
                session,
                progress_pct=85,
                current_step="正在進行多方觀點交叉比對，生成萬字級論文式深度專案研究報告...",
                thought_logs=tuple(thought_logs),
                sources=tuple(collected_sources),
            )
            self._repo.save_session(session)
            await self._notify_progress(session, progress_callback)

            all_notes_text = ""
            for idx, dn in enumerate(dimension_notes):
                all_notes_text += (
                    f"\n### 維度 {idx+1}: {dn['title']}\n"
                    f"- 探討問題: {dn['questions']}\n"
                    f"- 文獻要點萃取:\n{dn['notes']}\n"
                    f"- 批判性推演結論:\n{dn['reflection']}\n"
                )

            sources_summary = "\n".join(
                f"- [{s.title}]({s.url}) (擷取字數: {s.char_count})"
                for s in collected_sources
            )

            synthesis_user_prompt = (
                f"【研究主題】：{topic}\n\n"
                f"【多維度研究筆記與反思全集】：\n{all_notes_text}\n\n"
                f"【文獻來源清單】：\n{sources_summary}\n\n"
                "請嚴格依據系統提示詞之章節結構，撰寫出一篇內容極其豐富、論據扎實、具學術智庫深度的大型研究報告。"
            )

            final_report_md = await self._llm.generate(
                model=target_model,
                system_prompt=FINAL_SYNTHESIS_SYSTEM_PROMPT,
                user_prompt=synthesis_user_prompt,
                temperature=0.3,
                max_tokens=4096,
                timeout=300.0,
            )

            # 若初次生成為空或過短，嘗試以精簡 Prompt 重試一次
            if not final_report_md or len(final_report_md.strip()) < 50:
                logger.warning("[deep_search.engine] 初次報告生成為空，嘗試以精簡摘要提示詞重試...")
                compact_prompt = (
                    f"【研究主題】：{topic}\n\n"
                    f"【各維度核心結論】：\n{all_notes_text[:4000]}\n\n"
                    "請撰寫完整的研究專案報告，包含執行摘要、核心技術剖析與結論。"
                )
                try:
                    retry_md = await self._llm.generate(
                        model=target_model,
                        system_prompt=FINAL_SYNTHESIS_SYSTEM_PROMPT,
                        user_prompt=compact_prompt,
                        temperature=0.3,
                        max_tokens=4096,
                        timeout=240.0,
                    )
                    if retry_md and len(retry_md.strip()) >= 50:
                        final_report_md = retry_md
                except Exception as retry_err:
                    logger.warning("[deep_search.engine] 重試生成發生異常: %s", retry_err)

            # 若 LLM 依然失敗，自動使用多輪蒐集到的精華構建備援結構化報告
            if not final_report_md or len(final_report_md.strip()) < 50:
                logger.warning("[deep_search.engine] LLM 合成失敗，啟動備援報告構建機制...")
                final_report_md = self._build_fallback_report(
                    topic=topic,
                    dimension_notes=dimension_notes,
                    collected_sources=collected_sources,
                )

            # 提取執行摘要 (若有)
            exec_summary = self._extract_summary(final_report_md)
            if not exec_summary:
                # 若正則未提取出，自動由各維度核心推論提煉摘要
                exec_summary = self._build_fallback_summary(topic, dimension_notes, collected_sources)

            elapsed_sec = round(time.time() - start_time, 2)
            finish_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            report = DeepResearchReport(
                topic=topic,
                model_used=target_model,
                executive_summary=exec_summary,
                full_content=final_report_md,
                sources=collected_sources,
                total_time_seconds=elapsed_sec,
                created_at=finish_time_str,
            )

            thought_logs.append(
                ThoughtLogEntry(
                    timestamp=self._current_time_str(),
                    phase="SYNTHESIZING",
                    title="📑 專題研究報告撰寫完成",
                    content=f"已完成全篇報告合成，耗時 {elapsed_sec:.1f} 秒，共引用 {len(collected_sources)} 篇文獻來源。",
                    round_index=total_dims,
                )
            )

            session = self._clone_session_with(
                session,
                status="COMPLETED",
                progress_pct=100,
                current_step="研究專案推演圓滿完成！",
                thought_logs=tuple(thought_logs),
                sources=tuple(collected_sources),
                final_report=report,
                finished_at=finish_time_str,
            )
            self._repo.save_session(session)
            await self._notify_progress(session, progress_callback)
            logger.info("[deep_search.engine] 深度研究任務圓滿完成 (session_id=%s)", actual_session_id)
            return session

        except Exception as exc:
            logger.exception("[deep_search.engine] 深度研究推演異常中斷: %s", exc)
            finish_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            session = self._clone_session_with(
                session,
                status="FAILED",
                current_step=f"研究推演失敗: {exc}",
                error_message=str(exc),
                thought_logs=tuple(thought_logs),
                sources=tuple(collected_sources),
                finished_at=finish_time_str,
            )
            self._repo.save_session(session)
            await self._notify_progress(session, progress_callback)
            return session

    @staticmethod
    def _parse_planning_json(raw_text: str, topic: str) -> list[dict[str, Any]]:
        """安全解析 LLM 返回的研究維度 JSON 陣列。"""
        # 尋找代碼塊中的 JSON
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw_text, re.DOTALL)
        candidate = json_match.group(1) if json_match else raw_text.strip()

        try:
            data = json.loads(candidate)
            if isinstance(data, list) and len(data) > 0:
                clean_dims: list[dict[str, Any]] = []
                for item in data:
                    if isinstance(item, dict):
                        clean_dims.append({
                            "dimension_title": str(item.get("dimension_title", "")),
                            "key_questions": str(item.get("key_questions", "")),
                            "search_queries": list(item.get("search_queries", [topic])),
                        })
                if clean_dims:
                    return clean_dims
        except Exception:
            pass

        return []

    @staticmethod
    def _extract_summary(full_report: str) -> str:
        """從報告 Markdown 中萃取執行摘要段落。

        支援 ## 1. 執行摘要、## 執行摘要、### 執行摘要、**執行摘要** 及 Executive Summary。
        """
        if not full_report or not full_report.strip():
            return ""

        patterns = [
            r"(?:^|\n)##\s*(?:(?:1\.|一、)\s*)?(?:執行摘要|Executive Summary)[^\n]*\n+([\s\S]*?)(?=\n##|\Z)",
            r"(?:^|\n)###\s*(?:(?:1\.|一、)\s*)?(?:執行摘要|Executive Summary)[^\n]*\n+([\s\S]*?)(?=\n###?|\Z)",
            r"(?:^|\n)\*\*(?:(?:1\.|一、)\s*)?(?:執行摘要|Executive Summary)\*\*[^\n]*\n+([\s\S]*?)(?=\n\*\*|\n##|\Z)",
            r"(?:^|\n)#\s*(?:(?:1\.|一、)\s*)?(?:執行摘要|Executive Summary)[^\n]*\n+([\s\S]*?)(?=\n#|\Z)",
        ]

        for pat in patterns:
            match = re.search(pat, full_report, re.IGNORECASE)
            if match:
                raw_text = match.group(1).strip()
                cleaned = re.sub(r"^>\s*", "", raw_text, flags=re.MULTILINE).strip()
                if cleaned:
                    return cleaned

        # 備援：若無特定標題，回傳報告前段乾淨文字
        paragraphs = [p.strip() for p in full_report.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        if paragraphs:
            return paragraphs[0][:400]
        return ""

    @classmethod
    def _build_fallback_report(
        cls,
        topic: str,
        dimension_notes: list[dict[str, str]],
        collected_sources: list[SourceDoc],
    ) -> str:
        """當 LLM 合成階段異常或超時時，自動依據已萃取的各維度文獻筆記與反思合成結構化報告。"""
        summary_points = []
        for dn in dimension_notes:
            ref_snippet = dn.get("reflection", "").strip()
            if ref_snippet:
                first_line = ref_snippet.split("\n")[0].lstrip("-•*123456789. ")
                summary_points.append(f"- **{dn.get('title', '')}**：{first_line}")
            elif dn.get("notes"):
                first_note = dn.get("notes", "").split("\n")[0].lstrip("-•*123456789. ")
                summary_points.append(f"- **{dn.get('title', '')}**：{first_note}")

        exec_summary_text = (
            f"本專案針對「**{topic}**」展開深度多輪自主研究，全方位檢索並精讀 {len(collected_sources)} 篇即時文獻。\n\n"
            + "\n".join(summary_points)
        )

        dims_md = []
        for idx, dn in enumerate(dimension_notes):
            dims_md.append(
                f"### 3.{idx+1} {dn.get('title', f'維度 {idx+1}')}\n\n"
                f"**核心探討焦點**：{dn.get('questions', '')}\n\n"
                f"#### 📖 文獻事實與要點萃取\n{dn.get('notes', '無文獻記錄')}\n\n"
                f"#### 🧠 批判性反思與邏輯推演\n{dn.get('reflection', '無反思記錄')}\n"
            )

        sources_md = []
        for idx, s in enumerate(collected_sources):
            sources_md.append(f"{idx+1}. [{s.title}]({s.url}) (擷取內文字數: {s.char_count})")

        report_md = (
            f"# {topic} 深度專案研究報告\n\n"
            f"## 1. 執行摘要 (Executive Summary)\n{exec_summary_text}\n\n"
            f"## 2. 研究背景與核心問題定義 (Background & Core Problem)\n"
            f"本研究旨在系統性剖析「{topic}」之關鍵發展脈絡、運作機制與市場/技術影響。\n\n"
            f"## 3. 多維度深度技術與專題剖析 (In-Depth Thematic Analysis)\n"
            + "\n".join(dims_md)
            + f"\n## 4. 關鍵爭議、多方觀點與局限性評估 (Critique & Nuances)\n"
            f"綜合各維度文獻交叉比對，該領域受外部環境與數據變數影響顯著，建議持續追蹤後續進展。\n\n"
            f"## 5. 參考文獻與資料來源 (References & Sources)\n"
            + "\n".join(sources_md)
        )
        return report_md

    @staticmethod
    def _build_fallback_summary(
        topic: str,
        dimension_notes: list[dict[str, str]],
        collected_sources: list[SourceDoc],
    ) -> str:
        """構建備援執行摘要文字。"""
        summary_points = []
        for dn in dimension_notes:
            ref_snippet = dn.get("reflection", "").strip()
            if ref_snippet:
                first_line = ref_snippet.split("\n")[0].lstrip("-•*123456789. ")
                summary_points.append(f"• **{dn.get('title', '')}**：{first_line}")
        return (
            f"本研究針對「{topic}」完成 {len(dimension_notes)} 輪深度推演，引用 {len(collected_sources)} 篇文獻：\n"
            + "\n".join(summary_points)
        )

    @staticmethod
    def _current_time_str() -> str:
        """取得當前時間字串 (HH:MM:SS)。"""
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    @staticmethod
    async def _notify_progress(
        session: ResearchSession,
        callback: Callable[[ResearchProgress], Any] | None,
    ) -> None:
        """觸發進度回調通知。"""
        if callback is None:
            return
        latest_thought = session.thought_logs[-1] if session.thought_logs else None
        event = ResearchProgress(
            session_id=session.session_id,
            status=session.status,
            progress_pct=session.progress_pct,
            current_step=session.current_step,
            latest_thought=latest_thought,
        )
        try:
            res = callback(event)
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.debug("[deep_search.engine] 進度回調例外: %s", exc)

    @staticmethod
    def _clone_session_with(
        session: ResearchSession,
        **kwargs: Any,
    ) -> ResearchSession:
        """複製並更新 ResearchSession 屬性。"""
        curr = {
            "session_id": session.session_id,
            "topic": session.topic,
            "model": session.model,
            "status": session.status,
            "progress_pct": session.progress_pct,
            "current_step": session.current_step,
            "max_rounds": session.max_rounds,
            "current_round": session.current_round,
            "thought_logs": session.thought_logs,
            "sources": session.sources,
            "final_report": session.final_report,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "error_message": session.error_message,
            "discord_channel_id": session.discord_channel_id,
            "discord_message_id": session.discord_message_id,
        }
        curr.update(kwargs)
        return ResearchSession.from_dict(curr)