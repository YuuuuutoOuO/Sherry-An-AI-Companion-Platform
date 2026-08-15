from enum import Enum
import discord
from discord.ext import commands


class ToolResponseMode(str, Enum):
    """工具對話與回應模式。

    DIRECT_UI:
        工具執行時會直接向 Discord 頻道發送獨立 Embed/圖片/媒體或控制語音（例如 MusicTool, DrawTool, ImageSearchTool）。
        若使用者無額外聊天/心得要求，可進行行為自適應終止（Early Termination）以避免多餘文字重複洗版。
    DATA_ONLY:
        工具執行時主要為查詢純資料、API 數據或文字摘要（例如 StockTool, WeatherTool, SearchTool）。
        必須交由 PRIMARY_MODEL 進行最終的性格發言合成以傳達結果給使用者。
    """
    DIRECT_UI = "DIRECT_UI"
    DATA_ONLY = "DATA_ONLY"


class BaseTool:
    @property
    def intent_name(self) -> str:
        """Tool 唯一識別標籤 (如 'MUSIC', 'STOCK')"""
        raise NotImplementedError

    @property
    def llm_description(self) -> str:
        """交由分類模型判斷時，說明該在什麼時候喚起這個 Tool 的準則"""
        raise NotImplementedError

    @property
    def response_mode(self) -> ToolResponseMode:
        """工具的回應模式，預設為 DATA_ONLY。自帶 Discord 渲染/語音的工具可覆寫為 DIRECT_UI。"""
        return ToolResponseMode.DATA_ONLY

    def can_handle_by_regex(self, text: str) -> bool:
        """最高速匹配層：針對非常明顯的使用情境直接啟動不廢話"""
        return False

    async def execute(
        self,
        bot: commands.Bot,
        message: discord.Message,
        user_query: str,
        vibe_snippet: str,
        full_context: str,
        **kwargs: object,
    ) -> str | None:
        """工具核心：此功能實際上的運作邏輯。

        Args:
            bot: Discord Bot 實例。
            message: 觸發的 Discord 訊息。
            user_query: 使用者的文字輸入。
            vibe_snippet: 頻道近期話題氛圍摘要。
            full_context: 含上下文的完整提示文本。
            **kwargs: 擴充參數，例如 silent=True 使工具靜默執行並回傳狀態字串。

        Returns:
            None（直接回覆 Discord）或 str（silent 模式時回傳執行狀態字串）。
        """
        raise NotImplementedError