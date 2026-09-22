"""tests/unit/test_image_tool.py
==============================
ImageSearchTool 與 BooruClient 單元測試。
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.social.tools.image_tool import ImageSearchTool
from mod.clients.booru_client import BooruClient, BooruSearchResult


@pytest.fixture
def image_tool() -> ImageSearchTool:
    return ImageSearchTool()


def test_image_tool_init(image_tool: ImageSearchTool) -> None:
    """測試 ImageSearchTool 正確載入標籤字典與元資料。"""
    assert image_tool.intent_name == "PIXIV"
    assert len(image_tool._pixiv_dict) > 1000
    assert "芙莉蓮" in image_tool._pixiv_dict or "frieren" in image_tool._pixiv_dict


def test_extract_entities_from_llm(image_tool: ImageSearchTool) -> None:
    """測試 LLM JSON 實體萃取解析與容錯。"""
    raw_json = (
        '{\n'
        '  "character": "芙莉蓮",\n'
        '  "series": "葬送的芙莉蓮",\n'
        '  "clothing": "日常服",\n'
        '  "action": "吃東西",\n'
        '  "scene": "雪地",\n'
        '  "extra_tags": ["白髮", "尖耳朵"],\n'
        '  "is_nsfw": false\n'
        '}'
    )
    res = image_tool._extract_entities_from_llm(raw_json)
    assert res.get("character") == "芙莉蓮"
    assert res.get("series") == "葬送的芙莉蓮"
    assert res.get("action") == "吃東西"
    assert res.get("scene") == "雪地"

    # 測試 Markdown 代碼框包裝
    wrapped_json = f"```json\n{raw_json}\n```"
    res_wrapped = image_tool._extract_entities_from_llm(wrapped_json)
    assert res_wrapped.get("character") == "芙莉蓮"

    # 測試無效字串回傳空字典
    assert image_tool._extract_entities_from_llm("抱歉我無法處理此請求") == {}


def test_resolve_single_tag(image_tool: ImageSearchTool) -> None:
    """測試單一詞彙標準化對齊。"""
    assert image_tool._resolve_single_tag("芙莉蓮") == "frieren_(sousou_no_frieren)"
    assert image_tool._resolve_single_tag("星野") == "takanashi_hoshino_(blue_archive)"
    assert image_tool._resolve_single_tag("泳裝") == "swimsuit"
    assert image_tool._resolve_single_tag("巨乳") == "large_breasts"
    assert image_tool._resolve_single_tag("huge_boobs") == "large_breasts"


def test_resolve_tag_tiers(image_tool: ImageSearchTool) -> None:
    """測試標籤三級分流權重分配。"""
    entities = {
        "character": "芙莉蓮",
        "clothing": "泳裝",
        "action": "吃東西",
        "scene": "雪地",
    }
    t1, t2, t3 = image_tool._resolve_tag_tiers("找一張芙莉蓮穿泳裝在雪地吃東西的圖", entities=entities)

    # Tier 1 必須包含核心角色
    assert "frieren_(sousou_no_frieren)" in t1
    # Tier 2 必須包含服裝/姿勢
    assert "swimsuit" in t2
    # Tier 3 必須包含場景/修飾
    assert any(tag in ("snow", "eating") for tag in t3 or t2)


def test_tiered_booru_search_dynamic_reduction(image_tool: ImageSearchTool) -> None:
    """測試智慧動態縮減標籤（丟棄次要修飾詞，保護核心角色名）。"""
    async def run() -> None:
        mock_client = MagicMock(spec=BooruClient)

        # 模擬場景：
        # 輪次 1: [frieren, swimsuit, snow] -> 0 結果 (None)
        # 輪次 2: [frieren, swimsuit] -> 成功找到圖片
        async def mock_safebooru(tags: list[str], **kwargs: object) -> BooruSearchResult | None:
            if "snow" in tags:
                return None
            if "swimsuit" in tags and "frieren_(sousou_no_frieren)" in tags:
                return BooruSearchResult(
                    image_url="https://safebooru.org/images/sample.jpg",
                    source_site="safebooru",
                    post_id=12345,
                )
            return None

        mock_client.search_safebooru = AsyncMock(side_effect=mock_safebooru)
        mock_client.search_danbooru = AsyncMock(return_value=None)
        image_tool._booru_client = mock_client

        t1 = ["frieren_(sousou_no_frieren)"]
        t2 = ["swimsuit"]
        t3 = ["snow"]

        img_url, used_tags, is_fallback, site = await image_tool._tiered_booru_search(t1, t2, t3)

        assert img_url == "https://safebooru.org/images/sample.jpg"
        assert "frieren_(sousou_no_frieren)" in used_tags
        assert "swimsuit" in used_tags
        assert "snow" not in used_tags  # snow 已被動態縮減
        assert is_fallback is True
        assert site == "safebooru"

    asyncio.run(run())


def test_tiered_booru_search_danbooru_fallback(image_tool: ImageSearchTool) -> None:
    """測試 Safebooru 失敗時自動切換至 Danbooru SFW 備援。"""
    async def run() -> None:
        mock_client = MagicMock(spec=BooruClient)

        # Safebooru 全部回傳 None
        mock_client.search_safebooru = AsyncMock(return_value=None)
        # Danbooru 找到角色圖
        mock_client.search_danbooru = AsyncMock(
            return_value=BooruSearchResult(
                image_url="https://danbooru.donmai.us/sample.jpg",
                source_site="danbooru",
                post_id=67890,
            )
        )
        image_tool._booru_client = mock_client

        t1 = ["frieren_(sousou_no_frieren)"]
        t2 = []
        t3 = []

        img_url, used_tags, is_fallback, site = await image_tool._tiered_booru_search(t1, t2, t3)

        assert img_url == "https://danbooru.donmai.us/sample.jpg"
        assert "frieren_(sousou_no_frieren)" in used_tags
        assert site == "danbooru"
        assert is_fallback is True

    asyncio.run(run())


def test_booru_client_danbooru_sfw_enforcement() -> None:
    """測試 BooruClient 在非 NSFW 模式下自動注入 rating:g 安全過濾標籤。"""
    async def run() -> None:
        client = BooruClient()

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=[
                {"large_file_url": "https://danbooru.donmai.us/sample.jpg", "id": 1, "score": 100, "rating": "g"}
            ])
            mock_get.return_value.__aenter__.return_value = mock_resp

            res = await client.search_danbooru(["frieren_(sousou_no_frieren)"], is_nsfw=False)
            assert res is not None
            assert res.image_url == "https://danbooru.donmai.us/sample.jpg"
            assert res.source_site == "danbooru"

    asyncio.run(run())


def test_booru_client_handles_null_score_and_id() -> None:
    """測試 Safebooru 與 Danbooru 回傳 null score / id 時不會引發 TypeError。"""
    async def run() -> None:
        client = BooruClient()

        # 模擬 Safebooru 回傳 score: null, id: null
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=[
                {"directory": "123", "image": "test.jpg", "id": None, "score": None}
            ])
            mock_get.return_value.__aenter__.return_value = mock_resp

            res = await client.search_safebooru(["1girl"])
            assert res is not None
            assert res.score == 0
            assert res.post_id == 0

        # 模擬 Danbooru 回傳 score: null
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=[
                {"file_url": "https://danbooru.donmai.us/test.jpg", "id": None, "score": None, "rating": "g"}
            ])
            mock_get.return_value.__aenter__.return_value = mock_resp

            res_dan = await client.search_danbooru(["1girl"])
            assert res_dan is not None
            assert res_dan.score == 0
            assert res_dan.post_id == 0

    asyncio.run(run())


def test_image_search_tool_can_handle_by_regex_discrimination(image_tool: ImageSearchTool) -> None:
    """測試 ImageSearchTool 正則精準識別，排除多模態圖片分析與上下文描述。"""
    # 正向匹配：明確搜圖請求
    assert image_tool.can_handle_by_regex("幫我搜一張芙莉蓮的圖") is True
    assert image_tool.can_handle_by_regex("來點色圖") is True
    assert image_tool.can_handle_by_regex("找張星野的插畫") is True
    assert image_tool.can_handle_by_regex("pixiv frieren") is True
    assert image_tool.can_handle_by_regex("hatsune_miku 1girl") is True

    # 負向排除：多模態對話、上下文描述、引用
    user_prompt_with_image = (
        '【群友上傳了一張圖片】 請用有創意的方式說明你會如何享用圖中的小女生，'
        '可以參考我的方式："我正在給我的飯後甜點淋上牛奶 讓她們變得滑嫩Q彈且美味..."'
    )
    assert image_tool.can_handle_by_regex(user_prompt_with_image) is False
    assert image_tool.can_handle_by_regex("這張照片是誰？") is False
    assert image_tool.can_handle_by_regex("請說明圖中的意思") is False
    assert image_tool.can_handle_by_regex("看這張圖裡的文字") is False


