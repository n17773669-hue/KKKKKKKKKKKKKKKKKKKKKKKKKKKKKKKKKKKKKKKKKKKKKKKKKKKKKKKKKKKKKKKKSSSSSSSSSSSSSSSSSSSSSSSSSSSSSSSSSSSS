import json
import os
import sys
import time
import discord
from discord.ext import commands, tasks

BOT_START_TIME = time.time()


def get_config():
    config_path = "config.json"
    default_cfg = {
        "bat_status": True,
        "status_mode": "idle",  # Mặc định là idle (màu vàng)
        "status_delay": 15,
        "status_messages": [
            "🤖 {guilds} server | {members} thành viên",
            "⚡ Ping: {ping}ms | {latency_status}",
            "⏱️ Online: {uptime} | RAM: {ram}",
            "💤 gõ {prefix}help để xem lệnh",
        ],
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
        except Exception as e:
            print(f"[STATUS] Lỗi đọc config.json: {e}")
    return default_cfg


def get_ram_usage():
    try:
        import psutil

        process = psutil.Process(os.getpid())
        ram_mb = process.memory_info().rss / (1024 * 1024)
        return f"{ram_mb:.1f} MB"
    except Exception:
        return "N/A"


def get_uptime_str():
    delta_sec = int(time.time() - BOT_START_TIME)
    days, remainder = divmod(delta_sec, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class StatusRotator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._index = 0
        self.rotate_status.start()

    def cog_unload(self):
        self.rotate_status.cancel()

    def _get_presence_status(self, mode_str: str):
        mode = str(mode_str).lower().strip()
        if mode == "online":
            return discord.Status.online
        elif mode == "dnd":
            return discord.Status.dnd
        elif mode in ["invisible", "offline"]:
            return discord.Status.invisible
        # Mặc định luôn là IDLE (Màu vàng)
        return discord.Status.idle

    async def _apply_status(self, raw_text: str, mode_str: str):
        if not self.bot.is_ready():
            return

        guild_count = len(self.bot.guilds)
        member_count = sum(
            g.member_count or len(g.members) for g in self.bot.guilds
        )

        ping = round(self.bot.latency * 1000)
        cogs_count = len(self.bot.cogs)
        bot_name = self.bot.user.name if self.bot.user else "Bot"
        uptime_str = get_uptime_str()
        ram_str = get_ram_usage()

        if ping < 100:
            latency_status = "🟢 Mượt"
        elif ping < 250:
            latency_status = "🟡 Bình thường"
        else:
            latency_status = "🔴 Mạng chậm"

        cfg = get_config()
        prefix = cfg.get("prefix", ".")

        text = (
            raw_text.replace("{guilds}", str(guild_count))
            .replace("{members}", str(member_count))
            .replace("{prefix}", prefix)
            .replace("{ping}", str(ping))
            .replace("{bot_name}", bot_name)
            .replace("{uptime}", uptime_str)
            .replace("{ram}", ram_str)
            .replace("{cogs}", str(cogs_count))
            .replace("{latency_status}", latency_status)
        )

        status_enum = self._get_presence_status(mode_str)

        # Sử dụng CustomActivity để tạo bong bóng thoại hiển thị sát tên bot
        activity = discord.CustomActivity(name=text)

        try:
            await self.bot.change_presence(
                status=status_enum, activity=activity
            )
            print(
                f"[STATUS] OK -> status={status_enum.name}, text='{text}'"
            )
        except Exception as exc:
            print(f"[STATUS] Lỗi khi đổi status: {exc}")

    @tasks.loop(seconds=15)
    async def rotate_status(self):
        cfg = get_config()

        if not cfg.get("bat_status", True):
            return

        delay = int(cfg.get("status_delay", 15))
        if delay < 5:
            delay = 5
        if self.rotate_status.seconds != delay:
            self.rotate_status.change_interval(seconds=delay)

        messages = cfg.get("status_messages", [])
        if not messages:
            messages = [
                "🤖 {guilds} server | {members} thành viên",
                "⚡ Ping: {ping}ms | {latency_status}",
                "⏱️ Online: {uptime} | RAM: {ram}",
                "💤 gõ {prefix}help để xem lệnh",
            ]

        raw_text = messages[self._index % len(messages)]
        mode_str = cfg.get("status_mode", "idle")

        self._index += 1
        await self._apply_status(raw_text, mode_str)

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.bot.wait_until_ready()
        cfg = get_config()
        mode_str = cfg.get("status_mode", "idle")
        await self._apply_status("💤 đang khởi động...", mode_str)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatusRotator(bot))
