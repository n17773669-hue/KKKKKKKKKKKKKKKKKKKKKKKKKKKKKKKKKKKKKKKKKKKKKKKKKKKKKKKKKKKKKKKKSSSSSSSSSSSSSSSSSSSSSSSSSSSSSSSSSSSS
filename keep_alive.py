from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import urllib.parse
import threading
import subprocess
import datetime
import signal
import html
import time
import json
import uuid
import sys
import re
import os
import cgi

CONFIG_FILE = "config.json"
CONFIG_LOCK = threading.Lock()

# bot.py ghi file này định kỳ (xem hướng dẫn cuối file) để dashboard hiển thị
# số liệu thật thay vì số mẫu. Quá STATS_MAX_AGE giây không cập nhật thì coi
# như bot không gửi dữ liệu và hiển thị "—".
STATS_FILE = "stats.json"
STATS_MAX_AGE = 120

CHAT_HISTORY_FILE = "chat_history.json"
WEB_COMMANDS_FILE = "web_commands.json"

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

def read_chat_history(channel_id):
    if not os.path.exists(CHAT_HISTORY_FILE):
        return []
    try:
        with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get(str(channel_id), [])
    except Exception:
        pass
    return []

def add_web_command(cmd):
    cmds = []
    if os.path.exists(WEB_COMMANDS_FILE):
        try:
            with open(WEB_COMMANDS_FILE, "r", encoding="utf-8") as f:
                cmds = json.load(f)
            if not isinstance(cmds, list):
                cmds = []
        except Exception:
            cmds = []
    cmds.append(cmd)
    tmp = WEB_COMMANDS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cmds, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WEB_COMMANDS_FILE)

# bot.py (tùy chọn) ghi mảng JSON vào đây mỗi khi có lệnh được dùng, ví dụ:
# [{"time": 1729999999, "user": "tên#0001", "action": "!help"}, ...]
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


# Thời gian online của bot: discord_storage.py cộng dồn vào uptime.json mỗi ~10 giây
# và backup lên kênh #ksbot-uptime, nên số này không mất khi host deploy lại.
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
        # file được ghi mỗi ~10s -> cộng thêm phần chênh để số chạy mượt
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


# Hàng chờ tin nhắn: dashboard ghi job vào đây, bot.py đọc và gửi qua Discord
# rồi tự xóa/đánh dấu xong (xem hướng dẫn cuối file).
OUTBOX_FILE = "outbox.json"
OUTBOX_LOCK = threading.Lock()
OUTBOX_MAX = 30


def read_outbox():
    if not os.path.exists(OUTBOX_FILE):
        return []
    try:
        with OUTBOX_LOCK:
            with open(OUTBOX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def queue_message(channel_id, content):
    jobs = read_outbox()
    job = {
        "id": uuid.uuid4().hex[:8],
        "channel_id": channel_id,
        "content": content,
        "status": "pending",
        "created_at": time.time(),
    }
    jobs.append(job)
    jobs = jobs[-OUTBOX_MAX:]
    tmp = OUTBOX_FILE + ".tmp"
    with OUTBOX_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
        os.replace(tmp, OUTBOX_FILE)
    return job

# Bộ màu chủ đạo cho giao diện: (mã, tên hiển thị, màu chữ trên nền màu đó)
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
    # Đã cập nhật mặc định hình nền chuẩn theo ý bạn
    "bg_url": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQI2Snditi8l1N6o2Js0AKX67zA3EIyAwLYR2RK2yr93A&s=10",
    "bg_dim": 50,
}

# Link hình nền chỉ nhận https và không chứa ký tự có thể phá CSS/HTML
BG_URL_RE = re.compile(r"^https://[^\s\"'()<>\\]{4,480}$")

# Nút "Xem" của trạng thái Streaming chỉ hiện khi link là Twitch hoặc YouTube
STREAM_URL_RE = re.compile(
    r"^https://(www\.)?(twitch\.tv/[A-Za-z0-9_]{3,40}|youtube\.com/watch\?v=[A-Za-z0-9_-]{6,20}|youtu\.be/[A-Za-z0-9_-]{6,20})$"
)

def normalize_stream_url(url):
    """Discord chỉ hiện nút Xem + chấm tím với link dạng youtube.com/watch?v=... hoặc twitch.tv/...
    Đổi youtu.be/ID (kể cả có ?si=...) thành dạng youtube.com/watch?v=ID."""
    url = str(url or "").strip()
    m = re.match(r"^https://(?:www\.|m\.)?youtu\.be/([A-Za-z0-9_-]{6,20})", url)
    if not m:
        m = re.match(r"^https://(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})", url)
    return "https://www.youtube.com/watch?v=" + m.group(1) if m else url

# (key, icon, tên, file, mô tả ngắn)
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

# Loại hoạt động hiển thị trước câu status, đúng như 5 loại Discord thật sự có
ACTIVITY_TYPES = [
    ("playing",    "🎮", "Đang chơi",   "Hiện là: Đang chơi <câu status>"),
    ("listening",  "🎧", "Đang nghe",   "Hiện là: Đang nghe <câu status>"),
    ("watching",   "📺", "Đang xem",    "Hiện là: Đang xem <câu status>"),
    ("competing",  "🏆", "Đang thi đấu", "Hiện là: Đang thi đấu <câu status>"),
]
ACTIVITY_LABELS = {k: name for k, _i, name, _d in ACTIVITY_TYPES}

# Thiết bị hiển thị cạnh tên bot (status.py đọc key "platform" khi bot đăng nhập)
PLATFORM_OPTIONS = [
    ("vr",      "🥽", "VR",       "Discord VR / Quest"),
    ("android", "🤖", "Android",  "Biểu tượng điện thoại"),
    ("ios",     "📱", "iOS",      "Biểu tượng điện thoại"),
    ("default", "💻", "Mặc định", "Như bot bình thường"),
]
PLATFORM_KEYS = [p[0] for p in PLATFORM_OPTIONS]


INVITE_URL = "https://discord.com/oauth2/authorize?client_id=1536638391397908601&scope=applications.commands%20bot&permissions=8"

# Điều khiển tiến trình bot.py thật, không phải giả lập
BOT_LOCK = threading.Lock()
BOT_STATE = {"process": None, "should_run": True, "started_at": None, "stopped_by_user": False, "cooldown_until": None}

# bot.py thoát với mã này khi Discord/Cloudflare chặn IP (429 / lỗi 1015)
EXIT_RATE_LIMITED = 42
# Thời gian chờ (giây) giữa các lần thử lại khi bị chặn IP: 15 phút, 30 phút, rồi 60 phút
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
        # Bấm "Bật bot" khi đang chờ mở khóa IP = bỏ qua thời gian chờ, thử lại ngay
        BOT_STATE["cooldown_until"] = None


def stop_bot():
    with BOT_LOCK:
        BOT_STATE["should_run"] = False
        BOT_STATE["stopped_by_user"] = True
        p = BOT_STATE["process"]
    if p is not None and p.poll() is None:
        p.terminate()


def restart_bot():
    """Tắt tiến trình bot hiện tại, run_bot_forever sẽ tự bật lại."""
    with BOT_LOCK:
        BOT_STATE["should_run"] = True
        BOT_STATE["stopped_by_user"] = False
        BOT_STATE["cooldown_until"] = None
        p = BOT_STATE["process"]
    if p is not None and p.poll() is None:
        p.terminate()


def _forward_sigterm(signum, frame):
    """Host (Render/Railway) tắt dashboard -> chuyển SIGTERM xuống bot.py để
    discord_storage kịp lưu uptime/coin/trạng thái lần cuối lên kênh Discord."""
    BOT_STATE["should_run"] = False
    BOT_STATE["stopped_by_user"] = True
    p = BOT_STATE.get("process")
    if p is not None and p.poll() is None:
        try:
            p.terminate()          # bot.py -> _graceful_exit -> backup_all
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
    except Exception:
        pass
    return cfg


def write_config(data):
    # Ghi ra file tạm rồi đổi tên, tránh hỏng config nếu bị ngắt giữa chừng
    tmp = CONFIG_FILE + ".tmp"
    with CONFIG_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, CONFIG_FILE)


# ----------------------------------------------------------------------
# Giao diện
# ----------------------------------------------------------------------

PAGE_CSS = """
:root {
    --bg: #0e1118;
    --panel: #151923;
    --panel-2: #1b2030;
    --line: #262c3d;
    --text: #e9ecf5;
    --muted: #8b93a8;
    --accent: __ACCENT__;
    --accent-ink: __ACCENT_INK__;
    --ok: #3fc380;
    --warn: #f5b942;
    --bad: #ef5b6b;
    --radius: 14px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { color-scheme: dark; }
body {
    font-family: 'Be Vietnam Pro', system-ui, -apple-system, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
}
button, input, select, textarea { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.wrap { max-width: 1200px; margin: 0 auto; padding: 28px 18px 120px; }

.top { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 26px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
    width: 40px; height: 40px; border-radius: 12px; background: var(--accent);
    color: var(--accent-ink); display: grid; place-items: center; font-size: 20px; font-weight: 700;
}
.brand h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.brand p { font-size: 13px; color: var(--muted); }
.top-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.live { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); background: var(--panel); border: 1px solid var(--line); padding: 7px 12px; border-radius: 999px; }
.live i { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px rgba(63,195,128,.18); }
.invite-btn {
    display: flex; align-items: center; gap: 8px; background: #5865f2; color: #fff; text-decoration: none;
    font-size: 13px; font-weight: 600; padding: 9px 16px; border-radius: 999px; transition: background .15s;
}
.invite-btn:hover { background: #4752c4; }
.invite-btn svg { width: 16px; height: 16px; flex: none; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 22px; align-items: start; }
@media (max-width: 880px) { .layout { grid-template-columns: 1fr; } .side { order: -1; position: static !important; } }
.side { position: sticky; top: 20px; }

.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 22px; margin-bottom: 18px; }
.card h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.card .sub { font-size: 13px; color: var(--muted); margin-bottom: 18px; }

.field { margin-bottom: 18px; }
.field:last-child { margin-bottom: 0; }
.field > label, .label { display: block; font-size: 13px; font-weight: 500; color: var(--muted); margin-bottom: 8px; }
.hint { font-size: 12px; color: var(--muted); margin-top: 6px; }

input[type="text"], input[type="number"] {
    width: 100%; background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 10px; padding: 11px 13px; font-size: 14px; outline: none;
    transition: border-color .15s;
}
input[type="text"]:focus, input[type="number"]:focus { border-color: var(--accent); }

.prefix-row { display: flex; align-items: center; gap: 14px; }
.prefix-row input { width: 90px; text-align: center; font-weight: 600; font-size: 16px; }
.prefix-demo { font-size: 13px; color: var(--muted); }
.prefix-demo code { background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; padding: 3px 8px; color: var(--text); font-family: ui-monospace, Menlo, Consolas, monospace; }

/* Màu chủ đạo */
.swatches { display: flex; flex-wrap: wrap; gap: 10px; }
.swatch input { position: absolute; opacity: 0; pointer-events: none; }
.swatch label {
    display: flex; flex-direction: column; align-items: center; gap: 6px; cursor: pointer; width: 66px;
}
.swatch .dot {
    width: 36px; height: 36px; border-radius: 50%; border: 3px solid transparent; transition: border-color .15s, transform .15s;
}
.swatch input:checked + label .dot { border-color: #fff; transform: scale(1.08); }
.swatch input:focus-visible + label .dot { outline: 2px solid #fff; outline-offset: 2px; }
.swatch small { font-size: 11px; color: var(--muted); text-align: center; }

/* Chọn trạng thái */
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
@media (max-width: 520px) { .modes { grid-template-columns: 1fr 1fr; } }
.modes.platforms { grid-template-columns: repeat(4, 1fr); }
.modes.platforms .mode span { flex-direction: column; text-align: center; gap: 6px; padding: 12px 8px; }
@media (max-width: 720px) { .modes.platforms { grid-template-columns: 1fr 1fr; } }
.modes.statuses { grid-template-columns: repeat(5, 1fr); }
.modes.statuses .mode span { flex-direction: column; text-align: center; gap: 6px; padding: 12px 8px; }
@media (max-width: 720px) { .modes.statuses { grid-template-columns: 1fr 1fr; } }
.mode input { position: absolute; opacity: 0; pointer-events: none; }
.mode span {
    display: flex; align-items: center; gap: 10px; padding: 12px 14px;
    background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px;
    cursor: pointer; font-size: 14px; transition: border-color .15s, background .15s;
}
.mode small { display: block; font-size: 12px; color: var(--muted); }
.mode .dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
.mode .mode-ico { font-size: 18px; line-height: 1; flex: none; }
.mode input:checked + span { border-color: var(--accent); background: rgba(139,155,255,.1); }
.mode input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Thanh trượt thời gian */
.delay-row { display: flex; align-items: center; gap: 14px; }
.delay-row input[type=range] { flex: 1; accent-color: var(--accent); }
.delay-val { min-width: 64px; text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }

/* Danh sách status */
.msgs { display: flex; flex-direction: column; gap: 8px; }
.msg-row { display: flex; gap: 8px; }
.msg-row input { flex: 1; }
.icon-btn {
    width: 42px; flex: none; background: var(--panel-2); border: 1px solid var(--line);
    border-radius: 10px; cursor: pointer; color: var(--muted); font-size: 16px;
    transition: color .15s, border-color .15s;
}
.icon-btn:hover { color: var(--bad); border-color: var(--bad); }
.add-btn {
    margin-top: 10px; width: 100%; background: transparent; border: 1px dashed var(--line);
    border-radius: 10px; padding: 10px; color: var(--muted); cursor: pointer; font-size: 14px;
    transition: color .15s, border-color .15s;
}
.add-btn:hover { color: var(--accent); border-color: var(--accent); }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; align-items: center; }
.chips span { font-size: 12px; color: var(--muted); margin-right: 2px; }
.chip {
    background: var(--panel-2); border: 1px solid var(--line); border-radius: 999px;
    padding: 4px 10px; font-size: 12px; cursor: pointer; font-family: ui-monospace, Menlo, Consolas, monospace;
}
.chip:hover { border-color: var(--accent); color: var(--accent); }

/* Công tắc tính năng */
.card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }
.card-head .sub { margin-bottom: 0; }
.bulk { display: flex; gap: 6px; }
.bulk button {
    background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px;
    padding: 6px 11px; font-size: 12px; cursor: pointer; color: var(--muted);
}
.bulk button:hover { color: var(--text); border-color: var(--accent); }
.mods { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
@media (max-width: 600px) { .mods { grid-template-columns: 1fr; } }
.mod { position: relative; }
.mod input { position: absolute; opacity: 0; pointer-events: none; }
.mod label {
    display: flex; gap: 12px; align-items: center; padding: 13px 14px; cursor: pointer;
    background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px;
    transition: border-color .15s, opacity .15s; height: 100%;
}
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
.mod input:focus-visible + label { outline: 2px solid var(--accent); outline-offset: 2px; }

/* Xem trước */
.preview h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.profile {
    background: #11141d; border: 1px solid var(--line); border-radius: 16px; overflow: hidden; margin-top: 14px;
}
.banner { height: 70px; background: linear-gradient(120deg, #2a3158, #3b2f5c); }
.profile-body { padding: 0 18px 18px; }
.avatar-wrap { position: relative; width: 72px; height: 72px; margin-top: -36px; }
.avatar {
    width: 72px; height: 72px; border-radius: 50%; background: var(--accent); color: var(--accent-ink);
    display: grid; place-items: center; font-size: 32px; border: 5px solid #11141d; overflow: hidden;
}
.avatar img, .brand-mark img { width: 100%; height: 100%; object-fit: cover; display: block; }
.brand-mark { overflow: hidden; }
.status-dot {
    position: absolute; right: 0; bottom: 2px; width: 22px; height: 22px; border-radius: 50%;
    border: 4px solid #11141d; background: var(--ok); transition: background .25s;
}
.pname { font-size: 18px; font-weight: 700; margin-top: 10px; display: flex; align-items: center; gap: 8px; }
.tag { font-size: 11px; font-weight: 600; background: #5865f2; color: #fff; padding: 2px 6px; border-radius: 5px; }
.pstate { font-size: 13px; color: var(--muted); margin-top: 2px; }
.bubble {
    margin-top: 14px; background: var(--panel-2); border-radius: 10px; padding: 11px 13px;
    font-size: 14px; min-height: 44px; display: flex; align-items: center;
}
.bubble span { animation: swap .35s ease; }
@keyframes swap { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.off-note { font-size: 12px; color: var(--warn); margin-top: 10px; display: none; }
.preview .hint { margin-top: 12px; }

.stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
.stat { background: var(--panel-2); border-radius: 10px; padding: 10px 12px; }
.stat b { display: block; font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat span { font-size: 11px; color: var(--muted); }
.stat.na b { color: var(--muted); }
.data-note { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); margin-top: 10px; }
.data-note i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.data-note.live i { background: var(--ok); }
.data-note.stale i { background: var(--warn); }

/* Thanh lưu */
.savebar {
    position: fixed; left: 50%; bottom: 20px; transform: translate(-50%, 140%);
    background: #20263a; border: 1px solid #343c58; border-radius: 14px; padding: 10px 10px 10px 18px;
    display: flex; align-items: center; gap: 16px; box-shadow: 0 12px 40px rgba(0,0,0,.55);
    transition: transform .25s ease; z-index: 20; max-width: calc(100% - 28px);
}
.savebar.show { transform: translate(-50%, 0); }
.savebar p { font-size: 14px; }
.btn {
    border: none; cursor: pointer; border-radius: 10px; padding: 10px 18px; font-size: 14px; font-weight: 600;
}
.btn.primary { background: var(--accent); color: var(--accent-ink); }
.btn.primary:disabled { opacity: .6; cursor: wait; }
.btn.ghost { background: transparent; color: var(--muted); }
.btn.ghost:hover { color: var(--text); }

.toast {
    position: fixed; top: 20px; right: 20px; background: var(--panel-2); border: 1px solid var(--line);
    border-left: 4px solid var(--ok); padding: 12px 16px; border-radius: 10px; font-size: 14px;
    opacity: 0; transform: translateY(-10px); pointer-events: none; transition: all .25s; z-index: 30;
}
.toast.show { opacity: 1; transform: none; }
.toast.err { border-left-color: var(--bad); }

/* Điều khiển bot */
.botctl { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.botctl-state { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.botctl-state i { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); flex: none; }
.botctl-state.on i { background: var(--ok); box-shadow: 0 0 0 3px rgba(63,195,128,.18); }
.botctl-state.off i { background: var(--bad); }
.botctl-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.btn.start { background: var(--ok); color: #06180f; }
.btn.stop { background: var(--bad); color: #2a0a0a; }
.btn.restart { background: var(--panel-2); border: 1px solid var(--line); color: var(--text); }
.btn.restart:hover:not(:disabled) { border-color: var(--accent); }
.btn.small { padding: 8px 14px; font-size: 13px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }

/* Danh sách / log */
.list-empty {
    text-align: center; padding: 28px 16px; color: var(--muted); font-size: 13px;
    background: var(--panel-2); border-radius: 12px; border: 1px dashed var(--line);
}
.list-empty b { display: block; color: var(--text); font-size: 14px; margin-bottom: 4px; }
.rows { display: flex; flex-direction: column; gap: 8px; }
.row-item {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    background: var(--panel-2); border-radius: 10px; padding: 10px 14px; font-size: 13px;
}
.row-item .main { display: flex; align-items: center; gap: 10px; min-width: 0; }
.row-item .main b { font-size: 14px; display: block; }
.row-item .main small { color: var(--muted); display: block; }
.row-badge { font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 999px; flex: none; }
.row-badge.pending { background: rgba(245,185,66,.15); color: var(--warn); }
.row-badge.sent { background: rgba(63,195,128,.15); color: var(--ok); }
.row-badge.error { background: rgba(239,91,107,.15); color: var(--bad); }

/* Discord Client 3 Cột (Giao diện chuẩn Discord để chat, xem lịch sử, thả icon) */
.discord-app {
    display: flex;
    height: 600px;
    background: #2b2d31;
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--line);
    margin-top: 10px;
}
.sidebar-servers {
    width: 72px;
    background: #1e1f22;
    padding: 12px 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    overflow-y: auto;
    border-right: 1px solid #111214;
}
.server-ico {
    width: 48px; height: 48px; border-radius: 50%; background: #313338;
    display: grid; place-items: center; font-weight: 700; cursor: pointer;
    transition: all .2s; position: relative; color: #dbdee1;
}
.server-ico:hover, .server-ico.active { border-radius: 16px; background: #5865f2; color: #fff; }
.server-ico img { width: 100%; height: 100%; border-radius: inherit; object-fit: cover; }

.sidebar-channels {
    width: 220px;
    background: #2b2d31;
    padding: 12px 8px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    overflow-y: auto;
    border-right: 1px solid #1f2023;
}
.chan-title { font-size: 11px; font-weight: 700; color: #949ba4; text-transform: uppercase; padding: 6px 8px; }
.chan-item {
    padding: 8px 10px; border-radius: 6px; color: #949ba4; cursor: pointer;
    font-size: 14px; display: flex; align-items: center; gap: 8px; transition: background .15s;
}
.chan-item:hover, .chan-item.active { background: #35373c; color: #f2f3f5; }
.chan-item.active { font-weight: 600; }

.chat-area {
    flex: 1;
    display: flex;
    flex-direction: column;
    background: #313338;
}
.chat-header {
    height: 48px; padding: 0 16px; border-bottom: 1px solid #1f2023;
    display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 15px; color: #f2f3f5;
}
.chat-messages {
    flex: 1; padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px;
}
.msg-bubble { display: flex; gap: 12px; position: relative; }
.msg-avatar { width: 40px; height: 40px; border-radius: 50%; background: #5865f2; flex: none; overflow: hidden; }
.msg-avatar img { width: 100%; height: 100%; object-fit: cover; }
.msg-main { flex: 1; min-width: 0; }
.msg-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.msg-user { font-weight: 600; font-size: 14px; color: #f2f3f5; }
.msg-time { font-size: 11px; color: #949ba4; }
.msg-text { font-size: 14px; color: #dbdee1; word-break: break-word; }
.msg-img { max-width: 280px; max-height: 180px; border-radius: 8px; margin-top: 6px; display: block; }
.msg-reactions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.react-tag { background: #2b2d31; border: 1px solid #383a40; border-radius: 6px; padding: 2px 6px; font-size: 12px; display: flex; align-items: center; gap: 4px; }
.msg-actions { display: flex; gap: 6px; margin-top: 6px; }
.msg-act-btn { background: #2b2d31; border: 1px solid #383a40; color: #b5bac1; padding: 3px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; }
.msg-act-btn:hover { background: #35373c; color: #fff; }

.chat-input-box { padding: 12px 16px; background: #313338; }
.reply-preview { font-size: 12px; color: #b5bac1; background: #2b2d31; padding: 6px 12px; border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center; }
.chat-input-wrap { display: flex; align-items: center; gap: 8px; background: #383a40; border-radius: 8px; padding: 8px 12px; }
.chat-input-wrap input[type="text"] { flex: 1; background: transparent; border: none; outline: none; color: #f2f3f5; font-size: 14px; }
.chat-file-btn { cursor: pointer; color: #b5bac1; font-size: 18px; display: flex; align-items: center; }
.chat-file-btn:hover { color: #fff; }
.send-btn-discord { background: #5865f2; color: #fff; border: none; padding: 6px 14px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; }
.send-btn-discord:hover { background: #4752c4; }

/* Thanh tab chuyển mục */
.tabs { display: flex; gap: 4px; overflow-x: auto; margin: 0 0 22px; border-bottom: 1px solid var(--line); scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab {
    background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px;
    padding: 11px 16px; font-size: 14px; font-weight: 500; color: var(--muted); cursor: pointer;
    white-space: nowrap; transition: color .15s, border-color .15s;
}
.tab:hover { color: var(--text); }
.tab.on { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }

/* Mỗi phần tử có data-show chỉ hiện ở các tab được liệt kê */
body[data-tab="overview"] [data-show]:not([data-show~="overview"]),
body[data-tab="config"] [data-show]:not([data-show~="config"]),
body[data-tab="features"] [data-show]:not([data-show~="features"]),
body[data-tab="send"] [data-show]:not([data-show~="send"]),
body[data-tab="servers"] [data-show]:not([data-show~="servers"]),
body[data-tab="theme"] [data-show]:not([data-show~="theme"]) { display: none; }
body[data-tab="features"] .layout,
body[data-tab="send"] .layout,
body[data-tab="servers"] .layout,
body[data-tab="theme"] .layout { grid-template-columns: minmax(0, 1fr); }

/* Hình nền tùy chỉnh: lớp phủ tối + thẻ kính mờ để chữ vẫn dễ đọc */
body.has-bg {
    background-image: linear-gradient(rgba(14,17,24,var(--dim)), rgba(14,17,24,var(--dim))), var(--bg-img);
    background-size: cover; background-position: center; background-attachment: fixed;
}
body.has-bg .card { background: rgba(21,25,35,.78); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px); }
.theme-prev { height: 90px; border-radius: 12px; border: 1px solid var(--line); margin-top: 10px; background: var(--panel-2) center / cover no-repeat; }

/* Danh sách server chia 2 cột + nút chuyển trang */
.srv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 600px) { .srv-grid { grid-template-columns: 1fr; } }
.srv { display: flex; align-items: center; gap: 12px; background: var(--panel-2); border-radius: 10px; padding: 10px 12px; min-width: 0; }
.srv-ico {
    width: 40px; height: 40px; border-radius: 50%; background: var(--line); display: grid; place-items: center;
    font-weight: 700; overflow: hidden; flex: none;
}
.srv-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.srv-txt { min-width: 0; }
.srv-txt b { display: block; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srv-txt small { color: var(--muted); font-size: 12px; }
.pager { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 14px; font-size: 13px; color: var(--muted); }
.pg { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 7px 14px; cursor: pointer; font-size: 13px; }
.pg:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.pg:disabled { opacity: .4; cursor: not-allowed; }
.err-reason { color: var(--bad) !important; margin-top: 2px; }

@media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
}
"""

PAGE_JS = """
(function () {
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
    var outboxRows = document.getElementById('outboxRows');
    var botState = document.getElementById('botState');
    var btnStart = document.getElementById('btnStart');
    var btnStop = document.getElementById('btnStop');
    var btnRestart = document.getElementById('btnRestart');
    
    // Biến cho Discord Client 3 cột
    var serverList = document.getElementById('serverList');
    var chanList = document.getElementById('chanList');
    var chatMsgs = document.getElementById('chatMsgs');
    var currentChanTitle = document.getElementById('currentChanTitle');
    var webMsgInput = document.getElementById('webMsgInput');
    var fileInput = document.getElementById('fileInput');
    var webSendBtn = document.getElementById('webSendBtn');
    var replyBar = document.getElementById('replyBar');
    var replyText = document.getElementById('replyText');
    var cancelReply = document.getElementById('cancelReply');

    var serversData = [];
    var currentGuild = null;
    var currentChan = null;
    var replyToId = null;

    var siteTitle = document.getElementById('siteTitle');
    var bgUrl = document.getElementById('bgUrl');
    var bgDim = document.getElementById('bgDim');
    var bgDimVal = document.getElementById('bgDimVal');
    var bgPrev = document.getElementById('bgPrev');

    function renderServers(list) {
        serversData = list || [];
        if (!serverList) return;
        serverList.innerHTML = '';
        
        var dmBtn = document.createElement('div');
        dmBtn.className = 'server-ico' + (currentGuild === 'DM' ? ' active' : '');
        dmBtn.textContent = '💬';
        dmBtn.title = 'Direct Messages';
        dmBtn.onclick = function() {
            currentGuild = 'DM';
            currentChan = null;
            renderServers(serversData);
            renderChannels([]);
        };
        serverList.appendChild(dmBtn);

        serversData.forEach(function(g) {
            var el = document.createElement('div');
            el.className = 'server-ico' + (String(g.id) === String(currentGuild) ? ' active' : '');
            if (g.icon) {
                el.innerHTML = '<img src="' + g.icon + '" alt="">';
            } else {
                el.textContent = (g.name || '?').charAt(0).toUpperCase();
            }
            el.title = g.name;
            el.onclick = function() {
                currentGuild = g.id;
                currentChan = (g.channels && g.channels.length) ? g.channels[0].id : null;
                renderServers(serversData);
                renderChannels(g.channels || []);
            };
            serverList.appendChild(el);
        });

        if (currentGuild == null && serversData.length > 0) {
            currentGuild = serversData[0].id;
            currentChan = (serversData[0].channels && serversData[0].channels.length) ? serversData[0].channels[0].id : null;
            renderServers(serversData);
            renderChannels(serversData[0].channels || []);
        }
    }

    function renderChannels(channels) {
        if (!chanList) return;
        chanList.innerHTML = '';
        if (currentGuild === 'DM') {
            chanList.innerHTML = '<div class="chan-title">Nhập User ID để DM</div>';
            var dmInput = document.createElement('input');
            dmInput.type = 'text';
            dmInput.placeholder = 'Dán User ID vào đây...';
            dmInput.style.cssText = 'width:100%;padding:8px;background:#1e1f22;border:1px solid #383a40;border-radius:6px;color:#fff;font-size:13px;';
            dmInput.onchange = function() {
                currentChan = dmInput.value.trim();
                currentChanTitle.textContent = '@ User (' + currentChan + ')';
                fetchChatHistory();
            };
            chanList.appendChild(dmInput);
            return;
        }

        chanList.innerHTML = '<div class="chan-title">Kênh Văn Bản</div>';
        channels.forEach(function(c) {
            var el = document.createElement('div');
            el.className = 'chan-item' + (String(c.id) === String(currentChan) ? ' active' : '');
            el.innerHTML = '<span>#</span> ' + c.name;
            el.onclick = function() {
                currentChan = c.id;
                currentChanTitle.textContent = '# ' + c.name;
                renderChannels(channels);
                fetchChatHistory();
            };
            chanList.appendChild(el);
        });

        if (currentChan) fetchChatHistory();
    }

    function fetchChatHistory() {
        if (!currentChan || !chatMsgs) return;
        fetch('/api/chat/' + currentChan, { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(msgs) { renderMessages(msgs); })
            .catch(function(){});
    }

    function renderMessages(list) {
        if (!chatMsgs) return;
        chatMsgs.innerHTML = '';
        if (!list || !list.length) {
            chatMsgs.innerHTML = '<div style="color:#949ba4;text-align:center;margin-top:40px;">Chưa có tin nhắn nào trong cache kênh này.</div>';
            return;
        }

        list.forEach(function(m) {
            var bubble = document.createElement('div');
            bubble.className = 'msg-bubble';
            
            var avt = m.avatar ? '<img src="' + m.avatar + '">' : '🤖';
            var imgs = (m.attachments || []).map(function(u){ return '<img src="' + u + '" class="msg-img">'; }).join('');
            var reactTags = (m.reactions || []).map(function(r){ return '<span class="react-tag">' + r.emoji + ' ' + r.count + '</span>'; }).join('');

            bubble.innerHTML = 
                '<div class="msg-avatar">' + avt + '</div>' +
                '<div class="msg-main">' +
                    '<div class="msg-meta"><span class="msg-user">' + (m.author || 'User') + '</span><span class="msg-time">' + (m.timestamp || '') + '</span></div>' +
                    '<div class="msg-text">' + (m.content || '') + '</div>' +
                    imgs +
                    '<div class="msg-reactions">' + reactTags + '</div>' +
                    '<div class="msg-actions">' +
                        '<button class="msg-act-btn" onclick="setReply(\'' + m.id + '\', \'' + (m.author||'') + '\')">Reply</button>' +
                        '<button class="msg-act-btn" onclick="sendReact(\'' + m.id + '\', \'❤️\')">❤️</button>' +
                        '<button class="msg-act-btn" onclick="sendReact(\'' + m.id + '\', \'👍\')">👍</button>' +
                        '<button class="msg-act-btn" onclick="sendReact(\'' + m.id + '\', \'😂\')">😂</button>' +
                        '<button class="msg-act-btn" onclick="sendReact(\'' + m.id + '\', \'🔥\')">🔥</button>' +
                    '</div>' +
                '</div>';
            chatMsgs.appendChild(bubble);
        });
        chatMsgs.scrollTop = chatMsgs.scrollHeight;
    }

    window.setReply = function(id, author) {
        replyToId = id;
        replyText.textContent = 'Đang trả lời ' + author;
        replyBar.style.display = 'flex';
    };

    if (cancelReply) {
        cancelReply.onclick = function() {
            replyToId = null;
            replyBar.style.display = 'none';
        };
    }

    window.sendReact = function(msgId, emoji) {
        if (!currentChan) return;
        fetch('/api/react', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ channel_id: currentChan, message_id: msgId, emoji: emoji })
        }).then(function() { notify('Đã gửi lệnh thả icon!'); });
    };

    if (webSendBtn) {
        webSendBtn.onclick = function() {
            var txt = webMsgInput.value.trim();
            var file = fileInput.files[0];
            if (!currentChan || (!txt && !file)) {
                notify('Hãy chọn kênh và nhập nội dung/file!', true);
                return;
            }

            var fd = new FormData();
            fd.append('channel_id', currentChan);
            fd.append('content', txt);
            if (replyToId) fd.append('reply_to', replyToId);
            if (file) fd.append('file', file);

            webSendBtn.disabled = true;
            fetch('/api/send', { method: 'POST', body: fd })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    webMsgInput.value = '';
                    fileInput.value = '';
                    if (replyBar) replyBar.style.display = 'none';
                    replyToId = null;
                    notify('Đã đưa tin nhắn vào hàng chờ!');
                    setTimeout(fetchChatHistory, 1000);
                })
                .catch(function(err) { notify('Lỗi gửi tin', true); })
                .then(function() { webSendBtn.disabled = false; });
        };
    }

    var COLORS = { online: '#3fc380', idle: '#f5b942', dnd: '#ef5b6b', invisible: '#5c6370', stream: '#9b59ff' };
    var LABELS = { online: 'Trực tuyến', idle: 'Chờ', dnd: 'Đừng làm phiền', invisible: 'Ẩn (nhìn như offline)', stream: 'Đang stream' };
    var ACT_LABELS = { playing: 'Đang chơi', listening: 'Đang nghe', watching: 'Đang xem', competing: 'Đang thi đấu', streaming: 'Streaming' };
    var snapshot = '';
    var idx = 0, timer = null;
    var liveStats = { guilds: null, members: null, ram_mb: null, ping_ms: null, live: false };

    function getMessages() {
        var out = [];
        msgBox.querySelectorAll('input[type=text]').forEach(function (i) {
            if (i.value.trim()) out.push(i.value.trim());
        });
        return out;
    }
    function getMode() {
        return form.querySelector('input[name="status_mode"]:checked').value;
    }
    function getAccent() {
        var el = form.querySelector('input[name="accent"]:checked');
        return el ? el.value : '#8b9bff';
    }
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
        p.set('status_messages', getMessages().join('\\n'));
        form.querySelectorAll('.mod input[type=checkbox]').forEach(function (c) {
            if (c.checked) p.set(c.name, 'on');
        });
        var pl = form.querySelector('input[name="platform"]:checked');
        p.set('platform', pl ? pl.value : 'ios');
        return p;
    }

    function num(v) { return v == null ? null : Number(v).toLocaleString('vi-VN'); }

    function latencyLabel(ms) {
        if (ms == null) return 'N/A';
        return ms < 150 ? 'Mượt' : (ms < 300 ? 'Ổn định' : 'Hơi lag');
    }

    function fill(text) {
        var g = num(liveStats.guilds);
        var m = num(liveStats.members);
        return text
            .replace(/\\{guilds\\}/g, g != null ? g : '12')
            .replace(/\\{members\\}/g, m != null ? m : '3.456')
            .replace(/\\{ram\\}(\\s*MB)?/gi, function (_m, suf) {
                return liveStats.ram_mb != null ? liveStats.ram_mb + (suf || ' MB') : 'N/A';
            })
            .replace(/\\{ping\\}(\\s*ms)?/gi, function (_m, suf) {
                return liveStats.ping_ms != null ? liveStats.ping_ms + (suf || ' ms') : 'N/A';
            })
            .replace(/\\{latency_status\\}/g, latencyLabel(liveStats.ping_ms))
            .replace(/\\{prefix\\}/g, prefixInput.value || '!');
    }

    function showMsg() {
        var list = getMessages();
        var span = document.createElement('span');
        if (list.length) {
            span.textContent = (ACT_LABELS[getActivity()] || '') + ' ' + fill(list[idx % list.length]);
        } else {
            span.textContent = 'Chưa có dòng status nào';
        }
        bubble.innerHTML = '';
        bubble.appendChild(span);
    }
    function restartPreview() {
        if (timer) clearInterval(timer);
        idx = 0;
        showMsg();
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
    }

    var avatarEl = document.getElementById('avatar');
    var pnameText = document.getElementById('pnameText');
    var brandMark = document.getElementById('brandMark');
    var shownAvatar = null;
    function applyIdentity() {
        if (liveStats.bot_name) {
            pnameText.textContent = liveStats.bot_name;
        }
        var url = liveStats.bot_avatar;
        if (!url || !/^https:\\/\\//.test(url) || url === shownAvatar) return;
        shownAvatar = url;
        [avatarEl, brandMark].forEach(function (box) {
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
    function timeAgo(ts) {
        if (!ts) return '';
        var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
        if (s < 60) return s + ' giây trước';
        if (s < 3600) return Math.round(s / 60) + ' phút trước';
        if (s < 86400) return Math.round(s / 3600) + ' giờ trước';
        return Math.round(s / 86400) + ' ngày trước';
    }

    var GUILD_PER_PAGE = 8, guildPage = 0, guildData = [];
    function renderGuilds(list) {
        guildData = list || [];
        drawGuilds();
    }
    function drawGuilds() {
        if (!guildRows) return;
        if (!guildData.length) {
            guildRows.innerHTML = '<div class="list-empty"><b>Chưa có dữ liệu</b>Cần bot.py gửi guild_list lên stats.json.</div>';
            return;
        }
        var pages = Math.ceil(guildData.length / GUILD_PER_PAGE);
        if (guildPage >= pages) guildPage = pages - 1;
        if (guildPage < 0) guildPage = 0;
        var slice = guildData.slice(guildPage * GUILD_PER_PAGE, (guildPage + 1) * GUILD_PER_PAGE);
        var out = '<div class="srv-grid">' + slice.map(function (g) {
            var ico = (g.icon && /^https:\\/\\//.test(g.icon))
                ? '<img src="' + esc(g.icon) + '" alt="" loading="lazy">'
                : esc((g.name || '?').charAt(0).toUpperCase());
            return '<div class="srv"><div class="srv-ico">' + ico + '</div><div class="srv-txt"><b>' +
                esc(g.name || 'Không tên') + '</b><small>' +
                esc(g.members != null ? num(g.members) + ' thành viên' : '') + '</small></div></div>';
        }).join('') + '</div>';
        if (pages > 1) {
            out += '<div class="pager"><button type="button" class="pg" id="pgPrev"' + (guildPage === 0 ? ' disabled' : '') +
                '>‹ Trước</button><span>Trang ' + (guildPage + 1) + ' / ' + pages + '</span><button type="button" class="pg" id="pgNext"' + (guildPage >= pages - 1 ? ' disabled' : '') +
                '>Sau ›</button></div>';
        }
        guildRows.innerHTML = out;
        var prev = document.getElementById('pgPrev'), next = document.getElementById('pgNext');
        if (prev) prev.addEventListener('click', function () { guildPage--; drawGuilds(); });
        if (next) next.addEventListener('click', function () { guildPage++; drawGuilds(); });
    }

    function renderActivity(list) {
        if (!activityRows) return;
        if (!list || !list.length) {
            activityRows.innerHTML = '<div class="list-empty"><b>Chưa có hoạt động nào</b>Cần bot.py ghi log lệnh vào activity.json.</div>';
            return;
        }
        activityRows.innerHTML = '<div class="rows">' + list.map(function (a) {
            return '<div class="row-item"><div class="main"><div><b>' + esc(a.action || '') +
                '</b><small>' + esc(a.user || 'Ẩn danh') + ' • ' + timeAgo(a.time) + '</small></div></div></div>';
        }).join('') + '</div>';
    }

    function pollStats() {
        fetch('/stats', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) { 
                liveStats = d; 
                applyStats(); 
                showMsg(); 
                renderGuilds(d.guild_list); 
                renderServers(d.guild_list);
            })
            .catch(function () {});
    }
    function pollActivity() {
        fetch('/activity', { cache: 'no-store' }).then(function (r) { return r.json(); })
            .then(renderActivity).catch(function () {});
    }
    
    var upBase = null, upBaseAt = 0, upRunning = false;
    function fmtClock(sec) {
        sec = Math.max(0, Math.floor(sec));
        var d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600),
            m = Math.floor((sec % 3600) / 60), s = sec % 60;
        return (d ? d + ' ngày ' : '') + (d || h ? h + ' giờ ' : '') + m + ' phút ' + s + ' giây';
    }
    function drawUptime() {
        var el = document.getElementById('upTotal');
        if (!el || upBase == null) return;
        var extra = upRunning ? (Date.now() - upBaseAt) / 1000 : 0;
        el.textContent = fmtClock(upBase + extra);
    }
    function pollUptime() {
        fetch('/uptime', { cache: 'no-store' }).then(function (r) { return r.json(); })
            .then(function (u) {
                upBase = u.total_seconds != null ? Number(u.total_seconds) : null;
                upBaseAt = Date.now();
                upRunning = !!u.running;
                drawUptime();
            }).catch(function () {});
    }
    setInterval(drawUptime, 1000);

    function pollBotState() {
        fetch('/botctl', { cache: 'no-store' }).then(function (r) { return r.json(); })
            .then(function (d) {
                if (!botState) return;
                botState.className = 'botctl-state ' + (d.running ? 'on' : 'off');
                botState.querySelector('span').textContent = d.running ? 'Bot đang chạy' : 'Bot đã dừng';
                if (btnStart) btnStart.disabled = d.running;
                if (btnStop) btnStop.disabled = !d.running;
                if (btnRestart) btnRestart.disabled = !d.running;
            })
            .catch(function () {});
    }

    if (btnStart) btnStart.addEventListener('click', function () {
        fetch('/botctl', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'action=start' })
            .then(pollBotState).then(function () { notify('Đã bật bot'); });
    });
    if (btnStop) btnStop.addEventListener('click', function () {
        fetch('/botctl', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'action=stop' })
            .then(pollBotState).then(function () { notify('Đã tắt bot'); });
    });
    if (btnRestart) btnRestart.addEventListener('click', function () {
        if (!confirm('Khởi động lại bot?')) return;
        fetch('/botctl', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'action=restart' })
            .then(function () { notify('Đang khởi động lại bot...'); setTimeout(pollBotState, 1500); });
    });

    var BG_OK = /^https:\\/\\/[^\\s"'()<>\\\\]{4,480}$/;
    function applyTheme() {
        var root = document.documentElement;
        var u = bgUrl.value.trim();
        if (BG_OK.test(u)) {
            root.style.setProperty('--bg-img', 'url("' + u + '")');
            document.body.classList.add('has-bg');
            if (bgPrev) bgPrev.style.backgroundImage = 'url("' + u + '")';
        } else {
            document.body.classList.remove('has-bg');
            if (bgPrev) bgPrev.style.backgroundImage = 'none';
        }
        root.style.setProperty('--dim', String(bgDim.value / 100));
        if (bgDimVal) bgDimVal.textContent = bgDim.value + '%';
        var t = siteTitle.value.trim() || 'KSBot Command Center';
        var sh1 = document.getElementById('siteH1');
        if (sh1) sh1.textContent = t;
        document.title = t;
    }
    [siteTitle, bgUrl, bgDim].forEach(function (el) {
        if (el) el.addEventListener('input', function () { refresh(); });
    });
    var stUrl = document.getElementById('streamUrl');
    if (stUrl) stUrl.addEventListener('input', function () { refresh(); });
    var bgCl = document.getElementById('bgClear');
    if (bgCl) bgCl.addEventListener('click', function () { bgUrl.value = ''; refresh(); });

    function refresh() {
        applyTheme();
        document.documentElement.style.setProperty('--accent', getAccent());
        var mode = getMode();
        if (dot) dot.style.background = COLORS[mode];
        if (pstate) pstate.textContent = LABELS[mode];
        if (delayVal) delayVal.textContent = delayInput.value + ' giây';
        if (prefixDemo) prefixDemo.textContent = (prefixInput.value || '!') + 'help';
        if (offNote) offNote.style.display = statusSwitch.checked ? 'none' : 'block';
        if (saveBar) saveBar.classList.toggle('show', serialize().toString() !== snapshot);
    }

    function onChange() { showMsg(); refresh(); }

    function bindRow(row) {
        row.querySelector('input').addEventListener('input', function () { showMsg(); refresh(); });
        row.querySelector('.icon-btn').addEventListener('click', function () {
            if (msgBox.children.length > 1) { row.remove(); showMsg(); refresh(); }
            else { row.querySelector('input').value = ''; showMsg(); refresh(); }
        });
    }
    function addRow(value) {
        var row = document.createElement('div');
        row.className = 'msg-row';
        row.innerHTML = '<input type="text" maxlength="120" placeholder="Nhập câu status..."><button type="button" class="icon-btn" title="Xóa dòng" aria-label="Xóa dòng">✕</button>';
        row.querySelector('input').value = value || '';
        msgBox.appendChild(row);
        bindRow(row);
        return row;
    }

    if (msgBox) {
        msgBox.querySelectorAll('.msg-row').forEach(bindRow);
        var addMsgBtn = document.getElementById('addMsg');
        if (addMsgBtn) {
            addMsgBtn.addEventListener('click', function () {
                addRow('').querySelector('input').focus();
                refresh();
            });
        }
    }

    var lastFocus = null;
    if (msgBox) {
        msgBox.addEventListener('focusin', function (e) { if (e.target.tagName === 'INPUT') lastFocus = e.target; });
    }
    document.querySelectorAll('.chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            var t = lastFocus || (msgBox ? msgBox.querySelector('input') : null);
            if (!t) return;
            var s = t.selectionStart == null ? t.value.length : t.selectionStart;
            var e = t.selectionEnd == null ? s : t.selectionEnd;
            t.value = t.value.slice(0, s) + chip.dataset.v + t.value.slice(e);
            t.focus();
            t.setSelectionRange(s + chip.dataset.v.length, s + chip.dataset.v.length);
            showMsg(); refresh();
        });
    });

    var allOnBtn = document.getElementById('allOn');
    if (allOnBtn) allOnBtn.addEventListener('click', function () { setAll(true); });
    var allOffBtn = document.getElementById('allOff');
    if (allOffBtn) allOffBtn.addEventListener('click', function () { setAll(false); });
    function setAll(v) {
        form.querySelectorAll('.mod input[type=checkbox]').forEach(function (c) { c.checked = v; });
        refresh();
    }

    if (form) form.addEventListener('change', onChange);
    if (prefixInput) prefixInput.addEventListener('input', function () { showMsg(); refresh(); });
    if (delayInput) {
        delayInput.addEventListener('input', function () { refresh(); });
        delayInput.addEventListener('change', restartPreview);
    }

    function notify(text, isErr) {
        if (!toast) return;
        toast.textContent = text;
        toast.className = 'toast show' + (isErr ? ' err' : '');
        setTimeout(function () { toast.className = 'toast' + (isErr ? ' err' : ''); }, 2600);
    }

    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            saveBtn.disabled = true;
            saveBtn.textContent = 'Đang lưu...';
            var body = serialize();
            fetch('/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'fetch' },
                body: body.toString()
            }).then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            }).then(function (d) {
                snapshot = body.toString();
                refresh();
                notify(d && d.restarted ? 'Đã lưu, bot đang khởi động lại...' : 'Đã lưu cấu hình');
                if (d && d.restarted) setTimeout(pollBotState, 1500);
            }).catch(function (err) {
                notify('Lưu thất bại: ' + err.message, true);
            }).then(function () {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Lưu thay đổi';
            });
        });
    }

    if (resetBtn) resetBtn.addEventListener('click', function () { location.reload(); });

    var TABS = ['overview', 'config', 'features', 'send', 'servers', 'theme'];
    function setTab(name) {
        if (TABS.indexOf(name) < 0) name = 'overview';
        document.body.setAttribute('data-tab', name);
        document.querySelectorAll('.tab').forEach(function (t) {
            var on = t.getAttribute('data-tab') === name;
            t.classList.toggle('on', on);
            t.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        try { history.replaceState(null, '', '#' + name); } catch (e) {}
    }
    document.querySelectorAll('.tab').forEach(function (t) {
        t.addEventListener('click', function () { setTab(t.getAttribute('data-tab')); window.scrollTo(0, 0); });
    });
    setTab((location.hash || '').replace('#', ''));
    window.addEventListener('hashchange', function () { setTab((location.hash || '').replace('#', '')); });

    snapshot = serialize().toString();
    refresh();
    restartPreview();
    applyStats();
    pollStats();
    pollActivity();
    pollBotState();
    pollUptime();
    setInterval(pollStats, 10000);
    setInterval(pollActivity, 15000);
    setInterval(pollBotState, 8000);
    setInterval(pollUptime, 10000);
    setInterval(fetchChatHistory, 3000);
})();
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__SITE_TITLE__</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' rx='24' fill='%238b9bff'/%3E%3Crect x='24' y='34' width='52' height='38' rx='12' fill='%230e1118'/%3E%3Ccircle cx='38' cy='53' r='6' fill='%238b9bff'/%3E%3Ccircle cx='62' cy='53' r='6' fill='%238b9bff'/%3E%3Crect x='46' y='16' width='8' height='14' rx='4' fill='%230e1118'/%3E%3Ccircle cx='50' cy='14' r='6' fill='%238b9bff'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>__CSS__</style>
<style>__THEME_CSS__</style>
</head>
<body data-tab="overview" class="__BODY_CLASS__">
<script>(function(){var h=(location.hash||'').slice(1);if(['overview','config','features','send','servers','theme'].indexOf(h)>=0)document.body.setAttribute('data-tab',h);})();</script>
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
        </div>
    </header>

    <nav class="tabs" role="tablist" aria-label="Các mục của dashboard">
        <button type="button" class="tab on" role="tab" data-tab="overview">Tổng quan</button>
        <button type="button" class="tab" role="tab" data-tab="config">Cấu hình &amp; Status</button>
        <button type="button" class="tab" role="tab" data-tab="features">Tính năng</button>
        <button type="button" class="tab" role="tab" data-tab="send">Gửi tin nhắn &amp; Chat</button>
        <button type="button" class="tab" role="tab" data-tab="servers">Server</button>
        <button type="button" class="tab" role="tab" data-tab="theme">Giao diện</button>
    </nav>

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
                <p class="sub">Đổi tên, màu và hình nền.</p>
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
                    <input type="text" id="bgUrl" name="bg_url" value="__BG_URL__" placeholder="https://..." autocomplete="off">
                    <div class="theme-prev" id="bgPrev"></div>
                </div>
                <div class="field">
                    <label for="bgDim">Độ tối lớp phủ</label>
                    <div class="delay-row">
                        <input type="range" id="bgDim" name="bg_dim" min="0" max="90" value="__BG_DIM__">
                        <span class="delay-val" id="bgDimVal">__BG_DIM__%</span>
                    </div>
                </div>
                <button type="button" class="add-btn" id="bgClear">Xóa hình nền</button>
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
                            <span><i class="dot" style="background:#5c6370"></i><div>Invisible<small>Ẩn</small></div></span></label>
                        <label class="mode"><input type="radio" name="status_mode" value="stream" __M_STREAM__>
                            <span><i class="dot" style="background:#9b59ff"></i><div>Stream<small>Chấm tím</small></div></span></label>
                    </div>
                </div>

                <div class="field">
                    <span class="label">Thiết bị hiển thị cạnh tên bot</span>
                    <div class="modes platforms">__PLATFORMS__</div>
                </div>

                <div class="field">
                    <span class="label">Loại hoạt động</span>
                    <div class="modes">__ACTIVITY_TYPES__</div>
                </div>

                <div class="field">
                    <label for="streamUrl">Link Stream</label>
                    <input type="text" id="streamUrl" name="stream_url" value="__STREAM_URL__" autocomplete="off">
                </div>

                <div class="field">
                    <label for="delay">Đổi câu status sau mỗi</label>
                    <div class="delay-row">
                        <input type="range" id="delay" name="status_delay" min="1" max="60" value="__DELAY__">
                        <span class="delay-val" id="delayVal">__DELAY__ giây</span>
                    </div>
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

            <!-- GIAO DIỆN DISCORD CLIENT 3 CỘT ĐỂ CHAT, XEM LỊCH SỬ & THẢ ICON -->
            <section class="card" data-show="send">
                <h2>Discord Client (Chat &amp; Thả Icon)</h2>
                <p class="sub">Giao diện y chang Discord: chọn server, chọn kênh, xem lịch sử chat trực tiếp, reply và thả reaction icon siêu mượt.</p>
                
                <div class="discord-app">
                    <div class="sidebar-servers" id="serverList"></div>
                    <div class="sidebar-channels" id="chanList"></div>
                    <div class="chat-area">
                        <div class="chat-header" id="currentChanTitle">Chưa chọn kênh nào</div>
                        <div class="chat-messages" id="chatMsgs"></div>
                        <div class="chat-input-box">
                            <div class="reply-preview" id="replyBar" style="display:none;">
                                <span id="replyText">Đang trả lời...</span>
                                <span id="cancelReply" style="cursor:pointer;">✕</span>
                            </div>
                            <div class="chat-input-wrap">
                                <label class="chat-file-btn" title="Tải file/ảnh lên">
                                    📁 <input type="file" id="fileInput" style="display:none;">
                                </label>
                                <input type="text" id="webMsgInput" placeholder="Nhập tin nhắn..." autocomplete="off">
                                <button type="button" class="send-btn-discord" id="webSendBtn">Gửi</button>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            <section class="card" data-show="servers">
                <h2>Server bot đang ở</h2>
                <div id="guildRows"></div>
            </section>

            <section class="card" data-show="overview">
                <h2>Thông tin hệ thống &amp; Trạng thái bot</h2>
                <div class="ov-tiles" style="margin-top:10px">
                    <div class="ov-tile" style="grid-column: span 2"><span>Bot online liên tục</span><b id="upTotal">—</b></div>
                    <div class="ov-tile"><span>Số lần chạy</span><b id="upSessions">—</b></div>
                </div>
                <div class="botctl" style="margin-top:20px">
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
                <div id="activityRows"></div>
            </section>
        </main>

        <div class="side" data-show="overview config">
        <aside class="preview card" data-show="overview config">
            <h2>Xem trước</h2>
            <p class="sub" style="margin-bottom:0">Bot hiển thị trên Discord.</p>
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
        '<span><span class="mode-ico">%(i)s</span>'
        '<div>%(n)s<small>%(d)s</small></div></span></label>'
        % {"k": k, "i": icon, "n": esc(name), "d": esc(desc), "c": "checked" if k == activity else ""}
        for k, icon, name, desc in ACTIVITY_TYPES
    )

    platform = config.get("platform", "ios")
    if platform not in PLATFORM_KEYS:
        platform = "ios"
    platform_html = "".join(
        '<label class="mode"><input type="radio" name="platform" value="%(k)s" %(c)s>'
        '<span><span class="mode-ico">%(i)s</span>'
        '<div>%(n)s<small>%(d)s</small></div></span></label>'
        % {"k": k, "i": icon, "n": esc(name), "d": esc(desc), "c": "checked" if k == platform else ""}
        for k, icon, name, desc in PLATFORM_OPTIONS
    )

    accent = config.get("accent")
    accent_row = next((a for a in ACCENTS if a[0] == accent), ACCENTS[0])
    accent_hex, _accent_name, accent_ink = accent_row

    swatches_html = "".join(
        '<div class="swatch"><input type="radio" id="ac_%(i)d" name="accent" value="%(hex)s" %(c)s>'
        '<label for="ac_%(i)d"><span class="dot" style="background:%(hex)s"></span><small>%(n)s</small></label></div>'
        % {"i": i, "hex": hexv, "n": esc(name), "c": "checked" if hexv == accent_row[0] else ""}
        for i, (hexv, name, _ink) in enumerate(ACCENTS)
    )

    page = PAGE_TEMPLATE
    page = page.replace("__CSS__", PAGE_CSS).replace("__JS__", PAGE_JS)
    page = page.replace("__ACCENT__", accent_hex).replace("__ACCENT_INK__", accent_ink)
    page = page.replace("__SWATCHES__", swatches_html)
    page = page.replace("__PREFIX__", esc(config.get("prefix", "!")))
    page = page.replace("__DELAY__", str(delay))
    page = page.replace("__M_ONLINE__", "checked" if mode == "online" else "")
    page = page.replace("__M_IDLE__", "checked" if mode == "idle" else "")
    page = page.replace("__M_DND__", "checked" if mode == "dnd" else "")
    page = page.replace("__M_INVISIBLE__", "checked" if mode == "invisible" else "")
    page = page.replace("__M_STREAM__", "checked" if mode == "stream" else "")
    page = page.replace("__ACTIVITY_TYPES__", activity_html)
    page = page.replace("__PLATFORMS__", platform_html)
    page = page.replace("__MSG_ROWS__", msg_rows)
    page = page.replace("__MODULES__", mods_html)
    page = page.replace("__INVITE_URL__", esc(INVITE_URL))

    site_title = str(config.get("site_title") or DEFAULT_CONFIG["site_title"])[:40]
    bg_url = str(config.get("bg_url") or DEFAULT_CONFIG["bg_url"])
    if not BG_URL_RE.match(bg_url):
        bg_url = DEFAULT_CONFIG["bg_url"]
    try:
        bg_dim = max(0, min(90, int(config.get("bg_dim", 50))))
    except (TypeError, ValueError):
        bg_dim = 50
    theme_css = ":root{--dim:%s;%s}" % (
        bg_dim / 100, ('--bg-img:url("%s");' % bg_url) if bg_url else ""
    )
    page = page.replace("__THEME_CSS__", theme_css)
    page = page.replace("__BODY_CLASS__", "has-bg" if bg_url else "")
    page = page.replace("__SITE_TITLE__", esc(site_title))
    page = page.replace("__BG_URL__", esc(bg_url))
    stream_url = normalize_stream_url(config.get("stream_url"))
    if not STREAM_URL_RE.match(stream_url):
        stream_url = DEFAULT_CONFIG["stream_url"]
    page = page.replace("__STREAM_URL__", esc(stream_url))
    page = page.replace("__BG_DIM__", str(bg_dim))
    return page


# ----------------------------------------------------------------------
# Web server
# ----------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):

    def _send_ping_ok(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/ping", "/healthz"):
            self._send_ping_ok()
            return

        if self.path.startswith("/api/chat/"):
            ch_id = self.path.split("/")[-1]
            data = read_chat_history(ch_id)
            self._reply_json(200, data)
            return

        if self.path in ("/stats", "/activity", "/outbox", "/botctl", "/uptime"):
            payload = {
                "/stats": read_stats,
                "/activity": read_activity,
                "/outbox": lambda: list(reversed(read_outbox()))[:10],
                "/botctl": bot_status,
                "/uptime": read_uptime,
            }[self.path]()
            out = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(out)
            return

        body = render_page(read_config()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _reply_json(self, status, payload):
        out = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        if self.path == "/api/send":
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': self.headers.get('Content-Type', '')}
            )
            ch_id = form.getvalue("channel_id")
            content = form.getvalue("content", "")
            reply_to = form.getvalue("reply_to", None)
            
            file_path = None
            if "file" in form and form["file"].filename:
                file_item = form["file"]
                os.makedirs("/tmp", exist_ok=True)
                file_path = os.path.join("/tmp", file_item.filename)
                with open(file_path, "wb") as f:
                    f.write(file_item.file.read())
                    
            add_web_command({
                "type": "send",
                "channel_id": ch_id,
                "content": content,
                "reply_to": reply_to,
                "file_path": file_path
            })
            self._reply_json(200, {"ok": True})
            return

        if self.path == "/api/react":
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            add_web_command({
                "type": "react",
                "channel_id": data["channel_id"],
                "message_id": data["message_id"],
                "emoji": data["emoji"]
            })
            self._reply_json(200, {"ok": True})
            return

        if self.path not in ("/save", "/botctl", "/send-message"):
            self.send_response(404)
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > 100_000:
            self.send_response(413)
            self.end_headers()
            return

        post_data = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(post_data, keep_blank_values=True)

        if self.path == "/botctl":
            action = parsed.get("action", [""])[0]
            if action == "start":
                start_bot()
            elif action == "stop":
                stop_bot()
            elif action == "restart":
                restart_bot()
            else:
                self._reply_json(400, {"ok": False, "error": "action phải là start, stop hoặc restart"})
                return
            self._reply_json(200, {"ok": True, **bot_status()})
            return

        if self.path == "/send-message":
            channel_id = parsed.get("channel_id", [""])[0].strip()
            content = parsed.get("content", [""])[0].strip()
            if not channel_id.isdigit():
                self._reply_json(400, {"ok": False, "error": "ID kênh phải là số"})
                return
            if not content:
                self._reply_json(400, {"ok": False, "error": "Nội dung tin nhắn trống"})
                return
            if len(content) > 2000:
                content = content[:2000]
            job = queue_message(channel_id, content)
            self._reply_json(200, {"ok": True, "job": job})
            return

        raw_status = parsed.get("status_messages", [""])[0].replace("\r", "")
        status_list = [line.strip() for line in raw_status.split("\n") if line.strip()]

        try:
            delay = int(parsed.get("status_delay", [5])[0])
        except (TypeError, ValueError):
            delay = 5
        delay = max(1, min(3600, delay))

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
            bg_url = DEFAULT_CONFIG["bg_url"]
        stream_url = normalize_stream_url(parsed.get("stream_url", [""])[0])
        if not STREAM_URL_RE.match(stream_url):
            stream_url = DEFAULT_CONFIG["stream_url"]
        try:
            bg_dim = max(0, min(90, int(parsed.get("bg_dim", [50])[0])))
        except (TypeError, ValueError):
            bg_dim = 50

        new_config = {
            "site_title": site_title,
            "bg_url": bg_url,
            "bg_dim": bg_dim,
            "stream_url": stream_url,
            "force_stream": force_stream,
            "platform": platform,
            "prefix": prefix[:5],
            "accent": accent,
            "status_mode": mode,
            "activity_type": activity_type,
            "status_delay": delay,
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
            out = json.dumps({"ok": True, "restarted": restarted}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        else:
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    def log_message(self, format, *args):
        quiet_paths = ("/ping", "/healthz", "/stats", "/activity", "/outbox", "/botctl", "/uptime", "/api/chat")
        if not any(self.path.startswith(p) for p in quiet_paths):
            super().log_message(format, *args)


def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
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
