"""R16 架構護欄與工具註冊表合一的回歸測試。"""

from __future__ import annotations

import configparser
from pathlib import Path

from core.contracts import CapabilityDescriptor
from handlers.social.dispatch.prompts import _TOOL_ALIASES
from handlers.social.registry import TOOL_LIST, TOOL_REGISTRY, ToolCatalog, get_tool
from handlers.social.tool_base import BaseTool
from handlers.social.tool_specs import TOOL_ALIASES, TOOL_SPECS

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / ".importlinter"
EXEMPTIONS_DOC = ROOT / "Docs" / "architecture" / "import_linter_exemptions.md"


def _parse_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH, encoding="ascii")
    return parser


def test_importlinter_covers_web_ui_and_lower_layers() -> None:
    parser = _parse_config()
    roots = {line.strip() for line in parser["importlinter"]["root_packages"].splitlines() if line.strip()}
    assert {"app", "core", "handlers", "mod", "web_ui"} <= roots

    names = {
        section.split(":")[-1] if ":" in section else section
        for section in parser.sections()
        if section.startswith("importlinter:contract")
    }
    assert len(names) >= 6

    contract4 = next(
        section for section in parser.sections() if section.endswith(":4")
    )
    assert "web_ui" in parser[contract4]["forbidden_modules"]
    assert "handlers" in parser[contract4]["source_modules"]


def test_exemption_budget_and_register_document() -> None:
    parser = _parse_config()
    total = 0
    for section in parser.sections():
        if not section.startswith("importlinter:contract"):
            continue
        raw = parser[section].get("ignore_imports", "")
        total += len([line for line in raw.splitlines() if "->" in line])

    assert total <= 40, f"import-linter 豁免已達 {total} 條，請先清理再新增"
    assert EXEMPTIONS_DOC.exists(), "缺少豁免清單文件"

    doc = EXEMPTIONS_DOC.read_text(encoding="utf-8")
    for name in (
        "Contract 1",
        "Contract 4",
        "Contract 5",
        "Contract 6",
        "2027-03-31",
    ):
        assert name in doc
    assert str(total) in doc, "豁免清單的總數必須與 .importlinter 同步"


def test_tool_list_is_derived_from_capability_registry() -> None:
    descriptors = TOOL_REGISTRY.list_descriptors()
    assert [descriptor.name for descriptor in descriptors] == [
        tool.intent_name for tool in TOOL_LIST
    ]
    assert all(descriptor.is_ai_callable for descriptor in descriptors)
    assert {descriptor.category for descriptor in descriptors} == {"core_builtin"}


def test_tool_aliases_single_source_and_resolve() -> None:
    assert _TOOL_ALIASES is TOOL_ALIASES
    intents = {spec.name for spec in TOOL_SPECS}
    assert set(TOOL_ALIASES.values()) <= intents
    assert get_tool("DRAW_IMAGE") is get_tool("DRAW")
    assert get_tool("WEATHER") is get_tool("SEARCH")
    assert get_tool("SPARK") is get_tool("SCHEDULE")


def test_draw_tool_no_longer_reverse_scans_tool_list() -> None:
    source = (ROOT / "handlers" / "social" / "tools" / "draw_tool.py").read_text(encoding="utf-8")
    assert 'intent_name == "DRAW"' not in source
    assert "next((t for t in TOOL_LIST" not in source


class _DummyTool(BaseTool):
    @property
    def intent_name(self) -> str:
        return "DUMMY"

    @property
    def llm_description(self) -> str:
        return "測試用工具"

    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None


def test_new_tool_requires_zero_core_changes() -> None:
    catalog = ToolCatalog()
    catalog.register_tool(_DummyTool())
    tools = catalog.get_ai_tools()
    assert [tool.intent_name for tool in tools] == ["DUMMY"]
    descriptor = catalog.capabilities.get_capability("DUMMY")
    assert isinstance(descriptor, CapabilityDescriptor)
    assert descriptor.description == "測試用工具"
