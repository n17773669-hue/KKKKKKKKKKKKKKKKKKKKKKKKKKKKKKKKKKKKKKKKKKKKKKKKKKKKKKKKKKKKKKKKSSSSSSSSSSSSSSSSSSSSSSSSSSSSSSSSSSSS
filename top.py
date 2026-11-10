"""
top.py - Bảng xếp hạng đại gia (top coin trong server/bot)

Hiện tại: embed chữ, giữ nguyên như cũ trong bot.py.
Sau này làm xong ảnh thì chỉ cần thay file này bằng bản mới
(giữ lại hàm `setup` ở cuối là bot.py load được).
"""
import sqlite3

import discord
from discord.ext import commands

from database import DB_NAME, COIN_EMOJI, MAIN_COLOR


def get_top_rows(limit: int = 10):
    """Lấy top coin từ DB. Bản ảnh sau này dùng lại hàm này luôn."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, coin, streak FROM users ORDER BY coin DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


class TopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='top')
    async def cmd_top(self, ctx):
        """Xem bảng xếp hạng đại gia."""
        rows = get_top_rows(10)
        desc = ""
        for idx, (u, c, s) in enumerate(rows, 1):
            desc += f"`Top {idx}` <@!{u}>: `{c:,}` {COIN_EMOJI} | Streak: **{s}🔥**\n"
        embed = discord.Embed(title="🏆 BẢNG PHONG THẦN ĐẠI GIA", description=desc, color=MAIN_COLOR)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TopCog(bot))
