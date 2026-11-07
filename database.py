"""
discord_storage.py — Lưu TOÀN BỘ dữ liệu KS-BOT vào KÊNH CHAT DISCORD (thay Supabase).

Lưu gì
  • ksbot_master.db (coin, level, thú, đồ, kênh bị cấm bot...)
  • MỌI file .json ở thư mục bot (kênh lì xì, kênh thú hoang, kênh ksbot, outbox/activity,
    config.json = toàn bộ cài đặt dashboard...) — trừ stats.json/file có chữ token/secret.
  • NHẬT KÝ LỆNH  (command_log.jsonl + command_stats.json): AI dùng lệnh GÌ, GÕ NHƯ THẾ NÀO
    (nội dung/tham số), Ở SERVER/KÊNH NÀO, qua prefix hay slash, thành công hay lỗi, lúc nào.
  • NHẬT KÝ COIN  (coin_log.jsonl + mục "coins" trong command_stats.json): mỗi lần coin đổi —
    ai, trước, sau, chênh lệch, do lệnh nào, ở kênh nào; cộng thêm tổng thắng/thua mỗi người.

Cách lưu
  • Gom tất cả -> .tar.gz -> cắt phần <= 8MB -> gửi vào kênh riêng "ksbot-data-N".
  • Bot TỰ TẠO kênh (private) và GHI NHỚ ID kênh trong kênh "ksbot-index" (1 tin nhắn JSON được
    sửa lại sau mỗi lần backup) => khôi phục theo ID, kể cả khi ai đó đổi tên kênh.
  • Chỉ giữ KEEP_GENS bản tự động + KEEP_MANUAL bản thủ công (/savedata) mới nhất.
  • Kênh đầy => tự tạo kênh mới; kênh cũ trống => tự xóa.
  • Kênh PROTECTED_CHANNEL_ID tuyệt đối không bị đụng tới.
  • Bot khởi động mà DB local trống => tự khôi phục từ kênh.
  • /savedata (chỉ OWNER): lưu tổng quát NGAY, bản này được giữ riêng -> dùng trước khi update bot.
  • /coinlog, /cmdlog (chỉ OWNER): xem nhanh lịch sử coin / lệnh.
"""
import asyncio
import collections
import contextvars
import datetime
import hashlib
import io
import json
import os
import re
import sqlite3
import tarfile
import tempfile
import time

import discord
from discord.ext import commands

OWNER_USER_ID = 1404399385965498479
STORAGE_GUILD_ID = 1542062093908312074
PROTECTED_CHANNEL_ID = 1542062252897865802   # KHÔNG BAO GIỜ đụng tới kênh này

CHANNEL_PREFIX = "ksbot-data-"
INDEX_NAME = "ksbot-index"
DATA_TOPIC = "KSBOT-DATA-STORE"      # dấu hiệu nhận kênh dữ liệu, kể cả khi bị đổi tên
INDEX_TOPIC = "KSBOT-INDEX"
INDEX_MSG_PREFIX = "KSBOT-INDEX"
MSG_PREFIX = "KSBOT-DATA"
CHUNK_BYTES = 8_000_000          # < 10MB giới hạn upload mặc định của Discord
KEEP_GENS = 3                    # số bản backup TỰ ĐỘNG được giữ
KEEP_MANUAL = 3                  # số bản /savedata được giữ
MAX_MSGS_PER_CHANNEL = 40        # đầy thì tạo kênh mới
MIN_BACKUP_INTERVAL = 180        # giây, tối thiểu giữa 2 lần backup tự động
POLL_SECONDS = 10
CLEANUP_SECONDS = 1800
LOG_KEEP_LINES = 3000            # số dòng nhật ký lệnh gần nhất được giữ
COIN_KEEP_LINES = 5000           # số dòng nhật ký coin gần nhất được giữ

STATS_FILE = "command_stats.json"
LOG_FILE = "command_log.jsonl"
COIN_LOG_FILE = "coin_log.jsonl"
JSONL_FILES = (LOG_FILE, COIN_LOG_FILE)
JSON_DENY = {"stats.json", "package.json", "package-lock.json", "tsconfig.json"}

_HDR = re.compile(
    r"^KSBOT-DATA\|gen=(\d+)\|part=(\d+)/(\d+)\|sha=([0-9a-f]{64})\|size=(\d+)(?:\|tag=(\w+))?$"
)
_task = None
_db_path = "ksbot_master.db"
_backup_lock = asyncio.Lock()

# Lệnh đang chạy (để biết coin đổi do lệnh nào)
CURRENT_CMD = contextvars.ContextVar("ks_current_cmd", default=None)


def _iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- file helpers
def _json_ok(name):
    low = name.lower()
    return (
        low.endswith(".json")
        and os.sep not in name and "/" not in name
        and low not in JSON_DENY
        and "token" not in low and "secret" not in low
    )


def _extra_files():
    try:
        names = [n for n in os.listdir(".") if os.path.isfile(n) and _json_ok(n)]
    except OSError:
        names = []
    names += [n for n in JSONL_FILES if os.path.exists(n)]
    return sorted(set(names))


def _restorable(name, db_path):
    return name == os.path.basename(db_path) or name in JSONL_FILES or _json_ok(name)


# ---------------------------------------------------------------- tracker (lệnh + coin)
class Tracker:
    """Ghi nhớ lệnh nào được dùng ở đâu/như nào và mọi biến động coin."""

    def __init__(self):
        self.stats = {"total": 0, "commands": {}, "channels": {}, "users": {}, "coins": {}}
        self.log = collections.deque(maxlen=LOG_KEEP_LINES)
        self.coin_log = collections.deque(maxlen=COIN_KEEP_LINES)
        self.dirty = False
        self._recent = {}
        self.loaded = False

    # ---- nạp lại sau khi khôi phục
    def load(self):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in ("commands", "channels", "users", "coins"):
                    self.stats[k] = data.get(k, {}) or {}
                self.stats["total"] = int(data.get("total", 0))
        except Exception:
            pass
        for path, dq in ((LOG_FILE, self.log), (COIN_LOG_FILE, self.coin_log)):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            dq.append(line)
            except Exception:
                pass
        self.loaded = True

    # ---- lệnh
    def record(self, user, command, guild, channel, source, content=None, ok=True, error=None):
        now = time.time()
        key = (user.id, command, getattr(channel, "id", None), ok)
        last = self._recent.get(key)
        if last is not None and now - last < 1.5:        # chống ghi trùng
            return
        self._recent[key] = now
        if len(self._recent) > 500:
            self._recent = {k: t for k, t in self._recent.items() if now - t < 10}

        iso = _iso()
        gid = str(guild.id) if guild else "DM"
        cid = str(getattr(channel, "id", "0"))
        cname = getattr(channel, "name", None) or "DM"

        s = self.stats
        c = s["commands"].setdefault(command, {"count": 0, "errors": 0, "last_used": iso})
        c["last_used"] = iso
        if ok:
            s["total"] += 1
            c["count"] += 1
            ch = s["channels"].setdefault(f"{gid}:{cid}", {
                "guild_id": gid, "guild_name": guild.name if guild else "DM",
                "channel_id": cid, "channel_name": cname, "commands": {}, "last_used": iso,
            })
            ch["channel_name"] = cname
            ch["commands"][command] = ch["commands"].get(command, 0) + 1
            ch["last_used"] = iso
            u = s["users"].setdefault(str(user.id), {"name": str(user), "count": 0})
            u["name"] = str(user)
            u["count"] += 1
            u["last_command"] = command
            u["last_channel_id"] = cid
            u["last_used"] = iso
        else:
            c["errors"] = c.get("errors", 0) + 1

        entry = {
            "t": iso, "user_id": str(user.id), "user": str(user), "cmd": command,
            "args": (content or "")[:200],
            "guild_id": gid, "guild": guild.name if guild else "DM",
            "channel_id": cid, "channel": cname, "via": source, "ok": ok,
        }
        if error:
            entry["error"] = str(error)[:120]
        self.log.append(json.dumps(entry, ensure_ascii=False))
        self.dirty = True

    # ---- coin
    def record_coin(self, user_id, old, new, level=None):
        if old == new:
            return
        iso = _iso()
        ctx = CURRENT_CMD.get() or {}
        delta = new - old
        uid = str(user_id)
        c = self.stats.setdefault("coins", {}).setdefault(
            uid, {"coin": new, "gained": 0, "lost": 0, "changes": 0})
        c["coin"] = new
        c["changes"] = c.get("changes", 0) + 1
        if delta > 0:
            c["gained"] = c.get("gained", 0) + delta
        else:
            c["lost"] = c.get("lost", 0) + (-delta)
        c["last_change"] = iso
        c["last_cmd"] = ctx.get("command")
        self.coin_log.append(json.dumps({
            "t": iso, "user_id": uid, "old": old, "new": new, "delta": delta, "level": level,
            "cmd": ctx.get("command"), "args": ctx.get("args"),
            "guild_id": ctx.get("guild_id"), "channel_id": ctx.get("channel_id"),
        }, ensure_ascii=False))
        self.dirty = True

    def flush(self):
        if not self.dirty and os.path.exists(STATS_FILE):
            return
        try:
            tmp = STATS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=1)
            os.replace(tmp, STATS_FILE)
            for path, dq in ((LOG_FILE, self.log), (COIN_LOG_FILE, self.coin_log)):
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write("\n".join(dq) + ("\n" if dq else ""))
                os.replace(tmp, path)
            self.dirty = False
        except Exception as e:
            print(f"[Storage] Lỗi ghi nhật ký: {e}")


TRACKER = Tracker()
CommandTracker = Tracker  # tên cũ


def log_coin(user_id, old, new, level=None):
    """Gọi từ update_user_full() trong bot.py mỗi khi coin của 1 người đổi."""
    try:
        TRACKER.record_coin(user_id, old, new, level)
    except Exception:
        pass


# ---------------------------------------------------------------- discord helpers
def _is_data_channel(ch):
    if not isinstance(ch, discord.TextChannel) or ch.id == PROTECTED_CHANNEL_ID:
        return False
    return ch.name.startswith(CHANNEL_PREFIX) or (ch.topic or "").startswith(DATA_TOPIC)


def _chan_num(ch):
    try:
        return int(ch.name[len(CHANNEL_PREFIX):])
    except ValueError:
        return 0


async def _get_data_channels(guild):
    chans = [c for c in await guild.fetch_channels() if _is_data_channel(c)]
    return sorted(chans, key=lambda c: c.id)


async def _find_index(guild):
    for c in await guild.fetch_channels():
        if isinstance(c, discord.TextChannel) and c.id != PROTECTED_CHANNEL_ID and (
            c.name == INDEX_NAME or (c.topic or "").startswith(INDEX_TOPIC)
        ):
            return c
    return None


def _private_overwrites(guild, bot_user):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        bot_user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, manage_messages=True,
        ),
    }


async def _read_registry(idx, bot_id):
    async for msg in idx.history(limit=30):
        if msg.author.id == bot_id and (msg.content or "").startswith(INDEX_MSG_PREFIX):
            m = re.search(r"```json\n(.*?)\n```", msg.content, re.S)
            if m:
                try:
                    return json.loads(m.group(1)), msg
                except Exception:
                    pass
            return {}, msg
    return {}, None


async def _write_index(guild, bot_user, info, sha):
    """Ghi/sửa tin nhắn 'sổ đăng ký' ghi ID các kênh lưu trữ + bản backup mới nhất."""
    idx = await _find_index(guild)
    if idx is None:
        idx = await guild.create_text_channel(
            INDEX_NAME, overwrites=_private_overwrites(guild, bot_user),
            topic=f"{INDEX_TOPIC} — sổ ghi ID các kênh lưu dữ liệu của KS-BOT. KHÔNG xóa.",
            reason="KS-BOT tự tạo kênh sổ đăng ký",
        )
        print(f"[Storage] Đã tạo kênh sổ đăng ký: #{idx.name}")
    chans = await _get_data_channels(guild)
    reg = {
        "v": 1,
        "updated": _iso(),
        "guild_id": str(guild.id),
        "index_channel_id": str(idx.id),
        "data_channels": [{"id": str(c.id), "name": c.name} for c in chans],
        "latest": {
            "gen": info["gen"], "tag": info.get("tag"), "bytes": info["bytes"], "parts": info["parts"],
            "channel_id": str(info["channel_id"]), "sha": sha[:16],
        },
        "commands_total": TRACKER.stats.get("total", 0),
        "players_with_coin_log": len(TRACKER.stats.get("coins", {})),
    }
    content = INDEX_MSG_PREFIX + "\n```json\n" + json.dumps(reg, ensure_ascii=False) + "\n```"
    _, msg = await _read_registry(idx, bot_user.id)
    if msg is not None:
        await msg.edit(content=content)
    else:
        msg = await idx.send(content)
        try:
            await msg.pin()
        except Exception:
            pass


async def _candidate_channels(bot, guild):
    """Kênh lưu trữ: ưu tiên ID trong sổ đăng ký, cộng thêm quét theo tên/topic."""
    found = {}
    try:
        idx = await _find_index(guild)
        if idx:
            reg, _ = await _read_registry(idx, bot.user.id)
            for item in reg.get("data_channels", []):
                try:
                    cid = int(item["id"])
                    if cid == PROTECTED_CHANNEL_ID:
                        continue
                    ch = await bot.fetch_channel(cid)
                    if isinstance(ch, discord.TextChannel):
                        found[ch.id] = ch
                except Exception:
                    pass
    except Exception:
        pass
    for ch in await _get_data_channels(guild):
        found[ch.id] = ch
    return sorted(found.values(), key=lambda c: c.id)


async def _scan(channels, bot_id):
    gens, junk = {}, []
    for ch in channels:
        async for msg in ch.history(limit=None, oldest_first=True):
            if msg.author.id != bot_id:
                continue
            m = _HDR.match(msg.content or "")
            if not m or not msg.attachments:
                junk.append(msg)
                continue
            gen, part, total, sha, size, tag = int(m[1]), int(m[2]), int(m[3]), m[4], int(m[5]), m[6]
            g = gens.setdefault(gen, {"parts": {}, "total": total, "sha": sha, "size": size, "tag": tag})
            g["parts"][part] = msg
    return gens, junk


def _complete(g):
    return len(g["parts"]) == g["total"] and all(i in g["parts"] for i in range(1, g["total"] + 1))


def _build_archive(db_path):
    """Snapshot DB an toàn (sqlite backup API) + file json + nhật ký -> tar.gz bytes."""
    buf = io.BytesIO()
    tmp_dir = tempfile.mkdtemp(prefix="ksbot_snap_")
    snap = os.path.join(tmp_dir, os.path.basename(db_path))
    files = []
    try:
        if os.path.exists(db_path):
            src = sqlite3.connect(db_path, timeout=30)
            dst = sqlite3.connect(snap)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
            if os.path.exists(snap):
                tar.add(snap, arcname=os.path.basename(db_path))
                files.append(os.path.basename(db_path))
            for name in _extra_files():
                if name not in files and os.path.exists(name):
                    tar.add(name, arcname=name)
                    files.append(name)
    finally:
        try:
            os.remove(snap)
            os.rmdir(tmp_dir)
        except Exception:
            pass
    return buf.getvalue(), files


def _fingerprint(db_path):
    out = []
    for p in [db_path] + _extra_files():
        try:
            out.append((p, os.path.getmtime(p)))
        except OSError:
            out.append((p, 0))
    return tuple(out)


def _local_is_empty(db_path):
    if not os.path.exists(db_path):
        return True
    try:
        conn = sqlite3.connect(db_path)
        total = 0
        for t in ("users", "user_pets", "user_items"):
            try:
                total += conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                pass
        conn.close()
        return total == 0
    except Exception:
        return False


# ---------------------------------------------------------------- restore
async def restore_if_needed(bot, db_path):
    """Gọi SAU bot.login(), TRƯỚC khi bot.connect()."""
    if not _local_is_empty(db_path):
        print("[Storage] DB local đã có dữ liệu, bỏ qua khôi phục.")
        return
    try:
        guild = await bot.fetch_guild(STORAGE_GUILD_ID)
        channels = await _candidate_channels(bot, guild)
        gens, _ = await _scan(channels, bot.user.id)
        for gen in sorted(gens, reverse=True):
            g = gens[gen]
            if not _complete(g):
                continue
            data = b""
            for i in range(1, g["total"] + 1):
                data += await g["parts"][i].attachments[0].read()
            if hashlib.sha256(data).hexdigest() != g["sha"]:
                print(f"[Storage] Bản gen={gen} hỏng checksum, thử bản cũ hơn...")
                continue
            restored = []
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile() or not _restorable(member.name, db_path):
                        continue
                    content = tar.extractfile(member).read()
                    tmp = member.name + ".restore_tmp"
                    with open(tmp, "wb") as f:
                        f.write(content)
                    os.replace(tmp, member.name)
                    restored.append(member.name)
            print(f"[Storage] ✅ Đã khôi phục {len(restored)} file từ kênh Discord (gen={gen}): {', '.join(restored)}")
            return
        print("[Storage] Chưa có bản backup nào trong server lưu trữ — bắt đầu dữ liệu mới.")
    except Exception as e:
        print(f"[Storage] Lỗi khôi phục: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- backup / cleanup
async def _ensure_channel(guild, bot_user, parts_needed):
    chans = await _get_data_channels(guild)
    if chans:
        last = chans[-1]
        count = 0
        async for _ in last.history(limit=None):
            count += 1
        if count + parts_needed <= MAX_MSGS_PER_CHANNEL:
            return last
    next_num = max([_chan_num(c) for c in chans] + [0]) + 1
    ch = await guild.create_text_channel(
        f"{CHANNEL_PREFIX}{next_num}", overwrites=_private_overwrites(guild, bot_user),
        topic=f"{DATA_TOPIC} — KS-BOT lưu dữ liệu tự động. KHÔNG xóa/sửa tin nhắn trong kênh này.",
        reason="KS-BOT tự tạo kênh lưu dữ liệu",
    )
    print(f"[Storage] Đã tạo kênh lưu trữ mới: #{ch.name} (ID {ch.id})")
    return ch


async def cleanup(bot, guild=None):
    """Giữ KEEP_GENS bản tự động + KEEP_MANUAL bản thủ công; xóa phần còn lại."""
    guild = guild or await bot.fetch_guild(STORAGE_GUILD_ID)
    channels = await _get_data_channels(guild)
    gens, junk = await _scan(channels, bot.user.id)
    now = time.time()
    complete = sorted((g for g in gens if _complete(gens[g])), reverse=True)
    auto = [g for g in complete if gens[g]["tag"] != "manual"]
    manual = [g for g in complete if gens[g]["tag"] == "manual"]
    keep = set(auto[:KEEP_GENS]) | set(manual[:KEEP_MANUAL])
    deleted = 0
    for gen, g in gens.items():
        if gen in keep:
            continue
        if not _complete(g) and now - gen < 600:
            continue
        for msg in g["parts"].values():
            try:
                await msg.delete()
                deleted += 1
            except Exception:
                pass
    for msg in junk:
        try:
            await msg.delete()
            deleted += 1
        except Exception:
            pass
    channels = await _get_data_channels(guild)
    for ch in channels[:-1]:
        has_any = False
        async for _ in ch.history(limit=1):
            has_any = True
        if not has_any and ch.id != PROTECTED_CHANNEL_ID:
            try:
                await ch.delete(reason="KS-BOT dọn kênh lưu trữ trống")
                print(f"[Storage] Đã xóa kênh trống #{ch.name}")
            except Exception:
                pass
    if deleted:
        print(f"[Storage] Dọn dẹp: xóa {deleted} tin nhắn cũ/thừa.")


async def backup_now(bot, db_path, tag=None):
    """Backup toàn bộ. Trả về dict thông tin bản vừa lưu."""
    async with _backup_lock:
        TRACKER.flush()
        guild = bot.get_guild(STORAGE_GUILD_ID) or await bot.fetch_guild(STORAGE_GUILD_ID)
        data, files = await asyncio.to_thread(_build_archive, db_path)
        sha = hashlib.sha256(data).hexdigest()
        chunks = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)] or [b""]
        total = len(chunks)
        gen = int(time.time())
        ch = await _ensure_channel(guild, bot.user, total)
        sent = []
        try:
            for i, chunk in enumerate(chunks, 1):
                header = f"{MSG_PREFIX}|gen={gen}|part={i}/{total}|sha={sha}|size={len(data)}"
                if tag:
                    header += f"|tag={tag}"
                f = discord.File(io.BytesIO(chunk), filename=f"ksbot_{gen}_p{i}of{total}.bin")
                sent.append(await ch.send(content=header, file=f))
        except Exception:
            for m in sent:
                try:
                    await m.delete()
                except Exception:
                    pass
            raise
        print(f"[Storage] 💾 Backup{' (' + tag + ')' if tag else ''} -> #{ch.name} "
              f"(gen={gen}, {len(data):,} bytes, {total} phần, {len(files)} file).")
        await cleanup(bot, guild)
        info = {"gen": gen, "tag": tag, "bytes": len(data), "parts": total,
                "channel": ch.name, "channel_id": ch.id, "files": files}
        try:
            await _write_index(guild, bot.user, info, sha)
        except Exception as e:
            print(f"[Storage] Không ghi được sổ đăng ký ID kênh: {type(e).__name__}: {e}")
        return info


async def _loop(bot, db_path):
    await bot.wait_until_ready()
    last_fp = None
    last_backup = 0.0
    last_clean = 0.0
    while not bot.is_closed():
        try:
            TRACKER.flush()
            now = time.time()
            fp = _fingerprint(db_path)
            if fp != last_fp and now - last_backup >= MIN_BACKUP_INTERVAL:
                await backup_now(bot, db_path)
                last_fp, last_backup, last_clean = _fingerprint(db_path), time.time(), time.time()
            elif now - last_clean >= CLEANUP_SECONDS:
                await cleanup(bot)
                last_clean = time.time()
        except discord.Forbidden:
            print("[Storage] ❌ Bot thiếu quyền (cần Manage Channels, Manage Messages, Attach Files) trong server lưu trữ.")
            await asyncio.sleep(300)
        except Exception as e:
            print(f"[Storage] Lỗi: {type(e).__name__}: {e}")
            await asyncio.sleep(60)
        await asyncio.sleep(POLL_SECONDS)


def start(bot, db_path):
    """Bật vòng backup/dọn dẹp chạy nền. Gọi 1 lần trong main()."""
    global _task, _db_path
    _db_path = db_path
    if not TRACKER.loaded:
        TRACKER.load()
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(bot, db_path))


# ---------------------------------------------------------------- Cog
def _ctx_args(ctx, via):
    try:
        if via == "slash":
            kw = " ".join(f"{k}={v}" for k, v in (ctx.kwargs or {}).items() if v is not None)
            return f"/{ctx.command.qualified_name} {kw}".strip()
        return (ctx.message.content or "")
    except Exception:
        return ""


def _fmt_line(raw):
    try:
        d = json.loads(raw)
    except Exception:
        return None
    return d


class StorageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not TRACKER.loaded:
            TRACKER.load()

    async def cog_load(self):
        prev = getattr(self.bot, "_before_invoke", None)

        async def _ks_before_invoke(ctx):
            try:
                via = "slash" if getattr(ctx, "interaction", None) is not None else "prefix"
                CURRENT_CMD.set({
                    "command": ctx.command.qualified_name if ctx.command else None,
                    "args": _ctx_args(ctx, via)[:120],
                    "guild_id": str(ctx.guild.id) if ctx.guild else None,
                    "channel_id": str(ctx.channel.id) if ctx.channel else None,
                })
            except Exception:
                pass
            if prev is not None:
                await prev(ctx)

        self.bot.before_invoke(_ks_before_invoke)

    @commands.Cog.listener("on_command_completion")
    async def _on_cmd(self, ctx):
        if not ctx.command:
            return
        via = "slash" if getattr(ctx, "interaction", None) is not None else "prefix"
        TRACKER.record(ctx.author, ctx.command.qualified_name, ctx.guild, ctx.channel, via,
                       content=_ctx_args(ctx, via), ok=True)

    @commands.Cog.listener("on_command_error")
    async def _on_cmd_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound) or not ctx.command:
            return
        via = "slash" if getattr(ctx, "interaction", None) is not None else "prefix"
        TRACKER.record(ctx.author, ctx.command.qualified_name, ctx.guild, ctx.channel, via,
                       content=_ctx_args(ctx, via), ok=False, error=type(error).__name__)

    @commands.Cog.listener("on_app_command_completion")
    async def _on_app_cmd(self, interaction, command):
        TRACKER.record(interaction.user, command.qualified_name, interaction.guild,
                       interaction.channel, "slash", content=f"/{command.qualified_name}", ok=True)

    # ---------- /savedata
    @commands.hybrid_command(name="savedata", aliases=["luudata", "backupdata"])
    async def savedata(self, ctx):
        """[OWNER] Lưu tổng quát toàn bộ dữ liệu bot lên kênh lưu trữ (dùng trước khi update bot)."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        if getattr(ctx, "interaction", None) is not None:
            await ctx.defer(ephemeral=True)
        try:
            info = await backup_now(self.bot, _db_path, tag="manual")
        except discord.Forbidden:
            return await ctx.send("❌ Bot thiếu quyền trong server lưu trữ (Manage Channels / Manage Messages / Attach Files).")
        except Exception as e:
            return await ctx.send(f"❌ Lưu thất bại: `{type(e).__name__}: {e}`")

        s = TRACKER.stats
        embed = discord.Embed(
            title="💾 ĐÃ LƯU TỔNG QUÁT TOÀN BỘ DỮ LIỆU",
            description="Bản này được **giữ riêng**, bản tự động sẽ không đẩy nó mất. Giờ có thể update bot an toàn.",
            color=0x2ECC71,
        )
        embed.add_field(name="📦 Dung lượng", value=f"{info['bytes']:,} bytes • {info['parts']} phần", inline=True)
        embed.add_field(name="📁 Kênh lưu", value=f"#{info['channel']} (`{info['channel_id']}`)", inline=True)
        embed.add_field(name="🧾 Mã bản lưu", value=f"`{info['gen']}`", inline=True)
        embed.add_field(
            name="⌨️ Nhật ký lệnh",
            value=f"**{s['total']:,}** lệnh • **{len(s['commands'])}** loại • **{len(s['channels'])}** kênh • **{len(s['users'])}** người dùng",
            inline=False,
        )
        embed.add_field(
            name="💰 Nhật ký coin",
            value=f"**{len(TRACKER.coin_log):,}** lần đổi coin • **{len(s.get('coins', {}))}** người được theo dõi",
            inline=False,
        )
        embed.add_field(name="🗂️ File đã lưu", value="`" + "`, `".join(info["files"]) + "`", inline=False)
        embed.set_footer(text=f"ID kênh đã ghi vào #{INDEX_NAME} để khôi phục")
        await ctx.send(embed=embed)

    # ---------- /coinlog
    @commands.hybrid_command(name="coinlog", aliases=["lichsucoin"])
    async def coinlog(self, ctx, member: discord.Member = None):
        """[OWNER] Xem 10 lần đổi coin gần nhất của 1 người."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        member = member or ctx.author
        rows = []
        for raw in reversed(TRACKER.coin_log):
            d = _fmt_line(raw)
            if d and d.get("user_id") == str(member.id):
                rows.append(d)
                if len(rows) >= 10:
                    break
        c = TRACKER.stats.get("coins", {}).get(str(member.id))
        if not rows:
            return await ctx.send("Chưa có lịch sử coin nào của người này.")
        lines = []
        for d in rows:
            where = f"<#{d['channel_id']}>" if d.get("channel_id") else "—"
            lines.append(f"`{d['t'][5:16]}` {d['old']:,} → {d['new']:,} (**{d['delta']:+,}**) • `{d.get('cmd') or 'nút bấm/khác'}` • {where}")
        head = f"💰 **Lịch sử coin của {member.display_name}**"
        if c:
            head += f"\nTổng thắng **{c.get('gained', 0):,}** • tổng thua **{c.get('lost', 0):,}** • **{c.get('changes', 0)}** lần đổi"
        await ctx.send(head + "\n" + "\n".join(lines))

    # ---------- /cmdlog
    @commands.hybrid_command(name="cmdlog", aliases=["lichsulenh"])
    async def cmdlog(self, ctx, member: discord.Member = None):
        """[OWNER] Xem 10 lệnh gần nhất (của 1 người hoặc toàn bộ)."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        rows = []
        for raw in reversed(TRACKER.log):
            d = _fmt_line(raw)
            if not d:
                continue
            if member is None or d.get("user_id") == str(member.id):
                rows.append(d)
                if len(rows) >= 10:
                    break
        if not rows:
            return await ctx.send("Chưa có nhật ký lệnh nào.")
        lines = []
        for d in rows:
            mark = "✅" if d.get("ok", True) else "⚠️"
            args = f"`{d['args'][:60]}`" if d.get("args") else f"`{d['cmd']}`"
            lines.append(f"{mark} `{d['t'][5:16]}` **{d['user']}** • {args} • #{d['channel']} • {d['guild']} • {d['via']}")
        await ctx.send("⌨️ **Lệnh gần đây**\n" + "\n".join(lines))


async def setup(bot):
    await bot.add_cog(StorageCog(bot))
