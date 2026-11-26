"""
autoreply.py — KS-BOT tự trả lời khi phát hiện câu nói (reply + ping người nói).

VD: ai nhắn  =))   → bot reply  =))
    ai nhắn  ai hỏi → bot reply  tao hỏi

• Bot trả lời bằng REPLY và PING người đó.
• Kênh bị khóa (/lock) bot vẫn chat được (tự cấp quyền cho bot nếu thiếu).
• Không trả lời bot khác, không trả lời tin bắt đầu bằng . ! /
• Kênh bị .blockbot vẫn TRẢ LỜI BÌNH THƯỜNG (đã bỏ đoạn bỏ qua kênh block).
• Chống spam: mỗi câu kích hoạt chỉ trả lời 1 lần / 6 giây / kênh.
• Ai set được: Admin / Quản lý máy chủ / Quản lý kênh / Quản lý tin nhắn / Owner Bot / Chủ server.
• Dữ liệu lưu ở autoreply_config.json → discord_storage.py tự backup lên #ksbot-config.

Chữ trả lời dùng được: {user} (tag người nói) • {name} • {server}

Lệnh (có cả slash /autoreply ...), viết:  <câu phát hiện> | <câu trả lời>
  .autoreply                       xem danh sách
  .autoreply add ai hỏi | tao hỏi          tin có CHỨA "ai hỏi" là trả lời
  .autoreply addexact =)) | =))            tin phải Y HỆT "=))" mới trả lời
  .autoreply remove <số hoặc câu>  xóa 1 câu
  .autoreply clear                 xóa hết
"""
import json
import os
import time

import discord
from discord.ext import commands

OWNER_USER_ID = 1404399385965498479
CFG_FILE = "autoreply_config.json"
COOLDOWN = 6
MAX_RULES = 50

CFG = {}      # {guild_id(str): [{"t": phát hiện, "r": trả lời, "exact": bool}, ...]}
_cd = {}


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
        print(f"[AutoReply] Lỗi ghi {CFG_FILE}: {e}")


def can_manage(member):
    if member.id == OWNER_USER_ID or member == member.guild.owner:
        return True
    p = member.guild_permissions
    return p.administrator or p.manage_guild or p.manage_channels or p.manage_messages


async def _guard(ctx):
    if ctx.guild and can_manage(ctx.author):
        return True
    await ctx.send("❌ Chỉ **Admin / Mod có quyền quản lý** mới được cài tự trả lời.", delete_after=6)
    return False


def _find(rules, content):
    c = content.casefold().strip()
    for r in sorted(rules, key=lambda r: -len(r["t"])):   # câu dài ưu tiên trước
        t = r["t"].casefold()
        if (r.get("exact") and c == t) or (not r.get("exact") and t in c):
            return r
    return None


def _fmt(text, member):
    return (text.replace("{user}", member.mention)
                .replace("{name}", member.display_name)
                .replace("{server}", member.guild.name))


async def reply_ping(message, text):
    """Reply + ping người nói. Kênh khóa thì tự cấp quyền cho bot rồi gửi lại."""
    allowed = discord.AllowedMentions(everyone=False, roles=False, users=[message.author], replied_user=True)
    try:
        return await message.reply(text, allowed_mentions=allowed, mention_author=True)
    except discord.Forbidden:
        ch, me = message.channel, message.guild.me
        if ch.permissions_for(me).send_messages:
            return None
        try:
            await ch.set_permissions(me, view_channel=True, send_messages=True,
                                     reason="KS-BOT: cho bot chat được trong kênh bị khóa")
            return await message.reply(text, allowed_mentions=allowed, mention_author=True)
        except Exception:
            return None
    except Exception:
        return None


def _parse(noi_dung):
    if "|" not in noi_dung:
        return None
    t, r = noi_dung.split("|", 1)
    t, r = t.strip(), r.strip().replace("\\n", "\n")
    if not t or not r:
        return None
    return t[:100], r[:1500]


class AutoReplyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not message.content:
            return
        if message.content.lstrip().startswith((".", "!", "/")):
            return
        rules = CFG.get(str(message.guild.id))
        if not rules:
            return
        # (đã bỏ đoạn bỏ qua kênh .blockbot → autoreply vẫn trả lời ở kênh block)
        rule = _find(rules, message.content)
        if not rule:
            return
        key = (message.channel.id, rule["t"].casefold())
        now = time.time()
        if now - _cd.get(key, 0) < COOLDOWN:
            return
        _cd[key] = now
        if len(_cd) > 500:
            for k in [k for k, v in _cd.items() if now - v > 60]:
                _cd.pop(k, None)
        await reply_ping(message, _fmt(rule["r"], message.author)[:2000])

    # ------------------------------------------------ lệnh
    @commands.hybrid_group(name="autoreply", aliases=["trl", "tutraloi"], fallback="list")
    async def autoreply(self, ctx):
        """Xem danh sách câu tự trả lời."""
        if not await _guard(ctx):
            return
        rules = CFG.get(str(ctx.guild.id), [])
        if not rules:
            return await ctx.send("💬 Chưa có câu tự trả lời nào.\nThêm: `.autoreply add ai hỏi | tao hỏi`")
        lines = []
        for i, r in enumerate(rules, 1):
            t = r["t"].replace("`", "'")
            a = r["r"].replace("`", "'").replace("\n", " ")[:60]
            lines.append(f"`{i}.` **{'=' if r.get('exact') else '∋'}** `{t}` ➜ `{a}`")
        desc = "\n".join(lines)[:4000]
        e = discord.Embed(title=f"💬 TỰ TRẢ LỜI ({len(rules)})", description=desc, color=0x5865F2)
        e.set_footer(text="∋ = tin chứa câu đó • = = tin phải y hệt • Xóa: .autoreply remove <số>")
        await ctx.send(embed=e)

    async def _add(self, ctx, noi_dung, exact):
        if not await _guard(ctx):
            return
        parsed = _parse(noi_dung)
        if not parsed:
            return await ctx.send("❌ Cú pháp: `.autoreply add <câu phát hiện> | <câu trả lời>`\nVD: `.autoreply add ai hỏi | tao hỏi`",
                                  delete_after=8)
        t, r = parsed
        rules = CFG.setdefault(str(ctx.guild.id), [])
        for x in rules:
            if x["t"].casefold() == t.casefold() and bool(x.get("exact")) == exact:
                x["r"] = r
                _save()
                return await ctx.send(f"✅ Đã cập nhật: `{t}` ➜ `{r[:100]}`", delete_after=8)
        if len(rules) >= MAX_RULES:
            return await ctx.send(f"❌ Tối đa **{MAX_RULES}** câu. Xóa bớt bằng `.autoreply remove`.", delete_after=6)
        rules.append({"t": t, "r": r, "exact": exact})
        _save()
        await ctx.send(
            f"✅ Đã thêm: {'tin phải y hệt' if exact else 'tin chứa'} `{t}` ➜ bot reply `{r[:100]}`",
            delete_after=8)

    @autoreply.command(name="add")
    async def ar_add(self, ctx, *, noi_dung: str):
        """Thêm: <câu phát hiện> | <câu trả lời> (tin chứa câu đó là trả lời)."""
        await self._add(ctx, noi_dung, False)

    @autoreply.command(name="addexact")
    async def ar_addexact(self, ctx, *, noi_dung: str):
        """Thêm: <câu phát hiện> | <câu trả lời> (tin phải y hệt mới trả lời)."""
        await self._add(ctx, noi_dung, True)

    @autoreply.command(name="remove")
    async def ar_remove(self, ctx, *, so_hoac_cau: str):
        """Xóa 1 câu theo số thứ tự hoặc theo câu phát hiện."""
        if not await _guard(ctx):
            return
        rules = CFG.get(str(ctx.guild.id), [])
        key = so_hoac_cau.strip()
        idx = None
        if key.isdigit() and 1 <= int(key) <= len(rules):
            idx = int(key) - 1
        else:
            for i, r in enumerate(rules):
                if r["t"].casefold() == key.casefold():
                    idx = i
                    break
        if idx is None:
            return await ctx.send("❌ Không tìm thấy. Xem số thứ tự bằng `.autoreply`.", delete_after=6)
        gone = rules.pop(idx)
        _save()
        await ctx.send(f"🗑️ Đã xóa `{gone['t']}`", delete_after=6)

    @autoreply.command(name="clear")
    async def ar_clear(self, ctx):
        """Xóa toàn bộ câu tự trả lời."""
        if not await _guard(ctx):
            return
        CFG.pop(str(ctx.guild.id), None)
        _save()
        await ctx.send("🗑️ Đã xóa hết câu tự trả lời.", delete_after=6)


async def setup(bot):
    _load()
    await bot.add_cog(AutoReplyCog(bot))
