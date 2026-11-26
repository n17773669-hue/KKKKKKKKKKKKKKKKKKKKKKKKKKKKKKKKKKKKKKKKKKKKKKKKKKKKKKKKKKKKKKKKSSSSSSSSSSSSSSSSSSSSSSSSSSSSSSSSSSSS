import status
import os
import random
import time
import asyncio
import sqlite3
import datetime
import vr_patch
import re
import json
from typing import Optional
import threading

import discord
from discord.ext import commands

from database import (
    DB_NAME, COIN_EMOJI, MAIN_COLOR, UI_COLORS, UI_FOOTER,
    get_user, update_user_full, add_exp_to_user, parse_bet_amount,
)

# ==========================================
# KHỞI TẠO BOT & PREFIX (DẤU .)
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix=['.'], intents=intents)
bot.remove_command('help')

# Phòng chơi Ma Sói dùng chung giữa bot.py và masoi.py (cog đọc qua bot.game_lobbies)
bot.game_lobbies = {}

BOT_ORIGINAL_NAME = "💤KS-BOT💤"
DIEM_DANH_TRACKER = {}

RIGHT_REACTION = discord.PartialEmoji(name="3_", id=1548249974611910687, animated=True)
HELP_GIF = "https://cdn.discordapp.com/attachments/1573883113623261245/1574728280689483816/animation.gif"

# ================================================================
# 👑 OWNER CONFIG — ĐỔI ID Ở ĐÂY NẾU SAU NÀY ĐỔI TÀI KHOẢN
# ================================================================
OWNER_USER_ID = 1404399385965498479


def is_bot_owner(user):
    try:
        return int(getattr(user, "id", user)) == OWNER_USER_ID
    except Exception:
        return False


# ==========================================
# 🎨 BẢNG MÀU + HÀM TẠO EMBED
# ==========================================
C_OK = 0x2ECC71
C_ERR = 0xE74C3C
C_INFO = 0x5865F2
C_GOLD = 0xF1C40F
C_PINK = 0xEB459E
C_DARK = 0x2B2D31


def ks_embed(title, desc=None, color=C_INFO, user=None, thumb=None, footer=None):
    """Embed chuẩn của KS-BOT: tiêu đề đậm, có avatar người dùng nhỏ ở footer."""
    e = discord.Embed(title=title, description=desc, color=color)
    if thumb:
        e.set_thumbnail(url=thumb)
    if user is not None:
        e.set_footer(text=footer or f"Yêu cầu bởi {user.display_name}", icon_url=user.display_avatar.url)
    elif footer:
        e.set_footer(text=footer)
    return e


async def _ack(ctx, text="✅ Xong!"):
    """Xác nhận bằng emote (prefix) hoặc tin ẩn (slash). Không spam tin nhắn."""
    if ctx.interaction:
        return await ctx.send(text, ephemeral=True)
    try:
        await ctx.message.add_reaction(RIGHT_REACTION)
    except Exception:
        try:
            await ctx.message.add_reaction("✅")
        except Exception:
            pass


# ==========================================
# STREAK
# ==========================================
async def process_user_streak_and_nickname(member):
    uid_str = str(member.id)
    coin, level, exp, streak, last_streak_time, history_tx = get_user(uid_str)

    current_time = time.time()

    if last_streak_time == 0:
        last_streak_time = current_time
        streak = 1
    else:
        time_diff = current_time - last_streak_time
        if time_diff >= 86400:
            if time_diff >= 172800:
                streak = 1
            else:
                streak += 1
            last_streak_time = current_time
        elif time_diff < 0:
            last_streak_time = current_time

    update_user_full(uid_str, coin, level, exp, streak, last_streak_time, history_tx)


# ==========================================
# UI SKIN: thêm màu/footer mặc định cho mọi Embed
# ==========================================
def _ks_embed_init(original_init):
    def wrapped(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            if getattr(self, "colour", None) is None or getattr(self, "colour", None).value == 0:
                self.colour = discord.Colour(UI_COLORS["primary"])
            if not getattr(self, "footer", None) or not self.footer.get("text"):
                self.set_footer(text=UI_FOOTER)
        except Exception:
            pass
    return wrapped


if not getattr(discord.Embed.__init__, "_ksbot_v11", False):
    _orig_embed_init_v11 = discord.Embed.__init__
    discord.Embed.__init__ = _ks_embed_init(_orig_embed_init_v11)
    discord.Embed.__init__._ksbot_v11 = True


# ==========================================
# 💤 AFK — lưu file, key theo (server, user)
# ==========================================
AFK_FILE = "afk_users.json"
AFK_PREFIX = "[AFK] "


def _afk_key(guild_id, user_id):
    return f"{guild_id}:{user_id}"


def _load_afk():
    try:
        with open(AFK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_afk():
    try:
        with open(AFK_FILE, "w", encoding="utf-8") as f:
            json.dump(AFK_USERS, f, ensure_ascii=False)
    except Exception:
        pass


AFK_USERS = _load_afk()   # {"guild:user": {"reason": str, "old_nick": str|None}}


def _afk_original_nick(member):
    """Lấy nick gốc (None = đang dùng tên mặc định), bỏ [AFK] nếu có."""
    nick = member.nick
    if nick and nick.startswith(AFK_PREFIX):
        nick = nick[len(AFK_PREFIX):]
        if nick in (member.name, member.global_name):
            nick = None
    return nick


async def _apply_afk_nick(member, orig):
    try:
        g = member.guild
        if member == g.owner or not g.me.guild_permissions.manage_nicknames:
            return
        base = orig or member.global_name or member.name
        new = (AFK_PREFIX + base)[:32]
        if member.nick != new:
            await member.edit(nick=new, reason="Bật AFK")
    except Exception:
        pass


async def _restore_nick(member, orig):
    """Trả đúng nick gốc. Nếu người đó tự đổi nick khác khi đang AFK thì không đụng."""
    try:
        g = member.guild
        if member == g.owner or not g.me.guild_permissions.manage_nicknames:
            return
        if member.nick and member.nick.startswith(AFK_PREFIX):
            await member.edit(nick=orig, reason="Tắt AFK")   # orig=None → về tên mặc định
    except Exception:
        pass


@bot.hybrid_command(name='afk')
async def cmd_afk(ctx, *, reason: str = None):
    """Bật chế độ AFK."""
    if not ctx.guild:
        return
    member = ctx.author
    key = _afk_key(ctx.guild.id, member.id)
    reason = (reason or "thí chủ đi vắng rồi đừng gọi").strip()[:200]

    old = AFK_USERS.get(key)
    orig = old["old_nick"] if old else _afk_original_nick(member)   # không bao giờ ghi đè tên gốc
    AFK_USERS[key] = {"reason": reason, "old_nick": orig}
    _save_afk()
    await _apply_afk_nick(member, orig)
    await _ack(ctx, f"💤 Đã bật AFK: *{reason}*")


async def _afk_handle(message):
    """Xử lý AFK: tự tắt khi người đó nhắn, và trả lời khi có người ping/reply người đang AFK.
    Được gọi cả ở kênh bị .blockbot."""
    if not message.guild or message.author.bot:
        return

    key = _afk_key(message.guild.id, message.author.id)
    if key in AFK_USERS and _command_name_from_message(message) != "afk":
        info = AFK_USERS.pop(key)
        _save_afk()
        await _restore_nick(message.author, info.get("old_nick"))
        try:
            await message.channel.send(f"👋 {message.author.mention} đã quay lại, tắt AFK.", delete_after=5)
        except Exception:
            pass

    targets = {m.id: m for m in message.mentions}
    ref = message.reference
    if ref and isinstance(ref.resolved, discord.Message):
        targets.setdefault(ref.resolved.author.id, ref.resolved.author)
    for uid, user in list(targets.items())[:3]:
        if uid == message.author.id or user.bot:
            continue
        info = AFK_USERS.get(_afk_key(message.guild.id, uid))
        if info:
            try:
                await message.reply(
                    f"💤 **{user.display_name}** đang AFK: *{info['reason']}*",
                    mention_author=False, delete_after=15,
                )
            except Exception:
                pass


# ==========================================
# ĐỔI BIỆT DANH (.name) — chỉ thả emote
# ==========================================
@bot.hybrid_command(name='nickname', aliases=['name'])
async def cmd_name(ctx, *, new_nick: str = None):
    """Đổi biệt danh của bạn."""
    if not new_nick:
        return await ctx.send("❌ Cú pháp: `.nickname <tên_mới>` (hoặc `.name`)", delete_after=5)
    if len(new_nick) > 32:
        return await ctx.send("❌ Biệt danh tối đa 32 ký tự!", delete_after=5)
    if ctx.author == ctx.guild.owner:
        return await ctx.send("❌ Bot không thể đổi biệt danh của Chủ Server!", delete_after=5)
    try:
        await ctx.author.edit(nick=new_nick, reason="Đổi tên bằng lệnh .name")
        add_exp_to_user(ctx.author.id, 10)
        await _ack(ctx, f"✏️ Đã đổi tên thành **{new_nick}**")
    except Exception as e:
        await ctx.send(f"❌ Không đổi được: `{type(e).__name__}`", delete_after=6)


# ==========================================
# KINH TẾ: PAY / AV / COIN / DAILY
# ==========================================
@bot.hybrid_command(name='pay', aliases=['transfer'])
async def cmd_pay(ctx, member: discord.Member = None, amount_str: str = None):
    """Chuyển coin cho người khác."""
    if not member or not amount_str:
        await ctx.send("❌ Cú pháp: `.pay @user <số_tiền/all>`", delete_after=6)
        return

    if member.bot or member == ctx.author:
        await ctx.send("❌ Bạn không thể chuyển tiền cho bot hoặc chính mình!", delete_after=5)
        return

    sender_id = str(ctx.author.id)
    sender_data = get_user(sender_id)

    amount = parse_bet_amount(sender_data[0], amount_str)
    if amount is None or amount <= 0:
        await ctx.send("❌ Số tiền chuyển không hợp lệ!", delete_after=5)
        return

    if sender_data[0] < amount:
        await ctx.send(f"❌ Bạn không đủ tiền trong ví! Số dư: `{sender_data[0]:,}` {COIN_EMOJI}", delete_after=5)
        return

    receiver_id = str(member.id)
    receiver_data = get_user(receiver_id)

    update_user_full(sender_id, sender_data[0] - amount, sender_data[1], sender_data[2], sender_data[3], sender_data[4], sender_data[5])
    update_user_full(receiver_id, receiver_data[0] + amount, receiver_data[1], receiver_data[2], receiver_data[3], receiver_data[4], receiver_data[5])
    add_exp_to_user(sender_id, 10)

    embed = ks_embed(
        "💸 CHUYỂN TIỀN THÀNH CÔNG",
        f"📤 **Người gửi:** {ctx.author.mention}\n"
        f"📥 **Người nhận:** {member.mention}\n"
        f"💰 **Số tiền:** `{amount:,}` {COIN_EMOJI}",
        color=C_OK, user=ctx.author, thumb=member.display_avatar.url,
    )
    embed.add_field(name="Ví của bạn còn", value=f"`{sender_data[0] - amount:,}` {COIN_EMOJI}", inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='av', aliases=['avt'])
async def cmd_av(ctx, member: discord.Member = None):
    """Xem ảnh đại diện."""
    target = member or ctx.author
    embed = ks_embed(
        f"🖼️ ẢNH ĐẠI DIỆN CỦA {target.display_name.upper()}",
        f"🔗 [Tải ảnh HD]({target.display_avatar.url})",
        color=C_PINK, user=ctx.author,
    )
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='coin', aliases=['wallet'])
async def cmd_coin(ctx):
    """Xem số dư coin."""
    u_data = get_user(str(ctx.author.id))
    embed = ks_embed(
        f"💰 VÍ CỦA {ctx.author.display_name.upper()}",
        None, color=C_GOLD, user=ctx.author, thumb=ctx.author.display_avatar.url,
    )
    embed.add_field(name=f"{COIN_EMOJI} Coin", value=f"**{u_data[0]:,}**", inline=True)
    embed.add_field(name="⭐ Level", value=f"**Lv.{u_data[1]}**\n`EXP {u_data[2]}`", inline=True)
    embed.add_field(name="🔥 Streak", value=f"**{u_data[3]}** ngày", inline=True)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='daily')
async def cmd_daily(ctx):
    """Nhận lương hằng ngày."""
    uid = str(ctx.author.id)
    now = time.time()
    if uid in DIEM_DANH_TRACKER and now - DIEM_DANH_TRACKER[uid] < 20 * 3600:
        left = int(20 * 3600 - (now - DIEM_DANH_TRACKER[uid]))
        embed = ks_embed(
            "⏳ CHƯA ĐẾN GIỜ LĨNH LƯƠNG",
            f"Bạn đã nhận rồi! Quay lại sau **{left // 3600} giờ {(left % 3600) // 60} phút** nữa nhé.",
            color=C_ERR, user=ctx.author,
        )
        await ctx.send(embed=embed, delete_after=8)
        return
    DIEM_DANH_TRACKER[uid] = now
    u_data = get_user(uid)
    update_user_full(uid, u_data[0] + 1500, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
    add_exp_to_user(uid, 100)
    embed = ks_embed(
        "🎁 NHẬN LƯƠNG THÀNH CÔNG",
        f"Bạn nhận được **+1,500** {COIN_EMOJI}\n✨ Thưởng thêm **+100 EXP**",
        color=C_GOLD, user=ctx.author, thumb=ctx.author.display_avatar.url,
    )
    await ctx.send(embed=embed)


# ================================================================
# 🛡️ QUẢN TRỊ — GAG / UNMUTE / MUTE / KICK / BAN / UNBAN
# ================================================================
def _server_owner_only(ctx):
    """Chỉ Chủ Bot (owner code) hoặc Chủ Server (guild owner) mới được dùng."""
    return bool(
        ctx.guild and (
            is_bot_owner(ctx.author)
            or ctx.author == ctx.guild.owner
        )
    )


_admin_actor = _server_owner_only
_moderator_actor = _server_owner_only


def _can_act_on_member(ctx, member):
    if not member or not ctx.guild:
        return False
    if is_bot_owner(member):
        return False
    if member == ctx.guild.owner:
        return False
    if member == ctx.author and not is_bot_owner(ctx.author):
        return False
    if not is_bot_owner(ctx.author) and ctx.author != ctx.guild.owner:
        if member.top_role >= ctx.author.top_role:
            return False
    me = ctx.guild.me
    if me and member.top_role >= me.top_role:
        return False
    return True


async def _delete_command_quietly(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.hybrid_command(name='gag', aliases=['khoamom'])
async def cmd_khoamom(ctx, member: discord.Member = None):
    """Khóa mõm thành viên 5 phút."""
    if not member:
        await ctx.send("❌ Cú pháp: `.gag @user` (hoặc `.khoamom`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
        return
    try:
        await member.timeout(datetime.timedelta(minutes=5), reason="Khóa mõm 5p")
        await ctx.send(f"🤐 {member.mention} bị khóa mõm **5 phút**.", delete_after=8)
        add_exp_to_user(ctx.author.id, 10)
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}", delete_after=6)


@bot.hybrid_command(name='unmute', aliases=['unkhoamom', 'ungag'])
async def cmd_unmute(ctx, member: discord.Member = None):
    """Gỡ mute/timeout/khóa mõm cho thành viên."""
    if not member:
        await ctx.send("❌ Cú pháp: `.unmute @user` (hoặc `.ungag`, `.unkhoamom`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
        return
    try:
        await member.timeout(None, reason="Gỡ mute")
        await ctx.send(f"🔊 Đã gỡ mute cho {member.mention}.", delete_after=8)
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}", delete_after=6)


@bot.hybrid_command(name='mute', aliases=['khoachat', 'timeout'])
async def cmd_khoachat(ctx, member: discord.Member = None, time_str: str = None):
    """Khóa chat (mute/timeout) thành viên theo thời gian."""
    if not member or not time_str:
        await ctx.send("❌ Cú pháp: `.mute @user <10p/1h/1d>` (hoặc `.khoachat`, `.timeout`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được mute/timeout.", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể mute Owner, chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    t_num = ''.join(c for c in time_str if c.isdigit())
    t_unit = ''.join(c for c in time_str if not c.isdigit()).lower()
    if not t_num.isdigit() or int(t_num) <= 0:
        await ctx.send("❌ Thời gian không hợp lệ.", delete_after=5)
        return
    val = int(t_num)
    delta = datetime.timedelta(hours=val) if 'h' in t_unit else datetime.timedelta(days=val) if 'd' in t_unit else datetime.timedelta(minutes=val)
    try:
        await member.timeout(delta, reason=f"Khóa chat bởi {ctx.author}")
        await ctx.send(f"🔇 {member.mention} bị khóa chat **{time_str}**.", delete_after=8)
        add_exp_to_user(ctx.author.id, 15)
    except Exception as e:
        await ctx.send(f"❌ Không thể khóa chat: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name='kick')
async def cmd_kick(ctx, member: discord.Member = None, *, reason: str = 'Không ghi lý do'):
    """Kick thành viên khỏi server."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được kick.", delete_after=5)
        return
    if member is None:
        await ctx.send("❌ Cú pháp: `.kick @user [lý do]`", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể kick Owner/chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    if not ctx.guild.me.guild_permissions.kick_members:
        await ctx.send("❌ Bot thiếu quyền **Kick Members**.", delete_after=5)
        return
    try:
        await member.kick(reason=f"{reason} | bởi {ctx.author}")
        embed = ks_embed("👢 ĐÃ KICK", f"**{member}** bị đá khỏi server.\n> 📝 Lý do: *{reason}*",
                         color=C_ERR, user=ctx.author, thumb=member.display_avatar.url)
        await ctx.send(embed=embed, delete_after=10)
    except Exception as e:
        await ctx.send(f"❌ Không thể kick: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name='ban')
async def cmd_ban(ctx, member: discord.Member = None, *, reason: str = 'Không ghi lý do'):
    """Ban thành viên khỏi server."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được ban.", delete_after=5)
        return
    if member is None:
        await ctx.send("❌ Cú pháp: `.ban @user [lý do]`", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể ban Owner/chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ Bot thiếu quyền **Ban Members**.", delete_after=5)
        return
    try:
        await member.ban(reason=f"{reason} | bởi {ctx.author}")
        embed = ks_embed("🔨 ĐÃ BAN", f"**{member}** bị cấm vĩnh viễn.\n> 📝 Lý do: *{reason}*",
                         color=C_ERR, user=ctx.author, thumb=member.display_avatar.url)
        await ctx.send(embed=embed, delete_after=10)
    except Exception as e:
        await ctx.send(f"❌ Không thể ban: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name='unban')
async def cmd_unban(ctx, user_id: str = None):
    """Gỡ ban theo user ID."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được unban.", delete_after=5)
        return
    if not user_id or not user_id.isdigit():
        await ctx.send("❌ Cú pháp: `.unban <user_id>`", delete_after=5)
        return
    if not ctx.guild.me.guild_permissions.ban_members:
        await ctx.send("❌ Bot thiếu quyền **Ban Members**.", delete_after=5)
        return
    try:
        await ctx.guild.unban(discord.Object(id=int(user_id)), reason=f"Unban bởi {ctx.author}")
        await ctx.send(f"🔓 Đã gỡ ban ID `{user_id}`.", delete_after=8)
    except discord.NotFound:
        await ctx.send("❌ Không tìm thấy người này trong danh sách ban.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Không thể unban: `{type(e).__name__}`", delete_after=6)


# ================================================================
# 🧹 CLEAR / XTN / ADDROLE / DELROLE
# ================================================================
def _can_manage_messages(ctx):
    return _server_owner_only(ctx)


def _can_manage_roles(ctx):
    return _server_owner_only(ctx)


async def _clear_core(ctx, target, spec):
    """Lõi xử lý lệnh clear — dùng chung cho cả .clear (prefix) và /clear (slash)."""
    spec = (spec or "").lower().strip()
    duration_match = re.fullmatch(r"(\d+)(p|m|h|d)", spec)
    count_match = re.fullmatch(r"\d+", spec)
    cur_msg_id = ctx.message.id if getattr(ctx, "message", None) else None

    try:
        deleted = []
        if duration_match:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2)
            seconds = amount * {"p": 60, "m": 60, "h": 3600, "d": 86400}[unit]
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)

            def _check_duration(msg):
                if cur_msg_id is not None and msg.id == cur_msg_id:
                    return False
                if msg.created_at < cutoff:
                    return False
                return target is None or msg.author.id == target.id

            deleted = await ctx.channel.purge(limit=1000, check=_check_duration, bulk=True)
            label = f"{spec}"
        elif count_match:
            wanted = int(spec)
            if wanted <= 0:
                return await ctx.send("❌ Số lượng phải lớn hơn 0.", delete_after=5)
            counter = {"n": 0}

            def _check_count(msg):
                if (cur_msg_id is not None and msg.id == cur_msg_id) or counter["n"] >= wanted:
                    return False
                if target is not None and msg.author.id != target.id:
                    return False
                counter["n"] += 1
                return True

            deleted = await ctx.channel.purge(limit=1000, check=_check_count, bulk=True)
            label = str(wanted)
        else:
            return await ctx.send(
                "❌ Sai thời gian/số lượng. Ví dụ: `.clear 20`, `.clear @user 20`, `.clear 1h`, `.clear @user 1d`",
                delete_after=7
            )

        try:
            if getattr(ctx, "message", None):
                await ctx.message.delete()
        except Exception:
            pass

        who = f" của {target.mention}" if target else ""
        return await ctx.channel.send(f"🧹 Đã xóa **{len(deleted)}** tin nhắn{who} ({label}).", delete_after=4)
    except discord.Forbidden:
        return await ctx.send("❌ Bot không có quyền **Manage Messages** ở kênh này.", delete_after=6)
    except Exception as e:
        return await ctx.send(f"❌ Không thể clear: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name="clear")
async def cmd_clear(ctx, nguoi: Optional[discord.Member] = None, so_luong: str = None):
    """Xóa N tin nhắn hoặc tin nhắn trong một khoảng thời gian; có thể lọc theo @user."""
    if not _can_manage_messages(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if not so_luong:
        return await ctx.send(
            "❌ Cú pháp: `.clear [@user] <số>` hoặc `.clear [@user] <1p|1h|1d>`",
            delete_after=7
        )
    await _clear_core(ctx, nguoi, so_luong)


@bot.hybrid_command(name='delreply', aliases=['xtn'])
async def cmd_xtn(ctx):
    """Reply một tin nhắn rồi .xtn: xóa cả tin nhắn được reply và tin nhắn .xtn."""
    if not _can_manage_messages(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)

    ref = ctx.message.reference
    if not ref or not ref.message_id:
        return await ctx.send("❌ Hãy **reply** đúng tin nhắn cần xóa rồi dùng `.delreply`.", delete_after=6)

    try:
        await ctx.channel.get_partial_message(ref.message_id).delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        return await ctx.send("❌ Bot không có quyền xóa tin nhắn đó.", delete_after=6)
    except Exception:
        return await ctx.send("❌ Không tìm thấy/không xóa được tin nhắn được reply.", delete_after=6)

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.hybrid_command(name="addrole")
async def cmd_addrole(ctx, member: discord.Member = None, role: discord.Role = None):
    """`.addrole @user @role` — thêm role và tự xóa tin nhắn lệnh."""
    if not _can_manage_roles(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if member is None or role is None:
        return await ctx.send("❌ Cú pháp: `.addrole @user @role`", delete_after=6)
    if role.is_default():
        return await ctx.send("❌ Không thể thêm role @everyone.", delete_after=5)
    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return await ctx.send("❌ Bot chưa có quyền **Quản lý vai trò**.", delete_after=6)
    if role >= me.top_role:
        return await ctx.send("❌ Role này cao hơn hoặc ngang role cao nhất của bot.", delete_after=6)
    try:
        await member.add_roles(role, reason=f".addrole bởi {ctx.author}")
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ Bot không đủ quyền để thêm role này cho người đó.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Lỗi thêm role: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name="delrole")
async def cmd_delrole(ctx, member: discord.Member = None, role: discord.Role = None):
    """`.delrole @user @role` — gỡ role và tự xóa tin nhắn lệnh."""
    if not _can_manage_roles(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if member is None or role is None:
        return await ctx.send("❌ Cú pháp: `.delrole @user @role`", delete_after=6)
    if role.is_default():
        return await ctx.send("❌ Không thể gỡ role @everyone.", delete_after=5)

    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return await ctx.send("❌ Bot chưa có quyền **Quản lý vai trò**.", delete_after=6)
    if role >= me.top_role:
        return await ctx.send("❌ Role này cao hơn hoặc ngang role cao nhất của bot.", delete_after=6)

    try:
        await member.remove_roles(role, reason=f".delrole bởi {ctx.author}")
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ Bot không đủ quyền để gỡ role này khỏi người đó.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Lỗi gỡ role: `{type(e).__name__}`", delete_after=6)


# ================================================================
# 🔒 KÊNH CẤM BOT (.blockbot / .unblockbot)
#    Đặt QUYỀN RIÊNG CHO TỪNG BOT chỉ trong kênh này (View Channel = ❌).
#    Lưu SQLite để restart không mất.
#    Kênh bị block chỉ dùng được: .av  .afk  .name  (+ autoreply, + blockbot/unblockbot)
# ================================================================
BOT_BLOCKED_CHANNELS = {}
BLOCK_REASON = "KS-BOT: kênh cấm bot"

# Lệnh còn dùng được trong kênh bị block (tên gốc của command, dùng cho slash + check chung)
_BLOCKED_ALLOWED_NAMES = {"av", "nickname", "afk", "blockbot", "unblockbot"}
# Tên gõ ra khi dùng prefix (gồm cả alias)
_BLOCKED_ALLOWED_PREFIX = {
    "av", "avt", "nickname", "name", "afk",
    "blockbot", "clbot", "unblockbot", "dclbot",   # bắt buộc giữ để chủ còn mở lại được kênh
}


def _ensure_bot_control_table():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS bot_blocked_channels (guild_id INTEGER, channel_id INTEGER, PRIMARY KEY (guild_id, channel_id))")
    conn.commit()
    conn.close()


def persist_bot_block_channel(guild_id, channel_id):
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO bot_blocked_channels (guild_id, channel_id) VALUES (?, ?)", (guild_id, channel_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def unpersist_bot_block_channel(guild_id, channel_id):
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("DELETE FROM bot_blocked_channels WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def load_persisted_bot_blocks():
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT guild_id, channel_id FROM bot_blocked_channels")
        rows = cur.fetchall()
        conn.close()
        for gid, cid in rows:
            BOT_BLOCKED_CHANNELS.setdefault(gid, set()).add(cid)
    except Exception:
        pass


load_persisted_bot_blocks()
bot.blocked_channels = BOT_BLOCKED_CHANNELS  # cho các extension dùng chung


def is_bot_command_message(message):
    return (message.content or "").lstrip().startswith((".", "!"))


def _command_name_from_message(message):
    content = (message.content or "").lstrip()
    if not content.startswith((".", "!")):
        return ""
    return content[1:].split(None, 1)[0].lower() if content[1:].strip() else ""


@bot.check
async def _blocked_channel_gate(ctx):
    """Chặn mọi lệnh (kể cả slash và lệnh của cog) trong kênh bị block, trừ nhóm được phép."""
    if ctx.guild and ctx.channel.id in BOT_BLOCKED_CHANNELS.get(ctx.guild.id, set()):
        if ctx.command and ctx.command.name in _BLOCKED_ALLOWED_NAMES:
            return True
        if ctx.interaction:
            try:
                await ctx.send("🔒 Kênh này đã bị cấm bot, chỉ dùng được `/av` `/afk` `/nickname`.", ephemeral=True)
            except Exception:
                pass
        return False
    return True


@bot.event
async def on_command_error(ctx, error):
    # Im lặng với lệnh bị chặn / không tồn tại; in log các lỗi khác.
    if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
        return
    if hasattr(ctx.command, "on_error"):
        return
    print(f"[CommandError] {ctx.command}: {type(error).__name__}: {error}")


async def _hide_channel_from_bots(channel):
    """Ẩn kênh khỏi mọi bot khác (chỉ trong kênh này), chạy song song cho nhanh."""
    guild = channel.guild
    me = guild.me

    try:
        await channel.set_permissions(
            me, view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, manage_messages=True, reason=BLOCK_REASON,
        )
    except Exception:
        pass

    bots = [m for m in guild.members if m.bot and m.id != me.id]
    already = sum(1 for m in bots if channel.overwrites_for(m).view_channel is False)
    todo = [m for m in bots if channel.overwrites_for(m).view_channel is not False]

    async def _one(m):
        try:
            ow = channel.overwrites_for(m)
            ow.view_channel = False
            await channel.set_permissions(m, overwrite=ow, reason=BLOCK_REASON)
            return True
        except Exception:
            return False

    results = await asyncio.gather(*[_one(m) for m in todo]) if todo else []
    ok = sum(1 for r in results if r)
    return already + ok, len(results) - ok


async def _show_channel_to_bots(channel):
    """Gỡ quyền ẩn đã đặt ở kênh này cho các bot. Trả về số bot đã mở lại."""
    guild = channel.guild
    me = guild.me
    todo = [m for m in guild.members
            if m.bot and m.id != me.id and channel.overwrites_for(m).view_channel is False]

    async def _one(m):
        try:
            ow = channel.overwrites_for(m)
            ow.view_channel = None
            await channel.set_permissions(m, overwrite=(None if ow.is_empty() else ow), reason=BLOCK_REASON)
            return True
        except Exception:
            return False

    results = await asyncio.gather(*[_one(m) for m in todo]) if todo else []
    return sum(1 for r in results if r)


async def _delete_later(msg, seconds):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


@bot.hybrid_command(name='blockbot', aliases=['clbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_clbot(ctx, channel: discord.TextChannel = None):
    """Cấm bot khác nhìn/đọc kênh này."""
    target = channel or ctx.channel
    me = ctx.guild.me
    if me is None or not (me.guild_permissions.manage_roles or me.guild_permissions.manage_channels or me.guild_permissions.administrator):
        return await ctx.send("❌ KS-BOT thiếu quyền **Quản lý vai trò** (Manage Roles) để chỉnh quyền kênh.", delete_after=8)

    gid = ctx.guild.id if ctx.guild else 0
    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).add(target.id)
    persist_bot_block_channel(gid, target.id)

    msg = await ctx.send(f"🔒 Đang ẩn {target.mention} với bot khác ⏳")
    hidden, failed = await _hide_channel_from_bots(target)

    text = (
        f"🔒 Đã cấm bot ở {target.mention} — ẩn với **{hidden}** bot"
        + (f", không chỉnh được **{failed}** bot" if failed else "")
        + ".\nTrong kênh này chỉ còn `.av` `.afk` `.name` và autoreply. Dùng `.unblockbot` để mở lại."
    )
    try:
        await msg.edit(content=text)
    except Exception:
        await ctx.send(text)
    asyncio.create_task(_delete_later(msg, 15))


@bot.hybrid_command(name='unblockbot', aliases=['dclbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_dclbot(ctx, channel: discord.TextChannel = None):
    """Bỏ cấm bot trong kênh."""
    target = channel or ctx.channel
    gid = ctx.guild.id if ctx.guild else 0

    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).discard(target.id)
    unpersist_bot_block_channel(gid, target.id)

    msg = await ctx.send(f"🔓 Đang mở {target.mention} cho bot ⏳")
    restored = await _show_channel_to_bots(target)
    text = f"🔓 Đã mở lại {target.mention} — hiện lại kênh cho **{restored}** bot."
    try:
        await msg.edit(content=text)
    except Exception:
        await ctx.send(text)
    asyncio.create_task(_delete_later(msg, 10))


@cmd_clbot.error
async def cmd_clbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.blockbot`.", delete_after=5)


@cmd_dclbot.error
async def cmd_dclbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.unblockbot`.", delete_after=5)


@bot.listen("on_member_join")
async def _blockbot_new_bot_guard(member):
    """Bot mới được mời vào server → tự ẩn khỏi các kênh đang bị cấm bot."""
    if not member.bot or member.id == getattr(bot.user, "id", None):
        return
    for cid in list(BOT_BLOCKED_CHANNELS.get(member.guild.id, set())):
        ch = member.guild.get_channel(cid)
        if ch is None:
            continue
        try:
            ow = ch.overwrites_for(member)
            ow.view_channel = False
            await ch.set_permissions(member, overwrite=ow, reason=BLOCK_REASON)
        except Exception:
            pass


# ================================================================
# 🔐 KHÓA / MỞ KÊNH (.lock / .unlock) — không ai chat được trong kênh
# ================================================================
def _ensure_lock_table():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("CREATE TABLE IF NOT EXISTS channel_locks (guild_id INTEGER, channel_id INTEGER, prev INTEGER, PRIMARY KEY (guild_id, channel_id))")
    conn.commit()
    conn.close()


def _lock_get(guild_id, channel_id):
    try:
        _ensure_lock_table()
        conn = sqlite3.connect(DB_NAME)
        row = conn.execute("SELECT prev FROM channel_locks WHERE guild_id=? AND channel_id=?", (guild_id, channel_id)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _lock_set(guild_id, channel_id, prev):
    try:
        _ensure_lock_table()
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR IGNORE INTO channel_locks (guild_id, channel_id, prev) VALUES (?, ?, ?)", (guild_id, channel_id, prev))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _lock_del(guild_id, channel_id):
    try:
        _ensure_lock_table()
        conn = sqlite3.connect(DB_NAME)
        conn.execute("DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _bot_can_edit_perms(me):
    return me is not None and (me.guild_permissions.manage_roles or me.guild_permissions.manage_channels or me.guild_permissions.administrator)


@bot.hybrid_command(name='lock')
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_lock(ctx, channel: discord.TextChannel = None):
    """Khóa kênh: mọi người không chat được."""
    target = channel or ctx.channel
    if not _bot_can_edit_perms(ctx.guild.me):
        return await ctx.send("❌ KS-BOT thiếu quyền **Quản lý vai trò** (Manage Roles) để khóa kênh.", delete_after=8)

    everyone = ctx.guild.default_role
    ow = target.overwrites_for(everyone)
    if _lock_get(ctx.guild.id, target.id) is not None:
        return await ctx.send(f"🔒 {target.mention} đang bị khóa rồi.", delete_after=6)

    prev = -1 if ow.send_messages is None else (1 if ow.send_messages else 0)
    _lock_set(ctx.guild.id, target.id, prev)
    try:
        await target.set_permissions(ctx.guild.me, send_messages=True, reason=f"Lock bởi {ctx.author}")
        ow.send_messages = False
        await target.set_permissions(everyone, overwrite=ow, reason=f"Lock bởi {ctx.author}")
    except Exception as e:
        _lock_del(ctx.guild.id, target.id)
        return await ctx.send(f"❌ Không thể khóa kênh: `{type(e).__name__}`", delete_after=6)

    await ctx.send(f"🔒 Đã khóa {target.mention}. Dùng `.unlock` để mở.", delete_after=10)


@bot.hybrid_command(name='unlock')
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_unlock(ctx, channel: discord.TextChannel = None):
    """Mở khóa kênh."""
    target = channel or ctx.channel
    if not _bot_can_edit_perms(ctx.guild.me):
        return await ctx.send("❌ KS-BOT thiếu quyền **Quản lý vai trò** (Manage Roles) để mở khóa kênh.", delete_after=8)

    prev = _lock_get(ctx.guild.id, target.id)
    everyone = ctx.guild.default_role
    ow = target.overwrites_for(everyone)
    if prev is None and ow.send_messages is not False:
        return await ctx.send(f"🔓 {target.mention} không bị khóa.", delete_after=6)

    ow.send_messages = True if prev == 1 else (False if prev == 0 else None)
    try:
        await target.set_permissions(everyone, overwrite=(None if ow.is_empty() else ow), reason=f"Unlock bởi {ctx.author}")
        if target.id not in BOT_BLOCKED_CHANNELS.get(ctx.guild.id, set()):
            me_ow = target.overwrites_for(ctx.guild.me)
            me_ow.send_messages = None
            await target.set_permissions(ctx.guild.me, overwrite=(None if me_ow.is_empty() else me_ow), reason=f"Unlock bởi {ctx.author}")
    except Exception as e:
        return await ctx.send(f"❌ Không thể mở khóa kênh: `{type(e).__name__}`", delete_after=6)

    _lock_del(ctx.guild.id, target.id)
    await ctx.send(f"🔓 Đã mở khóa {target.mention}.", delete_after=10)


@cmd_lock.error
async def cmd_lock_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.lock`.", delete_after=5)


@cmd_unlock.error
async def cmd_unlock_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.unlock`.", delete_after=5)


# ================================================================
# 🤖 KÊNH RIÊNG CHO KS-BOT (.ksbotchannel)
# ================================================================
KS_BOT_CHANNELS = {}
KS_BOT_CHANNEL_FILE = "ksbot_channels.json"
try:
    with open(KS_BOT_CHANNEL_FILE, "r", encoding="utf-8") as _f:
        KS_BOT_CHANNELS = {int(k): int(v) for k, v in json.load(_f).items()}
except Exception:
    KS_BOT_CHANNELS = {}


def _save_ksbot_channels():
    try:
        with open(KS_BOT_CHANNEL_FILE, "w", encoding="utf-8") as _f:
            json.dump({str(k): int(v) for k, v in KS_BOT_CHANNELS.items()}, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@bot.hybrid_command(name='ksbotchannel', aliases=['kenhksbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_kenhksbot(ctx, channel: discord.TextChannel = None):
    """Chọn kênh riêng cho KS-BOT."""
    if channel is None:
        channel = ctx.channel
    KS_BOT_CHANNELS[ctx.guild.id] = channel.id
    _save_ksbot_channels()
    await ctx.send(f"🤖 {channel.mention} là kênh riêng của KS-BOT. Bot khác nhắn trong đây sẽ bị xóa.", delete_after=8)


@cmd_kenhksbot.error
async def cmd_kenhksbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.ksbotchannel`.", delete_after=5)


@bot.listen("on_message")
async def _ksbot_message_guard(message):
    if message.author == bot.user:
        return
    if message.guild and KS_BOT_CHANNELS.get(message.guild.id) == message.channel.id and message.author.bot:
        try:
            await message.delete()
        except Exception:
            pass
        return


# ================================================================
# 🧧 LÌ XÌ TOÀN SERVER (.lixichannel)
# ================================================================
LIXI_CLAIMED = {}
LIXI_EVENT_COUNTER = 0
LIXI_TASK_STARTED = False
LIXI_ACTIVE_EVENT = {}
LIXI_CHANNELS = {}
try:
    with open("lixi_channels.json", "r", encoding="utf-8") as _lf:
        for _gid, _cid in json.load(_lf).items():
            LIXI_CHANNELS[int(_gid)] = int(_cid)
except Exception:
    pass


def _save_lixi_channels():
    try:
        with open("lixi_channels.json", "w", encoding="utf-8") as _lf:
            json.dump({str(k): int(v) for k, v in LIXI_CHANNELS.items()}, _lf, ensure_ascii=False, indent=2)
    except Exception:
        pass


class LixiClaimView(discord.ui.View):
    def __init__(self, event_id, guild_id, amount):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.guild_id = guild_id
        self.amount = amount

    @discord.ui.button(label="🧧 NHẬN LÌ XÌ", style=discord.ButtonStyle.success, emoji="💰")
    async def claim(self, interaction, button):
        uid = str(interaction.user.id)
        if LIXI_ACTIVE_EVENT.get(self.guild_id) != self.event_id:
            return await interaction.response.send_message("⏰ Lì xì này đã hết hiệu lực.", ephemeral=True)
        claimed = LIXI_CLAIMED.setdefault(self.event_id, set())
        if interaction.user.id in claimed:
            return await interaction.response.send_message("❌ Bạn đã nhận lì xì đợt này rồi!", ephemeral=True)
        try:
            data = get_user(uid)
            update_user_full(uid, data[0] + self.amount, data[1], data[2], data[3], data[4], data[5])
            claimed.add(interaction.user.id)
            await interaction.response.send_message(f"🧧 **Bạn nhận được {self.amount:,} 🪙!**", ephemeral=True)
        except Exception as exc:
            print(f"[LIXI] {exc}")
            await interaction.response.send_message("❌ Không thể cộng tiền lúc này.", ephemeral=True)


async def lixi_server_loop():
    global LIXI_EVENT_COUNTER
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.randint(1200, 2400))
        for guild in list(bot.guilds):
            channel_id = LIXI_CHANNELS.get(guild.id)
            if not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None or channel.id in BOT_BLOCKED_CHANNELS.get(guild.id, set()):
                continue
            me = guild.me
            if me is None or not channel.permissions_for(me).send_messages:
                continue
            amount = random.randint(10000, 1000000)
            LIXI_EVENT_COUNTER += 1
            event_id = f"{guild.id}-{LIXI_EVENT_COUNTER}"
            LIXI_ACTIVE_EVENT[guild.id] = event_id
            LIXI_CLAIMED[event_id] = set()
            embed = discord.Embed(
                title="🧧 LÌ XÌ TOÀN SERVER ĐÃ XUẤT HIỆN!",
                description=(
                    f"🎉 **{amount:,} 🪙** đang chờ mọi người nhận!\n\n"
                    "👥 Mỗi người chỉ được nhận **1 lần** trong đợt này.\n"
                    "💰 Mỗi người nhận tối đa **1,000,000 🪙**.\n"
                    "⚡ Lì xì này không hết hạn — chỉ mất hiệu lực khi có đợt mới."
                ), color=0xe74c3c)
            try:
                await channel.send(content="🎊 **LÌ XÌ TOÀN SERVER!**", embed=embed,
                                   view=LixiClaimView(event_id, guild.id, amount))
            except Exception:
                pass


async def start_lixi_task_once():
    global LIXI_TASK_STARTED
    if not LIXI_TASK_STARTED:
        LIXI_TASK_STARTED = True
        asyncio.create_task(lixi_server_loop())


@bot.hybrid_command(name='lixichannel', aliases=['kenhlixi'])
async def cmd_kenhlixi(ctx, channel: discord.TextChannel = None):
    """Chọn đúng 1 kênh/server để phát lì xì."""
    if not _server_owner_only(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.lixichannel`.", delete_after=5)
    if channel is None:
        current = LIXI_CHANNELS.get(ctx.guild.id)
        if current:
            ch = ctx.guild.get_channel(current)
            if ch:
                return await ctx.send(f"🧧 Kênh lì xì hiện tại: {ch.mention}", delete_after=7)
        return await ctx.send("❌ Cú pháp: `.lixichannel #kênh` (hoặc `.kenhlixi`)", delete_after=6)

    LIXI_CHANNELS[ctx.guild.id] = channel.id
    _save_lixi_channels()
    await ctx.send(f"🧧 Lì xì sẽ phát tại {channel.mention}.", delete_after=8)


# ================================================================
# 📖 HELP — hàng nút, có GIF, không dropdown
# ================================================================
_DIV = "━━━━━━━━━━━━━━━━━━━━"

HELP_PAGE_ORDER = [
    ("home", "🏠"), ("werewolf", "🐺"), ("games", "🎮"), ("economy", "💰"),
    ("admin", "🛡️"), ("utility", "✨"), ("welcome", "👋"),
]


def help_pages_v2():
    home = discord.Embed(color=C_INFO)
    home.set_author(name="KS-BOT  •  Bảng lệnh")
    home.description = (
        "```ini\n[ prefix ] .    [ slash ] /\n```"
        "Bấm **nút bên dưới** để chuyển trang.\n"
        "🐺 Ma Sói · 🎮 Game · 💰 Kinh tế\n"
        "🛡️ Quản trị · ✨ Tiện ích · 👋 Chào & Reply\n\n"
        "**Hay dùng nhất**\n"
        "> `.coin` ví · `.daily` lương · `.masoi` Ma Sói\n"
        "> `.baucua` `.sicbo` `.mine` `.rps` chơi game"
    )
    home.set_image(url=HELP_GIF)
    home.set_footer(text="Nút xanh = trang đang xem • ✖️ để đóng")

    werewolf = discord.Embed(title="🐺 MA SÓI", description=_DIV, color=0x8E44AD)
    werewolf.add_field(
        name="📋 Lệnh",
        value=(
            "`.masoi` `/masoi` — Tạo phòng chơi\n"
            "`.masoiroles` `/masoiroles` — Xem bộ vai trò\n"
            "`.mav` `/mav` — Trưởng Làng tự công khai"
        ),
        inline=False
    )
    werewolf.add_field(
        name="🎭 Vai trò đặc biệt",
        value=(
            "💘 **Cupid** — hai người yêu biết nhau\n"
            "🛡️ **Bảo Vệ** — không bảo vệ cùng 1 người 3 đêm liền\n"
            "🧪 **Phù Thủy** — Cứu / Độc\n"
            "🏹 **Thợ Săn** — bắn 1 phát khi chết\n"
            "👑 **Trưởng Làng** — mỗi đêm chọn lộ diện hoặc ẩn danh"
        ),
        inline=False
    )

    games = discord.Embed(title="🎮 MINI GAME", description=_DIV, color=C_OK)
    games.add_field(name="🦀 Bầu Cua", value="`.baucua` `/baucua`\nBầu Cua Tôm Cá", inline=True)
    games.add_field(name="🎲 Tài Xỉu", value="`.sicbo` `/sicbo`\nalias: `taixiu`", inline=True)
    games.add_field(name="💣 Mines", value="`.mine` `/mine`\n`<tiền/all> [boom]`", inline=True)
    games.add_field(name="✂️ Kéo Búa Bao", value="`.rps` `/rps`\nalias: `tuxi` • `<tiền/all>`", inline=True)

    economy = discord.Embed(title="💰 KINH TẾ", description=_DIV, color=C_GOLD)
    economy.add_field(name="👛 Ví", value="`.coin` `/coin`\nXem số dư", inline=True)
    economy.add_field(name="📅 Lương", value="`.daily` `/daily`\nNhận mỗi ngày", inline=True)
    economy.add_field(name="🏆 Xếp hạng", value="`.top` `/top`\nBảng đại gia", inline=True)
    economy.add_field(name="💸 Chuyển coin", value="`.pay @user <tiền/all>` `/pay`", inline=False)
    economy.add_field(
        name="🧧 Lì xì server",
        value="Xuất hiện ngẫu nhiên • Mỗi người nhận **1 lần/đợt** • Tối đa **1,000,000** 🪙 • Đợt mới ra thì đợt cũ hết hạn.",
        inline=False
    )

    admin = discord.Embed(
        title="🛡️ QUẢN TRỊ",
        description=f"🔒 **Chỉ Owner Bot hoặc Chủ Server dùng được.**\n{_DIV}",
        color=C_ERR
    )
    admin.add_field(
        name="🔇 Xử lý thành viên",
        value=(
            "`.gag @user` — Khóa mõm 5 phút (`khoamom`)\n"
            "`.mute @user <10p/1h/1d>` — Khóa chat (`khoachat`, `timeout`)\n"
            "`.unmute @user` — Gỡ mute (`ungag`, `unkhoamom`)\n"
            "`.kick @user [lý do]` — Kick khỏi server\n"
            "`.ban @user [lý do]` — Ban khỏi server\n"
            "`.unban <user_id>` — Gỡ ban theo ID"
        ),
        inline=False
    )
    admin.add_field(
        name="🧹 Kênh & tin nhắn",
        value=(
            "`.clear [@user] <số|1p|1h|1d>` — Xóa tin nhắn\n"
            "`.delreply` — Reply rồi xóa cả 2 tin (`xtn`)\n"
            "`.blockbot [#kênh]` — **Ẩn kênh với bot khác** (`clbot`)\n"
            "`.unblockbot [#kênh]` — Mở lại kênh (`dclbot`)\n"
            "`.ksbotchannel #kênh` — Kênh riêng KS-BOT (`kenhksbot`)\n"
            "`.lixichannel #kênh` — Kênh phát lì xì (`kenhlixi`)"
        ),
        inline=False
    )
    admin.add_field(
        name="🔐 Khóa kênh",
        value=(
            "`.lock [#kênh]` `/lock` — 🔒 Khóa kênh, không ai chat được\n"
            "`.unlock [#kênh]` `/unlock` — 🔓 Mở khóa, chat lại bình thường"
        ),
        inline=False
    )
    admin.add_field(
        name="🎭 Vai trò",
        value=(
            "`.addrole @user @role` — Thêm role\n"
            "`.delrole @user @role` — Gỡ role"
        ),
        inline=False
    )
    admin.set_footer(text="👑 Owner Bot luôn bypass mọi giới hạn")

    utility = discord.Embed(title="✨ TIỆN ÍCH", description=_DIV, color=0x9B59B6)
    utility.add_field(name="🖼️ Avatar", value="`.av [@user]` `/av`", inline=True)
    utility.add_field(name="✏️ Đổi tên", value="`.nickname <tên>`\nalias: `name`", inline=True)
    utility.add_field(name="💤 AFK", value="`.afk [lý do]` `/afk`", inline=True)
    utility.add_field(name="📖 Trợ giúp", value="`.help` `/help`", inline=True)
    utility.add_field(name="👛 Ví", value="`.coin` `/coin`", inline=True)
    utility.add_field(name="📅 Lương", value="`.daily` `/daily`", inline=True)
    utility.add_field(
        name="🔒 Ghi chú",
        value=(
            "Kênh bị `.blockbot` chỉ còn dùng được `.av` `.afk` `.name` và **autoreply**. "
            "Người đang AFK bị ping/reply thì bot vẫn báo lý do."
        ),
        inline=False
    )

    welcome = discord.Embed(
        title="👋 CHÀO THÀNH VIÊN & TỰ TRẢ LỜI",
        description=f"🔑 **Admin / Mod có quyền quản lý máy chủ dùng được.**\n{_DIV}",
        color=C_INFO
    )
    welcome.add_field(
        name="👋 Lời chào thành viên mới",
        value=(
            "`.welcome` `/welcome` — Xem cài đặt\n"
            "`.welcome channel #kênh` — Đặt kênh chào\n"
            "`.welcome message <chữ>` — Dòng chữ trên cùng\n"
            "`.welcome text <chữ>` — Nội dung nhiều chữ trong embed\n"
            "`.welcome image <link>` — Ảnh/GIF lớn ở dưới (`off` để xóa)\n"
            "`.welcome color <#hex>` — Màu embed\n"
            "`.welcome dm on|off` — Gửi thêm vào tin nhắn riêng\n"
            "`.welcome toggle on|off` — Bật/tắt\n"
            "`.welcome test` — Xem thử ngay\n"
            "`.welcome reset` — Về mặc định"
        ),
        inline=False
    )
    welcome.add_field(
        name="🏷️ Biến dùng trong chữ",
        value="`{user}` tag người mới • `{name}` tên • `{server}` tên server • `{count}` số thành viên • `\\n` xuống dòng",
        inline=False
    )
    welcome.add_field(
        name="💬 Tự trả lời (bot reply + ping)",
        value=(
            "`.autoreply add ai hỏi | tao hỏi` — Tin **chứa** câu đó → bot reply\n"
            "`.autoreply addexact =)) | =))` — Tin phải **y hệt** mới reply\n"
            "`.autoreply` — Xem danh sách • `.autoreply remove <số>` — Xóa\n"
            "`.autoreply clear` — Xóa hết (alias: `trl`)"
        ),
        inline=False
    )

    return {
        "home": home, "werewolf": werewolf, "games": games,
        "economy": economy, "admin": admin, "utility": utility,
        "welcome": welcome,
    }


class HelpNavView(discord.ui.View):
    def __init__(self, author_id, current="home"):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.message = None
        self._build(current)

    def _build(self, current):
        self.clear_items()
        for i, (key, emoji) in enumerate(HELP_PAGE_ORDER):
            btn = discord.ui.Button(
                emoji=emoji, row=i // 5,
                style=discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_cb(key)
            self.add_item(btn)
        close = discord.ui.Button(emoji="✖️", style=discord.ButtonStyle.danger, row=1)
        close.callback = self._close
        self.add_item(close)

    def _make_cb(self, key):
        async def cb(interaction):
            self._build(key)
            await interaction.response.edit_message(embed=help_pages_v2()[key], view=self)
        return cb

    async def _close(self, interaction):
        await interaction.response.edit_message(view=None)
        self.stop()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Menu của người khác, gõ `.help` để mở menu riêng nhé.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


@bot.hybrid_command(name="help")
async def cmd_help_v2(ctx):
    """Mở menu trợ giúp."""
    view = HelpNavView(ctx.author.id)
    view.message = await ctx.send(embed=help_pages_v2()["home"], view=view)


# ==========================================
# SỰ KIỆN: on_member_update / on_message / on_ready
# ==========================================
@bot.event
async def on_member_update(before, after):
    # Giữ tên bot cố định
    if after.id == bot.user.id and (before.nick != after.nick or before.name != after.name):
        try:
            if after.guild.me.nick != BOT_ORIGINAL_NAME:
                await after.guild.me.edit(nick=BOT_ORIGINAL_NAME)
        except Exception:
            pass
        return

    # Owner Guard — nếu Owner bị timeout/mute thì bot tự gỡ im lặng.
    if after.id == OWNER_USER_ID and after.guild:
        try:
            if getattr(after, "timed_out_until", None) is not None:
                me = after.guild.me
                if me and me.guild_permissions.moderate_members:
                    await after.timeout(None, reason="Owner Guard — tự động gỡ mute Owner")
        except Exception:
            pass


@bot.event
async def on_message(message):
    # ===== KÊNH BỊ CẤM BOT =====
    # Chỉ chạy: AFK + lệnh .av/.afk/.name (+ blockbot/unblockbot). Không EXP, không streak, không Ma Sói.
    # Autoreply là cog riêng nghe on_message qua listener nên không bị chặn ở đây.
    if message.guild and message.channel.id in BOT_BLOCKED_CHANNELS.get(message.guild.id, set()):
        if message.author.bot:
            # Dự phòng: bot có quyền Administrator vẫn thấy kênh → xóa tin của nó.
            if message.author.id != getattr(bot.user, "id", None):
                try:
                    await message.delete()
                except Exception:
                    pass
            return
        await _afk_handle(message)
        if is_bot_command_message(message) and _command_name_from_message(message) in _BLOCKED_ALLOWED_PREFIX:
            await bot.process_commands(message)
        return

    if message.author.bot:
        return

    uid_str = str(message.author.id)
    ch_id = message.channel.id

    # Cộng EXP tự động cho mọi tin nhắn
    add_exp_to_user(uid_str, 5)

    # Chống lộ vai trò Ma Sói trong kênh chung
    game = bot.game_lobbies.get(ch_id)
    if game and message.author.id in game.players and game.players[message.author.id]["alive"]:
        content_lower = message.content.lower()
        reveal_keywords = ["tao là sói", "tôi là sói", "mình là sói", "tao tiên tri", "tôi là tiên tri", "mình tiên tri", "tao bảo vệ", "tôi là bảo vệ", "mình bảo vệ", "tao dân", "tôi là dân"]
        if any(kw in content_lower for kw in reveal_keywords):
            try:
                await message.delete()
                warning = await message.channel.send(f"⚠️ {message.author.mention} Không được phép tiết lộ vai trò trong kênh chung!")
                await asyncio.sleep(4)
                await warning.delete()
            except Exception:
                pass

    await _afk_handle(message)
    await process_user_streak_and_nickname(message.author)
    await bot.process_commands(message)


@bot.event
async def on_ready():
    print(f"🤖 BOT {BOT_ORIGINAL_NAME} ĐÃ KHỞI ĐỘNG THÀNH CÔNG VỚI PREFIX '.' !")

    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="với lệnh .help")
    )

    try:
        for guild in bot.guilds:
            if guild.me.nick != BOT_ORIGINAL_NAME:
                await guild.me.edit(nick=BOT_ORIGINAL_NAME)
    except Exception:
        pass

    await start_lixi_task_once()

    try:
        synced = await bot.tree.sync()
        print(f"✅ Đã đồng bộ {len(synced)} slash command.")
    except Exception as e:
        print(f"⚠️ Lỗi đồng bộ slash command: {e}")


# ================================================================
# 🔗 KẾT NỐI VỚI DASHBOARD (web.py) — stats.json / activity.json / outbox.json
# ================================================================
try:
    import psutil
except ImportError:
    psutil = None

DASHBOARD_STATS_FILE = "stats.json"
DASHBOARD_ACTIVITY_FILE = "activity.json"
DASHBOARD_OUTBOX_FILE = "outbox.json"
DASHBOARD_ACTIVITY_MAX = 20
DASHBOARD_ACTIVITY_LOCK = threading.Lock()


def _dashboard_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


async def dashboard_stats_loop():
    """Ghi số server/thành viên/RAM/ping thật ra stats.json mỗi 15 giây."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            guild_list = [
                {
                    "id": str(g.id),
                    "name": g.name,
                    "members": g.member_count,
                    "icon": str(g.icon.replace(size=64).url) if g.icon else None,
                    "channels": [
                        {"id": str(c.id), "name": c.name}
                        for c in g.text_channels
                        if g.me is not None and c.permissions_for(g.me).send_messages
                    ][:100],
                }
                for g in sorted(bot.guilds, key=lambda g: g.member_count or 0, reverse=True)[:60]
            ]
            ram_mb = None
            if psutil is not None:
                ram_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
            latency = bot.latency
            ping_ms = round(latency * 1000) if latency == latency else None
            _dashboard_write_json(DASHBOARD_STATS_FILE, {
                "guilds": len(bot.guilds),
                "members": sum(g.member_count or 0 for g in bot.guilds),
                "ram_mb": ram_mb,
                "ping_ms": ping_ms,
                "updated_at": time.time(),
                "guild_list": guild_list,
                "bot_name": bot.user.name if bot.user else None,
                "bot_avatar": str(bot.user.display_avatar.replace(size=128).url) if bot.user else None,
            })
        except Exception as e:
            print(f"[Dashboard] Lỗi ghi stats.json: {e}")
        await asyncio.sleep(15)


def dashboard_log_activity(user, action):
    """Ghi 1 dòng log lệnh vào activity.json (giữ 20 dòng gần nhất)."""
    try:
        with DASHBOARD_ACTIVITY_LOCK:
            items = []
            if os.path.exists(DASHBOARD_ACTIVITY_FILE):
                try:
                    with open(DASHBOARD_ACTIVITY_FILE, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    if not isinstance(items, list):
                        items = []
                except Exception:
                    items = []
            items.append({"time": time.time(), "user": str(user), "action": str(action)})
            items = items[-DASHBOARD_ACTIVITY_MAX:]
            _dashboard_write_json(DASHBOARD_ACTIVITY_FILE, items)
    except Exception as e:
        print(f"[Dashboard] Lỗi ghi activity.json: {e}")


@bot.event
async def on_command_completion(ctx):
    dashboard_log_activity(ctx.author, ctx.message.content if ctx.message else ctx.command)


def _dashboard_error_text(e):
    text = str(e)
    code = getattr(e, "code", None)
    status_code = getattr(e, "status", None)
    if status_code == 429 or "cloudflare" in text.lower():
        return "Discord đang chặn/giới hạn IP của host (429), thử lại sau ít phút"
    if code == 10003:
        return "Không tìm thấy kênh: ID sai hoặc bot không ở server có kênh này"
    if code == 50001:
        return "Bot không có quyền xem kênh này (Missing Access)"
    if code == 50013:
        return "Bot thiếu quyền Gửi tin nhắn trong kênh này (Missing Permissions)"
    if code == 50035:
        return "Nội dung tin nhắn không hợp lệ (quá dài hoặc trống)"
    if isinstance(e, AttributeError) and "send" in text:
        return "ID này không phải kênh chữ (có thể là danh mục, kênh thoại hoặc ID khác)"
    if isinstance(e, ValueError):
        return "ID kênh không hợp lệ"
    return (type(e).__name__ + ": " + text).replace("\n", " ")[:160]


async def dashboard_outbox_loop():
    """Đọc outbox.json do dashboard ghi, gửi tin thật qua Discord."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if os.path.exists(DASHBOARD_OUTBOX_FILE):
                with open(DASHBOARD_OUTBOX_FILE, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
                if isinstance(jobs, list):
                    changed = False
                    for job in jobs:
                        if job.get("status") != "pending":
                            continue
                        try:
                            cid = int(job["channel_id"])
                            channel = bot.get_channel(cid) or await bot.fetch_channel(cid)
                            await channel.send(job["content"])
                            job["status"] = "sent"
                            job.pop("error", None)
                        except Exception as e:
                            job["status"] = "error"
                            job["error"] = _dashboard_error_text(e)
                            print(f"[Dashboard] Gửi tin thất bại: {job['error']}")
                        changed = True
                    if changed:
                        _dashboard_write_json(DASHBOARD_OUTBOX_FILE, jobs)
        except Exception as e:
            print(f"[Dashboard] Lỗi xử lý outbox.json: {e}")
        await asyncio.sleep(5)


# ==========================================
# KHỞI CHẠY BOT
# ==========================================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Thiếu biến môi trường DISCORD_TOKEN. Hãy đặt token vào Environment "
        "của hosting — không hardcode token trong code."
    )


async def main():
    async with bot:
        # --- extension có sẵn của bạn ---
        await bot.load_extension("discord_storage")
        await bot.load_extension("noitu")
        await bot.load_extension("lixi_admin")
        await bot.load_extension("owner_mute")
        await bot.load_extension("status")
        await bot.load_extension("server_manager")
        await bot.load_extension("fun")
        await bot.load_extension("pingbot")
        await bot.load_extension("gif")
        await bot.load_extension("dashboard_bridge")
        # --- game mới tách ra ---
        await bot.load_extension("casino")
        await bot.load_extension("top")
        await bot.load_extension("masoi")
        await bot.load_extension("welcome")
        await bot.load_extension("autoreply")

        asyncio.create_task(dashboard_stats_loop())
        asyncio.create_task(dashboard_outbox_loop())
        try:
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            if e.status == 429:
                import sys as _sys
                print("[RateLimit] Discord/Cloudflare đang chặn IP host (429). Thoát mã 42 để chờ lâu rồi thử lại.")
                _sys.exit(42)
            raise


asyncio.run(main())
