"""
discord_storage.py — KS-BOT lưu TOÀN BỘ dữ liệu vào KÊNH CHAT DISCORD (thay Supabase).

MỖI LOẠI DỮ LIỆU CÓ 1 KÊNH RIÊNG (bot tự tạo, private):
  #ksbot-db       ksbot_master.db  (coin, level, thú, đồ, kênh bị cấm bot...)
  #ksbot-config   mọi file .json cài đặt (config.json, kênh lì xì, kênh thú hoang, kênh ksbot...)
  #ksbot-cmdlog   nhật ký lệnh: AI gõ lệnh gì, ở đâu, lúc nào   (command_log.jsonl + command_stats.json)
  #ksbot-coinlog  nhật ký coin: ai đổi coin, trước/sau, do lệnh nào (coin_log.jsonl)
  #ksbot-uptime   TỔNG THỜI GIAN BOT ĐÃ ONLINE (uptime.json)
  #ksbot-index    sổ ghi ID các kênh trên

KHÔNG CẦN SỬA bot.py ngoài 1 dòng trong main():
      await bot.load_extension("discord_storage")      # đặt ĐẦU TIÊN trong danh sách load_extension
File này tự: khôi phục dữ liệu khi host mất file, tự tắt Supabase, tự ghi nhật ký coin,
tự lưu lại lần cuối khi bot bị tắt (SIGTERM).

Lệnh: .botuptime (ai cũng dùng) • .savedata / .coinlog / .cmdlog (chỉ OWNER)
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
import signal
import sqlite3
import sys
import tarfile
import tempfile
import time

import discord
from discord.ext import commands

# ---------------------------------------------------------------- cấu hình
OWNER_USER_ID = 1404399385965498479
STORAGE_GUILD_ID = 1542062093908312074
PROTECTED_CHANNEL_ID = 1542062252897865802   # KHÔNG BAO GIỜ đụng tới kênh này

DB_PATH = "ksbot_master.db"
WORDS_DB = os.getenv("NOITU_DB", "noitu_words.db")   # database của noitu.py
NOITU_KEEP_TABLES = ("noitu_settings", "vtv_settings", "noitu_stats")  # kho từ tự tải lại online nên không backup
STATS_FILE = "command_stats.json"
LOG_FILE = "command_log.jsonl"
COIN_LOG_FILE = "coin_log.jsonl"
UPTIME_FILE = "uptime.json"
STATE_FILE = "runtime_state.json"

DATA_TOPIC = "KSBOT-DATA-STORE"
INDEX_NAME = "ksbot-index"
INDEX_TOPIC = "KSBOT-INDEX"
INDEX_MSG_PREFIX = "KSBOT-INDEX"
MSG_PREFIX = "KSBOT-DATA"
LEGACY_PREFIX = "ksbot-data-"      # kênh kiểu cũ (nếu từng backup bằng bản trước) — chỉ đọc để khôi phục

CHUNK_BYTES = 8_000_000            # < 10MB giới hạn upload của Discord
KEEP_GENS = 3                      # số bản tự động giữ lại MỖI kênh
KEEP_MANUAL = 2                    # số bản /savedata giữ lại MỖI kênh
POLL_SECONDS = 10
LOG_KEEP_LINES = 3000
COIN_KEEP_LINES = 5000

# interval = tối thiểu bao nhiêu giây giữa 2 lần backup của loại đó
CATS = {
    "db":      {"channel": "ksbot-db",      "interval": 300, "label": "Dữ liệu người chơi"},
    "config":  {"channel": "ksbot-config",  "interval": 180, "label": "Cài đặt (.json)"},
    "cmdlog":  {"channel": "ksbot-cmdlog",  "interval": 600, "label": "Nhật ký lệnh"},
    "coinlog": {"channel": "ksbot-coinlog", "interval": 600, "label": "Nhật ký coin"},
    "uptime":  {"channel": "ksbot-uptime",  "interval": 600, "label": "Thời gian bot online"},
    "noitu":   {"channel": "ksbot-noitu",   "interval": 300, "label": "Nối Từ + Vua TV (kênh cài đặt, điểm)"},
    "state":   {"channel": "ksbot-state",   "interval": 120, "label": "Trạng thái đang chạy (AFK, lì xì, daily...)"},
}

OWN_JSON = {STATS_FILE, UPTIME_FILE, STATE_FILE}
JSON_DENY = {"stats.json", "activity.json", "outbox.json", "package.json",
             "package-lock.json", "tsconfig.json"}

_HDR = re.compile(
    r"^KSBOT-DATA(?:\|cat=(\w+))?\|gen=(\d+)\|part=(\d+)/(\d+)\|sha=([0-9a-f]{64})\|size=(\d+)(?:\|tag=(\w+))?$"
)
_task = None
_db_path = DB_PATH
_backup_lock = asyncio.Lock()
_chan_cache = {}
_LATEST = {}
_closing = False

CURRENT_CMD = contextvars.ContextVar("ks_current_cmd", default=None)


def _iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fmt_dur(sec):
    sec = int(max(0, sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d:
        parts.append(f"{d} ngày")
    if h or d:
        parts.append(f"{h} giờ")
    parts.append(f"{m} phút")
    if not d:
        parts.append(f"{s} giây")
    return " ".join(parts)


# ---------------------------------------------------------------- file theo loại
def _json_ok(name):
    low = name.lower()
    return (
        low.endswith(".json") and "/" not in name and os.sep not in name
        and low not in JSON_DENY and name not in OWN_JSON
        and "token" not in low and "secret" not in low
    )


def _files_for(cat, db_path):
    if cat == "db":
        return [db_path] if os.path.exists(db_path) else []
    if cat == "config":
        try:
            return sorted(n for n in os.listdir(".") if os.path.isfile(n) and _json_ok(n))
        except OSError:
            return []
    if cat == "cmdlog":
        return [n for n in (STATS_FILE, LOG_FILE) if os.path.exists(n)]
    if cat == "coinlog":
        return [COIN_LOG_FILE] if os.path.exists(COIN_LOG_FILE) else []
    if cat == "uptime":
        return [UPTIME_FILE] if os.path.exists(UPTIME_FILE) else []
    if cat == "state":
        return [STATE_FILE] if os.path.exists(STATE_FILE) else []
    if cat == "noitu":
        return [WORDS_DB] if os.path.exists(WORDS_DB) else []
    return []


def _restorable(cat, name, db_path):
    if name != os.path.basename(name):
        return False
    if cat == "db":
        return name == os.path.basename(db_path)
    if cat == "config":
        return _json_ok(name)
    if cat == "cmdlog":
        return name in (STATS_FILE, LOG_FILE)
    if cat == "coinlog":
        return name == COIN_LOG_FILE
    if cat == "uptime":
        return name == UPTIME_FILE
    if cat == "state":
        return name == STATE_FILE
    if cat == "noitu":
        return name == os.path.basename(WORDS_DB)
    if cat == "legacy":
        return (name == os.path.basename(db_path) or _json_ok(name)
                or name in (STATS_FILE, LOG_FILE, COIN_LOG_FILE, UPTIME_FILE, STATE_FILE))
    return False


def _fingerprint(cat, db_path):
    out = []
    for p in _files_for(cat, db_path):
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
            if cat == "noitu" and os.path.exists(p + "-wal"):
                w = os.stat(p + "-wal")
                out.append((p + "-wal", w.st_mtime, w.st_size))
        except OSError:
            out.append((p, 0, 0))
    return tuple(out)


def _noitu_snapshot(src_path, dest_path):
    """Chỉ chép các bảng nhỏ (cài đặt kênh + điểm), bỏ kho từ vài chục MB vì bot tự tải lại online."""
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dest_path)
    try:
        for t in NOITU_KEEP_TABLES:
            row = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not row or not row[0]:
                continue
            dst.execute(row[0])
            rows = src.execute(f"SELECT * FROM {t}").fetchall()
            if rows:
                dst.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(rows[0]))})", rows)
        dst.commit()
    finally:
        src.close()
        dst.close()


def _build_archive(cat, db_path):
    """Gói các file của 1 loại thành tar.gz. DB dùng sqlite backup API nên an toàn khi đang ghi."""
    buf = io.BytesIO()
    names = []
    tmp_dir = tempfile.mkdtemp(prefix="ksbot_snap_")
    snap = None
    try:
        with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
            for path in _files_for(cat, db_path):
                arc = os.path.basename(path)
                if cat == "db":
                    snap = os.path.join(tmp_dir, arc)
                    src = sqlite3.connect(path, timeout=30)
                    dst = sqlite3.connect(snap)
                    with dst:
                        src.backup(dst)
                    src.close()
                    dst.close()
                    tar.add(snap, arcname=arc)
                elif cat == "noitu":
                    snap = os.path.join(tmp_dir, arc)
                    _noitu_snapshot(path, snap)
                    tar.add(snap, arcname=arc)
                else:
                    tar.add(path, arcname=arc)
                names.append(arc)
    finally:
        try:
            if snap and os.path.exists(snap):
                os.remove(snap)
            os.rmdir(tmp_dir)
        except Exception:
            pass
    return buf.getvalue(), names


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


# ---------------------------------------------------------------- thời gian online
class Uptime:
    """Cộng dồn tổng thời gian bot đã chạy, lưu vào uptime.json (rồi backup lên #ksbot-uptime)."""

    def __init__(self):
        self.data = {}
        self.session_start = time.time()
        self.last_tick = self.session_start
        self.started = False

    def begin(self):
        if self.started:
            return
        self.started = True
        try:
            with open(UPTIME_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = loaded
        except Exception:
            self.data = {}
        d = self.data
        d["total_seconds"] = float(d.get("total_seconds", 0.0))
        d.setdefault("first_start", _iso())
        d["sessions"] = int(d.get("sessions", 0)) + 1
        d.setdefault("longest_session", 0.0)
        d["current_session_start"] = _iso()
        self.session_start = self.last_tick = time.time()
        self._save()

    def tick(self):
        if not self.started:
            return
        now = time.time()
        el = now - self.last_tick
        self.last_tick = now
        if el < 0 or el > 300:                 # máy ngủ / đổi giờ -> không cộng bậy
            el = min(max(el, 0), POLL_SECONDS)
        d = self.data
        d["total_seconds"] = float(d.get("total_seconds", 0.0)) + el
        sess = now - self.session_start
        d["last_session_seconds"] = sess
        d["longest_session"] = max(float(d.get("longest_session", 0.0)), sess)
        d["last_seen"] = _iso()
        self._save()

    def _save(self):
        try:
            tmp = UPTIME_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, UPTIME_FILE)
        except Exception as e:
            print(f"[Storage] Lỗi ghi uptime: {e}")


UPTIME = Uptime()

# ---------------------------------------------------------------- trạng thái trong bộ nhớ
# Tên biến của bot.py cần lưu. Biến của extension khác viết dạng "tenfile.TEN_BIEN"
# (ví dụ "fun.TREO_VOICE") — thêm vào đây là được lưu + khôi phục.
STATE_VARS = [
    "AFK_USERS",              # ai đang AFK + nick cũ
    "DIEM_DANH_TRACKER",      # thời gian chờ .daily
    "BOT_BLOCKED_CHANNELS",   # kênh bị .blockbot
    "LIXI_CLAIMED",           # ai đã nhận lì xì đợt hiện tại
    "LIXI_ACTIVE_EVENT",      # đợt lì xì đang hiệu lực
    "LIXI_EVENT_COUNTER",     # số thứ tự đợt lì xì
    "WILD_NEXT_SPAWN",        # giờ thú hoang xuất hiện tiếp theo
    "ACTIVE_WILD_PETS",       # thú hoang đang xuất hiện
]
_state_raw = {}
_state_ready = False
_state_last = ""
_bot_mod = None


def _enc(v):
    if isinstance(v, dict):
        return {"__d": [[_enc(k), _enc(x)] for k, x in v.items()]}
    if isinstance(v, (set, frozenset)):
        return {"__s": [_enc(x) for x in v]}
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return None                      # kiểu không lưu được (object Discord...) -> bỏ qua


def _dec(v):
    if isinstance(v, dict):
        if "__d" in v:
            return {_dec(k): _dec(x) for k, x in v["__d"]}
        if "__s" in v:
            return {_dec(x) for x in v["__s"]}
    if isinstance(v, list):
        return [_dec(x) for x in v]
    return v


def _bot_module():
    global _bot_mod
    if _bot_mod is not None:
        return _bot_mod
    for mod in list(sys.modules.values()):
        try:
            if hasattr(mod, "update_user_full") and hasattr(mod, "ALL_PETS"):
                _bot_mod = mod
                return mod
        except Exception:
            continue
    return None


def _find_var(entry):
    if "." in entry:
        modname, var = entry.rsplit(".", 1)
        for key in (modname, "cogs." + modname):
            m = sys.modules.get(key)
            if m is not None:
                return m, var
        return None, var
    return _bot_module(), entry


def _load_state_file():
    global _state_raw
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _state_raw = data if isinstance(data, dict) else {}
    except Exception:
        _state_raw = {}


def _apply_state():
    """Đổ dữ liệu đã lưu ngược vào biến của bot (gộp, không xóa thứ đang có)."""
    for entry in STATE_VARS:
        if entry not in _state_raw:
            continue
        mod, var = _find_var(entry)
        if mod is None or not hasattr(mod, var):
            continue
        try:
            val = _dec(_state_raw[entry])
            cur = getattr(mod, var)
            if isinstance(cur, dict) and isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, set) and isinstance(cur.get(k), set):
                        cur[k] |= v
                    else:
                        cur[k] = v
            elif isinstance(cur, set) and isinstance(val, set):
                cur |= val
            elif isinstance(cur, (int, float)) and isinstance(val, (int, float)):
                setattr(mod, var, max(cur, val))
            else:
                setattr(mod, var, val)
        except Exception as e:
            print(f"[Storage] Không khôi phục được {entry}: {type(e).__name__}: {e}")


def _save_state():
    global _state_last
    if not _state_ready:
        return
    data = dict(_state_raw)
    for entry in STATE_VARS:
        mod, var = _find_var(entry)
        if mod is None or not hasattr(mod, var):
            continue
        cur = getattr(mod, var)
        if var == "LIXI_CLAIMED" and isinstance(cur, dict):
            active = set(getattr(mod, "LIXI_ACTIVE_EVENT", {}).values())
            cur = {k: v for k, v in cur.items() if k in active}
        data[entry] = _enc(cur)
    bm = _bot_module()
    lobbies = getattr(bm, "game_lobbies", None) if bm else None
    if isinstance(lobbies, dict):
        data["__ww_channels"] = list(lobbies.keys())
    try:
        text = json.dumps(data, ensure_ascii=False)
        if text == _state_last:
            return
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, STATE_FILE)
        _state_last = text
    except Exception as e:
        print(f"[Storage] Lỗi ghi trạng thái: {e}")



# ---------------------------------------------------------------- nhật ký lệnh + coin
class Tracker:
    def __init__(self):
        self.stats = {"total": 0, "commands": {}, "channels": {}, "users": {}, "coins": {}}
        self.log = collections.deque(maxlen=LOG_KEEP_LINES)
        self.coin_log = collections.deque(maxlen=COIN_KEEP_LINES)
        self.dirty = False
        self._recent = {}
        self.loaded = False

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

    def record(self, user, command, guild, channel, source, content=None, ok=True, error=None):
        now = time.time()
        key = (user.id, command, getattr(channel, "id", None), ok)
        last = self._recent.get(key)
        if last is not None and now - last < 1.5:
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


def log_coin(user_id, old, new, level=None):
    try:
        TRACKER.record_coin(user_id, old, new, level)
    except Exception:
        pass


# ---------------------------------------------------------------- kênh Discord
def _private_overwrites(guild, me):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, manage_messages=True,
        ),
    }


def _is_cat_channel(ch, cat):
    if not isinstance(ch, discord.TextChannel) or ch.id == PROTECTED_CHANNEL_ID:
        return False
    return ch.name == CATS[cat]["channel"] or (ch.topic or "").startswith(f"{DATA_TOPIC}|cat={cat}")


async def _get_channel(client, guild, cat, create=True):
    cid = _chan_cache.get(cat)
    if cid:
        try:
            ch = await client.fetch_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
        except discord.NotFound:
            _chan_cache.pop(cat, None)
    for c in await guild.fetch_channels():
        if _is_cat_channel(c, cat):
            _chan_cache[cat] = c.id
            return c
    if not create:
        return None
    ch = await guild.create_text_channel(
        CATS[cat]["channel"], overwrites=_private_overwrites(guild, client.user),
        topic=f"{DATA_TOPIC}|cat={cat} — KS-BOT lưu {CATS[cat]['label']} tự động. KHÔNG xóa/sửa tin nhắn.",
        reason=f"KS-BOT tự tạo kênh lưu {cat}",
    )
    _chan_cache[cat] = ch.id
    print(f"[Storage] Đã tạo kênh #{ch.name} (ID {ch.id}) cho '{cat}'.")
    return ch


async def _scan(channel, bot_id):
    gens, junk = {}, []
    async for msg in channel.history(limit=None, oldest_first=True):
        if msg.author.id != bot_id:
            continue
        m = _HDR.match(msg.content or "")
        if not m or not msg.attachments:
            junk.append(msg)
            continue
        gen, part, total = int(m[2]), int(m[3]), int(m[4])
        g = gens.setdefault(gen, {"parts": {}, "total": total, "sha": m[5], "size": int(m[6]), "tag": m[7]})
        g["parts"][part] = msg
    return gens, junk


def _complete(g):
    return len(g["parts"]) == g["total"] and all(i in g["parts"] for i in range(1, g["total"] + 1))


async def _write_index(client, guild, bot_user):
    idx = None
    for c in await guild.fetch_channels():
        if isinstance(c, discord.TextChannel) and c.id != PROTECTED_CHANNEL_ID and (
            c.name == INDEX_NAME or (c.topic or "").startswith(INDEX_TOPIC)
        ):
            idx = c
            break
    if idx is None:
        idx = await guild.create_text_channel(
            INDEX_NAME, overwrites=_private_overwrites(guild, bot_user),
            topic=f"{INDEX_TOPIC} — sổ ghi ID các kênh lưu dữ liệu KS-BOT. KHÔNG xóa.",
            reason="KS-BOT tự tạo kênh sổ đăng ký",
        )
    reg = {
        "v": 2, "updated": _iso(), "guild_id": str(guild.id),
        "channels": {cat: str(cid) for cat, cid in _chan_cache.items()},
        "latest": _LATEST,
        "bot_total_online_seconds": int(UPTIME.data.get("total_seconds", 0)),
        "commands_total": TRACKER.stats.get("total", 0),
    }
    content = INDEX_MSG_PREFIX + "\n```json\n" + json.dumps(reg, ensure_ascii=False) + "\n```"
    target = None
    async for msg in idx.history(limit=20):
        if msg.author.id == bot_user.id and (msg.content or "").startswith(INDEX_MSG_PREFIX):
            target = msg
            break
    if target is not None:
        await target.edit(content=content)
    else:
        msg = await idx.send(content)
        try:
            await msg.pin()
        except Exception:
            pass


# ---------------------------------------------------------------- khôi phục
async def _restore_channel(channel, bot_id, cat, db_path, db_empty):
    gens, _ = await _scan(channel, bot_id)
    for gen in sorted(gens, reverse=True):
        g = gens[gen]
        if not _complete(g):
            continue
        data = b""
        for i in range(1, g["total"] + 1):
            data += await g["parts"][i].attachments[0].read()
        if hashlib.sha256(data).hexdigest() != g["sha"]:
            print(f"[Storage] {cat}: bản gen={gen} hỏng checksum, thử bản cũ hơn...")
            continue
        restored = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                if not member.isfile() or not _restorable(cat, name, db_path):
                    continue
                if name == os.path.basename(db_path):
                    if not db_empty:
                        continue
                elif os.path.exists(name):
                    continue                     # không đè file đang có
                content = tar.extractfile(member).read()
                tmp = name + ".restore_tmp"
                with open(tmp, "wb") as f:
                    f.write(content)
                os.replace(tmp, name)
                restored.append(name)
        print(f"[Storage] ✅ Khôi phục '{cat}' (gen={gen}): {', '.join(restored) or 'không có gì mới'}")
        return True
    return False


async def restore_all(db_path):
    """Chạy lúc bot khởi động bằng 1 client tạm (chưa cần bot online). Chỉ khôi phục thứ còn THIẾU."""
    db_empty = _local_is_empty(db_path)
    need = [c for c in CATS if (db_empty if c == "db" else not _files_for(c, db_path))]
    if not need:
        print("[Storage] Dữ liệu local đầy đủ, bỏ qua khôi phục.")
        return
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[Storage] Thiếu DISCORD_TOKEN, không khôi phục được.")
        return
    client = discord.Client(intents=discord.Intents.none())
    try:
        await client.login(token)
        guild = await client.fetch_guild(STORAGE_GUILD_ID)
        for cat in need:
            ch = await _get_channel(client, guild, cat, create=False)
            if ch is None:
                continue
            await _restore_channel(ch, client.user.id, cat, db_path, db_empty)
        # Kênh kiểu cũ "ksbot-data-N": chỉ dùng khi DB vẫn trống
        if _local_is_empty(db_path):
            legacy = [c for c in await guild.fetch_channels()
                      if isinstance(c, discord.TextChannel) and c.id != PROTECTED_CHANNEL_ID
                      and c.name.startswith(LEGACY_PREFIX)]
            for ch in sorted(legacy, key=lambda c: c.id, reverse=True):
                if await _restore_channel(ch, client.user.id, "legacy", db_path, True):
                    break
    except Exception as e:
        print(f"[Storage] Lỗi khôi phục: {type(e).__name__}: {e}")
    finally:
        try:
            await client.close()
        except Exception:
            pass


# ---------------------------------------------------------------- backup
async def _cleanup_channel(channel, bot_id):
    gens, junk = await _scan(channel, bot_id)
    now = time.time()
    complete = sorted((g for g in gens if _complete(gens[g])), reverse=True)
    auto = [g for g in complete if gens[g]["tag"] != "manual"]
    manual = [g for g in complete if gens[g]["tag"] == "manual"]
    keep = set(auto[:KEEP_GENS]) | set(manual[:KEEP_MANUAL])
    for gen, g in gens.items():
        if gen in keep or (not _complete(g) and now - gen < 600):
            continue
        for msg in g["parts"].values():
            try:
                await msg.delete()
            except Exception:
                pass
    for msg in junk:
        try:
            await msg.delete()
        except Exception:
            pass


async def backup_category(bot, db_path, cat, tag=None):
    async with _backup_lock:
        TRACKER.flush()
        if cat == "uptime":
            UPTIME.tick()
        data, files = await asyncio.to_thread(_build_archive, cat, db_path)
        if not files:
            return None
        guild = bot.get_guild(STORAGE_GUILD_ID) or await bot.fetch_guild(STORAGE_GUILD_ID)
        ch = await _get_channel(bot, guild, cat)
        sha = hashlib.sha256(data).hexdigest()
        chunks = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)] or [b""]
        total = len(chunks)
        gen = int(time.time())
        sent = []
        try:
            for i, chunk in enumerate(chunks, 1):
                header = f"{MSG_PREFIX}|cat={cat}|gen={gen}|part={i}/{total}|sha={sha}|size={len(data)}"
                if tag:
                    header += f"|tag={tag}"
                f = discord.File(io.BytesIO(chunk), filename=f"{cat}_{gen}_p{i}of{total}.bin")
                sent.append(await ch.send(content=header, file=f))
        except Exception:
            for m in sent:
                try:
                    await m.delete()
                except Exception:
                    pass
            raise
        print(f"[Storage] 💾 {cat}{' (' + tag + ')' if tag else ''} -> #{ch.name} "
              f"(gen={gen}, {len(data):,} bytes, {total} phần).")
        try:
            await _cleanup_channel(ch, bot.user.id)
        except Exception as e:
            print(f"[Storage] Dọn kênh #{ch.name} lỗi: {type(e).__name__}: {e}")
        info = {"gen": gen, "tag": tag, "bytes": len(data), "parts": total,
                "channel": ch.name, "channel_id": ch.id, "files": files, "cat": cat}
        _LATEST[cat] = {"gen": gen, "tag": tag, "bytes": len(data), "channel_id": str(ch.id)}
        try:
            await _write_index(bot, guild, bot.user)
        except Exception as e:
            print(f"[Storage] Không ghi được sổ đăng ký: {type(e).__name__}: {e}")
        return info


async def backup_all(bot, db_path, tag=None):
    infos = []
    for cat in CATS:
        try:
            info = await backup_category(bot, db_path, cat, tag=tag)
            if info:
                infos.append(info)
        except Exception as e:
            print(f"[Storage] Backup '{cat}' lỗi: {type(e).__name__}: {e}")
    return infos


async def _loop(bot, db_path):
    await bot.wait_until_ready()
    state = {c: {"fp": None, "t": 0.0} for c in CATS}
    while not bot.is_closed():
        try:
            TRACKER.flush()
            UPTIME.tick()
            _save_state()
            now = time.time()
            for cat, cfg in CATS.items():
                st = state[cat]
                fp = _fingerprint(cat, db_path)
                if not fp or (fp == st["fp"]) or now - st["t"] < cfg["interval"]:
                    continue
                await backup_category(bot, db_path, cat)
                st["fp"], st["t"] = fp, time.time()
        except discord.Forbidden:
            print("[Storage] ❌ Bot thiếu quyền (Manage Channels, Manage Messages, Attach Files) trong server lưu trữ.")
            await asyncio.sleep(300)
        except Exception as e:
            print(f"[Storage] Lỗi: {type(e).__name__}: {e}")
            await asyncio.sleep(60)
        await asyncio.sleep(POLL_SECONDS)


async def _graceful_exit(bot):
    """Bot bị tắt (SIGTERM): lưu lần cuối rồi mới thoát để không mất thời gian online/coin."""
    global _closing
    if _closing:
        return
    _closing = True
    print("[Storage] Nhận tín hiệu tắt, đang lưu dữ liệu lần cuối...")
    try:
        UPTIME.tick()
        _save_state()
        await asyncio.wait_for(backup_all(bot, _db_path), timeout=20)
    except Exception as e:
        print(f"[Storage] Lưu lần cuối lỗi: {type(e).__name__}: {e}")
    await bot.close()


# ---------------------------------------------------------------- Cog
def _ctx_args(ctx, via):
    try:
        if via == "slash":
            kw = " ".join(f"{k}={v}" for k, v in (ctx.kwargs or {}).items() if v is not None)
            return f"/{ctx.command.qualified_name} {kw}".strip()
        return ctx.message.content or ""
    except Exception:
        return ""


def _parse(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _patch_bot_module():
    """Tự gắn nhật ký coin vào update_user_full() và tắt Supabase — KHÔNG cần sửa bot.py."""
    for mod in list(sys.modules.values()):
        try:
            if not (hasattr(mod, "update_user_full") and hasattr(mod, "ALL_PETS")):
                continue
        except Exception:
            continue
        if getattr(mod.update_user_full, "_ks_patched", False):
            return
        orig = mod.update_user_full
        db = getattr(mod, "DB_NAME", _db_path)

        def patched(user_id, coin, level, exp, streak, last_streak_time, history_tx, _orig=orig, _db=db):
            try:
                conn = sqlite3.connect(_db, timeout=5)
                row = conn.execute("SELECT coin FROM users WHERE user_id = ?", (str(user_id),)).fetchone()
                conn.close()
                if row is not None:
                    TRACKER.record_coin(user_id, row[0], coin, level)
            except Exception:
                pass
            return _orig(user_id, coin, level, exp, streak, last_streak_time, history_tx)

        patched._ks_patched = True
        mod.update_user_full = patched
        if hasattr(mod, "supabase"):
            mod.supabase = None                 # dữ liệu giờ nằm ở kênh Discord
        print("[Storage] Đã gắn nhật ký coin + tắt Supabase.")
        return


def _patch_noitu_coin():
    """noitu.py cộng coin thẳng vào DB (không qua update_user_full) -> gắn thêm nhật ký coin cho nó."""
    mod = sys.modules.get("noitu") or sys.modules.get("cogs.noitu")
    if mod is None or not hasattr(mod, "add_coin") or getattr(mod.add_coin, "_ks_patched", False):
        return
    orig = mod.add_coin

    def patched(user_id, amount, _orig=orig):
        new = _orig(user_id, amount)
        try:
            TRACKER.record_coin(user_id, new - int(amount), new)
        except Exception:
            pass
        return new

    patched._ks_patched = True
    mod.add_coin = patched
    print("[Storage] Đã gắn nhật ký coin cho Nối Từ / Vua TV.")


class StorageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        global _task
        _patch_bot_module()
        _apply_state()
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
        try:
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGTERM, lambda: asyncio.create_task(_graceful_exit(self.bot)))
        except (NotImplementedError, RuntimeError, ValueError):
            pass
        if _task is None or _task.done():
            _task = asyncio.create_task(_loop(self.bot, _db_path))

    @commands.Cog.listener("on_ready")
    async def _on_ready_state(self):
        """Khi bot online: nạp nốt biến của các extension, báo các phòng Ma Sói bị hủy, bật lưu trạng thái."""
        global _state_ready
        if _state_ready:
            return
        _patch_noitu_coin()
        _apply_state()
        stale = _state_raw.pop("__ww_channels", []) or []
        _state_ready = True
        for cid in stale:
            try:
                ch = self.bot.get_channel(int(cid))
                if ch:
                    await ch.send("🔄 Bot vừa khởi động lại nên **phòng/ván Ma Sói ở kênh này đã bị hủy**. Gõ `.masoi` để tạo lại nhé!",
                                  delete_after=60)
            except Exception:
                pass
        _save_state()

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

    # ---------- .botuptime
    @commands.hybrid_command(name="botuptime", aliases=["tgonline", "uptimebot"])
    async def botuptime(self, ctx):
        """Xem thời gian bot đã online."""
        UPTIME.tick()
        d = UPTIME.data
        embed = discord.Embed(title="⏱️ THỜI GIAN BOT ONLINE", color=0x57F287)
        embed.add_field(name="🟢 Lần chạy này", value=fmt_dur(time.time() - UPTIME.session_start), inline=True)
        embed.add_field(name="📊 Tổng cộng", value=fmt_dur(d.get("total_seconds", 0)), inline=True)
        embed.add_field(name="🔁 Số lần khởi động", value=f"{d.get('sessions', 1):,}", inline=True)
        embed.add_field(name="🏆 Lần chạy dài nhất", value=fmt_dur(d.get("longest_session", 0)), inline=True)
        embed.add_field(name="📅 Chạy lần đầu", value=str(d.get("first_start", "?"))[:10], inline=True)
        lat = self.bot.latency
        if lat == lat:
            embed.add_field(name="📡 Ping", value=f"{round(lat * 1000)} ms", inline=True)
        await ctx.send(embed=embed)

    # ---------- .savedata
    @commands.hybrid_command(name="savedata", aliases=["luudata", "backupdata"])
    async def savedata(self, ctx):
        """[OWNER] Lưu tổng quát toàn bộ dữ liệu ngay (dùng trước khi update bot)."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        if getattr(ctx, "interaction", None) is not None:
            await ctx.defer(ephemeral=True)
        try:
            infos = await backup_all(self.bot, _db_path, tag="manual")
        except discord.Forbidden:
            return await ctx.send("❌ Bot thiếu quyền trong server lưu trữ (Manage Channels / Manage Messages / Attach Files).")
        except Exception as e:
            return await ctx.send(f"❌ Lưu thất bại: `{type(e).__name__}: {e}`")
        if not infos:
            return await ctx.send("⚠️ Không có dữ liệu nào để lưu.")
        s = TRACKER.stats
        embed = discord.Embed(
            title="💾 ĐÃ LƯU TỔNG QUÁT TOÀN BỘ DỮ LIỆU",
            description="Bản này được **giữ riêng**, bản tự động không đẩy nó mất. Giờ có thể update bot an toàn.",
            color=0x2ECC71,
        )
        for i in infos:
            embed.add_field(name=f"📁 #{i['channel']}",
                            value=f"{i['bytes']:,} bytes • `{', '.join(i['files'])[:150]}`", inline=False)
        embed.add_field(name="⌨️ Lệnh đã ghi", value=f"**{s['total']:,}** lệnh • **{len(s['users'])}** người dùng", inline=True)
        embed.add_field(name="⏱️ Tổng online", value=fmt_dur(UPTIME.data.get("total_seconds", 0)), inline=True)
        await ctx.send(embed=embed)

    # ---------- .coinlog
    @commands.hybrid_command(name="coinlog", aliases=["lichsucoin"])
    async def coinlog(self, ctx, member: discord.Member = None):
        """[OWNER] Xem 10 lần đổi coin gần nhất của 1 người."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        member = member or ctx.author
        rows = []
        for raw in reversed(TRACKER.coin_log):
            d = _parse(raw)
            if d and d.get("user_id") == str(member.id):
                rows.append(d)
                if len(rows) >= 10:
                    break
        if not rows:
            return await ctx.send("Chưa có lịch sử coin nào của người này.")
        c = TRACKER.stats.get("coins", {}).get(str(member.id))
        lines = []
        for d in rows:
            where = f"<#{d['channel_id']}>" if d.get("channel_id") else "—"
            lines.append(f"`{d['t'][5:16]}` {d['old']:,} → {d['new']:,} (**{d['delta']:+,}**) • `{d.get('cmd') or 'nút bấm/khác'}` • {where}")
        head = f"💰 **Lịch sử coin của {member.display_name}**"
        if c:
            head += f"\nTổng thắng **{c.get('gained', 0):,}** • tổng thua **{c.get('lost', 0):,}** • **{c.get('changes', 0)}** lần đổi"
        await ctx.send(head + "\n" + "\n".join(lines))

    # ---------- .cmdlog
    @commands.hybrid_command(name="cmdlog", aliases=["lichsulenh"])
    async def cmdlog(self, ctx, member: discord.Member = None):
        """[OWNER] Xem 10 lệnh gần nhất."""
        if ctx.author.id != OWNER_USER_ID:
            return await ctx.send("❌ Chỉ **Owner Bot** mới được dùng lệnh này.", delete_after=5)
        rows = []
        for raw in reversed(TRACKER.log):
            d = _parse(raw)
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
    # 1) Khôi phục file còn thiếu từ các kênh Discord (trước khi bot dùng DB)
    await restore_all(_db_path)
    # 2) Nạp lại nhật ký + thời gian online từ file vừa khôi phục
    TRACKER.load()
    UPTIME.begin()
    _load_state_file()
    await bot.add_cog(StorageCog(bot))
