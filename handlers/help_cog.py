"""
handlers/help_cog.py
====================
雪莉 (Shirley) 全功能指南與互動式指令手冊。
提供 10 大分類的下拉選單說明與範例，完整涵蓋所有斜線子指令與自然語言用法。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import settings


class HelpSelect(discord.ui.Select):
    """互動式說明手冊分類選單。"""

    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="聊天與 AI 互動 (Chat)", description="動態人設、好感度與自然對話", emoji="💬"),
            discord.SelectOption(label="音樂播放系統 (Music)", description="/play 指令與自然語言點歌", emoji="🎵"),
            discord.SelectOption(label="語音交談系統 (Voice)", description="/join_voice 語音交談與神經網路", emoji="🎤"),
            discord.SelectOption(label="圖片生成與搜尋 (Image)", description="AI 繪圖與二次元圖庫搜尋", emoji="🎨"),
            discord.SelectOption(label="聯網搜尋與分析 (Web)", description="網址重點總結、即時新聞與氣象", emoji="🔍"),
            discord.SelectOption(label="排程與定時提醒 (Schedule)", description="自然語言鬧鐘、倒數與定時任務", emoji="⏰"),
            discord.SelectOption(label="股票市場與卡價 (Market & Card)", description="/market 股票與 /card PTCG 卡價", emoji="📈"),
            discord.SelectOption(label="D&D 跑團冒險 (D&D World)", description="/dnd 創角、擲骰與地下城主冒險", emoji="🐉"),
            discord.SelectOption(label="多媒體與情報 (Media & Feed)", description="/video 轉 GIF 與 AI 社群情報哨兵", emoji="🎬"),
            discord.SelectOption(label="記憶、管理與除錯 (Admin & Debug)", description="/memory, /vibe, /admin, /debug, /stats", emoji="⚙️"),
        ]
        super().__init__(placeholder="請選擇你想了解的功能分類...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        category = self.values[0]
        embed = discord.Embed(color=0xFFB6C1)
        if interaction.client.user and interaction.client.user.display_avatar:
            embed.set_author(name="雪莉 (Shirley) - 全功能指南手冊", icon_url=interaction.client.user.display_avatar.url)

        if category == "聊天與 AI 互動 (Chat)":
            embed.title = "💬 聊天與 AI 互動"
            embed.description = (
                "直接在頻道中 **`@雪莉`** 或是 **回覆她的訊息**，即可與她自由聊天！\n"
                "她具備動態人設靈魂、好感度機制、頻道氛圍感知與跨頻長期記憶能力。"
            )
            embed.add_field(
                name="🗣️ 自然對話範例",
                value=(
                    "• `@雪莉 妳今天心情好嗎？`\n"
                    "• `@雪莉 幫我總結一下上面大家在聊甚麼`\n"
                    "• `@雪莉 名偵探，這案子妳怎麼看？`"
                ),
                inline=False,
            )
            embed.add_field(
                name="✨ 特色機制",
                value="• 支援訊息反應按鈕（`🔄 重新生成`、`📌 釘選記憶`、`🗑️ 刪除訊息`）\n• 具備主動插話機制（當群組話題有趣時會主動加入聊兩句）",
                inline=False,
            )

        elif category == "音樂播放系統 (Music)":
            embed.title = "🎵 音樂播放系統"
            embed.description = "支援 YouTube 串流音樂播放。可直接透過 `/play` 斜線指令或自然語言對話操作！"
            embed.add_field(
                name="🎮 斜線指令：/play",
                value=(
                    "• `/play play_type:add context:<YouTube網址>` — 加入網址到播放清單\n"
                    "• `/play play_type:search context:<歌名>` — 搜尋歌曲並加入\n"
                    "• `/play play_type:insert context:<歌名/網址>` — 優先插播到最前\n"
                    "• `/play play_type:list` — 查看目前播放清單 (含翻頁按鈕)\n"
                    "• `/play play_type:skip` — 跳過當前歌曲\n"
                    "• `/play play_type:jump context:<編號>` — 優先播放指定編號的歌\n"
                    "• `/play play_type:remove context:<編號>` — 刪除指定編號歌曲 (支援 `3`、`1~5`)\n"
                    "• `/play play_type:clear` — 清空整個播放清單"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗣️ 自然語言點歌範例",
                value="• `@雪莉 幫我播 Yoasobi 的 Idol`\n• `@雪莉 這首好難聽，幫我卡歌 / 跳過`\n• 貼上 YouTube 連結直接自動播放",
                inline=False,
            )

        elif category == "語音交談系統 (Voice)":
            embed.title = "🎤 語音交談系統"
            embed.description = "讓雪莉加入語音頻道，用語音跟名偵探直接對話！（搭載 Nemotron-3.5 STT 語音辨識 + XTTS v2 擬真語音合成）"
            embed.add_field(
                name="🎮 語音指令",
                value=(
                    "• `/join_voice` — 讓雪莉加入你所在的語音頻道並啟動神經網路\n"
                    "• `/leave_voice` — 讓雪莉離開語音頻道並釋放 VRAM 顯存"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗣️ 使用方式",
                value="加入後對著麥克風說話，呼叫喚醒詞 **「雪莉」**（例如：*「雪莉，今天台北天氣如何？」*），雪莉聽懂後會直接用語音回覆你！",
                inline=False,
            )

        elif category == "圖片生成與搜尋 (Image)":
            embed.title = "🎨 畫布繪圖、AI 生成與二次元搜尋"
            embed.description = "支援專屬數位畫布多筆刷繪圖 (`/draw`)、雲端/本地 AI 繪圖與 Safebooru/Danbooru 二次元圖庫搜尋。"
            embed.add_field(
                name="🖌️ 畫布繪圖指令：/draw",
                value=(
                    "• `/draw content:<想畫什麼> type:<風格>`\n"
                    "  * 支援風格：`水墨畫`、`水彩`、`簡筆塗鴉`、`像素藝術`、`賽博霓虹`、`粉筆蠟筆`、`厚塗油畫`、`隨機自由`\n"
                    "  * 繪製完成自動存檔至 `./data/painting/*.jpg`，並可在 Web 控制台畫廊檢視！"
                ),
                inline=False,
            )
            embed.add_field(
                name="🎨 AI 繪圖範例 (自然語言)",
                value=(
                    "• `@雪莉 幫我畫一張白髮紅眼的精靈少女`\n"
                    "• `@雪莉 畫一隻可愛的貓咪在星空下散步`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🖼️ 二次元圖片搜尋 (自然語言)",
                value=(
                    "• `@雪莉 找一些初音未來的圖給我看`\n"
                    "• `@雪莉 我想看蔚藍檔案的色圖 / 插畫`"
                ),
                inline=False,
            )

        elif category == "聯網搜尋與分析 (Web)":
            embed.title = "🔍 聯網搜尋與網頁分析"
            embed.description = "雪莉在對話中具備全自動網頁抓取摘要、即時新聞搜尋與天氣氣象預報功能。"
            embed.add_field(
                name="🌐 網頁閱讀與即時搜尋範例",
                value=(
                    "• 貼上任何文章網址（如 `https://...`），雪莉會自動抓取全文並做 AI 條列摘要\n"
                    "• `@雪莉 目前最新的 AI 新聞有哪些？`\n"
                    "• `@雪莉 明天台北會下雨嗎？`\n"
                    "• `@雪莉 台積電最近有甚麼大消息？`"
                ),
                inline=False,
            )

        elif category == "排程與定時提醒 (Schedule)":
            embed.title = "⏰ 排程與定時提醒"
            embed.description = "使用自然對話設定定時任務、鬧鐘或倒數提醒，時間到達時雪莉會主動在頻道標註通知。"
            embed.add_field(
                name="⏰ 自然語言排程範例",
                value=(
                    "• `@雪莉 10分鐘後提醒我關瓦斯`\n"
                    "• `@雪莉 明天早上 8 點叫我起床`\n"
                    "• `@雪莉 每天下午 3 點提醒大家喝水`"
                ),
                inline=False,
            )

        elif category == "股票市場與卡價 (Market & Card)":
            embed.title = "📈 股票市場與 PTCG 卡價追蹤"
            embed.description = "提供美股/台股技術指標晨報、即時 K 線分析，以及日本 SNKRDUNK PTCG 卡牌行情追蹤。"
            embed.add_field(
                name="📈 /market 股票追蹤 (Choice 選單)",
                value=(
                    "• `/market action:raw` — 手動生成最新技術指標晨報\n"
                    "• `/market action:add [list_type: ai/raw/etf] [ticker]` — 加入股票追蹤清單\n"
                    "• `/market action:remove [list_type: ai/raw/etf] [ticker]` — 移除股票追蹤\n"
                    "• `/market action:list` — 查看目前股票追蹤清單"
                ),
                inline=False,
            )
            embed.add_field(
                name="🃏 /card PTCG 卡價 (Choice 選單)",
                value=(
                    "• `/card action:add [name] [url]` — 新增 SNKRDUNK 卡牌價格追蹤\n"
                    "• `/card action:remove [name]` — 停止追蹤卡牌\n"
                    "• `/card action:list` — 查看追蹤卡片與等級報價 (A / PSA10 / BGS9.5)\n"
                    "• `/card action:update` — 手動啟動爬蟲更新最新卡價"
                ),
                inline=False,
            )
            embed.add_field(
                name="💡 前綴與自然語言快速查詢",
                value="• `!stock_status` — 查詢雪莉的美股虛擬量化帳戶損益\n• `@雪莉 幫我看 NVDA 走勢圖` — 即時繪製 K 線走勢圖",
                inline=False,
            )

        elif category == "D&D 跑團冒險 (D&D World)":
            embed.title = "🐉 D&D 跑團冒險世界"
            embed.description = "在 Discord 體驗完整的桌上角色扮演冒險！由雪莉擔任地下城主 (DM) 進行即時敘事推演。"
            embed.add_field(
                name="🎲 /dnd 冒險子指令群組",
                value=(
                    "• `/dnd join <race> <class> <name>` — 創建你的冒險者角色卡 (選種族與職業)\n"
                    "• `/dnd session <action: start/stop>` — 開啟/關閉頻道的自動冒險打字模式\n"
                    "• `/dnd action <action_text>` — 提出冒險動作敘事\n"
                    "• `/dnd roll [dice: d20] [modifier] [mode: normal/advantage/disadvantage]` — 擲骰判定\n"
                    "• `/dnd status` — 查看角色卡 HP、防禦、等級與能力值\n"
                    "• `/dnd inventory` — 查看背包裝備與金錢\n"
                    "• `/dnd world` — 查看艾瑟蘭大陸世界時間與勢力狀態"
                ),
                inline=False,
            )

        elif category == "多媒體與情報 (Media & Feed)":
            embed.title = "🎬 多媒體與情報哨兵"
            embed.description = "YouTube 影片片段剪輯與社群情報自動巡邏推播。"
            embed.add_field(
                name="🎬 影片剪輯",
                value="• `/video <url> <start_time> <end_time>` — 將 YouTube 影片指定時間片段剪輯為高品質 GIF 動圖",
                inline=False,
            )
            embed.add_field(
                name="📺 直播螢幕播放 (Livestream)",
                value=(
                    "• `/play_video action:<file/youtube/bilibili> video_name:<名稱/網址/BV/清單> [mode:<autoplay/loop/single>] [episode:<集數/P>]` — 播放本機、YouTube 或 Bilibili (支援多模式與播放清單)\n"
                    "• `/stream_stop` — 停止直播影片播放並切換回雪莉待機畫面\n"
                    "• `/stream_status` — 查看直播螢幕目前播放狀態與本機可用影片庫"
                ),
                inline=False,
            )
            embed.add_field(
                name="📡 AI 社群情報哨兵",
                value="• `/sentinel_fetch` — 手動觸發情報哨兵巡邏，抓取最新 AI 社群趨勢與論文",
                inline=False,
            )

        elif category == "記憶、管理與除錯 (Admin & Debug)":
            embed.title = "⚙️ 記憶、管理與除錯面板"
            embed.description = "伺服器狀態監控、長期記憶管理、頻道氛圍維護與開發者除錯工具。"
            embed.add_field(
                name="🧠 /memory & /vibe 記憶管理 (Choice 選單)",
                value=(
                    "• `/memory action:show <user>` — 檢視指定使用者的長期記憶檔案\n"
                    "• `/memory action:clear <user>` — 清除指定使用者的長期記憶 (管理員)\n"
                    "• `/vibe action:show [channel]` — 檢視頻道的即時話題氛圍\n"
                    "• `/vibe action:clear [channel]` — 清除頻道氛圍 (管理員)"
                ),
                inline=False,
            )
            embed.add_field(
                name="⚙️ /admin 系統管理與重啟 (Choice 選單)",
                value=(
                    "• `/admin action:sync` — 即時強制同步所有 Slash 指令樹至 Discord API\n"
                    "• `/admin action:reload [target]` — 重新加載指定 Cog 擴展\n"
                    "• `/admin action:restart [target]` — 模組熱重啟與 settings 重載\n"
                    "• `/admin action:config` — 開啟動態參數設定面板 (管理員)"
                ),
                inline=False,
            )
            embed.add_field(
                name="🐞 /debug & /stats 系統狀態與除錯",
                value=(
                    "• `/debug action:detail <enable: True/False>` — 切換詳細卡片回覆模式 (含 CoT/耗時/模型)\n"
                    "• `/stats` — 查看系統詳細運行狀態（LLM 佇列、記憶人數、開機時間）"
                ),
                inline=False,
            )

        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    """說明面板 View。"""

    def __init__(self) -> None:
        super().__init__(timeout=settings.DISCORD_UI_VIEW_TIMEOUT)
        self.add_item(HelpSelect())


class HelpCog(commands.Cog):
    """提供 /help 全功能使用說明指南。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="顯示雪莉的所有功能說明與指令大全")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        """顯示雪莉全功能指南首頁。"""
        embed = discord.Embed(
            title="🌸 雪莉 (Shirley) - 全功能指南手冊",
            description=(
                "你好！我是 **橘雪莉 (Sherry)**，一位活潑且自稱名偵探的 AI 伴侶。\n\n"
                "💡 **如何使用本指南？**\n"
                "請點擊下方的 **【下拉選單】** 選擇想了解的功能分類，即可檢視詳細的斜線指令與自然語言範例！\n\n"
                "🔹 **常用快速指令**：\n"
                "• `/help` — 隨時呼叫本指南\n"
                "• `/play` — 音樂播放系統\n"
                "• `/stats` — 查看雪莉大腦與系統狀態\n"
                "• `@雪莉` — 直接與我聊天或指派工作！"
            ),
            color=0xFFB6C1,
        )
        if interaction.client.user and interaction.client.user.display_avatar:
            embed.set_thumbnail(url=interaction.client.user.display_avatar.url)

        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


async def setup(bot: commands.Bot) -> None:
    """掛載 HelpCog 擴展。"""
    await bot.add_cog(HelpCog(bot))
