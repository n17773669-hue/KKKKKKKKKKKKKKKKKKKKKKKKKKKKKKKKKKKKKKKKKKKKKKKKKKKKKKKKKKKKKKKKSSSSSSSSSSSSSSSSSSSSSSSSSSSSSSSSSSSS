"""
================================================================================
DISCORD ULTIMATE QUEST & BADGE FARMER ENGINE — COMPONENTS V2 & MULTI-SETUP EDITION
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
SETUP_CONFIG_FILE = "server_setups.json"
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

active_quests: Dict[int, Dict[str, Any]] = {}
active_game_sessions: Dict[int, Any] = {}
dashboard_messages: Dict[int, discord.Message] = {}

# ==============================================================================
# 11 PROFILES GIẢ LẬP THIẾT BỊ ANTI-BAN ĐA NỀN TẢNG
# ==============================================================================
DEVICE_PROFILES = [
    {
        "os": "Windows", "os_version": "10.0.26100", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "Windows", "os_version": "10.0.22631", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9168", "browser_version": "32.2.5", "native_build_number": 59210,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9168 Chrome/128.0.6613.162 Electron/32.2.5 Safari/537.36"
    },
    {
        "os": "Windows", "os_version": "10.0.19045", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "Windows", "os_version": "10", "os_arch": "x64", "app_arch": "x64",
        "browser": "Chrome", "client_version": "130.0.0.0", "browser_version": "130.0.0.0", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    },
    {
        "os": "Windows", "os_version": "10", "os_arch": "x64", "app_arch": "x64",
        "browser": "Edge", "client_version": "130.0.0.0", "browser_version": "130.0.0.0", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    },
    {
        "os": "Mac OS X", "os_version": "14.5.0", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "Mac OS X", "os_version": "15.1.0", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Client", "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "Mac OS X", "os_version": "14.6.1", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Safari", "client_version": "17.6", "browser_version": "17.6", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
    },
    {
        "os": "Linux", "os_version": "6.8.0-45-generic", "os_arch": "x64", "app_arch": "x64",
        "browser": "Discord Client", "client_version": "0.0.72", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) discord/0.0.72 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "iOS", "os_version": "17.6.1", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord iOS", "client_version": "252.0", "browser_version": "17.6.1", "native_build_number": 64210,
        "ua": "Discord-iOS/252.0; Darwin/23.6.0"
    },
    {
        "os": "Android", "os_version": "14", "os_arch": "arm64", "app_arch": "arm64",
        "browser": "Discord Android", "client_version": "252.15 - Stable", "browser_version": "14", "native_build_number": 252015,
        "ua": "Discord-Android/252015;RNA"
    }
]

# ==============================================================================
# QUẢN LÝ LƯU TRỮ TOKEN & CẤU HÌNH SETUP THEO SERVER (NHỚ SAU KHI RESTART)
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

def load_server_setups() -> Dict[str, Any]:
    if os.path.exists(SETUP_CONFIG_FILE):
        try:
            with open(SETUP_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_server_setup(guild_id: str, channel_id: int, message_id: int, preset: str):
    setups = load_server_setups()
    setups[str(guild_id)] = {
        "channel_id": channel_id,
        "message_id": message_id,
        "preset": preset,
        "updated_at": datetime.now().isoformat()
    }
    with open(SETUP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(setups, f, indent=4)

def fetch_latest_build_number() -> int:
    FALLBACK = 504649
    try:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        r = requests.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=10)
        if r.status_code != 200:
            return FALLBACK
        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', r.text)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', r.text)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]
        for asset_hash in scripts[-5:]:
            try:
                ar = requests.get(f"https://discord.com/assets/{asset_hash}.js", headers={"User-Agent": ua}, timeout=10)
                m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar.text)
                if m:
                    return int(m.group(1))
            except Exception:
                continue
        return FALLBACK
    except Exception:
        return FALLBACK

def make_super_properties(build_number: Optional[int] = None) -> str:
    profile = random.choice(DEVICE_PROFILES)
    cb_num = build_number if build_number else profile["native_build_number"]
    obj = {
        "os": profile["os"], "browser": profile["browser"], "release_channel": "stable",
        "client_version": profile["client_version"], "os_version": profile["os_version"],
        "os_arch": profile["os_arch"], "app_arch": profile["app_arch"],
        "system_locale": "vi-VN", "browser_user_agent": profile["ua"],
        "browser_version": profile["browser_version"], "client_build_number": cb_num,
        "native_build_number": profile["native_build_number"], "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()

class DiscordAPI:
    def __init__(self, token: str, build_number: Optional[int] = None):
        self.token = token
        self.session = requests.Session()
        profile = random.choice(DEVICE_PROFILES)
        self.session.headers.update({
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
            "User-Agent": profile["ua"],
            "X-Super-Properties": make_super_properties(build_number),
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
# MODULE 2: QUEST HELPERS & PARSERS
# ==============================================================================
def _get(d: Optional[dict], *keys):
    if d is None: return None
    for k in keys:
        if k in d: return d[k]
    return None

def get_task_config(quest: dict) -> Optional[dict]:
    cfg = quest.get("config", {})
    return _get(cfg, "taskConfig", "task_config", "taskConfigV2", "task_config_v2")

def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name")
    if name: return name.strip()
    game = _get(msgs, "gameTitle", "game_title")
    if game: return game.strip()
    app_name = cfg.get("application", {}).get("name")
    if app_name: return app_name
    return f"Quest#{quest.get('id', '?')}"

def get_expires_at(quest: dict) -> Optional[str]:
    return _get(quest.get("config", {}), "expiresAt", "expires_at")

def get_user_status(quest: dict) -> dict:
    us = _get(quest, "userStatus", "user_status")
    return us if isinstance(us, dict) else {}

def is_completable(quest: dict) -> bool:
    expires = get_expires_at(quest)
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt <= datetime.now(timezone.utc):
                return False
        except Exception:
            pass
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return False
    tasks_dict = tc["tasks"]
    return any(tasks_dict.get(t) is not None for t in SUPPORTED_TASKS)

def is_enrolled(quest: dict) -> bool:
    return bool(_get(get_user_status(quest), "enrolledAt", "enrolled_at"))

def is_completed(quest: dict) -> bool:
    return bool(_get(get_user_status(quest), "completedAt", "completed_at"))

def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None: return t
    return None

def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    task_type = get_task_type(quest)
    if not tc or not task_type: return 0
    return tc["tasks"][task_type].get("target", 0)

def get_seconds_done(quest: dict) -> float:
    task_type = get_task_type(quest)
    if not task_type: return 0
    progress = get_user_status(quest).get("progress", {}) or {}
    return progress.get(task_type, {}).get("value", 0)

def parse_quest_orbs(quest: dict) -> int:
    cfg = quest.get("config", {})
    rewards_config = cfg.get("rewardsConfig", {}) or cfg.get("rewards_config", {})
    rewards = rewards_config.get("rewards", []) or cfg.get("rewards", []) or []
    for rw in rewards:
        if isinstance(rw, dict):
            if "orb_quantity" in rw: return rw["orb_quantity"]
            if "amount" in rw and rw.get("type") in [0, "ORBS", "ORB"]: return rw["amount"]
            r_msgs = rw.get("messages", {}) or {}
            r_name = str(r_msgs.get("name", "") or r_msgs.get("title", ""))
            nums = re.findall(r'\d+', r_name)
            if nums and ("orb" in r_name.lower() or rw.get("type") == 0): return int(nums[0])
    t_type = get_task_type(quest)
    return 200 if t_type in ["WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"] else 700


# ==============================================================================
# MODULE 3: BADGE & GAME FARMER (TỐC ĐỘ ĐỔI GAME THÔNG MINH THEO SỐ GAME)
# ==============================================================================
class BadgeFarmSession:
    def __init__(self, user_id: int, token: str, max_games: int, target_hours: int):
        self.user_id = user_id
        self.token = token
        self.max_games = max(1, min(max_games, 100, len(PC_GAMES_110)))
        self.target_hours = max(1, min(target_hours, 5000))
        self.start_time = time.time()
        self.is_running = True
        self.is_paused = False
        self.current_game = "Đang khởi tạo..."
        self.games_rotated = 0
        self.pool = PC_GAMES_110[:self.max_games].copy()
        random.shuffle(self.pool)
        self.ws = None
        self._my_sid = None

        # Tính toán tốc độ đổi game thông minh dựa trên tổng số game cày
        self.min_interval, self.max_interval, self.speed_label = self._calc_rotation_speed()

    def _calc_rotation_speed(self):
        """
        Tự động điều chỉnh tốc độ xoay vòng game:
        - Cày nhiều game (>= 60 game): Xoay nhanh (2.5 - 4.5 phút/game) để sớm phủ đủ danh sách game.
        - Cày trung bình (20 - 59 game): Xoay vừa phải (5 - 8 phút/game) tự nhiên.
        - Cày ít game (< 20 game): Giữ mỗi game lâu (10 - 15 phút/game) chống spam presence.
        """
        if self.max_games >= 60:
            return (150, 270, "⚡ Nhanh (2.5 - 4.5p/game)")
        elif self.max_games >= 20:
            return (300, 480, "⚖️ Vừa phải (5 - 8p/game)")
        else:
            return (600, 900, "🐢 Chậm & Sâu (10 - 15p/game)")

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
            if (time.time() - self.start_time) >= (self.target_hours * 3600):
                self.is_running = False
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
                                wait_sec = random.randint(self.min_interval, self.max_interval)
                                await asyncio.sleep(wait_sec)
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
# MODULE 4: AUTO-QUEST ENGINE (CHỐNG TRÙNG LẶP & BẢNG TIẾN TRÌNH REAL-TIME)
# ==============================================================================
def create_quest_status_embed(user_id: int) -> discord.Embed:
    if user_id not in active_quests:
        return discord.Embed(
            title="🛡️ TIẾN TRÌNH AUTO-QUEST",
            description="Hiện tại bạn **không có tiến trình cày Quest** nào đang chạy.",
            color=discord.Color.from_rgb(43, 45, 49)
        )

    data = active_quests[user_id]
    quests = data["quests"]

    embed = discord.Embed(
        title="🚀 TIẾN TRÌNH AUTO-QUEST — ĐANG CÀY NGẦM",
        color=discord.Color.from_rgb(46, 204, 113)
    )
    embed.add_field(
        name="👤 Tài khoản",
        value=f"**{data['user_name']}** • 🔮 Tổng dự kiến: **{data['total_orbs']} Orbs**",
        inline=False
    )

    total = len(quests)
    completed = sum(1 for q in quests if q["done_seconds"] >= q["duration"])
    avg_progress = int(sum((min(q["done_seconds"], q["duration"]) / max(q["duration"], 1)) * 100 for q in quests) / total) if total > 0 else 0
    bar = "█" * (avg_progress // 10) + "░" * (10 - (avg_progress // 10))

    embed.add_field(
        name="📊 Tổng quan tiến độ",
        value=f"`[{bar}]` **{avg_progress}%** ({completed}/{total} nhiệm vụ hoàn tất)",
        inline=False
    )

    for idx, q in enumerate(quests, 1):
        icon = "🎬" if "VIDEO" in str(q["type"]).upper() else "🎮"
        dur = max(q["duration"], 1)
        done = min(q["done_seconds"], dur)

        pct = min(100, int((done / dur) * 100))
        filled = pct // 20
        q_bar = "█" * filled + "░" * (5 - filled)

        done_m, done_s = divmod(int(done), 60)
        tot_m, tot_s = divmod(int(dur), 60)

        status_tag = "✅ Xong" if done >= dur else "⏳ Cày"
        field_name = f"#{idx} {q['title'][:24]} ({status_tag})"
        field_val = f"{icon} `{q['type']}` • 🔮 **{q['orb']} Orbs**\n`[{q_bar}]` **{pct}%** (`{done_m}p{done_s:02d}s/{tot_m}p{tot_s:02d}s`)"
        embed.add_field(name=field_name, value=field_val, inline=True)

    embed.set_footer(text=f"Đồng bộ lúc {datetime.now().strftime('%H:%M:%S')} • Anti-Ban Multi-Profile")
    return embed


class QuestControlView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Làm mới tiến trình", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="qctrl:refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bảng điều khiển này không phải của bạn.", ephemeral=True)
            return
        if self.user_id not in active_quests:
            await interaction.response.edit_message(
                content="✅ **Phiên cày Quest đã kết thúc hoặc đã được hủy.**",
                embed=None,
                view=None
            )
            return
        await interaction.response.edit_message(embed=create_quest_status_embed(self.user_id), view=self)

    @discord.ui.button(label="Hủy cày Quest", style=discord.ButtonStyle.danger, emoji="🛑", custom_id="qctrl:cancel")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bảng điều khiển này không phải của bạn.", ephemeral=True)
            return
        if self.user_id in active_quests:
            active_quests.pop(self.user_id, None)
            dashboard_messages.pop(self.user_id, None)
            await interaction.response.edit_message(
                content="🛑 **Đã hủy tiến trình cày Quest thành công!**",
                embed=None,
                view=None
            )
        else:
            await interaction.response.edit_message(
                content="ℹ️ Không có tiến trình cày Quest nào đang chạy.",
                embed=None,
                view=None
            )


def run_single_quest_sync(p_quest: dict, api: DiscordAPI, user_id: int):
    quest = p_quest["raw_quest"]
    qid = quest["id"]
    task_type = p_quest["type"]
    seconds_needed = p_quest["duration"]
    seconds_done = p_quest["done_seconds"]

    if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
        speed = random.uniform(5.5, 7.5)
        while seconds_done < seconds_needed:
            if user_id not in active_quests:
                return
            timestamp = seconds_done + speed
            try:
                r = api.post(f"/quests/{qid}/video-progress", {
                    "timestamp": min(seconds_needed, timestamp + random.uniform(0.1, 0.9))
                })
                if r.status_code == 401:
                    remove_token_from_file(str(user_id))
                    active_quests.pop(user_id, None)
                    return
                elif r.status_code == 200:
                    body = r.json()
                    if body.get("completed_at"):
                        p_quest["done_seconds"] = seconds_needed
                        p_quest["status"] = "🎁 Đã xong"
                        return
                    seconds_done = min(seconds_needed, timestamp)
                    p_quest["done_seconds"] = seconds_done
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 5)
                    time.sleep(retry_after + random.uniform(2.0, 4.0))
                    continue
            except Exception:
                pass
            if timestamp >= seconds_needed:
                break
            time.sleep(random.uniform(0.9, 1.4))
        try:
            api.post(f"/quests/{qid}/video-progress", {"timestamp": seconds_needed})
        except Exception:
            pass

    elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
        pid = random.randint(1000, 30000)
        while seconds_done < seconds_needed:
            if user_id not in active_quests:
                return
            try:
                r = api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": f"call:0:{pid}",
                    "terminal": False,
                })
                if r.status_code == 401:
                    remove_token_from_file(str(user_id))
                    active_quests.pop(user_id, None)
                    return
                elif r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and task_type in progress_data:
                        seconds_done = progress_data[task_type].get("value", seconds_done)
                    p_quest["done_seconds"] = seconds_done
                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        p_quest["done_seconds"] = seconds_needed
                        p_quest["status"] = "🎁 Đã xong"
                        return
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    time.sleep(retry_after + random.uniform(3.0, 5.0))
                    continue
            except Exception:
                pass
            time.sleep(random.uniform(18.0, 24.0))
        try:
            api.post(f"/quests/{qid}/heartbeat", {"stream_key": f"call:0:{pid}", "terminal": True})
        except Exception:
            pass

    elif task_type == "PLAY_ACTIVITY":
        stream_key = "call:0:1"
        while seconds_done < seconds_needed:
            if user_id not in active_quests:
                return
            try:
                r = api.post(f"/quests/{qid}/heartbeat", {"stream_key": stream_key, "terminal": False})
                if r.status_code == 401:
                    remove_token_from_file(str(user_id))
                    active_quests.pop(user_id, None)
                    return
                elif r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and "PLAY_ACTIVITY" in progress_data:
                        seconds_done = progress_data["PLAY_ACTIVITY"].get("value", seconds_done)
                    p_quest["done_seconds"] = seconds_done
                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        p_quest["done_seconds"] = seconds_needed
                        p_quest["status"] = "🎁 Đã xong"
                        break
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    time.sleep(retry_after + random.uniform(3.0, 5.0))
                    continue
            except Exception:
                pass
            time.sleep(random.uniform(18.0, 24.0))
        try:
            api.post(f"/quests/{qid}/heartbeat", {"stream_key": stream_key, "terminal": True})
        except Exception:
            pass

    p_quest["done_seconds"] = seconds_needed
    p_quest["status"] = "🎁 Đã xong"


async def run_quest_farming_background(bot: commands.Bot, user_id: int):
    if user_id not in active_quests:
        return
    data = active_quests[user_id]
    api = data["api"]

    while user_id in active_quests:
        quests_to_run = [q for q in data["quests"] if q["done_seconds"] < q["duration"]]
        if not quests_to_run:
            break
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_QUESTS) as executor:
            futures = [loop.run_in_executor(executor, run_single_quest_sync, q, api, user_id) for q in quests_to_run]
            await asyncio.gather(*futures, return_exceptions=True)
        await asyncio.sleep(random.uniform(2.0, 4.0))

    if user_id in active_quests:
        try:
            user = await bot.fetch_user(user_id)
            await user.send(f"🎉 **Chúc mừng {data['user_name']}!** Đã cày hoàn thành toàn bộ nhiệm vụ Quest an toàn.")
        except Exception:
            pass
        await asyncio.sleep(30)
        active_quests.pop(user_id, None)
        dashboard_messages.pop(user_id, None)


async def start_or_show_autoquest(interaction: discord.Interaction, user_token: str):
    """
    Khởi động AutoQuest thông minh:
    - Nếu đang chạy rồi -> Hiển thị ngay bảng tiến trình + nút Hủy (chống chạy lặp lại).
    - Nếu chưa chạy -> Quét Quest, nhận nhiệm vụ, bật tiến trình và trả về bảng tiến trình.
    """
    user_id = interaction.user.id

    # Chống lặp lại tiến trình nếu đang cày
    if user_id in active_quests:
        embed = create_quest_status_embed(user_id)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                content="⚠️ **Bạn đang có một phiên cày Quest chạy ngầm!** Dưới đây là tiến trình hiện tại:",
                embed=embed,
                view=QuestControlView(user_id),
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                content="⚠️ **Bạn đang có một phiên cày Quest chạy ngầm!** Dưới đây là tiến trình hiện tại:",
                embed=embed,
                view=QuestControlView(user_id),
                ephemeral=True
            )
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    loop = asyncio.get_running_loop()
    build_number = await loop.run_in_executor(None, fetch_latest_build_number)
    api = DiscordAPI(user_token, build_number)

    r = await loop.run_in_executor(None, api.get, "/users/@me")
    if r.status_code != 200:
        remove_token_from_file(str(user_id))
        await interaction.followup.send("❌ **Token đã hết hạn hoặc không hợp lệ!** Vui lòng cập nhật lại Token.", ephemeral=True)
        return

    user_data = r.json()
    display_name = user_data.get("global_name") or user_data.get("username")

    def fetch_and_enroll():
        q_res = api.get("/quests/@me")
        if q_res.status_code != 200:
            return []
        q_json = q_res.json()
        raw_quests = q_json.get("quests", []) if isinstance(q_json, dict) else q_json

        unaccepted = [q for q in raw_quests if not is_enrolled(q) and not is_completed(q) and is_completable(q)]
        for q in unaccepted:
            qid = q.get("id")
            api.post(f"/quests/{qid}/enroll", {
                "location": 11,
                "is_targeted": False,
                "metadata_raw": None,
                "metadata_sealed": None,
            })
            time.sleep(random.uniform(0.8, 1.4))

        if unaccepted:
            q_res = api.get("/quests/@me")
            if q_res.status_code == 200:
                q_json = q_res.json()
                raw_quests = q_json.get("quests", []) if isinstance(q_json, dict) else q_json

        return [q for q in raw_quests if is_enrolled(q) and not is_completed(q) and is_completable(q)]

    actionable = await loop.run_in_executor(None, fetch_and_enroll)
    if not actionable:
        await interaction.followup.send("⚠️ **Tài khoản hiện không có Quest khả dụng nào hoặc đã làm xong hết!**", ephemeral=True)
        return

    parsed_quests = []
    for q in actionable:
        parsed_quests.append({
            "raw_quest": q,
            "id": q.get("id"),
            "title": get_quest_name(q),
            "type": get_task_type(q),
            "orb": parse_quest_orbs(q),
            "duration": get_seconds_needed(q),
            "done_seconds": get_seconds_done(q),
            "status": "⏳ Đang cày..."
        })

    save_token_to_file(str(user_id), user_token)

    active_quests[user_id] = {
        "user_name": display_name,
        "token": user_token,
        "api": api,
        "quests": parsed_quests,
        "start_time": time.time(),
        "total_orbs": sum(p["orb"] for p in parsed_quests),
    }

    embed = create_quest_status_embed(user_id)
    msg = await interaction.followup.send(
        content=f"🚀 **Đã nhận {len(parsed_quests)} Quest(s) và bắt đầu cày ngầm!**",
        embed=embed,
        view=QuestControlView(user_id),
        ephemeral=True
    )
    dashboard_messages[user_id] = msg
    asyncio.create_task(run_quest_farming_background(interaction.client, user_id))


# ==============================================================================
# MODULE 5: BADGE FARMER PROGRESS VIEW & MODALS
# ==============================================================================
class BadgeProgressView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Xem Tiến Trình", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="badgeprog:view")
    async def view_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bảng này không phải của bạn.", ephemeral=True)
            return
        ses: BadgeFarmSession = active_game_sessions.get(self.user_id)
        if not ses or not ses.is_running:
            await interaction.response.send_message("❌ Tiến trình cày Game/Badge đã dừng!", ephemeral=True)
            return
        elapsed_h = round((time.time() - ses.start_time) / 3600, 2)
        embed = discord.Embed(title="🎮 TRẠNG THÁI BADGE & GAME FARMER", color=discord.Color.from_rgb(43, 45, 49))
        embed.add_field(name="Trạng thái", value="⏸️ Đang tạm dừng (Bạn đang online)" if ses.is_paused else "🟢 Đang cày ngầm", inline=False)
        embed.add_field(name="Game hiện tại", value=f"`{ses.current_game}`", inline=True)
        embed.add_field(name="Tốc độ xoay game", value=ses.speed_label, inline=True)
        embed.add_field(name="Số game đã xoay", value=f"**{ses.games_rotated}** / {ses.max_games} game", inline=True)
        embed.add_field(name="Thời gian chạy", value=f"⏱️ **{elapsed_h} / {ses.target_hours} giờ**", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Hủy Cày Game", style=discord.ButtonStyle.danger, emoji="⏹️", custom_id="badgeprog:stop")
    async def stop_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Bảng này không phải của bạn.", ephemeral=True)
            return
        ses = active_game_sessions.pop(self.user_id, None)
        if ses:
            ses.stop()
            await interaction.response.edit_message(content="🛑 **Đã dừng hoàn toàn tiến trình cày Game/Badge!**", embed=None, view=None)
        else:
            await interaction.response.send_message("❌ Không tìm thấy tiến trình cày game.", ephemeral=True)


class BadgeFarmModal(discord.ui.Modal, title="🎮 Cày Game & Badge Tùy Chỉnh"):
    token_input = discord.ui.TextInput(label="Discord User Token", placeholder="Dán token (để trống nếu đã lưu)...", style=discord.TextStyle.short, required=False)
    games_input = discord.ui.TextInput(label="Số game muốn cày (1 - 100)", default="100", style=discord.TextStyle.short, required=True, max_length=3)
    hours_input = discord.ui.TextInput(label="Số giờ muốn cày (1 - 5000)", default="1000", style=discord.TextStyle.short, required=True, max_length=4)

    async def on_submit(self, interaction: discord.Interaction):
        raw_tok = self.token_input.value.strip()
        token = raw_tok if raw_tok else load_saved_tokens().get(str(interaction.user.id), "")
        if not token:
            await interaction.response.send_message("❌ **Bạn chưa nhập Token hoặc chưa lưu Token trước đó!**", ephemeral=True)
            return

        try: max_g = max(1, min(int(self.games_input.value.strip()), 100))
        except ValueError: max_g = 100
        try: max_h = max(1, min(int(self.hours_input.value.strip()), 5000))
        except ValueError: max_h = 1000

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
            description=(
                f"🟢 Đang treo **{max_g} game** trong tối đa **{max_h} giờ**.\n"
                f"⚙️ **Chế độ xoay vòng tự động:** {session.speed_label}"
            ),
            color=discord.Color.from_rgb(43, 45, 49)
        )
        await interaction.response.send_message(embed=embed, view=BadgeProgressView(interaction.user.id), ephemeral=True)


class TokenModal(discord.ui.Modal, title="🔐 CẬP NHẬT USER TOKEN"):
    token_input = discord.ui.TextInput(label="User Token Discord", placeholder="Dán token vào đây...", style=discord.TextStyle.short, required=True)

    def __init__(self, auto_start_quest: bool = False):
        super().__init__()
        self.auto_start_quest = auto_start_quest

    async def on_submit(self, interaction: discord.Interaction):
        token = self.token_input.value.strip()
        api = DiscordAPI(token)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, api.get, "/users/@me")
        if res.status_code == 200:
            save_token_to_file(str(interaction.user.id), token)
            if self.auto_start_quest:
                await start_or_show_autoquest(interaction, token)
            else:
                await interaction.response.send_message("✅ **Lưu Token thành công!** Bạn có thể sử dụng các chức năng ngay.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ **Token không hợp lệ hoặc đã hết hạn!**", ephemeral=True)


# ==============================================================================
# MODULE 6: HYPESQUAD SELECTOR VIEW (CÓ SẴN ICON NHÀ TRÊN NÚT & TRONG BẢNG)
# ==============================================================================
HYPESQUAD_HOUSES = {
    1: {"name": "HypeSquad Bravery", "emoji": "<:hypesquad_2:1571114614975107144>", "desc": "Dũng cảm & Nhiệt huyết"},
    2: {"name": "HypeSquad Brilliance", "emoji": "<:hypesquad_1:1571114521601245224>", "desc": "Sáng suốt & Kỷ luật"},
    3: {"name": "HypeSquad Balance", "emoji": "<:hypesquad:1571114469130633316>", "desc": "Cân bằng & Hài hòa"},
}

class HypeSquadView(discord.ui.View):
    def __init__(self, token: str):
        super().__init__(timeout=60)
        self.token = token

    async def _select(self, interaction: discord.Interaction, house_id: int):
        house = HYPESQUAD_HOUSES[house_id]
        api = DiscordAPI(self.token)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, lambda: api.post("/hypesquad/online", {"house_id": house_id}))
        if res.status_code in (200, 204):
            embed = discord.Embed(
                title="🎉 ĐỔI NHÀ HYPESQUAD THÀNH CÔNG",
                description=f"Tài khoản của bạn đã chuyển sang nhà {house['emoji']} **{house['name']}**!",
                color=discord.Color.from_rgb(43, 45, 49)
            )
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.edit_message(content="❌ Lỗi khi đổi HypeSquad (Token hết hạn hoặc bị giới hạn)!", embed=None, view=None)

    @discord.ui.button(label="Bravery", style=discord.ButtonStyle.secondary, emoji="<:hypesquad_2:1571114614975107144>")
    async def b1(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 1)

    @discord.ui.button(label="Brilliance", style=discord.ButtonStyle.secondary, emoji="<:hypesquad_1:1571114521601245224>")
    async def b2(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 2)

    @discord.ui.button(label="Balance", style=discord.ButtonStyle.secondary, emoji="<:hypesquad:1571114469130633316>")
    async def b3(self, i: discord.Interaction, b: discord.ui.Button): await self._select(i, 3)


# ==============================================================================
# MODULE 7: COMPONENTS V2 DASHBOARD (CONTAINER + ACTIONROW BÊN TRONG KHUNG)
# ==============================================================================
SETUP_PRESETS = {
    "all": {
        "label": "🌟 Tất Cả Chức Năng (Full Menu)",
        "desc": "Bao gồm: AutoQuest, Tiến Trình, Hủy Quest, Badge Farmer, HypeSquad, Token",
        "features": ["autoquest", "progress", "cancel_quest", "badge", "hypesquad", "token"],
        "lines": [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Theo dõi phần trăm Quest theo thời gian thực",
            "**3. Hủy Quest** — Dừng ngay phiên cày Quest đang chạy",
            "**4. Badge Farmer** — Cày 100 game / 5000 giờ tự chỉnh tốc độ",
            "**5. HypeSquad** — Đổi nhà <:hypesquad_2:1571114614975107144> <:hypesquad_1:1571114521601245224> <:hypesquad:1571114469130633316> tức thì"
        ]
    },
    "quest_only": {
        "label": "🤖 Chuyên Cày Quest (AutoQuest + Tiến Trình + Hủy + Token)",
        "desc": "Gọn nhẹ cho server chỉ cần cày Quest Orb",
        "features": ["autoquest", "progress", "cancel_quest", "token"],
        "lines": [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Xem tiến độ & thời gian còn lại",
            "**3. Hủy Quest** — Hủy tiến trình đang cày tức thì",
            "**4. Token** — Lưu hoặc cập nhật User Token bảo mật"
        ]
    },
    "quest_badge": {
        "label": "🎮 Quest & Badge Farmer (AutoQuest + Tiến Trình + Hủy + Badge)",
        "desc": "Dành cho server cày Quest và treo giờ chơi Game",
        "features": ["autoquest", "progress", "cancel_quest", "badge"],
        "lines": [
            "**1. AutoQuest** — Tự động nhận & cày Discord Quest",
            "**2. Tiến Trình** — Theo dõi tiến độ Quest real-time",
            "**3. Hủy Quest** — Dừng phiên cày Quest đang chạy",
            "**4. Badge Farmer** — Farm badge Game Time / Đa dạng game"
        ]
    },
    "social_badge": {
        "label": "🏠 Badge & HypeSquad (Badge Farmer + HypeSquad + Token)",
        "desc": "Dành cho server trang trí profile & cày giờ chơi game",
        "features": ["badge", "hypesquad", "token"],
        "lines": [
            "**1. Badge Farmer** — Cày tới 100 game / 5000 giờ",
            "**2. HypeSquad** — Đổi nhà <:hypesquad_2:1571114614975107144> <:hypesquad_1:1571114521601245224> <:hypesquad:1571114469130633316> nhanh",
            "**3. Token** — Quản lý User Token an toàn"
        ]
    }
}


class DynamicDashboardView(discord.ui.LayoutView):
    """
    Sử dụng Discord Components V2 (LayoutView + Container + ActionRow)
    để đặt toàn bộ nút bấm NẰM BÊN TRONG khung giống hệt ảnh mẫu!
    """
    def __init__(self, preset_key: str = "all"):
        super().__init__(timeout=None)
        if preset_key not in SETUP_PRESETS:
            preset_key = "all"
        self.preset_key = preset_key
        preset = SETUP_PRESETS[preset_key]

        container = discord.ui.Container(accent_color=discord.Color.from_rgb(255, 255, 255))

        header_text = (
            "## 🪽 TỔNG HỢP CHỨC NĂNG 🪽\n"
            "**Danh sách chức năng:**\n"
            + "\n".join(preset["lines"])
        )
        container.add_item(discord.ui.TextDisplay(header_text))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # Tạo danh sách nút theo preset (tối đa 5 nút / 1 ActionRow)
        buttons: List[discord.ui.Button] = []
        feats = preset["features"]

        if "autoquest" in feats:
            b = discord.ui.Button(label="AutoQuest", style=discord.ButtonStyle.secondary, custom_id=f"v2:{preset_key}:autoquest")
            b.callback = self.on_autoquest
            buttons.append(b)

        if "progress" in feats:
            b = discord.ui.Button(label="Tiến Trình", style=discord.ButtonStyle.secondary, custom_id=f"v2:{preset_key}:progress")
            b.callback = self.on_progress
            buttons.append(b)

        if "cancel_quest" in feats:
            b = discord.ui.Button(label="Hủy Quest", style=discord.ButtonStyle.secondary, custom_id=f"v2:{preset_key}:cancel_quest")
            b.callback = self.on_cancel_quest
            buttons.append(b)

        if "badge" in feats:
            b = discord.ui.Button(label="Badge Farmer", style=discord.ButtonStyle.secondary, custom_id=f"v2:{preset_key}:badge")
            b.callback = self.on_badge
            buttons.append(b)

        if "hypesquad" in feats:
            b = discord.ui.Button(
                label="HypeSquad",
                style=discord.ButtonStyle.secondary,
                emoji="<:hypesquad:1571114469130633316>",
                custom_id=f"v2:{preset_key}:hypesquad"
            )
            b.callback = self.on_hypesquad
            buttons.append(b)

        if "token" in feats:
            b = discord.ui.Button(label="Token", style=discord.ButtonStyle.secondary, custom_id=f"v2:{preset_key}:token")
            b.callback = self.on_token
            buttons.append(b)

        # Chia thành các hàng (mỗi ActionRow tối đa 5 nút để luôn hiển thị đẹp)
        for i in range(0, len(buttons), 5):
            row = discord.ui.ActionRow(*buttons[i:i+5])
            container.add_item(row)

        self.add_item(container)

    def _get_token(self, uid: int) -> Optional[str]:
        return load_saved_tokens().get(str(uid))

    async def on_autoquest(self, interaction: discord.Interaction):
        token = self._get_token(interaction.user.id)
        if not token:
            await interaction.response.send_modal(TokenModal(auto_start_quest=True))
            return
        await start_or_show_autoquest(interaction, token)

    async def on_progress(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in active_quests:
            await interaction.response.send_message(
                "ℹ️ Bạn hiện **không có tiến trình cày Quest nào** đang chạy. Bấm **AutoQuest** để bắt đầu!",
                ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=create_quest_status_embed(uid),
            view=QuestControlView(uid),
            ephemeral=True
        )

    async def on_cancel_quest(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid in active_quests:
            active_quests.pop(uid, None)
            dashboard_messages.pop(uid, None)
            await interaction.response.send_message("🛑 **Đã hủy tiến trình cày Quest thành công!**", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Hiện không có tiến trình cày Quest nào đang chạy.", ephemeral=True)

    async def on_badge(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BadgeFarmModal())

    async def on_hypesquad(self, interaction: discord.Interaction):
        token = self._get_token(interaction.user.id)
        if not token:
            await interaction.response.send_modal(TokenModal(auto_start_quest=False))
            return
        embed = discord.Embed(
            title="🏡 CHỌN NHÀ HYPESQUAD",
            description=(
                "Chọn nhà HypeSquad bạn muốn nhận huy hiệu bên dưới:\n"
                "• <:hypesquad_2:1571114614975107144> **Bravery** — Dũng cảm & Nhiệt huyết\n"
                "• <:hypesquad_1:1571114521601245224> **Brilliance** — Sáng suốt & Kỷ luật\n"
                "• <:hypesquad:1571114469130633316> **Balance** — Cân bằng & Hài hòa"
            ),
            color=discord.Color.from_rgb(43, 45, 49)
        )
        await interaction.response.send_message(embed=embed, view=HypeSquadView(token), ephemeral=True)

    async def on_token(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TokenModal(auto_start_quest=False))


# ==============================================================================
# MODULE 8: COG CHÍNH & LỆNH SLASH (TIẾN TRÌNH, HỦY QUEST, SETUP ĐA CHẾ ĐỘ)
# ==============================================================================
class QuestBotCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._views_registered = False
        self.dashboard_loop.start()

    def cog_unload(self):
        self.dashboard_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self._views_registered:
            # Đăng ký toàn bộ các loại LayoutView để nút bấm hoạt động vĩnh viễn sau restart
            for preset_key in SETUP_PRESETS.keys():
                self.bot.add_view(DynamicDashboardView(preset_key))
            self._views_registered = True

            saved_setups = load_server_setups()
            logger.info(f"✅ Đã khôi phục {len(SETUP_PRESETS)} mẫu giao diện V2 cho {len(saved_setups)} server đã lưu!")

    @tasks.loop(seconds=6)
    async def dashboard_loop(self):
        for uid, msg in list(dashboard_messages.items()):
            try:
                if uid in active_quests:
                    await msg.edit(embed=create_quest_status_embed(uid), view=QuestControlView(uid))
                else:
                    dashboard_messages.pop(uid, None)
            except Exception:
                dashboard_messages.pop(uid, None)

    @app_commands.command(name="setup_menu", description="[ADMIN] Gửi bảng điều khiển chức năng (Tự nhớ cấu hình cho server)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(che_do="Chọn loại menu chức năng phù hợp cho server này")
    @app_commands.choices(che_do=[
        app_commands.Choice(name="🌟 Tất Cả Chức Năng (Full Menu)", value="all"),
        app_commands.Choice(name="🤖 Chuyên Cày Quest (AutoQuest + Tiến Trình + Hủy + Token)", value="quest_only"),
        app_commands.Choice(name="🎮 Quest & Badge Farmer (AutoQuest + Tiến Trình + Hủy + Badge)", value="quest_badge"),
        app_commands.Choice(name="🏠 Badge & HypeSquad (Badge Farmer + HypeSquad + Token)", value="social_badge"),
    ])
    async def setup_menu(self, interaction: discord.Interaction, che_do: app_commands.Choice[str]):
        preset_key = che_do.value
        view = DynamicDashboardView(preset_key)
        sent_msg = await interaction.channel.send(view=view)

        if interaction.guild_id:
            save_server_setup(str(interaction.guild_id), interaction.channel_id, sent_msg.id, preset_key)

        await interaction.response.send_message(
            f"✅ Đã thiết lập bảng **{che_do.name}** thành công! Cấu hình đã được lưu tự động cho server này (tự khôi phục sau khi reset bot).",
            ephemeral=True
        )

    @app_commands.command(name="tien_trinh", description="[QUEST] Xem tiến trình cày Quest hiện tại của bạn")
    async def cmd_tien_trinh(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid not in active_quests:
            await interaction.response.send_message(
                "ℹ️ Bạn hiện **không có tiến trình cày Quest nào** đang chạy.",
                ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=create_quest_status_embed(uid),
            view=QuestControlView(uid),
            ephemeral=True
        )
        dashboard_messages[uid] = await interaction.original_response()

    @app_commands.command(name="huy_quest", description="[QUEST] Hủy ngay tiến trình cày Quest đang chạy")
    async def cmd_huy_quest(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid in active_quests:
            active_quests.pop(uid, None)
            dashboard_messages.pop(uid, None)
            await interaction.response.send_message("🛑 **Đã hủy tiến trình cày Quest thành công!**", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Không có tiến trình cày Quest nào đang chạy.", ephemeral=True)


async def setup(bot: commands.Bot):
    for preset_key in SETUP_PRESETS.keys():
        bot.add_view(DynamicDashboardView(preset_key))
    await bot.add_cog(QuestBotCog(bot))
