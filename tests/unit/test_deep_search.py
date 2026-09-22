"""
tests/unit/test_deep_search.py
==============================
深度研究 (Deep Search) 模組之全覆蓋單元測試。
遵循 AAA (Arrange / Act / Assert) 規範與 AGENTS.md。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from mod.deep_search.domain.interfaces import (
    ILLMClient,
    IScraperClient,
    ISearchClient,
)
from mod.deep_search.domain.models import (
    DeepResearchReport,
    ResearchProgress,
    ResearchSession,
    SearchQuery,
    SearchResultItem,
    SourceDoc,
    ThoughtLogEntry,
)
from mod.deep_search.engine.deep_search_engine import DeepSearchEngine
from mod.deep_search.engine.prompts import (
    FINAL_SYNTHESIS_SYSTEM_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    READING_SYSTEM_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
)
from mod.deep_search.repositories.scraper_client import TrafilaturaScraperClient
from mod.deep_search.repositories.search_client import DuckDuckGoSearchClient
from mod.deep_search.repositories.session_repository import FileSessionRepository


# =============================================================================
# 1. 領域模型與序列化測試 (Domain Models & Serialization)
# =============================================================================

def test_models_to_dict_and_from_dict() -> None:
    """測試所有領域模型物件的雙向序列化正確性。"""
    # Arrange
    query = SearchQuery(query="常溫超導", aspect="材料架構", round_index=1)
    result_item = SearchResultItem(title="超導快訊", url="https://example.com/sc", snippet="測試摘要")
    source_doc = SourceDoc(
        url="https://example.com/paper",
        title="超導論文",
        content="這是一篇深度研究正文",
        snippet="摘要",
        scraped_at="2026-08-18T08:00:00Z",
        char_count=12,
    )
    thought = ThoughtLogEntry(
        timestamp="12:00:00",
        phase="PLANNING",
        title="研究綱領規劃",
        content="已拆解出 3 個維度",
        round_index=0,
    )
    report = DeepResearchReport(
        topic="常溫超導",
        model_used="lms:gemma4",
        executive_summary="執行摘要簡述",
        full_content="# 常溫超導報告\n內文",
        sources=[source_doc],
        total_time_seconds=45.2,
        created_at="2026-08-18 12:00:45",
    )
    session = ResearchSession(
        session_id="rs-test-123",
        topic="常溫超導",
        model="lms:gemma4",
        status="COMPLETED",
        progress_pct=100,
        current_step="完成",
        max_rounds=3,
        current_round=3,
        thought_logs=(thought,),
        sources=(source_doc,),
        final_report=report,
        started_at="2026-08-18 12:00:00",
        finished_at="2026-08-18 12:00:45",
    )

    # Act
    q_dict = query.to_dict()
    r_dict = result_item.to_dict()
    s_dict = source_doc.to_dict()
    t_dict = thought.to_dict()
    rep_dict = report.to_dict()
    sess_dict = session.to_dict()

    rebuilt_source = SourceDoc.from_dict(s_dict)
    rebuilt_thought = ThoughtLogEntry.from_dict(t_dict)
    rebuilt_report = DeepResearchReport.from_dict(rep_dict)
    rebuilt_session = ResearchSession.from_dict(sess_dict)

    # Assert
    assert q_dict["query"] == "常溫超導"
    assert r_dict["title"] == "超導快訊"
    assert rebuilt_source.title == "超導論文"
    assert rebuilt_thought.phase == "PLANNING"
    assert rebuilt_report.topic == "常溫超導"
    assert len(rebuilt_report.sources) == 1
    assert rebuilt_session.session_id == "rs-test-123"
    assert rebuilt_session.status == "COMPLETED"
    assert len(rebuilt_session.thought_logs) == 1
    assert len(rebuilt_session.sources) == 1
    assert rebuilt_session.final_report is not None


# =============================================================================
# 2. 檔案資料倉儲測試 (FileSessionRepository)
# =============================================================================

def test_file_session_repository_crud(tmp_path: Path) -> None:
    """測試 ResearchSession 的 JSON 檔案存取、列表與刪除。"""
    # Arrange
    repo = FileSessionRepository(storage_dir=tmp_path)
    session1 = ResearchSession(
        session_id="rs-test-001",
        topic="量子密碼學",
        model="ollama:llama3.2",
        status="RUNNING",
        progress_pct=25,
        current_step="檢索中",
    )
    session2 = ResearchSession(
        session_id="rs-test-002",
        topic="常溫超導",
        model="lms:gemma4",
        status="COMPLETED",
        progress_pct=100,
        current_step="完成",
    )

    # Act & Assert - Save
    assert repo.save_session(session1) is True
    assert repo.save_session(session2) is True

    # Act & Assert - Load
    loaded1 = repo.load_session("rs-test-001")
    assert loaded1 is not None
    assert loaded1.topic == "量子密碼學"
    assert loaded1.status == "RUNNING"

    # Act & Assert - List
    all_sessions = repo.list_sessions(limit=10)
    assert len(all_sessions) == 2
    session_ids = [s.session_id for s in all_sessions]
    assert "rs-test-001" in session_ids
    assert "rs-test-002" in session_ids

    # Act & Assert - Delete
    assert repo.delete_session("rs-test-001") is True
    assert repo.load_session("rs-test-001") is None
    assert len(repo.list_sessions()) == 1


# =============================================================================
# 3. 提示詞零人格檢驗 (Zero-Persona Prompts Verification)
# =============================================================================

def test_prompts_strictly_zero_persona() -> None:
    """嚴格檢驗所有提示詞絕不包含任何雪莉角色設定或動漫口吻。"""
    # Arrange
    forbidden_terms = [
        "雪莉",
        "Sherry",
        "名偵探",
        "15歲",
        "高中生",
        "傲嬌",
        "本偵探",
        "橘雪莉",
        "真相永遠只有一個",
    ]

    all_prompts = [
        PLANNING_SYSTEM_PROMPT,
        READING_SYSTEM_PROMPT,
        REFLECTION_SYSTEM_PROMPT,
        FINAL_SYNTHESIS_SYSTEM_PROMPT,
    ]

    # Assert
    for p in all_prompts:
        for term in forbidden_terms:
            assert term not in p, f"提示詞違規包含人格字眼: '{term}'"
            assert term.lower() not in p.lower(), f"提示詞違規包含人格字眼: '{term}'"


# =============================================================================
# 4. 檢索解析器測試 (DuckDuckGo Search Client)
# =============================================================================

def test_duckduckgo_search_html_parsing() -> None:
    """測試 DuckDuckGo HTML 解析與 uddg 轉址還原。"""
    # Arrange
    client = DuckDuckGoSearchClient()
    mock_html = """
    <html>
        <body>
            <div class="result">
                <a class="result__a" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.com%2Fquantum">量子密碼防禦</a>
                <a class="result__snippet" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.com%2Fquantum">
                    這是<b>量子密碼學</b>的最新研究成果。
                </a>
            </div>
            <div class="result">
                <a class="result__a" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.org%2Frsa">RSA 安全性評估</a>
                <a class="result__snippet" href="/l/?kh=-1&uddg=https%3A%2F%2Fexample.org%2Frsa">
                    評估 Shor 演算法對傳統非對稱密碼之威脅。
                </a>
            </div>
        </body>
    </html>
    """

    # Act
    items = client._parse_html(mock_html, max_results=4)

    # Assert
    assert len(items) == 2
    assert items[0].title == "量子密碼防禦"
    assert items[0].url == "https://example.com/quantum"
    assert "量子密碼學" in items[0].snippet
    assert items[1].url == "https://example.org/rsa"


# =============================================================================
# 5. 網頁擷取解析測試 (Trafilatura Scraper Client)
# =============================================================================

def test_scraper_client_fallback_and_truncation() -> None:
    """測試網頁擷取客戶端之字數截斷與摘要回退機制。"""
    async def _run() -> None:
        client = TrafilaturaScraperClient(max_chars=50, timeout_seconds=5)

        # Act - 空網址
        assert await client.scrape("") is None

        # Act - 略過檔案格式
        assert await client.scrape("https://example.com/file.pdf") is None

        # Act - 模擬同步抓取失敗但具備 snippet 回退
        with patch("urllib.request.urlopen", side_effect=Exception("網路連線超時")):
            doc = await client.scrape("https://example.com/test", snippet="備援摘要內容")
            # Assert
            assert doc is not None
            assert "備援摘要內容" in doc.content
            assert doc.url == "https://example.com/test"

    asyncio.run(_run())


# =============================================================================
# 6. 多輪深度研究引擎端到端推演測試 (DeepSearchEngine E2E Loop)
# =============================================================================

def test_deep_search_engine_full_cycle(tmp_path: Path) -> None:
    """測試深度研究引擎的完整規劃、多輪搜尋、精讀、反思與報告生成流程。"""
    async def _run() -> None:
        mock_search = AsyncMock(spec=ISearchClient)
        mock_search.search.side_effect = lambda query, max_results=4: [
            SearchResultItem(title=f"文獻: {query}", url=f"https://example.com/{abs(hash(query)) % 100000}", snippet="超導數據細節")
        ]

        mock_scraper = AsyncMock(spec=IScraperClient)
        mock_scraper.scrape.side_effect = lambda url, snippet="": SourceDoc(
            url=url,
            title="測試標題",
            content=f"這是來自 {url} 的詳細論文研究內文...",
            snippet=snippet,
            scraped_at="2026-08-18T00:00:00Z",
            char_count=50,
        )

        planning_json_response = """
        ```json
        [
          {
            "dimension_title": "超導機制與晶體結構",
            "key_questions": "常溫超導之電子配對機制為何？",
            "search_queries": ["常溫超導 電子配對", "LK-99 重現實驗"]
          },
          {
            "dimension_title": "實驗重現與爭議評估",
            "key_questions": "各大實驗室的獨立驗證結果如何？",
            "search_queries": ["常溫超導 實驗驗證", "超導 磁化率測量"]
          }
        ]
        ```
        """

        mock_llm = AsyncMock(spec=ILLMClient)
        mock_llm.generate.side_effect = [
            planning_json_response,                         # Phase 1: Planning
            "【核心論點】：晶格畸變引發超導。\n【數據】：Tc > 298K。",  # Round 1: Reading
            "批判性反思：實驗數據重複性仍偏低，需更多中子繞射驗證。",   # Round 1: Reflection
            "【核心論點】：多數實驗室判定為抗磁性而非超導。",           # Round 2: Reading
            "批判性反思：主流學界傾向否認常溫超導特性。",             # Round 2: Reflection
            """# 常溫超導深度專案研究報告\n\n## 1. 執行摘要\n本研究全方位剖析常溫超導之爭議與最新實驗現況，提供學術界與產業界全面的決策洞見。\n\n## 2. 研究背景\n...""",  # Synthesis
        ]

        repo = FileSessionRepository(storage_dir=tmp_path)
        engine = DeepSearchEngine(
            search_client=mock_search,
            scraper_client=mock_scraper,
            llm_client=mock_llm,
            session_repository=repo,
        )

        progress_events: list[ResearchProgress] = []

        def _progress_cb(evt: ResearchProgress) -> None:
            progress_events.append(evt)

        # Act
        session = await engine.run_research(
            topic="常溫超導體最新研究進展",
            model="lms:gemma4",
            max_rounds=2,
            session_id="rs-superconductor-test",
            progress_callback=_progress_cb,
        )

        # Assert
        assert session.status == "COMPLETED"
        assert session.progress_pct == 100
        assert session.topic == "常溫超導體最新研究進展"
        assert session.final_report is not None
        assert "常溫超導深度專案研究報告" in session.final_report.full_content
        assert "本研究全方位剖析常溫超導" in session.final_report.executive_summary

        # 檢查思考軌跡
        phases = [t.phase for t in session.thought_logs]
        assert "PLANNING" in phases
        assert "SEARCHING" in phases
        assert "READING" in phases
        assert "REFLECTING" in phases
        assert "SYNTHESIZING" in phases

        # 檢查文獻來源
        assert len(session.sources) > 0

        # 檢查進度回調事件
        assert len(progress_events) >= 5
        assert progress_events[-1].status == "COMPLETED"
        assert progress_events[-1].progress_pct == 100

        # 檢查是否確實寫入倉儲
        saved_session = repo.load_session("rs-superconductor-test")
        assert saved_session is not None
        assert saved_session.status == "COMPLETED"

    asyncio.run(_run())


# =============================================================================
# 7. 報告摘要萃取多樣式測試 (Summary Extraction Variations)
# =============================================================================

def test_extract_summary_variations() -> None:
    """測試 _extract_summary 支援多種 Markdown 標題與引用區塊格式。"""
    # Arrange & Act - ## 1. 執行摘要
    report1 = "# 主題\n\n## 1. 執行摘要\n這是一份關於 BTC 走勢的推演報告。\n\n## 2. 背景\n內文..."
    assert "這是一份關於 BTC 走勢的推演報告。" in DeepSearchEngine._extract_summary(report1)

    # Arrange & Act - ## 執行摘要 (無編號)
    report2 = "# 主題\n\n## 執行摘要\n> 這是引用格式的執行摘要內容。\n\n## 2. 分析\n..."
    assert "這是引用格式的執行摘要內容。" in DeepSearchEngine._extract_summary(report2)

    # Arrange & Act - ### 執行摘要
    report3 = "# 主題\n\n### 執行摘要\n### 3. 子項目\n三級標題摘要。\n\n## 2. 結論"
    assert "三級標題摘要。" in DeepSearchEngine._extract_summary(report3)

    # Arrange & Act - **執行摘要**
    report4 = "# 主題\n\n**執行摘要**\n加粗格式摘要段落。\n\n## 下一節"
    assert "加粗格式摘要段落。" in DeepSearchEngine._extract_summary(report4)

    # Arrange & Act - Executive Summary (英文)
    report5 = "# Topic\n\n## Executive Summary\nEnglish summary text.\n\n## Section 2"
    assert "English summary text." in DeepSearchEngine._extract_summary(report5)


# =============================================================================
# 8. 合成失敗自動備援報告構建測試 (Fallback Report Generation)
# =============================================================================

def test_deep_search_fallback_report_generation(tmp_path: Path) -> None:
    """測試當 LLM 合成階段失敗返回空字串時，自動由多輪筆記構建結構化備援報告。"""
    async def _run() -> None:
        mock_search = AsyncMock(spec=ISearchClient)
        mock_search.search.side_effect = lambda query, max_results=4: [
            SearchResultItem(title=f"Source: {query}", url="https://example.com/source", snippet="Snippet")
        ]

        mock_scraper = AsyncMock(spec=IScraperClient)
        mock_scraper.scrape.side_effect = lambda url, snippet="": SourceDoc(
            url=url,
            title="BTC M2關聯",
            content="M2貨幣供應量與BTC走勢高度正相關...",
            snippet=snippet,
            scraped_at="2026-08-24T00:00:00Z",
            char_count=35,
        )

        mock_llm = AsyncMock(spec=ILLMClient)
        # Phase 1: Planning, Round 1: Reading, Round 1: Reflection, Round 2: Reading, Round 2: Reflection, Synthesis (returns empty "")
        mock_llm.generate.side_effect = [
            """[{"dimension_title": "總體經濟 M2 關聯", "key_questions": "M2 與 BTC 關聯為何？", "search_queries": ["BTC M2"]}]""",
            "M2 擴張期推動流動性溢出至加密資產。",
            "批判性反思：需考量降息時滯效應與監管變數。",
            "",  # Synthesis fails/empty
            "",  # Retry fails/empty
        ]

        repo = FileSessionRepository(storage_dir=tmp_path)
        engine = DeepSearchEngine(
            search_client=mock_search,
            scraper_client=mock_scraper,
            llm_client=mock_llm,
            session_repository=repo,
        )

        # Act
        session = await engine.run_research(
            topic="BTC走勢",
            model="freetoken:Gemma-4",
            max_rounds=1,
            session_id="rs-btc-fallback",
        )

        # Assert
        assert session.status == "COMPLETED"
        assert session.final_report is not None
        assert len(session.final_report.full_content) > 100
        assert "BTC走勢 深度專案研究報告" in session.final_report.full_content
        assert len(session.final_report.executive_summary) > 0
        assert "總體經濟 M2 關聯" in session.final_report.executive_summary

    asyncio.run(_run())


# =============================================================================
# 9. Discord Cog 初始化與前綴指令輪數解析測試
# =============================================================================

def test_deep_search_cog_prefix_round_parsing() -> None:
    """測試前綴指令中對輪數參數 (!deep_search 2 BTC 或 !deep_search BTC -r 2) 的解析。"""
    # Arrange
    import re
    queries = [
        ("2 BTC走勢", 2, "BTC走勢"),
        ("4 量子計算最新突破", 4, "量子計算最新突破"),
        ("比特幣行情 -r 3", 3, "比特幣行情"),
        ("常溫超導體", 4, "常溫超導體"),  # 預設 4
    ]

    for raw, expected_rounds, expected_topic in queries:
        actual_rounds = 4
        target_topic = raw
        m_r_prefix = re.match(r"^(\d+)\s+(.+)$", raw)
        m_r_suffix = re.search(r"\s+-r\s*(\d+)$", raw)
        if m_r_prefix:
            r_val = int(m_r_prefix.group(1))
            if 2 <= r_val <= 8:
                actual_rounds = r_val
                target_topic = m_r_prefix.group(2).strip()
        elif m_r_suffix:
            r_val = int(m_r_suffix.group(1))
            if 2 <= r_val <= 8:
                actual_rounds = r_val
                target_topic = target_topic[:m_r_suffix.start()].strip()

        # Assert
        assert actual_rounds == expected_rounds
        assert target_topic == expected_topic


def test_deep_search_cog_setup() -> None:
    """測試 DeepSearchCog 在 Bot 上的初始化與 setup 掛載。"""
    async def _run() -> None:
        from mod.deep_search.cog import DeepSearchCog, setup

        mock_bot = MagicMock()
        mock_bot.add_cog = AsyncMock()

        # Act
        await setup(mock_bot)

        # Assert
        assert mock_bot.add_cog.called
        cog_instance = mock_bot.add_cog.call_args[0][0]
        assert isinstance(cog_instance, DeepSearchCog)
        assert hasattr(mock_bot, "_deep_search_cog")

    asyncio.run(_run())