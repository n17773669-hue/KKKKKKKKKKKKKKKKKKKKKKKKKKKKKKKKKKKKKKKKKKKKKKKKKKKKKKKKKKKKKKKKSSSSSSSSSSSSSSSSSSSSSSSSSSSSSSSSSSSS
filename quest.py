"""
================================================================================
DISCORD ULTIMATE QUEST ENGINE & SPOOFER BOT — FULL MAIN.PY CORE INTEGRATION
Author: Personal Collaborator / Custom Build for Server (Anti-Ban Enhanced)
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
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("UltimateQuestEngine")

TOKEN_FILE = "tokens.json"
API_BASE = "https://discord.com/api/v9"
MAX_CONCURRENT_QUESTS = 5
AUTO_ACCEPT = True

SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]

# Quản lý Token trên ổ đĩa
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
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=4)
    except Exception:
        pass

def remove_token_from_file(user_id: str):
    tokens = load_saved_tokens()
    if str(user_id) in tokens:
        del tokens[str(user_id)]
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(tokens, f, indent=4)
        except Exception:
            pass

active_quests: Dict[int, Dict[str, Any]] = {}
dashboard_messages: Dict[int, discord.Message] = {}

# ==============================================================================
# MODULE 1: ADVANCED ANTI-BAN SPOOFING & MULTI-PROFILE BUILD GENERATOR
# ==============================================================================

# Danh sách các cấu hình giả lập thiết bị đa dạng (Windows, Mac, Client versions)
DEVICE_PROFILES = [
    {
        "os": "Windows", "os_version": "10.0.26100", "os_arch": "x64", "app_arch": "x64",
        "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    },
    {
        "os": "Windows", "os_version": "10.0.19045", "os_arch": "x64", "app_arch": "x64",
        "client_version": "1.0.9160", "browser_version": "32.2.1", "native_build_number": 59000,
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9160 Chrome/128.0.6613.119 Electron/32.2.1 Safari/537.36"
    },
    {
        "os": "Mac OS X", "os_version": "14.5.0", "os_arch": "x64", "app_arch": "x64",
        "client_version": "1.0.9175", "browser_version": "32.2.7", "native_build_number": 59498,
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36"
    }
]

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

        if not scripts:
            return FALLBACK

        for asset_hash in scripts[-5:]:
            try:
                ar = requests.get(
                    f"https://discord.com/assets/{asset_hash}.js",
                    headers={"User-Agent": ua}, timeout=10
                )
                m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar.text)
                if m:
                    return int(m.group(1))
            except Exception:
                continue

        return FALLBACK
    except Exception:
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    # Chọn ngẫu nhiên một profile thiết bị mỗi lần khởi tạo để tránh lộ dấu vân tay tĩnh
    profile = random.choice(DEVICE_PROFILES)
    obj = {
        "os": profile["os"],
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": profile["client_version"],
        "os_version": profile["os_version"],
        "os_arch": profile["os_arch"],
        "app_arch": profile["app_arch"],
        "system_locale": random.choice(["en-US", "en-GB", "vi-VN"]),
        "browser_user_agent": profile["ua"],
        "browser_version": profile["browser_version"],
        "client_build_number": build_number,
        "native_build_number": profile["native_build_number"],
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


class DiscordAPI:
    def __init__(self, token: str, build_number: int):
        self.token = token
        self.session = requests.Session()
        profile = random.choice(DEVICE_PROFILES)
        ua = profile["ua"]
        sp = make_super_properties(build_number)
        self.session.headers.update({
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ua,
            "X-Super-Properties": sp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{API_BASE}{path}"
        return self.session.get(url, **kwargs)

    def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> requests.Response:
        url = f"{API_BASE}{path}"
        return self.session.post(url, json=payload, **kwargs)


# ==============================================================================
# MODULE 2: QUEST HELPERS (BÊ 100% NGUYÊN BẢN TỪ MAIN.PY)
# ==============================================================================

def _get(d: Optional[dict], *keys):
    if d is None:
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None

def get_task_config(quest: dict) -> Optional[dict]:
    cfg = quest.get("config", {})
    return _get(cfg, "taskConfig", "task_config", "taskConfigV2", "task_config_v2")

def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name")
    if name:
        return name.strip()
    game = _get(msgs, "gameTitle", "game_title")
    if game:
        return game.strip()
    app_name = cfg.get("application", {}).get("name")
    if app_name:
        return app_name
    return f"Quest#{quest.get('id', '?')}"

def get_expires_at(quest: dict) -> Optional[str]:
    cfg = quest.get("config", {})
    return _get(cfg, "expiresAt", "expires_at")

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
    if not tc or "tasks" not in tc:
        return False

    tasks = tc["tasks"]
    return any(tasks.get(t) is not None for t in SUPPORTED_TASKS)

def is_enrolled(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "enrolledAt", "enrolled_at"))

def is_completed(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "completedAt", "completed_at"))

def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None:
            return t
    return None

def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    task_type = get_task_type(quest)
    if not tc or not task_type:
        return 0
    return tc["tasks"][task_type].get("target", 0)

def get_seconds_done(quest: dict) -> float:
    task_type = get_task_type(quest)
    if not task_type:
        return 0
    us = get_user_status(quest)
    progress = us.get("progress", {})
    if not progress:
        progress = {}
    return progress.get(task_type, {}).get("value", 0)

def get_enrolled_at(quest: dict) -> Optional[str]:
    us = get_user_status(quest)
    return _get(us, "enrolledAt", "enrolled_at")

def parse_quest_orbs(quest: dict) -> int:
    cfg = quest.get("config", {})
    rewards_config = cfg.get("rewardsConfig", {}) or cfg.get("rewards_config", {})
    rewards = rewards_config.get("rewards", []) or cfg.get("rewards", []) or []
    
    for rw in rewards:
        if isinstance(rw, dict):
            if "orb_quantity" in rw:
                return rw["orb_quantity"]
            if "amount" in rw and rw.get("type") in [0, "ORBS", "ORB"]:
                return rw["amount"]
            r_msgs = rw.get("messages", {}) or {}
            r_name = str(r_msgs.get("name", "") or r_msgs.get("title", ""))
            nums = re.findall(r'\d+', r_name)
            if nums and ("orb" in r_name.lower() or rw.get("type") == 0):
                return int(nums[0])
    
    t_type = get_task_type(quest)
    if t_type in ["WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"]:
        return 200
    return 700


# ==============================================================================
# MODULE 3: MODAL & TOKEN PROCESSING
# ==============================================================================

class TokenInputModal(discord.ui.Modal, title="🔐 CẬP NHẬT DISCORD USER TOKEN"):
    token_input = discord.ui.TextInput(
        label="Dán User Token mới vào đây",
        placeholder="Dán token để hệ thống lưu và cày Quests...",
        style=discord.TextStyle.short,
        required=True,
        max_length=150
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        user_token = self.token_input.value.strip()
        await process_user_token(interaction, user_token)


async def process_user_token(interaction: discord.Interaction, user_token: str):
    user_id = interaction.user.id
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    loop = asyncio.get_running_loop()
    build_number = await loop.run_in_executor(None, fetch_latest_build_number)
    api = DiscordAPI(user_token, build_number)

    r = await loop.run_in_executor(None, api.get, "/users/@me")
    if r.status_code != 200:
        remove_token_from_file(str(user_id))
        await interaction.followup.send("❌ **Token đã hết hạn hoặc không hợp lệ!** Đã xóa token khỏi hệ thống.", ephemeral=True)
        return

    user_data = r.json()
    display_name = user_data.get("global_name") or user_data.get("username")

    def fetch_and_enroll():
        q_res = api.get("/quests/@me")
        if q_res.status_code != 200:
            return []
        q_json = q_res.json()
        raw_quests = q_json.get("quests", []) if isinstance(q_json, dict) else q_json

        unaccepted = [
            q for q in raw_quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]
        for q in unaccepted:
            qid = q.get("id")
            api.post(f"/quests/{qid}/enroll", {
                "location": 11,
                "is_targeted": False,
                "metadata_raw": None,
                "metadata_sealed": None,
            })
            time.sleep(random.uniform(0.8, 1.5))

        if unaccepted:
            q_res = api.get("/quests/@me")
            if q_res.status_code == 200:
                q_json = q_res.json()
                raw_quests = q_json.get("quests", []) if isinstance(q_json, dict) else q_json

        return [
            q for q in raw_quests
            if is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]

    actionable = await loop.run_in_executor(None, fetch_and_enroll)

    if not actionable:
        await interaction.followup.send("⚠️ **Tài khoản hiện không có Quest khả dụng nào hoặc đã làm xong hết!**", ephemeral=True)
        return

    parsed_quests = []
    for q in actionable:
        task_type = get_task_type(q)
        target = get_seconds_needed(q)
        done = get_seconds_done(q)
        name = get_quest_name(q)
        orb_count = parse_quest_orbs(q)

        parsed_quests.append({
            "raw_quest": q,
            "id": q.get("id"),
            "title": name,
            "type": task_type,
            "orb": orb_count,
            "duration": target,
            "done_seconds": done,
            "status": "⏳ Đang cày..."
        })

    save_token_to_file(str(user_id), user_token)

    total_found = len(parsed_quests)
    active_quests[user_id] = {
        "user_name": display_name,
        "token": user_token,
        "api": api,
        "quests": parsed_quests,
        "start_time": time.time(),
        "total_orbs": sum(p["orb"] for p in parsed_quests),
    }

    embed = create_status_embed(user_id)
    await interaction.followup.send(content=f"🚀 **Đã lưu token & tự động nhận nhiệm vụ!** Tìm thấy **{total_found} Quest(s)**. Đang cày ngầm đa luồng an toàn...", embed=embed, ephemeral=False)

    asyncio.create_task(run_farming_process(interaction.client, user_id))


# ==============================================================================
# MODULE 4: REAL MULTI-THREADED CORE + ANTI-BAN JITTER TIMING
# ==============================================================================

def run_single_quest_sync(p_quest: dict, api: DiscordAPI, user_id: int, bot: commands.Bot):
    quest = p_quest["raw_quest"]
    qid = quest["id"]
    task_type = p_quest["type"]
    seconds_needed = p_quest["duration"]
    seconds_done = p_quest["done_seconds"]

    if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
        speed = random.uniform(5.5, 7.5) # Tốc độ ngẫu nhiên giả lập người thật xem video
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
                    asyncio.run_coroutine_threadsafe(notify_token_expired(bot, user_id), bot.loop)
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
                    time.sleep(retry_after + random.uniform(2.0, 4.0)) # Bổ sung khoảng đệm an toàn khi dính rate limit
                    continue
            except Exception:
                pass

            if timestamp >= seconds_needed:
                break
            time.sleep(random.uniform(0.9, 1.4)) # Jitter ngẫu nhiên cho mỗi nhịp gửi

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
                    asyncio.run_coroutine_threadsafe(notify_token_expired(bot, user_id), bot.loop)
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

            time.sleep(random.uniform(18.0, 24.0)) # Heartbeat giãn cách ngẫu nhiên

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
                    asyncio.run_coroutine_threadsafe(notify_token_expired(bot, user_id), bot.loop)
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


async def run_farming_process(bot: commands.Bot, user_id: int):
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
            futures = [loop.run_in_executor(executor, run_single_quest_sync, q, api, user_id, bot) for q in quests_to_run]
            await asyncio.gather(*futures, return_exceptions=True)

        await asyncio.sleep(random.uniform(2.0, 4.0))

    if user_id in active_quests:
        try:
            user = await bot.fetch_user(user_id)
            await user.send(f"🎉 **Chúc mừng {data['user_name']}!** Đã cày hoàn thành toàn bộ nhiệm vụ an toàn.")
        except Exception:
            pass
        await asyncio.sleep(60)
        active_quests.pop(user_id, None)


async def notify_token_expired(bot: commands.Bot, user_id: int):
    try:
        user = await bot.fetch_user(user_id)
        await user.send("❌ **Cảnh báo:** Token Discord của bạn đã hết hạn hoặc không sử dụng được! Hệ thống đã tự động xóa token. Vui lòng dùng lệnh `/token` để cập nhật lại.")
    except Exception:
        pass
    active_quests.pop(user_id, None)


# ==============================================================================
# MODULE 5: DASHBOARD UI & EMBED BUILDER
# ==============================================================================

def create_status_embed(user_id: int) -> discord.Embed:
    if user_id not in active_quests:
        return discord.Embed(title="🛡️ DISCORD ANTI-BAN QUEST ENGINE", description="Không có tiến trình cày nào đang chạy.", color=discord.Color.blue())

    data = active_quests[user_id]
    quests = data["quests"]

    embed = discord.Embed(title="🛡️ DISCORD QUEST ENGINE — LIVE DASHBOARD", color=discord.Color.from_rgb(46, 204, 113))
    embed.add_field(name="👤 Tài khoản", value=f"**{data['user_name']}** — 🔮 Tổng Orb: **{data['total_orbs']} Orbs**", inline=False)

    total = len(quests)
    completed = sum(1 for q in quests if q["done_seconds"] >= q["duration"])
    avg_progress = int(sum((q["done_seconds"] / q["duration"]) * 100 for q in quests) / total) if total > 0 else 0
    bar = "█" * (avg_progress // 10) + "░" * (10 - (avg_progress // 10))

    embed.add_field(name="⚡ Trạng thái Hệ thống", value=f"🟢 **Multi-Threaded Mode (Đang cày song song)**\n📊 **Tiến độ chung:** `[{bar}]` **{avg_progress}%** ({completed}/{total})", inline=False)

    for idx, q in enumerate(quests, 1):
        icon = "🎬" if "VIDEO" in str(q["type"]).upper() else "🎮"
        dur = q["duration"]
        done = q["done_seconds"]
        
        pct = min(100, int((done / dur) * 100)) if dur > 0 else 0
        filled = pct // 20
        empty = 5 - filled
        q_bar = "█" * filled + "░" * empty

        done_m, done_s = divmod(int(done), 60)
        tot_m, tot_s = divmod(int(dur), 60)
        
        field_name = f"#{idx} — {q['title'][:22]}"
        field_val = f"{icon} `{q['type']}` — 🔮 **{q['orb']} Orbs**\n`[{q_bar}]` **{pct}%**\n⏱️ `{done_m}p {done_s:02d}s / {tot_m}p {tot_s:02d}s`"
        embed.add_field(name=field_name, value=field_val, inline=True)

    embed.set_footer(text=f"Cập nhật: {datetime.now().strftime('%H:%M:%S')} — Anti-Ban v20.0 Safe Multi-Profile")
    return embed


# ==============================================================================
# MODULE 6: DISCORD COG & COMMANDS REGISTRY
# ==============================================================================

class UltimateQuestCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.dashboard_loop.start()

    def cog_unload(self):
        self.dashboard_loop.cancel()

    @app_commands.command(name="auto", description="[CÀY QUEST] Tự động quét, nhận và cày sạch Quests song song")
    async def auto_cay(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        saved_tokens = load_saved_tokens()

        if user_id_str in saved_tokens:
            await interaction.response.send_message("🔍 Đã tìm thấy token trong bộ nhớ. Đang quét và cày ngầm song song...", ephemeral=True)
            await process_user_token(interaction, saved_tokens[user_id_str])
        else:
            await interaction.response.send_modal(TokenInputModal(self.bot))

    @app_commands.command(name="token", description="[BẢO MẬT] Nhập hoặc cập nhật User Token Discord của bạn")
    async def nhap_token(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TokenInputModal(self.bot))

    @app_commands.command(name="xoatoken", description="[QUẢN LÝ] Xóa token đã lưu khỏi hệ thống bot")
    async def xoa_token(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        saved_tokens = load_saved_tokens()
        if user_id_str in saved_tokens:
            remove_token_from_file(user_id_str)
            await interaction.response.send_message("✅ Đã xóa token thành công khỏi hệ thống.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Bạn chưa lưu token nào trong hệ thống.", ephemeral=True)

    @app_commands.command(name="tintinh", description="[DASHBOARD] Xem bảng tiến độ chi tiết real-time")
    async def xem_tien_trinh(self, interaction: discord.Interaction):
        embed = create_status_embed(interaction.user.id)
        await interaction.response.send_message(embed=embed)
        dashboard_messages[interaction.channel_id] = await interaction.original_response()

    @app_commands.command(name="huyorb", description="[ĐIỀU KHIỂN] Hủy phiên cày hiện tại đang chạy")
    async def huy_quest(self, interaction: discord.Interaction):
        if interaction.user.id in active_quests:
            active_quests.pop(interaction.user.id, None)
            await interaction.response.send_message("✅ Đã hủy tiến trình thành công.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Không có tiến trình cày nào đang chạy.", ephemeral=True)

    @tasks.loop(seconds=6)
    async def dashboard_loop(self):
        for cid, msg in list(dashboard_messages.items()):
            try:
                if active_quests:
                    first_user_id = list(active_quests.keys())[0]
                    await msg.edit(embed=create_status_embed(first_user_id))
            except Exception:
                dashboard_messages.pop(cid, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(UltimateQuestCog(bot))
