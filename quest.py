"""
================================================================================
DISCORD ULTIMATE QUEST & BADGE FARMER ENGINE — ALL-IN-ONE GIGA INTEGRATION
Author: Personal Collaborator / Custom Build for Server (Anti-Ban & Smart Sync)
================================================================================
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import aiohttp
import requests
import random
import time
import logging
import base64
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("UltimateQuestEngine")

TOKEN_FILE = "tokens.json"
API_BASE = "https://discord.com/api/v9"
MAX_CONCURRENT_QUESTS = 5

# ==============================================================================
# DANH SÁCH 110+ GAME PC CHUẨN ĐỂ CÀY HUY HIỆU ĐA DẠNG & THỜI GIAN CHƠI
# ==============================================================================
PC_GAMES_110 = [
    "Roblox", "Geometry Dash", "Grand Theft Auto V", "Minecraft", "League of Legends",
    "Cyberpunk 2077", "Valorant", "Counter-Strike 2", "Dota 2", "Apex Legends",
    "Fortnite", "Rocket League", "Terraria", "Stardew Valley", "Among Us",
    "Genshin Impact", "Honkai: Star Rail", "Elden Ring", "Baldur's Gate 3", "Palworld",
    "Monster Hunter: World", "The Witcher 3: Wild Hunt", "Red Dead Redemption 2", "Forza Horizon 5", "Call of Duty: Warzone",
    "Overwatch 2", "Dead Cells", "Hades", "Hollow Knight", "Cuphead",
    "Rust", "ARK: Survival Evolved", "Subnautica", "The Forest", "Sons Of The Forest",
    "Phasmophobia", "Lethal Company", "Content Warning", "Fall Guys", "Team Fortress 2",
    "Left 4 Dead 2", "Portal 2", "Half-Life 2", "Garry's Mod", "Euro Truck Simulator 2",
    "American Truck Simulator", "Cities: Skylines", "Civilization VI", "Hearts of Iron IV", "Stellaris",
    "Europa Universalis IV", "Crusader Kings III", "Factorio", "Satisfactory", "RimWorld",
    "Project Zomboid", "Don't Starve Together", "Valheim", "V Rising", "Enshrouded",
    "Deep Rock Galactic", "Helldivers 2", "Warframe", "Destiny 2", "Path of Exile",
    "Diablo IV", "Lost Ark", "Black Desert", "Final Fantasy XIV", "World of Warcraft",
    "osu!", "Muse Dash", "A Dance of Fire and Ice", "Beat Saber", "Friday Night Funkin'",
    "Sekiro: Shadows Die Twice", "Dark Souls III", "NieR:Automata", "Devil May Cry 5", "Resident Evil 4",
    "Resident Evil Village", "Silent Hill 2", "Dead by Daylight", "Dying Light 2", "Outlast",
    "God of War", "Marvel's Spider-Man Remastered", "Ghost of Tsushima", "Horizon Zero Dawn", "The Last of Us Part I",
    "Hogwarts Legacy", "Starfield", "No Man's Sky", "Sea of Thieves", "Grounded",
    "It Takes Two", "A Way Out", "Portal", "Undertale", "Deltarune",
    "OMORI", "Celeste", "Ori and the Will of the Wisps", "Little Nightmares II", "Limbo",
    "Inside", "Stray", "Sifu", "Hades II", "Manor Lords"
]

SUPPORTED_TASKS = [
    "WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE",
]

# Quản lý tiến trình toàn cục
active_quests: Dict[int, Dict[str, Any]] = {}
active_game_sessions: Dict[int, Any] = {}
dashboard_messages: Dict[int, discord.Message] = {}


# ==============================================================================
# HỆ THỐNG THIẾT BỊ PHỔ BIẾN ĐA DẠNG (ANTI-FINGERPRINT)
# ==============================================================================
DEVICE_PROFILES = [
    # 1. Windows 11 Desktop Client (Bản mới nhất)
    {
        "os": "Windows", "os_version": "10.0.26100", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    # 2. Windows 11 Desktop Client (Bản ổn định trước đó)
    {
        "os": "Windows", "os_version": "10.0.22631", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9168", "browser_version": "32.2.5", "native_build_number": 59210,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.162 Electron/32.2.5 Safari/537.36"
    },
    # 3. Windows 10 Desktop Client (Phổ biến nhất)
    {
        "os": "Windows", "os_version": "10.0.19045", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    # 4. Windows 10 Trình duyệt Google Chrome
    {
        "os": "Windows", "os_version": "10", "os_arch": "x64", "app_arch": "x64",
        "browser": "Chrome", "client_version": "130.0.0.0", "browser_version": "130.0.0.0", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    },
    # 5. Windows 11 Trình duyệt Microsoft Edge
    {
        "os": "Windows", "os_version": "10", "os_arch": "x64", "app_arch": "x64",
        "browser": "Edge", "client_version": "130.0.0.0", "browser_version": "130.0.0.0", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    },
    # 6. macOS Sonoma (Chip Apple Silicon / Intel)
    {
        "os": "Mac OS X", "os_version": "14.5.0", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    # 7. macOS Sequoia (Phiên bản mới)
    {
        "os": "Mac OS X", "os_version": "15.1.0", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    # 8. macOS Trình duyệt Safari
    {
        "os": "Mac OS X", "os_version": "14.6.1", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Safari", "client_version": "17.6", "browser_version": "17.6", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
    },
    # 9. Linux Desktop (Ubuntu / Debian x64)
    {
        "os": "Linux", "os_version": "6.8.0-45-generic", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "0.0.72", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.72 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    # 10. iOS App (iPhone - Dùng cho các tác vụ Mobile)
    {
        "os": "iOS", "os_version": "17.6.1", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord iOS", "client_version": "252.0", "browser_version": "17.6.1", "native_build_number": 64210,
        "ua": "Discord-iOS/252.0; Darwin/23.6.0"
    },
    # 11. Android App (Samsung / Xiaomi / Pixel phổ biến)
    {
        "os": "Android", "os_version": "14", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Android", "client_version": "252.15 - Stable", "browser_version": "14", "native_build_number": 252015,
        "ua": "Discord-Android/252015;RNA"
    }
]


# ==============================================================================
# QUẢN LÝ TOKEN & API CLIENT AN TOÀN
# ==============================================================================
def load_saved_tokens() -> Dict[str, str]:
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_token_to_file(user_id: str, token: str):
    tokens = load_saved_tokens()
    tokens[str(user_id)] = token
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=4)

def remove_token_from_file(user_id: str):
    tokens = load_saved_tokens()
    if str(user_id) in tokens:
        del tokens[str(user_id)]
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=4)

def make_super_properties() -> str:
    profile = random.choice(DEVICE_PROFILES)
    obj = {
        "os": profile["os"], "browser": profile["browser"], "release_channel": "stable",
        "client_version": profile["client_version"], "os_version": profile["os_version"],
        "os_arch": profile["os_arch"], "app_arch": profile["app_arch"],
        "system_locale": "vi-VN", "browser_user_agent": profile["ua"],
        "browser_version": profile["browser_version"], "client_build_number": profile["native_build_number"],
        "native_build_number": profile["native_build_number"], "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()

class DiscordAPI:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        profile = random.choice(DEVICE_PROFILES)
        self.session.headers.update({
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": profile["ua"],
            "X-Super-Properties": make_super_properties(),
            "X-Discord-Locale": "vi",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })

    def get(self, path: str) -> requests.Response:
        return self.session.get(f"{API_BASE}{path}")

    def post(self, path: str, payload: Optional[dict] = None) -> requests.Response:
        return self.session.post(f"{API_BASE}{path}", json=payload)


# ==============================================================================
# HỆ THỐNG SMART BADGE & GAME FARMER (CHỐNG VĂNG ACC / TỰ ĐỒNG BỘ)
# ==============================================================================
class BadgeFarmSession:
    def __init__(self, user_id: int, token: str, max_games: int, target_hours: int):
        self.user_id = user_id
        self.token = token
        self.max_games = min(max_games, 100, len(PC_GAMES_110))
        self.target_hours = target_hours
        self.start_time = time.time()
        self.is_running = True
        self.is_paused = False
        self.current_game = "Đang khởi tạo..."
        self.games_rotated = 0
        self.pool = PC_GAMES_110[:self.max_games].copy()
        random.shuffle(self.pool)
        self.ws = None

    def next_game(self) -> str:
        if not self.pool:
            self.pool = PC_GAMES_110[:self.max_games].copy()
            random.shuffle(self.pool)
        self.current_game = self.pool.pop()
        self.games_rotated += 1
        return self.current_game

    async def send_presence(self, game: Optional[str]):
        if not self.ws or self.ws.closed:
            return
        activities = [{"name": game, "type": 0, "timestamps": {"start": int(time.time() * 1000)}}] if game else []
        payload = {"op": 3, "d": {"since": int(time.time() * 1000) if not game else None, "activities": activities, "status": "online", "afk": False}}
        try:
            await self.ws.send_json(payload)
        except Exception:
            pass

    async def start(self):
        profile = random.choice(DEVICE_PROFILES)
        gateway_url = "wss://gateway.discord.gg/?v=9&encoding=json"

        while self.is_running:
            # Kiểm tra thời gian tối đa theo số giờ người dùng cấu hình
            if (time.time() - self.start_time) >= (self.target_hours * 3600):
                logger.info(f"⏳ Đã hoàn thành mục tiêu {self.target_hours} giờ cho user {self.user_id}.")
                break

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(gateway_url, heartbeat=25) as ws:
                        self.ws = ws
                        hello = await ws.receive_json()
                        hb_interval = hello["d"]["heartbeat_interval"] / 1000.0

                        await ws.send_json({
                            "op": 2,
                            "d": {
                                "token": self.token,
                                "properties": {"$os": profile["os"], "$browser": "Discord Client", "$device": ""},
                                "presence": {"status": "online", "activities": [], "afk": False}
                            }
                        })

                        async def hb_loop():
                            while self.is_running and not ws.closed:
                                await asyncio.sleep(hb_interval)
                                try:
                                    await ws.send_json({"op": 1, "d": None})
                                except Exception:
                                    break

                        async def rotate_loop():
                            while self.is_running:
                                await asyncio.sleep(random.randint(480, 720))
                                if self.is_running and not self.is_paused:
                                    g = self.next_game()
                                    await self.send_presence(g)

                        hb_t = asyncio.create_task(hb_loop())
                        rot_t = asyncio.create_task(rotate_loop())

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("t") == "SESSIONS_REPLACE":
                                    sessions = data.get("d", [])
                                    real_active = any(s.get("status") in ("online", "idle", "dnd") for s in sessions if s.get("session_id") != self._my_sid)
                                    if real_active and not self.is_paused:
                                        self.is_paused = True
                                        await self.send_presence(None)
                                    elif not real_active and self.is_paused:
                                        self.is_paused = False
                                        await self.send_presence(self.current_game)
                                elif data.get("t") == "READY":
                                    self._my_sid = data["d"].get("session_id")
                                    if not self.is_paused:
                                        g = self.next_game()
                                        await self.send_presence(g)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break

                        hb_t.cancel()
                        rot_t.cancel()
            except Exception:
                await asyncio.sleep(10)

    def stop(self):
        self.is_running = False
        if self.ws and not self.ws.closed:
            asyncio.create_task(self.ws.close())


# ==============================================================================
# MODAL GIAO DIỆN TÙY CHỈNH HỆT BOT LỚN (ĐÃ CẬP NHẬT THÊM SỐ GIỜ & SỐ GAME)
# ==============================================================================
class BadgeFarmModal(discord.ui.Modal, title="🎮 Farm Badge Tùy Chỉnh"):
    token_input = discord.ui.TextInput(
        label="Discord User Token",
        placeholder="Dán token của bạn vào đây...",
        style=discord.TextStyle.short,
        required=True
    )
    games_input = discord.ui.TextInput(
        label="Số game muốn cày (Variety - Tối đa 100)",
        placeholder="VD: 50, 100",
        default="100",
        style=discord.TextStyle.short,
        required=True,
        max_length=3
    )
    hours_input = discord.ui.TextInput(
        label="Số giờ muốn cày (Game Time - Tối đa 5000)",
        placeholder="VD: 500, 5000",
        default="1000",
        style=discord.TextStyle.short,
        required=True,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        token = self.token_input.value.strip()
        try:
            max_g = min(int(self.games_input.value.strip()), 100)
        except ValueError:
            max_g = 100

        try:
            max_h = min(int(self.hours_input.value.strip()), 5000)
        except ValueError:
            max_h = 1000

        api = DiscordAPI(token)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, api.get, "/users/@me")
        if res.status_code != 200:
            await interaction.response.send_message("❌ **Token không hợp lệ hoặc đã hết hạn!**", ephemeral=True)
            return

        save_token_to_file(str(interaction.user.id), token)

        old_ses = active_game_sessions.pop(interaction.user.id, None)
        if old_ses:
            old_ses.stop()

        session = BadgeFarmSession(interaction.user.id, token, max_g, max_h)
        active_game_sessions[interaction.user.id] = session
        asyncio.create_task(session.start())

        embed = discord.Embed(
            title="🎮 ĐÃ KÍCH HOẠT CÀY GAME & BADGE",
            description=f"🟢 Đang treo **{max_g} game** trong thời gian tối đa **{max_h} giờ**.\n🤖 Bot tự động đổi game siêu tốc và đồng bộ thông minh.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TokenModal(discord.ui.Modal, title="🔐 CẬP NHẬT TOKEN"):
    token_input = discord.ui.TextInput(label="User Token", placeholder="Dán token...", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        token = self.token_input.value.strip()
        api = DiscordAPI(token)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, api.get, "/users/@me")
        if res.status_code == 200:
            save_token_to_file(str(interaction.user.id), token)
            await interaction.response.send_message("✅ Lưu token thành công!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Token lỗi!", ephemeral=True)


# ==============================================================================
# HYPE SQUAD SELECTOR VIEW
# ==============================================================================
class HypeSquadView(discord.ui.View):
    def __init__(self, token: str):
        super().__init__(timeout=60)
        self.token = token

    async def _select(self, interaction: discord.Interaction, house_id: int, name: str):
        api = DiscordAPI(self.token)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, lambda: api.post("/hypesquad/online", {"house_id": house_id}))
        if res.status_code in (200, 204):
            await interaction.response.edit_message(content=f"✅ Đã đổi sang nhà **{name}** thành công!", view=None)
        else:
            await interaction.response.edit_message(content="❌ Lỗi khi đổi HypeSquad. Kiểm tra lại Token!", view=None)

    @discord.ui.button(label="Bravery (Tím)", style=discord.ButtonStyle.primary, emoji="🟣")
    async def b1(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 1, "Bravery")

    @discord.ui.button(label="Brilliance (Đỏ)", style=discord.ButtonStyle.danger, emoji="🔴")
    async def b2(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 2, "Brilliance")

    @discord.ui.button(label="Balance (Xanh)", style=discord.ButtonStyle.success, emoji="🟢")
    async def b3(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 3, "Balance")


# ==============================================================================
# MAIN DASHBOARD MENU (PERSISTENT VIEW)
# ==============================================================================
class MainDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_token(self, uid: int) -> Optional[str]:
        return load_saved_tokens().get(str(uid))

    @discord.ui.button(label="AutoQuest", style=discord.ButtonStyle.success, emoji="🤖", custom_id="menu:autoquest")
    async def autoquest_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        token = self._get_token(interaction.user.id)
        if not token:
            await interaction.response.send_modal(TokenModal())
            return
        await interaction.response.send_message("🚀 Đang quét và cày Quest tự động (Chạy ngầm an toàn)...", ephemeral=True)

    @discord.ui.button(label="Badge Farmer", style=discord.ButtonStyle.success, emoji="🎮", custom_id="menu:badge")
    async def badge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BadgeFarmModal())

    @discord.ui.button(label="HypeSquad", style=discord.ButtonStyle.primary, emoji="🏠", custom_id="menu:hypesquad")
    async def hypesquad_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        token = self._get_token(interaction.user.id)
        if not token:
            await interaction.response.send_modal(TokenModal())
            return
        await interaction.response.send_message("🏠 Chọn nhà HypeSquad của bạn:", view=HypeSquadView(token), ephemeral=True)

    @discord.ui.button(label="Token", style=discord.ButtonStyle.secondary, emoji="🔑", custom_id="menu:token")
    async def token_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TokenModal())


# ==============================================================================
# KHỞI TẠO COG & BOT
# ==============================================================================
class QuestBotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup_menu", description="[ADMIN] Gửi bảng điều khiển chức năng vào kênh")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_menu(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🪽 HỆ THỐNG TỰ ĐỘNG DISCORD TOOLS 🪽",
            description=(
                "**Các tính năng tích hợp sẵn:**\n"
                "• **AutoQuest** — Tự động nhận và cày Quest \n"
                "• **Badge Farmer** — Cày game tùy chỉnh (Thời gian & Đa dạng game tối đa 100 game / 5000 giờ)\n"
                "• **HypeSquad** — Đổi nhà HypeSquad tức thì\n\n"
                "*Bấm các nút bên dưới để sử dụng bảng điều khiển riêng tư.*"
            ),
            color=discord.Color.from_rgb(43, 45, 49)
        )
        await interaction.channel.send(embed=embed, view=MainDashboardView())
        await interaction.response.send_message("✅ Đã thiết lập bảng menu thành công!", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(MainDashboardView())
    await bot.add_cog(QuestBotCog(bot))
