"""AI 工具註冊表。

``TOOL_LIST`` 不再是手寫清單，而是 ``CapabilityRegistry`` 的衍生視圖：
工具在建構時註冊 ``CapabilityDescriptor``，所有消費端（router／executor／
admin／SPARK／prompt）共用同一份真實來源。
"""

from __future__ import annotations

from core.contracts import CapabilityDescriptor
from core.registry import CapabilityRegistry
from handlers.social.tool_base import BaseTool
from handlers.social.tool_specs import SPEC_BY_NAME, TOOL_SPECS, resolve_tool_name
from handlers.social.tools.chat_tools import ChatTool
from handlers.social.tools.draw_tool import DrawTool
from handlers.social.tools.image_tool import ImageSearchTool
from handlers.social.tools.music_tool import MusicTool
from handlers.social.tools.schedule_tool import ScheduleTool
from handlers.social.tools.search_tools import AdvancedSearchTool, URLSummaryTool
from handlers.social.tools.stock_tool import StockTool
from handlers.social.tools.sys_inspect_tool import SystemInspectTool

#: canonical intent → 工具工廠。新增工具只需加一筆（搭配 tool_specs.TOOL_SPECS）。
_TOOL_FACTORIES = {
    "SUMMARY": URLSummaryTool,
    "MUSIC": MusicTool,
    "SCHEDULE": ScheduleTool,
    "DRAW": DrawTool,
    "PIXIV": ImageSearchTool,
    "STOCK": StockTool,
    "SYSTEM_INSPECT": SystemInspectTool,
    "SEARCH": AdvancedSearchTool,
    "CHAT": ChatTool,
}


class ToolCatalog:
    """工具實例與能力描述子的唯一持有者。"""

    def __init__(self) -> None:
        self.capabilities = CapabilityRegistry()
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> None:
        """註冊工具；別名由 ``ToolSpec`` 衍生，工具本身不需重複宣告。"""

        name = str(tool.intent_name).upper()
        spec = SPEC_BY_NAME.get(name)
        self.capabilities.register(
            CapabilityDescriptor(
                name=name,
                category="core_builtin",
                description=tool.llm_description,
                aliases=list(spec.aliases) if spec else [],
                is_ai_callable=True,
            )
        )
        self._tools[name] = tool

    def get_tool(self, name: str) -> BaseTool | None:
        """以 canonical 名稱或別名取得工具實例。"""

        canonical = resolve_tool_name(name)
        tool = self._tools.get(canonical)
        if tool is not None:
            return tool
        descriptor = self.capabilities.get_capability(name)
        return self._tools.get(descriptor.name) if descriptor else None

    def get_ai_tools(self) -> list[BaseTool]:
        """``CapabilityRegistry`` 中標記為 AI callable 的工具（保留宣告順序）。"""

        return [
            self._tools[descriptor.name]
            for descriptor in self.capabilities.get_ai_callable_tools()
            if descriptor.name in self._tools
        ]

    def list_descriptors(self) -> list[CapabilityDescriptor]:
        return self.capabilities.list_all()


def _build_catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    for spec in TOOL_SPECS:
        factory = _TOOL_FACTORIES.get(spec.name)
        if factory is None:
            raise RuntimeError(f"ToolSpec 缺少工具工廠：{spec.name}")
        catalog.register_tool(factory())
    return catalog


TOOL_REGISTRY: ToolCatalog = _build_catalog()
TOOL_LIST: list[BaseTool] = TOOL_REGISTRY.get_ai_tools()


def get_tool(name: str) -> BaseTool | None:
    return TOOL_REGISTRY.get_tool(name)


def get_fallback_tool() -> BaseTool:
    # 保持舊語意：fallback 每次回傳獨立實例，避免共用工具狀態。
    return ChatTool()
