import discord
from discord.ext import commands
import random

class AntiPing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Danh sách 2 ID emoji động
        self.emojis = [
            "<a:fuckyou:1562687318870204487>",
            "<a:nguthichet:1562686888341545052>"
        ]

    @commands.Cog.listener()
    async def on_message(self, message):
        # Bỏ qua tin nhắn của bot để tránh lặp vô hạn
        if message.author.bot:
            return

        # Kiểm tra xem tin nhắn có ping @everyone hoặc @here không
        if "@everyone" in message.content or "@here" in message.content or message.mention_everyone:
            try:
                # Tỉ lệ 50/50 chọn ngẫu nhiên 1 trong 2 emoji
                chosen_emoji = random.choice(self.emojis)
                # Thả tương tác vào tin nhắn
                await message.add_reaction(chosen_emoji)
            except Exception as e:
                print(f"Lỗi khi bot thả reaction anti-ping: {e}")

# Hàm bắt buộc để Discord nhận diện Cog/Extension khi load
async def setup(bot):
    await bot.add_cog(AntiPing(bot))
