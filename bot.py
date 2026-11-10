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
AFK_USERS = {}

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
# ĐỔI BIỆT DANH (.name)
# ==========================================
@bot.hybrid_command(name='nickname', aliases=['name'])
async def cmd_name(ctx, *, new_nick: str = None):
    """Đổi biệt danh của bạn."""
    if not new_nick:
        await ctx.send("❌ Cú pháp: `.nickname <tên_mới>` (hoặc `.name`)", delete_after=5)
        return

    if len(new_nick) > 32:
        await ctx.send("❌ Biệt danh không được vượt quá 32 ký tự!", delete_after=5)
        return

    try:
        if ctx.author == ctx.guild.owner:
            await ctx.send("❌ Bot không thể đổi biệt danh của Chủ Server!", delete_after=5)
            return

        await ctx.author.edit(nick=new_nick, reason="Người dùng đổi tên bằng lệnh .name")
        await ctx.send(f"✅ Đã đổi biệt danh thành công thành: **{new_nick}**!")
        add_exp_to_user(ctx.author.id, 10)
    except Exception as e:
        await ctx.send(f"❌ Không thể đổi biệt danh: {e}", delete_after=6)


# ==========================================
# AFK (.afk)
# ==========================================
@bot.hybrid_command(name='afk')
async def cmd_afk(ctx, *, reason: str = None):
    """Bật chế độ AFK."""
    uid_str = str(ctx.author.id)
    old_nick = ctx.author.nick or ctx.author.name

    AFK_USERS[uid_str] = {
        "reason": reason if reason else "thí chủ đi vắng rồi đừng gọi",
        "old_nick": old_nick
    }

    try:
        if ctx.author != ctx.guild.owner and ctx.guild.me.guild_permissions.manage_nicknames:
            clean_name = old_nick
            if "[AFK]" not in clean_name:
                new_afk_nick = f"[AFK] {clean_name}"
                if len(new_afk_nick) <= 32:
                    await ctx.author.edit(nick=new_afk_nick, reason="Bật chế độ AFK")
    except Exception:
        pass

    display_reason = reason if reason else "thí chủ đi vắng rồi đừng gọi"
    await ctx.send(f"💤 **{ctx.author.display_name}** đã bật trạng thái AFK. Lý do: *{display_reason}*")


# ==========================================
# KINH TẾ: PAY / AV / COIN / DAILY / TOP
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

    embed = discord.Embed(
        title="💸 CHUYỂN TIỀN THÀNH CÔNG",
        description=f"📤 **{ctx.author.mention}** đã chuyển **+{amount:,}** {COIN_EMOJI} cho **{member.mention}**!",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)


@bot.hybrid_command(name='av', aliases=['avt'])
async def cmd_av(ctx, member: discord.Member = None):
    """Xem ảnh đại diện."""
    target = member or ctx.author
    embed = discord.Embed(
        title=f"🖼️ MẶT TIỀN CỦA {target.display_name.upper()}",
        description=f"🔗 [Lưu ảnh HD]({target.display_avatar.url})",
        color=MAIN_COLOR
    )
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='coin', aliases=['wallet'])
async def cmd_coin(ctx):
    """Xem số dư coin."""
    u_data = get_user(str(ctx.author.id))
    embed = discord.Embed(
        title=f"💰 KÉT SẮT CÁ NHÂN: {ctx.author.name}",
        description=f"💵 Coin: **{u_data[0]:,}** {COIN_EMOJI}\n⭐ Level: `Lv.{u_data[1]}` (EXP: {u_data[2]})\n🔥 Streak hoạt động: **{u_data[3]} ngày**",
        color=MAIN_COLOR
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


@bot.hybrid_command(name='daily')
async def cmd_daily(ctx):
    """Nhận lương hằng ngày."""
    uid = str(ctx.author.id)
    now = time.time()
    if uid in DIEM_DANH_TRACKER and now - DIEM_DANH_TRACKER[uid] < 20 * 3600:
        await ctx.send("Mới lấy lương hồi nãy! Mai quay lại.", delete_after=5)
        return
    DIEM_DANH_TRACKER[uid] = now
    u_data = get_user(uid)
    update_user_full(uid, u_data[0] + 1500, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
    add_exp_to_user(uid, 100)
    await ctx.send(f"🎁 Nhận lương thành công **+1,500** {COIN_EMOJI}!")


@bot.hybrid_command(name='top')
async def cmd_top(ctx):
    """Xem bảng xếp hạng đại gia."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, coin, streak FROM users ORDER BY coin DESC LIMIT 10')
    rows = cursor.fetchall()
    conn.close()
    desc = ""
    for idx, (u, c, s) in enumerate(rows, 1):
        desc += f"`Top {idx}` <@!{u}>: `{c:,}` {COIN_EMOJI} | Streak: **{s}🔥**\n"
    embed = discord.Embed(title="🏆 BẢNG PHONG THẦN ĐẠI GIA", description=desc, color=MAIN_COLOR)
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
        await ctx.send(f"🤐 Đã khóa mõm **{member.mention}** trong **5 phút**!")
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
        await ctx.send(f"🔊 Đã gỡ mute cho **{member.mention}**!")
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
        await ctx.send(f"🔇 Đã khóa chat {member.mention} trong **{time_str}**.", delete_after=5)
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
        await ctx.send(f"👢 Đã kick **{member}**.", delete_after=5)
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
        await ctx.send(f"🔨 Đã ban **{member}**.", delete_after=5)
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
        await ctx.send(f"🔓 Đã unban ID `{user_id}`.", delete_after=5)
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
# 🔒 KÊNH CẤM BOT (.blockbot / .unblockbot) — lưu SQLite
# ================================================================
BOT_BLOCKED_CHANNELS = {}


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


def is_bot_command_message(message):
    return (message.content or "").lstrip().startswith((".", "!"))


@bot.hybrid_command(name='blockbot', aliases=['clbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_clbot(ctx, channel: discord.TextChannel = None):
    """Cấm lệnh bot & xóa bot khác trong kênh."""
    gid = ctx.guild.id if ctx.guild else 0
    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).add(ctx.channel.id)
    persist_bot_block_channel(gid, ctx.channel.id)  # bản cũ quên lưu → restart là mất
    embed = discord.Embed(
        title="🔒 ĐÃ CẤM LỆNH BOT",
        description=(
            f"Kênh {ctx.channel.mention} đã được khóa lệnh bot.\n\n"
            "• Lệnh `.` / `!` của bot sẽ không được xử lý tại đây.\n"
            "• Tin nhắn của **bot khác** gửi vào đây sẽ tự động bị xóa.\n"
            "• Dùng `.unblockbot` để mở lại."
        ), color=0xe74c3c)
    await ctx.send(embed=embed, delete_after=8)


@bot.hybrid_command(name='unblockbot', aliases=['dclbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_dclbot(ctx, channel: discord.TextChannel = None):
    """Bỏ cấm lệnh bot trong kênh."""
    target = channel or ctx.channel
    gid = ctx.guild.id if ctx.guild else 0
    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).discard(target.id)
    unpersist_bot_block_channel(gid, target.id)
    embed = discord.Embed(
        title="🔓 ĐÃ BỎ CẤM BOT",
        description=f"Kênh {target.mention} đã được mở lại cho các lệnh bot.",
        color=0x2ecc71)
    await ctx.send(embed=embed, delete_after=6)


@cmd_clbot.error
async def cmd_clbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.blockbot`.", delete_after=5)


@cmd_dclbot.error
async def cmd_dclbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.unblockbot`.", delete_after=5)


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
    await ctx.send(f"✅ Đã đặt {channel.mention} là **kênh của KS-BOT**. Bot khác nhắn trong đây sẽ bị xóa.", delete_after=6)


@cmd_kenhksbot.error
async def cmd_kenhksbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.ksbotchannel`.", delete_after=5)


@bot.listen("on_message")
async def _ksbot_message_guard(message):
    if message.author == bot.user:
        return
    # Bot khác nhắn trong kênh KS-BOT -> xóa ngay.
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
    await ctx.send(
        f"🧧 **ĐÃ ĐẶT KÊNH LÌ XÌ**\n📍 Chỉ phát lì xì tại {channel.mention}\n"
        "🔒 Bot sẽ không tự chọn kênh khác nữa.",
        delete_after=8
    )


# ================================================================
# 📖 HELP V2
# ================================================================
class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🏠 Trang chính", value="home", description="Tổng quan bot & cách dùng"),
            discord.SelectOption(label="🐺 Ma Sói", value="werewolf", description="Phòng, vai trò, manh mối"),
            discord.SelectOption(label="🎮 Mini Game", value="games", description="Bầu Cua, Tài Xỉu, Mines, Tuxi"),
            discord.SelectOption(label="💰 Kinh Tế", value="economy", description="Coin, daily, pay, top"),
            discord.SelectOption(label="🛡️ Quản Trị", value="admin", description="Mute, khóa bot, quản lý"),
            discord.SelectOption(label="✨ Tiện Ích", value="utility", description="Avatar, AFK, đổi tên, lì xì"),
        ]
        super().__init__(placeholder="Chọn mục trợ giúp", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        pages = help_pages_v2()
        embed = pages.get(self.values[0], pages["home"])
        embed.set_footer(text="💡 Dùng .help hoặc /help để mở lại menu • Một số lệnh cần quyền phù hợp")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpHomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(HelpCategorySelect())


def help_pages_v2():
    home = discord.Embed(
        title="🤖  K S - B O T   •   T R U N G   T Â M   T R Ợ   G I Ú P",
        description=(
            "Chào mừng! Chọn **danh mục** ở menu ⬇️ bên dưới để xem chi tiết từng lệnh.\n"
            "```⚡ Prefix : .        🆕 Slash : /```"
        ),
        color=0x5865F2
    )
    home.add_field(name="🐺 Ma Sói", value="Phòng chơi, vai trò, sự kiện", inline=True)
    home.add_field(name="🎮 Mini Game", value="Bầu Cua, Tài Xỉu, Mines…", inline=True)
    home.add_field(name="💰 Kinh Tế", value="Ví, lương, chuyển coin", inline=True)
    home.add_field(name="🛡️ Quản Trị", value="🔒 Chỉ Owner Bot / Chủ Server", inline=True)
    home.add_field(name="✨ Tiện Ích", value="Avatar, đổi tên, AFK…", inline=True)
    home.add_field(name="\u200b", value="\u200b", inline=True)
    home.add_field(
        name="💡 Mẹo nhanh",
        value=(
            "• Gõ `/` rồi chọn lệnh — Discord tự gợi ý cú pháp.\n"
            "• Một số lệnh `.` có thêm alias để gõ tắt.\n"
            "• Ví dụ: `.coin` · `.masoi` · `/help`"
        ),
        inline=False
    )
    home.set_footer(text="KS-BOT  •  Chọn mục bên dưới để xem thêm ✨")

    werewolf = discord.Embed(title="🐺  M A   S Ó I", color=0x8E44AD)
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

    games = discord.Embed(title="🎮  M I N I   G A M E", color=0x2ECC71)
    games.add_field(name="🦀 `.baucua` `/baucua`", value="Bầu Cua Tôm Cá", inline=True)
    games.add_field(name="🎲 `.sicbo` `/sicbo`", value="Tài Xỉu (alias: `taixiu`)", inline=True)
    games.add_field(name="💣 `.mine` `/mine`", value="Mines — `<tiền/all> [boom]`", inline=True)
    games.add_field(name="✂️ `.rps` `/rps`", value="Kéo Búa Bao (alias: `tuxi`) — `<tiền/all>`", inline=True)

    economy = discord.Embed(title="💰  K I N H   T Ế", color=0xF1C40F)
    economy.add_field(name="👛 `.coin` `/coin`", value="Xem số dư ví", inline=True)
    economy.add_field(name="📅 `.daily` `/daily`", value="Nhận lương hằng ngày", inline=True)
    economy.add_field(name="🏆 `.top` `/top`", value="Bảng xếp hạng đại gia", inline=True)
    economy.add_field(name="💸 `.pay` `/pay`", value="`@user <tiền/all>` — Chuyển coin", inline=False)
    economy.add_field(
        name="🧧 Lì xì server",
        value="Xuất hiện ngẫu nhiên toàn server • Mỗi người nhận **1 lần/đợt** • Tối đa **1,000,000** 🪙 • Đợt mới ra thì đợt cũ hết hạn.",
        inline=False
    )

    admin = discord.Embed(
        title="🛡️  Q U Ả N   T R Ị",
        description="🔒 **Toàn bộ lệnh dưới đây chỉ dùng được bởi Owner Bot hoặc Chủ Server.**",
        color=0xE74C3C
    )
    admin.add_field(
        name="🔇 Xử lý thành viên",
        value=(
            "`.gag @user` `/gag` — Khóa mõm 5 phút (alias: `khoamom`)\n"
            "`.mute @user <10p/1h/1d>` `/mute` — Khóa chat (alias: `khoachat`, `timeout`)\n"
            "`.unmute @user` `/unmute` — Gỡ mute/khóa mõm (alias: `ungag`, `unkhoamom`)\n"
            "`.kick @user [lý do]` `/kick` — Kick khỏi server\n"
            "`.ban @user [lý do]` `/ban` — Ban khỏi server\n"
            "`.unban <user_id>` `/unban` — Gỡ ban theo ID"
        ),
        inline=False
    )
    admin.add_field(
        name="🧹 Kênh & tin nhắn",
        value=(
            "`.clear [@user] <số|1p|1h|1d>` `/clear` — Xóa tin nhắn\n"
            "`.delreply` `/delreply` — Reply rồi xóa cả 2 tin (alias: `xtn`)\n"
            "`.blockbot [#kênh...]` `/blockbot` — Cấm lệnh bot & xóa bot khác trong kênh (alias: `clbot`)\n"
            "`.unblockbot [#kênh...]` `/unblockbot` — Bỏ cấm (alias: `dclbot`)\n"
            "`.ksbotchannel #kênh` `/ksbotchannel` — Đặt kênh riêng cho KS-BOT (alias: `kenhksbot`)\n"
            "`.lixichannel #kênh` `/lixichannel` — Chọn kênh phát lì xì (alias: `kenhlixi`)"
        ),
        inline=False
    )
    admin.add_field(
        name="🎭 Vai trò",
        value=(
            "`.addrole @user @role` `/addrole` — Thêm role\n"
            "`.delrole @user @role` `/delrole` — Gỡ role"
        ),
        inline=False
    )
    admin.set_footer(text="👑 Owner Bot luôn bypass mọi giới hạn • ID Owner nằm gần đầu file bot.py")

    utility = discord.Embed(title="✨  T I Ệ N   Í C H", color=0x9B59B6)
    utility.add_field(name="🖼️ `.av [@user]` `/av`", value="Xem avatar", inline=True)
    utility.add_field(name="✏️ `.nickname <tên>` `/nickname`", value="Đổi biệt danh (alias: `name`)", inline=True)
    utility.add_field(name="💤 `.afk [lý do]` `/afk`", value="Bật chế độ AFK", inline=True)
    utility.add_field(name="📖 `.help` `/help`", value="Mở menu này", inline=True)
    utility.add_field(name="👛 `.coin` `/coin`", value="Xem số dư", inline=True)
    utility.add_field(name="📅 `.daily` `/daily`", value="Nhận lương", inline=True)
    utility.add_field(name="🏆 `.top` `/top`", value="Bảng xếp hạng", inline=True)
    utility.add_field(name="💸 `.pay @user` `/pay`", value="Chuyển coin", inline=True)
    utility.add_field(
        name="🔒 Ghi chú",
        value="Khi kênh bị `.blockbot` khóa, các lệnh **quản trị / AFK / clear / delreply** vẫn dùng được bình thường.",
        inline=False
    )

    return {
        "home": home, "werewolf": werewolf, "games": games,
        "economy": economy, "admin": admin, "utility": utility,
    }


@bot.hybrid_command(name="help")
async def cmd_help_v2(ctx):
    """Mở menu trợ giúp."""
    embed = help_pages_v2()["home"]
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed, view=HelpHomeView())


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


# Kênh bị .blockbot vẫn cho phép các lệnh quản trị/AFK này.
_BLOCKED_CHANNEL_ALLOWED_COMMANDS = {
    "afk", "xtn", "clear", "addrole", "delrole", "kenhlixi",
    "khoamom", "unkhoamom", "khoachat", "mute", "timeout", "unmute",
    "ban", "unban", "kick", "clbot", "dclbot",
}


def _command_name_from_message(message):
    content = (message.content or "").lstrip()
    if not content.startswith((".", "!")):
        return ""
    return content[1:].split(None, 1)[0].lower() if content[1:].strip() else ""


@bot.event
async def on_message(message):
    # --- Kênh bị cấm bot ---
    if message.guild and message.channel.id in BOT_BLOCKED_CHANNELS.get(message.guild.id, set()):
        if message.author.bot:
            if message.author.id != getattr(bot.user, "id", None):
                try:
                    await message.delete()
                except Exception:
                    pass
            return
        if is_bot_command_message(message):
            if _command_name_from_message(message) in _BLOCKED_CHANNEL_ALLOWED_COMMANDS:
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

    # AFK: tự gỡ khi nhắn tin
    if uid_str in AFK_USERS:
        afk_info = AFK_USERS.pop(uid_str)
        try:
            if message.author != message.guild.owner and message.guild.me.guild_permissions.manage_nicknames:
                await message.author.edit(nick=afk_info["old_nick"], reason="Tự động gỡ AFK khi nhắn tin")
        except Exception:
            pass
        await message.channel.send(f"👋 Chào mừng **{message.author.mention}** đã quay lại! Đã tắt chế độ AFK.", delete_after=6)

    # AFK: báo khi bị tag
    if message.mentions:
        for mentioned_user in message.mentions:
            m_id = str(mentioned_user.id)
            if m_id in AFK_USERS:
                await message.channel.send(f"💤 **{mentioned_user.display_name}** đang bận AFK với lý do: *{AFK_USERS[m_id]['reason']}*")

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

    # Đồng bộ slash command (/) lên Discord
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
    dashboard_log_activity(ctx.author, ctx.message.content)


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
        await bot.load_extension("quest")
        await bot.load_extension("fun")
        await bot.load_extension("pingbot")
        await bot.load_extension("gif")
        await bot.load_extension("dashboard_bridge")
        # --- game mới tách ra ---
        await bot.load_extension("casino")
        await bot.load_extension("masoi")

        asyncio.create_task(dashboard_stats_loop())
        asyncio.create_task(dashboard_outbox_loop())
        try:
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            # 429 từ Cloudflare = IP host bị Discord chặn → thoát mã 42 cho keep_alive.py chờ lâu.
            if e.status == 429:
                import sys as _sys
                print("[RateLimit] Discord/Cloudflare đang chặn IP host (429). Thoát mã 42 để chờ lâu rồi thử lại.")
                _sys.exit(42)
            raise


asyncio.run(main())
