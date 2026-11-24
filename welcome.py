"""
welcome.py — KS-BOT chào thành viên mới bằng EMBED xanh (giống mẫu Delta Lightning).

Tin nhắn gồm:
  • Dòng chữ trên cùng  : "Hé lô bro @người_mới đã bay vào server ...!"   (tự đặt được)
  • Embed màu xanh      : tên + icon server ở đầu • nội dung nhiều chữ (tự đặt)
                          • avatar người mới (avatar thường HOẶC avatar GIF) ở góc phải
                          • ảnh/GIF lớn ở dưới (tự dán link)
  • (tuỳ chọn) gửi thêm 1 bản vào tin nhắn riêng (DM) của người mới

Ai set được: Admin / người có quyền Quản lý máy chủ / Quản lý kênh / Owner Bot / Chủ server.
Dữ liệu lưu ở welcome_config.json → discord_storage.py tự backup lên kênh #ksbot-config.

Biến dùng được trong chữ:  {user} (tag người mới) • {name} (tên) • {server} • {count} (số thành viên) • {id}
Xuống dòng: gõ \\n  (hoặc Shift+Enter khi dùng lệnh dấu chấm)

Lệnh (có cả slash /welcome ...):
  .welcome                  xem cài đặt hiện tại
  .welcome channel #kênh    đặt kênh chào
  .welcome message <chữ>    dòng chữ trên cùng (chỗ "Hé lô bro ... đã bay vào")
  .welcome text <chữ>       nội dung nhiều chữ trong embed
  .welcome image <link>     ảnh/GIF lớn ở dưới  (gõ off để xóa)
  .welcome color <#hex>     màu embed (mặc định xanh)
  .welcome dm on|off        gửi thêm vào tin nhắn riêng
  .welcome toggle on|off    bật/tắt lời chào
  .welcome test             xem thử ngay (chào chính bạn)
  .welcome reset            về mặc định
"""
import json
import os
from typing import Literal

import discord
from discord.ext import commands

OWNER_USER_ID = 1404399385965498479
CFG_FILE = "welcome_config.json"
DEFAULT_COLOR = 0x3498DB

DEFAULT = {
    "enabled": True,
    "channel": None,
    "message": "Hé lô bro {user} đã bay vào server **{server}**!",
    "text": (
        "# HÊ LÔ BRO TỚI VỚI {server}! 🎉\n"
        "## Bạn là thành viên thứ {count}\n"
        "1. Đọc luật server trước khi chat nhé.\n"
        "2. Làm quen với mọi người trong kênh chat.\n"
        "Cảm ơn bro đã join nhé!"
    ),
    "image": None,
    "color": DEFAULT_COLOR,
    "dm": False,
}

CFG = {}


def _load():
    global CFG
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        CFG = data if isinstance(data, dict) else {}
    except Exception:
        CFG = {}


def _save():
    try:
        tmp = CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CFG_FILE)
    except Exception as e:
        print(f"[Welcome] Lỗi ghi {CFG_FILE}: {e}")


def get_cfg(guild_id):
    merged = dict(DEFAULT)
    merged.update(CFG.get(str(guild_id), {}))
    return merged


def set_cfg(guild_id, **kw):
    CFG.setdefault(str(guild_id), {}).update(kw)
    _save()


def can_manage(member):
    if member.id == OWNER_USER_ID:
        return True
    if member == member.guild.owner:
        return True
    p = member.guild_permissions
    return p.administrator or p.manage_guild or p.manage_channels


def _fmt(text, member):
    g = member.guild
    rep = {
        "{user}": member.mention,
        "{name}": member.display_name,
        "{server}": g.name,
        "{count}": str(g.member_count or len(g.members)),
        "{id}": str(member.id),
    }
    for k, v in rep.items():
        text = text.replace(k, v)
    return text


def build(member, cfg):
    """Tạo (nội dung chữ, embed) y như mẫu."""
    guild = member.guild
    content = _fmt(cfg["message"], member)[:2000]
    embed = discord.Embed(
        description=_fmt(cfg["text"], member)[:4000],
        color=cfg.get("color") or DEFAULT_COLOR,
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    # display_avatar: tự lấy avatar server nếu có, avatar GIF thì tự động là GIF
    embed.set_thumbnail(url=member.display_avatar.replace(size=256).url)
    if cfg.get("image"):
        embed.set_image(url=cfg["image"])
    embed.remove_footer()
    return content, embed


async def safe_send(channel, **kw):
    """Gửi tin; nếu kênh bị khóa không chat được thì tự cấp quyền cho bot rồi gửi lại."""
    try:
        return await channel.send(**kw)
    except discord.Forbidden:
        try:
            await channel.set_permissions(
                channel.guild.me, view_channel=True, send_messages=True, embed_links=True,
                reason="KS-BOT: cho bot chat được trong kênh bị khóa",
            )
            return await channel.send(**kw)
        except Exception:
            return None
    except Exception:
        return None


async def _guard(ctx):
    if ctx.guild and can_manage(ctx.author):
        return True
    await ctx.send("❌ Chỉ **Admin / người có quyền Quản lý máy chủ** mới được cài lời chào.", delete_after=6)
    return False


def _ok(title, desc):
    return discord.Embed(title=title, description=desc, color=0x2ECC71)


class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------ sự kiện
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        cfg = get_cfg(member.guild.id)
        if not cfg["enabled"]:
            return
        content, embed = build(member, cfg)
        allowed = discord.AllowedMentions(users=[member], roles=False, everyone=False)
        if cfg["channel"]:
            ch = member.guild.get_channel(int(cfg["channel"]))
            if ch is not None:
                await safe_send(ch, content=content, embed=embed, allowed_mentions=allowed)
        if cfg["dm"]:
            try:
                await member.send(content=content, embed=embed)
            except Exception:
                pass  # người đó đóng tin nhắn riêng

    # ------------------------------------------------ lệnh
    @commands.hybrid_group(name="welcome", aliases=["chao"], fallback="show")
    async def welcome(self, ctx):
        """Xem cài đặt lời chào thành viên mới."""
        if not await _guard(ctx):
            return
        cfg = get_cfg(ctx.guild.id)
        ch = ctx.guild.get_channel(int(cfg["channel"])) if cfg["channel"] else None
        e = discord.Embed(title="👋 CÀI ĐẶT LỜI CHÀO", color=cfg.get("color") or DEFAULT_COLOR)
        e.add_field(name="Trạng thái", value="🟢 Bật" if cfg["enabled"] else "🔴 Tắt", inline=True)
        e.add_field(name="Kênh chào", value=ch.mention if ch else "❌ Chưa đặt (`.welcome channel #kênh`)", inline=True)
        e.add_field(name="Gửi DM", value="✅ Có" if cfg["dm"] else "❌ Không", inline=True)
        e.add_field(name="Dòng chữ trên cùng", value=f"```{cfg['message'][:300]}```", inline=False)
        e.add_field(name="Nội dung embed", value=f"```{cfg['text'][:700]}```", inline=False)
        e.add_field(name="Ảnh/GIF dưới", value=cfg["image"] or "Chưa có", inline=False)
        e.set_footer(text="Biến: {user} {name} {server} {count} {id}  •  Thử ngay: .welcome test")
        await ctx.send(embed=e)

    @welcome.command(name="channel")
    async def w_channel(self, ctx, channel: discord.TextChannel = None):
        """Đặt kênh gửi lời chào."""
        if not await _guard(ctx):
            return
        channel = channel or ctx.channel
        set_cfg(ctx.guild.id, channel=channel.id)
        await ctx.send(embed=_ok("✅ ĐÃ ĐẶT KÊNH CHÀO", f"Thành viên mới sẽ được chào ở {channel.mention}."))

    @welcome.command(name="message")
    async def w_message(self, ctx, *, noi_dung: str):
        """Dòng chữ trên cùng, VD: Hé lô bro {user} đã bay vào server {server}!"""
        if not await _guard(ctx):
            return
        set_cfg(ctx.guild.id, message=noi_dung.replace("\\n", "\n")[:500])
        await ctx.send(embed=_ok("✅ ĐÃ ĐỔI DÒNG CHỮ TRÊN CÙNG", "Gõ `.welcome test` để xem thử."))

    @welcome.command(name="text")
    async def w_text(self, ctx, *, noi_dung: str):
        """Nội dung nhiều chữ trong embed (hỗ trợ # tiêu đề, danh sách, tag kênh)."""
        if not await _guard(ctx):
            return
        set_cfg(ctx.guild.id, text=noi_dung.replace("\\n", "\n")[:3500])
        await ctx.send(embed=_ok("✅ ĐÃ ĐỔI NỘI DUNG EMBED", "Gõ `.welcome test` để xem thử."))

    @welcome.command(name="image")
    async def w_image(self, ctx, link: str):
        """Ảnh/GIF lớn ở dưới embed (dán link, gõ off để xóa)."""
        if not await _guard(ctx):
            return
        if link.lower() in ("off", "tat", "tắt", "xoa", "xóa", "none"):
            set_cfg(ctx.guild.id, image=None)
            return await ctx.send(embed=_ok("✅ ĐÃ XÓA ẢNH", "Embed sẽ không có ảnh lớn ở dưới."))
        if not link.lower().startswith(("http://", "https://")):
            return await ctx.send("❌ Link phải bắt đầu bằng `http://` hoặc `https://`.", delete_after=6)
        set_cfg(ctx.guild.id, image=link.strip("<>"))
        await ctx.send(embed=_ok(
            "✅ ĐÃ ĐẶT ẢNH/GIF",
            "Gõ `.welcome test` để xem thử.\n💡 Nên dùng link ảnh/GIF **cố định** (imgur, giphy, host riêng). "
            "Link file đính kèm của Discord sẽ hết hạn sau ít lâu.",
        ))

    @welcome.command(name="color")
    async def w_color(self, ctx, ma_mau: str):
        """Màu embed, VD: #3498DB"""
        if not await _guard(ctx):
            return
        try:
            val = int(ma_mau.strip().lstrip("#"), 16)
            if not 0 <= val <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            return await ctx.send("❌ Mã màu sai. Ví dụ: `.welcome color #3498DB`", delete_after=6)
        set_cfg(ctx.guild.id, color=val)
        await ctx.send(embed=_ok("✅ ĐÃ ĐỔI MÀU EMBED", f"Màu mới: `#{val:06X}`"))

    @welcome.command(name="dm")
    async def w_dm(self, ctx, trang_thai: Literal["on", "off"]):
        """Gửi thêm lời chào vào tin nhắn riêng của người mới."""
        if not await _guard(ctx):
            return
        set_cfg(ctx.guild.id, dm=(trang_thai == "on"))
        await ctx.send(embed=_ok("✅ ĐÃ CẬP NHẬT", f"Gửi DM: **{'Bật' if trang_thai == 'on' else 'Tắt'}**"))

    @welcome.command(name="toggle")
    async def w_toggle(self, ctx, trang_thai: Literal["on", "off"]):
        """Bật/tắt lời chào."""
        if not await _guard(ctx):
            return
        set_cfg(ctx.guild.id, enabled=(trang_thai == "on"))
        await ctx.send(embed=_ok("✅ ĐÃ CẬP NHẬT", f"Lời chào: **{'Bật' if trang_thai == 'on' else 'Tắt'}**"))

    @welcome.command(name="test")
    async def w_test(self, ctx):
        """Xem thử lời chào (chào chính bạn, gửi ngay tại kênh này)."""
        if not await _guard(ctx):
            return
        content, embed = build(ctx.author, get_cfg(ctx.guild.id))
        await ctx.send(content=content, embed=embed)

    @welcome.command(name="reset")
    async def w_reset(self, ctx):
        """Đưa lời chào về mặc định."""
        if not await _guard(ctx):
            return
        CFG.pop(str(ctx.guild.id), None)
        _save()
        await ctx.send(embed=_ok("✅ ĐÃ RESET", "Lời chào về mặc định. Nhớ đặt lại kênh bằng `.welcome channel #kênh`."))


async def setup(bot):
    _load()
    await bot.add_cog(WelcomeCog(bot))
