from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
import threading
import subprocess
import datetime
import hashlib
import signal
import html
import hmac
import time
import json
import uuid
import sys
import re
import os

CONFIG_FILE = "config.json"
CONFIG_LOCK = threading.Lock()

# ----------------------------------------------------------------------
# Bảo mật: đặt biến môi trường DASHBOARD_PASSWORD (Bot-Hosting > Môi trường).
# Có mật khẩu -> toàn bộ dashboard phải đăng nhập. Không có -> tab Chat bị khóa
# (vì Chat đọc được tin nhắn riêng và gửi tin bằng tên bot).
# ----------------------------------------------------------------------
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
CHAT_ENABLED = bool(PASSWORD)
SESSION_TOKEN = hashlib.sha256(("ksbot-dashboard|" + PASSWORD).encode()).hexdigest()

# Thư mục trao đổi với dashboard_bridge.py (chạy trong bot)
SPOOL_DIR = "web_spool"
UPLOAD_DIR = "web_uploads"
os.makedirs(SPOOL_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
MAX_UPLOAD = 10 * 1024 * 1024

DEFAULT_BG = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQI2Snditi8l1N6o2Js0AKX67zA3EIyAwLYR2RK2yr93A&s=10"

STATS_FILE = "stats.json"
STATS_MAX_AGE = 120


def read_stats():
    out = {
        "guilds": None, "members": None, "ram_mb": None, "ping_ms": None,
        "updated_at": None, "live": False, "guild_list": None,
        "bot_name": None, "bot_avatar": None,
    }
    if not os.path.exists(STATS_FILE):
        return out
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update({k: data.get(k) for k in (
                "guilds", "members", "ram_mb", "ping_ms", "updated_at", "guild_list",
                "bot_name", "bot_avatar",
            )})
            if isinstance(out["updated_at"], (int, float)):
                out["live"] = (time.time() - out["updated_at"]) <= STATS_MAX_AGE
    except Exception:
        pass
    return out


ACTIVITY_FILE = "activity.json"
ACTIVITY_MAX = 20


def read_activity():
    if not os.path.exists(ACTIVITY_FILE):
        return []
    try:
        with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        items = [x for x in data if isinstance(x, dict) and "action" in x]
        items.sort(key=lambda x: x.get("time", 0), reverse=True)
        return items[:ACTIVITY_MAX]
    except Exception:
        return []


UPTIME_FILE = "uptime.json"


def read_uptime():
    out = {"running": False, "session_seconds": None, "total_seconds": None,
           "sessions": None, "longest_session": None, "first_start": None}
    try:
        with open(UPTIME_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return out
    if not isinstance(d, dict):
        return out

    running = bot_status()["running"]
    total = float(d.get("total_seconds", 0))
    longest = float(d.get("longest_session", 0))
    session = None
    if running:
        try:
            gap = max(0.0, min(30.0, time.time() - os.path.getmtime(UPTIME_FILE)))
        except OSError:
            gap = 0.0
        total += gap
        try:
            start = datetime.datetime.fromisoformat(d.get("current_session_start"))
            session = max(0.0, time.time() - start.timestamp())
        except Exception:
            session = float(d.get("last_session_seconds", 0))
        longest = max(longest, session)

    out.update({
        "running": running, "session_seconds": session, "total_seconds": total,
        "sessions": d.get("sessions"), "longest_session": longest,
        "first_start": str(d.get("first_start", ""))[:10] or None,
    })
    return out


# ----------------------------------------------------------------------
# RPC tới bot (qua thư mục web_spool) + upload file
# ----------------------------------------------------------------------
def _id(v):
    s = str(v or "").strip()
    return s if s.isdigit() and 5 <= len(s) <= 20 else None


def find_upload(uid):
    if not re.fullmatch(r"[0-9a-f]{32}", str(uid or "")):
        return None
    try:
        for fn in os.listdir(UPLOAD_DIR):
            if fn.startswith(uid + "__"):
                return {"path": os.path.join(UPLOAD_DIR, fn), "name": fn.split("__", 1)[1]}
    except OSError:
        pass
    return None


def build_rpc(body):
    """Kiểm tra + làm sạch dữ liệu client gửi lên. Trả (payload, lỗi)."""
    t = body.get("type")
    p = {"type": t}
    if t == "channels":
        p["guild_id"] = _id(body.get("guild_id"))
        ok = p["guild_id"]
    elif t == "history":
        p["channel_id"] = _id(body.get("channel_id"))
        p["after"] = _id(body.get("after"))
        p["before"] = _id(body.get("before"))
        try:
            p["limit"] = max(1, min(100, int(body.get("limit") or 50)))
        except (TypeError, ValueError):
            p["limit"] = 50
        ok = p["channel_id"]
    elif t == "react":
        p["channel_id"] = _id(body.get("channel_id"))
        p["message_id"] = _id(body.get("message_id"))
        p["emoji"] = str(body.get("emoji") or "")[:100]
        p["remove"] = bool(body.get("remove"))
        ok = p["channel_id"] and p["message_id"] and p["emoji"]
    elif t == "delete":
        p["channel_id"] = _id(body.get("channel_id"))
        p["message_id"] = _id(body.get("message_id"))
        ok = p["channel_id"] and p["message_id"]
    elif t == "send":
        p["channel_id"] = _id(body.get("channel_id"))
        p["content"] = str(body.get("content") or "")[:2000]
        p["reply_to"] = _id(body.get("reply_to"))
        # Khi trả lời: có tag (ping) người được trả lời hay không. Mặc định CÓ.
        p["ping"] = body.get("ping") is not False
        ups = []
        for uid in (body.get("uploads") or [])[:10]:
            u = find_upload(uid)
            if u:
                ups.append(u)
        p["uploads"] = ups
        ok = p["channel_id"] and (p["content"].strip() or ups)
    elif t == "emojis":
        p["guild_id"] = _id(body.get("guild_id"))
        ok = True
    elif t == "dm_list":
        ok = True
    elif t == "dm_open":
        p["user_id"] = _id(body.get("user_id"))
        ok = p["user_id"]
    else:
        return None, "Lệnh không hợp lệ"
    if not ok:
        return None, "Thiếu hoặc sai dữ liệu"
    return p, None


def rpc_call(payload, timeout=12):
    cid = uuid.uuid4().hex
    payload = dict(payload, id=cid)
    path = os.path.join(SPOOL_DIR, "cmd_%s.json" % cid)
    res_path = os.path.join(SPOOL_DIR, "res_%s.json" % cid)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(res_path):
            try:
                with open(res_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(res_path)
                return data
            except Exception:
                time.sleep(0.05)
                continue
        time.sleep(0.1)
    try:
        os.remove(path)
    except OSError:
        pass
    return {"ok": False, "error": "Bot không phản hồi (bot đang tắt, hoặc chưa load dashboard_bridge)"}


def cleanup_uploads():
    now = time.time()
    try:
        for fn in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, fn)
            if now - os.path.getmtime(p) > 3600:
                os.remove(p)
    except OSError:
        pass


# ----------------------------------------------------------------------
# Cấu hình
# ----------------------------------------------------------------------
ACCENTS = [
    ("#8b9bff", "Tím chàm", "#0e1118"),
    ("#2dd4bf", "Ngọc lam", "#06201c"),
    ("#f472b6", "Hồng đào", "#2a0e1d"),
    ("#fbbf24", "Hổ phách", "#2a1c02"),
    ("#4ade80", "Xanh lá",  "#07230f"),
    ("#fb7185", "Đỏ san hô", "#2a0a10"),
]
ACCENT_VALUES = [a[0] for a in ACCENTS]

DEFAULT_CONFIG = {
    "prefix": "!",
    "accent": ACCENT_VALUES[0],
    "bat_status": True,
    "status_mode": "online",
    "status_delay": 5,
    "status_messages": [
        "🤖 {guilds} server, phục vụ {members} thành viên",
        "💤 gõ {prefix}help để xem lệnh",
        "🔥 Bot cực xịn 24/7"
    ],
    "bat_ai": True,
    "bat_caro": True,
    "bat_noitu": True,
    "bat_music": True,
    "bat_lixi": True,
    "bat_yt_notify": True,
    "bat_owner_mute": True,
    "bat_bot_guard": True,
    "activity_type": "playing",
    "force_stream": False,
    "platform": "ios",
    "stream_url": "https://www.youtube.com/watch?v=9svI65nxVhc",
    "site_title": "KSBot Command Center",
    "bg_url": DEFAULT_BG,
    "bg_v2": True,
    "bg_dim": 60,
}

BG_URL_RE = re.compile(r"^https://[^\s\"'()<>\\]{4,480}$")
STREAM_URL_RE = re.compile(
    r"^https://(www\.)?(twitch\.tv/[A-Za-z0-9_]{3,40}|youtube\.com/watch\?v=[A-Za-z0-9_-]{6,20}|youtu\.be/[A-Za-z0-9_-]{6,20})$"
)


def normalize_stream_url(url):
    url = str(url or "").strip()
    m = re.match(r"^https://(?:www\.|m\.)?youtu\.be/([A-Za-z0-9_-]{6,20})", url)
    if not m:
        m = re.match(r"^https://(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})", url)
    return "https://www.youtube.com/watch?v=" + m.group(1) if m else url


MODULES = [
    ("bat_status",     "🟢", "Trạng thái bot",   "status.py",      "Đổi status luân phiên theo danh sách bên trái"),
    ("bat_ai",         "🧠", "Trợ lý AI",        "ai.py",          "Trả lời câu hỏi và trò chuyện bằng AI"),
    ("bat_caro",       "⭕", "Game Caro",        "caro.py",        "Chơi cờ caro giữa các thành viên"),
    ("bat_noitu",      "🔤", "Game Nối từ",      "noitu.py",       "Trò chơi nối từ trong kênh chat"),
    ("bat_music",      "🎵", "Phát nhạc",        "music.py",       "Phát nhạc trong kênh thoại"),
    ("bat_lixi",       "🧧", "Lì xì admin",      "lixi_admin.py",  "Admin phát lì xì cho thành viên"),
    ("bat_yt_notify",  "📺", "Thông báo YouTube", "yt_notify.py",  "Báo khi kênh YouTube có video mới"),
    ("bat_owner_mute", "🔇", "Mute owner",       "owner_mute.py",  "Cho owner tắt tiếng thành viên nhanh"),
    ("bat_bot_guard",  "🛡️", "Bot Guard",        "bot_guard.py",   "Chặn bot lạ và hành vi nguy hiểm"),
]

ACTIVITY_TYPES = [
    ("playing",    "🎮", "Đang chơi",   "Hiện là: Đang chơi <câu status>"),
    ("listening",  "🎧", "Đang nghe",   "Hiện là: Đang nghe <câu status>"),
    ("watching",   "📺", "Đang xem",    "Hiện là: Đang xem <câu status>"),
    ("competing",  "🏆", "Đang thi đấu", "Hiện là: Đang thi đấu <câu status>"),
]
ACTIVITY_LABELS = {k: name for k, _i, name, _d in ACTIVITY_TYPES}

PLATFORM_OPTIONS = [
    ("vr",      "🥽", "VR",       "Discord VR / Quest"),
    ("android", "🤖", "Android",  "Biểu tượng điện thoại"),
    ("ios",     "📱", "iOS",      "Biểu tượng điện thoại"),
    ("default", "💻", "Mặc định", "Như bot bình thường"),
]
PLATFORM_KEYS = [p[0] for p in PLATFORM_OPTIONS]

INVITE_URL = "https://discord.com/oauth2/authorize?client_id=1536638391397908601&scope=applications.commands%20bot&permissions=8"

BOT_LOCK = threading.Lock()
BOT_STATE = {"process": None, "should_run": True, "started_at": None, "stopped_by_user": False, "cooldown_until": None}
EXIT_RATE_LIMITED = 42
RATE_LIMIT_WAITS = [15 * 60, 30 * 60, 60 * 60]


def bot_status():
    with BOT_LOCK:
        p = BOT_STATE["process"]
        running = p is not None and p.poll() is None
        return {
            "running": running,
            "should_run": BOT_STATE["should_run"],
            "started_at": BOT_STATE["started_at"],
            "cooldown_until": BOT_STATE["cooldown_until"],
        }


def start_bot():
    with BOT_LOCK:
        BOT_STATE["should_run"] = True
        BOT_STATE["stopped_by_user"] = False
        BOT_STATE["cooldown_until"] = None


def stop_bot():
    with BOT_LOCK:
        BOT_STATE["should_run"] = False
        BOT_STATE["stopped_by_user"] = True
        p = BOT_STATE["process"]
    if p is not None and p.poll() is None:
        p.terminate()


def restart_bot():
    with BOT_LOCK:
        BOT_STATE["should_run"] = True
        BOT_STATE["stopped_by_user"] = False
        BOT_STATE["cooldown_until"] = None
        p = BOT_STATE["process"]
    if p is not None and p.poll() is None:
        p.terminate()


def _forward_sigterm(signum, frame):
    BOT_STATE["should_run"] = False
    BOT_STATE["stopped_by_user"] = True
    p = BOT_STATE.get("process")
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            p.wait(timeout=25)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    os._exit(0)


def default_copy():
    cfg = DEFAULT_CONFIG.copy()
    cfg["status_messages"] = list(DEFAULT_CONFIG["status_messages"])
    return cfg


def read_config():
    cfg = default_copy()
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with CONFIG_LOCK:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
            # Lần đầu lên bản mới: config cũ có bg_url rỗng thì gán nền mặc định.
            if not data.get("bg_v2"):
                if not cfg.get("bg_url"):
                    cfg["bg_url"] = DEFAULT_BG
                cfg["bg_v2"] = True
    except Exception:
        pass
    return cfg


def write_config(data):
    tmp = CONFIG_FILE + ".tmp"
    with CONFIG_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)


# ----------------------------------------------------------------------
# Giao diện
# ----------------------------------------------------------------------
PAGE_CSS = r"""
:root {
    --bg: #0e1118; --panel: #151923; --panel-2: #1b2030; --line: #262c3d;
    --text: #e9ecf5; --muted: #8b93a8; --accent: __ACCENT__; --accent-ink: __ACCENT_INK__;
    --ok: #3fc380; --warn: #f5b942; --bad: #ef5b6b; --radius: 14px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
[hidden] { display: none !important; }
html { color-scheme: dark; }
body {
    font-family: 'Be Vietnam Pro', system-ui, -apple-system, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.5;
    -webkit-font-smoothing: antialiased;
}
button, input, select, textarea { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 28px 18px 120px; }
body[data-tab="chat"] .wrap { max-width: 1480px; padding-bottom: 40px; }

.top { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 26px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark { width: 40px; height: 40px; border-radius: 12px; background: var(--accent); color: var(--accent-ink); display: grid; place-items: center; font-size: 20px; font-weight: 700; overflow: hidden; }
.brand h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.brand p { font-size: 13px; color: var(--muted); }
.top-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.live { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); background: var(--panel); border: 1px solid var(--line); padding: 7px 12px; border-radius: 999px; }
.live i { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px rgba(63,195,128,.18); }
.invite-btn { display: flex; align-items: center; gap: 8px; background: #5865f2; color: #fff; text-decoration: none; font-size: 13px; font-weight: 600; padding: 9px 16px; border-radius: 999px; transition: background .15s; }
.invite-btn:hover { background: #4752c4; }
.invite-btn svg { width: 16px; height: 16px; flex: none; }
.logout { font-size: 13px; color: var(--muted); text-decoration: none; padding: 7px 12px; border: 1px solid var(--line); border-radius: 999px; background: var(--panel); }
.logout:hover { color: var(--text); }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 22px; align-items: start; }
body:not([data-tab="overview"]):not([data-tab="config"]) .layout { grid-template-columns: minmax(0, 1fr); }
@media (max-width: 880px) { .layout { grid-template-columns: 1fr; } .side { order: -1; position: static !important; } }
.side { position: sticky; top: 20px; }

.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 22px; margin-bottom: 18px; }
.card h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.card .sub { font-size: 13px; color: var(--muted); margin-bottom: 18px; }
.field { margin-bottom: 18px; }
.field:last-child { margin-bottom: 0; }
.field > label, .label { display: block; font-size: 13px; font-weight: 500; color: var(--muted); margin-bottom: 8px; }
.hint { font-size: 12px; color: var(--muted); margin-top: 6px; }

input[type="text"], input[type="number"], input[type="password"] {
    width: 100%; background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 10px; padding: 11px 13px; font-size: 14px; outline: none; transition: border-color .15s;
}
input[type="text"]:focus, input[type="number"]:focus, input[type="password"]:focus { border-color: var(--accent); }

.prefix-row { display: flex; align-items: center; gap: 14px; }
.prefix-row input { width: 90px; text-align: center; font-weight: 600; font-size: 16px; }
.prefix-demo { font-size: 13px; color: var(--muted); }
.prefix-demo code { background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 3px 8px; color: var(--text); font-family: ui-monospace, Menlo, Consolas, monospace; }

.swatches { display: flex; flex-wrap: wrap; gap: 10px; }
.swatch { position: relative; }
.swatch input { position: absolute; opacity: 0; pointer-events: none; }
.swatch label { display: flex; flex-direction: column; align-items: center; gap: 6px; cursor: pointer; width: 66px; }
.swatch .dot { width: 36px; height: 36px; border-radius: 50%; border: 3px solid transparent; transition: border-color .15s, transform .15s; }
.swatch input:checked + label .dot { border-color: #fff; transform: scale(1.08); }
.swatch small { font-size: 11px; color: var(--muted); text-align: center; }

.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.modes.platforms { grid-template-columns: repeat(4, 1fr); }
.modes.statuses { grid-template-columns: repeat(5, 1fr); }
.modes.platforms .mode span, .modes.statuses .mode span { flex-direction: column; text-align: center; gap: 6px; padding: 12px 8px; }
@media (max-width: 720px) { .modes.platforms, .modes.statuses { grid-template-columns: 1fr 1fr; } }
.mode { position: relative; }
.mode input { position: absolute; opacity: 0; pointer-events: none; }
.mode span { display: flex; align-items: center; gap: 10px; padding: 12px 14px; background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; cursor: pointer; font-size: 14px; transition: border-color .15s, background .15s; }
.mode small { display: block; font-size: 12px; color: var(--muted); }
.mode .dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
.mode .mode-ico { font-size: 18px; line-height: 1; flex: none; }
.mode input:checked + span { border-color: var(--accent); background: rgba(139,155,255,.1); }

.delay-row { display: flex; align-items: center; gap: 14px; }
.delay-row input[type=range] { flex: 1; accent-color: var(--accent); }
.delay-val { min-width: 64px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }

.msgs { display: flex; flex-direction: column; gap: 8px; }
.msg-row { display: flex; gap: 8px; }
.msg-row input { flex: 1; }
.icon-btn { width: 42px; flex: none; background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; cursor: pointer; color: var(--muted); font-size: 16px; }
.icon-btn:hover { color: var(--bad); border-color: var(--bad); }
.add-btn { margin-top: 10px; width: 100%; background: transparent; border: 1px dashed var(--line); border-radius: 10px; padding: 10px; color: var(--muted); cursor: pointer; font-size: 14px; }
.add-btn:hover { color: var(--accent); border-color: var(--accent); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; align-items: center; }
.chips span { font-size: 12px; color: var(--muted); margin-right: 2px; }
.chip { background: var(--panel-2); border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; font-size: 12px; cursor: pointer; font-family: ui-monospace, Menlo, Consolas, monospace; }
.chip:hover { border-color: var(--accent); color: var(--accent); }

.card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }
.card-head .sub { margin-bottom: 0; }
.bulk { display: flex; gap: 6px; }
.bulk button { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 11px; font-size: 12px; cursor: pointer; color: var(--muted); }
.bulk button:hover { color: var(--text); border-color: var(--accent); }
.mods { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
@media (max-width: 600px) { .mods { grid-template-columns: 1fr; } }
.mod { position: relative; }
.mod input { position: absolute; opacity: 0; pointer-events: none; }
.mod label { display: flex; gap: 12px; align-items: center; padding: 13px 14px; cursor: pointer; background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; transition: border-color .15s, opacity .15s; height: 100%; }
.mod .ico { font-size: 22px; width: 30px; text-align: center; flex: none; }
.mod .txt { flex: 1; min-width: 0; }
.mod b { display: block; font-size: 14px; font-weight: 600; }
.mod small { display: block; font-size: 12px; color: var(--muted); line-height: 1.35; margin-top: 2px; }
.mod .tg { width: 38px; height: 22px; border-radius: 22px; background: #394057; position: relative; flex: none; transition: background .2s; }
.mod .tg::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .2s; }
.mod input:checked + label { border-color: rgba(139,155,255,.55); }
.mod input:checked + label .tg { background: var(--accent); }
.mod input:checked + label .tg::after { transform: translateX(16px); }
.mod input:not(:checked) + label { opacity: .62; }

.preview h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.profile { background: #11141d; border: 1px solid var(--line); border-radius: 16px; overflow: hidden; margin-top: 14px; }
.banner { height: 70px; background: linear-gradient(120deg, #2a3158, #3b2f5c); }
.profile-body { padding: 0 18px 18px; }
.avatar-wrap { position: relative; width: 72px; height: 72px; margin-top: -36px; }
.avatar { width: 72px; height: 72px; border-radius: 50%; background: var(--accent); color: var(--accent-ink); display: grid; place-items: center; font-size: 32px; border: 5px solid #11141d; overflow: hidden; }
.avatar img, .brand-mark img { width: 100%; height: 100%; object-fit: cover; display: block; }
.status-dot { position: absolute; right: 0; bottom: 2px; width: 22px; height: 22px; border-radius: 50%; border: 4px solid #11141d; background: var(--ok); transition: background .25s; }
.pname { font-size: 18px; font-weight: 700; margin-top: 10px; display: flex; align-items: center; gap: 8px; }
.tag { font-size: 11px; font-weight: 600; background: #5865f2; color: #fff; padding: 2px 6px; border-radius: 5px; }
.pstate { font-size: 13px; color: var(--muted); margin-top: 2px; }
.bubble { margin-top: 14px; background: var(--panel-2); border-radius: 10px; padding: 11px 13px; font-size: 14px; min-height: 44px; display: flex; align-items: center; }
.bubble span { animation: swap .35s ease; }
@keyframes swap { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.off-note { font-size: 12px; color: var(--warn); margin-top: 10px; display: none; }
.preview .hint { margin-top: 12px; }
.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
.stat { background: var(--panel-2); border-radius: 10px; padding: 10px 12px; }
.stat b { display: block; font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat span { font-size: 11px; color: var(--muted); }
.data-note { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin-top: 10px; }
.data-note i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.data-note.live i { background: var(--ok); }
body[data-tab="overview"] .preview .stats, body[data-tab="overview"] .preview .data-note { display: none; }

.savebar { position: fixed; left: 50%; bottom: 20px; transform: translate(-50%, 140%); background: #20263a; border: 1px solid #343c58; border-radius: 14px; padding: 10px 10px 10px 18px; display: flex; align-items: center; gap: 16px; box-shadow: 0 12px 40px rgba(0,0,0,.55); transition: transform .25s ease; z-index: 20; max-width: calc(100% - 28px); }
.savebar.show { transform: translate(-50%, 0); }
.savebar p { font-size: 14px; }
.btn { border: none; cursor: pointer; border-radius: 10px; padding: 10px 18px; font-size: 14px; font-weight: 600; }
.btn.primary { background: var(--accent); color: var(--accent-ink); }
.btn.primary:disabled { opacity: .6; cursor: wait; }
.btn.ghost { background: transparent; color: var(--muted); }
.btn.ghost:hover { color: var(--text); }
.toast { position: fixed; top: 20px; right: 20px; background: var(--panel-2); border: 1px solid var(--line); border-left: 4px solid var(--ok); padding: 12px 16px; border-radius: 10px; font-size: 14px; opacity: 0; transform: translateY(-10px); pointer-events: none; transition: all .25s; z-index: 60; max-width: 360px; }
.toast.show { opacity: 1; transform: none; }
.toast.err { border-left-color: var(--bad); }

.botctl { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.botctl-state { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.botctl-state i { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); flex: none; }
.botctl-state.on i { background: var(--ok); box-shadow: 0 0 0 3px rgba(63,195,128,.18); }
.botctl-state.off i { background: var(--bad); }
.botctl-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.btn.start { background: var(--ok); color: #06180f; }
.btn.stop { background: var(--bad); color: #2a0a0a; }
.btn.restart { background: var(--panel-2); border: 1px solid var(--line); color: var(--text); }
.btn.small { padding: 8px 14px; font-size: 13px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }

.list-empty { text-align: center; padding: 28px 16px; color: var(--muted); font-size: 13px; background: var(--panel-2); border-radius: 12px; border: 1px dashed var(--line); }
.list-empty b { display: block; color: var(--text); font-size: 14px; margin-bottom: 4px; }
.rows { display: flex; flex-direction: column; gap: 8px; }
.row-item { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: var(--panel-2); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
.row-item .main { display: flex; align-items: center; gap: 10px; min-width: 0; }
.row-item .main b { font-size: 14px; display: block; }
.row-item .main small { color: var(--muted); display: block; }

.ov-tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
@media (max-width: 600px) { .ov-tiles { grid-template-columns: 1fr 1fr; } }
.ov-tile { background: var(--panel-2); border-radius: 12px; padding: 14px 14px 12px; min-width: 0; }
.ov-tile span { font-size: 12px; color: var(--muted); }
.ov-tile b { display: block; font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 2px; }
.spark { display: block; width: 100%; height: 22px; margin-top: 6px; }
.spark polyline { fill: none; stroke: var(--accent); stroke-width: 1.6; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.ov-foot { font-size: 12px; color: var(--muted); margin-top: 12px; }

body.has-bg { background-image: linear-gradient(rgba(14,17,24,var(--dim)), rgba(14,17,24,var(--dim))), var(--bg-img); background-size: cover; background-position: center; background-attachment: fixed; }
body.has-bg .card { background: rgba(21,25,35,.78); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); }
.theme-prev { height: 90px; border-radius: 12px; border: 1px solid var(--line); margin-top: 10px; background: var(--panel-2) center / cover no-repeat; }

.srv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 600px) { .srv-grid { grid-template-columns: 1fr; } }
.srv { display: flex; align-items: center; gap: 12px; background: var(--panel-2); border-radius: 10px; padding: 10px 12px; min-width: 0; }
.srv-ico { width: 40px; height: 40px; border-radius: 50%; background: var(--line); display: grid; place-items: center; font-weight: 700; overflow: hidden; flex: none; }
.srv-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.srv-txt { min-width: 0; }
.srv-txt b { display: block; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srv-txt small { color: var(--muted); font-size: 12px; }
.pager { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 14px; font-size: 13px; color: var(--muted); }
.pg { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }
.pg:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pg:disabled { opacity: .4; cursor: not-allowed; }

.tabs { display: flex; gap: 4px; overflow-x: auto; margin: 0 0 22px; border-bottom: 1px solid var(--line); scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab { background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; padding: 11px 16px; font-size: 14px; font-weight: 500; color: var(--muted); cursor: pointer; white-space: nowrap; }
.tab:hover { color: var(--text); }
.tab.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }

/* ============ CHAT KIỂU DISCORD ============ */
/* grid-template-rows + min-height:0 để CẢ 3 CỘT (server, kênh, chat) đều tự cuộn được */
.dc { display: grid; grid-template-columns: 72px 248px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr); height: calc(100vh - 175px); min-height: 560px; border-radius: 14px; overflow: hidden; border: 1px solid #1e1f22; background: #313338; margin-bottom: 18px; color: #dbdee1; }
.dc-rail { background: #1e1f22; overflow-y: auto; padding: 12px 0; display: flex; flex-direction: column; align-items: center; gap: 8px; scrollbar-width: none; min-height: 0; overscroll-behavior: contain; }
.dc-rail::-webkit-scrollbar { display: none; }
.rail-ico { width: 48px; height: 48px; border-radius: 50%; background: #313338; display: grid; place-items: center; cursor: pointer; overflow: hidden; font-weight: 600; color: #dbdee1; border: 0; transition: border-radius .15s, background .15s; flex: none; font-size: 18px; position: relative; }
.rail-ico:hover, .rail-ico.on { border-radius: 16px; background: var(--accent); color: var(--accent-ink); }
.rail-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rail-sep { width: 32px; height: 2px; background: #35373c; flex: none; border-radius: 2px; }
.dc-side { background: #2b2d31; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.dc-side-head { padding: 14px; font-weight: 700; border-bottom: 1px solid #1e1f22; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-shadow: 0 1px 0 rgba(0,0,0,.15); flex: none; }
.dc-chans { overflow-y: auto; padding: 8px; flex: 1; min-height: 0; overscroll-behavior: contain; scrollbar-width: thin; }
.grp { font-size: 11px; font-weight: 700; text-transform: uppercase; color: #949ba4; padding: 14px 6px 4px; letter-spacing: .02em; }
.chan { display: flex; align-items: center; gap: 8px; width: 100%; background: none; border: 0; color: #949ba4; padding: 6px 8px; border-radius: 6px; cursor: pointer; text-align: left; font-size: 14px; }
.chan:hover { background: #35373c; color: #dbdee1; }
.chan.on { background: #404249; color: #fff; }
.chan .h { opacity: .7; font-weight: 600; flex: none; }
.chan .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chan img { width: 24px; height: 24px; border-radius: 50%; flex: none; }
.dm-open { display: flex; gap: 6px; padding: 8px 6px; }
.dm-open input { background: #1e1f22; border: 0; border-radius: 6px; padding: 8px 10px; font-size: 13px; width: 100%; color: #dbdee1; outline: none; }
.dm-open button { background: #5865f2; border: 0; border-radius: 6px; color: #fff; padding: 0 12px; cursor: pointer; font-weight: 600; }
.dc-note { padding: 14px 10px; font-size: 13px; color: #949ba4; }

.dc-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: #313338; position: relative; }
.dc-top { padding: 12px 16px; font-weight: 700; border-bottom: 1px solid #26272b; display: flex; gap: 8px; align-items: center; box-shadow: 0 1px 0 rgba(0,0,0,.15); flex: none; }
.dc-top .dim { color: #949ba4; font-weight: 400; }
.dc-back { display: none; background: none; border: 0; color: #dbdee1; font-size: 22px; cursor: pointer; padding: 0 6px; }
.dc-msgs { flex: 1; overflow-y: auto; padding: 8px 0 12px; min-height: 0; overscroll-behavior: contain; }
.dc-empty { padding: 40px 20px; text-align: center; color: #949ba4; font-size: 14px; }
.dc-older { display: block; margin: 8px auto; background: #2b2d31; border: 0; color: #b5bac1; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; }
.dc-older:hover { color: #fff; }
.m { position: relative; display: flex; gap: 14px; padding: 2px 16px; line-height: 1.4; }
.m:hover { background: #2e3035; }
.m.first { margin-top: 14px; }
.m .av { width: 40px; height: 40px; border-radius: 50%; flex: none; cursor: pointer; object-fit: cover; margin-top: 2px; background: #1e1f22; }
.m .gut { width: 40px; flex: none; font-size: 10px; color: #949ba4; text-align: center; padding-top: 5px; visibility: hidden; }
.m:hover .gut { visibility: visible; }
.m .body { min-width: 0; flex: 1; }
.m .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.m .nm { font-weight: 600; cursor: pointer; color: #fff; }
.m .nm:hover { text-decoration: underline; }
.m .ts { font-size: 11px; color: #949ba4; }
.m .tag { font-size: 10px; padding: 1px 5px; align-self: center; }
.m .txt { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; color: #dbdee1; }
.m .txt a, .emb a { color: #00a8fc; text-decoration: none; }
.m .txt a:hover { text-decoration: underline; }
.m .txt code { background: #1e1f22; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.m .ed { color: #949ba4; font-size: 11px; }
.m .rep { font-size: 12px; color: #949ba4; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m .rep b { color: #b5bac1; font-weight: 600; }
.m .tools { position: absolute; right: 16px; top: -14px; display: none; background: #313338; border: 1px solid #26272b; border-radius: 8px; overflow: hidden; z-index: 3; }
.m:hover .tools, .m.sel .tools { display: flex; }
.tools button { background: none; border: 0; color: #b5bac1; padding: 5px 9px; cursor: pointer; font-size: 15px; }
.tools button:hover { background: #3a3c42; color: #fff; }
.att img { max-width: min(420px, 100%); max-height: 320px; border-radius: 8px; margin-top: 6px; display: block; }
.att video { max-width: min(420px, 100%); border-radius: 8px; margin-top: 6px; display: block; }
.file { display: inline-flex; gap: 6px; align-items: center; margin-top: 6px; background: #2b2d31; border: 1px solid #1e1f22; border-radius: 8px; padding: 8px 12px; color: #00a8fc; text-decoration: none; font-size: 14px; }
.emb { border-left: 4px solid var(--accent); background: #2b2d31; border-radius: 4px; padding: 8px 12px 10px; margin-top: 6px; max-width: 520px; font-size: 14px; }
.emb .et { font-weight: 700; color: #fff; margin: 2px 0; }
.emb .ea { font-size: 12px; color: #b5bac1; }
.emb .ed2 { white-space: pre-wrap; overflow-wrap: anywhere; color: #dbdee1; }
.emb .ef { margin-top: 6px; font-size: 13px; } .emb .ef b { display: block; }
.emb img.big { max-width: 100%; border-radius: 6px; margin-top: 8px; display: block; }
.emb .ft { font-size: 11px; color: #949ba4; margin-top: 6px; }
.comp-note { font-size: 12px; color: #949ba4; margin-top: 4px; font-style: italic; }
.rx { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.rxb { display: inline-flex; align-items: center; gap: 5px; background: #2b2d31; border: 1px solid transparent; border-radius: 8px; padding: 2px 8px; font-size: 14px; color: #b5bac1; cursor: pointer; }
.rxb:hover { border-color: #4e5058; }
.rxb.me { background: rgba(88,101,242,.25); border-color: #5865f2; color: #dee0fc; }
.ce { width: 22px; height: 22px; vertical-align: -5px; object-fit: contain; }
.rxb .ce { width: 18px; height: 18px; vertical-align: middle; }

.dc-comp { padding: 0 16px 16px; position: relative; flex: none; }
.dc-reply { background: #2b2d31; border-radius: 8px 8px 0 0; padding: 6px 12px; font-size: 12px; color: #b5bac1; display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.dc-reply button { background: none; border: 0; color: #b5bac1; cursor: pointer; font-size: 14px; }
.dc-reply .rbtns { display: flex; align-items: center; gap: 8px; flex: none; }
.dc-reply .pingbtn { font-size: 12px; font-weight: 700; padding: 3px 9px; border-radius: 6px; background: #1e1f22; color: #949ba4; }
.dc-reply .pingbtn.on { background: #5865f2; color: #fff; }
.dc-files { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 0 8px; }
.dc-files:empty { display: none; }
.fchip { display: flex; align-items: center; gap: 8px; background: #2b2d31; border-radius: 8px; padding: 6px 10px; font-size: 12px; max-width: 220px; }
.fchip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fchip button { background: none; border: 0; color: #f23f43; cursor: pointer; }
.cbox { display: flex; align-items: flex-end; gap: 4px; background: #383a40; border-radius: 10px; padding: 2px 6px; }
.cbox textarea { flex: 1; background: none; border: 0; outline: none; resize: none; max-height: 150px; padding: 11px 6px; color: #dbdee1; font-size: 15px; line-height: 1.35; font-family: inherit; }
.cbox textarea:disabled { opacity: .5; }
.cbox button { background: none; border: 0; color: #b5bac1; cursor: pointer; font-size: 20px; padding: 8px 9px; border-radius: 6px; }
.cbox button:hover:not(:disabled) { color: #fff; }
.cbox button:disabled { opacity: .4; cursor: not-allowed; }
.cbox #dcSend { color: var(--accent); }
.dc-pop { position: absolute; bottom: 72px; right: 16px; width: 340px; max-width: calc(100% - 32px); max-height: 320px; overflow: auto; background: #2b2d31; border: 1px solid #1e1f22; border-radius: 10px; padding: 8px; display: none; z-index: 10; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
.dc-pop.show { display: block; }
.pop-tabs { display: flex; gap: 6px; margin-bottom: 6px; }
.pop-tabs button { background: #1e1f22; border: 0; color: #b5bac1; border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 12px; }
.pop-tabs button.on { background: #5865f2; color: #fff; }
.emo-grid { display: grid; grid-template-columns: repeat(8, 1fr); gap: 2px; }
.emo-grid button { font-size: 22px; background: none; border: 0; cursor: pointer; border-radius: 6px; padding: 3px; line-height: 1.2; }
.emo-grid button:hover { background: #3a3c42; }
.emo-grid img { width: 26px; height: 26px; object-fit: contain; }
.dc.drag .dc-main::after { content: "Thả file vào đây để đính kèm"; position: absolute; inset: 0; background: rgba(88,101,242,.35); display: grid; place-items: center; font-weight: 700; z-index: 20; }

/* Icon bot + nút khóa cuộn trang */
.dc-bot { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: #b5bac1; min-width: 0; }
.dc-bot #dcBotName { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.dc-bot-ico { width: 24px; height: 24px; border-radius: 50%; background: #5865f2; display: grid; place-items: center; font-size: 14px; overflow: hidden; flex: none; }
.dc-bot-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.lock-btn { background: #2b2d31; border: 0; border-radius: 8px; padding: 5px 10px; cursor: pointer; color: #dbdee1; font-size: 14px; flex: none; }
.lock-btn:hover { background: #3a3c42; }
.lock-btn.on { background: var(--accent); color: var(--accent-ink); }
html.chat-lock, html.chat-lock body { overflow: hidden; }
html.chat-lock .wrap { padding-bottom: 0; }
html.chat-lock .dc { height: calc(100vh - 150px); }

@media (max-width: 800px) {
    .dc { grid-template-columns: 60px minmax(0, 1fr); height: calc(100vh - 140px); }
    .dc-rail .rail-ico { width: 42px; height: 42px; }
    .dc:not(.in-chat) .dc-main { display: none; }
    .dc.in-chat .dc-side { display: none; }
    .dc-back { display: block; }
    .dc-pop { right: 8px; }
    .dc-bot #dcBotName { display: none; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }

/* Trang đăng nhập */
.login { max-width: 360px; margin: 14vh auto 0; padding: 0 18px; }
.login .card { padding: 28px; }
.login h1 { font-size: 20px; margin-bottom: 6px; }
.login .err { color: var(--bad); font-size: 13px; margin-bottom: 10px; }
.login button { width: 100%; margin-top: 14px; }
"""

PAGE_JS = r"""
(function () {
    var CHAT_ON = __CHAT_ON__;
    var DEFAULT_BG = "__DEFAULT_BG__";
    var form = document.getElementById('cfg');
    var saveBar = document.getElementById('savebar');
    var saveBtn = document.getElementById('saveBtn');
    var resetBtn = document.getElementById('resetBtn');
    var toast = document.getElementById('toast');
    var msgBox = document.getElementById('msgs');
    var delayInput = document.getElementById('delay');
    var delayVal = document.getElementById('delayVal');
    var prefixInput = document.getElementById('prefix');
    var prefixDemo = document.getElementById('prefixDemo');
    var dot = document.getElementById('dot');
    var pstate = document.getElementById('pstate');
    var bubble = document.getElementById('bubble');
    var offNote = document.getElementById('offNote');
    var statusSwitch = document.querySelector('input[name="bat_status"]');
    var statGuilds = document.getElementById('statGuilds');
    var statMembers = document.getElementById('statMembers');
    var statRam = document.getElementById('statRam');
    var statPing = document.getElementById('statPing');
    var dataNote = document.getElementById('dataNote');
    var guildRows = document.getElementById('guildRows');
    var activityRows = document.getElementById('activityRows');
    var botState = document.getElementById('botState');
    var btnStart = document.getElementById('btnStart');
    var btnStop = document.getElementById('btnStop');
    var btnRestart = document.getElementById('btnRestart');
    var siteTitle = document.getElementById('siteTitle');
    var bgUrl = document.getElementById('bgUrl');
    var bgDim = document.getElementById('bgDim');
    var bgDimVal = document.getElementById('bgDimVal');
    var bgPrev = document.getElementById('bgPrev');
    var onGuildList = function () {};

    var COLORS = { online: '#3fc380', idle: '#f5b942', dnd: '#ef5b6b', invisible: '#5c6370', stream: '#9b59ff' };
    var LABELS = { online: 'Trực tuyến', idle: 'Chờ', dnd: 'Đừng làm phiền', invisible: 'Ẩn (nhìn như offline)', stream: 'Đang stream' };
    var ACT_LABELS = { playing: 'Đang chơi', listening: 'Đang nghe', watching: 'Đang xem', competing: 'Đang thi đấu', streaming: 'Streaming' };
    var snapshot = '';
    var idx = 0, timer = null;
    var liveStats = { guilds: null, members: null, ram_mb: null, ping_ms: null, live: false };

    function getMessages() {
        var out = [];
        msgBox.querySelectorAll('input[type=text]').forEach(function (i) { if (i.value.trim()) out.push(i.value.trim()); });
        return out;
    }
    function getMode() { return form.querySelector('input[name="status_mode"]:checked').value; }
    function getAccent() { var el = form.querySelector('input[name="accent"]:checked'); return el ? el.value : '#8b9bff'; }
    function getActivity() {
        if (getMode() === 'stream') return 'streaming';
        var el = form.querySelector('input[name="activity_type"]:checked');
        return el ? el.value : 'playing';
    }
    function serialize() {
        var p = new URLSearchParams();
        p.set('prefix', prefixInput.value);
        p.set('accent', getAccent());
        p.set('status_mode', getMode());
        p.set('activity_type', getActivity());
        p.set('status_delay', delayInput.value);
        p.set('site_title', siteTitle.value);
        p.set('bg_url', bgUrl.value.trim());
        p.set('bg_dim', bgDim.value);
        p.set('stream_url', document.getElementById('streamUrl').value.trim());
        p.set('status_messages', getMessages().join('\n'));
        form.querySelectorAll('.mod input[type=checkbox]').forEach(function (c) { if (c.checked) p.set(c.name, 'on'); });
        var pl = form.querySelector('input[name="platform"]:checked');
        p.set('platform', pl ? pl.value : 'ios');
        return p;
    }

    function num(v) { return v == null ? null : Number(v).toLocaleString('vi-VN'); }
    function latencyLabel(ms) { if (ms == null) return 'N/A'; return ms < 150 ? 'Mượt' : (ms < 300 ? 'Ổn định' : 'Hơi lag'); }

    function fill(text) {
        var g = num(liveStats.guilds), m = num(liveStats.members);
        return text
            .replace(/\{guilds\}/g, g != null ? g : '12')
            .replace(/\{members\}/g, m != null ? m : '3.456')
            .replace(/\{ram\}(\s*MB)?/gi, function (_m, suf) { return liveStats.ram_mb != null ? liveStats.ram_mb + (suf || ' MB') : 'N/A'; })
            .replace(/\{ping\}(\s*ms)?/gi, function (_m, suf) { return liveStats.ping_ms != null ? liveStats.ping_ms + (suf || ' ms') : 'N/A'; })
            .replace(/\{latency_status\}/g, latencyLabel(liveStats.ping_ms))
            .replace(/\{prefix\}/g, prefixInput.value || '!');
    }

    function showMsg() {
        var list = getMessages();
        var span = document.createElement('span');
        span.textContent = list.length ? (ACT_LABELS[getActivity()] || '') + ' ' + fill(list[idx % list.length]) : 'Chưa có dòng status nào';
        bubble.innerHTML = '';
        bubble.appendChild(span);
    }
    function restartPreview() {
        if (timer) clearInterval(timer);
        idx = 0; showMsg();
        var s = Math.max(1, parseInt(delayInput.value, 10) || 5);
        timer = setInterval(function () { idx++; showMsg(); }, s * 1000);
    }

    var DEMO = { guilds: 12, members: '3.456', ram_mb: 128, ping_ms: 42 };
    function setTile(el, real, demo, suffix) {
        var v = real != null ? real : demo;
        el.classList.remove('na');
        el.querySelector('b').textContent = (num(v) || v) + (suffix || '');
    }
    function applyStats() {
        setTile(statGuilds, liveStats.guilds, DEMO.guilds, '');
        setTile(statMembers, liveStats.members, DEMO.members, '');
        setTile(statRam, liveStats.ram_mb, DEMO.ram_mb, ' MB');
        setTile(statPing, liveStats.ping_ms, DEMO.ping_ms, ' ms');
        dataNote.className = 'data-note' + (liveStats.live ? ' live' : '');
        dataNote.querySelector('span').textContent = liveStats.live ? 'Dữ liệu trực tiếp từ bot' : 'Số liệu minh họa';
        applyIdentity();
        applyOverview();
    }

    var pingHist = [];
    function applyOverview() {
        var L = liveStats;
        function put(id, v, suffix) { document.getElementById(id).textContent = v == null ? '—' : (num(v) || v) + (suffix || ''); }
        put('ovGuilds', L.guilds); put('ovMembers', L.members);
        put('ovRam', L.ram_mb, ' MB'); put('ovPing', L.ping_ms, ' ms');
        document.getElementById('ovFoot').textContent = L.live
            ? 'Dữ liệu trực tiếp từ bot • cập nhật ' + timeAgo(L.updated_at)
            : 'Bot chưa gửi dữ liệu mới, số liệu có thể đã cũ hoặc trống';
    }
    function pushPing() {
        if (liveStats.ping_ms == null) return;
        pingHist.push(liveStats.ping_ms);
        if (pingHist.length > 30) pingHist.shift();
        var svg = document.getElementById('ovSpark');
        if (pingHist.length < 2) { svg.innerHTML = ''; return; }
        var lo = Math.min.apply(null, pingHist), hi = Math.max.apply(null, pingHist);
        var span = Math.max(1, hi - lo);
        var pts = pingHist.map(function (v, i) {
            return ((i / (pingHist.length - 1)) * 100).toFixed(1) + ',' + (20 - ((v - lo) / span) * 18).toFixed(1);
        }).join(' ');
        svg.innerHTML = '<polyline points="' + pts + '"/>';
    }
    function fmtDur(sec) {
        sec = Math.max(0, Math.round(sec));
        if (sec < 60) return sec + ' giây';
        if (sec < 3600) return Math.floor(sec / 60) + ' phút';
        if (sec < 86400) return Math.floor(sec / 3600) + ' giờ ' + Math.floor((sec % 3600) / 60) + ' phút';
        return Math.floor(sec / 86400) + ' ngày ' + Math.floor((sec % 86400) / 3600) + ' giờ';
    }

    var avatarEl = document.getElementById('avatar');
    var pnameText = document.getElementById('pnameText');
    var brandMark = document.getElementById('brandMark');
    var shownAvatar = null;
    function applyIdentity() {
        if (liveStats.bot_name) {
            pnameText.textContent = liveStats.bot_name;
            var bn = document.getElementById('dcBotName');
            if (bn) bn.textContent = liveStats.bot_name;
        }
        var url = liveStats.bot_avatar;
        if (!url || !/^https:\/\//.test(url) || url === shownAvatar) return;
        shownAvatar = url;
        [avatarEl, brandMark, document.getElementById('dcBotIco')].forEach(function (box) {
            if (!box) return;
            var img = document.createElement('img');
            img.alt = '';
            img.onload = function () { box.innerHTML = ''; box.appendChild(img); };
            img.src = url;
        });
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML.replace(/"/g, '&quot;');
    }
    function emptyState(title, hint) { return '<div class="list-empty"><b>' + esc(title) + '</b>' + esc(hint) + '</div>'; }
    function timeAgo(ts) {
        if (!ts) return '';
        var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
        if (s < 60) return s + ' giây trước';
        if (s < 3600) return Math.round(s / 60) + ' phút trước';
        if (s < 86400) return Math.round(s / 3600) + ' giờ trước';
        return Math.round(s / 86400) + ' ngày trước';
    }

    var GUILD_PER_PAGE = 8, guildPage = 0, guildData = [];
    function renderGuilds(list) { guildData = list || []; drawGuilds(); }
    function drawGuilds() {
        if (!guildData.length) { guildRows.innerHTML = emptyState('Chưa có dữ liệu', 'Cần bot.py gửi guild_list lên stats.json để hiện danh sách thật.'); return; }
        var pages = Math.ceil(guildData.length / GUILD_PER_PAGE);
        if (guildPage >= pages) guildPage = pages - 1;
        if (guildPage < 0) guildPage = 0;
        var slice = guildData.slice(guildPage * GUILD_PER_PAGE, (guildPage + 1) * GUILD_PER_PAGE);
        var out = '<div class="srv-grid">' + slice.map(function (g) {
            var ico = (g.icon && /^https:\/\//.test(g.icon)) ? '<img src="' + esc(g.icon) + '" alt="" loading="lazy">' : esc((g.name || '?').charAt(0).toUpperCase());
            return '<div class="srv"><div class="srv-ico">' + ico + '</div><div class="srv-txt"><b>' + esc(g.name || 'Không tên') + '</b><small>' + esc(g.members != null ? num(g.members) + ' thành viên' : '') + '</small></div></div>';
        }).join('') + '</div>';
        if (pages > 1) {
            out += '<div class="pager"><button type="button" class="pg" id="pgPrev"' + (guildPage === 0 ? ' disabled' : '') + '>‹ Trước</button><span>Trang ' + (guildPage + 1) + ' / ' + pages + ' • ' + guildData.length + ' server</span><button type="button" class="pg" id="pgNext"' + (guildPage >= pages - 1 ? ' disabled' : '') + '>Sau ›</button></div>';
        }
        guildRows.innerHTML = out;
        var prev = document.getElementById('pgPrev'), next = document.getElementById('pgNext');
        if (prev) prev.addEventListener('click', function () { guildPage--; drawGuilds(); });
        if (next) next.addEventListener('click', function () { guildPage++; drawGuilds(); });
    }

    function renderActivity(list) {
        if (!list || !list.length) { activityRows.innerHTML = emptyState('Chưa có hoạt động nào', 'Cần bot.py ghi log lệnh vào activity.json để hiện ở đây.'); return; }
        activityRows.innerHTML = '<div class="rows">' + list.map(function (a) {
            return '<div class="row-item"><div class="main"><div><b>' + esc(a.action || '') + '</b><small>' + esc(a.user || 'Ẩn danh') + ' • ' + timeAgo(a.time) + '</small></div></div></div>';
        }).join('') + '</div>';
    }

    function authFetch(url, opt) {
        return fetch(url, opt).then(function (r) { if (r.status === 401) { location.reload(); throw new Error('Hết phiên đăng nhập'); } return r; });
    }
    function pollStats() {
        authFetch('/stats', { cache: 'no-store' }).then(function (r) { return r.json(); })
            .then(function (d) { liveStats = d; applyStats(); pushPing(); showMsg(); renderGuilds(d.guild_list); onGuildList(d.guild_list); })
            .catch(function () {});
    }
    function pollActivity() { authFetch('/activity', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(renderActivity).catch(function () {}); }

    var upBase = null, upBaseAt = 0, upRunning = false;
    function fmtClock(sec) {
        sec = Math.max(0, Math.floor(sec));
        var d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
        return (d ? d + ' ngày ' : '') + (d || h ? h + ' giờ ' : '') + m + ' phút ' + s + ' giây';
    }
    function drawUptime() {
        var el = document.getElementById('upTotal');
        if (upBase == null) { el.textContent = '—'; return; }
        el.textContent = fmtClock(upBase + (upRunning ? (Date.now() - upBaseAt) / 1000 : 0));
    }
    function pollUptime() {
        authFetch('/uptime', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (u) {
            upBase = u.total_seconds != null ? Number(u.total_seconds) : null;
            upBaseAt = Date.now(); upRunning = !!u.running;
            drawUptime();
            document.getElementById('upSessions').textContent = u.sessions != null ? num(Math.max(0, u.sessions - 1)) : '—';
            document.getElementById('upFirst').textContent = u.first_start || '—';
            document.getElementById('upFoot').textContent = u.first_start
                ? (upRunning ? 'Đang đếm tiếp từ tổng thời gian đã lưu, khởi động lại không bị reset' : 'Bot đang tắt, đồng hồ tạm dừng và sẽ đếm tiếp khi bật lại') + ' • lưu trên kênh Discord nên deploy lại cũng không mất'
                : 'Chưa có dữ liệu uptime (bot cần load discord_storage)';
        }).catch(function () {});
    }
    setInterval(drawUptime, 1000);
    function pollBotState() {
        authFetch('/botctl', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
            botState.className = 'botctl-state ' + (d.running ? 'on' : 'off');
            var cool = d.cooldown_until ? Math.ceil((d.cooldown_until - Date.now() / 1000) / 60) : 0;
            botState.querySelector('span').textContent = d.running
                ? 'Bot đang chạy' + (d.started_at ? ' • đã chạy ' + fmtDur(Date.now() / 1000 - d.started_at) : '')
                : (cool > 0 ? 'Discord đang chặn IP, tự thử lại sau ~' + cool + ' phút' : 'Bot đã dừng');
            btnStart.disabled = d.running; btnStop.disabled = !d.running; btnRestart.disabled = !d.running;
        }).catch(function () { botState.className = 'botctl-state off'; botState.querySelector('span').textContent = 'Không kiểm tra được'; });
    }
    function botPost(action) {
        return authFetch('/botctl', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'action=' + action });
    }
    btnStart.addEventListener('click', function () { botPost('start').then(pollBotState).then(function () { notify('Đã bật bot'); }); });
    btnStop.addEventListener('click', function () { botPost('stop').then(pollBotState).then(function () { notify('Đã tắt bot'); }); });
    btnRestart.addEventListener('click', function () {
        if (!confirm('Khởi động lại bot? Bot sẽ offline vài giây.')) return;
        botPost('restart').then(function () { notify('Đang khởi động lại bot...'); setTimeout(pollBotState, 1500); });
    });

    var BG_OK = /^https:\/\/[^\s"'()<>\\]{4,480}$/;
    function applyTheme() {
        var root = document.documentElement;
        var u = bgUrl.value.trim();
        if (BG_OK.test(u)) {
            root.style.setProperty('--bg-img', 'url("' + u + '")');
            document.body.classList.add('has-bg');
            bgPrev.style.backgroundImage = 'url("' + u + '")';
        } else {
            document.body.classList.remove('has-bg');
            bgPrev.style.backgroundImage = 'none';
        }
        root.style.setProperty('--dim', String(bgDim.value / 100));
        bgDimVal.textContent = bgDim.value + '%';
        var t = siteTitle.value.trim() || 'KSBot Command Center';
        document.getElementById('siteH1').textContent = t;
        document.title = t;
    }
    [siteTitle, bgUrl, bgDim].forEach(function (el) { el.addEventListener('input', function () { refresh(); }); });
    document.getElementById('streamUrl').addEventListener('input', function () { refresh(); });
    document.getElementById('bgClear').addEventListener('click', function () { bgUrl.value = ''; refresh(); });
    document.getElementById('bgDefault').addEventListener('click', function () { bgUrl.value = DEFAULT_BG; refresh(); });

    function refresh() {
        applyTheme();
        document.documentElement.style.setProperty('--accent', getAccent());
        var mode = getMode();
        dot.style.background = COLORS[mode];
        pstate.textContent = LABELS[mode];
        delayVal.textContent = delayInput.value + ' giây';
        prefixDemo.textContent = (prefixInput.value || '!') + 'help';
        offNote.style.display = statusSwitch.checked ? 'none' : 'block';
        saveBar.classList.toggle('show', serialize().toString() !== snapshot);
    }
    function onChange() { showMsg(); refresh(); }

    function bindRow(row) {
        row.querySelector('input').addEventListener('input', function () { showMsg(); refresh(); });
        row.querySelector('.icon-btn').addEventListener('click', function () {
            if (msgBox.children.length > 1) { row.remove(); } else { row.querySelector('input').value = ''; }
            showMsg(); refresh();
        });
    }
    function addRow(value) {
        var row = document.createElement('div');
        row.className = 'msg-row';
        row.innerHTML = '<input type="text" maxlength="120" placeholder="Nhập câu status..."><button type="button" class="icon-btn" title="Xóa dòng" aria-label="Xóa dòng">✕</button>';
        row.querySelector('input').value = value || '';
        msgBox.appendChild(row); bindRow(row);
        return row;
    }
    msgBox.querySelectorAll('.msg-row').forEach(bindRow);
    document.getElementById('addMsg').addEventListener('click', function () { addRow('').querySelector('input').focus(); refresh(); });

    var lastFocus = null;
    msgBox.addEventListener('focusin', function (e) { if (e.target.tagName === 'INPUT') lastFocus = e.target; });
    document.querySelectorAll('.chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            var t = lastFocus || msgBox.querySelector('input');
            if (!t) return;
            var s = t.selectionStart == null ? t.value.length : t.selectionStart;
            var e = t.selectionEnd == null ? s : t.selectionEnd;
            t.value = t.value.slice(0, s) + chip.dataset.v + t.value.slice(e);
            t.focus(); t.setSelectionRange(s + chip.dataset.v.length, s + chip.dataset.v.length);
            showMsg(); refresh();
        });
    });
    function setAll(v) { form.querySelectorAll('.mod input[type=checkbox]').forEach(function (c) { c.checked = v; }); refresh(); }
    document.getElementById('allOn').addEventListener('click', function () { setAll(true); });
    document.getElementById('allOff').addEventListener('click', function () { setAll(false); });

    form.addEventListener('change', onChange);
    prefixInput.addEventListener('input', function () { showMsg(); refresh(); });
    delayInput.addEventListener('input', function () { refresh(); });
    delayInput.addEventListener('change', restartPreview);

    var toastTimer = null;
    function notify(text, isErr) {
        toast.textContent = text;
        toast.className = 'toast show' + (isErr ? ' err' : '');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.className = 'toast' + (isErr ? ' err' : ''); }, 3000);
    }

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        saveBtn.disabled = true; saveBtn.textContent = 'Đang lưu...';
        var body = serialize();
        authFetch('/save', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'fetch' }, body: body.toString() })
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (d) {
                snapshot = body.toString(); refresh();
                notify(d && d.restarted ? 'Đã lưu, bot đang khởi động lại để đổi thiết bị...' : 'Đã lưu cấu hình');
                if (d && d.restarted) setTimeout(pollBotState, 1500);
            })
            .catch(function (err) { notify('Lưu thất bại: ' + err.message, true); })
            .then(function () { saveBtn.disabled = false; saveBtn.textContent = 'Lưu thay đổi'; });
    });
    resetBtn.addEventListener('click', function () { location.reload(); });

    /* ===================== CHAT KIỂU DISCORD ===================== */
    function initChat() {
        var dc = document.getElementById('dc');
        if (!dc) return;
        var rail = document.getElementById('dcRail'), sideHead = document.getElementById('dcSideHead'),
            chans = document.getElementById('dcChans'), msgs = document.getElementById('dcMsgs'),
            title = document.getElementById('dcTitle'), ta = document.getElementById('dcInput'),
            fileIn = document.getElementById('dcFile'), pop = document.getElementById('dcPop'),
            replyBar = document.getElementById('dcReply'), filesBar = document.getElementById('dcFiles'),
            sendBtn = document.getElementById('dcSend');
        var C = { guild: null, chan: null, name: '', isDM: false, canSend: true, list: [], lastId: null,
                  reply: null, pending: [], tick: 0, busy: false, sending: false, seq: 0, railSig: '',
                  chCache: {}, popMode: 'compose', popMsg: null, popTab: 'uni', guilds: [] };
        var UNI = '😀 😁 😂 🤣 😊 😍 😘 😎 🤔 😅 😭 😡 🥺 😴 🤯 🥳 👍 👎 👏 🙏 💪 👀 🔥 ✨ 💯 ❤️ 💔 💜 🎉 🎁 ✅ ❌ ⭐ 🌟 💀 👑 🐺 🍀 🎮 🎲 💰 🧧 🌙 ☀️ 😈 🤝 🙌 😢 😏 🥰 😱 🤡 💩 👋 😆 😋'.split(' ');

        function h(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
        function rpc(type, data) {
            return authFetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ type: type }, data || {})) })
                .then(function (r) { return r.json(); })
                .then(function (d) { if (!d.ok) throw new Error(d.error || 'Lỗi không rõ'); return d.data; });
        }
        function saveLast() { try { localStorage.setItem('ks_chat_last', JSON.stringify({ g: C.guild, c: C.chan, n: C.name, d: C.isDM })); } catch (e) {} }
        function loadLast() { try { return JSON.parse(localStorage.getItem('ks_chat_last') || 'null') || {}; } catch (e) { return {}; } }

        function fmt(text) {
            var s = h(text);
            s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
            s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
            s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
            s = s.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
            s = s.replace(/&lt;(a?):(\w+):(\d+)&gt;/g, function (_m, a, n, id) {
                return '<img class="ce" alt=":' + n + ':" src="https://cdn.discordapp.com/emojis/' + id + (a ? '.gif' : '.png') + '?size=48">';
            });
            return s;
        }
        function p2(n) { return ('0' + n).slice(-2); }
        function tfmt(ts) {
            var d = new Date(ts * 1000), t = p2(d.getHours()) + ':' + p2(d.getMinutes());
            return d.toDateString() === new Date().toDateString() ? 'Hôm nay ' + t : p2(d.getDate()) + '/' + p2(d.getMonth() + 1) + ' ' + t;
        }
        function fsize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }
        function httpsOnly(u) { return /^https:\/\//.test(u || '') ? u : ''; }

        function embedHtml(e) {
            var o = '<div class="emb"' + (e.color != null ? ' style="border-left-color:#' + ('000000' + Number(e.color).toString(16)).slice(-6) + '"' : '') + '>';
            if (e.author) o += '<div class="ea">' + h(e.author) + '</div>';
            if (e.title) o += '<div class="et">' + (e.url ? '<a href="' + h(httpsOnly(e.url)) + '" target="_blank" rel="noopener noreferrer">' + h(e.title) + '</a>' : h(e.title)) + '</div>';
            if (e.description) o += '<div class="ed2">' + fmt(e.description) + '</div>';
            (e.fields || []).forEach(function (f) { o += '<div class="ef"><b>' + h(f.name) + '</b>' + fmt(f.value) + '</div>'; });
            if (httpsOnly(e.image)) o += '<img class="big" loading="lazy" src="' + h(e.image) + '" alt="">';
            else if (httpsOnly(e.thumb)) o += '<img class="big" loading="lazy" src="' + h(e.thumb) + '" alt="">';
            if (e.footer) o += '<div class="ft">' + h(e.footer) + '</div>';
            return o + '</div>';
        }

        function msgHtml(m, prev) {
            var grp = prev && prev.author.id === m.author.id && !m.reply && (m.ts - prev.ts) < 420;
            var o = '<div class="m' + (grp ? '' : ' first') + '" data-id="' + h(m.id) + '" data-act="sel">';
            o += grp ? '<div class="gut">' + tfmt(m.ts).slice(-5) + '</div>'
                     : '<img class="av" data-act="dm" data-uid="' + h(m.author.id) + '" loading="lazy" alt="" src="' + h(m.author.avatar) + '">';
            o += '<div class="body">';
            if (m.reply) o += '<div class="rep">↪ <b>' + h(m.reply.author || '?') + '</b> ' + h(m.reply.content || '(tin nhắn gốc)') + '</div>';
            if (!grp) o += '<div class="head"><span class="nm" data-act="dm" data-uid="' + h(m.author.id) + '">' + h(m.author.name) + '</span>' + (m.author.bot ? '<span class="tag">BOT</span>' : '') + '<span class="ts">' + tfmt(m.ts) + '</span></div>';
            if (m.content) o += '<div class="txt">' + fmt(m.content) + (m.edited ? ' <span class="ed">(đã sửa)</span>' : '') + '</div>';
            (m.atts || []).forEach(function (a) {
                var u = h(httpsOnly(a.url));
                if (/^image\//.test(a.type)) o += '<div class="att"><a href="' + u + '" target="_blank" rel="noopener noreferrer"><img loading="lazy" src="' + u + '" alt=""></a></div>';
                else if (/^video\//.test(a.type)) o += '<div class="att"><video controls preload="none" src="' + u + '"></video></div>';
                else o += '<a class="file" href="' + u + '" target="_blank" rel="noopener noreferrer">📎 ' + h(a.name) + ' <small>(' + fsize(a.size) + ')</small></a>';
            });
            (m.embeds || []).forEach(function (e) { o += embedHtml(e); });
            if (m.components) o += '<div class="comp-note">(tin nhắn có nút bấm/menu, chỉ dùng được trên Discord)</div>';
            if (m.reactions && m.reactions.length) {
                o += '<div class="rx">';
                m.reactions.forEach(function (r) {
                    o += '<button type="button" class="rxb' + (r.me ? ' me' : '') + '" data-act="rx" data-emoji="' + h(r.emoji) + '" data-me="' + (r.me ? 1 : 0) + '">' +
                        (r.url ? '<img class="ce" src="' + h(r.url) + '" alt="">' : h(r.emoji)) + ' <span>' + r.count + '</span></button>';
                });
                o += '</div>';
            }
            o += '</div><div class="tools"><button type="button" data-act="rxadd" title="Thả icon">😊</button><button type="button" data-act="reply" title="Trả lời">↩</button><button type="button" data-act="del" title="Xóa tin">🗑</button></div></div>';
            return o;
        }

        function renderMsgs(stick) {
            var atBottom = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90, top = msgs.scrollTop, oldH = msgs.scrollHeight;
            var o = C.list.length ? '<button type="button" class="dc-older" data-act="older">⬆ Tải tin cũ hơn</button>' : '';
            for (var i = 0; i < C.list.length; i++) o += msgHtml(C.list[i], C.list[i - 1]);
            msgs.innerHTML = o || '<div class="dc-empty">Chưa có tin nhắn nào trong kênh này</div>';
            C.lastId = C.list.length ? C.list[C.list.length - 1].id : null;
            if (stick || atBottom) msgs.scrollTop = msgs.scrollHeight; else msgs.scrollTop = top + (msgs.scrollHeight - oldH > 0 && C.prepended ? msgs.scrollHeight - oldH : 0);
            C.prepended = false;
        }

        function mergeById(a, b) {
            var seen = {}, out = [];
            a.concat(b).forEach(function (m) { if (!seen[m.id]) { seen[m.id] = 1; out.push(m); } });
            out.sort(function (x, y) { return x.ts - y.ts; });
            return out;
        }

        function loadHist(incremental, stick) {
            if (!C.chan || C.busy) return Promise.resolve();
            C.busy = true;
            var seq = C.seq, req = { channel_id: C.chan, limit: incremental && C.lastId ? 50 : Math.min(100, Math.max(50, C.list.length)) };
            if (incremental && C.lastId) req.after = C.lastId;
            return rpc('history', req).then(function (d) {
                if (seq !== C.seq) return;
                C.canSend = d.channel.can_send;
                syncComposer();
                if (incremental && C.lastId) {
                    if (!d.messages.length) return;
                    C.list = mergeById(C.list, d.messages).slice(-300);
                } else {
                    C.list = d.messages;
                }
                renderMsgs(stick);
            }).catch(function (e) {
                if (seq !== C.seq) return;
                if (!C.list.length) msgs.innerHTML = '<div class="dc-empty">' + h(e.message) + '</div>';
                else notify(e.message, true);
            }).then(function () { C.busy = false; });
        }

        function syncComposer() {
            ta.disabled = !C.chan || !C.canSend;
            sendBtn.disabled = ta.disabled;
            ta.placeholder = !C.chan ? 'Chọn kênh để chat' : (C.canSend ? 'Nhắn vào ' + (C.isDM ? '@' : '#') + C.name + ' bằng tên bot' : 'Bot không có quyền gửi tin ở kênh này');
        }

        function openChannel(id, name, canSend, isDM) {
            C.seq++; C.chan = id; C.name = name; C.isDM = !!isDM; C.canSend = canSend !== false; C.list = []; C.lastId = null; C.busy = false;
            clearReply();
            title.innerHTML = '<span class="dim">' + (isDM ? '@' : '#') + '</span>' + h(name);
            msgs.innerHTML = '<div class="dc-empty">Đang tải tin nhắn...</div>';
            dc.classList.add('in-chat');
            syncComposer(); markChan(); saveLast();
            loadHist(false, true);
        }
        function markChan() {
            chans.querySelectorAll('.chan').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-id') === C.chan); });
        }

        /* --- thanh bên trái: server + DM --- */
        function renderRail(list) {
            C.guilds = (list || []).filter(function (g) { return g && g.id != null; });
            var sig = JSON.stringify(C.guilds.map(function (g) { return [g.id, g.icon]; })) + C.guild;
            if (sig === C.railSig) return;
            C.railSig = sig;
            var keep = rail.scrollTop;
            var o = '<button type="button" class="rail-ico' + (C.guild === '@dm' ? ' on' : '') + '" data-g="@dm" title="Tin nhắn riêng">💬</button><div class="rail-sep"></div>';
            C.guilds.forEach(function (g) {
                var ico = httpsOnly(g.icon) ? '<img src="' + h(g.icon) + '" alt="">' : h((g.name || '?').charAt(0).toUpperCase());
                o += '<button type="button" class="rail-ico' + (String(g.id) === String(C.guild) ? ' on' : '') + '" data-g="' + h(g.id) + '" title="' + h(g.name) + '">' + ico + '</button>';
            });
            rail.innerHTML = o;
            rail.scrollTop = keep;
        }
        rail.addEventListener('click', function (e) {
            var b = e.target.closest('.rail-ico'); if (!b) return;
            var g = b.getAttribute('data-g');
            if (g === '@dm') selectDM(); else selectGuild(g);
        });

        function selectGuild(id, thenChan) {
            C.guild = id; C.railSig = ''; renderRail(C.guilds);
            dc.classList.remove('in-chat');
            var g = C.guilds.filter(function (x) { return String(x.id) === String(id); })[0];
            sideHead.textContent = g ? g.name : 'Server';
            chans.innerHTML = '<div class="dc-note">Đang tải kênh...</div>';
            var show = function (d) {
                if (C.guild !== id) return;
                var o = '';
                d.groups.forEach(function (gr) {
                    if (gr.name) o += '<div class="grp">' + h(gr.name) + '</div>';
                    gr.channels.forEach(function (c) { o += '<button type="button" class="chan" data-id="' + h(c.id) + '" data-name="' + h(c.name) + '" data-send="' + (c.can_send ? 1 : 0) + '"><span class="h">#</span><span class="t">' + h(c.name) + '</span></button>'; });
                });
                chans.innerHTML = o || '<div class="dc-note">Bot không xem được kênh chữ nào ở server này.</div>';
                markChan();
                if (thenChan) { var el = chans.querySelector('.chan[data-id="' + thenChan + '"]'); if (el) openChannel(thenChan, el.getAttribute('data-name'), el.getAttribute('data-send') === '1', false); }
            };
            if (C.chCache[id] && Date.now() - C.chCache[id].t < 60000) { show(C.chCache[id].d); return; }
            rpc('channels', { guild_id: id }).then(function (d) { C.chCache[id] = { t: Date.now(), d: d }; show(d); })
                .catch(function (e) { chans.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        function selectDM() {
            C.guild = '@dm'; C.railSig = ''; renderRail(C.guilds);
            dc.classList.remove('in-chat');
            sideHead.textContent = 'Tin nhắn riêng';
            chans.innerHTML = '<div class="dm-open"><input type="text" id="dmId" placeholder="Nhập User ID để mở DM" inputmode="numeric"><button type="button" id="dmGo">Mở</button></div><div id="dmList"><div class="dc-note">Đang tải...</div></div>';
            document.getElementById('dmGo').addEventListener('click', function () { var v = document.getElementById('dmId').value.trim(); if (v) openDM(v); });
            rpc('dm_list').then(function (list) {
                var box = document.getElementById('dmList'); if (!box) return;
                box.innerHTML = list.length ? list.map(function (x) {
                    return '<button type="button" class="chan" data-dm="' + h(x.user.id) + '" data-name="' + h(x.user.name) + '"><img src="' + h(x.user.avatar) + '" alt=""><span class="t">' + h(x.user.name) + '</span></button>';
                }).join('') : '<div class="dc-note">Chưa có cuộc DM nào. Nhập User ID ở trên, hoặc bấm vào tên/avatar ai đó trong chat server để mở DM.</div>';
            }).catch(function (e) { var box = document.getElementById('dmList'); if (box) box.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        function openDM(uid) {
            rpc('dm_open', { user_id: uid }).then(function (d) {
                if (C.guild !== '@dm') selectDM();
                openChannel(d.channel_id, d.user.name, true, true);
            }).catch(function (e) { notify(e.message, true); });
        }
        chans.addEventListener('click', function (e) {
            var b = e.target.closest('.chan'); if (!b) return;
            if (b.hasAttribute('data-dm')) { openDM(b.getAttribute('data-dm')); return; }
            openChannel(b.getAttribute('data-id'), b.getAttribute('data-name'), b.getAttribute('data-send') === '1', false);
        });
        document.getElementById('dcBack').addEventListener('click', function () { dc.classList.remove('in-chat'); });

        /* --- thao tác trên tin nhắn --- */
        function msgOf(el) { var row = el.closest('.m'); if (!row) return null; var id = row.getAttribute('data-id'); return C.list.filter(function (m) { return m.id === id; })[0] || null; }
        function toggleReact(mid, emoji, remove) {
            rpc('react', { channel_id: C.chan, message_id: mid, emoji: emoji, remove: remove })
                .then(function () { C.busy = false; return loadHist(false); })
                .catch(function (e) { notify(e.message, true); });
        }
        msgs.addEventListener('click', function (e) {
            var t = e.target.closest('[data-act]'); if (!t) return;
            var act = t.getAttribute('data-act');
            if (act === 'sel') { if (e.target.closest('a,img.ce,video')) return; document.querySelectorAll('.m.sel').forEach(function (x) { if (x !== t) x.classList.remove('sel'); }); t.classList.toggle('sel'); return; }
            e.stopPropagation();
            var m = msgOf(t);
            if (act === 'older') {
                if (!C.list.length) return;
                rpc('history', { channel_id: C.chan, before: C.list[0].id, limit: 50 }).then(function (d) {
                    if (!d.messages.length) { notify('Đã tới tin nhắn đầu tiên'); return; }
                    C.list = mergeById(d.messages, C.list); C.prepended = true; renderMsgs(false);
                }).catch(function (er) { notify(er.message, true); });
            } else if (act === 'dm') { openDM(t.getAttribute('data-uid')); }
            else if (!m) { return; }
            else if (act === 'rx') toggleReact(m.id, t.getAttribute('data-emoji'), t.getAttribute('data-me') === '1');
            else if (act === 'rxadd') openPop('react', m.id);
            else if (act === 'reply') { C.reply = { id: m.id, name: m.author.name, ping: true }; drawReply(); ta.focus(); }
            else if (act === 'del') {
                if (!confirm('Xóa tin nhắn này?')) return;
                rpc('delete', { channel_id: C.chan, message_id: m.id }).then(function () { C.list = C.list.filter(function (x) { return x.id !== m.id; }); renderMsgs(false); notify('Đã xóa tin nhắn'); }).catch(function (er) { notify(er.message, true); });
            }
        });
        /* thanh "Đang trả lời" có nút @ bật/tắt tag người được trả lời (mặc định BẬT) */
        function drawReply() {
            if (!C.reply) { replyBar.hidden = true; replyBar.innerHTML = ''; return; }
            replyBar.hidden = false;
            replyBar.innerHTML = '<span>Đang trả lời <b>' + h(C.reply.name) + '</b></span>' +
                '<span class="rbtns"><button type="button" class="pingbtn' + (C.reply.ping ? ' on' : '') + '" id="dcPing" title="Bật: tag người được trả lời. Tắt: trả lời im lặng">@ ' + (C.reply.ping ? 'BẬT' : 'TẮT') + '</button>' +
                '<button type="button" id="dcReplyX" title="Hủy trả lời">✕</button></span>';
        }
        function clearReply() { C.reply = null; drawReply(); }
        replyBar.addEventListener('click', function (e) {
            if (e.target.id === 'dcReplyX') clearReply();
            else if (e.target.id === 'dcPing' && C.reply) { C.reply.ping = !C.reply.ping; drawReply(); }
        });

        /* --- picker emoji --- */
        function popBuild() {
            var o = '<div class="pop-tabs"><button type="button" data-t="uni" class="' + (C.popTab === 'uni' ? 'on' : '') + '">Emoji</button><button type="button" data-t="srv" class="' + (C.popTab === 'srv' ? 'on' : '') + '">Emoji server</button></div><div class="emo-grid" id="emoGrid"></div>';
            pop.innerHTML = o;
            var grid = document.getElementById('emoGrid');
            if (C.popTab === 'uni') { grid.innerHTML = UNI.map(function (e) { return '<button type="button" data-e="' + h(e) + '">' + h(e) + '</button>'; }).join(''); return; }
            grid.innerHTML = '<div class="dc-note" style="grid-column:1/-1">Đang tải...</div>';
            rpc('emojis', { guild_id: C.guild && C.guild !== '@dm' ? C.guild : null }).then(function (list) {
                grid.innerHTML = list.length ? list.map(function (x) {
                    return '<button type="button" data-e="&lt;' + (x.animated ? 'a' : '') + ':' + h(x.name) + ':' + h(x.id) + '&gt;" title=":' + h(x.name) + ':"><img loading="lazy" src="' + h(x.url) + '" alt=""></button>';
                }).join('') : '<div class="dc-note" style="grid-column:1/-1">Không có emoji server nào.</div>';
            }).catch(function (e) { grid.innerHTML = '<div class="dc-note" style="grid-column:1/-1">' + h(e.message) + '</div>'; });
        }
        function openPop(mode, msgId) { C.popMode = mode; C.popMsg = msgId || null; pop.classList.add('show'); popBuild(); }
        function closePop() { pop.classList.remove('show'); }
        pop.addEventListener('click', function (e) {
            e.stopPropagation();
            var t = e.target.closest('button'); if (!t) return;
            if (t.hasAttribute('data-t')) { C.popTab = t.getAttribute('data-t'); popBuild(); return; }
            var raw = t.getAttribute('data-e'); if (!raw) return;
            var emoji = raw.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
            if (C.popMode === 'react') { toggleReact(C.popMsg, emoji, false); closePop(); }
            else { var s = ta.selectionStart || ta.value.length; ta.value = ta.value.slice(0, s) + emoji + ta.value.slice(ta.selectionEnd || s); ta.focus(); ta.setSelectionRange(s + emoji.length, s + emoji.length); }
        });
        document.getElementById('dcEmoji').addEventListener('click', function (e) { e.stopPropagation(); if (pop.classList.contains('show') && C.popMode === 'compose') closePop(); else openPop('compose'); });
        document.addEventListener('click', function (e) { if (!pop.contains(e.target)) closePop(); });

        /* --- file đính kèm + gửi --- */
        function renderFiles() {
            filesBar.innerHTML = C.pending.map(function (f, i) { return '<div class="fchip"><span>📎 ' + h(f.name) + ' (' + fsize(f.size) + ')</span><button type="button" data-i="' + i + '">✕</button></div>'; }).join('');
        }
        filesBar.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; C.pending.splice(+b.getAttribute('data-i'), 1); renderFiles(); });
        function addFiles(list) {
            Array.prototype.forEach.call(list || [], function (f) {
                if (f.size > 10 * 1024 * 1024) { notify(f.name + ' quá 10MB, Discord không nhận', true); return; }
                if (C.pending.length >= 10) { notify('Tối đa 10 file mỗi tin', true); return; }
                C.pending.push(f);
            });
            renderFiles();
        }
        document.getElementById('dcAttach').addEventListener('click', function () { if (C.chan && C.canSend) fileIn.click(); });
        fileIn.addEventListener('change', function () { addFiles(fileIn.files); fileIn.value = ''; });
        ta.addEventListener('paste', function (e) { if (e.clipboardData && e.clipboardData.files.length) { addFiles(e.clipboardData.files); e.preventDefault(); } });
        var main = dc.querySelector('.dc-main');
        main.addEventListener('dragover', function (e) { e.preventDefault(); dc.classList.add('drag'); });
        main.addEventListener('dragleave', function (e) { if (e.target === main) dc.classList.remove('drag'); });
        main.addEventListener('drop', function (e) { e.preventDefault(); dc.classList.remove('drag'); if (C.chan && C.canSend) addFiles(e.dataTransfer.files); });

        function uploadAll(files) {
            return files.reduce(function (p, f) {
                return p.then(function (arr) {
                    return authFetch('/api/upload', { method: 'POST', headers: { 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' }, body: f })
                        .then(function (r) { return r.json(); })
                        .then(function (d) { if (!d.ok) throw new Error(d.error || 'Upload lỗi'); arr.push(d.id); return arr; });
                });
            }, Promise.resolve([]));
        }
        function autosize() { ta.style.height = 'auto'; ta.style.height = Math.min(150, ta.scrollHeight) + 'px'; }
        function send() {
            if (!C.chan || !C.canSend || C.sending) return;
            var text = ta.value.trim();
            if (!text && !C.pending.length) return;
            C.sending = true; sendBtn.disabled = true;
            var files = C.pending.slice();
            uploadAll(files).then(function (ids) {
                return rpc('send', { channel_id: C.chan, content: text, reply_to: C.reply ? C.reply.id : null, ping: C.reply ? C.reply.ping : true, uploads: ids });
            }).then(function () {
                ta.value = ''; autosize(); C.pending = []; renderFiles(); clearReply();
                C.busy = false; return loadHist(true, true);
            }).catch(function (e) { notify(e.message, true); })
              .then(function () { C.sending = false; syncComposer(); });
        }
        sendBtn.addEventListener('click', send);
        ta.addEventListener('input', autosize);
        ta.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });

        /* --- nút KHÓA: khóa cuộn cả trang khi ở tab Chat, chỉ cuộn trong 3 cột --- */
        var lockBtn = document.getElementById('dcLock');
        var locked = true;
        try { locked = localStorage.getItem('ks_chat_lock') !== '0'; } catch (e) {}
        function applyLock() {
            var on = locked && document.body.getAttribute('data-tab') === 'chat';
            document.documentElement.classList.toggle('chat-lock', on);
            lockBtn.textContent = locked ? '🔒' : '🔓';
            lockBtn.classList.toggle('on', locked);
            lockBtn.title = locked ? 'Đang khóa trang, chỉ cuộn trong chat. Bấm để mở' : 'Bấm để khóa trang, không cuộn cả web';
        }
        lockBtn.addEventListener('click', function () {
            locked = !locked;
            try { localStorage.setItem('ks_chat_lock', locked ? '1' : '0'); } catch (e) {}
            applyLock();
        });
        window.__chatLockApply = applyLock;
        applyLock();

        /* --- cập nhật tin mới --- */
        setInterval(function () {
            if (document.body.getAttribute('data-tab') !== 'chat' || !C.chan || document.hidden || C.busy || C.sending) return;
            C.tick++;
            loadHist(!(C.tick % 6 === 0 && C.list.length <= 100));
        }, 2500);

        onGuildList = renderRail;
        syncComposer();
        window.__chatShow = function () {
            renderRail(liveStats.guild_list || C.guilds);
            if (C.guild) return;
            var last = loadLast();
            if (last.d && last.c) { selectDM(); openDM_last(last); }
            else if (last.g && last.g !== '@dm') selectGuild(last.g, last.c);
            else if (C.guilds.length) selectGuild(C.guilds[0].id);
        };
        function openDM_last(last) { openChannel(last.c, last.n || 'DM', true, true); }
    }
    if (CHAT_ON) initChat();

    var TABS = ['overview', 'config', 'features', 'chat', 'servers', 'theme'];
    function setTab(name) {
        if (TABS.indexOf(name) < 0) name = 'overview';
        document.body.setAttribute('data-tab', name);
        if (window.__chatLockApply) window.__chatLockApply();
        document.querySelectorAll('[data-show]').forEach(function (el) { el.hidden = el.getAttribute('data-show').split(' ').indexOf(name) < 0; });
        document.querySelectorAll('.tab').forEach(function (t) {
            var on = t.getAttribute('data-tab') === name;
            t.classList.toggle('on', on); t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        try { history.replaceState(null, '', '#' + name); } catch (e) {}
        if (name === 'chat' && window.__chatShow) window.__chatShow();
    }
    document.querySelectorAll('.tab').forEach(function (t) {
        t.addEventListener('click', function () { setTab(t.getAttribute('data-tab')); window.scrollTo(0, 0); });
    });
    window.addEventListener('hashchange', function () { setTab((location.hash || '').replace('#', '')); });

    snapshot = serialize().toString();
    refresh(); restartPreview(); applyStats();
    setTab((location.hash || '').replace('#', ''));
    pollStats(); pollActivity(); pollBotState(); pollUptime();
    setInterval(pollStats, 10000);
    setInterval(pollActivity, 15000);
    setInterval(pollBotState, 8000);
    setInterval(pollUptime, 10000);
})();
"""

CHAT_BLOCK = """
<section class="dc" id="dc" data-show="chat">
    <nav class="dc-rail" id="dcRail" aria-label="Server"></nav>
    <aside class="dc-side">
        <div class="dc-side-head" id="dcSideHead">Chọn server</div>
        <div class="dc-chans" id="dcChans"><div class="dc-note">Chọn một server ở cột bên trái, hoặc 💬 để xem tin nhắn riêng.</div></div>
    </aside>
    <div class="dc-main">
        <div class="dc-top">
            <button type="button" class="dc-back" id="dcBack" aria-label="Quay lại">‹</button>
            <span id="dcTitle" class="dim">Chọn một kênh để bắt đầu</span>
            <span class="dc-bot" title="Bạn đang chat bằng tên bot này"><span class="dc-bot-ico" id="dcBotIco">🤖</span><span id="dcBotName">Bot</span></span>
            <button type="button" class="lock-btn" id="dcLock" aria-label="Khóa cuộn trang">🔒</button>
        </div>
        <div class="dc-msgs" id="dcMsgs"><div class="dc-empty">Chưa chọn kênh nào</div></div>
        <div class="dc-comp">
            <div class="dc-reply" id="dcReply" hidden></div>
            <div class="dc-files" id="dcFiles"></div>
            <div class="cbox">
                <button type="button" id="dcAttach" title="Đính kèm file (hoặc kéo thả / dán ảnh)">＋</button>
                <textarea id="dcInput" rows="1" placeholder="Chọn kênh để chat" disabled></textarea>
                <button type="button" id="dcEmoji" title="Emoji">😊</button>
                <button type="button" id="dcSend" title="Gửi" disabled>➤</button>
            </div>
            <input type="file" id="dcFile" multiple hidden>
            <div class="dc-pop" id="dcPop"></div>
        </div>
    </div>
</section>
"""

CHAT_LOCKED = """
<section class="card" data-show="chat">
    <h2>🔒 Chat đang bị khóa</h2>
    <p class="sub">Tab này đọc được tin nhắn (kể cả DM) và gửi tin bằng tên bot, nên bắt buộc có mật khẩu.</p>
    <p class="hint">Vào Bot-Hosting → <b>Môi trường</b> → thêm biến <code>DASHBOARD_PASSWORD</code> với mật khẩu bạn muốn → khởi động lại. Sau đó toàn bộ dashboard sẽ yêu cầu đăng nhập.</p>
</section>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__SITE_TITLE__</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='24' fill='%238b9bff'/%3E%3Crect x='24' y='34' width='52' height='38' rx='12' fill='%230e1118'/%3E%3Ccircle cx='38' cy='53' r='6' fill='%238b9bff'/%3E%3Ccircle cx='62' cy='53' r='6' fill='%238b9bff'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>__CSS__</style>
<style>__THEME_CSS__</style>
</head>
<body data-tab="overview" class="__BODY_CLASS__">
<div class="wrap">
    <header class="top">
        <div class="brand">
            <div class="brand-mark" id="brandMark">K</div>
            <div>
                <h1 id="siteH1">__SITE_TITLE__</h1>
                <p>Chỉnh cấu hình bot, thay đổi có hiệu lực sau khi lưu</p>
            </div>
        </div>
        <div class="top-right">
            <a class="invite-btn" href="__INVITE_URL__" target="_blank" rel="noopener">
                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.3 4.4A19.8 19.8 0 0 0 15.6 3c-.2.4-.5.9-.6 1.3a18 18 0 0 0-5.9 0C8.9 3.9 8.6 3.4 8.4 3a19.8 19.8 0 0 0-4.7 1.4C1.4 8.3.8 12 1.1 15.7a20 20 0 0 0 6 3c.5-.6.9-1.3 1.3-2a13 13 0 0 1-2-1c.2-.1.3-.2.5-.3a14.4 14.4 0 0 0 12.2 0l.5.3a13 13 0 0 1-2 1c.4.7.8 1.4 1.3 2a20 20 0 0 0 6-3c.4-4.3-.6-8-2.6-11.3ZM8.7 13.4c-.9 0-1.6-.9-1.6-1.9s.7-1.9 1.6-1.9 1.7.9 1.6 1.9c0 1-.7 1.9-1.6 1.9Zm6.6 0c-.9 0-1.6-.9-1.6-1.9s.7-1.9 1.6-1.9 1.7.9 1.6 1.9c0 1-.7 1.9-1.6 1.9Z"/></svg>
                Mời bot vào server
            </a>
            <div class="live"><i></i> Dashboard đang chạy</div>
            __LOGOUT__
        </div>
    </header>

    <nav class="tabs" role="tablist" aria-label="Các mục của dashboard">
        <button type="button" class="tab on" role="tab" data-tab="overview">Tổng quan</button>
        <button type="button" class="tab" role="tab" data-tab="config">Cấu hình &amp; Status</button>
        <button type="button" class="tab" role="tab" data-tab="features">Tính năng</button>
        <button type="button" class="tab" role="tab" data-tab="chat">💬 Chat</button>
        <button type="button" class="tab" role="tab" data-tab="servers">Server</button>
        <button type="button" class="tab" role="tab" data-tab="theme">Giao diện</button>
    </nav>

    __CHAT_BLOCK__

    <form id="cfg" action="/save" method="POST">
    <div class="layout">
        <main>
            <section class="card" data-show="config">
                <h2>Cấu hình chung</h2>
                <p class="sub">Ký tự đứng trước mỗi lệnh của bot.</p>
                <div class="field">
                    <label for="prefix">Prefix lệnh</label>
                    <div class="prefix-row">
                        <input type="text" id="prefix" name="prefix" maxlength="5" value="__PREFIX__" autocomplete="off">
                        <div class="prefix-demo">Ví dụ: <code id="prefixDemo">__PREFIX__help</code></div>
                    </div>
                </div>
            </section>

            <section class="card" data-show="theme">
                <h2>Giao diện dashboard</h2>
                <p class="sub">Đổi tên, màu và hình nền. Thấy ngay khi chỉnh, bấm Lưu để giữ lại.</p>
                <div class="field">
                    <label for="siteTitle">Tên dashboard</label>
                    <input type="text" id="siteTitle" name="site_title" maxlength="40" value="__SITE_TITLE__" autocomplete="off">
                </div>
                <div class="field">
                    <span class="label">Màu chủ đạo</span>
                    <div class="swatches">__SWATCHES__</div>
                </div>
                <div class="field">
                    <label for="bgUrl">Hình nền (link ảnh https)</label>
                    <input type="text" id="bgUrl" name="bg_url" value="__BG_URL__" placeholder="https://i.imgur.com/ten-anh.jpg" autocomplete="off">
                    <p class="hint">Dán link trực tiếp tới file ảnh (.jpg, .png, .webp). Ảnh từ Google (gstatic) thường nhỏ nên bị mờ khi phóng to; muốn nét thì up lên Imgur/Postimages rồi dán link vào đây.</p>
                    <div class="theme-prev" id="bgPrev"></div>
                </div>
                <div class="field">
                    <label for="bgDim">Độ tối lớp phủ (cao hơn thì chữ dễ đọc hơn)</label>
                    <div class="delay-row">
                        <input type="range" id="bgDim" name="bg_dim" min="0" max="90" value="__BG_DIM__">
                        <span class="delay-val" id="bgDimVal">__BG_DIM__%</span>
                    </div>
                </div>
                <div class="modes" style="grid-template-columns:1fr 1fr">
                    <button type="button" class="add-btn" id="bgDefault" style="margin:0">Dùng nền mặc định</button>
                    <button type="button" class="add-btn" id="bgClear" style="margin:0">Xóa hình nền</button>
                </div>
            </section>

            <section class="card" data-show="config">
                <h2>Trạng thái bot</h2>
                <p class="sub">Màu chấm, thiết bị, tốc độ xoay và nội dung các dòng status.</p>
                <div class="field">
                    <span class="label">Màu chấm trạng thái</span>
                    <div class="modes statuses">
                        <label class="mode"><input type="radio" name="status_mode" value="online" __M_ONLINE__>
                            <span><i class="dot" style="background:#3fc380"></i><div>Online<small>Chấm xanh</small></div></span></label>
                        <label class="mode"><input type="radio" name="status_mode" value="idle" __M_IDLE__>
                            <span><i class="dot" style="background:#f5b942"></i><div>Idle<small>Trăng vàng</small></div></span></label>
                        <label class="mode"><input type="radio" name="status_mode" value="dnd" __M_DND__>
                            <span><i class="dot" style="background:#ef5b6b"></i><div>DND<small>Đừng làm phiền</small></div></span></label>
                        <label class="mode"><input type="radio" name="status_mode" value="invisible" __M_INVISIBLE__>
                            <span><i class="dot" style="background:#5c6370"></i><div>Invisible<small>Ẩn, nhìn như offline</small></div></span></label>
                        <label class="mode"><input type="radio" name="status_mode" value="stream" __M_STREAM__>
                            <span><i class="dot" style="background:#9b59ff"></i><div>Stream<small>Chấm tím + nút Xem</small></div></span></label>
                    </div>
                    <p class="hint">Đổi màu chấm có hiệu lực ngay, không cần khởi động lại.</p>
                </div>
                <div class="field">
                    <span class="label">Thiết bị hiển thị cạnh tên bot</span>
                    <div class="modes platforms">__PLATFORMS__</div>
                    <p class="hint">Đổi thiết bị thì bot tự khởi động lại khi bạn bấm <b>Lưu thay đổi</b> (offline vài giây).</p>
                </div>
                <div class="field">
                    <span class="label">Loại hoạt động (chữ hiện trước câu status)</span>
                    <div class="modes">__ACTIVITY_TYPES__</div>
                    <p class="hint">Chọn nút <b>Stream</b> ở trên thì loại hoạt động này bị bỏ qua.</p>
                </div>
                <div class="field">
                    <label for="streamUrl">Link Stream (dùng cho loại Streaming)</label>
                    <input type="text" id="streamUrl" name="stream_url" value="__STREAM_URL__" placeholder="https://www.twitch.tv/ten_kenh" autocomplete="off">
                    <p class="hint">Chỉ nhận link twitch.tv/... hoặc YouTube (youtu.be/... tự đổi sang youtube.com/watch?v=...).</p>
                </div>
                <div class="field">
                    <label for="delay">Đổi câu status sau mỗi</label>
                    <div class="delay-row">
                        <input type="range" id="delay" name="status_delay" min="1" max="60" value="__DELAY__">
                        <span class="delay-val" id="delayVal">__DELAY__ giây</span>
                    </div>
                    <p class="hint">Discord giới hạn tốc độ đổi status, nên để từ 5 giây trở lên cho ổn định.</p>
                </div>
                <div class="field">
                    <span class="label">Các câu status luân phiên</span>
                    <div class="msgs" id="msgs">__MSG_ROWS__</div>
                    <button type="button" class="add-btn" id="addMsg">+ Thêm câu status</button>
                    <div class="chips">
                        <span>Chèn biến:</span>
                        <button type="button" class="chip" data-v="{guilds}">{guilds}</button>
                        <button type="button" class="chip" data-v="{members}">{members}</button>
                        <button type="button" class="chip" data-v="{prefix}">{prefix}</button>
                        <button type="button" class="chip" data-v="{ping}">{ping}</button>
                        <button type="button" class="chip" data-v="{ram}">{ram}</button>
                        <button type="button" class="chip" data-v="{latency_status}">{latency_status}</button>
                    </div>
                </div>
            </section>

            <section class="card" data-show="features">
                <div class="card-head">
                    <div>
                        <h2>Tính năng</h2>
                        <p class="sub">Bật hoặc tắt từng module của bot.</p>
                    </div>
                    <div class="bulk">
                        <button type="button" id="allOn">Bật tất cả</button>
                        <button type="button" id="allOff">Tắt tất cả</button>
                    </div>
                </div>
                <div class="mods">__MODULES__</div>
            </section>

            <section class="card" data-show="servers">
                <h2>Server bot đang ở</h2>
                <p class="sub">Danh sách lấy từ dữ liệu bot gửi lên, bấm Trước/Sau để chuyển trang.</p>
                <div id="guildRows"></div>
            </section>

            <section class="card" data-show="overview">
                <div class="ov-tiles">
                    <div class="ov-tile"><span>Server</span><b id="ovGuilds">—</b></div>
                    <div class="ov-tile"><span>Thành viên</span><b id="ovMembers">—</b></div>
                    <div class="ov-tile"><span>RAM dùng</span><b id="ovRam">—</b></div>
                    <div class="ov-tile"><span>Ping</span><b id="ovPing">—</b>
                        <svg class="spark" id="ovSpark" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true"></svg>
                    </div>
                </div>
                <p class="ov-foot" id="ovFoot">Đang chờ dữ liệu từ bot...</p>
                <div class="ov-tiles" style="margin-top:14px">
                    <div class="ov-tile" style="grid-column: span 2"><span>Bot online liên tục</span><b id="upTotal">—</b></div>
                    <div class="ov-tile"><span>Số lần khởi động lại</span><b id="upSessions">—</b></div>
                    <div class="ov-tile"><span>Chạy từ ngày</span><b id="upFirst">—</b></div>
                </div>
                <p class="ov-foot" id="upFoot"></p>
            </section>

            <section class="card" data-show="overview">
                <h2>Điều khiển bot</h2>
                <p class="sub" style="margin-bottom:0">Bật, tắt hoặc khởi động lại hẳn tiến trình bot, không chỉ ẩn trạng thái.</p>
                <div class="botctl">
                    <div class="botctl-state" id="botState"><i></i><span>Đang kiểm tra...</span></div>
                    <div class="botctl-btns">
                        <button type="button" class="btn start small" id="btnStart">Bật bot</button>
                        <button type="button" class="btn restart small" id="btnRestart">Khởi động lại</button>
                        <button type="button" class="btn stop small" id="btnStop">Tắt bot</button>
                    </div>
                </div>
            </section>

            <section class="card" data-show="overview">
                <h2>Hoạt động gần đây</h2>
                <p class="sub">Lệnh thành viên vừa dùng, lấy từ log bot gửi lên.</p>
                <div id="activityRows"></div>
            </section>
        </main>

        <div class="side" data-show="overview config">
        <aside class="preview card">
            <h2>Xem trước</h2>
            <p class="sub" style="margin-bottom:0">Bot sẽ hiển thị như thế này trên Discord.</p>
            <div class="profile">
                <div class="banner"></div>
                <div class="profile-body">
                    <div class="avatar-wrap">
                        <div class="avatar" id="avatar">🤖</div>
                        <i class="status-dot" id="dot"></i>
                    </div>
                    <div class="pname"><span id="pnameText">KSBot</span> <span class="tag">BOT</span></div>
                    <div class="pstate" id="pstate">Trực tuyến</div>
                    <div class="bubble" id="bubble"></div>
                    <div class="stats">
                        <div class="stat na" id="statGuilds"><b>—</b><span>Server</span></div>
                        <div class="stat na" id="statMembers"><b>—</b><span>Thành viên</span></div>
                        <div class="stat na" id="statRam"><b>—</b><span>RAM dùng</span></div>
                        <div class="stat na" id="statPing"><b>—</b><span>Ping</span></div>
                    </div>
                    <div class="data-note" id="dataNote"><i></i><span>Đang chờ dữ liệu từ bot...</span></div>
                </div>
            </div>
            <p class="off-note" id="offNote">Module Trạng thái bot đang tắt nên status sẽ không được đổi.</p>
            <p class="hint">Khi bot đang online, các biến như {guilds}, {members}, {ping} trong câu status dùng số thật từ bot. Chưa có dữ liệu thì dùng số mẫu.</p>
        </aside>
        </div>
    </div>

    <div class="savebar" id="savebar" role="status">
        <p>Bạn có thay đổi chưa lưu</p>
        <button type="button" class="btn ghost" id="resetBtn">Hoàn tác</button>
        <button type="submit" class="btn primary" id="saveBtn">Lưu thay đổi</button>
    </div>
    </form>
</div>
<div class="toast" id="toast" role="status"></div>
<script>__JS__</script>
</body>
</html>
"""

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Đăng nhập</title><style>__CSS__</style></head>
<body><div class="login"><div class="card">
<h1>🔐 Đăng nhập dashboard</h1>
<p class="sub">Nhập mật khẩu (biến DASHBOARD_PASSWORD).</p>
__ERR__
<form method="POST" action="/login">
<input type="password" name="password" placeholder="Mật khẩu" autofocus autocomplete="current-password">
<button class="btn primary" type="submit">Đăng nhập</button>
</form></div></div></body></html>
"""


def render_login(error=False):
    err = '<p class="err">Sai mật khẩu, thử lại.</p>' if error else ""
    css = PAGE_CSS.replace("__ACCENT__", ACCENTS[0][0]).replace("__ACCENT_INK__", ACCENTS[0][2])
    return LOGIN_PAGE.replace("__CSS__", css).replace("__ERR__", err)


def render_page(config):
    esc = lambda s: html.escape(str(s), quote=True)

    msgs = config.get("status_messages") or DEFAULT_CONFIG["status_messages"]
    msg_rows = "".join(
        '<div class="msg-row"><input type="text" maxlength="120" value="%s" placeholder="Nhập câu status...">'
        '<button type="button" class="icon-btn" title="Xóa dòng" aria-label="Xóa dòng">✕</button></div>' % esc(m)
        for m in msgs
    )

    mods_html = ""
    for key, icon, name, filename, desc in MODULES:
        checked = "checked" if config.get(key, True) else ""
        mods_html += (
            '<div class="mod"><input type="checkbox" id="%(k)s" name="%(k)s" %(c)s>'
            '<label for="%(k)s"><span class="ico">%(i)s</span>'
            '<span class="txt"><b>%(n)s</b><small>%(d)s</small></span>'
            '<span class="tg"></span></label></div>'
        ) % {"k": key, "c": checked, "i": icon, "n": esc(name), "d": esc(desc)}

    mode = config.get("status_mode", "online")
    if config.get("force_stream", False):
        mode = "stream"
    try:
        delay = max(1, min(60, int(config.get("status_delay", 5))))
    except (TypeError, ValueError):
        delay = 5

    activity = config.get("activity_type", "playing")
    if activity not in ACTIVITY_LABELS:
        activity = "playing"
    activity_html = "".join(
        '<label class="mode"><input type="radio" name="activity_type" value="%(k)s" %(c)s>'
        '<span><span class="mode-ico">%(i)s</span><div>%(n)s<small>%(d)s</small></div></span></label>'
        % {"k": k, "i": icon, "n": esc(name), "d": esc(desc), "c": "checked" if k == activity else ""}
        for k, icon, name, desc in ACTIVITY_TYPES
    )

    platform = config.get("platform", "ios")
    if platform not in PLATFORM_KEYS:
        platform = "ios"
    platform_html = "".join(
        '<label class="mode"><input type="radio" name="platform" value="%(k)s" %(c)s>'
        '<span><span class="mode-ico">%(i)s</span><div>%(n)s<small>%(d)s</small></div></span></label>'
        % {"k": k, "i": icon, "n": esc(name), "d": esc(desc), "c": "checked" if k == platform else ""}
        for k, icon, name, desc in PLATFORM_OPTIONS
    )

    accent = config.get("accent")
    accent_row = next((a for a in ACCENTS if a[0] == accent), ACCENTS[0])
    accent_hex, _n, accent_ink = accent_row
    swatches_html = "".join(
        '<div class="swatch"><input type="radio" id="ac_%(i)d" name="accent" value="%(hex)s" %(c)s>'
        '<label for="ac_%(i)d"><span class="dot" style="background:%(hex)s"></span><small>%(n)s</small></label></div>'
        % {"i": i, "hex": hexv, "n": esc(name), "c": "checked" if hexv == accent_row[0] else ""}
        for i, (hexv, name, _ink) in enumerate(ACCENTS)
    )

    css = PAGE_CSS.replace("__ACCENT__", accent_hex).replace("__ACCENT_INK__", accent_ink)
    js = PAGE_JS.replace("__CHAT_ON__", "true" if CHAT_ENABLED else "false").replace("__DEFAULT_BG__", DEFAULT_BG)

    site_title = str(config.get("site_title") or DEFAULT_CONFIG["site_title"])[:40]
    bg_url = str(config.get("bg_url") or "")
    if not BG_URL_RE.match(bg_url):
        bg_url = ""
    try:
        bg_dim = max(0, min(90, int(config.get("bg_dim", 60))))
    except (TypeError, ValueError):
        bg_dim = 60
    theme_css = ":root{--dim:%s;%s}" % (bg_dim / 100, ('--bg-img:url("%s");' % bg_url) if bg_url else "")
    stream_url = normalize_stream_url(config.get("stream_url"))
    if not STREAM_URL_RE.match(stream_url):
        stream_url = DEFAULT_CONFIG["stream_url"]

    page = PAGE_TEMPLATE
    page = page.replace("__CHAT_BLOCK__", CHAT_BLOCK if CHAT_ENABLED else CHAT_LOCKED)
    page = page.replace("__LOGOUT__", '<a class="logout" href="/logout">Đăng xuất</a>' if PASSWORD else "")
    page = page.replace("__SWATCHES__", swatches_html)
    page = page.replace("__PREFIX__", esc(config.get("prefix", "!")))
    page = page.replace("__DELAY__", str(delay))
    for key, val in (("ONLINE", "online"), ("IDLE", "idle"), ("DND", "dnd"), ("INVISIBLE", "invisible"), ("STREAM", "stream")):
        page = page.replace("__M_%s__" % key, "checked" if mode == val else "")
    page = page.replace("__ACTIVITY_TYPES__", activity_html)
    page = page.replace("__PLATFORMS__", platform_html)
    page = page.replace("__MSG_ROWS__", msg_rows)
    page = page.replace("__MODULES__", mods_html)
    page = page.replace("__INVITE_URL__", esc(INVITE_URL))
    page = page.replace("__THEME_CSS__", theme_css)
    page = page.replace("__BODY_CLASS__", "has-bg" if bg_url else "")
    page = page.replace("__SITE_TITLE__", esc(site_title))
    page = page.replace("__BG_URL__", esc(bg_url))
    page = page.replace("__STREAM_URL__", esc(stream_url))
    page = page.replace("__BG_DIM__", str(bg_dim))
    # CSS/JS thay cuối cùng để không bị replace nhầm bên trong
    page = page.replace("__CSS__", css).replace("__JS__", js)
    return page


# ----------------------------------------------------------------------
# Web server
# ----------------------------------------------------------------------
API_GET = ("/stats", "/activity", "/botctl", "/uptime")


class DashboardHandler(BaseHTTPRequestHandler):

    def _authed(self):
        if not PASSWORD:
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ks_session" and hmac.compare_digest(v, SESSION_TOKEN):
                return True
        return False

    def _send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def _redirect(self, where, extra=None):
        self.send_response(303)
        self.send_header("Location", where)
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/ping", "/healthz"):
            self._send(200, "OK", "text/plain; charset=utf-8")
            return
        if path == "/logout":
            self._redirect("/", [("Set-Cookie", "ks_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")])
            return
        if not self._authed():
            if path in API_GET:
                self._json(401, {"ok": False, "error": "Chưa đăng nhập"})
            else:
                self._send(401, render_login())
            return
        if path in API_GET:
            payload = {"/stats": read_stats, "/activity": read_activity, "/botctl": bot_status, "/uptime": read_uptime}[path]()
            self._json(200, payload)
            return
        self._send(200, render_page(read_config()))

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0

        if path == "/login":
            if length > 4096:
                self._send(413, "")
                return
            parsed = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            pw = parsed.get("password", [""])[0]
            if PASSWORD and hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                self._redirect("/", [("Set-Cookie", "ks_session=%s; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax%s" % (SESSION_TOKEN, secure))])
            else:
                time.sleep(1.5)
                self._send(401, render_login(error=True))
            return

        if not self._authed():
            self._json(401, {"ok": False, "error": "Chưa đăng nhập"})
            return

        if path == "/api/upload":
            if not CHAT_ENABLED:
                self._json(403, {"ok": False, "error": "Chat đang khóa"})
                return
            if length <= 0 or length > MAX_UPLOAD:
                self._json(413, {"ok": False, "error": "File rỗng hoặc quá 10MB"})
                return
            name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
            name = re.sub(r"[^\w.\- ]", "_", os.path.basename(name))[:80] or "file"
            data = self.rfile.read(length)
            uid = uuid.uuid4().hex
            cleanup_uploads()
            with open(os.path.join(UPLOAD_DIR, "%s__%s" % (uid, name)), "wb") as f:
                f.write(data)
            self._json(200, {"ok": True, "id": uid, "name": name, "size": len(data)})
            return

        if length > 100_000:
            self._send(413, "")
            return
        raw = self.rfile.read(length).decode("utf-8", "replace")

        if path == "/api/rpc":
            if not CHAT_ENABLED:
                self._json(403, {"ok": False, "error": "Chat đang khóa: cần đặt DASHBOARD_PASSWORD"})
                return
            try:
                body = json.loads(raw)
                assert isinstance(body, dict)
            except Exception:
                self._json(400, {"ok": False, "error": "JSON không hợp lệ"})
                return
            payload, err = build_rpc(body)
            if err:
                self._json(400, {"ok": False, "error": err})
                return
            self._json(200, rpc_call(payload))
            return

        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)

        if path == "/botctl":
            action = parsed.get("action", [""])[0]
            if action == "start":
                start_bot()
            elif action == "stop":
                stop_bot()
            elif action == "restart":
                restart_bot()
            else:
                self._json(400, {"ok": False, "error": "action phải là start, stop hoặc restart"})
                return
            self._json(200, dict(bot_status(), ok=True))
            return

        if path != "/save":
            self._send(404, "")
            return

        raw_status = parsed.get("status_messages", [""])[0].replace("\r", "")
        status_list = [line.strip() for line in raw_status.split("\n") if line.strip()]
        try:
            delay = max(1, min(3600, int(parsed.get("status_delay", [5])[0])))
        except (TypeError, ValueError):
            delay = 5

        mode = parsed.get("status_mode", ["online"])[0]
        force_stream = (mode == "stream")
        if mode not in ("online", "idle", "dnd", "invisible"):
            mode = "online"

        activity_type = parsed.get("activity_type", ["playing"])[0]
        if activity_type not in ACTIVITY_LABELS:
            activity_type = "playing"
        platform = parsed.get("platform", ["ios"])[0]
        if platform not in PLATFORM_KEYS:
            platform = "ios"
        prefix = parsed.get("prefix", ["!"])[0].strip() or "!"
        accent = parsed.get("accent", [ACCENT_VALUES[0]])[0]
        if accent not in ACCENT_VALUES:
            accent = ACCENT_VALUES[0]
        site_title = parsed.get("site_title", [""])[0].strip()[:40] or DEFAULT_CONFIG["site_title"]
        bg_url = parsed.get("bg_url", [""])[0].strip()
        if not BG_URL_RE.match(bg_url):
            bg_url = ""
        stream_url = normalize_stream_url(parsed.get("stream_url", [""])[0])
        if not STREAM_URL_RE.match(stream_url):
            stream_url = DEFAULT_CONFIG["stream_url"]
        try:
            bg_dim = max(0, min(90, int(parsed.get("bg_dim", [60])[0])))
        except (TypeError, ValueError):
            bg_dim = 60

        new_config = {
            "site_title": site_title, "bg_url": bg_url, "bg_v2": True, "bg_dim": bg_dim,
            "stream_url": stream_url, "force_stream": force_stream, "platform": platform,
            "prefix": prefix[:5], "accent": accent, "status_mode": mode,
            "activity_type": activity_type, "status_delay": delay,
            "status_messages": status_list if status_list else list(DEFAULT_CONFIG["status_messages"]),
        }
        for key, *_ in MODULES:
            new_config[key] = key in parsed

        merged = read_config()
        old_platform = merged.get("platform", "ios")
        merged.update(new_config)
        write_config(merged)

        restarted = False
        if platform != old_platform and bot_status()["running"]:
            restart_bot()
            restarted = True

        if self.headers.get("X-Requested-With") == "fetch":
            self._json(200, {"ok": True, "restarted": restarted})
        else:
            self._redirect("/")

    def log_message(self, format, *args):
        quiet = ("/ping", "/healthz", "/stats", "/activity", "/botctl", "/uptime", "/api/rpc")
        if self.path.split("?")[0] not in quiet:
            super().log_message(format, *args)


def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)

    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    print("=" * 60)
    if domain:
        print(f"🌐 Dashboard: https://{domain}")
    elif render_url:
        print(f"🌐 Dashboard: {render_url}")
    else:
        print(f"🌐 Dashboard đang chạy ở cổng {port}")
    if not PASSWORD:
        print("⚠️  Chưa đặt DASHBOARD_PASSWORD -> tab Chat bị khóa, dashboard không có đăng nhập.")
    print("=" * 60, flush=True)
    server.serve_forever()


def run_bot_forever():
    MIN_HEALTHY_RUN_SECONDS = 30
    BACKOFF_START = 5
    BACKOFF_MAX = 300
    backoff = BACKOFF_START
    rate_limit_hits = 0

    while True:
        with BOT_LOCK:
            should_run = BOT_STATE["should_run"]
        if not should_run:
            time.sleep(2)
            continue

        script = "run_bot.py" if os.path.exists("run_bot.py") else "bot.py"
        process = subprocess.Popen([sys.executable, script])
        with BOT_LOCK:
            BOT_STATE["process"] = process
            BOT_STATE["started_at"] = time.time()
        start_time = time.time()
        process.wait()
        ran_seconds = time.time() - start_time
        print(f"[BotRunner] {script} đã thoát (mã {process.returncode}) sau {ran_seconds:.1f}s", flush=True)
        with BOT_LOCK:
            BOT_STATE["process"] = None

        if BOT_STATE["stopped_by_user"]:
            backoff = BACKOFF_START
            time.sleep(2)
            continue

        if process.returncode == EXIT_RATE_LIMITED:
            wait_s = RATE_LIMIT_WAITS[min(rate_limit_hits, len(RATE_LIMIT_WAITS) - 1)]
            rate_limit_hits += 1
            until = time.time() + wait_s
            with BOT_LOCK:
                BOT_STATE["cooldown_until"] = until
            print(f"[BotRunner] Discord đang chặn IP của host. Chờ {wait_s // 60} phút rồi mới thử lại (lần chặn thứ {rate_limit_hits}).")
            while time.time() < until:
                with BOT_LOCK:
                    if BOT_STATE["cooldown_until"] is None or not BOT_STATE["should_run"]:
                        break
                time.sleep(2)
            with BOT_LOCK:
                BOT_STATE["cooldown_until"] = None
            continue

        if ran_seconds >= MIN_HEALTHY_RUN_SECONDS:
            backoff = BACKOFF_START
            rate_limit_hits = 0
        else:
            print(f"[BotRunner] bot.py vừa thoát chỉ sau {ran_seconds:.1f}s, chờ {backoff}s rồi mới thử lại để tránh bị Discord khóa IP.")
            time.sleep(backoff)
            backoff = min(backoff * 3, BACKOFF_MAX)
            continue

        time.sleep(2)


if __name__ == "__main__":
    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

    try:
        signal.signal(signal.SIGTERM, _forward_sigterm)
    except (ValueError, OSError):
        pass

    run_bot_forever()
