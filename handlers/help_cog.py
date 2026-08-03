import discord
from discord import app_commands
from discord.ext import commands

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="聊天與互動 (Chat)", description="如何與雪莉日常對話", emoji="💬"),
            discord.SelectOption(label="音樂系統 (Music)", description="點歌與音樂播放指令", emoji="🎵"),
            discord.SelectOption(label="圖片生成 (Draw)", description="讓雪莉幫你畫圖", emoji="🎨"),
            discord.SelectOption(label="圖片搜尋 (Search Image)", description="搜尋動漫圖片 (Pixiv 等)", emoji="🖼️"),
            discord.SelectOption(label="網頁搜尋 (Search Web)", description="搜尋最新資訊與新聞", emoji="🔍"),
            discord.SelectOption(label="語音系統 (Voice)", description="加入語音頻道交談", emoji="🎤"),
            discord.SelectOption(label="排程與提醒 (Schedule)", description="設定定時任務", emoji="⏰"),
            discord.SelectOption(label="記憶系統 (Memory)", description="雪莉的長期記憶管理", emoji="🧠"),
        ]
        super().__init__(placeholder="請選擇你想了解的功能分類...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        embed = discord.Embed(color=0xffb6c1) # Pink color for Shirley
        embed.set_author(name="雪莉 (Shirley) - 使用說明書", icon_url=interaction.client.user.display_avatar.url if interaction.client.user.display_avatar else None)
        
        if category == "聊天與互動 (Chat)":
            embed.title = "💬 聊天與互動"
            embed.description = "直接在頻道中 `@雪莉` 或是回覆她的訊息，即可與她聊天！她擁有動態人設與記憶能力。"
            embed.add_field(name="範例", value="`@雪莉 妳今天心情好嗎？`\n`@雪莉 幫我總結一下上面的對話`", inline=False)
        elif category == "音樂系統 (Music)":
            embed.title = "🎵 音樂系統"
            embed.description = "支援 YouTube 音樂播放。你甚至不需要用斜線指令，直接用文字告訴她即可！"
            embed.add_field(name="斜線指令", value="`/play <網址>` - 播放音樂\n`/skip` - 跳過當前歌曲\n`/stop` - 停止並清空清單", inline=False)
            embed.add_field(name="自然語言範例", value="`@雪莉 幫我播 Yoasobi 的 Idol`\n`@雪莉 這首不好聽，卡歌`", inline=False)
        elif category == "圖片生成 (Draw)":
            embed.title = "🎨 圖片生成"
            embed.description = "使用 Stable Diffusion 等模型為你生成精美圖片。"
            embed.add_field(name="自然語言範例", value="`@雪莉 幫我畫一個白髮紅眼的精靈少女`\n`@雪莉 畫一隻可愛的貓咪`", inline=False)
        elif category == "圖片搜尋 (Search Image)":
            embed.title = "🖼️ 圖片搜尋"
            embed.description = "搜尋 Pixiv 或二次元圖片。"
            embed.add_field(name="自然語言範例", value="`@雪莉 找一些初音未來的圖給我看`\n`@雪莉 我想看蔚藍檔案的色圖`", inline=False)
        elif category == "網頁搜尋 (Search Web)":
            embed.title = "🔍 網頁搜尋與聯網分析"
            embed.description = "雪莉在聊天對話中已全面具備即時聯網搜尋與實體/新聞分析能力！"
            embed.add_field(name="自然語言對話範例", value="`@雪莉 你覺得0050怎麼樣？`\n`@雪莉 目前美伊戰爭怎麼樣了？`\n`https://... (貼網址自動閱讀與總結)`", inline=False)
        elif category == "語音系統 (Voice)":
            embed.title = "🎤 語音系統"
            embed.description = "加入語音頻道，用語音跟雪莉聊天！"
            embed.add_field(name="斜線指令", value="`/join_voice` - 讓雪莉加入你所在的語音頻道\n`/leave_voice` - 讓雪莉離開", inline=False)
            embed.add_field(name="使用方式", value="加入後直接對著麥克風說話，雪莉聽懂後會直接用聲音回答你！", inline=False)
        elif category == "排程與提醒 (Schedule)":
            embed.title = "⏰ 排程與提醒"
            embed.description = "設定定時任務或鬧鐘。"
            embed.add_field(name="自然語言範例", value="`@雪莉 明天早上 8 點叫我起床`\n`@雪莉 10分鐘後提醒我關火`", inline=False)
        elif category == "記憶系統 (Memory)":
            embed.title = "🧠 記憶系統"
            embed.description = "雪莉會自動記住重要的事情。"
            embed.add_field(name="斜線指令", value="`/memory_show` - 查看雪莉對你的記憶檔案 (僅限自己可見)", inline=False)
            embed.add_field(name="自然語言範例", value="`@雪莉 記住我最喜歡吃草莓蛋糕`\n`@雪莉 妳還記得我喜歡吃什麼嗎？`", inline=False)
            
        await interaction.response.edit_message(embed=embed, view=self.view)

class HelpView(discord.ui.View):
    def __init__(self):
        import settings
        super().__init__(timeout=settings.DISCORD_UI_VIEW_TIMEOUT)
        self.add_item(HelpSelect())

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="顯示雪莉的所有功能說明與指令大全")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="雪莉 (Shirley) - 全功能指南",
            description="你好！我是雪莉。請從下方的選單選擇你想了解的功能分類。",
            color=0xffb6c1
        )
        if interaction.client.user.display_avatar:
            embed.set_thumbnail(url=interaction.client.user.display_avatar.url)
            
        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
