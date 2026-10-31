# ==============================================================================
# SUPABASE CLOUD MEMORY & BOT STATE OPTIMIZER — FULL PERSISTENT EDITION (v3)
# Kết nối Supabase (Postgres), lưu trữ token, trạng thái bot, thời gian hoạt động,
# NHẬT KÝ lệnh (ai dùng lệnh gì, ở server/kênh nào), NHẬT KÝ coin, thống kê
# người dùng, cấu hình server... và dọn dẹp bộ nhớ thông minh có nút xác nhận.
#
# CÁCH HOẠT ĐỘNG (tóm tắt):
#   • bot.py vẫn dùng SQLite (nhanh, code cũ giữ nguyên).
#   • File này PHẢN CHIẾU (mirror) toàn bộ SQLite lên Supabase:
#       - Mọi dòng của mọi bảng SQLite -> bảng "sql_rows" (cột tbl, key, data jsonb)
#       - Cấu trúc bảng                -> bảng "sql_schema"
#     Khi bot khởi động lại (mất file .db), dữ liệu được KHÔI PHỤC từ Supabase.
#   • Ngoài ra ghi thêm: bot_logs (command_logs, coin_logs, game_logs,
#     interaction_logs, command_errors, guild_events), user_stats, guilds,
#     runtime_state, bot_sessions, system_states (uptime)...
#
# CÀI ĐẶT:
#   1. pip install supabase     (thêm "supabase" vào requirements.txt, bỏ "pymongo")
#   2. Chạy file supabase_setup.sql trong Supabase > SQL Editor (1 lần).
#   3. Đặt biến môi trường (Render > Environment, KHÔNG viết khoá vào code):
#
# BIẾN MÔI TRƯỜNG:
#   SUPABASE_URL         https://xxxx.supabase.co
#   SUPABASE_KEY         khoá "service_role"/secret (khuyên dùng). Khoá publishable chỉ chạy
#                        được nếu bạn tắt RLS các bảng (xem cuối supabase_setup.sql).
#   SUPABASE_SYNC_SECONDS (tuỳ chọn) chu kỳ đồng bộ SQLite -> Supabase, mặc định 10 giây
#   SUPABASE_RESTORE_MODE (tuỳ chọn) "overwrite" (mặc định: cloud ghi đè file local khi khởi động)
#                        hoặc "missing" (chỉ bổ sung dòng còn thiếu)
#   OWNER_USER_ID        (tuỳ chọn) ID Discord của owner để dùng /syncdb, /coinlog
# ==============================================================================

import sys
import os
import re
import json
import copy
import time
import base64
import asyncio
import sqlite3
import threading
import collections
import contextvars
from datetime import datetime, timezone

from supabase import create_client
import discord
from discord import app_commands
from discord.ext import commands

try:
    import psutil
except ImportError:
    psutil = None


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).isoformat()


def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _j(o):
    """Chuyển đối tượng Python sang dạng JSON an toàn (datetime, bytes, set...)."""
    if isinstance(o, dict):
        return {str(k): _j(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_j(v) for v in o]
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return {"__b64": base64.b64encode(bytes(o)).decode()}
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


def _unj(v):
    if isinstance(v, dict) and set(v.keys()) == {"__b64"}:
        return base64.b64decode(v["__b64"])
    return v


# Thông tin kết nối Supabase — LẤY TỪ BIẾN MÔI TRƯỜNG (đừng hardcode khoá!)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SYNC_INTERVAL = max(3, _env_int("SUPABASE_SYNC_SECONDS", 10))
FULL_SYNC_INTERVAL = 60  # cứ 60 giây quét lại TOÀN BỘ bảng 1 lần (bắt cả file khác tự ghi SQLite)
RESTORE_MODE = os.getenv("SUPABASE_RESTORE_MODE", "overwrite").strip().lower()
OWNER_ID = _env_int("OWNER_USER_ID", 1404399385965498479)
PAGE = 1000  # Supabase trả tối đa 1000 dòng / lần

sb = None
_connected = False
_connect_lock = threading.Lock()
_START_TIME = time.time()
_SESSION_STARTED = _now()
_SESSION_ID = f"{int(_START_TIME)}"
_bot = None  # được gán khi setup(bot)
_total_uptime = 0.0


# ---------------- Hàm tiện ích truy cập Supabase ----------------
def _fetch_all(table, columns="*", **eq):
    """Đọc toàn bộ 1 bảng (tự phân trang)."""
    out, start = [], 0
    while True:
        q = sb.table(table).select(columns)
        for k, v in eq.items():
            q = q.eq(k, v)
        rows = q.order("id" if table not in ("sql_rows",) else "key").range(start, start + PAGE - 1).execute().data
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        start += PAGE


def _get_one(table, id_):
    r = sb.table(table).select("*").eq("id", str(id_)).limit(1).execute().data
    return r[0] if r else None


def _kv_get(table, id_):
    r = _get_one(table, id_)
    return r.get("data") if r else None


def _kv_upsert(table, items):
    """items: list[(id, data)] -> upsert theo lô."""
    rows = [{"id": str(i), "data": _j(d), "updated_at": _iso()} for i, d in items]
    for n in range(0, len(rows), 500):
        sb.table(table).upsert(rows[n:n + 500], on_conflict="id").execute()


def _count(table, kind=None):
    q = sb.table(table).select("id", count="exact")
    if kind:
        q = q.eq("kind", kind)
    return q.limit(1).execute().count or 0


def _try_connect():
    """Kết nối Supabase. An toàn khi gọi nhiều lần."""
    global sb, _connected, _total_uptime
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    with _connect_lock:
        if _connected:
            return True
        try:
            c = create_client(SUPABASE_URL, SUPABASE_KEY)
            c.table("system_states").select("id").limit(1).execute()  # kiểm tra bảng + quyền
            sb = c
            st = _kv_get("system_states", "bot_state") or {}
            _total_uptime = float(st.get("total_uptime_seconds", 0) or 0)
            _connected = True
            print("[Supabase] Kết nối cơ sở dữ liệu cloud thành công!")
            return True
        except Exception as e:
            print(f"[Supabase] Lỗi kết nối cơ sở dữ liệu: {e}")
            return False


if not SUPABASE_URL or not SUPABASE_KEY:
    print("[Supabase] ⚠️ Chưa đặt SUPABASE_URL / SUPABASE_KEY — bot vẫn chạy nhưng CHỈ lưu SQLite local (mất khi restart).")
else:
    _try_connect()


def is_connected():
    return _connected


# ==============================================================================
# 0. HÀNG ĐỢI NHẬT KÝ + THỐNG KÊ NGƯỜI DÙNG (ghi theo lô, không làm lag bot)
# ==============================================================================

_log_queues = {}
_log_lock = threading.Lock()


def log_event(collection, doc):
    """Đưa 1 bản ghi nhật ký vào hàng đợi; luồng nền sẽ insert hàng loạt lên Supabase."""
    try:
        doc.setdefault("time", _now())
        with _log_lock:
            q = _log_queues.get(collection)
            if q is None:
                q = collections.deque(maxlen=20000)
                _log_queues[collection] = q
            q.append(doc)
    except Exception:
        pass


def pending_logs():
    with _log_lock:
        return sum(len(q) for q in _log_queues.values())


def _flush_logs():
    if not _connected:
        return
    with _log_lock:
        names = list(_log_queues.keys())
    for name in names:
        docs = []
        with _log_lock:
            q = _log_queues[name]
            while q and len(docs) < 1000:
                docs.append(q.popleft())
        if not docs:
            continue
        try:
            rows = []
            for d in docs:
                d2 = dict(d)
                t = d2.pop("time", None) or _now()
                rows.append({"kind": name, "created_at": _iso(t) if isinstance(t, datetime) else str(t),
                             "data": _j(d2)})
            sb.table("bot_logs").insert(rows).execute()
        except Exception as e:
            print(f"[Supabase] Lỗi ghi nhật ký '{name}': {e}")
            with _log_lock:
                q = _log_queues[name]
                for d in reversed(docs):
                    q.appendleft(d)


_stat_lock = threading.Lock()
_stat_inc = {}
_stat_set = {}


def bump_user(user_id, inc=None, set_=None):
    """Cộng dồn thống kê của 1 người dùng (messages, commands...) rồi ghi theo lô."""
    try:
        uid = str(user_id)
        with _stat_lock:
            if inc:
                d = _stat_inc.setdefault(uid, {})
                for k, v in inc.items():
                    d[k] = d.get(k, 0) + v
            if set_:
                _stat_set.setdefault(uid, {}).update(set_)
    except Exception:
        pass


def _dot_apply(doc, path, value, add=False):
    """Ghi/cộng giá trị vào dict lồng nhau theo đường dẫn 'a.b.c'."""
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    if add:
        cur[parts[-1]] = (cur.get(parts[-1]) or 0) + value
    else:
        cur[parts[-1]] = value


def _flush_stats():
    if not _connected:
        return
    with _stat_lock:
        inc = dict(_stat_inc)
        sets = dict(_stat_set)
        _stat_inc.clear()
        _stat_set.clear()
    if not inc and not sets:
        return
    try:
        uids = list(set(inc) | set(sets))
        existing = {}
        for n in range(0, len(uids), 200):
            rows = sb.table("user_stats").select("id,data").in_("id", uids[n:n + 200]).execute().data
            for r in rows:
                existing[r["id"]] = r["data"] or {}
        items = []
        for uid in uids:
            doc = existing.get(uid) or {}
            doc.setdefault("first_seen", _iso())
            for k, v in (inc.get(uid) or {}).items():
                _dot_apply(doc, k, v, add=True)
            for k, v in (sets.get(uid) or {}).items():
                _dot_apply(doc, k, _j(v))
            items.append((uid, doc))
        _kv_upsert("user_stats", items)
    except Exception as e:
        print(f"[Supabase] Lỗi ghi thống kê người dùng: {e}")
        with _stat_lock:
            for uid, d in inc.items():
                t = _stat_inc.setdefault(uid, {})
                for k, v in d.items():
                    t[k] = t.get(k, 0) + v
            for uid, d in sets.items():
                merged = dict(d)
                merged.update(_stat_set.get(uid, {}))
                _stat_set[uid] = merged


# Ngữ cảnh lệnh đang chạy (để ghi coin_logs biết coin đổi do lệnh nào)
CURRENT_COMMAND = contextvars.ContextVar("ks_current_command", default=None)


def log_coin_change(user_id, old_coin, new_coin, level=None):
    """Ghi lại mỗi lần coin của 1 người thay đổi (ai, trước, sau, chênh lệch, lệnh nào)."""
    try:
        ctx = CURRENT_COMMAND.get()
        log_event("coin_logs", {
            "user_id": str(user_id),
            "old": old_coin,
            "new": new_coin,
            "delta": new_coin - old_coin,
            "level": level,
            "command": ctx.get("command") if ctx else None,
            "guild_id": ctx.get("guild_id") if ctx else None,
            "channel_id": ctx.get("channel_id") if ctx else None,
        })
    except Exception:
        pass


def log_game(game, win_message):
    """Ghi kết quả 1 ván Ma Sói (người chơi, vai trò, phe thắng)."""
    try:
        ctx = getattr(game, "ctx", None)
        guild = getattr(ctx, "guild", None)
        channel = getattr(ctx, "channel", None)
        players = []
        for uid, d in game.players.items():
            member = d.get("member")
            players.append({
                "user_id": str(uid),
                "name": getattr(member, "display_name", None),
                "role": d.get("role"),
                "alive": bool(d.get("alive")),
            })
        log_event("game_logs", {
            "game": "masoi",
            "result": str(win_message)[:300],
            "guild_id": str(guild.id) if guild else None,
            "guild_name": guild.name if guild else None,
            "channel_id": str(channel.id) if channel else None,
            "player_count": len(players),
            "players": players,
        })
    except Exception:
        pass


# ==============================================================================
# 1. HỆ THỐNG LƯU TRỮ TOKEN VĨNH VIỄN TRÊN SUPABASE (THAY THẾ tokens.json)
# ==============================================================================

class CloudTokenStorage:
    @staticmethod
    def load_all() -> dict:
        if sb is None:
            return {}
        try:
            return {str(r["id"]): r.get("token", "") for r in _fetch_all("bot_tokens")}
        except Exception:
            return {}

    @staticmethod
    def save(user_id: str, token: str):
        if sb is not None:
            try:
                sb.table("bot_tokens").upsert(
                    {"id": str(user_id), "token": token, "updated_at": _iso()},
                    on_conflict="id").execute()
            except Exception:
                pass

    @staticmethod
    def remove(user_id: str):
        if sb is not None:
            try:
                sb.table("bot_tokens").delete().eq("id", str(user_id)).execute()
            except Exception:
                pass


# ==============================================================================
# 2. SUPABASE SMART MEMORY (DICTIONARY TỰ ĐỘNG ĐỒNG BỘ CLOUD)
# ==============================================================================

class MongoMemory(dict):
    """
    Bộ nhớ thông minh lưu RAM và đồng bộ lên Supabase (bảng bot_storage).
    (Giữ tên lớp MongoMemory để bot.py cũ không phải sửa.)
    - Truy cập key nào thì coi là "có thể đã bị sửa bên trong" (user_data[uid]["money"] += 1).
    - Cứ 15 giây so sánh với bản gốc, chỉ ghi những key thật sự thay đổi (upsert, chạy thread riêng).
    - Key bị xả khỏi RAM sẽ tự đọc lại từ DB khi cần (không bao giờ ghi đè bằng 0).
    """
    def __init__(self):
        super().__init__()
        self._dirty = set()
        self._snap = {}
        self._task = None
        self.load_all_from_db()

    @staticmethod
    def _dbkey(k):
        return str(k) if isinstance(k, int) else k

    def load_all_from_db(self):
        if sb is None:
            return
        try:
            for doc in _fetch_all("bot_storage"):
                key = doc.get("id")
                if key is None:
                    continue
                if isinstance(key, str) and key.isdigit():
                    key = int(key)
                data = doc.get("data") or {}
                super().__setitem__(key, data)
                self._snap[key] = copy.deepcopy(data)
            print(f"[Supabase] Đã đồng bộ thành công {len(self)} bản ghi dữ liệu vào RAM!")
        except Exception as e:
            print(f"[Supabase] Lỗi tải dữ liệu lưu trữ: {e}")

    def __getitem__(self, key):
        if not super().__contains__(key):
            doc = None
            if sb is not None:
                try:
                    doc = _get_one("bot_storage", self._dbkey(key))
                except Exception:
                    doc = None
            data = doc["data"] if doc and doc.get("data") is not None else {"money": 0, "exp": 0}
            super().__setitem__(key, data)
            self._snap[key] = copy.deepcopy(data) if doc else None
        self._dirty.add(key)  # có thể bị sửa bên trong
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._dirty.add(key)

    def _collect_ops(self):
        keys, self._dirty = list(self._dirty), set()
        ops = []
        for k in keys:
            if not super().__contains__(k):
                continue
            v = super().__getitem__(k)
            if self._snap.get(k) == v:
                continue
            self._snap[k] = copy.deepcopy(v)
            payload = copy.deepcopy(v) if isinstance(v, dict) else {"value": v}
            ops.append((self._dbkey(k), payload))
        return keys, ops

    async def flush(self):
        if sb is None:
            return
        keys, ops = self._collect_ops()
        if not ops:
            return
        try:
            await asyncio.to_thread(_kv_upsert, "bot_storage", ops)
        except Exception as e:
            print(f"[Supabase] Lỗi lưu: {e}")
            self._dirty.update(keys)  # thử lại lần sau
            for k in keys:
                self._snap.pop(k, None)

    async def _loop(self):
        last_gc = time.time()
        while True:
            await asyncio.sleep(15)
            await self.flush()
            if time.time() - last_gc > 3600 and len(self) > 5000:
                last_gc = time.time()
                await self.flush()
                removed = list(self.keys())[:-2000]
                for k in removed:
                    super().pop(k, None)  # an toàn vì sẽ tự tải lại từ DB
                    self._snap.pop(k, None)
                print(f"[Memory Optimizer] Đã xả bớt {len(removed)} mục ít dùng khỏi RAM.")

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        await self.flush()


shared_cloud_memory = MongoMemory()


# ==============================================================================
# 3. PHẢN CHIẾU TOÀN BỘ SQLITE LÊN SUPABASE (users, user_pets, user_items, ...)
# ==============================================================================

_sqlite_path = None
_snap = {}          # {bảng: {khóa: dòng}} — bản đã đẩy lên cloud lần gần nhất
_schema_snap = {}
_dirty = True
_sync_lock = threading.Lock()
last_sync_info = {"time": None, "written": 0, "error": None}


def mark_dirty():
    """Báo cho luồng nền biết SQLite vừa có thay đổi."""
    global _dirty
    _dirty = True


class TrackedConnection(sqlite3.Connection):
    """Kết nối SQLite tự báo 'đã đổi dữ liệu' mỗi lần commit()."""
    def commit(self):
        super().commit()
        mark_dirty()


def _q(name):
    return '"' + str(name).replace('"', '""') + '"'


def _fname(col):
    return "__id" if col == "_id" else col


def _safe(v):
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 2 ** 63:
        return str(v)
    return v


def _list_tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]


def _table_info(conn, table):
    info = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
    cols = [r[1] for r in info]
    pk = [r[1] for r in sorted((r for r in info if r[5] > 0), key=lambda r: r[5])]
    return cols, pk


def _read_table(conn, table):
    """Trả về (cột, khóa chính, {khóa_chuỗi: dòng})."""
    cols, pk = _table_info(conn, table)
    if not cols:
        return cols, pk, {}
    if pk:
        idx = [cols.index(c) for c in pk]
        rows = conn.execute(f"SELECT * FROM {_q(table)}").fetchall()
        data = {":".join(str(r[i]) for i in idx): r for r in rows}
    else:
        rows = conn.execute(f"SELECT rowid, * FROM {_q(table)}").fetchall()
        data = {str(r[0]): r for r in rows}
    return cols, pk, data


def _row_to_doc(cols, pk, row):
    doc = {}
    vals = row
    if not pk:
        doc["__rowid"] = row[0]
        vals = row[1:]
    for c, v in zip(cols, vals):
        doc[_fname(c)] = _j(_safe(v))
    return doc


def sync_once(force=False, block=False):
    """Đẩy phần thay đổi của SQLite lên Supabase (chạy ở luồng nền). Trả về số thao tác đã ghi."""
    global _dirty
    if not _connected or not _sqlite_path or not os.path.exists(_sqlite_path):
        return 0
    if not (_dirty or force):
        return 0
    if not _sync_lock.acquire(blocking=block):
        return 0
    written = 0
    conn = None
    try:
        _dirty = False
        conn = sqlite3.connect(_sqlite_path, timeout=10)
        now = _iso()

        # --- cấu trúc bảng / index ---
        schema_rows = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
        ops, new_schema = [], {}
        for typ, name, tbl, sql in schema_rows:
            key = f"{typ}:{name}"
            new_schema[key] = sql
            if _schema_snap.get(key) != sql:
                ops.append({"id": key, "type": typ, "name": name, "tbl_name": tbl,
                            "sql": sql, "updated_at": now})
        if ops:
            sb.table("sql_schema").upsert(ops, on_conflict="id").execute()
            _schema_snap.update(new_schema)
            written += len(ops)

        # --- dữ liệu từng bảng ---
        for t in _list_tables(conn):
            try:
                cols, pk, current = _read_table(conn, t)
            except sqlite3.Error as e:
                print(f"[Supabase] Bỏ qua bảng '{t}': {e}")
                continue
            snap = _snap.setdefault(t, {})
            upserts = []
            for k, row in current.items():
                if snap.get(k) != row:
                    upserts.append({"tbl": t, "key": k, "data": _row_to_doc(cols, pk, row),
                                    "updated_at": now})
            deletes = [k for k in snap if k not in current]
            if not upserts and not deletes:
                continue
            for i in range(0, len(upserts), 500):
                sb.table("sql_rows").upsert(upserts[i:i + 500], on_conflict="tbl,key").execute()
            for i in range(0, len(deletes), 200):
                sb.table("sql_rows").delete().eq("tbl", t).in_("key", deletes[i:i + 200]).execute()
            _snap[t] = dict(current)
            written += len(upserts) + len(deletes)

        last_sync_info.update({"time": _now(), "written": written, "error": None})
    except Exception as e:
        _dirty = True  # thử lại ở chu kỳ sau
        last_sync_info["error"] = str(e)[:200]
        print(f"[Supabase] Lỗi đồng bộ SQLite -> Supabase: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _sync_lock.release()
    return written


def restore_sqlite():
    """Khôi phục SQLite từ Supabase (gọi 1 lần lúc khởi động, sau init_db())."""
    if not _connected or not _sqlite_path:
        return
    mode_missing = (RESTORE_MODE == "missing")
    conn = sqlite3.connect(_sqlite_path, timeout=30)
    restored, failed = 0, 0
    try:
        docs = _fetch_all("sql_schema")
        docs.sort(key=lambda d: 0 if d.get("type") == "table" else 1)
        for d in docs:
            try:
                conn.execute(d["sql"])
            except sqlite3.Error:
                pass  # đã tồn tại
            if not mode_missing and d.get("sql"):
                _schema_snap[f"{d.get('type')}:{d.get('name')}"] = d["sql"]
        conn.commit()

        verb = "INSERT OR IGNORE" if mode_missing else "INSERT OR REPLACE"
        for t in _list_tables(conn):
            rows_docs = _fetch_all("sql_rows", tbl=t)
            if not rows_docs:
                continue
            cols, pk = _table_info(conn, t)
            for rd in rows_docs:
                d = rd.get("data") or {}
                names, vals = [], []
                if not pk and "__rowid" in d:
                    names.append("rowid")
                    vals.append(d["__rowid"])
                for c in cols:
                    f = _fname(c)
                    if f in d:
                        names.append(c)
                        vals.append(_unj(d[f]))
                if not names:
                    continue
                col_sql = ",".join("rowid" if n == "rowid" else _q(n) for n in names)
                sql = f"{verb} INTO {_q(t)} ({col_sql}) VALUES ({','.join('?' * len(names))})"
                try:
                    conn.execute(sql, vals)
                    restored += 1
                except sqlite3.Error:
                    failed += 1
            conn.commit()
            if not mode_missing:
                _, _, current = _read_table(conn, t)
                keys = {rd["key"] for rd in rows_docs}
                _snap[t] = {k: r for k, r in current.items() if k in keys}
        print(f"[Supabase] Đã khôi phục {restored} dòng từ cloud vào SQLite local"
              + (f" ({failed} dòng lỗi bị bỏ qua)" if failed else "") + ".")
    finally:
        conn.close()


def attach_sqlite(path):
    """Gọi 1 lần ngay sau init_db() trong bot.py: khôi phục dữ liệu từ cloud rồi bật đồng bộ nền."""
    global _sqlite_path
    _sqlite_path = path
    if _connected:
        try:
            restore_sqlite()
        except Exception as e:
            print(f"[Supabase] Lỗi khôi phục SQLite: {e}")
    mark_dirty()
    start_worker()


def pull_user(user_id):
    """Đọc 1 user từ cloud (dùng khi local chưa có user này)."""
    if not _connected:
        return None
    try:
        r = (sb.table("sql_rows").select("data").eq("tbl", "users")
             .eq("key", str(user_id)).limit(1).execute().data)
        return r[0]["data"] if r else None
    except Exception:
        return None


# ==============================================================================
# 4. LƯU CÁC BIẾN TRONG RAM CỦA BOT (AFK, điểm danh, kênh bị cấm, kênh lì xì...)
# ==============================================================================

_persisters = {}


def persist_dict(name, obj, key_type=str, enc=None, dec=None):
    """Đăng ký 1 dict trong bot.py để tự lưu lên Supabase (bảng runtime_state)
    và tự nạp lại khi khởi động. Nạp xong sửa TRỰC TIẾP trên chính dict đó."""
    entry = {"obj": obj, "key_type": key_type,
             "enc": enc or (lambda v: v), "dec": dec or (lambda v: v), "last": None}
    _persisters[name] = entry
    if _connected:
        try:
            data = _kv_get("runtime_state", name)
            if isinstance(data, dict):
                obj.clear()
                for k, v in data.items():
                    try:
                        obj[key_type(k)] = entry["dec"](v)
                    except Exception:
                        pass
                entry["last"] = copy.deepcopy(_j(_encode(entry)))
                print(f"[Supabase] Đã nạp '{name}' ({len(obj)} mục) từ cloud.")
        except Exception as e:
            print(f"[Supabase] Lỗi nạp '{name}': {e}")


def _encode(entry):
    return {str(k): entry["enc"](v) for k, v in dict(entry["obj"]).items()}


def _flush_state():
    if not _connected:
        return
    for name, e in list(_persisters.items()):
        try:
            data = _j(_encode(e))
            if data == e["last"]:
                continue
            _kv_upsert("runtime_state", [(name, data)])
            e["last"] = copy.deepcopy(data)
        except RuntimeError:
            continue  # dict đang bị sửa lúc duyệt, chu kỳ sau thử lại
        except Exception as ex:
            print(f"[Supabase] Lỗi lưu '{name}': {ex}")


# ==============================================================================
# 5. TRẠNG THÁI BOT / THỜI GIAN HOẠT ĐỘNG / DANH SÁCH SERVER
# ==============================================================================

_last_hb = time.time()


def _heartbeat(final=False):
    global _last_hb, _total_uptime
    if not _connected:
        return
    now_t = time.time()
    dt = max(0.0, now_t - _last_hb)
    info = {}
    b = _bot
    if b is not None:
        try:
            info["guilds"] = len(b.guilds)
            info["members"] = sum(g.member_count or 0 for g in b.guilds)
            lat = b.latency
            info["ping_ms"] = round(lat * 1000) if lat == lat else None
            if b.user:
                info["bot_name"] = b.user.name
                info["bot_id"] = str(b.user.id)
        except Exception:
            pass
    if psutil is not None:
        try:
            info["ram_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
        except Exception:
            pass
    info.update({
        "status": "stopped" if final else "online",
        "last_heartbeat": _iso(),
        "session_id": _SESSION_ID,
        "session_started_at": _iso(_SESSION_STARTED),
        "total_uptime_seconds": _total_uptime + dt,
    })
    try:
        _kv_upsert("system_states", [("bot_state", info)])
        _kv_upsert("bot_sessions", [(_SESSION_ID, {
            "started_at": _iso(_SESSION_STARTED), "last_heartbeat": _iso(),
            "uptime_seconds": int(now_t - _START_TIME), "ended": bool(final)})])
        _total_uptime += dt
        _last_hb = now_t
    except Exception as e:
        print(f"[Supabase] Lỗi ghi trạng thái bot: {e}")


def _refresh_guilds():
    b = _bot
    if b is None or not _connected:
        return
    try:
        old = {r["id"]: (r.get("data") or {}) for r in _fetch_all("guilds")}
        items = []
        for g in list(b.guilds):
            doc = old.get(str(g.id), {})
            doc.update({
                "name": g.name,
                "member_count": g.member_count,
                "owner_id": str(g.owner_id) if g.owner_id else None,
                "icon": str(g.icon.url) if g.icon else None,
                "text_channels": len(g.text_channels),
                "left": False,
            })
            doc.setdefault("first_seen", _iso())
            items.append((str(g.id), doc))
        if items:
            _kv_upsert("guilds", items)
    except Exception as e:
        print(f"[Supabase] Lỗi ghi danh sách server: {e}")


def _mark_guild_left(guild_id):
    doc = _kv_get("guilds", guild_id) or {}
    doc["left"] = True
    _kv_upsert("guilds", [(str(guild_id), doc)])


# ==============================================================================
# 6. LUỒNG NỀN (mọi thao tác cloud nặng đều chạy ở đây, không chặn bot)
# ==============================================================================

_worker_thread = None
_stop = threading.Event()


def _worker():
    last = {"sync": 0.0, "full": 0.0, "logs": 0.0, "hb": 0.0, "guilds": 0.0, "retry": time.time()}
    while not _stop.is_set():
        now_t = time.time()
        try:
            if not _connected and SUPABASE_URL and SUPABASE_KEY and now_t - last["retry"] >= 60:
                last["retry"] = now_t
                _try_connect()
            if _connected:
                if now_t - last["logs"] >= 5:
                    last["logs"] = now_t
                    _flush_logs()
                    _flush_stats()
                    _flush_state()
                if now_t - last["sync"] >= SYNC_INTERVAL:
                    last["sync"] = now_t
                    force = now_t - last["full"] >= FULL_SYNC_INTERVAL
                    if force:
                        last["full"] = now_t
                    sync_once(force=force)
                if now_t - last["hb"] >= 30:
                    last["hb"] = now_t
                    _heartbeat()
                if now_t - last["guilds"] >= 300 and _bot is not None:
                    last["guilds"] = now_t
                    _refresh_guilds()
        except Exception as e:
            print(f"[Supabase] Lỗi luồng nền: {e}")
        _stop.wait(1)


def start_worker():
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _stop.clear()
    _worker_thread = threading.Thread(target=_worker, name="supabase-worker", daemon=True)
    _worker_thread.start()


def shutdown():
    """Gọi khi bot tắt: ghi nốt mọi thứ còn tồn đọng."""
    try:
        _stop.set()
        if _connected:
            _flush_logs()
            _flush_stats()
            _flush_state()
            sync_once(force=True, block=True)
            _heartbeat(final=True)
            print("[Supabase] Đã lưu nốt dữ liệu trước khi tắt bot.")
    except Exception as e:
        print(f"[Supabase] Lỗi khi tắt: {e}")


# ==============================================================================
# 7. GIAO DIỆN NÚT BẤM XÁC NHẬN DỌN DẸP BỘ NHỚ (CONFIRMATION VIEW)
# ==============================================================================

class ClearMemoryConfirmView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id

    @discord.ui.button(label="Xác nhận dọn dẹp", style=discord.ButtonStyle.danger, emoji="🧹")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Bạn không có quyền bấm nút này!", ephemeral=True)
            return

        cleared_count = 0
        try:
            await shared_cloud_memory.flush()  # lưu trước khi xả để không mất gì
            keys_to_purge = [k for k, v in shared_cloud_memory.items() if isinstance(v, dict) and len(v) == 0]
            for k in keys_to_purge:
                shared_cloud_memory.pop(k, None)
                cleared_count += 1
        except Exception:
            pass

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="🧹 ĐÃ DỌN DẸP BỘ NHỚ THÀNH CÔNG",
            description=f"✅ Đã giải phóng **{cleared_count}** mục dữ liệu rác khỏi bộ nhớ đệm RAM.\n💾 Hệ thống Cloud Supabase vẫn an toàn tuyệt đối.",
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Hủy bỏ", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Bạn không có quyền bấm nút này!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(title="❌ ĐÃ HỦY LỆNH DỌN DẸP", description="Không có dữ liệu nào bị thay đổi.", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


# ==============================================================================
# 8. DISCORD COG: LỆNH /memory /clearmemory /syncdb /coinlog + GHI NHẬT KÝ TỰ ĐỘNG
# ==============================================================================

def _fmt_dur(seconds):
    seconds = int(seconds or 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days} ngày, {hours} giờ, {minutes} phút, {secs} giây"


def _collect_stats():
    s = {"connected": _connected}
    if not _connected:
        return s
    try:
        s["tokens"] = _count("bot_tokens")
        s["storage_docs"] = _count("bot_storage")
        s["users_docs"] = (sb.table("sql_rows").select("key", count="exact")
                           .eq("tbl", "users").limit(1).execute().count or 0)
        s["command_logs"] = _count("bot_logs", "command_logs")
        s["coin_logs"] = _count("bot_logs", "coin_logs")
        s["user_stats"] = _count("user_stats")
        st = _kv_get("system_states", "bot_state") or {}
        s["total_uptime"] = st.get("total_uptime_seconds", 0)
    except Exception as e:
        s["error"] = str(e)[:150]
    return s


_recent_cmds = {}
_SAFE_KEY = re.compile(r"[.$\s]")


def _record_command(user, command_name, guild, channel, content, source):
    """Ghi 1 lệnh đã dùng (ai, lệnh gì, server nào, kênh nào). Có chống ghi trùng prefix/slash."""
    try:
        now_t = time.time()
        k = (user.id, command_name, getattr(channel, "id", None))
        last = _recent_cmds.get(k)
        if last is not None and now_t - last < 1.5:
            return
        _recent_cmds[k] = now_t
        if len(_recent_cmds) > 1000:
            for kk in [kk for kk, tt in _recent_cmds.items() if now_t - tt > 10]:
                _recent_cmds.pop(kk, None)

        gid = str(guild.id) if guild else None
        cid = str(channel.id) if channel is not None and getattr(channel, "id", None) else None
        log_event("command_logs", {
            "user_id": str(user.id),
            "username": str(user),
            "command": command_name,
            "content": (content or "")[:300],
            "guild_id": gid,
            "guild_name": guild.name if guild else None,
            "channel_id": cid,
            "channel_name": getattr(channel, "name", None) or "DM",
            "source": source,
        })
        safe = _SAFE_KEY.sub("_", command_name or "unknown")
        inc = {"commands": 1, f"cmd_counts.{safe}": 1}
        sets = {"username": str(user), "last_seen": _now(), "last_command": command_name,
                "last_channel_id": cid}
        if guild:
            inc[f"per_guild.{gid}.commands"] = 1
            sets[f"per_guild.{gid}.name"] = guild.name
            sets["last_guild_id"] = gid
            sets["last_guild_name"] = guild.name
        bump_user(user.id, inc, sets)
    except Exception:
        pass


class DatabaseManagementCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_load(self):
        global _bot
        _bot = self.bot
        shared_cloud_memory.start()
        start_worker()

        # Ghi nhớ "lệnh nào đang chạy" để coin_logs biết coin đổi do lệnh nào.
        prev = getattr(self.bot, "_before_invoke", None)

        async def _ks_before_invoke(ctx):
            try:
                CURRENT_COMMAND.set({
                    "command": ctx.command.qualified_name if ctx.command else None,
                    "guild_id": str(ctx.guild.id) if ctx.guild else None,
                    "channel_id": str(ctx.channel.id) if ctx.channel else None,
                })
            except Exception:
                pass
            if prev is not None:
                await prev(ctx)

        self.bot.before_invoke(_ks_before_invoke)

    async def cog_unload(self):
        await shared_cloud_memory.stop()
        await asyncio.to_thread(shutdown)

    # ---------------- GHI NHẬT KÝ TỰ ĐỘNG ----------------
    @commands.Cog.listener("on_message")
    async def _log_message(self, message):
        if message.author.bot:
            return
        inc = {"messages": 1}
        sets = {"username": str(message.author), "last_seen": _now(),
                "last_channel_id": str(message.channel.id)}
        if message.guild:
            gid = str(message.guild.id)
            inc[f"per_guild.{gid}.messages"] = 1
            sets[f"per_guild.{gid}.name"] = message.guild.name
            sets["last_guild_id"] = gid
            sets["last_guild_name"] = message.guild.name
        bump_user(message.author.id, inc, sets)

    @commands.Cog.listener("on_command_completion")
    async def _log_command_done(self, ctx):
        name = ctx.command.qualified_name if ctx.command else "?"
        content = getattr(getattr(ctx, "message", None), "content", "") or f"/{name}"
        source = "slash" if getattr(ctx, "interaction", None) is not None else "prefix"
        _record_command(ctx.author, name, ctx.guild, ctx.channel, content, source)

    @commands.Cog.listener("on_app_command_completion")
    async def _log_app_command_done(self, interaction, command):
        _record_command(interaction.user, command.qualified_name, interaction.guild,
                        interaction.channel, f"/{command.qualified_name}", "slash")

    @commands.Cog.listener("on_command_error")
    async def _log_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        try:
            log_event("command_errors", {
                "user_id": str(ctx.author.id),
                "username": str(ctx.author),
                "command": ctx.command.qualified_name if ctx.command else None,
                "error_type": type(error).__name__,
                "error": str(error)[:300],
                "guild_id": str(ctx.guild.id) if ctx.guild else None,
                "channel_id": str(ctx.channel.id) if ctx.channel else None,
            })
        except Exception:
            pass

    @commands.Cog.listener("on_interaction")
    async def _log_interaction(self, interaction):
        try:
            if interaction.type not in (discord.InteractionType.component,
                                        discord.InteractionType.modal_submit):
                return
            data = interaction.data or {}
            log_event("interaction_logs", {
                "user_id": str(interaction.user.id),
                "username": str(interaction.user),
                "type": interaction.type.name,
                "custom_id": str(data.get("custom_id"))[:100],
                "guild_id": str(interaction.guild.id) if interaction.guild else None,
                "channel_id": str(interaction.channel.id) if interaction.channel else None,
            })
        except Exception:
            pass

    @commands.Cog.listener("on_guild_join")
    async def _guild_join(self, guild):
        log_event("guild_events", {"event": "join", "guild_id": str(guild.id), "guild_name": guild.name,
                                   "member_count": guild.member_count})
        await asyncio.to_thread(_refresh_guilds)

    @commands.Cog.listener("on_guild_remove")
    async def _guild_remove(self, guild):
        log_event("guild_events", {"event": "leave", "guild_id": str(guild.id), "guild_name": guild.name})
        if _connected:
            try:
                await asyncio.to_thread(_mark_guild_left, guild.id)
            except Exception:
                pass

    @commands.Cog.listener("on_ready")
    async def _ready_refresh(self):
        await asyncio.to_thread(_refresh_guilds)

    # ---------------- CÁC LỆNH SLASH ----------------
    @app_commands.command(name="memory", description="[HỆ THỐNG] Kiểm tra dung lượng RAM, trạng thái kết nối Cloud & dữ liệu nặng")
    async def check_memory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uptime_str = _fmt_dur(time.time() - self.start_time)
        s = await asyncio.to_thread(_collect_stats)
        ram_items_count = len(shared_cloud_memory)

        embed = discord.Embed(
            title="📊 TRẠNG THÁI BỘ NHỚ & CLOUD DATABASE",
            color=discord.Color.from_rgb(52, 152, 219)
        )
        embed.add_field(name="🟢 Trạng thái Cloud",
                        value="`Supabase Connected`" if s["connected"] else "`🔴 Offline (đang chỉ lưu SQLite)`",
                        inline=True)
        embed.add_field(name="⏱️ Thời gian chạy bot (phiên này)", value=f"`{uptime_str}`", inline=False)
        if s["connected"]:
            embed.add_field(name="🕰️ Tổng thời gian hoạt động từ trước đến nay",
                            value=f"`{_fmt_dur(s.get('total_uptime', 0))}`", inline=False)
            embed.add_field(name="🔑 Tokens đã lưu vĩnh viễn", value=f"**{s.get('tokens', 0)}** tài khoản", inline=True)
            embed.add_field(name="👤 Người chơi đã lưu", value=f"**{s.get('users_docs', 0)}** người", inline=True)
            embed.add_field(name="📦 Bản ghi bot_storage", value=f"**{s.get('storage_docs', 0)}** documents", inline=True)
            embed.add_field(name="📝 Nhật ký lệnh", value=f"**{s.get('command_logs', 0)}** dòng", inline=True)
            embed.add_field(name="💰 Nhật ký coin", value=f"**{s.get('coin_logs', 0)}** dòng", inline=True)
            embed.add_field(name="📈 Thống kê người dùng", value=f"**{s.get('user_stats', 0)}** người", inline=True)
            ls = last_sync_info.get("time")
            sync_txt = ls.strftime("%H:%M:%S UTC") if ls else "chưa có"
            if last_sync_info.get("error"):
                sync_txt += f" ⚠️ {last_sync_info['error'][:80]}"
            embed.add_field(name="🔄 Đồng bộ SQLite→Supabase gần nhất", value=f"`{sync_txt}`", inline=True)
            embed.add_field(name="⏳ Nhật ký đang chờ ghi", value=f"**{pending_logs()}**", inline=True)
        embed.add_field(name="🧠 Bộ nhớ đệm RAM", value=f"**{ram_items_count}** active keys", inline=True)
        embed.set_footer(text="Hệ thống tự động đồng bộ hóa thời gian thực — Safe Anti-Ban")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="clearmemory", description="[QUẢN TRỊ] Dọn dẹp các dữ liệu rác, tối ưu hóa bộ nhớ RAM của bot")
    async def clear_memory_cmd(self, interaction: discord.Interaction):
        is_admin = bool(interaction.guild and interaction.user.guild_permissions.administrator)
        if interaction.user.id != OWNER_ID and not is_admin:
            await interaction.response.send_message("❌ Chỉ Owner Bot hoặc Admin server mới được dùng lệnh này.", ephemeral=True)
            return
        embed = discord.Embed(
            title="⚠️ XÁC NHẬN DỌN DẸP BỘ NHỚ",
            description="Bạn có chắc chắn muốn tiến hành dọn dẹp RAM, quét và giải phóng các tiến trình lưu trữ không cần thiết không?\n\n*(Dữ liệu quan trọng và Token trên Cloud Supabase sẽ được giữ nguyên an toàn).*",
            color=discord.Color.orange()
        )
        view = ClearMemoryConfirmView(interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="syncdb", description="[OWNER] Ép đồng bộ toàn bộ dữ liệu SQLite lên Supabase ngay lập tức")
    async def syncdb_cmd(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Chỉ Owner Bot mới được dùng lệnh này.", ephemeral=True)
            return
        if not _connected:
            await interaction.response.send_message("🔴 Chưa kết nối Supabase (kiểm tra SUPABASE_URL / SUPABASE_KEY).", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        def _do():
            _flush_logs()
            _flush_stats()
            _flush_state()
            return sync_once(force=True, block=True)

        written = await asyncio.to_thread(_do)
        err = last_sync_info.get("error")
        await interaction.followup.send(
            f"✅ Đã đồng bộ xong — ghi **{written}** thay đổi lên Supabase." + (f"\n⚠️ Lỗi gần nhất: `{err}`" if err else ""),
            ephemeral=True)

    @app_commands.command(name="coinlog", description="[OWNER] Xem 10 lần đổi coin gần nhất của 1 người")
    @app_commands.describe(member="Người cần xem")
    async def coinlog_cmd(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Chỉ Owner Bot mới được dùng lệnh này.", ephemeral=True)
            return
        if not _connected:
            await interaction.response.send_message("🔴 Chưa kết nối Supabase.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        def _query():
            return (sb.table("bot_logs").select("created_at,data")
                    .eq("kind", "coin_logs").eq("data->>user_id", str(member.id))
                    .order("created_at", desc=True).limit(10).execute().data)

        rows = await asyncio.to_thread(_query)
        if not rows:
            await interaction.followup.send("Chưa có lịch sử coin nào của người này.", ephemeral=True)
            return
        lines = []
        for r in rows:
            t = _parse_dt(r.get("created_at"))
            ts = t.strftime("%d/%m %H:%M") if t else "?"
            d = r.get("data") or {}
            lines.append(f"`{ts}` {d.get('old', 0):,} → {d.get('new', 0):,} (**{d.get('delta', 0):+,}**) • {d.get('command') or 'nút bấm/khác'}")
        await interaction.followup.send(f"💰 **Lịch sử coin của {member.display_name}:**\n" + "\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(DatabaseManagementCog(bot))


# ==============================================================================
# 9. HÀM TIÊM KẾT NỐI (AUTO INJECTOR) — TUỲ CHỌN, KHÔNG TỰ ĐỘNG GỌI
# ==============================================================================

BOT_MODULE_NAMES = ("bot", "__main__", "music", "noitu", "ai", "lixi_admin", "owner_mute",
                    "status", "server_manager", "quest", "fun", "pingbot")


def inject_database(module_names=None):
    """
    Liên kết các biến toàn cục (như user_data, users, v.v.) với Supabase Cloud Memory
    trong các file của bot. CHỈ quét các module của bot (không đụng thư viện hệ thống).
    Lưu ý: hàm này GHI ĐÈ biến cùng tên — chỉ gọi khi bạn chắc các file đó dùng đúng kiểu dict {id: {...}}.
    """
    target_variables = [
        'user_data', 'users', 'players', 'profiles', 'data',
        'user_money', 'inventory', 'bank', 'levels', 'shop_data', 'active_quests'
    ]
    names = module_names or BOT_MODULE_NAMES
    injected_count = 0
    for module_name in names:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for var_name in target_variables:
            if hasattr(module, var_name):
                setattr(module, var_name, shared_cloud_memory)
                injected_count += 1
                print(f"[Database Injector] Đã liên kết Supabase vào biến '{var_name}' trong file '{module_name}.py'")
    print(f"[Database Injector] Hoàn tất! Đã tiêm database thành công vào {injected_count} điểm.")
