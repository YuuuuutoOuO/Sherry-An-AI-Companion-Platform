"""單一工具宣告來源（ToolSpec）。

此模組刻意保持「零工具 import」：dispatch prompt、註冊表與測試皆可安全引用，
避免 ``prompts -> registry -> tools -> prompts`` 的循環依賴。

新增一個 AI 工具時只需：
1. 在 ``TOOL_SPECS`` 加一筆 ``ToolSpec``（名稱／別名）；
2. 在 ``handlers/social/registry.py`` 的 ``_TOOL_FACTORIES`` 掛上實例工廠。

別名表與 prompt 由本模組衍生，不再手動同步多處。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    """AI 工具的單一聲明。"""

    name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)
    label: str = ""


# 順序即 UI／prompt 的呈現順序（SUMMARY 必須在 CHAT 前，避免摘要被聊天攔截）。
TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "SUMMARY",
        ("URL_SUMMARY", "URL_SUMMARIZE", "SUMMARIZE_URL", "SUMMARY_TOOL", "網頁摘要", "影片摘要"),
        label="網址摘要",
    ),
    ToolSpec("MUSIC", ("PLAY_MUSIC", "MUSIC_PLAYER", "PLAY_SONG", "SONG"), label="音樂"),
    ToolSpec(
        "SCHEDULE",
        (
            "SET_ALARM",
            "SET_REMINDER",
            "ALARM",
            "REMINDER",
            "SCHEDULE_REMINDER",
            "SPARK",
            "SPARK_TOOL",
        ),
        label="鬧鐘／提醒",
    ),
    ToolSpec("DRAW", ("DRAW_IMAGE", "GENERATE_IMAGE", "CREATE_IMAGE"), label="畫圖"),
    ToolSpec(
        "PIXIV",
        ("PIXIV_SEARCH", "PIXIV_TOOL", "FIND_IMAGE", "SEARCH_IMAGE", "GET_IMAGE", "GET_PIC"),
        label="Pixiv",
    ),
    ToolSpec(
        "STOCK",
        ("STOCK_ANALYSIS", "STOCK_LOOKUP", "STOCK_QUOTE"),
        label="股票",
    ),
    ToolSpec(
        "SYSTEM_INSPECT",
        ("SYSTEM_STATUS", "SYSTEM_MONITOR", "SYS_INSPECT", "SYS_STATUS", "SYS_MONITOR", "HARDWARE"),
        label="系統檢查",
    ),
    ToolSpec(
        "SEARCH",
        (
            "SEARCH_WEB",
            "WEB_SEARCH",
            "GOOGLE_SEARCH",
            "WEATHER",
            "WEATHER_LOOKUP",
            "WEATHER_REPORT",
        ),
        label="搜尋",
    ),
    ToolSpec("CHAT", ("CHAT_TOOL", "CASUAL_CHAT"), label="閒聊"),
)

SPEC_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

#: 別名（含 canonical 自身）→ canonical intent。
TOOL_ALIASES: dict[str, str] = {
    alias.upper(): spec.name
    for spec in TOOL_SPECS
    for alias in (spec.name, *spec.aliases)
}


def resolve_tool_name(name: str) -> str:
    """把別名正規化為 canonical intent 名稱。"""

    return TOOL_ALIASES.get(str(name).upper(), str(name).upper())


#: 能力提示不列出的工具（CHAT 不是使用者會直接要求的 intent）。
CAPABILITY_HIDDEN: frozenset[str] = frozenset({"CHAT"})


def format_capability_hint(
    allowed: object,
    *,
    exclude: frozenset[str] | set[str] = CAPABILITY_HIDDEN,
) -> str:
    """產生「這一輪能用／不能用」的短提示。

    只輸出中文標籤，不輸出 ``llm_description``；順序固定跟隨 ``TOOL_SPECS``，
    確保 system prompt 在同樣的 policy 下完全穩定、可被前綴快取重用。
    """

    if allowed is None:
        return ""
    allowed_names = {
        resolve_tool_name(str(item))
        for item in allowed  # type: ignore[union-attr]
        if str(item).strip()
    }
    available = [
        spec.label or spec.name
        for spec in TOOL_SPECS
        if spec.name in allowed_names and spec.name not in exclude
    ]
    unavailable = [
        spec.label or spec.name
        for spec in TOOL_SPECS
        if spec.name not in allowed_names and spec.name not in exclude
    ]
    if not available:
        return ""
    lines = ["- 本回合可用：" + "、".join(available) + "。"]
    if unavailable:
        lines.append(
            "- 其他工具（" + "、".join(unavailable) + "）不可用；"
            "不確定就說做不到，不要先答應。"
        )
    return "\n".join(lines)
