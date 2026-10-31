"""Module đổi status luân phiên cho bot (extension: bot.load_extension("status")).

Đọc config.json do dashboard ghi: bat_status, status_messages, status_delay,
status_mode, activity_type, force_stream, stream_url, prefix.
Nút "Stream" trên dashboard = force_stream bật: bot hiện "Đang stream" kèm nút Xem.
"""
import json
import re
import time

import discord
from discord.ext import commands, tasks

CONFIG_FILE = "config.json"
DEFAULT_STREAM_URL = "https://www.youtube.com/watch?v=9svI65nxVhc"
START_TIME = time.time()

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "listening": discord.ActivityType.listening,
    "watching": discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}
STATUSES = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}


def normalize_stream_url(url):
    """Discord chỉ hiện nút Xem + chấm tím với link dạng youtube.com/watch?v=... hoặc twitch.tv/...
    Đổi youtu.be/ID (kể cả có ?si=...) thành dạng youtube.com/watch?v=ID."""
    url = str(url or "").strip()
    m = re.match(r"^https://(?:www\.|m\.)?youtu\.be/([A-Za-z0-9_-]{6,20})", url)
    if not m:
        m = re.match(r"^https://(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})", url)
    return "https://www.youtube.com/watch?v=" + m.group(1) if m else url


def load_cfg():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def ram_mb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return None


def fmt_uptime(seconds):
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} ngày {h} giờ"
    if h:
        return f"{h} giờ {m} phút"
    return f"{m} phút"


def latency_label(ms):
    if ms is None:
        return "N/A"
    return "Mượt" if ms < 150 else ("Ổn định" if ms < 300 else "Hơi lag")


def render_status(text, *, guilds, members, prefix, ping_ms, ram, uptime_s):
    """Thay các biến {guilds} {members} {prefix} {ping} {ram} {uptime} {latency_status}."""
    out = text.replace("{guilds}", f"{guilds:,}".replace(",", "."))
    out = out.replace("{members}", f"{members:,}".replace(",", "."))
    out = out.replace("{prefix}", prefix)
    out = re.sub(r"\{ping\}(\s*ms)?", lambda m: f"{ping_ms} ms" if ping_ms is not None else "N/A", out, flags=re.I)
    out = re.sub(r"\{ram\}(\s*MB)?", lambda m: f"{ram} MB" if ram is not None else "N/A", out, flags=re.I)
    out = out.replace("{uptime}", fmt_uptime(uptime_s))
    out = out.replace("{latency_status}", latency_label(ping_ms))
    return out


class StatusCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.index = 0
        self.rotate.start()

    def cog_unload(self):
        self.rotate.cancel()

    def build_text(self, text, cfg):
        lat = self.bot.latency
        ping = round(lat * 1000) if lat == lat and lat not in (float("inf"), float("-inf")) else None
        return render_status(
            text,
            guilds=len(self.bot.guilds),
            members=sum((g.member_count or 0) for g in self.bot.guilds),
            prefix=str(cfg.get("prefix", "!")),
            ping_ms=ping,
            ram=ram_mb(),
            uptime_s=time.time() - START_TIME,
        )

    @tasks.loop(seconds=5)
    async def rotate(self):
        cfg = load_cfg()
        try:
            self.rotate.change_interval(seconds=max(5, int(cfg.get("status_delay", 5))))
        except Exception:
            pass
        if not cfg.get("bat_status", True):
            return
        msgs = [m for m in (cfg.get("status_messages") or []) if str(m).strip()] or ["KSBot"]
        text = self.build_text(str(msgs[self.index % len(msgs)]), cfg)[:128]
        self.index += 1

        status = STATUSES.get(cfg.get("status_mode", "online"), discord.Status.online)
        if cfg.get("force_stream", True) or cfg.get("activity_type") == "streaming":
            activity = discord.Streaming(name=text, url=normalize_stream_url(cfg.get("stream_url") or DEFAULT_STREAM_URL))
            if status is not discord.Status.invisible:
                status = discord.Status.online  # chấm tím chỉ hiện khi Online
        else:
            kind = ACTIVITY_TYPES.get(cfg.get("activity_type", "playing"), discord.ActivityType.playing)
            activity = discord.Activity(type=kind, name=text)
        try:
            await self.bot.change_presence(status=status, activity=activity)
        except Exception as e:
            print(f"[status] Không đổi được status: {e!r}", flush=True)

    @rotate.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(StatusCog(bot))
