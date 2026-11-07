"""
dashboard_bridge.py — cầu nối giữa Dashboard (web.py) và Discord.

Cách dùng: trong bot.py, hàm main(), thêm 1 dòng cùng chỗ các load_extension khác:
    await bot.load_extension("dashboard_bridge")
và XÓA dòng  asyncio.create_task(dashboard_reaction_loop())  (+ cả hàm đó).

web.py ghi lệnh vào thư mục web_spool/cmd_<id>.json, file này xử lý rồi ghi
kết quả ra web_spool/res_<id>.json. Mỗi lệnh 1 file nên không bị đọc/ghi đè nhau.
"""
import asyncio
import json
import os
import re
import time

import discord
from discord.ext import commands

SPOOL_DIR = "web_spool"
UPLOAD_DIR = "web_uploads"
DM_FILE = "dm_users.json"
CMD_MAX_AGE = 30      # lệnh nằm quá lâu (web đã bỏ cuộc) thì bỏ qua
POLL_SECONDS = 0.3
ID_RE = re.compile(r"^[0-9a-f]{32}$")

os.makedirs(SPOOL_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


class BridgeError(Exception):
    pass


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _load_dm_ids():
    try:
        with open(DM_FILE, "r", encoding="utf-8") as f:
            return [int(x) for x in json.load(f)]
    except Exception:
        return []


def _remember_dm(uid):
    ids = _load_dm_ids()
    if ids and ids[-1] == uid:
        return
    if uid in ids:
        ids.remove(uid)
    ids.append(uid)
    try:
        _write_json(DM_FILE, ids[-100:])
    except Exception:
        pass


def _err_text(e):
    if isinstance(e, BridgeError):
        return str(e)
    code = getattr(e, "code", None)
    status = getattr(e, "status", None)
    table = {
        50001: "Bot không có quyền xem kênh này",
        50013: "Bot thiếu quyền cho thao tác này",
        50007: "Không nhắn DM được cho người này (họ tắt DM hoặc không chung server)",
        10003: "Không tìm thấy kênh",
        10008: "Tin nhắn không còn tồn tại",
        90001: "Người này đã chặn bot nên không thả reaction được",
        30010: "Tin nhắn đã đạt tối đa số loại reaction",
        40005: "File quá lớn",
        50035: "Nội dung không hợp lệ (quá dài hoặc trống)",
    }
    if code in table:
        return table[code]
    if status == 429:
        return "Discord đang giới hạn tốc độ, thử lại sau vài giây"
    return (type(e).__name__ + ": " + str(e)).replace("\n", " ")[:160]


def ser_user(u):
    return {
        "id": str(u.id),
        "name": getattr(u, "display_name", None) or u.name,
        "avatar": str(u.display_avatar.with_size(64).url),
        "bot": bool(u.bot),
    }


def ser_embed(e):
    d = e.to_dict()
    out = {
        "title": d.get("title"),
        "description": (d.get("description") or "")[:600] or None,
        "url": d.get("url"),
        "color": d.get("color"),
        "author": (d.get("author") or {}).get("name"),
        "footer": (d.get("footer") or {}).get("text"),
        "image": (d.get("image") or {}).get("url"),
        "thumb": (d.get("thumbnail") or {}).get("url"),
        "fields": [
            {"name": f.get("name"), "value": (f.get("value") or "")[:200]}
            for f in (d.get("fields") or [])[:6]
        ],
    }
    return out


def ser_msg(m, me_id):
    ref = None
    if m.reference and m.reference.message_id:
        rm = m.reference.resolved if isinstance(m.reference.resolved, discord.Message) else None
        ref = {
            "id": str(m.reference.message_id),
            "author": rm.author.display_name if rm else None,
            "content": rm.clean_content[:80] if rm else None,
        }
    if m.type in (discord.MessageType.default, discord.MessageType.reply):
        content = m.clean_content
    else:
        content = m.system_content or m.clean_content
    reactions = []
    for r in m.reactions:
        e = r.emoji
        reactions.append({
            "emoji": str(e),
            "url": None if isinstance(e, str) else str(e.url),
            "count": r.count,
            "me": bool(r.me),
        })
    return {
        "id": str(m.id),
        "ts": m.created_at.timestamp(),
        "edited": m.edited_at is not None,
        "author": ser_user(m.author),
        "mine": m.author.id == me_id,
        "content": content,
        "reply": ref,
        "atts": [
            {"url": a.url, "name": a.filename, "type": a.content_type or "", "size": a.size}
            for a in m.attachments
        ],
        "embeds": [ser_embed(e) for e in m.embeds[:3]],
        "reactions": reactions,
        "components": bool(m.components),
    }


class DashboardBridge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.task = None
        self.user_cache = {}

    async def cog_load(self):
        self.task = asyncio.create_task(self._loop())

    async def cog_unload(self):
        if self.task:
            self.task.cancel()

    # ---------- ghi nhớ ai từng nhắn DM với bot ----------
    @commands.Cog.listener()
    async def on_message(self, message):
        ch = message.channel
        if isinstance(ch, discord.DMChannel) and ch.recipient and not ch.recipient.bot:
            _remember_dm(ch.recipient.id)

    # ---------- vòng lặp nhận lệnh ----------
    async def _loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                now = time.time()
                for fn in os.listdir(SPOOL_DIR):
                    path = os.path.join(SPOOL_DIR, fn)
                    if fn.startswith("res_") and fn.endswith(".json"):
                        if now - os.path.getmtime(path) > 60:
                            os.remove(path)
                        continue
                    if not (fn.startswith("cmd_") and fn.endswith(".json")):
                        continue
                    try:
                        age = now - os.path.getmtime(path)
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        os.remove(path)
                    except Exception:
                        continue
                    if age > CMD_MAX_AGE:
                        continue
                    asyncio.create_task(self._run(data))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Bridge] Lỗi vòng lặp: {e}")
            await asyncio.sleep(POLL_SECONDS)

    async def _run(self, data):
        cid = str(data.get("id", ""))
        if not ID_RE.match(cid):
            return
        handler = getattr(self, "h_" + str(data.get("type")), None)
        try:
            if handler is None:
                raise BridgeError("Lệnh không hợp lệ")
            result = {"ok": True, "data": await handler(data)}
        except Exception as e:
            result = {"ok": False, "error": _err_text(e)}
            if not isinstance(e, BridgeError):
                print(f"[Bridge] {data.get('type')} lỗi: {e}")
        try:
            _write_json(os.path.join(SPOOL_DIR, f"res_{cid}.json"), result)
        except Exception as e:
            print(f"[Bridge] Không ghi được kết quả: {e}")

    # ---------- tiện ích ----------
    async def _channel(self, cid):
        ch = self.bot.get_channel(int(cid))
        if ch is None:
            ch = await self.bot.fetch_channel(int(cid))
        return ch

    @staticmethod
    def _check_read(ch):
        g = getattr(ch, "guild", None)
        if g and g.me:
            p = ch.permissions_for(g.me)
            if not (p.view_channel and p.read_message_history):
                raise BridgeError("Bot không có quyền xem/đọc lịch sử kênh này")

    @staticmethod
    def _can_send(ch):
        g = getattr(ch, "guild", None)
        if g and g.me:
            return bool(ch.permissions_for(g.me).send_messages)
        return True

    # ---------- các lệnh ----------
    async def h_channels(self, d):
        g = self.bot.get_guild(int(d["guild_id"]))
        if g is None:
            raise BridgeError("Bot không ở server này")
        me = g.me

        def visible(c):
            p = c.permissions_for(me)
            return p.view_channel and p.read_message_history

        def pack(c):
            return {"id": str(c.id), "name": c.name, "can_send": bool(c.permissions_for(me).send_messages)}

        groups = []
        loose = [pack(c) for c in g.text_channels if c.category is None and visible(c)]
        if loose:
            groups.append({"name": "", "channels": loose})
        for cat in g.categories:
            chans = [pack(c) for c in cat.text_channels if visible(c)]
            if chans:
                groups.append({"name": cat.name, "channels": chans})
        return {"guild": {"id": str(g.id), "name": g.name}, "groups": groups}

    async def h_history(self, d):
        ch = await self._channel(d["channel_id"])
        self._check_read(ch)
        limit = min(max(int(d.get("limit") or 50), 1), 100)
        kw = {"limit": limit}
        if d.get("after"):
            kw["after"] = discord.Object(id=int(d["after"]))
        elif d.get("before"):
            kw["before"] = discord.Object(id=int(d["before"]))
        msgs = [m async for m in ch.history(**kw)]
        msgs.sort(key=lambda m: m.id)
        me_id = self.bot.user.id
        is_dm = isinstance(ch, discord.DMChannel)
        return {
            "channel": {
                "id": str(ch.id),
                "name": ch.recipient.name if is_dm and ch.recipient else getattr(ch, "name", ""),
                "is_dm": is_dm,
                "can_send": self._can_send(ch),
            },
            "messages": [ser_msg(m, me_id) for m in msgs],
        }

    async def h_react(self, d):
        ch = await self._channel(d["channel_id"])
        msg = ch.get_partial_message(int(d["message_id"]))
        emoji = discord.PartialEmoji.from_str(d["emoji"])
        if d.get("remove"):
            await msg.remove_reaction(emoji, self.bot.user)
        else:
            await msg.add_reaction(emoji)
        return {}

    async def h_send(self, d):
        ch = await self._channel(d["channel_id"])
        content = (d.get("content") or "").strip()[:2000]
        base = os.path.realpath(UPLOAD_DIR)
        files, paths = [], []
        for u in d.get("uploads") or []:
            p = os.path.realpath(u.get("path", ""))
            if p.startswith(base + os.sep) and os.path.isfile(p):
                files.append(discord.File(p, filename=str(u.get("name") or "file")[:80]))
                paths.append(p)
        if not content and not files:
            raise BridgeError("Tin nhắn trống")
        kw = {}
        if d.get("reply_to"):
            kw["reference"] = discord.MessageReference(
                message_id=int(d["reply_to"]), channel_id=ch.id, fail_if_not_exists=False
            )
        try:
            await ch.send(
                content=content or None,
                files=files or None,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
                **kw,
            )
        finally:
            for f in files:
                try:
                    f.close()
                except Exception:
                    pass
            for p in paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
        return {}

    async def h_delete(self, d):
        ch = await self._channel(d["channel_id"])
        await ch.get_partial_message(int(d["message_id"])).delete()
        return {}

    async def h_emojis(self, d):
        if d.get("guild_id"):
            g = self.bot.get_guild(int(d["guild_id"]))
            src = list(g.emojis) if g else []
        else:
            src = list(self.bot.emojis)[:200]
        return [
            {"id": str(e.id), "name": e.name, "animated": e.animated, "url": str(e.url)}
            for e in src if e.available
        ][:200]

    async def _user(self, uid):
        u = self.bot.get_user(uid) or self.user_cache.get(uid)
        if u is None:
            try:
                u = await self.bot.fetch_user(uid)
            except Exception:
                return None
        self.user_cache[uid] = u
        return u

    async def h_dm_list(self, d):
        ids = []
        for ch in self.bot.private_channels:
            if isinstance(ch, discord.DMChannel) and ch.recipient and not ch.recipient.bot:
                ids.append(ch.recipient.id)
        for uid in _load_dm_ids():
            if uid not in ids:
                ids.append(uid)
        out = []
        for uid in ids[-60:][::-1]:
            u = await self._user(uid)
            if u is None:
                continue
            out.append({"user": ser_user(u), "channel_id": str(u.dm_channel.id) if u.dm_channel else None})
        return out

    async def h_dm_open(self, d):
        u = await self._user(int(d["user_id"]))
        if u is None:
            raise BridgeError("Không tìm thấy user với ID này")
        if u.bot:
            raise BridgeError("Bot không DM được bot khác")
        dm = u.dm_channel or await u.create_dm()
        _remember_dm(u.id)
        return {"channel_id": str(dm.id), "user": ser_user(u)}


async def setup(bot):
    await bot.add_cog(DashboardBridge(bot))
