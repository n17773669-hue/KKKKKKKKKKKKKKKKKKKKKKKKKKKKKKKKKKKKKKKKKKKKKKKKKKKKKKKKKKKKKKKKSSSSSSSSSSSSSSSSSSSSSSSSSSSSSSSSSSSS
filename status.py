##############################################################
# ╔══════════════════════════════════════════════════════╗
# ║ Made by deleteduserf0bd3b64                          ║
# ║ Vui lòng ghi công lại tác giả gốc nếu bạn muốn       ║
# ║ tái tạo lại đoạn mã này.                             ║
# ║ Link server support: https://discord.gg/eCx2NwAPwA   ║
# ╚══════════════════════════════════════════════════════╝
##############################################################
"""Module đổi status luân phiên cho bot (extension: bot.load_extension("status")).

Đọc config.json (do dashboard hoặc lệnh Discord ghi): bat_status, status_messages,
status_delay, status_mode, activity_type, force_stream, stream_url, prefix.

Slash command (CHỈ owner, ID bên dưới), phản hồi ẩn chỉ owner thấy:
    /status info                xem cấu hình hiện tại
    /status toggle <bật|tắt>    bật/tắt module đổi status
    /status mode <online|idle|dnd|invisible|stream>
    /status type <playing|listening|watching|competing>
    /status delay <giây>        (tối thiểu 5)
    /status add <câu status>
    /status remove <số>
    /status list
    /status url <link twitch/youtube>
    /status vars

LƯU Ý: extension này phải được load TRƯỚC khi bot kết nối Discord
(ví dụ trong setup_hook hoặc trước bot.run) thì patch VR mới có tác dụng.
"""
import json
import os
import re
import time

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

OWNER_ID = 1404399385965498479

CONFIG_FILE = "config.json"
DEFAULT_STREAM_URL = "https://www.youtube.com/watch?v=9svI65nxVhc"
START_TIME = time.time()

STREAM_URL_RE = re.compile(
    r"^https://(www\.)?(twitch\.tv/[A-Za-z0-9_]{3,40}|youtube\.com/watch\?v=[A-Za-z0-9_-]{6,20}|youtu\.be/[A-Za-z0-9_-]{6,20})$"
)

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


# ----------------------------------------------------------------------
# Patch Gateway identify để hiển thị dưới dạng thiết bị VR
# Giữ nguyên payload gốc của discord.py (shard, intents, presence...) và chỉ
# thay "properties", nên không bị lệch phiên bản discord.py.
# ----------------------------------------------------------------------
def patch_gateway_identify():
    try:
        from discord.gateway import DiscordWebSocket
    except Exception as e:
        print(f"[PATCH] Không patch được gateway: {e!r}", flush=True)
        return
    if getattr(DiscordWebSocket, "_vr_patched", False):
        return

    original_identify = DiscordWebSocket.identify

    async def patched_identify(self):
        original_send = self.send_as_json

        async def send(data):
            if isinstance(data, dict) and data.get("op") == self.IDENTIFY:
                data["d"]["properties"] = {
                    "$os": "android",
                    "$browser": "Discord VR",
                    "$device": "Meta Quest 3",
                }
            await original_send(data)

        self.send_as_json = send
        try:
            await original_identify(self)
        finally:
            # bỏ override trên instance, trả về method gốc của class
            self.__dict__.pop("send_as_json", None)

    DiscordWebSocket.identify = patched_identify
    DiscordWebSocket._vr_patched = True
    print("[PATCH] Gateway identify patched for VR Device.", flush=True)


patch_gateway_identify()


# ----------------------------------------------------------------------
# Tiện ích config
# ----------------------------------------------------------------------
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


def save_cfg(updates):
    """Đọc config hiện tại, gộp thay đổi, ghi file tạm rồi đổi tên (không hỏng file)."""
    cfg = load_cfg()
    cfg.update(updates)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)
    return cfg


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


# ----------------------------------------------------------------------
# Cog
# ----------------------------------------------------------------------
class StatusCog(commands.GroupCog, group_name="status", group_description="Chỉnh status bot (chỉ owner)"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.index = 0
        self._synced = False
        self.rotate.start()

    def cog_unload(self):
        self.rotate.cancel()

    # Chỉ owner mới dùng được mọi slash command trong cog này
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_ID:
            raise app_commands.CheckFailure("not owner")
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            msg = "⛔ Chỉ owner bot mới dùng được lệnh này."
        else:
            print(f"[status] lỗi lệnh: {error!r}", flush=True)
            msg = "⚠️ Có lỗi xảy ra, xem log bot."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def sync_tree(self):
        """Đăng ký slash command lên Discord (1 lần). Nếu bot.py đã tự sync thì có thể bỏ."""
        if self._synced:
            return
        self._synced = True
        try:
            synced = await self.bot.tree.sync()
            print(f"[status] Đã sync {len(synced)} slash command.", flush=True)
        except Exception as e:
            self._synced = False
            print(f"[status] Sync slash command lỗi: {e!r}", flush=True)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.sync_tree()

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

    def apply_now(self):
        """Áp dụng ngay thay vì chờ vòng lặp kế tiếp."""
        try:
            self.rotate.restart()
        except Exception:
            pass

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

    # ------------------------------------------------------------------
    # Slash command
    # ------------------------------------------------------------------
    @app_commands.command(name="info", description="Xem cấu hình status hiện tại")
    async def status_info(self, interaction: discord.Interaction):
        cfg = load_cfg()
        mode = "stream" if cfg.get("force_stream", True) else cfg.get("status_mode", "online")
        msgs = cfg.get("status_messages") or []
        await interaction.response.send_message(
            "**⚙️ Cấu hình status hiện tại**\n"
            f"• Module: {'🟢 bật' if cfg.get('bat_status', True) else '🔴 tắt'}\n"
            f"• Chế độ: `{mode}`\n"
            f"• Loại hoạt động: `{cfg.get('activity_type', 'playing')}`\n"
            f"• Đổi câu mỗi: `{cfg.get('status_delay', 5)}` giây\n"
            f"• Link stream: {cfg.get('stream_url') or DEFAULT_STREAM_URL}\n"
            f"• Số câu status: `{len(msgs)}`",
            ephemeral=True,
        )

    @app_commands.command(name="toggle", description="Bật/tắt module đổi status")
    @app_commands.describe(state="Bật hoặc tắt")
    async def status_toggle(self, interaction: discord.Interaction, state: Literal["bật", "tắt"]):
        on = state == "bật"
        save_cfg({"bat_status": on})
        if on:
            self.apply_now()
        await interaction.response.send_message(
            "🟢 Đã bật đổi status." if on else "🔴 Đã tắt đổi status (giữ nguyên status hiện tại).",
            ephemeral=True,
        )

    @app_commands.command(name="mode", description="Đổi màu chấm trạng thái")
    @app_commands.describe(mode="online / idle / dnd / invisible / stream")
    async def status_mode(self, interaction: discord.Interaction,
                          mode: Literal["online", "idle", "dnd", "invisible", "stream"]):
        if mode == "stream":
            save_cfg({"force_stream": True, "status_mode": "online"})
            note = "Chấm tím + nút Xem."
        else:
            save_cfg({"force_stream": False, "status_mode": mode})
            note = "Đã tắt chế độ stream."
        self.apply_now()
        await interaction.response.send_message(f"✅ Chế độ: `{mode}`. {note}", ephemeral=True)

    @app_commands.command(name="type", description="Đổi loại hoạt động (Đang chơi/nghe/xem/thi đấu)")
    @app_commands.describe(kind="Loại hoạt động")
    async def status_type(self, interaction: discord.Interaction,
                          kind: Literal["playing", "listening", "watching", "competing"]):
        was_stream = load_cfg().get("force_stream", True)
        save_cfg({"activity_type": kind, "force_stream": False})
        self.apply_now()
        extra = " (đã tắt chế độ stream vì stream ghi đè loại hoạt động)" if was_stream else ""
        await interaction.response.send_message(f"✅ Loại hoạt động: `{kind}`{extra}", ephemeral=True)

    @app_commands.command(name="delay", description="Đổi tốc độ xoay câu status (giây)")
    @app_commands.describe(seconds="Từ 5 đến 3600 giây")
    async def status_delay(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 5, 3600]):
        save_cfg({"status_delay": seconds})
        self.apply_now()
        await interaction.response.send_message(f"✅ Đổi câu status mỗi `{seconds}` giây.", ephemeral=True)

    @app_commands.command(name="add", description="Thêm một câu status")
    @app_commands.describe(text="Nội dung, có thể dùng biến như {guilds} {members} {ping}")
    async def status_add(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 120]):
        msgs = list(load_cfg().get("status_messages") or [])
        msgs.append(text.strip())
        save_cfg({"status_messages": msgs})
        await interaction.response.send_message(f"✅ Đã thêm câu #{len(msgs)}: {text}", ephemeral=True)

    @app_commands.command(name="remove", description="Xóa một câu status theo số thứ tự")
    @app_commands.describe(number="Số thứ tự (xem bằng /status list)")
    async def status_remove(self, interaction: discord.Interaction, number: int):
        msgs = list(load_cfg().get("status_messages") or [])
        if not 1 <= number <= len(msgs):
            await interaction.response.send_message(f"⚠️ Số phải từ 1 đến {len(msgs)}.", ephemeral=True)
            return
        if len(msgs) == 1:
            await interaction.response.send_message("⚠️ Phải giữ lại ít nhất 1 câu status.", ephemeral=True)
            return
        removed = msgs.pop(number - 1)
        save_cfg({"status_messages": msgs})
        await interaction.response.send_message(f"🗑️ Đã xóa: {removed}", ephemeral=True)

    @app_commands.command(name="list", description="Xem các câu status")
    async def status_list(self, interaction: discord.Interaction):
        msgs = load_cfg().get("status_messages") or []
        if not msgs:
            await interaction.response.send_message("Chưa có câu status nào.", ephemeral=True)
            return
        text = "**📋 Các câu status:**\n" + "\n".join(f"`{i}.` {m}" for i, m in enumerate(msgs, 1))
        await interaction.response.send_message(text[:1900], ephemeral=True)

    @app_commands.command(name="url", description="Đổi link stream (Twitch/YouTube)")
    @app_commands.describe(link="twitch.tv/... hoặc youtube.com/watch?v=... hoặc youtu.be/...")
    async def status_url(self, interaction: discord.Interaction, link: str):
        link = normalize_stream_url(link)
        if not STREAM_URL_RE.match(link):
            await interaction.response.send_message(
                "⚠️ Chỉ nhận link Twitch hoặc YouTube.", ephemeral=True)
            return
        save_cfg({"stream_url": link})
        self.apply_now()
        await interaction.response.send_message(f"✅ Link stream: {link}", ephemeral=True)

    @app_commands.command(name="vars", description="Xem các biến dùng trong câu status")
    async def status_vars(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**Biến dùng trong câu status:**\n"
            "`{guilds}` số server • `{members}` tổng thành viên • `{prefix}` prefix\n"
            "`{ping}` ping • `{ram}` RAM • `{uptime}` thời gian chạy • `{latency_status}` Mượt/Ổn định/Hơi lag",
            ephemeral=True,
        )


async def setup(bot):
    cog = StatusCog(bot)
    await bot.add_cog(cog)
    if bot.is_ready():  # load muộn sau on_ready thì sync luôn
        await cog.sync_tree()
