"""
tests/test_soul_persona.py
===========================
測試 SOUL.md 人格動態載入、頻道映射與 Fallback 備援機制。
"""
import os
import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DISCORD_BOT_TOKEN", "dummy_token_for_test")

import settings

# 動態載入 handlers/social/persona.py
spec = importlib.util.spec_from_file_location("persona", str(Path(__file__).resolve().parent.parent / "handlers" / "social" / "persona.py"))
assert spec is not None and spec.loader is not None
persona_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persona_mod)

build_dynamic_persona = persona_mod.build_dynamic_persona
_load_soul_persona = persona_mod._load_soul_persona


def test_default_soul_load():
    """測試預設 SOUL 檔案載入。"""
    head_persona, _tail = build_dynamic_persona("你好", "CHAT")
    assert "名偵探" in head_persona or "15" in head_persona
    assert "橘雪莉" in head_persona
    assert "天然黑" in head_persona or "櫻羽艾瑪" in head_persona
    assert "實幹列舉鐵則" in head_persona


def test_channel_soul_map():
    """測試頻道映照切換至 serious 人設。"""
    settings.CHANNEL_SOUL_MAP[99999] = "sherry_serious.md"
    head_persona, _tail = build_dynamic_persona("這問題怎麼解", "CHAT", channel_id=99999)
    assert "嚴肅特派偵探模式" in head_persona or "專業問題" in head_persona


def test_fallback_on_missing_file():
    """測試當指定 soul 檔案不存在時回退至 CORE_PERSONA。"""
    original_default = settings.DEFAULT_SOUL_PATH
    try:
        settings.DEFAULT_SOUL_PATH = settings.BASE_DIR / "soul" / "non_existent_soul.md"
        loaded = _load_soul_persona()
        assert loaded == settings.CORE_PERSONA
    finally:
        settings.DEFAULT_SOUL_PATH = original_default


def test_listing_challenge_lore():
    """測試挑釁/舉證/列舉名單情境下，反空話指令進入 tail_lore 高優先區域。"""
    head_persona, tail_lore = build_dynamic_persona("愛我的人有誰？說出來讓我知道啊", "CHAT")
    assert "名偵探實證與反空話強烈指令" in tail_lore
    assert "絕對不可說空話或預告" in tail_lore


if __name__ == "__main__":
    test_default_soul_load()
    print("test_default_soul_load passed!")
    test_channel_soul_map()
    print("test_channel_soul_map passed!")
    test_fallback_on_missing_file()
    print("test_fallback_on_missing_file passed!")
    test_listing_challenge_lore()
    print("test_listing_challenge_lore passed!")
    print("\n[OK] ALL SOUL PERSONA TESTS PASSED SUCCESSFULLY!")

