import re
import time
import asyncio
import sqlite3
import discord
from discord.ext import commands
 
# ================================================================
# LỆNH .lixi RIÊNG — CHỈ NGƯỜI CÓ ID TRONG DANH SÁCH DƯỚI ĐÂY DÙNG ĐƯỢC
#
# File này ĐỘC LẬP với bot.py (không cần sửa gì bên bot.py), nhưng đọc/ghi
# CHUNG 1 database (ksbot_master.db, bảng `users`, cột `coin`) — nên coin
# phát ra từ lệnh .lixi ở đây LUÔN đồng bộ với toàn bộ hệ kinh tế bên
# bot.py (shop, cược, top, .cash, ...). Người chơi nhận xong là dùng
# được ngay, không cần chỉnh gì thêm.
#
# CÁCH DÙNG:
#   .lixi <tiền> <thời gian hết hạn>
#   VD:  .lixi 500000 10m     -> mỗi người nhận 500,000 coin, hết hạn sau 10 phút
#        .lixi 2tr 1h         -> mỗi người nhận 2,000,000 coin, hết hạn sau 1 giờ
#        .lixi 500k 30s       -> mỗi người nhận 500,000 coin, hết hạn sau 30 giây
#
#   Đơn vị thời gian: s (giây), m (phút - mặc định nếu không ghi), h (giờ), d (ngày)
#   Đơn vị tiền: số thường (500000), có thể dùng hậu tố k (nghìn) hoặc tr/m (triệu)
# ================================================================
 
# --- DANH SÁCH ID ĐƯỢC PHÉP DÙNG LỆNH .lixi ---
# Thêm/bớt ID Discord (số nguyên) của người được phép ở đây, mỗi ID 1 dòng.
ALLOWED_LIXI_IDS = {
    1404399385965498479,   # <-- ĐỔI thành ID Discord thật của bạn (đang để tạm theo OWNER_USER_ID bên bot.py)
    # 111111111111111111,  # <-- thêm ID khác nếu muốn cho phép thêm người
}
 
DB_NAME = "ksbot_master.db"     # PHẢI trùng tên file DB với bot.py để coin đồng bộ
DEFAULT_COIN = 100000            # PHẢI trùng giá trị mặc định coin khởi tạo bên bot.py
 
 
# ----------------------------------------------------------------
# TRUY CẬP DATABASE — dùng chung schema/cột với bot.py (users.coin)
# ----------------------------------------------------------------
 
def _add_coin(user_id: int, amount: int) -> int:
    """Cộng thêm `amount` coin cho user_id trong CHÍNH database bot.py đang dùng.
    Trả về số coin mới sau khi cộng."""
    uid = str(user_id)
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT coin FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    if row is None:
        new_coin = DEFAULT_COIN + amount
        cur.execute(
            "INSERT INTO users (user_id, coin, level, exp, streak, last_streak_time, history_tx) "
            "VALUES (?, ?, 1, 0, 1, ?, '')",
            (uid, new_coin, time.time()),
        )
    else:
        new_coin = row[0] + amount
        cur.execute("UPDATE users SET coin = ? WHERE user_id = ?", (new_coin, uid))
    conn.commit()
    conn.close()
    return new_coin
 
 
# ----------------------------------------------------------------
# PARSE SỐ TIỀN / THỜI GIAN
# ----------------------------------------------------------------
 
def _parse_amount(raw: str):
    """VD hợp lệ: 500000 | 500,000 | 500k | 2tr | 2m"""
    if not raw:
        return None
    s = raw.strip().lower().replace(",", "").replace(".", "")
    multiplier = 1
    if s.endswith("tr"):
        multiplier = 1_000_000
        s = s[:-2]
    elif s.endswith("k"):
        multiplier = 1_000
        s = s[:-1]
    elif s.endswith("m"):
        multiplier = 1_000_000
        s = s[:-1]
    if not s.isdigit():
        return None
    val = int(s) * multiplier
    return val if val > 0 else None
 
 
def _parse_duration(raw: str):
    """VD hợp lệ: 30s | 10m | 2h | 1d | 15 (mặc định phút). Trả về số giây."""
    if not raw:
        return None
    m = re.fullmatch(r"(\d+)\s*(s|m|h|d)?", raw.strip().lower())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2) or "m"
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    total = value * seconds
    return total if total > 0 else None
 
 
def _fmt_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} ngày"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} giờ"
    if seconds % 60 == 0:
        return f"{seconds // 60} phút"
    return f"{seconds} giây"
 
 
# ----------------------------------------------------------------
# NÚT NHẬN LÌ XÌ
# ----------------------------------------------------------------
 
class ManualLixiView(discord.ui.View):
    """Mỗi người chỉ nhận được 1 lần / đợt. Tự hết hạn theo thời gian admin đặt."""
 
    def __init__(self, amount: int, expire_at: float):
        super().__init__(timeout=None)
        self.amount = amount
        self.expire_at = expire_at
        self.claimed_ids = set()
        self.active = True
 
    @discord.ui.button(label="🧧 NHẬN LÌ XÌ", style=discord.ButtonStyle.success, emoji="💰")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.active or time.time() >= self.expire_at:
            self.active = False
            return await interaction.response.send_message(
                "⏰ Lì xì này đã hết hạn.", ephemeral=True
            )
        if interaction.user.id in self.claimed_ids:
            return await interaction.response.send_message(
                "❌ Bạn đã nhận lì xì đợt này rồi!", ephemeral=True
            )
        self.claimed_ids.add(interaction.user.id)
        try:
            new_coin = _add_coin(interaction.user.id, self.amount)
            await interaction.response.send_message(
                f"🧧 **Bạn nhận được {self.amount:,} 🪙!**\n💰 Số dư hiện tại: **{new_coin:,} 🪙**",
                ephemeral=True,
            )
        except Exception:
            self.claimed_ids.discard(interaction.user.id)
            await interaction.response.send_message(
                "❌ Không thể cộng tiền lúc này, hãy thử lại.", ephemeral=True
            )
 
 
# ----------------------------------------------------------------
# COG
# ----------------------------------------------------------------
 
class LixiAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
 
    def _is_allowed(self, user: discord.abc.User) -> bool:
        return user.id in ALLOWED_LIXI_IDS
 
    @commands.command(name="lixi")
    @commands.guild_only()
    async def lixi(self, ctx: commands.Context, amount_raw: str = None, duration_raw: str = None):
        """.lixi <tiền> <thời gian hết hạn> — VD: .lixi 500k 10m
        CHỈ người có ID trong ALLOWED_LIXI_IDS mới dùng được lệnh này."""
 
        if not self._is_allowed(ctx.author):
            return  # im lặng bỏ qua, không lộ cú pháp lệnh cho người không có quyền
 
        if amount_raw is None or duration_raw is None:
            return await ctx.send(
                "❌ Cú pháp: `.lixi <tiền> <thời gian>`\n"
                "VD: `.lixi 500000 10m` hoặc `.lixi 2tr 1h`\n"
                "Đơn vị thời gian: s (giây), m (phút, mặc định), h (giờ), d (ngày).",
                delete_after=10,
            )
 
        amount = _parse_amount(amount_raw)
        if amount is None:
            return await ctx.send(
                "❌ Số tiền không hợp lệ. VD: `500000`, `500k`, `2tr`.", delete_after=8
            )
 
        duration = _parse_duration(duration_raw)
        if duration is None:
            return await ctx.send(
                "❌ Thời gian không hợp lệ. VD: `30s`, `10m`, `2h`, `1d`.", delete_after=8
            )
 
        expire_at = time.time() + duration
        view = ManualLixiView(amount, expire_at)
 
        embed = discord.Embed(
            title="🧧 LÌ XÌ ĐẶC BIỆT!",
            description=(
                f"🎉 Mỗi người nhận được **{amount:,} 🪙**!\n\n"
                f"👥 Mỗi người chỉ được nhận **1 lần**.\n"
                f"⏰ Hết hạn sau **{_fmt_duration(duration)}**.\n"
                f"⚡ Nhanh tay bấm nút bên dưới!"
            ),
            color=0xE74C3C,
        )
        embed.set_footer(text=f"Phát bởi {ctx.author.display_name}")
 
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
 
        msg = await ctx.send(content="🎊 **LÌ XÌ TOÀN SERVER!**", embed=embed, view=view)
 
        async def _expire_later():
            await asyncio.sleep(duration)
            view.active = False
            for item in view.children:
                item.disabled = True
            try:
                expired_embed = embed.copy()
                expired_embed.title = "🧧 LÌ XÌ ĐÃ HẾT HẠN"
                expired_embed.color = 0x7F8C8D
                await msg.edit(embed=expired_embed, view=view)
            except (discord.NotFound, discord.HTTPException):
                pass
 
        asyncio.create_task(_expire_later())
 
 
async def setup(bot: commands.Bot):
    await bot.add_cog(LixiAdminCog(bot))
