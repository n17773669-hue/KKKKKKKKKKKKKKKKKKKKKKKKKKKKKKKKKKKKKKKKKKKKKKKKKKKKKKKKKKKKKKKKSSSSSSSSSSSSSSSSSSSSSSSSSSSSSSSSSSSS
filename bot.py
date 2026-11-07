import status
import os 
import random
import time
import asyncio
import sqlite3
import datetime
import vr_patch
import math
import re
import json
from typing import Optional
import discord
from discord.ext import commands
import threading
from supabase import create_client, Client

# ==========================================
# 0. KẾT NỐI SUPABASE (CLOUD MEMORY) - ĐỒNG BỘ DỮ LIỆU LÊN MẠNG
# ==========================================
# QUAN TRỌNG: thay SUPABASE_URL bằng URL project THẬT của bạn
# (dạng https://xxxxxxxxxxxx.supabase.co) — lấy trong Supabase Dashboard
# > Project Settings > API. URL "https://supabase.co" chỉ là ví dụ, không
# phải URL project thật nên sẽ không kết nối được nếu bạn không thay.
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://cztkiffqiqzzadfuxckm.supabase.co")  # TODO: dán URL project thật vào đây
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_MNqVCJ3phNPXd1I_-l9yHw_AEbRIGJ-")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("[Cloud] Đã khởi tạo kết nối Supabase.")
except Exception as e:
    supabase = None
    print(f"[Cloud] Lỗi khởi tạo Supabase: {e}")

def cloud_pull_user(user_id):
    """Tải dữ liệu 1 user từ Supabase về.
    Dùng khi local sqlite chưa có sẵn user này — ví dụ sau khi bot bị
    restart/redeploy trên hosting có ổ đĩa tạm (ephemeral) và mất file db."""
    if supabase is None:
        return None
    try:
        uid_str = str(user_id)
        res = supabase.table("user_data").select("*").eq("user_id", uid_str).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"[Cloud] Lỗi tải user {user_id}: {e}")
    return None

def _cloud_push_user_sync(user_id, coin, level, exp, streak, last_streak_time, history_tx):
    if supabase is None:
        return
    try:
        uid_str = str(user_id)
        payload = {
            "user_id": uid_str,
            "coin": coin,
            "level": level,
            "exp": exp,
            "streak": streak,
            "last_streak_time": last_streak_time,
            "history_tx": history_tx,
        }
        check = supabase.table("user_data").select("user_id").eq("user_id", uid_str).execute()
        if check.data:
            supabase.table("user_data").update(payload).eq("user_id", uid_str).execute()
        else:
            supabase.table("user_data").insert(payload).execute()
    except Exception as e:
        print(f"[Cloud] Lỗi lưu ngầm dữ liệu cho {user_id}: {e}")

def cloud_push_user(user_id, coin, level, exp, streak, last_streak_time, history_tx):
    """Đẩy dữ liệu 1 user lên Supabase. Chạy ngầm bằng thread riêng để
    không làm lag các lệnh của bot."""
    threading.Thread(
        target=_cloud_push_user_sync,
        args=(user_id, coin, level, exp, streak, last_streak_time, history_tx),
        daemon=True,
    ).start()

# ==========================================
# 1. DATABASE SQLITE & HỆ THỐNG STREAK CHUẨN TIKTOK (24 GIỜ)
# ==========================================
DB_NAME = "ksbot_master.db"

# ==========================================
# 0.1 SAO LƯU / KHÔI PHỤC TOÀN BỘ FILE DATABASE LÊN SUPABASE STORAGE
# ==========================================
# Cách này backup NGUYÊN file ksbot_master.db (bao gồm users, user_pets,
# user_items, bot_meta... và bất kỳ bảng nào sinh ra sau này) thay vì phải
# sửa từng lệnh/từng hàm rải rác trong file — "đồng bộ từ A-Z" một chỗ.
#
# YÊU CẦU: vào Supabase Dashboard > Storage > tạo 1 bucket tên đúng như
# BACKUP_BUCKET bên dưới (mặc định "ksbot-backups"), để chế độ Public
# hoặc cấu hình policy cho phép SUPABASE_KEY bạn đang dùng đọc/ghi được.
BACKUP_BUCKET = os.getenv("BACKUP_BUCKET", "ksbot-backups")
BACKUP_FILE_NAME = "ksbot_master.db"
AUTO_BACKUP_POLL_SECONDS = 2  # cứ mỗi 2 giây kiểm tra 1 lần xem file db có thay đổi không

def restore_db_from_cloud():
    """Nếu local CHƯA có file db (vd bot vừa restart/redeploy trên hosting
    có ổ đĩa tạm nên mất file cũ), tải bản backup mới nhất từ Supabase
    Storage về để không bị mất dữ liệu users/pets/items."""
    if supabase is None:
        print("[Cloud] Supabase chưa kết nối, bỏ qua khôi phục database.")
        return
    if os.path.exists(DB_NAME):
        print("[Cloud] Đã có sẵn file database local, không cần khôi phục.")
        return
    try:
        data = supabase.storage.from_(BACKUP_BUCKET).download(BACKUP_FILE_NAME)
        with open(DB_NAME, "wb") as f:
            f.write(data)
        print(f"[Cloud] Đã khôi phục toàn bộ database từ Supabase Storage ({len(data)} bytes)!")
    except Exception as e:
        print(f"[Cloud] Không tìm thấy backup cũ hoặc lỗi tải về: {e} (sẽ tạo database mới từ đầu)")

def backup_db_to_cloud():
    """Đẩy NGUYÊN file database hiện tại lên Supabase Storage, ghi đè bản cũ."""
    if supabase is None:
        return
    try:
        with open(DB_NAME, "rb") as f:
            data = f.read()
        try:
            supabase.storage.from_(BACKUP_BUCKET).update(BACKUP_FILE_NAME, data)
        except Exception:
            # Nếu file chưa tồn tại trên storage (lần đầu) thì upload mới
            supabase.storage.from_(BACKUP_BUCKET).upload(BACKUP_FILE_NAME, data)
        print(f"[Cloud] Đã sao lưu toàn bộ database lên Supabase Storage ({len(data)} bytes).")
    except Exception as e:
        print(f"[Cloud] Lỗi sao lưu database: {e}")

def _auto_backup_loop():
    """Không đợi hẹn giờ cố định — cứ mỗi AUTO_BACKUP_POLL_SECONDS giây kiểm
    tra xem file database có VỪA bị ghi đổi hay không (dựa vào mtime, tức
    thời điểm sửa đổi cuối). Có thay đổi -> backup ngay lập tức (đè bản cũ).
    Không có gì thay đổi -> bỏ qua, không tốn request lãng phí."""
    last_backed_up_mtime = None
    while True:
        try:
            if os.path.exists(DB_NAME):
                current_mtime = os.path.getmtime(DB_NAME)
                if last_backed_up_mtime is None or current_mtime > last_backed_up_mtime:
                    backup_db_to_cloud()
                    last_backed_up_mtime = current_mtime
        except Exception as e:
            print(f"[Cloud] Lỗi khi kiểm tra thay đổi database: {e}")
        time.sleep(AUTO_BACKUP_POLL_SECONDS)

def start_auto_backup():
    """Gọi hàm này SAU KHI init_db() chạy xong — backup ngay 1 lần, rồi từ
    đó cứ có thay đổi trong database là tự backup lại ngay (xem _auto_backup_loop)."""
    backup_db_to_cloud()
    threading.Thread(target=_auto_backup_loop, daemon=True).start()

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            coin INTEGER DEFAULT 100000,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 1,
            last_streak_time REAL DEFAULT 0,
            history_tx TEXT DEFAULT ""
        )
    ''')
    # Bảng lưu trữ thú của người dùng
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            pet_index INTEGER,
            pet_name TEXT,
            hp INTEGER,
            atk INTEGER,
            rarity TEXT,
            in_team INTEGER DEFAULT 0
        )
    ''')
    # Bảng lưu trữ shop đồ trang bị/tăng chỉ số thú cho user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_items (
            user_id TEXT,
            item_id TEXT,
            quantity INTEGER,
            PRIMARY KEY (user_id, item_id)
        )
    ''')
    conn.commit()
    conn.close()

restore_db_from_cloud()
init_db()
start_auto_backup()

def get_user(user_id):
    uid_str = str(user_id)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT coin, level, exp, streak, last_streak_time, history_tx FROM users WHERE user_id = ?', (uid_str,))
    row = cursor.fetchone()
    current_time = time.time()
    
    if row is None:
        # Thử tải lại từ Supabase trước (phòng khi local db bị mất do restart/redeploy)
        cloud_row = cloud_pull_user(uid_str)
        if cloud_row:
            default_coin = cloud_row.get("coin", 100000)
            level0 = cloud_row.get("level", 1)
            exp0 = cloud_row.get("exp", 0)
            streak0 = cloud_row.get("streak", 1)
            last_time0 = cloud_row.get("last_streak_time", current_time)
            history0 = cloud_row.get("history_tx", "")
            cursor.execute('INSERT INTO users (user_id, coin, level, exp, streak, last_streak_time, history_tx) VALUES (?, ?, ?, ?, ?, ?, ?)', (uid_str, default_coin, level0, exp0, streak0, last_time0, history0))
            conn.commit()
            data = (default_coin, level0, exp0, streak0, last_time0, history0)
            print(f"[Cloud] Đã khôi phục dữ liệu user {uid_str} từ Supabase.")
        else:
            default_coin = 100000
            cursor.execute('INSERT INTO users (user_id, coin, level, exp, streak, last_streak_time, history_tx) VALUES (?, ?, 1, 0, 1, ?, "")', (uid_str, default_coin, current_time))
            conn.commit()
            data = (default_coin, 1, 0, 1, current_time, "")
    else:
        data = row
    conn.close()
    return data

def update_user_full(user_id, coin, level, exp, streak, last_streak_time, history_tx):
    uid_str = str(user_id)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, coin, level, exp, streak, last_streak_time, history_tx) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (uid_str, coin, level, exp, streak, last_streak_time, history_tx))
    conn.commit()
    conn.close()
    # Đồng bộ ngay lên Supabase để không mất dữ liệu khi bot restart/redeploy
    cloud_push_user(uid_str, coin, level, exp, streak, last_streak_time, history_tx)

def add_exp_to_user(user_id, exp_gain):
    uid_str = str(user_id)
    coin, level, exp, streak, last_time, history = get_user(uid_str)
    exp += exp_gain
    # Công thức level lên càng cao càng khó, cần nhiều exp hơn
    needed_exp = level * 300 + 500
    while exp >= needed_exp:
        exp -= needed_exp
        level += 1
        needed_exp = level * 300 + 500
    update_user_full(uid_str, coin, level, exp, streak, last_time, history)

def parse_bet_amount(user_coin, arg_str):
    if not arg_str:
        return None
    arg_clean = arg_str.strip().lower()
    if arg_clean in ["all", "tất", "tat"]:
        return user_coin
    
    raw_val = arg_clean.replace(",", "").replace(".", "")
    if raw_val.isdigit():
        val = int(raw_val)
        return val
    return None

async def process_user_streak_and_nickname(member):
    uid_str = str(member.id)
    u_data = get_user(uid_str)
    coin, level, exp, streak, last_streak_time, history_tx = u_data
    
    current_time = time.time()
    
    if last_streak_time == 0:
        last_streak_time = current_time
        streak = 1
    else:
        time_diff = current_time - last_streak_time
        if time_diff >= 86400:
            if time_diff >= 172800:
                streak = 1
            else:
                streak += 1
            last_streak_time = current_time
        elif time_diff < 0:
            last_streak_time = current_time

    update_user_full(uid_str, coin, level, exp, streak, last_streak_time, history_tx)


# ==========================================
# 1.1. DANH SÁCH 40 CON THÚ & CƠ CHẾ THÚ THỦ CÔNG
# ==========================================
ALL_PETS = [
    {"id": 1, "name": "Thỏ Rừng", "rarity": "Thường", "hp": 100, "atk": 20},
    {"id": 2, "name": "Gấu Xám", "rarity": "Thường", "hp": 150, "atk": 25},
    {"id": 3, "name": "Sói Xám", "rarity": "Thường", "hp": 120, "atk": 30},
    {"id": 4, "name": "Hổ Vằn", "rarity": "Hiếm", "hp": 200, "atk": 45},
    {"id": 5, "name": "Sư Tử Đất", "rarity": "Hiếm", "hp": 220, "atk": 40},
    {"id": 6, "name": "Báo Đen", "rarity": "Hiếm", "hp": 180, "atk": 50},
    {"id": 7, "name": "Voi Ma Mút Nhỏ", "rarity": "Hiếm", "hp": 300, "atk": 35},
    {"id": 8, "name": "Tê Giác Thiết Giáp", "rarity": "Hiếm", "hp": 280, "atk": 38},
    {"id": 9, "name": "Đại Bàng Lửa", "rarity": "Hiếm", "hp": 160, "atk": 55},
    {"id": 10, "name": "Cá Sấu Sông Nile", "rarity": "Hiếm", "hp": 240, "atk": 42},
    {"id": 11, "name": "Hà Mã Bạo Chúa", "rarity": "Hiếm", "hp": 320, "atk": 35},
    {"id": 12, "name": "Trăn Nam Mỹ", "rarity": "Hiếm", "hp": 210, "atk": 48},
    {"id": 13, "name": "Khỉ Đột Núi", "rarity": "Hiếm", "hp": 250, "atk": 44},
    {"id": 14, "name": "Linh Cẩu Đốm", "rarity": "Thường", "hp": 130, "atk": 32},
    {"id": 15, "name": "Lợn Lòi Hoang", "rarity": "Thường", "hp": 140, "atk": 28},
    {"id": 16, "name": "Nhím Gai Độc", "rarity": "Thường", "hp": 90, "atk": 35},
    {"id": 17, "name": "Cú Mèo Bóng Đêm", "rarity": "Thường", "hp": 110, "atk": 30},
    {"id": 18, "name": "Rùa Đá Cổ Đại", "rarity": "Hiếm", "hp": 350, "atk": 25},
    {"id": 19, "name": "Kẻ Hủy Diệt - T-Rex", "rarity": "Sử Thi", "hp": 500, "atk": 85},
    {"id": 20, "name": "Rồng Đất Cổ Đại", "rarity": "Sử Thi", "hp": 550, "atk": 80},
    {"id": 21, "name": "Phượng Hoàng Lửa", "rarity": "Huyền Thoại", "hp": 600, "atk": 110},
    {"id": 22, "name": "Kỳ Lân Sấm Sét", "rarity": "Huyền Thoại", "hp": 580, "atk": 115},
    {"id": 23, "name": "Hắc Long Bóng Đêm", "rarity": "Huyền Thoại", "hp": 650, "atk": 125},
    {"id": 24, "name": "Bạch Hổ Thần Thánh", "rarity": "Sử Thi", "hp": 480, "atk": 90},
    {"id": 25, "name": "Thanh Long Vực Sâu", "rarity": "Huyền Thoại", "hp": 700, "atk": 130},
    {"id": 26, "name": "Chu Tước Viêm Vương", "rarity": "Huyền Thoại", "hp": 620, "atk": 120},
    {"id": 27, "name": "Huyền Vũ Mai Đen", "rarity": "Sử Thi", "hp": 750, "atk": 70},
    {"id": 28, "name": "Yeti Người Tuyết", "rarity": "Hiếm", "hp": 340, "atk": 50},
    {"id": 29, "name": "Nhện Chúa Hang Động", "rarity": "Hiếm", "hp": 200, "atk": 60},
    {"id": 30, "name": "Bọ Cạp Quỷ Vương", "rarity": "Hiếm", "hp": 220, "atk": 65},
    {"id": 31, "name": "Tinh Linh Ánh Sáng", "rarity": "Sử Thi", "hp": 400, "atk": 95},
    {"id": 32, "name": "Ác Quỷ Địa Ngục", "rarity": "Sử Thi", "hp": 520, "atk": 100},
    {"id": 33, "name": "Hiệp Sĩ Vô Hồn", "rarity": "Hiếm", "hp": 300, "atk": 55},
    {"id": 34, "name": "Golem Đất Sét", "rarity": "Thường", "hp": 400, "atk": 20},
    {"id": 35, "name": "Nhân Sư Thông Thái", "rarity": "Sử Thi", "hp": 450, "atk": 85},
    {"id": 36, "name": "Kraken Thủy Quái", "rarity": "Huyền Thoại", "hp": 800, "atk": 140},
    {"id": 37, "name": "Cerberus Chó Ba Đầu", "rarity": "Sử Thi", "hp": 550, "atk": 105},
    {"id": 38, "name": "Chim Sấm Thần Thoại", "rarity": "Huyền Thoại", "hp": 600, "atk": 125},
    {"id": 39, "name": "Titan Hủy Diệt", "rarity": "Huyền Thoại", "hp": 900, "atk": 150},
    {"id": 40, "name": "Thần Rồng Tối Thượng", "rarity": "Thần Thoại", "hp": 1200, "atk": 220}
]

# Định nghĩa Shop Đồ Tăng Chỉ Số Thú (Giá đắt để tránh lạm phát)
SHOP_ITEMS = {
    "1": {"id": "thuoc_cuong_luc", "name": "Thuốc Cường Lực (Tăng 20 ATK cho đội hình)", "price": 300000, "type": "atk", "val": 20},
    "2": {"id": "giap_kim_cuong", "name": "Giáp Kim Cương (Tăng 100 HP cho đội hình)", "price": 450000, "type": "hp", "val": 100},
    "3": {"id": "huy_hieu_than_thu", "name": "Huy Hiệu Thần Thú (Tăng 50 ATK & 250 HP)", "price": 1200000, "type": "all", "atk": 50, "hp": 250},
    "4": {"id": "vot_bat_thu", "name": "Vợt Bắt Thú (Dùng để bắt thú hoang)", "price": 50000, "type": "tool"}
}

# Biến lưu trữ thú hoang xuất hiện ngẫu nhiên trong kênh
ACTIVE_WILD_PETS = {} # {channel_id: {"pet": pet_dict, "expires": time}}


# ==========================================
# 2. KHỞI TẠO BOT & PREFIX (DẤU .)
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=['.'], intents=intents)
bot.remove_command('help')

MAIN_COLOR = 0xb388ff
BOT_ORIGINAL_NAME = "💤KS-BOT💤"
DIEM_DANH_TRACKER = {}

AFK_USERS = {}
COIN_EMOJI = "<:cat_coin:1536695423014535268>"
game_lobbies = {}

# Kho tàng 30 loại manh mối hack não phong phú, không lặp lại cho Ma Sói
RANDOM_CLUES = [
    "🌙 Đêm qua, có tiếng bước chân rón rén đi ngang qua cửa nhà của {target}, âm thanh vô cùng khả nghi.",
    "🕯️ Ánh nến hắt qua cửa sổ nhà {target} bỗng dưng vụt tắt khi một bóng đen lướt qua.",
    "🍃 Cơn gió lạnh lụi tàn thổi qua hiên nhà {target}, để lại một cảm giác rợn ngợp kỳ lạ.",
    "🐾 Người ta tìm thấy những dấu chân lạ dính bùn đen xuất hiện trước sân nhà {target}.",
    "🦉 Tiếng chim cú mèo kêu vang lên ba tiếng hướng về phía khu vực của {target} lúc nửa đêm.",
    "🔮 Một tấm gương cổ trong làng bỗng xuất hiện hình ảnh mờ ảo của {target} vào lúc canh ba.",
    "⚡ Khói sương mù dày đặc bỗng quẩn quanh khu vực nhà {target} suốt cả đêm qua.",
    "🗡️ Có tiếng kim loại va chạm nhẹ phát ra từ phía nhà của {target} vào rạng sáng.",
    "💧 Người ta phát hiện những giọt sương đỏ như máu đọng lại trước bậc cửa nhà {target}.",
    "📜 Một mảnh giấy rách được tìm thấy rải rác gần nhà {target} với ký hiệu kỳ lạ.",
    "🪵 Tiếng cạch cửa đột ngột vang lên từ ngôi nhà của {target} rồi im bặt.",
    "🦇 Đàn dơi bỗng bay vụt ra từ mái nhà {target} khi màn đêm đạt đỉnh điểm.",
    "🌌 Ánh sao trên trời bỗng tối sầm lại chiếu thẳng xuống khu vực nhà {target}.",
    "🔥 Đốm lửa ma trơi xanh ngắt xuất hiện bay lượn quanh khu vườn của {target}.",
    "👣 Dấu vết móng vuốt sắc nhọn cào cấu trên cột gỗ trước nhà {target}.",
    "🔔 Tiếng chuông gió vang lên lanh lảnh một hồi bất thường ở hiên nhà {target}.",
    "🗝️ Tiếng chìa khóa xoay ổ khóa lách cách phát ra từ tư gia của {target}.",
    "🌪️ Một luồng gió quẩn bất thường thổi bay chiếc mũ trước hiên nhà {target}.",
    "🎭 Chiếc mặt nạ gỗ treo trước cửa nhà {target} bỗng rơi xuống đất vỡ tan.",
    "⚰️ Mùi hương hoa huệ tây thoang thoảng bay ra từ hướng nhà {target} trong đêm vắng.",
    "🌊 Tiếng nước chảy róc rách sát vách nhà {target} bỗng chuyển sang màu đen đục.",
    "🪞 Bóng phản chiếu của {target} trong giếng làng hình như không khớp với người thật.",
    "🏮 Chiếc lồng đèn trước nhà {target} chớp tắt liên hồi suốt đêm không rõ nguyên nhân.",
    "🌾 Cánh đồng lúa gần nhà {target} bị đè bẹp một khoảng lớn không rõ lý do.",
    "🦅 Một con quạ đen sà xuống mái nhà {target} rồi cất tiếng kêu ghê rợn.",
    " footsteps Tiếng gõ cửa ba nhịp bí ẩn vang lên nhà {target} nhưng không có ai mở.",
    "📌 Một chiếc ghim sắt rỉ sét cắm trên khung cửa sổ phòng {target}.",
    "🕳️ Mặt đất quanh hiên nhà {target} có dấu hiệu bị xới tung nhẹ.",
    "⚡ Tia chớp lóe lên soi rõ bóng một người đang đứng thẫn thờ trước nhà {target}.",
    "🌀 Một cảm giác lạnh sống lưng bao trùm lấy bất kỳ ai bước qua ngõ nhà {target}."
]

class WerewolfGame:
    def __init__(self, ctx):
        self.ctx = ctx
        self.players = {}  # {user_id: {"member": member, "role": str, "alive": bool}}
        self.state = "waiting"
        self.wolves = []
        self.seer = None
        self.guard = None
        self.night_actions = {}
        self.lobby_message = None
        self.clue_count_in_round = 0  # Đếm số lần dùng lệnh .manhmoi trong vòng (tối đa 2)

class JoinGameView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Tham Gia Ma Sói", style=discord.ButtonStyle.green, emoji="🐺")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if self.game.state != "waiting":
            await interaction.response.send_message("❌ Trò chơi đã bắt đầu, không thể tham gia lúc này!", ephemeral=True)
            return
        if uid in self.game.players:
            await interaction.response.send_message("❌ Bạn đã có trong danh sách chơi rồi!", ephemeral=True)
            return
        
        self.game.players[uid] = {"member": interaction.user, "role": "Dân Làng", "alive": True}
        await interaction.response.send_message("✅ Bạn đã tham gia phòng chờ thành công!", ephemeral=True)
        await update_lobby_embed(self.game)

    @discord.ui.button(label="Bắt Đầu Game", style=discord.ButtonStyle.blurple, emoji="🎮")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.game.ctx.author:
            await interaction.response.send_message("❌ Chỉ có chủ phòng mới được bấm bắt đầu!", ephemeral=True)
            return
        if len(self.game.players) < 5:
            await interaction.response.send_message("❌ Cần ít nhất 5 người chơi để bắt đầu!", ephemeral=True)
            return
        
        self.game.state = "running"
        self.stop()
        await interaction.response.send_message("🎮 Đang khởi động trò chơi Ma Sói...")
        
        try:
            await run_game(self.game)
        except Exception as e:
            print(f"Lỗi game: {e}")
            await self.game.ctx.send("❌ Đã có lỗi xảy ra trong quá trình chạy game.")
        finally:
            if self.game.ctx.channel.id in game_lobbies:
                del game_lobbies[self.game.ctx.channel.id]

async def update_lobby_embed(game):
    if not game.lobby_message:
        return
    player_list = "\n".join([f"• {data['member'].mention}" for data in game.players.values()])
    if not player_list:
        player_list = "Chưa có ai tham gia..."

    embed = discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=f"Bấm nút bên dưới để tham gia phòng chơi!\nChủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5 người).\n\n**Danh sách người chơi ({len(game.players)}):**\n{player_list}",
        color=UI_COLORS["cyan"]
    )
    try:
        await game.lobby_message.edit(embed=embed)
    except Exception:
        pass

async def run_game(game):
    p_ids = list(game.players.keys())
    random.shuffle(p_ids)
    
    total_players = len(p_ids)
    num_wolves = 1 if total_players < 6 else 2
    
    game.wolves = p_ids[:num_wolves]
    remaining = p_ids[num_wolves:]
    
    game.seer = remaining.pop(0) if remaining else None
    game.guard = remaining.pop(0) if remaining else None
    
    for uid in p_ids:
        if uid in game.wolves:
            game.players[uid]["role"] = "Sói"
        elif uid == game.seer:
            game.players[uid]["role"] = "Tiên Tri"
        elif uid == game.guard:
            game.players[uid]["role"] = "Bảo Vệ"
        else:
            game.players[uid]["role"] = "Dân Làng"
            
        try:
            m = game.players[uid]["member"]
            await m.send(f"🌙 Vai trò của bạn trong ván này là: **{game.players[uid]['role']}**")
        except Exception:
            pass

    await game.ctx.send("🎭 Đã phân phát vai trò qua tin nhắn riêng (DM). Trời đã tối, mọi người đi ngủ...")
    await start_night(game)

async def start_night(game):
    game.state = "night"
    game.night_actions = {}
    
    embed = discord.Embed(title="🌙 ĐÊM XUỐNG", description="Các chức năng ban đêm bắt đầu hoạt động...", color=0x2b2d31)
    await game.ctx.send(embed=embed)
    
    if game.seer and game.players[game.seer]["alive"]:
        seer_data = game.players[game.seer]
        view = SeerActionView(game)
        try:
            await seer_data["member"].send("🔮 Chọn người bạn muốn soi thân phận đêm nay:", view=view)
        except Exception:
            pass

    if game.guard and game.players[game.guard]["alive"]:
        guard_data = game.players[game.guard]
        view = GuardActionView(game)
        try:
            await guard_data["member"].send("🛡️ Chọn người bạn muốn bảo vệ đêm nay:", view=view)
        except Exception:
            pass

    for wolf_id in game.wolves:
        wolf_data = game.players.get(wolf_id)
        if wolf_data and wolf_data["alive"]:
            view = WolfActionView(game)
            try:
                await wolf_data["member"].send("🔪 Chọn nạn nhân bạn muốn ăn thịt đêm nay:", view=view)
            except Exception:
                pass

    night_msg = await game.ctx.send("⏳ Thời gian ban đêm còn lại: **25** giây...")
    for i in range(24, 0, -1):
        await asyncio.sleep(1)
        try:
            await night_msg.edit(content=f"⏳ Thời gian ban đêm còn lại: **{i}** giây...")
        except Exception:
            pass
    try:
        await night_msg.delete()
    except Exception:
        pass

    await resolve_night(game)

class SeerActionView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=25)
        self.game = game
        for uid, data in game.players.items():
            if data["alive"] and uid != game.seer:
                self.add_item(SeerTargetButton(uid, data["member"].display_name))

class SeerTargetButton(discord.ui.Button):
    def __init__(self, target_id, target_name):
        super().__init__(label=f"Soi {target_name}", style=discord.ButtonStyle.blurple)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        target_role = self.view.game.players[self.target_id]["role"]
        target_name = self.view.game.players[self.target_id]["member"].display_name
        result = "Sói 🐺" if target_role == "Sói" else "Không phải Sói 🛡️"
        await interaction.response.send_message(f"🔮 Kết quả soi: **{target_name}** có thân phận là: **{result}**", ephemeral=True)
        self.view.stop()

class GuardActionView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=25)
        self.game = game
        for uid, data in game.players.items():
            if data["alive"]:
                self.add_item(GuardTargetButton(uid, data["member"].display_name))

class GuardTargetButton(discord.ui.Button):
    def __init__(self, target_id, target_name):
        super().__init__(label=f"Bảo vệ {target_name}", style=discord.ButtonStyle.success)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        self.view.game.night_actions["guard_target"] = self.target_id
        await interaction.response.send_message(f"✅ Đã chọn bảo vệ mục tiêu!", ephemeral=True)
        self.view.stop()

class WolfActionView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=25)
        self.game = game
        for uid, data in game.players.items():
            if data["alive"] and uid not in game.wolves:
                self.add_item(WolfTargetButton(uid, data["member"].display_name))

class WolfTargetButton(discord.ui.Button):
    def __init__(self, target_id, target_name):
        super().__init__(label=f"Cắn {target_name}", style=discord.ButtonStyle.danger)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        self.view.game.night_actions["wolf_target"] = self.target_id
        await interaction.response.send_message(f"✅ Đã chọn cắn mục tiêu!", ephemeral=True)
        self.view.stop()

async def resolve_night(game):
    killed_id = game.night_actions.get("wolf_target")
    protected_id = game.night_actions.get("guard_target")
    result_text = ""
    
    if killed_id and killed_id in game.players:
        if killed_id == protected_id:
            result_text = "☀️ Sáng hôm sau, mọi người thở phào nhẹ nhõm vì Bảo Vệ đã cứu kịp thời nạn nhân khỏi nanh sói!"
        else:
            game.players[killed_id]["alive"] = False
            victim_name = game.players[killed_id]["member"].display_name
            result_text = f"☀️ Sáng hôm sau, mọi người bàng hoàng phát hiện **{victim_name}** đã bị sói ăn thịt!"
    else:
        result_text = "☀️ Trời sáng bình yên, không có ai bị sát hại đêm qua."

    embed = discord.Embed(title="☀️ BAN NGÀY", description=result_text, color=0xf1c40f)
    await game.ctx.send(embed=embed)
    
    if check_win_condition(game):
        return
    
    await start_day_discussion(game)

async def trigger_random_clue(channel, game):
    alive_players = [p for p in game.players.values() if p["alive"]]
    if not alive_players:
        return
    target = random.choice(alive_players)
    template = random.choice(RANDOM_CLUES)
    clue_text = template.format(target=target['member'].display_name)
    await channel.send(f"🧩 **MANH MỐI BÍ ẨN TỰ ĐỘNG:** {clue_text}")

async def start_day_discussion(game):
    game.state = "day"
    game.clue_count_in_round = 0
    
    mentions = [data["member"].mention for data in game.players.values() if data["alive"]]
    ping_text = " ".join(mentions)
    
    disc_msg = await game.ctx.send(f"🗣️ {ping_text} Mọi người có **60** giây để thảo luận và tìm ra Sói!\n💡 *Gõ `.manhmoi` (tối đa 2 lần/vòng) để tìm manh mối hack não!*")
    
    auto_clue_triggered = False
    for i in range(59, 0, -1):
        await asyncio.sleep(1)
        if i == 35 and not auto_clue_triggered:
            auto_clue_triggered = True
            await trigger_random_clue(game.ctx.channel, game)

        try:
            await disc_msg.edit(content=f"🗣️ {ping_text} Mọi người có **{i}** giây để thảo luận và tìm ra Sói!\n💡 *Gõ `.manhmoi` (tối đa 2 lần/vòng) để tìm manh mối hack não!*")
        except Exception:
            pass

    try:
        await disc_msg.delete()
    except Exception:
        pass
        
    await start_voting(game)

@bot.hybrid_command(name='manhmoi')
async def cmd_manhmoi(ctx):
    """Xem/tìm manh mối trong ngày (Ma Sói)."""
    ch_id = ctx.channel.id
    if ch_id not in game_lobbies:
        await ctx.send("❌ Hiện không có ván chơi nào đang diễn ra trong kênh này!", delete_after=5)
        return
    
    game = game_lobbies[ch_id]
    if game.state != "day":
        await ctx.send("❌ Chỉ có thể sử dụng lệnh `.manhmoi` trong thời gian thảo luận ban ngày!", delete_after=5)
        return
        
    if game.clue_count_in_round >= 2:
        await ctx.send("⚠️ Vòng này đã sử dụng tối đa 2 lần lệnh `.manhmoi`, hãy chờ vòng sau nhé!", delete_after=5)
        return
        
    game.clue_count_in_round += 1
    await trigger_random_clue(ctx.channel, game)
    add_exp_to_user(ctx.author.id, 50)

async def start_voting(game):
    await game.ctx.send("⚖️ Hết giờ thảo luận! Mọi người có **15 giây** để vote treo cổ nghi phạm (hoặc bỏ phiếu trắng).")
    game.night_actions["votes"] = {}
    
    view = VoteView(game)
    vote_msg = await game.ctx.send("🗳️ Bấm nút dưới đây để chọn:", view=view)
    
    for i in range(14, 0, -1):
        await asyncio.sleep(1)
        try:
            await vote_msg.edit(content=f"🗳️ Bấm nút dưới đây để chọn (Còn **{i}** giây):", view=view)
        except Exception:
            pass
            
    view.stop()
    try:
        await vote_msg.delete()
    except Exception:
        pass
        
    await resolve_voting(game)

class VoteView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=15)
        self.game = game
        for uid, data in game.players.items():
            if data["alive"]:
                self.add_item(VoteButton(uid, data["member"].display_name))
        self.add_item(BlankVoteButton())

class VoteButton(discord.ui.Button):
    def __init__(self, target_id, target_name):
        super().__init__(label=f"Treo {target_name}", style=discord.ButtonStyle.blurple)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        voter_id = interaction.user.id
        if voter_id not in self.view.game.players or not self.view.game.players[voter_id]["alive"]:
            await interaction.response.send_message("❌ Bạn không có quyền vote!", ephemeral=True)
            return
            
        votes = self.view.game.night_actions.setdefault("votes", {})
        votes[voter_id] = self.target_id
        
        current_tally = sum(1 for t in votes.values() if t == self.target_id)
        target_name = self.view.game.players[self.target_id]["member"].display_name
        await interaction.response.send_message(f"✅ Đã vote treo **{target_name}**! (Tổng số phiếu hiện tại: **{current_tally}**)", ephemeral=True)

class BlankVoteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🏳️ Phiếu Trắng", style=discord.ButtonStyle.grey)

    async def callback(self, interaction: discord.Interaction):
        voter_id = interaction.user.id
        if voter_id not in self.view.game.players or not self.view.game.players[voter_id]["alive"]:
            await interaction.response.send_message("❌ Bạn không có quyền vote!", ephemeral=True)
            return
            
        votes = self.view.game.night_actions.setdefault("votes", {})
        votes[voter_id] = "blank"
        await interaction.response.send_message("✅ Bạn đã bỏ **Phiếu Trắng** thành công!", ephemeral=True)

async def resolve_voting(game):
    votes = game.night_actions.get("votes", {})
    tally = {}
    blank_count = 0
    
    for voter, target in votes.items():
        if target == "blank":
            blank_count += 1
        else:
            tally[target] = tally.get(target, 0) + 1
        
    if not tally:
        await game.ctx.send(f"⚖️ Mọi người đã chọn bỏ phiếu trắng ({blank_count} phiếu). Không có ai bị treo cổ!")
    else:
        max_votes = max(tally.values())
        top_targets = [t for t, v in tally.items() if v == max_votes]
        
        if len(top_targets) == 1:
            executed_id = top_targets[0]
            game.players[executed_id]["alive"] = False
            ex_name = game.players[executed_id]["member"].display_name
            role_revealed = game.players[executed_id]["role"]
            await game.ctx.send(f"⚖️ Kết quả treo cổ: **{ex_name}** đã bị treo cổ với {max_votes} phiếu! (Phiếu trắng: {blank_count})\n🎭 Vai trò của họ là: **{role_revealed}**")
        else:
            await game.ctx.send(f"⚖️ Số phiếu ngang nhau, không ai bị treo cổ ở lượt này. (Phiếu trắng: {blank_count})")
            
    if check_win_condition(game):
        return
        
    await game.ctx.send("🔄 Vòng chơi tiếp theo bắt đầu...")
    await start_night(game)

def check_win_condition(game):
    alive_wolves = [uid for uid, data in game.players.items() if data["role"] == "Sói" and data["alive"]]
    alive_villagers = [uid for uid, data in game.players.items() if data["role"] != "Sói" and data["alive"]]
    
    if not alive_wolves:
        asyncio.create_task(end_game(game, "🎉 **DÂN LÀNG CHIẾN THẮNG!** Đã tiêu diệt hết Sói."))
        return True
    if len(alive_wolves) >= len(alive_villagers):
        asyncio.create_task(end_game(game, "🐺 **SÓI CHIẾN THẮNG!** Số sói đã áp đảo dân làng."))
        return True
    return False

async def end_game(game, win_message):
    await game.ctx.send(win_message)
    
    summary_list = []
    for uid, data in game.players.items():
        role_emoji = "🐺" if data["role"] == "Sói" else ("🔮" if data["role"] == "Tiên Tri" else ("🛡️" if data["role"] == "Bảo Vệ" else "🧑‍🌾"))
        status = "🟢 Còn sống" if data["alive"] else "🔴 Đã chết"
        summary_list.append(f"• {data['member'].mention} — {role_emoji} **{data['role']}** ({status})")
        
    embed = discord.Embed(
        title="📜 TỔNG KẾT THÂN PHẬN VÁN ĐẤU",
        description="\n".join(summary_list),
        color=0xe74c3c
    )
    await game.ctx.send(embed=embed)
    
    if game.ctx.channel.id in game_lobbies:
        del game_lobbies[game.ctx.channel.id]

@bot.hybrid_command(name='masoi', aliases=['wolf'])
async def cmd_masoi(ctx):
    """Tạo phòng Ma Sói."""
    if ctx.channel.id in game_lobbies:
        game = game_lobbies[ctx.channel.id]
        if game.state != "waiting":
            await ctx.send("❌ Ván chơi đang diễn ra, không thể tạo phòng chờ mới!")
            return
        else:
            try:
                if game.lobby_message:
                    await game.lobby_message.delete()
            except Exception:
                pass
    else:
        game = WerewolfGame(ctx)
        game_lobbies[ctx.channel.id] = game
    
    view = JoinGameView(game)
    embed = discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=f"Bấm nút bên dưới để tham gia phòng chơi!\nChủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5 người).\n\n**Danh sách người chơi ({len(game.players)}):**\n" + 
                    ("\n".join([f"• {data['member'].mention}" for data in game.players.values()]) if game.players else "Chưa có ai tham gia..."),
        color=UI_COLORS["cyan"]
    )
    game.lobby_message = await ctx.send(embed=embed, view=view)
    add_exp_to_user(ctx.author.id, 20)


# ==========================================
# 1.2. HỆ THỐNG BẮT THÚ (.h), ĐỘI HÌNH (.dh), DANH SÁCH (.ds), SHOP ĐỒ VÀ CHIẾN ĐẤU (.dau)
# ==========================================
def roll_rarity_pet():
    # Tỷ lệ giảm dần từ 100% xuống 0.1% theo yêu cầu
    # Phân nhóm theo độ hiếm: Thường (50%), Hiếm (30%), Sử Thi (15%), Huyền Thoại (4.9%), Thần Thoại (0.1%)
    roll = random.uniform(0.0, 100.0)
    if roll <= 0.1:
        target_rarity = "Thần Thoại"
    elif roll <= 5.0: # 0.1 đến 5.0 (~4.9%)
        target_rarity = "Huyền Thoại"
    elif roll <= 20.0: # 5.0 đến 20.0 (15%)
        target_rarity = "Sử Thi"
    elif roll <= 50.0: # 20.0 đến 50.0 (30%)
        target_rarity = "Hiếm"
    else: # 50.0 đến 100.0 (50%)
        target_rarity = "Thường"
    
    filtered = [p for p in ALL_PETS if p["rarity"] == target_rarity]
    if not filtered:
        filtered = ALL_PETS
    return random.choice(filtered)

class HuntView(discord.ui.View):
    def __init__(self, pet_template):
        super().__init__(timeout=60)
        self.pet_template = pet_template

    @discord.ui.button(label="Bắt Thú (Dùng Vợt)", style=discord.ButtonStyle.success, emoji="🥅", custom_id="catch_pet_btn")
    async def catch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid_str = str(interaction.user.id)
        
        # Kiểm tra xem user có Vợt Bắt Thú trong user_items không
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT quantity FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, "vot_bat_thu"))
        item_row = cursor.fetchone()
        
        if not item_row or item_row[0] <= 0:
            conn.close()
            await interaction.response.send_message(f"❌ Bạn không có **Vợt Bắt Thú** trong túi! Hãy vào cửa hàng mua bằng lệnh `.shop` hoặc `.buy 4` trước.", ephemeral=True)
            return
            
        # Trừ 1 vợt
        new_qty = item_row[0] - 1
        if new_qty <= 0:
            cursor.execute('DELETE FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, "vot_bat_thu"))
        else:
            cursor.execute('UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_id = ?', (new_qty, uid_str, "vot_bat_thu"))
            
        # Thêm thú vào database user_pets
        cursor.execute('SELECT id FROM user_pets WHERE user_id = ? AND pet_index = ?', (uid_str, self.pet_template["id"]))
        existing = cursor.fetchone()
        
        caught_new = False
        if not existing:
            cursor.execute('''
                INSERT INTO user_pets (user_id, pet_index, pet_name, hp, atk, rarity, in_team)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (uid_str, self.pet_template["id"], self.pet_template["name"], self.pet_template["hp"], self.pet_template["atk"], self.pet_template["rarity"]))
            caught_new = True
        conn.commit()
        conn.close()
        
        embed = discord.Embed(
            title="🎯 BẮT THÚ THÀNH CÔNG!",
            description=f"🎉 **{interaction.user.mention}** đã dùng Vợt bắt thành công:\n\n✨ **{self.pet_template['name']}** (`{self.pet_template['rarity']}`)\n❤️ HP: `{self.pet_template['hp']}` | ⚔️ ATK: `{self.pet_template['atk']}`",
            color=discord.Color.green()
        )
        if caught_new:
            embed.set_footer(text="🎉 Chúc mừng! Con thú này đã được thêm mới vào bộ sưu tập của bạn!")
        else:
            embed.set_footer(text="💡 Bạn đã sở hữu con thú này rồi, tiếp tục săn lùng các con khác nhé!")
            
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
        add_exp_to_user(interaction.user.id, 60)

class WildCatchView(discord.ui.View):
    def __init__(self, pet_template):
        super().__init__(timeout=30)
        self.pet_template = pet_template

    @discord.ui.button(label="Bắt Thú Hoang (Free)", style=discord.ButtonStyle.primary, emoji="✨", custom_id="catch_wild_btn")
    async def wild_catch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid_str = str(interaction.user.id)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM user_pets WHERE user_id = ? AND pet_index = ?', (uid_str, self.pet_template["id"]))
        existing = cursor.fetchone()
        
        caught_new = False
        if not existing:
            cursor.execute('''
                INSERT INTO user_pets (user_id, pet_index, pet_name, hp, atk, rarity, in_team)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            ''', (uid_str, self.pet_template["id"], self.pet_template["name"], self.pet_template["hp"], self.pet_template["atk"], self.pet_template["rarity"]))
            caught_new = True
        conn.commit()
        conn.close()
        
        embed = discord.Embed(
            title="🌿 BẮT THÚ HOANG THÀNH CÔNG!",
            description=f"🎉 **{interaction.user.mention}** đã bắt nhanh chú thú hoang xuất hiện ngẫu nhiên:\n\n✨ **{self.pet_template['name']}** (`{self.pet_template['rarity']}`)\n❤️ HP: `{self.pet_template['hp']}` | ⚔️ ATK: `{self.pet_template['atk']}`",
            color=discord.Color.green()
        )
        if caught_new:
            embed.set_footer(text="🎉 Quá may mắn! Thú hoang xuất hiện miễn phí và đã vào bộ sưu tập!")
        else:
            embed.set_footer(text="💡 Bạn đã có con thú này rồi!")
            
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()
        add_exp_to_user(interaction.user.id, 80)

class ShopBuyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mua Thuốc Cường Lực (.buy 1)", style=discord.ButtonStyle.primary, custom_id="shop_buy_1")
    async def buy_item_1(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = SHOP_ITEMS["1"]
        uid_str = str(interaction.user.id)
        u_data = get_user(uid_str)
        if u_data[0] < item["price"]:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Cần `{item['price']:,}` {COIN_EMOJI}.", ephemeral=True)
            return
        new_coin = u_data[0] - item["price"]
        update_user_full(uid_str, new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT quantity FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, item["id"]))
        row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_id = ?', (row[0] + 1, uid_str, item["id"]))
        else:
            cursor.execute('INSERT INTO user_items (user_id, item_id, quantity) VALUES (?, ?, 1)', (uid_str, item["id"]))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ Mua thành công **{item['name']}** với giá **{item['price']:,}** {COIN_EMOJI}!", ephemeral=True)
        add_exp_to_user(interaction.user.id, 30)

    @discord.ui.button(label="Mua Giáp Kim Cương (.buy 2)", style=discord.ButtonStyle.primary, custom_id="shop_buy_2")
    async def buy_item_2(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = SHOP_ITEMS["2"]
        uid_str = str(interaction.user.id)
        u_data = get_user(uid_str)
        if u_data[0] < item["price"]:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Cần `{item['price']:,}` {COIN_EMOJI}.", ephemeral=True)
            return
        new_coin = u_data[0] - item["price"]
        update_user_full(uid_str, new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT quantity FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, item["id"]))
        row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_id = ?', (row[0] + 1, uid_str, item["id"]))
        else:
            cursor.execute('INSERT INTO user_items (user_id, item_id, quantity) VALUES (?, ?, 1)', (uid_str, item["id"]))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ Mua thành công **{item['name']}** với giá **{item['price']:,}** {COIN_EMOJI}!", ephemeral=True)
        add_exp_to_user(interaction.user.id, 30)

    @discord.ui.button(label="Mua Huy Hiệu Thần Thú (.buy 3)", style=discord.ButtonStyle.success, custom_id="shop_buy_3")
    async def buy_item_3(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = SHOP_ITEMS["3"]
        uid_str = str(interaction.user.id)
        u_data = get_user(uid_str)
        if u_data[0] < item["price"]:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Cần `{item['price']:,}` {COIN_EMOJI}.", ephemeral=True)
            return
        new_coin = u_data[0] - item["price"]
        update_user_full(uid_str, new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT quantity FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, item["id"]))
        row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_id = ?', (row[0] + 1, uid_str, item["id"]))
        else:
            cursor.execute('INSERT INTO user_items (user_id, item_id, quantity) VALUES (?, ?, 1)', (uid_str, item["id"]))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ Mua thành công **{item['name']}** với giá **{item['price']:,}** {COIN_EMOJI}!", ephemeral=True)
        add_exp_to_user(interaction.user.id, 50)

    @discord.ui.button(label="Mua Vợt Bắt Thú (.buy 4)", style=discord.ButtonStyle.secondary, custom_id="shop_buy_4")
    async def buy_item_4(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = SHOP_ITEMS["4"]
        uid_str = str(interaction.user.id)
        u_data = get_user(uid_str)
        if u_data[0] < item["price"]:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Cần `{item['price']:,}` {COIN_EMOJI}.", ephemeral=True)
            return
        new_coin = u_data[0] - item["price"]
        update_user_full(uid_str, new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT quantity FROM user_items WHERE user_id = ? AND item_id = ?', (uid_str, item["id"]))
        row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_id = ?', (row[0] + 1, uid_str, item["id"]))
        else:
            cursor.execute('INSERT INTO user_items (user_id, item_id, quantity) VALUES (?, ?, 1)', (uid_str, item["id"]))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ Mua thành công **{item['name']}** với giá **{item['price']:,}** {COIN_EMOJI}!", ephemeral=True)
        add_exp_to_user(interaction.user.id, 20)


# ==========================================
# 3. HỆ THỐNG TÀI XỈU GIAO DIỆN NÚT BẤM (CÓ ĐẾM NGƯỢC 35s LIÊN TỤC)
# ==========================================
class TaiXiuModal(discord.ui.Modal, title="NHẬP SỐ TIỀN CƯỢC"):
    money_input = discord.ui.TextInput(
        label="SỐ TIỀN CƯỢC (Hoặc gõ 'all')",
        placeholder="Nhập số tiền hoặc all...",
        min_length=1,
        max_length=15
    )

    def __init__(self, outer_view, choice_type):
        super().__init__()
        self.outer_view = outer_view
        self.choice_type = choice_type

    async def on_submit(self, interaction: discord.Interaction):
        u_data = get_user(str(interaction.user.id))
        bet = parse_bet_amount(u_data[0], self.money_input.value)
        
        if bet is None or bet <= 0:
            await interaction.response.send_message("❌ Số tiền cược không hợp lệ!", ephemeral=True)
            return

        if u_data[0] < bet:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", ephemeral=True)
            return

        if interaction.user.id not in self.outer_view.bets:
            self.outer_view.bets[interaction.user.id] = {}
        
        current_door_bet = self.outer_view.bets[interaction.user.id].get(self.choice_type, 0)
        self.outer_view.bets[interaction.user.id][self.choice_type] = current_door_bet + bet
        
        await interaction.response.send_message(f"✅ Đã đặt cược **{bet:,}** {COIN_EMOJI} vào cửa **{str(self.choice_type).upper()}**!", ephemeral=True)

class TaiXiuButtonsView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=35)
        self.ctx = ctx
        self.bets = {}

        self.add_item(TaiXiuChoiceButton("Xỉu (3-10)", "Xiu (3-10)", discord.ButtonStyle.success, 0))
        self.add_item(TaiXiuChoiceButton("Tài (11-18)", "Tai (11-18)", discord.ButtonStyle.danger, 0))
        self.add_item(TaiXiuChoiceButton("Chẵn", "Chẵn", discord.ButtonStyle.secondary, 0))
        self.add_item(TaiXiuChoiceButton("Lẻ", "Lẻ", discord.ButtonStyle.secondary, 0))

        for i in range(3, 19):
            row_idx = 1 + (i - 3) // 5
            self.add_item(TaiXiuChoiceButton(f"Số {i}", f"Số {i}", discord.ButtonStyle.primary, row_idx))

class TaiXiuChoiceButton(discord.ui.Button):
    def __init__(self, label, choice_value, style, row_val):
        super().__init__(label=label, style=style, row=row_val)
        self.choice_value = choice_value

    async def callback(self, interaction: discord.Interaction):
        modal = TaiXiuModal(self.view, self.choice_value)
        await interaction.response.send_modal(modal)

@bot.hybrid_command(name='sicbo', aliases=['taixiu'])
async def cmd_tx(ctx):
    """Chơi Tài Xỉu."""
    view = TaiXiuButtonsView(ctx)
    
    def get_tx_desc(sec):
        return (
            f"🎲 **Tài Xỉu 💤KS-BOT💤 - Nhà cái đến từ Châu Á!** 🔥🔥🔥\n\n"
            f"Chọn Tài (11–18), Xỉu (3–10), Chẵn/Lẻ hoặc số cụ thể (3–18) để đặt cược.\n"
            f"Sau khi chọn, nhập số gold hoặc `all` muốn cược.\n"
            f"⏳ Thời gian đặt cược còn lại: **{sec} giây**."
        )

    embed = discord.Embed(title=f"TÀI XỈU #{random.randint(1000, 9999)}", description=get_tx_desc(35), color=0x4a2e80)
    msg = await ctx.send(embed=embed, view=view)
    
    for remaining in range(34, -1, -1):
        await asyncio.sleep(1)
        if view.is_finished():
            break
        embed.description = get_tx_desc(remaining)
        try:
            await msg.edit(embed=embed, view=view)
        except Exception:
            pass

    view.stop()

    shaking_embed = discord.Embed(
        title=f"🎲 ĐANG LẮC XÚC XẮC...",
        description="🎲 🎲 🎲\nNhà cái đang xóc đĩa, xin vui lòng chờ kết quả...",
        color=0x4a2e80
    )
    try:
        await msg.edit(embed=shaking_embed, view=None)
    except Exception:
        pass
    
    await asyncio.sleep(6)

    d1 = random.randint(1, 6)
    d2 = random.randint(1, 6)
    d3 = random.randint(1, 6)
    total = d1 + d2 + d3
    is_tai = total >= 11
    is_chan = total % 2 == 0

    dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
    dice_str = f"{dice_emojis[d1]} {dice_emojis[d2]} {dice_emojis[d3]}"
    ket_qua_tx = "Tài" if is_tai else "Xỉu"
    ket_qua_cl = "Chẵn" if is_chan else "Lẻ"

    result_desc = (
        f"Kết quả:\n"
        f"{dice_str} = {total}\n"
        f"Tài/Xỉu: **{ket_qua_tx}**\n"
        f"Chẵn/Lẻ: **{ket_qua_cl}**\n\n"
        f"**Tổng kết:**\n"
    )

    if not view.bets:
        result_desc += "Không có ai tham gia đặt cược ván này."
    else:
        for uid, user_bets in view.bets.items():
            u_data = get_user(uid)
            coin, level, exp, streak, last_time, history = u_data
            
            total_bet_user = sum(user_bets.values())
            coin = max(0, coin - total_bet_user)

            total_win_payout = 0
            bet_descriptions = []

            for choice_name, bet_amt in user_bets.items():
                bet_descriptions.append(f"**{choice_name}** ({bet_amt:,})")
                
                won = False
                multiplier = 1.0

                c_lower = choice_name.lower()
                if "tài" in c_lower and is_tai:
                    won = True
                elif "xỉu" in c_lower and not is_tai:
                    won = True
                elif "chẵn" in c_lower and is_chan:
                    won = True
                elif "lẻ" in c_lower and not is_chan:
                    won = True
                elif "số" in c_lower:
                    try:
                        num_val = int("".join([c for c in choice_name if c.isdigit()]))
                        if num_val == total:
                            won = True
                            multiplier = 10.0
                    except Exception:
                        pass

                if won:
                    total_win_payout += int(bet_amt * multiplier) + bet_amt

            net_profit = total_win_payout - total_bet_user
            coin += total_win_payout
            update_user_full(uid, coin, level, exp, streak, last_time, history)
            add_exp_to_user(uid, 60)

            bets_str_joined = " và ".join(bet_descriptions)
            if net_profit > 0:
                result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Lụm **+{net_profit:,}** {COIN_EMOJI} (Tổng nhận: {total_win_payout:,})\n"
            elif net_profit < 0:
                result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Toạch **-{total_bet_user:,}** {COIN_EMOJI}\n"
            else:
                result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Hòa vốn.\n"

    final_embed = discord.Embed(description=result_desc, color=0x2b5345)
    try:
        await msg.edit(embed=final_embed, view=None)
    except Exception:
        pass


# ==========================================
# 4. HỆ THỐNG BẦU CUA (CÓ ĐẾM NGƯỢC 30s LIÊN TỤC)
# ==========================================
BC_ANIMALS = ["nai", "bầu", "gà", "cá", "cua", "tôm"]
BC_EMOJIS = {
    "nai": "🦌",
    "bầu": "🎃",
    "gà": "🐓",
    "cá": "🐠",
    "cua": "🦀",
    "tôm": "🦐"
}

class BauCuaModal(discord.ui.Modal, title="NHẬP SỐ TIỀN CƯỢC BẦU CUA"):
    money_input = discord.ui.TextInput(
        label="SỐ TIỀN CƯỢC (Hoặc gõ 'all')",
        placeholder="Nhập số tiền hoặc all...",
        min_length=1,
        max_length=15
    )

    def __init__(self, outer_view, choice_type):
        super().__init__()
        self.outer_view = outer_view
        self.choice_type = choice_type

    async def on_submit(self, interaction: discord.Interaction):
        u_data = get_user(str(interaction.user.id))
        bet = parse_bet_amount(u_data[0], self.money_input.value)
        
        if bet is None or bet <= 0:
            await interaction.response.send_message("❌ Số tiền cược không hợp lệ!", ephemeral=True)
            return
        if bet > 250000:
            await interaction.response.send_message("❌ Số tiền cược tối đa mỗi lần là 250,000!", ephemeral=True)
            return

        if u_data[0] < bet:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", ephemeral=True)
            return

        if interaction.user.id not in self.outer_view.bets:
            self.outer_view.bets[interaction.user.id] = {}
        
        current_door_bet = self.outer_view.bets[interaction.user.id].get(self.choice_type, 0)
        self.outer_view.bets[interaction.user.id][self.choice_type] = current_door_bet + bet
        
        await interaction.response.send_message(f"✅ Đã đặt cược **{bet:,}** {COIN_EMOJI} vào cửa **{self.choice_type.capitalize()}**!", ephemeral=True)

class BauCuaButtonsView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bets = {}

        self.add_item(BauCuaChoiceButton("Nai", "nai", "🦌", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Bầu", "bầu", "🎃", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Gà", "gà", "🐓", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Cá", "cá", "🐠", discord.ButtonStyle.primary, 1))
        self.add_item(BauCuaChoiceButton("Cua", "cua", "🦀", discord.ButtonStyle.primary, 1))
        self.add_item(BauCuaChoiceButton("Tôm", "tôm", "🦐", discord.ButtonStyle.primary, 1))

class BauCuaChoiceButton(discord.ui.Button):
    def __init__(self, label, choice_value, emoji, style, row_val):
        super().__init__(label=label, emoji=emoji, style=style, row=row_val)
        self.choice_value = choice_value

    async def callback(self, interaction: discord.Interaction):
        modal = BauCuaModal(self.view, self.choice_value)
        await interaction.response.send_modal(modal)

@bot.hybrid_command(name='baucua')
async def cmd_baucua(ctx):
    """Chơi Bầu Cua."""
    view = BauCuaButtonsView(ctx)
    
    def get_bc_desc(sec):
        return (
            f"🎲 **Bầu Cua 💤KS-BOT💤 - Nhà cái đến từ Việt Nam!** 🔥🔥🔥\n\n"
            f"Chọn bầu, cua, tôm, cá, gà, hoặc nai để cược (nhập số hoặc `all`, tối đa 250,000 {COIN_EMOJI}).\n"
            f"⏳ Thời gian đếm ngược đặt cược: **{sec} giây**."
        )

    embed = discord.Embed(description=get_bc_desc(30), color=0x2b5345)
    msg = await ctx.send(embed=embed, view=view)
    
    for remaining in range(29, -1, -1):
        await asyncio.sleep(1)
        if view.is_finished():
            break
        embed.description = get_bc_desc(remaining)
        try:
            await msg.edit(embed=embed, view=view)
        except Exception:
            pass

    view.stop()

    shaking_embed = discord.Embed(
        title="🎲 ĐANG LẮC BẦU CUA...",
        description="🎲 Xóc xóc xóc... Nhà cái đang mở lồng bàn, chờ 10 giây...",
        color=0x2b5345
    )
    try:
        await msg.edit(embed=shaking_embed, view=None)
    except Exception:
        pass
    
    await asyncio.sleep(10)

    result_dice = [random.choice(BC_ANIMALS) for _ in range(3)]
    dice_display = f"{BC_EMOJIS[result_dice[0]]} {BC_EMOJIS[result_dice[1]]} {BC_EMOJIS[result_dice[2]]}"

    result_desc = f"{dice_display}\n**Tổng kết:**\n"

    if not view.bets:
        result_desc += "Không có ai tham gia đặt cược ván này."
    else:
        for uid, user_bets in view.bets.items():
            u_data = get_user(uid)
            coin, level, exp, streak, last_time, history = u_data
            
            total_bet_user = sum(user_bets.values())
            coin = max(0, coin - total_bet_user)
            
            total_win_payout = 0
            bet_descriptions = []

            for choice_name, bet_amt in user_bets.items():
                bet_descriptions.append(f"{choice_name} ({bet_amt:,})")
                matches = result_dice.count(choice_name)
                if matches > 0:
                    total_win_payout += bet_amt + (bet_amt * matches)

            net_profit = total_win_payout - total_bet_user
            coin += total_win_payout
            update_user_full(uid, coin, level, exp, streak, last_time, history)
            add_exp_to_user(uid, 50)

            bets_str_joined = " và ".join(bet_descriptions)
            if total_win_payout > 0:
                result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Lụm **+{net_profit:,}** {COIN_EMOJI} (Tổng nhận: {total_win_payout:,})\n"
            else:
                result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Toạch **-{total_bet_user:,}** {COIN_EMOJI}\n"

    final_embed = discord.Embed(description=result_desc, color=0x2b5345)
    try:
        await msg.edit(embed=final_embed, view=None)
    except Exception:
        pass


# ==========================================
# 5. HỆ THỐNG TÙ XÌ (.tuxi + tiền)
# ==========================================
class TuXiView(discord.ui.View):
    def __init__(self, author, bet_amount):
        super().__init__(timeout=60)
        self.author = author
        self.bet_amount = bet_amount
        self.choice = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Đây không phải ván Tù Xì của bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Búa", style=discord.ButtonStyle.primary, emoji="✊")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "búa"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Kéo", style=discord.ButtonStyle.success, emoji="✌️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "kéo"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Bao", style=discord.ButtonStyle.danger, emoji="🖐️")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "bao"
        self.stop()
        await interaction.response.defer()

@bot.hybrid_command(name='rps', aliases=['tuxi'])
async def cmd_tuxi(ctx, bet_str: str = "100"):
    """Chơi Kéo Búa Bao, cược coin."""
    user_id = str(ctx.author.id)
    u_data = get_user(user_id)
    
    bet = parse_bet_amount(u_data[0], bet_str)
    if bet is None or bet <= 0:
        await ctx.send("❌ Số tiền cược không hợp lệ!", delete_after=5)
        return

    if u_data[0] < bet:
        await ctx.send(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", delete_after=5)
        return

    view = TuXiView(ctx.author, bet)

    desc = (
        f"Trò chơi Kéo Búa Bao. Chọn chiến thuật của bạn!\n\n"
        f"Mức cược: **{bet:,}** {COIN_EMOJI}. Hãy bấm vào nút bên dưới để chọn."
    )

    embed = discord.Embed(description=desc, color=discord.Color.blue())
    msg = await ctx.send(embed=embed, view=view)

    await view.wait()

    current_coin = get_user(user_id)[0]
    current_coin = max(0, current_coin - bet)
    update_user_full(user_id, current_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])

    if view.choice is None:
        await msg.edit(
            content=f"{ctx.author.mention} đã hết thời gian chờ và bị xử thua mất **-{bet:,}** {COIN_EMOJI}!",
            embed=None,
            view=None
        )
        return

    bot_choice = random.choice(["búa", "kéo", "bao"])
    user_choice = view.choice
    emojis = {"búa": "✊", "kéo": "✌️", "bao": "🖐️"}

    u_data_updated = get_user(user_id)
    c_coin = u_data_updated[0]

    result_msg = ""
    net_change = 0
    if user_choice == bot_choice:
        c_coin += bet 
        result_msg = f"Hòa! Được hoàn lại {bet:,} {COIN_EMOJI}."
    elif (
        (user_choice == "búa" and bot_choice == "kéo") or
        (user_choice == "kéo" and bot_choice == "bao") or
        (user_choice == "bao" and bot_choice == "búa")
    ):
        net_change = bet 
        c_coin += bet * 2 
        result_msg = f"Thắng! Bạn nhận được **+{net_change:,}** {COIN_EMOJI}."
    else:
        net_change = -bet
        result_msg = f"Thua! Bạn mất **{net_change:,}** {COIN_EMOJI}."

    update_user_full(user_id, c_coin, u_data_updated[1], u_data_updated[2], u_data_updated[3], u_data_updated[4], u_data_updated[5])
    add_exp_to_user(user_id, 30)

    final_content = (
        f"{ctx.author.mention} đã tham gia game Kéo Búa Bao!\n"
        f"**Kết quả:** Bot chọn {emojis[bot_choice]} (`{bot_choice}`), bạn chọn {emojis[user_choice]} (`{user_choice}`). {result_msg}"
    )
    await msg.edit(content=final_content, embed=None, view=None)


# ==========================================
# 6. LỆNH ĐỔI BIỆT DANH (.name)
# ==========================================
@bot.hybrid_command(name='nickname', aliases=['name'])
async def cmd_name(ctx, *, new_nick: str = None):
    """Đổi biệt danh của bạn."""
    if not new_nick:
        await ctx.send("❌ Cú pháp: `.nickname <tên_mới>` (hoặc `.name`)", delete_after=5)
        return
    
    if len(new_nick) > 32:
        await ctx.send("❌ Biệt danh không được vượt quá 32 ký tự!", delete_after=5)
        return

    try:
        if ctx.author == ctx.guild.owner:
            await ctx.send("❌ Bot không thể đổi biệt danh của Chủ Server!", delete_after=5)
            return

        target_nick = new_nick
        await ctx.author.edit(nick=target_nick, reason="Người dùng đổi tên bằng lệnh .name")
        await ctx.send(f"✅ Đã đổi biệt danh thành công thành: **{target_nick}**!")
        add_exp_to_user(ctx.author.id, 10)
    except Exception as e:
        await ctx.send(f"❌ Không thể đổi biệt danh: {e}", delete_after=6)


# ==========================================
# 7. HỆ THỐNG AFK (.afk)
# ==========================================
@bot.hybrid_command(name='afk')
async def cmd_afk(ctx, *, reason: str = None):
    """Bật chế độ AFK."""
    uid_str = str(ctx.author.id)
    old_nick = ctx.author.nick or ctx.author.name
    
    AFK_USERS[uid_str] = {
        "reason": reason if reason else "thí chủ đi vắng rồi đừng gọi",
        "old_nick": old_nick
    }

    try:
        if ctx.author != ctx.guild.owner and ctx.guild.me.guild_permissions.manage_nicknames:
            clean_name = old_nick
            if "[AFK]" not in clean_name:
                new_afk_nick = f"[AFK] {clean_name}"
                if len(new_afk_nick) <= 32:
                    await ctx.author.edit(nick=new_afk_nick, reason="Bật chế độ AFK")
    except Exception:
        pass

    display_reason = reason if reason else "thí chủ đi vắng rồi đừng gọi"
    await ctx.send(f"💤 **{ctx.author.display_name}** đã bật trạng thái AFK. Lý do: *{display_reason}*")


# ==========================================
# 8. TRÒ CHƠI DÒ MÌN (MINES)
# ==========================================
class MinesButton(discord.ui.Button):
    def __init__(self, index, view_obj):
        super().__init__(style=discord.ButtonStyle.secondary, label="?", row=index//3)
        self.index = index
        self.mine_view = view_obj

    async def callback(self, interaction: discord.Interaction):
        await self.mine_view.handle_click(interaction, self)

class MinesView(discord.ui.View):
    def __init__(self, ctx, bet, mines_count):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.bet = bet
        self.mines_count = mines_count
        self.grid_size = 9
        self.mines = random.sample(range(self.grid_size), self.mines_count)
        self.revealed = [False] * self.grid_size
        self.game_over = False
        self.diamonds_found = 0

        for i in range(self.grid_size):
            self.add_item(MinesButton(i, self))

    def calculate_multiplier(self):
        n = self.grid_size
        k = self.mines_count
        r = self.diamonds_found
        if r == 0:
            return 1.0
        mult = math.prod([(n - i) / (n - k - i) for i in range(r)]) * 0.95
        return round(mult, 2)

    async def handle_click(self, interaction: discord.Interaction, button: MinesButton):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Đây không phải ván chơi của bạn!", ephemeral=True)
            return
        if self.game_over:
            await interaction.response.send_message("❌ Ván chơi đã kết thúc!", ephemeral=True)
            return

        index = button.index

        if index in self.mines:
            self.game_over = True
            for child in self.children:
                if isinstance(child, MinesButton):
                    child.disabled = True
                    if child.index in self.mines:
                        child.emoji = "💣"
                        child.label = ""
                        child.style = discord.ButtonStyle.danger
                    else:
                        child.emoji = "💎"
                        child.label = ""
                        child.style = discord.ButtonStyle.success
                elif isinstance(child, discord.ui.Button) and child.label == "Cash Out":
                    child.disabled = True

            u_data = get_user(str(self.ctx.author.id))
            new_coin = max(0, u_data[0] - self.bet)
            update_user_full(str(self.ctx.author.id), new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])

            embed = discord.Embed(
                title=f"💥 **{self.ctx.author.display_name}** đã dính mìn và mất **-{self.bet:,}** {COIN_EMOJI}!",
                description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Winnings:** `0 (0.00x)`",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        else:
            self.revealed[index] = True
            self.diamonds_found += 1
            current_mult = self.calculate_multiplier()

            button.emoji = "💎"
            button.label = ""
            button.style = discord.ButtonStyle.success
            button.disabled = True

            max_safe = self.grid_size - self.mines_count
            if self.diamonds_found >= max_safe:
                await self.do_cash_out(interaction, win_multiplier=current_mult)
                return

            next_mult = round(math.prod([(self.grid_size - i) / (self.grid_size - self.mines_count - i) for i in range(self.diamonds_found + 1)]) * 0.95, 2)
            
            embed = discord.Embed(
                title=f"💎 **{self.ctx.author.display_name}** đang dò mìn...",
                description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Cash Out:** `{int(self.bet * current_mult):,}` (`{current_mult}x`)\n**Next:** `+{int(self.bet * next_mult):,}` (`{next_mult}x`)",
                color=MAIN_COLOR
            )
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success, row=3)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Đây không phải ván chơi của bạn!", ephemeral=True)
            return
        if self.game_over:
            await interaction.response.send_message("❌ Ván chơi đã kết thúc!", ephemeral=True)
            return
        if self.diamonds_found == 0:
            await interaction.response.send_message("❌ Bạn phải lật ít nhất 1 ô an toàn trước khi Cash Out!", ephemeral=True)
            return

        current_mult = self.calculate_multiplier()
        await self.do_cash_out(interaction, win_multiplier=current_mult)

    async def do_cash_out(self, interaction, win_multiplier):
        self.game_over = True
        winnings = int(self.bet * win_multiplier)
        net_profit = winnings - self.bet
        
        u_data = get_user(str(self.ctx.author.id))
        new_coin = u_data[0] + net_profit
        update_user_full(str(self.ctx.author.id), new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        add_exp_to_user(str(self.ctx.author.id), 50)

        for child in self.children:
            if isinstance(child, MinesButton):
                child.disabled = True
                if child.index in self.mines:
                    child.emoji = "💣"
                else:
                    child.emoji = "💎"
                child.label = ""
            elif isinstance(child, discord.ui.Button) and child.label == "Cash Out":
                child.style = discord.ButtonStyle.secondary
                child.disabled = True

        embed = discord.Embed(
            title=f"💎 **{self.ctx.author.display_name}** cashed out thành công!",
            description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Lãi:** `+{net_profit:,}` {COIN_EMOJI} (`{win_multiplier}x`)",
            color=discord.Color.green()
        )
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            await interaction.message.edit(embed=embed, view=self)
        self.stop()

@bot.hybrid_command(name='mine', aliases=['mines'])
async def cmd_mine(ctx, bet_str: str = None, boom_str: str = None):
    """Chơi Mines, cược coin."""
    u_data = get_user(str(ctx.author.id))
    bet = parse_bet_amount(u_data[0], bet_str)
    
    if bet is None or bet <= 0:
        await ctx.send("❌ Cú pháp: `.mine <tiền_cược/all> [số_boom]`", delete_after=6)
        return

    if u_data[0] < bet:
        await ctx.send(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", delete_after=5)
        return

    mines_count = 3
    if boom_str and boom_str.isdigit():
        mines_count = int(boom_str)
        if mines_count < 1 or mines_count >= 9:
            await ctx.send("❌ Số boom phải từ 1 đến 8!", delete_after=5)
            return

    view = MinesView(ctx, bet, mines_count)
    embed = discord.Embed(
        title=f"💣 **{ctx.author.display_name}** đã bắt đầu một ván Dò Mìn.",
        description=f"**Bet:** `{bet}` | **Mines:** `{mines_count}`\n**Cash Out:** `0 (0.00x)`\n**Next:** `14 (1.40x)`",
        color=MAIN_COLOR
    )
    await ctx.send(embed=embed, view=view)


# ==========================================
# 9. TIỆN ÍCH CƠ BẢN & CHUYỂN TIỀN
# ==========================================
@bot.hybrid_command(name='pay', aliases=['transfer'])
async def cmd_pay(ctx, member: discord.Member = None, amount_str: str = None):
    """Chuyển coin cho người khác."""
    if not member or not amount_str:
        await ctx.send("❌ Cú pháp: `.pay @user <số_tiền/all>`", delete_after=6)
        return
    
    if member.bot or member == ctx.author:
        await ctx.send("❌ Bạn không thể chuyển tiền cho bot hoặc chính mình!", delete_after=5)
        return

    sender_id = str(ctx.author.id)
    sender_data = get_user(sender_id)

    amount = parse_bet_amount(sender_data[0], amount_str)
    if amount is None or amount <= 0:
        await ctx.send("❌ Số tiền chuyển không hợp lệ!", delete_after=5)
        return

    if sender_data[0] < amount:
        await ctx.send(f"❌ Bạn không đủ tiền trong ví! Số dư: `{sender_data[0]:,}` {COIN_EMOJI}", delete_after=5)
        return

    receiver_id = str(member.id)
    receiver_data = get_user(receiver_id)

    update_user_full(sender_id, sender_data[0] - amount, sender_data[1], sender_data[2], sender_data[3], sender_data[4], sender_data[5])
    update_user_full(receiver_id, receiver_data[0] + amount, receiver_data[1], receiver_data[2], receiver_data[3], receiver_data[4], receiver_data[5])
    add_exp_to_user(sender_id, 10)

    embed = discord.Embed(
        title="💸 CHUYỂN TIỀN THÀNH CÔNG",
        description=f"📤 **{ctx.author.mention}** đã chuyển **+{amount:,}** {COIN_EMOJI} cho **{member.mention}**!",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.hybrid_command(name='av', aliases=['avt'])
async def cmd_av(ctx, member: discord.Member = None):
    """Xem ảnh đại diện."""
    target = member or ctx.author
    embed = discord.Embed(
        title=f"🖼️ MẶT TIỀN CỦA {target.display_name.upper()}",
        description=f"🔗 [Lưu ảnh HD]({target.display_avatar.url})",
        color=MAIN_COLOR
    )
    embed.set_image(url=target.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name='coin', aliases=['wallet'])
async def cmd_coin(ctx):
    """Xem số dư coin."""
    u_data = get_user(str(ctx.author.id))
    embed = discord.Embed(
        title=f"💰 KÉT SẮT CÁ NHÂN: {ctx.author.name}",
        description=f"💵 Coin: **{u_data[0]:,}** {COIN_EMOJI}\n⭐ Level: `Lv.{u_data[1]}` (EXP: {u_data[2]})\n🔥 Streak hoạt động: **{u_data[3]} ngày**",
        color=MAIN_COLOR
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.hybrid_command(name='daily')
async def cmd_daily(ctx):
    """Nhận lương hằng ngày."""
    uid = str(ctx.author.id)
    now = time.time()
    if uid in DIEM_DANH_TRACKER and now - DIEM_DANH_TRACKER[uid] < 20 * 3600:
        await ctx.send("Mới lấy lương hồi nãy! Mai quay lại.", delete_after=5)
        return
    DIEM_DANH_TRACKER[uid] = now
    u_data = get_user(uid)
    update_user_full(uid, u_data[0] + 1500, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
    add_exp_to_user(uid, 100)
    await ctx.send(f"🎁 Nhận lương thành công **+1,500** {COIN_EMOJI}!")

@bot.hybrid_command(name='top')
async def cmd_top(ctx):
    """Xem bảng xếp hạng đại gia."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, coin, streak FROM users ORDER BY coin DESC LIMIT 10')
    rows = cursor.fetchall()
    conn.close()
    desc = ""
    for idx, (u, c, s) in enumerate(rows, 1):
        desc += f"`Top {idx}` <@!{u}>: `{c:,}` {COIN_EMOJI} | Streak: **{s}🔥**\n"
    embed = discord.Embed(title="🏆 BẢNG PHONG THẦN ĐẠI GIA", description=desc, color=MAIN_COLOR)
    await ctx.send(embed=embed)

@bot.hybrid_command(name='help')
async def cmd_help(ctx):
    """Mở menu trợ giúp."""
    embed = discord.Embed(
        title=f"📖 BẢNG HƯỚNG DẪN {BOT_ORIGINAL_NAME}",
        description="Danh sách các lệnh khả dụng (hỗ trợ nhập `all` cho các lệnh cược):",
        color=MAIN_COLOR
    )
    embed.add_field(
        name="🐺 Trò Chơi Ma Sói (Mới)",
        value=(
            "• `.masoi`: Tạo phòng chờ chơi Ma Sói (tối thiểu 5 người).\n"
            "• `.manhmoi`: Tìm manh mối hack não (tối đa 2 lần/vòng ban ngày)."
        ),
        inline=False
    )
    embed.add_field(
        name="🐾 Hệ Thống Thú Hoang & Đấu Trường (Mới)",
        value=(
            "• `.h`: Đi săn bắt thú hoang ngẫu nhiên (cần Vợt Bắt Thú, tỷ lệ giảm dần từ 100% xuống 0.1%).\n"
            "• `.setthu`: Thả thú hoang ngẫu nhiên ra kênh (bắt miễn phí, lâu lâu mới có).\n"
            "• `.ds`: Xem danh sách bộ sưu tập thú đã bắt và chưa bắt.\n"
            "• `.add`: Tự động chọn những con thú mạnh nhất vào đội hình.\n"
            "• `.dh`: Xem thông tin đội hình và chỉ số.\n"
            "• `.shop`: Mở cửa hàng mua trang bị & Vợt Bắt Thú (có nút bấm mua nhanh).\n"
            "• `.buy <1/2/3/4>`: Mua đồ cường hóa hoặc vợt bắt thú.\n"
            "• `.dau @user`: Thách đấu PvP đội hình thú chiến (30 lượt)."
        ),
        inline=False
    )
    embed.add_field(
        name="🛡️ Kiểm Soát & Quản Trị",
        value=(
            "• `.khoamom @user`: Khóa mõm nhanh 5p.\n"
            "• `.unkhoamom @user`: Gỡ khóa mõm.\n"
            "• `.khoachat @user <thời_gian>`: Khóa chat tùy chỉnh.\n"
            "• `.unmute @user`: Gỡ mute."
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Tiện Ích & Mini-Game",
        value=(
            "• `.baucua`: Chơi Bầu Cua (có đếm ngược).\n"
            "• `.taixiu`: Mở bàn Tài Xỉu (có đếm ngược).\n"
            "• `.mine <tiền/all> [boom]`: Chơi game dò mìn.\n"
            "• `.tuxi <tiền/all>`: Chơi Kéo Búa Bao.\n"
            "• `.name <tên_mới>`: Đổi biệt danh nhanh.\n"
            "• `.afk [lý do]`: Bật chế độ AFK.\n"
            "• `.pay @user <tiền/all>`: Chuyển tiền.\n"
            "• `.av [@user]`: Xem avatar.\n"
            "• `.coin`: Xem số dư ví và streak.\n"
            "• `.daily`: Điểm danh nhận lương.\n"
            "• `.top`: Xem bảng xếp hạng."
        ),
        inline=False
    )
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


# ==========================================
# 10. LỆNH QUẢN TRỊ PHÂN QUYỀN
# ==========================================
@bot.hybrid_command(name='gag', aliases=['khoamom'])
async def cmd_khoamom(ctx, member: discord.Member = None):
    """Khóa mõm thành viên 5 phút."""
    if not member:
        await ctx.send("❌ Cú pháp: `.gag @user` (hoặc `.khoamom`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
        return
    try:
        await member.timeout(datetime.timedelta(minutes=5), reason="Khóa mõm 5p")
        await ctx.send(f"🤐 Đã khóa mõm **{member.mention}** trong **5 phút**!")
        add_exp_to_user(ctx.author.id, 10)
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}", delete_after=6)

@bot.hybrid_command(name='unmute', aliases=['unkhoamom', 'ungag'])
async def cmd_unmute(ctx, member: discord.Member = None):
    """Gỡ mute/timeout/khóa mõm cho thành viên."""
    if not member:
        await ctx.send("❌ Cú pháp: `.unmute @user` (hoặc `.ungag`, `.unkhoamom`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
        return
    try:
        await member.timeout(None, reason="Gỡ mute")
        await ctx.send(f"🔊 Đã gỡ mute cho **{member.mention}**!")
    except Exception as e:
        await ctx.send(f"❌ Lỗi: {e}", delete_after=6)


# ================================================================
# 🛡️ QUẢN TRỊ BỔ SUNG — BAN / KICK / MUTE / TIMEOUT / UNBAN
# Các lệnh này được kiểm tra quyền + hierarchy của Discord.
# ================================================================
def _server_owner_only(ctx):
    """Chỉ Chủ Bot (owner code) hoặc Chủ Server (guild owner) mới được dùng — không tính role/permission nào khác."""
    return bool(
        ctx.guild and (
            is_bot_owner(ctx.author)
            or ctx.author == ctx.guild.owner
        )
    )

# Giữ lại tên cũ để không phải sửa từng chỗ gọi — cả hai đều trỏ về cùng 1 luật: chỉ Owner/Chủ server.
_admin_actor = _server_owner_only
_moderator_actor = _server_owner_only

def _can_act_on_member(ctx, member):
    if not member or not ctx.guild:
        return False
    if is_bot_owner(member):
        return False
    if member == ctx.guild.owner:
        return False
    if member == ctx.author and not is_bot_owner(ctx.author):
        return False
    if not is_bot_owner(ctx.author) and ctx.author != ctx.guild.owner:
        if member.top_role >= ctx.author.top_role:
            return False
    me = ctx.guild.me
    if me and member.top_role >= me.top_role:
        return False
    return True

async def _delete_command_quietly(ctx):
    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.hybrid_command(name='mute', aliases=['khoachat', 'timeout'])
async def cmd_khoachat(ctx, member: discord.Member = None, time_str: str = None):
    """Khóa chat (mute/timeout) thành viên theo thời gian."""
    if not member or not time_str:
        await ctx.send("❌ Cú pháp: `.mute @user <10p/1h/1d>` (hoặc `.khoachat`, `.timeout`)", delete_after=5)
        return
    if not _server_owner_only(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được mute/timeout.", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể mute Owner, chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    # Chuyển sang logic timeout an toàn, không tạo role mute riêng.
    t_num = ''.join(c for c in time_str if c.isdigit())
    t_unit = ''.join(c for c in time_str if not c.isdigit()).lower()
    if not t_num.isdigit() or int(t_num) <= 0:
        await ctx.send("❌ Thời gian không hợp lệ.", delete_after=5)
        return
    val = int(t_num)
    delta = datetime.timedelta(hours=val) if 'h' in t_unit else datetime.timedelta(days=val) if 'd' in t_unit else datetime.timedelta(minutes=val)
    try:
        await member.timeout(delta, reason=f"Khóa chat bởi {ctx.author}")
        await ctx.send(f"🔇 Đã khóa chat {member.mention} trong **{time_str}**.", delete_after=5)
        add_exp_to_user(ctx.author.id, 15)
    except Exception as e:
        await ctx.send(f"❌ Không thể khóa chat: `{type(e).__name__}`", delete_after=6)

@bot.hybrid_command(name='kick')
async def cmd_kick(ctx, member: discord.Member = None, *, reason: str = 'Không ghi lý do'):
    """Kick thành viên khỏi server."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được kick.", delete_after=5)
        return
    if member is None:
        await ctx.send("❌ Cú pháp: `.kick @user [lý do]`", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể kick Owner/chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    me = ctx.guild.me
    if not me.guild_permissions.kick_members:
        await ctx.send("❌ Bot thiếu quyền **Kick Members**.", delete_after=5)
        return
    try:
        await member.kick(reason=f"{reason} | bởi {ctx.author}")
        await ctx.send(f"👢 Đã kick **{member}**.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Không thể kick: `{type(e).__name__}`", delete_after=6)

@bot.hybrid_command(name='ban')
async def cmd_ban(ctx, member: discord.Member = None, *, reason: str = 'Không ghi lý do'):
    """Ban thành viên khỏi server."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được ban.", delete_after=5)
        return
    if member is None:
        await ctx.send("❌ Cú pháp: `.ban @user [lý do]`", delete_after=5)
        return
    if not _can_act_on_member(ctx, member):
        await ctx.send("❌ Không thể ban Owner/chủ server hoặc thành viên có role ngang/cao hơn.", delete_after=5)
        return
    me = ctx.guild.me
    if not me.guild_permissions.ban_members:
        await ctx.send("❌ Bot thiếu quyền **Ban Members**.", delete_after=5)
        return
    try:
        await member.ban(reason=f"{reason} | bởi {ctx.author}")
        await ctx.send(f"🔨 Đã ban **{member}**.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Không thể ban: `{type(e).__name__}`", delete_after=6)

@bot.hybrid_command(name='unban')
async def cmd_unban(ctx, user_id: str = None):
    """Gỡ ban theo user ID."""
    if not _moderator_actor(ctx):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được unban.", delete_after=5)
        return
    if not user_id or not user_id.isdigit():
        await ctx.send("❌ Cú pháp: `.unban <user_id>`", delete_after=5)
        return
    me = ctx.guild.me
    if not me.guild_permissions.ban_members:
        await ctx.send("❌ Bot thiếu quyền **Ban Members**.", delete_after=5)
        return
    try:
        await ctx.guild.unban(discord.Object(id=int(user_id)), reason=f"Unban bởi {ctx.author}")
        await ctx.send(f"🔓 Đã unban ID `{user_id}`.", delete_after=5)
    except discord.NotFound:
        await ctx.send("❌ Không tìm thấy người này trong danh sách ban.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Không thể unban: `{type(e).__name__}`", delete_after=6)

# ==========================================
# 11. ON_MESSAGE & SỰ KIỆN GIÁM SÁT AFK, MA SÓI & STREAK
# ==========================================
@bot.event
async def on_member_update(before, after):
    if after.id == bot.user.id and (before.nick != after.nick or before.name != after.name):
        try:
            if after.guild.me.nick != BOT_ORIGINAL_NAME:
                await after.guild.me.edit(nick=BOT_ORIGINAL_NAME)
        except Exception:
            pass
        return

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    uid_str = str(message.author.id)
    ch_id = message.channel.id

    # Cộng EXP tự động cho mọi hoạt động chat/trò chuyện (Càng lên cao càng cần nhiều EXP)
    add_exp_to_user(uid_str, 5)

    # Kiểm tra chống lộ vai trò Ma Sói trong kênh chung
    if ch_id in game_lobbies:
        game = game_lobbies[ch_id]
        if message.author.id in game.players and game.players[message.author.id]["alive"]:
            content_lower = message.content.lower()
            reveal_keywords = ["tao là sói", "tôi là sói", "mình là sói", "tao tiên tri", "tôi là tiên tri", "mình tiên tri", "tao bảo vệ", "tôi là bảo vệ", "mình bảo vệ", "tao dân", "tôi là dân"]
            if any(kw in content_lower for kw in reveal_keywords):
                try:
                    await message.delete()
                    warning = await message.channel.send(f"⚠️ {message.author.mention} Không được phép tiết lộ vai trò trong kênh chung!")
                    await asyncio.sleep(4)
                    await warning.delete()
                except Exception:
                    pass

    if uid_str in AFK_USERS:
        afk_info = AFK_USERS.pop(uid_str)
        old_nick = afk_info["old_nick"]
        try:
            if message.author != message.guild.owner and message.guild.me.guild_permissions.manage_nicknames:
                await message.author.edit(nick=old_nick, reason="Tự động gỡ AFK khi nhắn tin")
        except Exception:
            pass
        await message.channel.send(f"👋 Chào mừng **{message.author.mention}** đã quay lại! Đã tắt chế độ AFK.", delete_after=6)

    if message.mentions:
        for mentioned_user in message.mentions:
            m_id = str(mentioned_user.id)
            if m_id in AFK_USERS:
                reason_msg = AFK_USERS[m_id]["reason"]
                await message.channel.send(f"💤 **{mentioned_user.display_name}** đang bận AFK với lý do: *{reason_msg}*")

    await process_user_streak_and_nickname(message.author)
    await bot.process_commands(message)


# ==========================================
# 12. KHỞI CHẠY BOT
# ==========================================
@bot.event
async def on_ready():
    print(f"🤖 BOT {BOT_ORIGINAL_NAME} ĐÃ KHỞI ĐỘNG THÀNH CÔNG VỚI PREFIX '.' !")
    
    # SỬA DÒNG NÀY ĐỂ BẬT MẶC ĐỊNH MÀU VÀNG (IDLE)
    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="với lệnh .help")
    )

    try:
        for guild in bot.guilds:
            if guild.me.nick != BOT_ORIGINAL_NAME:
                await guild.me.edit(nick=BOT_ORIGINAL_NAME)
    except Exception:
        pass


# ================================================================
# 🐺 MA SÓI V2.0 — CÂN BẰNG + VAI TRÒ NÂNG CAO
# CHỈ BỔ SUNG/GHI ĐÈ LOGIC MA SÓI. KHÔNG XÓA CÁC HỆ THỐNG CŨ.
# ================================================================

WW_ROLE_INFO_V2 = {
    "Sói": ("🐺", "Phe Sói", "Mỗi đêm bỏ phiếu kín chọn nạn nhân. Nếu có nhiều Sói, mục tiêu được nhiều Sói chọn nhất sẽ bị tấn công."),
    "Tiên Tri": ("🔮", "Phe Dân", "Mỗi đêm soi 1 người để biết người đó có phải Sói hay không."),
    "Bảo Vệ": ("🛡️", "Phe Dân", "Mỗi đêm bảo vệ 1 người. Không thể bảo vệ cùng một người trong 3 đêm liên tiếp."),
    "Phù Thủy": ("🧪", "Phe Dân", "Có 1 bình Cứu và 1 bình Độc, mỗi bình chỉ dùng 1 lần trong ván."),
    "Thợ Săn": ("🏹", "Phe Dân", "Khi chết được chọn 1 người còn sống để bắn. Có thể bỏ qua phát bắn."),
    "Cupid": ("💘", "Phe Dân", "Đêm đầu ghép 2 người. Hai người được DM cho nhau biết và trở thành Người Yêu."),
    "Trưởng Làng": ("👑", "Phe Dân", "Sau mỗi đêm được hỏi có muốn lộ thân phận hay chưa. Khi công khai, phiếu của Trưởng Làng tính 2. Lộ quá sớm có thể khiến Sói nhắm tới."),
    "Thám Tử": ("🕵️", "Phe Dân", "Mỗi đêm kiểm tra 2 người: biết họ cùng phe hay khác phe, nhưng không biết chính xác vai trò."),
    "Nhà Tiên Tri": ("📜", "Phe Dân", "Mỗi đêm có thể xem vai trò của 1 người đã chết để thu thập thông tin."),
    "Người Gác Cổng": ("🚪", "Phe Dân", "Mỗi đêm biết mình có bị nhắm tới hay không; không biết ai đã nhắm. Không chặn sát thương."),
    "Dân Làng": ("🧑‍🌾", "Phe Dân", "Không có kỹ năng đêm. Thảo luận, suy luận và bỏ phiếu."),
}


def ww_role_emoji(role):
    return WW_ROLE_INFO_V2.get(role, ("🎭", "Phe Dân", ""))[0]


def ww_is_wolf(role):
    return role in ("Sói", "Sói Alpha")


def ww_alive(game):
    return [uid for uid, d in game.players.items() if d.get("alive")]


def ww_player_label(game, uid):
    d = game.players.get(uid)
    return d["member"].display_name if d else "Không rõ"


def ww_distribution(n):
    # Cân bằng theo số người: không để 1-2 Sói ăn quá dễ ở lobby lớn.
    wolves = 1 if n <= 6 else 2 if n <= 9 else 3 if n <= 13 else 4 if n <= 17 else 5
    roles = ["Sói"] * wolves

    # Vai trò dân được mở dần theo quy mô, tránh nhồi quá nhiều chức năng ở lobby nhỏ.
    fixed = ["Tiên Tri", "Bảo Vệ", "Phù Thủy", "Thợ Săn"]
    if n >= 7:
        fixed.append("Cupid")
    if n >= 10:
        fixed.append("Trưởng Làng")
    if n >= 12:
        fixed.append("Thám Tử")
    if n >= 14:
        fixed.append("Nhà Tiên Tri")
    if n >= 16:
        fixed.append("Người Gác Cổng")

    # Nếu lobby quá nhỏ, ưu tiên bộ lõi trước.
    while len(roles) + len(fixed) > n:
        if "Nhà Tiên Tri" in fixed:
            fixed.remove("Nhà Tiên Tri")
        elif "Thám Tử" in fixed:
            fixed.remove("Thám Tử")
        elif "Trưởng Làng" in fixed:
            fixed.remove("Trưởng Làng")
        elif "Cupid" in fixed:
            fixed.remove("Cupid")
        else:
            break

    roles.extend(fixed)
    roles.extend(["Dân Làng"] * max(0, n - len(roles)))
    random.shuffle(roles)
    return roles


class WWHunterView(discord.ui.View):
    def __init__(self, game, hunter_id):
        super().__init__(timeout=20)
        self.game = game
        self.hunter_id = hunter_id
        options = []
        for uid, d in game.players.items():
            if d.get("alive") and uid != hunter_id:
                options.append(discord.SelectOption(
                    label=d["member"].display_name[:100],
                    description="Chọn để bắn hạ người này",
                    value=str(uid),
                    emoji="🏹"
                ))
        self.select = discord.ui.Select(
            placeholder="🏹 Chọn người để bắn (hoặc bỏ qua)...",
            min_values=1, max_values=1, options=options[:25]
        )
        self.select.callback = self.select_target
        self.add_item(self.select)

        skip = discord.ui.Button(label="Bỏ qua phát bắn", style=discord.ButtonStyle.secondary, emoji="🕊️")
        skip.callback = self.skip_shot
        self.add_item(skip)

    async def select_target(self, interaction):
        if interaction.user.id != self.hunter_id:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=False)
            return
        target_id = int(self.select.values[0])
        if target_id not in self.game.players or not self.game.players[target_id].get("alive"):
            await interaction.response.send_message("❌ Mục tiêu không còn sống.", ephemeral=False)
            return
        target_member = self.game.players[target_id]["member"]
        await interaction.response.send_message(
            f"🏹 **Đã chọn mục tiêu:** {target_member.mention}\n"
            "⚡ Phát bắn cuối của Thợ Săn đang được thực hiện...",
            ephemeral=False
        )
        await ww_hunter_shoot(self.game, self.hunter_id, target_id)
        self.stop()

    async def skip_shot(self, interaction):
        if interaction.user.id != self.hunter_id:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=False)
            return
        self.game.players[self.hunter_id]["hunter_used"] = True
        await interaction.response.edit_message(content="🕊️ Bạn đã bỏ qua phát bắn. Cây súng được giữ lại không dùng nữa trong ván.", view=None)
        self.stop()


async def ww_hunter_shoot(game, hunter_id, target_id):
    if hunter_id not in game.players or target_id not in game.players:
        return
    # Thợ Săn đã chết nhưng vẫn được phép dùng phát bắn cuối.
    # Chỉ chặn nếu ID/role không hợp lệ hoặc đã dùng phát bắn.
    if game.players[hunter_id].get("role") != "Thợ Săn":
        return
    if game.players[hunter_id].get("hunter_used"):
        return
    game.players[hunter_id]["hunter_used"] = True
    target = game.players[target_id]
    if not target.get("alive"):
        return
    target["alive"] = False
    await game.ctx.send(
        f"🏹 **THỢ SĂN PHẢN CÔNG!** {game.players[hunter_id]['member'].mention} "
        f"đã bắn hạ {target['member'].mention}.\n"
        f"🎭 Vai trò của mục tiêu: **{target['role']}**"
    )
    await ww_cupid_chain(game, target_id)


class WWCupidView(discord.ui.View):
    def __init__(self, game, cupid_id):
        super().__init__(timeout=30)
        self.game = game
        self.cupid_id = cupid_id
        options = [
            discord.SelectOption(label=d["member"].display_name[:100], value=str(uid), emoji="💘")
            for uid, d in game.players.items() if d.get("alive") and uid != cupid_id
        ][:25]
        self.first = discord.ui.Select(placeholder="💘 Chọn người yêu thứ nhất", options=options, min_values=1, max_values=1)
        self.second = discord.ui.Select(placeholder="💘 Chọn người yêu thứ hai", options=options, min_values=1, max_values=1)
        self.confirm = discord.ui.Button(label="Ghép đôi", style=discord.ButtonStyle.danger, emoji="💞")
        self.confirm.callback = self.confirm_pair
        self.add_item(self.first)
        self.add_item(self.second)
        self.add_item(self.confirm)

    async def confirm_pair(self, interaction):
        if interaction.user.id != self.cupid_id:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
            return
        if not self.first.values or not self.second.values:
            await interaction.response.send_message("❌ Hãy chọn đủ 2 người.", ephemeral=True)
            return
        a, b = int(self.first.values[0]), int(self.second.values[0])
        if a == b:
            await interaction.response.send_message("❌ Hai người yêu phải là 2 người khác nhau.", ephemeral=True)
            return
        self.game.players[a]["lover"] = b
        self.game.players[b]["lover"] = a
        self.game.players[a]["lover_linked"] = True
        self.game.players[b]["lover_linked"] = True
        await interaction.response.edit_message(content="💘 Ghép đôi thành công! Cả hai người sẽ được báo riêng.", view=None)
        await ww_notify_lovers(self.game, a, b)
        self.stop()


async def ww_notify_lovers(game, a, b):
    ma = game.players[a]["member"]
    mb = game.players[b]["member"]
    for me, partner in ((ma, mb), (mb, ma)):
        try:
            embed = discord.Embed(
                title="💘 BẠN ĐÃ CÓ NGƯỜI YÊU",
                description=(
                    f"Bạn và **{partner.display_name}** đã được Cupid ghép thành một cặp.\n\n"
                    "❤️ Hai người **biết nhau là Người Yêu**.\n"
                    "🛡️ Hãy phối hợp, bảo vệ và cân nhắc vote để cả hai sống sót.\n"
                    "⚠️ Nếu một người chết, người còn lại cũng sẽ chết theo."
                ),
                color=0xff4d8d
            )
            await me.send(embed=embed)
        except Exception:
            pass


async def ww_cupid_chain(game, victim_id):
    d = game.players.get(victim_id)
    if not d:
        return
    lover = d.get("lover")
    if lover and lover in game.players and game.players[lover].get("alive"):
        game.players[lover]["alive"] = False
        await game.ctx.send(
            f"💔 **BI KỊCH TÌNH YÊU:** {game.players[lover]['member'].display_name} "
            f"đã chết theo người yêu **{d['member'].display_name}**."
        )


class WWTownNightView(discord.ui.View):
    def __init__(self, game, uid, kind):
        super().__init__(timeout=30)
        self.game = game
        self.uid = uid
        self.kind = kind
        options = []
        for pid, d in game.players.items():
            if pid == uid:
                continue
            if kind == "medium":
                if d.get("alive"):
                    continue
            else:
                if not d.get("alive"):
                    continue
            options.append(discord.SelectOption(label=d["member"].display_name[:100], value=str(pid), emoji="🎯"))
        options = options[:25]
        self.select = discord.ui.Select(placeholder="Chọn mục tiêu...", options=options, min_values=1, max_values=1)
        self.select.callback = self.choose
        self.add_item(self.select)

    async def choose(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=False)
            return
        target = int(self.select.values[0])
        g = self.game
        if self.kind == "seer":
            role = g.players[target]["role"]
            result = "🐺 CÓ DẤU HIỆU SÓI" if ww_is_wolf(role) else "🟢 KHÔNG PHẢI SÓI"
            await interaction.response.send_message(
                f"🔮 **KẾT QUẢ TIÊN TRI**\n`{g.players[target]['member'].display_name}` → **{result}**", ephemeral=False
            )
        elif self.kind == "guard":
            history = getattr(g, "guard_history", [])
            if len(history) >= 2 and history[-1] == target and history[-2] == target:
                await interaction.response.send_message(
                    "🚫 **Không thể bảo vệ người này 3 đêm liên tiếp.** Hãy chọn người khác.", ephemeral=False
                )
                return
            g.night_actions["guard_target"] = target
            await interaction.response.send_message("🛡️ Đã ghi nhận mục tiêu bảo vệ.", ephemeral=False)
        elif self.kind == "detective":
            # handled by the two-person detective view
            pass
        elif self.kind == "medium":
            role = g.players[target]["role"]
            if g.players[target].get("alive"):
                await interaction.response.send_message("📜 Người này vẫn còn sống — Nhà Tiên Tri chỉ xem được người đã chết.", ephemeral=False)
                return
            await interaction.response.send_message(f"📜 Người chết **{g.players[target]['member'].display_name}** từng là **{role}**.", ephemeral=False)
        self.stop()


class WWDetectiveView(discord.ui.View):
    def __init__(self, game, uid):
        super().__init__(timeout=30)
        self.game = game
        self.uid = uid
        options = [discord.SelectOption(label=d["member"].display_name[:100], value=str(pid), emoji="🕵️")
                   for pid, d in game.players.items() if d.get("alive") and pid != uid][:25]
        self.a = discord.ui.Select(placeholder="Chọn người thứ nhất", options=options, min_values=1, max_values=1)
        self.b = discord.ui.Select(placeholder="Chọn người thứ hai", options=options, min_values=1, max_values=1)
        self.ok = discord.ui.Button(label="Kiểm tra", style=discord.ButtonStyle.blurple, emoji="🔍")
        self.ok.callback = self.check_pair
        self.add_item(self.a); self.add_item(self.b); self.add_item(self.ok)

    async def check_pair(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=False)
            return
        a, b = int(self.a.values[0]), int(self.b.values[0])
        if a == b:
            await interaction.response.send_message("❌ Hãy chọn 2 người khác nhau.", ephemeral=False)
            return
        same = ww_is_wolf(self.game.players[a]["role"]) == ww_is_wolf(self.game.players[b]["role"])
        result = "🟢 CÙNG PHE" if same else "🔴 KHÁC PHE"
        await interaction.response.send_message(
            f"🕵️ **KẾT QUẢ THÁM TỬ**\n{self.game.players[a]['member'].display_name} + {self.game.players[b]['member'].display_name}\n→ **{result}**",
            ephemeral=False
        )
        self.stop()


class WWMajorRevealView(discord.ui.View):
    def __init__(self, game, uid):
        super().__init__(timeout=30)
        self.game = game
        self.uid = uid
        b = discord.ui.Button(label="Công khai thân phận", style=discord.ButtonStyle.danger, emoji="👑")
        b.callback = self.reveal
        self.add_item(b)

    async def reveal(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
            return
        d = self.game.players[self.uid]
        if d.get("mayor_revealed"):
            await interaction.response.send_message("⚠️ Bạn đã công khai rồi.", ephemeral=True)
            return
        d["mayor_revealed"] = True
        await interaction.response.edit_message(content="👑 Bạn đã **CÔNG KHAI**. Từ giờ phiếu của bạn tính **2 phiếu**.", view=None)
        await self.game.ctx.send(f"👑 **{d['member'].mention} đã công khai là TRƯỞNG LÀNG!** Phiếu của Trưởng Làng từ giờ được tính **2**.")
        self.stop()


class WWWitchView(discord.ui.View):
    def __init__(self, game, uid, victim_id):
        super().__init__(timeout=15)
        self.game = game
        self.uid = uid
        self.victim_id = victim_id
        save = discord.ui.Button(label="Cứu nạn nhân", style=discord.ButtonStyle.success, emoji="💚")
        poison = discord.ui.Button(label="Dùng độc", style=discord.ButtonStyle.danger, emoji="☠️")
        skip = discord.ui.Button(label="Không dùng", style=discord.ButtonStyle.secondary, emoji="⏭️")
        save.callback = self.save
        poison.callback = self.poison
        skip.callback = self.skip
        self.add_item(save); self.add_item(poison); self.add_item(skip)

    async def save(self, interaction):
        if interaction.user.id != self.uid: return await interaction.response.send_message("❌ Không phải lượt của bạn!", ephemeral=True)
        g = self.game
        if g.players[self.uid].get("witch_heal_used"):
            return await interaction.response.send_message("⚠️ Bình Cứu đã được dùng.", ephemeral=True)
        g.players[self.uid]["witch_heal_used"] = True
        g.night_actions["witch_save"] = True
        await interaction.response.edit_message(content=f"💚 Đã cứu **{g.players[self.victim_id]['member'].display_name}**.", view=None)
        self.stop()

    async def poison(self, interaction):
        if interaction.user.id != self.uid: return await interaction.response.send_message("❌ Không phải lượt của bạn!", ephemeral=True)
        g = self.game
        if g.players[self.uid].get("witch_poison_used"):
            return await interaction.response.send_message("⚠️ Bình Độc đã được dùng.", ephemeral=True)
        opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(pid), emoji="☠️")
                for pid,d in g.players.items() if d.get("alive") and pid != self.uid][:25]
        select = discord.ui.Select(placeholder="Chọn người để đầu độc", options=opts, min_values=1, max_values=1)
        async def cb(i):
            if i.user.id != self.uid:
                return await i.response.send_message("❌ Không phải lượt của bạn!", ephemeral=True)
            target = int(select.values[0])
            g.players[self.uid]["witch_poison_used"] = True
            g.night_actions["witch_poison"] = target
            await i.response.edit_message(content=f"☠️ Đã dùng bình Độc lên **{g.players[target]['member'].display_name}**.", view=None)
            self.stop()
        select.callback = cb
        v = discord.ui.View(timeout=15); v.add_item(select)
        await interaction.response.edit_message(content="☠️ Chọn mục tiêu cho bình Độc:", view=v)

    async def skip(self, interaction):
        if interaction.user.id != self.uid: return await interaction.response.send_message("❌ Không phải lượt của bạn!", ephemeral=True)
        await interaction.response.edit_message(content="⏭️ Bạn không dùng bình thuốc đêm nay.", view=None)
        self.stop()


# ---------- phân vai trò mới ----------
async def ww_assign_roles(game):
    p_ids = list(game.players.keys())
    random.shuffle(p_ids)
    roles = ww_distribution(len(p_ids))
    game.wolves = []
    game.seer = game.guard = None
    game.night_actions = {}
    game.guard_history = []
    game.first_night = True

    for uid, role in zip(p_ids, roles):
        d = game.players[uid]
        d.update({
            "role": role, "alive": True, "lover": None, "lover_linked": False,
            "witch_heal_used": False, "witch_poison_used": False,
            "hunter_used": False, "mayor_revealed": False,
            "gate_targeted": False,
        })
        if ww_is_wolf(role): game.wolves.append(uid)
        if role == "Tiên Tri": game.seer = uid
        if role == "Bảo Vệ": game.guard = uid

    for uid, d in game.players.items():
        role = d["role"]
        icon, faction, desc = WW_ROLE_INFO_V2.get(role, ("🎭", "Phe Dân", ""))
        embed = discord.Embed(title=f"{icon} VAI TRÒ CỦA BẠN: {role}", color=0x5865f2)
        embed.add_field(name="⚔️ Phe", value=faction, inline=True)
        embed.add_field(name="📖 Chức năng", value=desc, inline=False)
        if role == "Sói":
            teammates = [game.players[x]["member"].display_name for x in game.wolves if x != uid]
            embed.add_field(name="🐺 Đồng đội Sói", value=", ".join(teammates) if teammates else "Bạn là Sói duy nhất.", inline=False)
        try: await d["member"].send(embed=embed)
        except Exception: pass


async def run_game(game):
    await ww_assign_roles(game)
    start_embed = ui_panel(
        "MA SÓI ĐÃ BẮT ĐẦU",
        (f"👥 **{len(game.players)} người chơi**  •  🐺 **{len(game.wolves)} Sói**\n\n"
         "🔐 Vai trò cá nhân đã được gửi riêng qua DM.\n"
         "🌙 Trời đã tối — hãy dùng kỹ năng của bạn thật khôn ngoan."),
        color="purple", emoji="🎭"
    )
    await game.ctx.send(embed=start_embed)
    await start_night(game)


async def start_night(game):
    game.state = "night"
    game.night_actions = {"wolf_votes": {}}
    alive = ww_alive(game)
    await game.ctx.send(embed=ui_panel(
        "ĐÊM XUỐNG",
        "🌙 Các vai trò ban đêm đang hành động.\n\n🔐 **Không tiết lộ vai trò trong kênh chung.**\n🎯 Hãy hoàn thành lựa chọn của bạn qua DM.",
        color="dark", emoji="🌙"
    ))

    # Cupid chỉ hoạt động đúng đêm đầu.
    if getattr(game, "first_night", False):
        cupid = next((u for u,d in game.players.items() if d["role"] in ("Cupid", "Thần Tình Yêu") and d["alive"]), None)
        if cupid:
            try: await game.players[cupid]["member"].send("💘 **CUPID — ĐÊM ĐẦU:** Chọn 2 người để ghép đôi. Sau khi ghép, **cả hai sẽ biết nhau**.", view=WWCupidView(game, cupid))
            except Exception: pass

    if game.seer and game.players[game.seer]["alive"]:
        try: await game.players[game.seer]["member"].send("🔮 **TIÊN TRI:** Chọn một người để soi.", view=WWTownNightView(game, game.seer, "seer"))
        except Exception: pass

    if game.guard and game.players[game.guard]["alive"]:
        try: await game.players[game.guard]["member"].send("🛡️ **BẢO VỆ:** Chọn người cần bảo vệ. Không thể chọn cùng một người 3 đêm liên tiếp.", view=WWTownNightView(game, game.guard, "guard"))
        except Exception: pass

    detective = next((u for u,d in game.players.items() if d["role"] == "Thám Tử" and d["alive"]), None)
    if detective:
        try: await game.players[detective]["member"].send("🕵️ **THÁM TỬ:** Chọn 2 người để biết họ cùng phe hay khác phe.", view=WWDetectiveView(game, detective))
        except Exception: pass

    medium = next((u for u,d in game.players.items() if d["role"] == "Nhà Tiên Tri" and d["alive"]), None)
    if medium:
        dead = [u for u,d in game.players.items() if not d["alive"]]
        if dead:
            try: await game.players[medium]["member"].send("📜 **NHÀ TIÊN TRI:** Chọn một người đã chết để xem vai trò.", view=WWTownNightView(game, medium, "medium"))
            except Exception: pass

    for wolf_id in game.wolves:
        if game.players.get(wolf_id, {}).get("alive"):
            opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(uid), emoji="🐺")
                    for uid,d in game.players.items() if d["alive"] and not ww_is_wolf(d["role"])][:25]
            if opts:
                view = discord.ui.View(timeout=20)
                sel = discord.ui.Select(placeholder="🐺 Chọn nạn nhân", options=opts, min_values=1, max_values=1)
                async def wolf_cb(interaction, wid=wolf_id, select=sel):
                    if interaction.user.id != wid:
                        return await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
                    target = int(select.values[0])
                    game.night_actions.setdefault("wolf_votes", {})[wid] = target
                    await interaction.response.send_message("🐺 Đã ghi nhận phiếu săn.", ephemeral=True)
                sel.callback = wolf_cb; view.add_item(sel)
                try: await game.players[wolf_id]["member"].send("🐺 **HỘI SÓI:** Chọn nạn nhân. Mỗi Sói bỏ 1 phiếu; mục tiêu nhiều phiếu nhất bị tấn công.", view=view)
                except Exception: pass

    await asyncio.sleep(30)

    # Phù thủy chỉ được xử lý sau khi biết kết quả chọn của Sói.
    wolf_votes = game.night_actions.get("wolf_votes", {})
    tally = {}
    for target in wolf_votes.values(): tally[target] = tally.get(target, 0) + 1
    if tally:
        max_votes = max(tally.values())
        top_targets = [uid for uid, votes in tally.items() if votes == max_votes]
        killed_id = random.choice(top_targets)
    else:
        killed_id = None
    game.night_actions["wolf_target"] = killed_id

    witch = next((u for u,d in game.players.items() if d["role"] == "Phù Thủy" and d["alive"]), None)
    if witch and killed_id:
        try:
            await game.players[witch]["member"].send(
                f"🧪 **PHÙ THỦY:** Đêm nay Sói đang nhắm vào **{game.players[killed_id]['member'].display_name}**.\n"
                "💚 Bạn còn Bình Cứu / ☠️ Bình Độc nếu chưa dùng.",
                view=WWitchView(game, witch, killed_id)
            )
        except Exception: pass
        await asyncio.sleep(20)

    await resolve_night(game)


async def ww_resolve_hunter_if_needed(game, uid):
    d = game.players.get(uid)
    if not d or d["role"] != "Thợ Săn" or d.get("hunter_used"):
        return
    try:
        await d["member"].send(
            "🏹 **THỢ SĂN — PHÁT BẮN CUỐI**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💀 Bạn đã chết, nhưng vẫn có **1 phát bắn cuối cùng**.\n"
            "🎯 Chọn một người còn sống hoặc bỏ qua.\n"
            "📌 Tên người chơi còn sống sẽ hiện trực tiếp trong danh sách lựa chọn bên dưới.",
            view=WWHunterView(game, uid)
        )
        await asyncio.sleep(15)
    except Exception:
        d["hunter_used"] = True


async def resolve_night(game):
    killed_id = game.night_actions.get("wolf_target")
    protected_id = game.night_actions.get("guard_target")
    poison_id = game.night_actions.get("witch_poison")
    save = game.night_actions.get("witch_save", False)
    for gd, data in game.players.items():
        if data.get("role") == "Người Gác Cổng" and data.get("alive"):
            data["gate_targeted"] = (gd == killed_id or gd == poison_id)
            if data["gate_targeted"]:
                try:
                    await data["member"].send("🚪 **GÁC CỔNG:** Đêm qua bạn cảm nhận được có người nhắm tới mình. Bạn không biết ai.")
                except Exception:
                    pass

    if game.guard and game.players.get(game.guard, {}).get("alive"):
        if protected_id is not None:
            game.guard_history.append(protected_id)
            game.guard_history = game.guard_history[-2:]

    deaths = []
    if killed_id and killed_id != protected_id and not save:
        deaths.append((killed_id, "🐺 bị Sói tấn công"))
    if poison_id and poison_id in game.players and game.players[poison_id]["alive"]:
        deaths.append((poison_id, "☠️ trúng độc"))

    # Khử trùng mục tiêu trùng nhau.
    seen = set()
    for uid, reason in deaths:
        if uid in seen or not game.players[uid]["alive"]: continue
        seen.add(uid)
        game.players[uid]["alive"] = False
        await game.ctx.send(f"☀️ **BÌNH MINH:** {game.players[uid]['member'].display_name} đã chết ({reason}).")
        await ww_cupid_chain(game, uid)
        await ww_resolve_hunter_if_needed(game, uid)

    if not deaths:
        await game.ctx.send("☀️ **BÌNH MINH:** Đêm qua không có ai chết. Một đêm hiếm hoi bình yên...")

    game.first_night = False
    if check_win_condition(game): return
    await ww_mayor_end_of_night_prompt(game)
    await start_day_discussion(game)


async def ww_kill_with_vote(game, uid):
    if uid not in game.players or not game.players[uid]["alive"]: return
    game.players[uid]["alive"] = False
    await ww_cupid_chain(game, uid)
    await ww_resolve_hunter_if_needed(game, uid)


async def start_day_discussion(game):
    game.state = "day"
    game.clue_count_in_round = 0
    mentions = " ".join(d["member"].mention for d in game.players.values() if d["alive"])
    await game.ctx.send(
        f"🗣️ {mentions}\n**☀️ BAN NGÀY** — 60 giây thảo luận.\n"
        "🧩 Dùng `.manhmoi` để nhận manh mối vừa phải."
    )
    for i in range(60, 0, -1):
        await asyncio.sleep(1)
    await start_voting(game)


class WWVoteView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=15)
        self.game = game
        for uid,d in game.players.items():
            if d["alive"]:
                b = discord.ui.Button(label=f"Treo {d['member'].display_name[:70]}", style=discord.ButtonStyle.blurple)
                async def cb(interaction, target=uid):
                    voter = interaction.user.id
                    if voter not in game.players or not game.players[voter]["alive"]:
                        return await interaction.response.send_message("❌ Bạn không còn quyền vote.", ephemeral=True)
                    game.night_actions.setdefault("votes", {})[voter] = target
                    await interaction.response.send_message("🗳️ Đã ghi nhận phiếu kín.", ephemeral=True)
                b.callback = cb; self.add_item(b)
        b = discord.ui.Button(label="Phiếu trắng", style=discord.ButtonStyle.secondary, emoji="🏳️")
        async def blank(interaction):
            voter = interaction.user.id
            if voter not in game.players or not game.players[voter]["alive"]:
                return await interaction.response.send_message("❌ Bạn không còn quyền vote.", ephemeral=True)
            game.night_actions.setdefault("votes", {})[voter] = "blank"
            await interaction.response.send_message("🏳️ Đã bỏ phiếu trắng.", ephemeral=True)
        b.callback = blank; self.add_item(b)


async def start_voting(game):
    game.night_actions["votes"] = {}
    view = WWVoteView(game)
    msg = await game.ctx.send("⚖️ **BỎ PHIẾU TREO CỔ — 15 GIÂY**\nPhiếu của Trưởng Làng công khai sẽ tính **2**.", view=view)
    await asyncio.sleep(15)
    view.stop()
    try: await msg.delete()
    except Exception: pass
    await resolve_voting(game)


async def resolve_voting(game):
    votes = game.night_actions.get("votes", {})
    tally = {}; blank = 0
    for voter,target in votes.items():
        if target == "blank": blank += 1; continue
        weight = 2 if game.players.get(voter, {}).get("role") == "Trưởng Làng" and game.players[voter].get("mayor_revealed") else 1
        tally[target] = tally.get(target, 0) + weight
    if not tally:
        await game.ctx.send(f"🏳️ Không ai bị treo cổ. Phiếu trắng: **{blank}**.")
    else:
        mx=max(tally.values()); tops=[u for u,v in tally.items() if v==mx]
        if len(tops)==1:
            uid=tops[0]
            await game.ctx.send(f"⚖️ **{game.players[uid]['member'].display_name}** nhận **{mx} phiếu** và bị treo cổ.\n🎭 Vai trò: **{game.players[uid]['role']}**")
            await ww_kill_with_vote(game, uid)
        else:
            await game.ctx.send(f"⚖️ Hòa phiếu ({mx}-{mx}), không ai bị treo cổ.")
    if check_win_condition(game): return
    await start_night(game)


def check_win_condition(game):
    # Không dùng logic cũ bị coi tất cả vai trò khác Sói là dân một cách mù quáng.
    alive = [d for d in game.players.values() if d["alive"]]
    wolves = [d for d in alive if ww_is_wolf(d["role"])]
    nonwolves = [d for d in alive if not ww_is_wolf(d["role"])]
    if not wolves:
        asyncio.create_task(end_game(game, "🎉 **PHE DÂN CHIẾN THẮNG!** Tất cả Sói đã bị loại."))
        return True
    if len(wolves) >= len(nonwolves):
        asyncio.create_task(end_game(game, "🐺 **PHE SÓI CHIẾN THẮNG!** Sói đã đạt thế áp đảo."))
        return True
    return False


async def end_game(game, win_message):
    await game.ctx.send(win_message)
    lines=[]
    for uid,d in game.players.items():
        status="🟢 Sống" if d["alive"] else "🔴 Chết"
        lines.append(f"{ww_role_emoji(d['role'])} {d['member'].mention} — **{d['role']}** — {status}")
    embed=discord.Embed(title="📜 KẾT QUẢ VÁN MA SÓI", description="\n".join(lines), color=0x8e44ad)
    await game.ctx.send(embed=embed)
    if game.ctx.channel.id in game_lobbies:
        del game_lobbies[game.ctx.channel.id]


@bot.hybrid_command(name="masoiroles", aliases=["msroles", "roles"])
async def cmd_masoiroles_v2(ctx):
    """Xem bộ vai trò Ma Sói."""
    embed=discord.Embed(title="🐺 BỘ VAI TRÒ MA SÓI V2", description="Các vai trò được mở dần theo số người để ván không bị lệch.", color=0x5865f2)
    for role,(emoji,faction,desc) in WW_ROLE_INFO_V2.items():
        embed.add_field(name=f"{emoji} {role} • {faction}", value=desc, inline=False)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="mav")
async def cmd_mav(ctx):
    """Trưởng Làng có thể tự công khai bằng lệnh này trong ban ngày."""
    game=game_lobbies.get(ctx.channel.id)
    if not game or game.state != "day":
        return await ctx.send("❌ Lệnh này chỉ dùng trong ban ngày của Ma Sói.", delete_after=5)
    d=game.players.get(ctx.author.id)
    if not d or not d["alive"] or d["role"] != "Trưởng Làng":
        return await ctx.send("❌ Bạn không phải Trưởng Làng.", delete_after=5)
    if d.get("mayor_revealed"):
        return await ctx.send("⚠️ Bạn đã công khai rồi.", delete_after=5)
    d["mayor_revealed"]=True
    await ctx.send(f"👑 **{ctx.author.mention} đã CÔNG KHAI là TRƯỞNG LÀNG!** Phiếu của họ từ giờ tính **2**.")



# Lưu kênh cấm bot vào SQLite để restart bot vẫn giữ nguyên cấu hình.
def _ensure_bot_control_table():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS bot_blocked_channels (guild_id INTEGER, channel_id INTEGER, PRIMARY KEY (guild_id, channel_id))")
    conn.commit(); conn.close()

def persist_bot_block_channel(guild_id, channel_id):
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO bot_blocked_channels (guild_id, channel_id) VALUES (?, ?)", (guild_id, channel_id))
        conn.commit(); conn.close()
    except Exception:
        pass

def unpersist_bot_block_channel(guild_id, channel_id):
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("DELETE FROM bot_blocked_channels WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
        conn.commit(); conn.close()
    except Exception:
        pass

def load_persisted_bot_blocks():
    try:
        _ensure_bot_control_table()
        conn = sqlite3.connect(DB_NAME); cur = conn.cursor()
        cur.execute("SELECT guild_id, channel_id FROM bot_blocked_channels")
        rows = cur.fetchall(); conn.close()
        for gid, cid in rows:
            BOT_BLOCKED_CHANNELS.setdefault(gid, set()).add(cid)
    except Exception:
        pass

# ================================================================
# 🆕 HỆ THỐNG BỔ SUNG — TRƯỞNG LÀNG / CẤM BOT / LÌ XÌ TOÀN SERVER
# CHỈ THÊM/SỬA — KHÔNG XÓA CÁC HỆ THỐNG CŨ.
# ================================================================

BOT_BLOCKED_CHANNELS = globals().get("BOT_BLOCKED_CHANNELS", {})
LIXI_CLAIMED = globals().get("LIXI_CLAIMED", {})
LIXI_EVENT_COUNTER = globals().get("LIXI_EVENT_COUNTER", 0)
LIXI_TASK_STARTED = globals().get("LIXI_TASK_STARTED", False)
LIXI_ACTIVE_EVENT = globals().get("LIXI_ACTIVE_EVENT", {})
load_persisted_bot_blocks()


def is_bot_command_message(message):
    return (message.content or "").lstrip().startswith((".", "!"))


@bot.hybrid_command(name='blockbot', aliases=['clbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_clbot(ctx, channel: discord.TextChannel = None):
    """Cấm lệnh bot & xóa bot khác trong kênh."""
    gid = ctx.guild.id if ctx.guild else 0
    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).add(ctx.channel.id)
    embed = discord.Embed(
        title="🔒 ĐÃ CẤM LỆNH BOT",
        description=(
            f"Kênh {ctx.channel.mention} đã được khóa lệnh bot.\n\n"
            "• Lệnh `.` / `!` của bot sẽ không được xử lý tại đây.\n"
            "• Tin nhắn của **bot khác** gửi vào đây sẽ tự động bị xóa.\n"
            "• Dùng `.unblockbot` để mở lại."
        ), color=0xe74c3c)
    await ctx.send(embed=embed, delete_after=8)


@bot.hybrid_command(name='unblockbot', aliases=['dclbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_dclbot(ctx, channel: discord.TextChannel = None):
    """Bỏ cấm lệnh bot trong kênh."""
    target = channel or ctx.channel
    gid = ctx.guild.id if ctx.guild else 0
    BOT_BLOCKED_CHANNELS.setdefault(gid, set()).discard(target.id)
    unpersist_bot_block_channel(gid, target.id)
    embed = discord.Embed(
        title="🔓 ĐÃ BỎ CẤM BOT",
        description=f"Kênh {target.mention} đã được mở lại cho các lệnh bot.",
        color=0x2ecc71)
    await ctx.send(embed=embed, delete_after=6)


@cmd_clbot.error
async def cmd_clbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.blockbot`.", delete_after=5)


@cmd_dclbot.error
async def cmd_dclbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.unblockbot`.", delete_after=5)


class MayorRevealView(discord.ui.View):
    def __init__(self, game, uid):
        super().__init__(timeout=25)
        self.game, self.uid = game, uid

    @discord.ui.button(label="👑 Lộ thân phận", style=discord.ButtonStyle.success)
    async def reveal(self, interaction, button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("❌ Đây không phải lựa chọn của bạn!", ephemeral=True)
        d=self.game.players.get(self.uid)
        if not d or not d.get("alive") or d.get("role") != "Trưởng Làng":
            return await interaction.response.send_message("❌ Bạn không còn là Trưởng Làng đang sống.", ephemeral=True)
        d["mayor_revealed"] = True
        await interaction.response.edit_message(content="👑 **Bạn đã công khai thân phận Trưởng Làng!** Từ giờ phiếu của bạn tính **2**.", view=None)
        await self.game.ctx.send(f"👑 **{d['member'].mention} đã công khai là TRƯỞNG LÀNG!**\n⚖️ Phiếu của Trưởng Làng từ giờ tính **2**.")
        self.stop()

    @discord.ui.button(label="🤫 Chưa lộ", style=discord.ButtonStyle.secondary)
    async def hide(self, interaction, button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("❌ Đây không phải lựa chọn của bạn!", ephemeral=True)
        await interaction.response.edit_message(content="🤫 **Bạn chọn CHƯA lộ thân phận.** Hãy chỉ công khai khi thực sự có lợi cho phe Dân.", view=None)
        self.stop()


async def ww_mayor_end_of_night_prompt(game):
    for uid,d in game.players.items():
        if d.get("alive") and d.get("role") == "Trưởng Làng" and not d.get("mayor_revealed"):
            try:
                embed=discord.Embed(
                    title="👑 TRƯỞNG LÀNG — HẾT ĐÊM",
                    description=(
                        "🌅 Đêm vừa kết thúc. Bạn có muốn **lộ thân phận Trưởng Làng** không?\n\n"
                        "👑 **Lộ:** mọi người biết bạn là Trưởng Làng và phiếu của bạn tính **2**.\n"
                        "🤫 **Chưa lộ:** tiếp tục giữ bí mật.\n\n"
                        "⚠️ **Lưu ý:** Tùy mục đích mới nên lộ thân phận. Nếu lộ quá sớm, bạn có thể trở thành mục tiêu của Sói và việc công khai có thể gây thêm rắc rối."
                    ), color=0xf1c40f)
                await d["member"].send(embed=embed, view=MayorRevealView(game, uid))
            except Exception:
                pass


class LixiClaimView(discord.ui.View):
    def __init__(self, event_id, guild_id, amount):
        super().__init__(timeout=None)
        self.event_id=event_id; self.guild_id=guild_id; self.amount=amount

    @discord.ui.button(label="🧧 NHẬN LÌ XÌ", style=discord.ButtonStyle.success, emoji="💰")
    async def claim(self, interaction, button):
        uid=str(interaction.user.id)
        # Chỉ đợt lì xì mới nhất của server còn hiệu lực.
        if LIXI_ACTIVE_EVENT.get(self.guild_id) != self.event_id:
            return await interaction.response.send_message("⏰ Lì xì này đã hết hiệu lực vì server đã có đợt lì xì mới!", ephemeral=True)
        claimed=LIXI_CLAIMED.setdefault(self.event_id,set())
        if interaction.user.id in claimed:
            return await interaction.response.send_message("❌ Bạn đã nhận lì xì đợt này rồi!", ephemeral=True)
        claimed.add(interaction.user.id)
        try:
            data=get_user(uid)
            update_user_full(uid, data[0]+self.amount, data[1], data[2], data[3], data[4])
            await interaction.response.send_message(f"🧧 **Bạn nhận được {self.amount:,} 🪙!**", ephemeral=True)
        except Exception:
            claimed.discard(interaction.user.id)
            await interaction.response.send_message("❌ Không thể cộng tiền lúc này, hãy thử lại.", ephemeral=True)


async def lixi_server_loop():
    global LIXI_EVENT_COUNTER
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.randint(1200, 2400))
        for guild in list(bot.guilds):
            eligible=[]
            for ch in guild.text_channels:
                if ch.id in BOT_BLOCKED_CHANNELS.get(guild.id,set()):
                    continue
                if ch.permissions_for(guild.me).send_messages:
                    eligible.append(ch)
            if not eligible:
                continue
            channel=random.choice(eligible)
            amount=random.randint(10000,1000000)
            LIXI_EVENT_COUNTER += 1
            event_id=f"{guild.id}-{LIXI_EVENT_COUNTER}"
            # Khi lì xì mới xuất hiện, lì xì cũ lập tức mất hiệu lực.
            LIXI_ACTIVE_EVENT[guild.id] = event_id
            LIXI_CLAIMED[event_id]=set()
            embed=discord.Embed(
                title="🧧 LÌ XÌ TOÀN SERVER ĐÃ XUẤT HIỆN!",
                description=(
                    f"🎉 **{amount:,} 🪙** đang chờ mọi người nhận!\n\n"
                    "👥 Mỗi người chỉ được nhận **1 lần** trong đợt này.\n"
                    "💰 Mỗi người nhận tối đa **1,000,000 🪙**.\n"
                    "⚡ Ai nhanh tay thì nhận trước!"
                ), color=0xe74c3c)
            try:
                await channel.send(content="🎊 **LÌ XÌ TOÀN SERVER!**", embed=embed, view=LixiClaimView(event_id,guild.id,amount))
            except Exception:
                pass


async def start_lixi_task_once():
    global LIXI_TASK_STARTED
    if not LIXI_TASK_STARTED:
        LIXI_TASK_STARTED=True
        asyncio.create_task(lixi_server_loop())


# Ghi đè on_message để thêm: cấm lệnh bot + tự xóa bot khác trong kênh bị khóa.
_old_on_message = on_message
@bot.event
async def on_message(message):
    if message.author.bot:
        if message.author.id != getattr(bot.user, "id", None) and message.guild:
            if message.channel.id in BOT_BLOCKED_CHANNELS.get(message.guild.id,set()):
                try: await message.delete()
                except Exception: pass
        return
    if message.guild and message.channel.id in BOT_BLOCKED_CHANNELS.get(message.guild.id,set()):
        if is_bot_command_message(message) and not message.content.lstrip().startswith((".clbot", ".dclbot", ".blockbot", ".unblockbot")):
            return
    await _old_on_message(message)


# Ghi đè on_ready để khởi động lì xì mà vẫn chạy on_ready cũ.
_old_on_ready = on_ready
@bot.event
async def on_ready():
    await _old_on_ready()
    await start_lixi_task_once()



# ================================================================
# 👑 OWNER CONFIG — ĐỔI ID Ở ĐÂY NẾU SAU NÀY ĐỔI TÀI KHOẢN
# ================================================================
# ID tài khoản Owner — dùng cho toàn bộ quyền đặc biệt và Owner Guard.
# Đổi đúng ID này nếu sau này tài khoản Owner thay đổi.
OWNER_USER_ID = 1404399385965498479

def is_bot_owner(user):
    try:
        return int(getattr(user, "id", user)) == OWNER_USER_ID
    except Exception:
        return False


# ================================================================
# 🆕 HELP V2 — MENU PHÂN LOẠI, KHÔNG XÓA CODE HELP CŨ
# ================================================================
class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🏠 Trang chính", value="home", description="Tổng quan bot & cách dùng"),
            discord.SelectOption(label="🐺 Ma Sói", value="werewolf", description="Phòng, vai trò, manh mối"),
            discord.SelectOption(label="🎮 Mini Game", value="games", description="Bầu Cua, Tài Xỉu, Mines, Tuxi"),
            discord.SelectOption(label="💰 Kinh Tế", value="economy", description="Coin, daily, pay, top"),
            discord.SelectOption(label="🛡️ Quản Trị", value="admin", description="Mute, khóa bot, quản lý"),
            discord.SelectOption(label="✨ Tiện Ích", value="utility", description="Avatar, AFK, đổi tên, lì xì"),
        ]
        super().__init__(placeholder="Chọn mục trợ giúp", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        category = self.values[0]
        pages = help_pages_v2()
        embed = pages.get(category, pages["home"])
        embed.set_footer(text="💡 Dùng .help hoặc /help để mở lại menu • Một số lệnh cần quyền phù hợp")
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpHomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(HelpCategorySelect())


def help_pages_v2():
    home = discord.Embed(
        title="🤖  K S - B O T   •   T R U N G   T Â M   T R Ợ   G I Ú P",
        description=(
            "Chào mừng! Chọn **danh mục** ở menu ⬇️ bên dưới để xem chi tiết từng lệnh.\n"
            "```⚡ Prefix : .        🆕 Slash : /```"
        ),
        color=0x5865F2
    )
    home.add_field(name="🐺 Ma Sói", value="Phòng chơi, vai trò, sự kiện", inline=True)
    home.add_field(name="🎮 Mini Game", value="Bầu Cua, Tài Xỉu, Mines…", inline=True)
    home.add_field(name="💰 Kinh Tế", value="Ví, lương, chuyển coin", inline=True)
    home.add_field(name="🛡️ Quản Trị", value="🔒 Chỉ Owner Bot / Chủ Server", inline=True)
    home.add_field(name="✨ Tiện Ích", value="Avatar, đổi tên, AFK…", inline=True)
    home.add_field(name="\u200b", value="\u200b", inline=True)
    home.add_field(
        name="💡 Mẹo nhanh",
        value=(
            "• Gõ `/` rồi chọn lệnh — Discord tự gợi ý cú pháp.\n"
            "• Một số lệnh `.` có thêm alias để gõ tắt.\n"
            "• Ví dụ: `.coin` · `.masoi` · `/help`"
        ),
        inline=False
    )
    home.set_footer(text="KS-BOT  •  Chọn mục bên dưới để xem thêm ✨")

    werewolf = discord.Embed(title="🐺  M A   S Ó I", color=0x8E44AD)
    werewolf.add_field(
        name="📋 Lệnh",
        value=(
            "`.masoi` `/masoi` — Tạo phòng chơi\n"
            "`.masoiroles` `/masoiroles` — Xem bộ vai trò\n"
            "`.mav` `/mav` — Trưởng Làng tự công khai\n"
            "`.manhmoi` — Xem manh mối trong ngày"
        ),
        inline=False
    )
    werewolf.add_field(
        name="🎭 Vai trò đặc biệt",
        value=(
            "💘 **Cupid** — hai người yêu biết nhau\n"
            "🛡️ **Bảo Vệ** — không bảo vệ cùng 1 người 3 đêm liền\n"
            "🧪 **Phù Thủy** — Cứu / Độc\n"
            "🏹 **Thợ Săn** — bắn 1 phát khi chết\n"
            "👑 **Trưởng Làng** — mỗi đêm chọn lộ diện hoặc ẩn danh"
        ),
        inline=False
    )

    games = discord.Embed(title="🎮  M I N I   G A M E", color=0x2ECC71)
    games.add_field(name="🦀 `.baucua` `/baucua`", value="Bầu Cua Tôm Cá", inline=True)
    games.add_field(name="🎲 `.sicbo` `/sicbo`", value="Tài Xỉu (alias: `taixiu`)", inline=True)
    games.add_field(name="💣 `.mine` `/mine`", value="Mines — `<tiền/all> [boom]`", inline=True)
    games.add_field(name="✂️ `.rps` `/rps`", value="Kéo Búa Bao (alias: `tuxi`) — `<tiền/all>`", inline=True)

    economy = discord.Embed(title="💰  K I N H   T Ế", color=0xF1C40F)
    economy.add_field(name="👛 `.coin` `/coin`", value="Xem số dư ví", inline=True)
    economy.add_field(name="📅 `.daily` `/daily`", value="Nhận lương hằng ngày", inline=True)
    economy.add_field(name="🏆 `.top` `/top`", value="Bảng xếp hạng đại gia", inline=True)
    economy.add_field(name="💸 `.pay` `/pay`", value="`@user <tiền/all>` — Chuyển coin", inline=False)
    economy.add_field(
        name="🧧 Lì xì server",
        value="Xuất hiện ngẫu nhiên toàn server • Mỗi người nhận **1 lần/đợt** • Tối đa **1,000,000** 🪙 • Đợt mới ra thì đợt cũ hết hạn.",
        inline=False
    )

    admin = discord.Embed(
        title="🛡️  Q U Ả N   T R Ị",
        description="🔒 **Toàn bộ lệnh dưới đây chỉ dùng được bởi Owner Bot hoặc Chủ Server.**",
        color=0xE74C3C
    )
    admin.add_field(
        name="🔇 Xử lý thành viên",
        value=(
            "`.gag @user` `/gag` — Khóa mõm 5 phút (alias: `khoamom`)\n"
            "`.mute @user <10p/1h/1d>` `/mute` — Khóa chat (alias: `khoachat`, `timeout`)\n"
            "`.unmute @user` `/unmute` — Gỡ mute/khóa mõm (alias: `ungag`, `unkhoamom`)\n"
            "`.kick @user [lý do]` `/kick` — Kick khỏi server\n"
            "`.ban @user [lý do]` `/ban` — Ban khỏi server\n"
            "`.unban <user_id>` `/unban` — Gỡ ban theo ID"
        ),
        inline=False
    )
    admin.add_field(
        name="🧹 Kênh & tin nhắn",
        value=(
            "`.clear [@user] <số|1p|1h|1d>` `/clear` — Xóa tin nhắn\n"
            "`.delreply` `/delreply` — Reply rồi xóa cả 2 tin (alias: `xtn`)\n"
            "`.blockbot [#kênh...]` `/blockbot` — Cấm lệnh bot & xóa bot khác trong kênh (alias: `clbot`)\n"
            "`.unblockbot [#kênh...]` `/unblockbot` — Bỏ cấm (alias: `dclbot`)\n"
            "`.ksbotchannel #kênh` `/ksbotchannel` — Đặt kênh riêng cho KS-BOT (alias: `kenhksbot`)\n"
            "`.lixichannel #kênh` `/lixichannel` — Chọn kênh phát lì xì (alias: `kenhlixi`)"
        ),
        inline=False
    )
    admin.add_field(
        name="🎭 Vai trò",
        value=(
            "`.addrole @user @role` `/addrole` — Thêm role\n"
            "`.delrole @user @role` `/delrole` — Gỡ role"
        ),
        inline=False
    )
    admin.set_footer(text="👑 Owner Bot luôn bypass mọi giới hạn • ID Owner nằm gần cuối code")

    utility = discord.Embed(title="✨  T I Ệ N   Í C H", color=0x9B59B6)
    utility.add_field(name="🖼️ `.av [@user]` `/av`", value="Xem avatar", inline=True)
    utility.add_field(name="✏️ `.nickname <tên>` `/nickname`", value="Đổi biệt danh (alias: `name`)", inline=True)
    utility.add_field(name="💤 `.afk [lý do]` `/afk`", value="Bật chế độ AFK", inline=True)
    utility.add_field(name="📖 `.help` `/help`", value="Mở menu này", inline=True)
    utility.add_field(name="👛 `.coin` `/coin`", value="Xem số dư", inline=True)
    utility.add_field(name="📅 `.daily` `/daily`", value="Nhận lương", inline=True)
    utility.add_field(name="🏆 `.top` `/top`", value="Bảng xếp hạng", inline=True)
    utility.add_field(name="💸 `.pay @user` `/pay`", value="Chuyển coin", inline=True)
    utility.add_field(
        name="🔒 Ghi chú",
        value=(
            "Khi kênh bị `.blockbot` khóa, các lệnh **quản trị / AFK / clear / delreply** vẫn dùng được bình thường."
        ),
        inline=False
    )

    return {
        "home": home,
        "werewolf": werewolf,
        "games": games,
        "economy": economy,
        "admin": admin,
        "utility": utility,
    }

try:
    bot.remove_command("help")
except Exception:
    pass

@bot.hybrid_command(name="help")
async def cmd_help_v2(ctx):
    """Mở menu trợ giúp."""
    embed = help_pages_v2()["home"]
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed, view=HelpHomeView())



# ================================================================
# 🐾 THÚ HOANG TỰ ĐỘNG — .setthu #kênh
# ================================================================
# Mỗi server chọn 1 kênh. Bot tự phát thú khoảng 40–60 phút/lần.
# Người chơi bắt miễn phí, không cần Vợt Bắt Thú.
WILD_AUTO_CHANNELS = {}
WILD_NEXT_SPAWN = {}

try:
    with open("wild_spawn_channels.json", "r", encoding="utf-8") as _f:
        _wild_saved_channels = json.load(_f)
    for _gid, _cid in _wild_saved_channels.items():
        WILD_AUTO_CHANNELS[int(_gid)] = int(_cid)
        WILD_NEXT_SPAWN[int(_gid)] = time.time() + random.randint(2400, 3600)
except Exception:
    _wild_saved_channels = {}

async def _spawn_auto_wild_pet(guild, channel):
    try:
        pet_template = roll_rarity_pet()
        view = WildCatchView(pet_template)
        embed = discord.Embed(
            title="🌿 THÚ HOANG VỪA XUẤT HIỆN!",
            description=(
                "✨ Một con thú hoang vừa xuất hiện bất ngờ!\n\n"
                f"🐾 **{pet_template['name']}** (`{pet_template['rarity']}`)\n"
                f"❤️ HP: `{pet_template['hp']}`  |  ⚔️ ATK: `{pet_template['atk']}`\n\n"
                "🎁 **Bắt hoàn toàn miễn phí — không cần Vợt Bắt Thú!**\n"
                "🎲 Độ hiếm được quyết định theo tỷ lệ của hệ thống.\n"
                "⚡ Ai nhanh tay thì bắt trước!"
            ),
            color=0x2ECC71
        )
        embed.set_footer(text="🐾 Thú hoang tự động • 40–60 phút/lần")
        await channel.send(embed=embed, view=view)
    except Exception:
        pass

async def _wild_auto_spawn_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = time.time()
        for guild_id, channel_id in list(WILD_AUTO_CHANNELS.items()):
            if now < WILD_NEXT_SPAWN.get(guild_id, now):
                continue
            guild = bot.get_guild(guild_id)
            if guild is None:
                WILD_NEXT_SPAWN[guild_id] = now + random.randint(2400, 3600)
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                WILD_NEXT_SPAWN[guild_id] = now + random.randint(2400, 3600)
                continue
            me = guild.me
            if me and not channel.permissions_for(me).send_messages:
                WILD_NEXT_SPAWN[guild_id] = now + random.randint(2400, 3600)
                continue
            await _spawn_auto_wild_pet(guild, channel)
            WILD_NEXT_SPAWN[guild_id] = time.time() + random.randint(2400, 3600)
        await asyncio.sleep(30)

_wild_auto_task = None
_old_on_ready_for_wild = on_ready

@bot.event
async def on_ready():
    # Thêm dòng set màu vàng ở đây:
    await bot.change_presence(
        status=discord.Status.idle,
        activity=discord.Game(name="với lệnh .help")
    )
    
    global _wild_auto_task
    await _old_on_ready_for_wild()
    if _wild_auto_task is None or _wild_auto_task.done():
        _wild_auto_task = asyncio.create_task(_wild_auto_spawn_loop())

    # Đồng bộ slash command (/) lên Discord — chỉ cần chạy khi bot khởi động.
    try:
        synced = await bot.tree.sync()
        print(f"✅ Đã đồng bộ {len(synced)} slash command.")
    except Exception as e:
        print(f"⚠️ Lỗi đồng bộ slash command: {e}")



# ================================================================
# 🆕 QUẢN TRỊ V6 — CLEAR / XTN / ADDROLE / KÊNH LÌ XÌ / OWNER GUARD
# CHỈ THÊM/SỬA — GIỮ NGUYÊN CÁC HỆ THỐNG CŨ.
# ================================================================

LIXI_CHANNELS = {}
try:
    with open("lixi_channels.json", "r", encoding="utf-8") as _lf:
        _saved_lixi_channels = json.load(_lf)
    for _gid, _cid in _saved_lixi_channels.items():
        LIXI_CHANNELS[int(_gid)] = int(_cid)
except Exception:
    _saved_lixi_channels = {}


def _save_lixi_channels():
    try:
        with open("lixi_channels.json", "w", encoding="utf-8") as _lf:
            json.dump({str(k): int(v) for k, v in LIXI_CHANNELS.items()}, _lf, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _can_manage_messages(ctx):
    return _server_owner_only(ctx)


def _can_manage_roles(ctx):
    return _server_owner_only(ctx)


async def _clear_core(ctx, target, spec):
    """Lõi xử lý lệnh clear — dùng chung cho cả .clear (prefix) và /clear (slash)."""
    spec = (spec or "").lower().strip()
    duration_match = re.fullmatch(r"(\d+)(p|m|h|d)", spec)
    count_match = re.fullmatch(r"\d+", spec)
    # Khi dùng slash, ctx.message có thể không tồn tại (không có tin nhắn thật).
    cur_msg_id = ctx.message.id if getattr(ctx, "message", None) else None

    try:
        deleted = []
        if duration_match:
            amount = int(duration_match.group(1))
            unit = duration_match.group(2)
            seconds = amount * {"p": 60, "m": 60, "h": 3600, "d": 86400}[unit]
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)

            def _check_duration(msg):
                if cur_msg_id is not None and msg.id == cur_msg_id:
                    return False
                if msg.created_at < cutoff:
                    return False
                return target is None or msg.author.id == target.id

            deleted = await ctx.channel.purge(limit=1000, check=_check_duration, bulk=True)
            label = f"{spec}"
        elif count_match:
            wanted = int(spec)
            if wanted <= 0:
                return await ctx.send("❌ Số lượng phải lớn hơn 0.", delete_after=5)
            counter = {"n": 0}

            def _check_count(msg):
                if (cur_msg_id is not None and msg.id == cur_msg_id) or counter["n"] >= wanted:
                    return False
                if target is not None and msg.author.id != target.id:
                    return False
                counter["n"] += 1
                return True

            deleted = await ctx.channel.purge(limit=1000, check=_check_count, bulk=True)
            label = str(wanted)
        else:
            return await ctx.send(
                "❌ Sai thời gian/số lượng. Ví dụ: `.clear 20`, `.clear @user 20`, `.clear 1h`, `.clear @user 1d`",
                delete_after=7
            )

        # Xóa luôn tin nhắn lệnh sau khi xử lý (chỉ áp dụng khi gọi bằng .clear thường).
        try:
            if getattr(ctx, "message", None):
                await ctx.message.delete()
        except Exception:
            pass

        who = f" của {target.mention}" if target else ""
        notice = await ctx.channel.send(f"🧹 Đã xóa **{len(deleted)}** tin nhắn{who} ({label}).", delete_after=4)
        return notice
    except discord.Forbidden:
        return await ctx.send("❌ Bot không có quyền **Manage Messages** ở kênh này.", delete_after=6)
    except Exception as e:
        return await ctx.send(f"❌ Không thể clear: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name="clear")
async def cmd_clear(ctx, nguoi: Optional[discord.Member] = None, so_luong: str = None):
    """Xóa N tin nhắn hoặc tin nhắn trong một khoảng thời gian; có thể lọc theo @user."""
    if not _can_manage_messages(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if not so_luong:
        return await ctx.send(
            "❌ Cú pháp: `.clear [@user] <số>` hoặc `.clear [@user] <1p|1h|1d>`",
            delete_after=7
        )
    await _clear_core(ctx, nguoi, so_luong)


@bot.hybrid_command(name='delreply', aliases=['xtn'])
async def cmd_xtn(ctx):
    """Reply một tin nhắn rồi .xtn: xóa cả tin nhắn được reply và tin nhắn .xtn."""
    if not _can_manage_messages(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)

    ref = ctx.message.reference
    if not ref or not ref.message_id:
        return await ctx.send("❌ Hãy **reply** đúng tin nhắn cần xóa rồi dùng `.delreply`.", delete_after=6)

    try:
        target_message = ctx.channel.get_partial_message(ref.message_id)
        await target_message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        return await ctx.send("❌ Bot không có quyền xóa tin nhắn đó.", delete_after=6)
    except Exception:
        return await ctx.send("❌ Không tìm thấy/không xóa được tin nhắn được reply.", delete_after=6)

    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.hybrid_command(name="addrole")
async def cmd_addrole(ctx, member: discord.Member = None, role: discord.Role = None):
    """`.addrole @user @role` — thêm role và tự xóa tin nhắn lệnh."""
    if not _can_manage_roles(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if member is None or role is None:
        return await ctx.send("❌ Cú pháp: `.addrole @user @role`", delete_after=6)
    if role.is_default():
        return await ctx.send("❌ Không thể thêm role @everyone.", delete_after=5)
    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return await ctx.send("❌ Bot chưa có quyền **Quản lý vai trò**.", delete_after=6)
    if role >= me.top_role:
        return await ctx.send("❌ Role này cao hơn hoặc ngang role cao nhất của bot.", delete_after=6)
    if member.top_role >= me.top_role and member != ctx.guild.owner:
        # Discord vẫn quyết định quyền thực tế; thông báo trước để dễ hiểu.
        pass
    try:
        await member.add_roles(role, reason=f".addrole bởi {ctx.author}")
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ Bot không đủ quyền để thêm role này cho người đó.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Lỗi thêm role: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name="delrole")
async def cmd_delrole(ctx, member: discord.Member = None, role: discord.Role = None):
    """`.delrole @user @role` — gỡ role và tự xóa tin nhắn lệnh."""
    if not _can_manage_roles(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng lệnh này.", delete_after=5)
    if member is None or role is None:
        return await ctx.send("❌ Cú pháp: `.delrole @user @role`", delete_after=6)
    if role.is_default():
        return await ctx.send("❌ Không thể gỡ role @everyone.", delete_after=5)

    me = ctx.guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return await ctx.send("❌ Bot chưa có quyền **Quản lý vai trò**.", delete_after=6)
    if role >= me.top_role:
        return await ctx.send("❌ Role này cao hơn hoặc ngang role cao nhất của bot.", delete_after=6)

    try:
        await member.remove_roles(role, reason=f".delrole bởi {ctx.author}")
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ Bot không đủ quyền để gỡ role này khỏi người đó.", delete_after=6)
    except Exception as e:
        await ctx.send(f"❌ Lỗi gỡ role: `{type(e).__name__}`", delete_after=6)


@bot.hybrid_command(name='lixichannel', aliases=['kenhlixi'])
async def cmd_kenhlixi(ctx, channel: discord.TextChannel = None):
    """Chọn đúng 1 kênh/server để phát lì xì."""
    if not _server_owner_only(ctx):
        return await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.lixichannel`.", delete_after=5)
    if channel is None:
        current = LIXI_CHANNELS.get(ctx.guild.id)
        if current:
            ch = ctx.guild.get_channel(current)
            if ch:
                return await ctx.send(f"🧧 Kênh lì xì hiện tại: {ch.mention}", delete_after=7)
        return await ctx.send("❌ Cú pháp: `.lixichannel #kênh` (hoặc `.kenhlixi`)", delete_after=6)

    LIXI_CHANNELS[ctx.guild.id] = channel.id
    _save_lixi_channels()
    await ctx.send(
        f"🧧 **ĐÃ ĐẶT KÊNH LÌ XÌ**\n📍 Chỉ phát lì xì tại {channel.mention}\n"
        "🔒 Bot sẽ không tự chọn kênh khác nữa.",
        delete_after=8
    )


# Ghi đè vòng lì xì: dùng đúng kênh `.kenhlixi`, tránh phát rải rác toàn server.
async def lixi_server_loop():
    global LIXI_EVENT_COUNTER
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.randint(1200, 2400))
        for guild in list(bot.guilds):
            channel_id = LIXI_CHANNELS.get(guild.id)
            if not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None or channel.id in BOT_BLOCKED_CHANNELS.get(guild.id, set()):
                continue
            me = guild.me
            if me is None or not channel.permissions_for(me).send_messages:
                continue
            amount = random.randint(10000, 1000000)
            LIXI_EVENT_COUNTER += 1
            event_id = f"{guild.id}-{LIXI_EVENT_COUNTER}"
            LIXI_ACTIVE_EVENT[guild.id] = event_id
            LIXI_CLAIMED[event_id] = set()
            embed = discord.Embed(
                title="🧧 LÌ XÌ TOÀN SERVER ĐÃ XUẤT HIỆN!",
                description=(
                    f"🎉 **{amount:,} 🪙** đang chờ mọi người nhận!\n\n"
                    "👥 Mỗi người chỉ được nhận **1 lần** trong đợt này.\n"
                    "💰 Mỗi người nhận tối đa **1,000,000 🪙**.\n"
                    "⚡ Lì xì này không hết hạn — chỉ mất hiệu lực khi có đợt mới."
                ), color=0xe74c3c)
            try:
                await channel.send(content="🎊 **LÌ XÌ TOÀN SERVER!**", embed=embed,
                                   view=LixiClaimView(event_id, guild.id, amount))
            except Exception:
                pass


# ================================================================
# 🛡️ OWNER GUARD — nếu Owner bị timeout/mute thì bot tự gỡ im lặng.
# ================================================================
_old_member_update = on_member_update
@bot.event
async def on_member_update(before, after):
    try:
        await _old_member_update(before, after)
    except Exception:
        pass

    if after.id == OWNER_USER_ID and after.guild:
        try:
            if getattr(after, "timed_out_until", None) is not None:
                me = after.guild.me
                if me and me.guild_permissions.moderate_members:
                    await after.timeout(None, reason="Owner Guard — tự động gỡ mute Owner")
        except Exception:
            pass


# ================================================================
# 🔒 KÊNH CẤM BOT — vẫn cho phép lệnh quản trị/AFK/clear/xtn/addrole/delrole.
# ================================================================
_BLOCKED_CHANNEL_ALLOWED_COMMANDS = {
    "afk", "xtn", "clear", "addrole", "delrole", "kenhlixi",
    "khoamom", "unkhoamom", "khoachat", "mute", "timeout", "unmute",
    "ban", "unban", "kick", "clbot", "dclbot"
}


def _command_name_from_message(message):
    content = (message.content or "").lstrip()
    if not content.startswith((".", "!")):
        return ""
    first = content[1:].split(None, 1)[0].lower() if content[1:].strip() else ""
    return first


# Override handler cuối cùng: command quản trị vẫn chạy trong kênh .clbot.
_old_final_on_message = on_message
@bot.event
async def on_message(message):
    if message.guild and message.channel.id in BOT_BLOCKED_CHANNELS.get(message.guild.id, set()):
        if message.author.bot:
            if message.author.id != getattr(bot.user, "id", None):
                try:
                    await message.delete()
                except Exception:
                    pass
            return
        command_name = _command_name_from_message(message)
        if command_name in _BLOCKED_CHANNEL_ALLOWED_COMMANDS:
            await bot.process_commands(message)
            return
    await _old_final_on_message(message)


TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_DISCORD_BOT_TOKEN_HERE")


# ================================================================
# 🆕 MA SÓI V10 — GIAO DIỆN + ĐẾM NGƯỢC + NHỊP VÁN MỚI
# CHỈ SỬA/THÊM LOGIC MA SÓI, KHÔNG XÓA HỆ THỐNG CŨ.
# ================================================================
WW_DISCUSSION_SECONDS_V10 = 90
WW_VOTE_SECONDS_V10 = 30
WW_CLUE_HINT_LEVEL_V10 = 0.35

# ================================================================
# ✨ KS-BOT UI THEME V11 — lớp giao diện chung
# Không thay đổi logic game/economy/admin; chỉ làm UI đồng bộ, sạch và dễ đọc.
# ================================================================
UI_COLORS = {
    "primary": 0x5865F2,
    "success": 0x57F287,
    "danger": 0xED4245,
    "warning": 0xFEE75C,
    "purple": 0x9B59B6,
    "pink": 0xEB459E,
    "cyan": 0x00B8D9,
    "dark": 0x202225,
}
UI_FOOTER = "KS-BOT  •  dùng .help để xem toàn bộ lệnh"

def ui_embed(title, description=None, color=None):
    e = discord.Embed(title=title, description=description, color=color or UI_COLORS["primary"])
    e.set_footer(text=UI_FOOTER)
    return e

def ui_panel(title, description, color="primary", emoji="✨"):
    e = ui_embed(f"{emoji} {title}", description, UI_COLORS.get(color, UI_COLORS["primary"]))
    return e

def _ks_embed_init(original_init):
    # Chỉ bổ sung màu/footer cho Embed không khai báo sẵn; Embed cũ vẫn giữ màu riêng.
    def wrapped(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            if getattr(self, "colour", None) is None or getattr(self, "colour", None).value == 0:
                self.colour = discord.Colour(UI_COLORS["primary"])
            if not getattr(self, "footer", None) or not self.footer.get("text"):
                self.set_footer(text=UI_FOOTER)
        except Exception:
            pass
    return wrapped

# Áp dụng một lớp skin nhẹ cho toàn bộ Embed đang có trong code.
if not getattr(discord.Embed.__init__, "_ksbot_v11", False):
    _orig_embed_init_v11 = discord.Embed.__init__
    discord.Embed.__init__ = _ks_embed_init(_orig_embed_init_v11)
    discord.Embed.__init__._ksbot_v11 = True

# Nút bấm dùng cùng ngôn ngữ màu, nhưng không ép đổi style do từng chức năng đã có màu riêng.
def ui_status_line(label, seconds):
    return f"⏱️ **{label}**  ·  còn **{seconds:02d}s**"


def ww_role_count_text_v10(game):
    counts = {}
    for d in game.players.values():
        r = d.get("role", "Dân Làng")
        counts[r] = counts.get(r, 0) + 1
    order = ["Sói", "Tiên Tri", "Bảo Vệ", "Phù Thủy", "Thợ Săn", "Cupid", "Trưởng Làng", "Thám Tử", "Nhà Tiên Tri", "Người Gác Cổng", "Dân Làng"]
    lines=[]
    for role in order:
        if role in counts:
            icon = ww_role_emoji(role)
            desc = WW_ROLE_INFO_V2.get(role, ("🎭", "", ""))[2]
            lines.append(f"{icon} **{role} × {counts[role]}** — {desc}")
    for role,count in counts.items():
        if role not in order:
            lines.append(f"{ww_role_emoji(role)} **{role} × {count}**")
    return "\n".join(lines)

async def ww_show_role_setup_v10(game):
    embed = ui_panel(
        "CƠ CẤU VAI TRÒ VÁN MA SÓI",
        (
            f"👥 **{len(game.players)} người**  •  🐺 **{len(game.wolves)} Sói**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔐 **Thông tin công khai**\n"
            "• Chỉ công khai *số lượng* từng vai trò.\n"
            "• Danh tính người giữ vai trò vẫn được giữ bí mật.\n"
            "• Vai trò + chức năng chi tiết được gửi riêng qua DM.\n\n"
            "📋 **Đội hình ván này**\n" + ww_role_count_text_v10(game)
        ),
        color="purple", emoji="🎭"
    )
    embed.set_footer(text="🔐 Vai trò cá nhân được giữ bí mật • KS-BOT Ma Sói")
    await game.ctx.send(embed=embed)

async def start_day_discussion(game):
    game.state = "day"
    game.clue_count_in_round = 0
    mentions = " ".join(d["member"].mention for d in game.players.values() if d["alive"])
    embed = ui_panel(
        "BAN NGÀY — THẢO LUẬN",
        (f"🗣️ **Người còn sống:** {mentions}\n\n"
         "💬 **Giai đoạn:** Thảo luận\n"
         "🧩 `.manhmoi` → manh mối khoảng **35% liên quan**, chỉ mang tính gợi ý."),
        color="warning", emoji="☀️")
    msg = await game.ctx.send(embed=embed)
    for remaining in range(89, -1, -1):
        try:
            embed.description = (f"🗣️ **Người còn sống:** {mentions}\n\n"
                                 f"💬 Thảo luận tự do\n{ui_status_line('THẢO LUẬN', remaining)}\n"
                                 "🧩 `.manhmoi` → manh mối vừa phải (~35% liên quan)." if remaining
                                 else "🗣️ **HẾT THỜI GIAN THẢO LUẬN!**\n\n🗳️ Chuẩn bị bỏ phiếu...")
            await msg.edit(embed=embed)
        except Exception:
            break
        if remaining:
            await asyncio.sleep(1)
    await start_voting(game)

async def start_voting(game):
    game.night_actions["votes"] = {}
    view = WWVoteView(game)
    embed = ui_panel(
        "BỎ PHIẾU TREO CỔ",
        "🔐 **Phiếu kín**\n👑 Trưởng Làng đã công khai sẽ có **2 phiếu**.\n\n" + ui_status_line("VOTE", 30),
        color="danger", emoji="⚖️"
    )
    msg = await game.ctx.send(embed=embed, view=view)
    for remaining in range(29, -1, -1):
        try:
            embed.description = (
                "🔐 **Phiếu kín**\n"
                "👑 Trưởng Làng đã công khai sẽ có **2 phiếu**.\n\n"
                + (ui_status_line("VOTE", remaining) if remaining else "⏰ **HẾT GIỜ VOTE!**")
            )
            await msg.edit(embed=embed, view=view)
        except Exception:
            break
        if remaining:
            await asyncio.sleep(1)
    view.stop()
    try: await msg.delete()
    except Exception: pass
    await resolve_voting(game)

async def run_game(game):
    await ww_assign_roles(game)
    await game.ctx.send(f"🎭 **MA SÓI ĐÃ BẮT ĐẦU!**\n👥 Người chơi: **{len(game.players)}** • 🐺 Sói: **{len(game.wolves)}**\n🔐 Vai trò cá nhân đã gửi riêng qua DM.")
    await ww_show_role_setup_v10(game)
    await start_night(game)

async def trigger_random_clue(channel, game):
    alive_players = [p for p in game.players.values() if p.get("alive")]
    if not alive_players:
        return
    target = random.choice(alive_players)
    if random.random() < WW_CLUE_HINT_LEVEL_V10:
        text = random.choice([
            "có dấu vết cho thấy ai đó đã di chuyển rất kín trong đêm",
            "một hành động đêm qua có vẻ không khớp với lời kể ban ngày",
            "có người thường xuyên xuất hiện gần khu vực này nhưng chưa đủ để kết luận",
            "có tiếng động bất thường nhưng chưa đủ để xác định phe nào",
        ])
    else:
        text = random.choice([
            "có tiếng bước chân quanh khu vực này, nhưng không rõ là ai",
            "ánh đèn gần khu vực này tắt bất thường giữa đêm",
            "có dấu chân lạ nhưng chưa đủ để xác định thủ phạm",
            "có tiếng động lạ nhưng manh mối vẫn còn mơ hồ",
        ])
    embed = ui_panel(
        "MANH MỐI BÍ ẨN",
        f"📍 **Khu vực:** {target['member'].display_name}\n\n{text.capitalize()}.",
        color="purple", emoji="🧩"
    )
    embed.set_footer(text="🧩 Gợi ý ~35% • Không phải bằng chứng chắc chắn • KS-BOT Ma Sói")
    await channel.send(embed=embed)



# ================================================================
# 🛠️ MA SÓI PATCH — GIỮ NGUYÊN CODE CŨ, CHỈ OVERRIDE/SỬA PHẦN CẦN THIẾT
# ================================================================

# GIF chỉ xuất hiện trong PHÒNG CHỜ, không xuất hiện trong lúc đang chơi.
WW_LOBBY_GIF_URL = "https://static2.klipy.com/ii/925f17378dd1893b674a723c07535afe/ac/02/SPv7AMLv.gif"

async def update_lobby_embed(game):
    """Cập nhật lobby nhẹ: chỉ edit 1 message, không spam message mới."""
    if not game.lobby_message:
        return
    player_list = "\n".join(
        f"• {data['member'].mention}" for data in game.players.values()
    ) or "Chưa có ai tham gia..."

    embed = discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=(
            "Bấm nút bên dưới để tham gia phòng chơi!\n"
            "Chủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5 người).\n\n"
            f"**Danh sách người chơi ({len(game.players)}):**\n{player_list}"
        ),
        color=UI_COLORS["cyan"]
    )
    embed.set_image(url=WW_LOBBY_GIF_URL)
    embed.set_footer(text="🐺 Sói đang nhảy múa chờ đủ người • KS-BOT")
    try:
        await game.lobby_message.edit(embed=embed)
    except Exception:
        pass


# ---------- Phù Thủy: 2 bình độc lập, có thể dùng cả Cứu + Độc trong cùng đêm ----------
class WWWitchView(discord.ui.View):
    def __init__(self, game, uid, victim_id):
        super().__init__(timeout=25)
        self.game = game
        self.uid = uid
        self.victim_id = victim_id

        save = discord.ui.Button(
            label="Cứu nạn nhân",
            style=discord.ButtonStyle.success,
            emoji="💚"
        )
        poison = discord.ui.Button(
            label="Dùng bình Độc",
            style=discord.ButtonStyle.danger,
            emoji="☠️"
        )
        done = discord.ui.Button(
            label="Xong lượt",
            style=discord.ButtonStyle.secondary,
            emoji="✅"
        )
        save.callback = self.save
        poison.callback = self.poison
        done.callback = self.done
        self.add_item(save)
        self.add_item(poison)
        self.add_item(done)

    async def _auth(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message(
                "❌ Đây không phải lượt của bạn!", ephemeral=True
            )
            return False
        return True

    async def save(self, interaction):
        if not await self._auth(interaction):
            return
        g = self.game
        d = g.players.get(self.uid, {})
        if d.get("witch_heal_used"):
            await interaction.response.send_message(
                "⚠️ Bình Cứu đã được dùng từ trước.", ephemeral=True
            )
            return
        if not self.victim_id or not g.players.get(self.victim_id, {}).get("alive"):
            await interaction.response.send_message(
                "⚠️ Nạn nhân không còn hợp lệ để cứu.", ephemeral=True
            )
            return

        d["witch_heal_used"] = True
        g.night_actions["witch_save"] = True
        await interaction.response.send_message(
            f"💚 Đã dùng **Bình Cứu** cho "
            f"**{g.players[self.victim_id]['member'].display_name}**.",
            ephemeral=True
        )

    async def poison(self, interaction):
        if not await self._auth(interaction):
            return
        g = self.game
        if g.players[self.uid].get("witch_poison_used"):
            await interaction.response.send_message(
                "⚠️ Bình Độc đã được dùng từ trước.", ephemeral=True
            )
            return

        opts = [
            discord.SelectOption(
                label=d["member"].display_name[:100],
                value=str(pid),
                emoji="☠️"
            )
            for pid, d in g.players.items()
            if d.get("alive") and pid != self.uid
        ][:25]

        if not opts:
            await interaction.response.send_message(
                "❌ Không còn ai sống để dùng Bình Độc.", ephemeral=True
            )
            return

        select = discord.ui.Select(
            placeholder="☠️ Chọn người để đầu độc",
            options=opts,
            min_values=1,
            max_values=1
        )

        async def cb(i):
            if i.user.id != self.uid:
                await i.response.send_message(
                    "❌ Đây không phải lượt của bạn!", ephemeral=True
                )
                return
            if g.players[self.uid].get("witch_poison_used"):
                await i.response.send_message(
                    "⚠️ Bình Độc đã được dùng từ trước.", ephemeral=True
                )
                return

            target = int(select.values[0])
            if target not in g.players or not g.players[target].get("alive"):
                await i.response.send_message(
                    "❌ Mục tiêu không còn sống.", ephemeral=True
                )
                return

            g.players[self.uid]["witch_poison_used"] = True
            g.night_actions["witch_poison"] = target
            await i.response.send_message(
                f"☠️ Đã dùng **Bình Độc** lên "
                f"**{g.players[target]['member'].display_name}**.",
                ephemeral=True
            )

        select.callback = cb
        v = discord.ui.View(timeout=20)
        v.add_item(select)
        await interaction.response.send_message(
            "☠️ Chọn mục tiêu cho **Bình Độc**:",
            view=v,
            ephemeral=True
        )

    async def done(self, interaction):
        if not await self._auth(interaction):
            return
        await interaction.response.edit_message(
            content=(
                "🧪 **Đã chốt lượt Phù Thủy.**\n"
                f"💚 Bình Cứu: {'đã dùng' if self.game.players[self.uid].get('witch_heal_used') else 'chưa dùng'}\n"
                f"☠️ Bình Độc: {'đã dùng' if self.game.players[self.uid].get('witch_poison_used') else 'chưa dùng'}"
            ),
            view=None
        )
        self.stop()


# ---------- Cupid: 1 menu chọn 2 người, không tạo 2 select + giảm await nối tiếp ----------
class WWCupidView(discord.ui.View):
    def __init__(self, game, cupid_id):
        super().__init__(timeout=25)
        self.game = game
        self.cupid_id = cupid_id

        options = [
            discord.SelectOption(
                label=d["member"].display_name[:100],
                value=str(uid),
                emoji="💘"
            )
            for uid, d in game.players.items()
            if d.get("alive") and uid != cupid_id
        ][:25]

        self.select = discord.ui.Select(
            placeholder="💘 Chọn đúng 2 người để ghép đôi",
            options=options,
            min_values=2,
            max_values=2
        )
        self.select.callback = self.select_pair
        self.add_item(self.select)

        confirm = discord.ui.Button(
            label="Ghép đôi ngay",
            style=discord.ButtonStyle.danger,
            emoji="💞"
        )
        confirm.callback = self.confirm_pair
        self.add_item(confirm)

    async def select_pair(self, interaction):
        if interaction.user.id != self.cupid_id:
            await interaction.response.send_message(
                "❌ Đây không phải lượt của bạn!", ephemeral=False
            )
            return
        await interaction.response.send_message(
            "💘 Đã chọn 2 người. Bấm **Ghép đôi ngay** để xác nhận.",
            ephemeral=False
        )

    async def confirm_pair(self, interaction):
        if interaction.user.id != self.cupid_id:
            await interaction.response.send_message(
                "❌ Đây không phải lượt của bạn!", ephemeral=False
            )
            return

        values = list(self.select.values)
        if len(values) != 2:
            await interaction.response.send_message(
                "❌ Hãy chọn đúng **2 người**.", ephemeral=False
            )
            return

        a, b = map(int, values)
        if a == b or a not in self.game.players or b not in self.game.players:
            await interaction.response.send_message(
                "❌ Hai người yêu phải là 2 người khác nhau.", ephemeral=False
            )
            return

        if not (
            self.game.players[a].get("alive")
            and self.game.players[b].get("alive")
        ):
            await interaction.response.send_message(
                "❌ Một trong hai người đã không còn sống.", ephemeral=False
            )
            return

        self.game.players[a]["lover"] = b
        self.game.players[b]["lover"] = a
        self.game.players[a]["lover_linked"] = True
        self.game.players[b]["lover_linked"] = True

        # Trả lời interaction trước, rồi mới notify để không bị cảm giác Discord treo.
        await interaction.response.edit_message(
            content="💘 **Ghép đôi thành công!** Đã báo riêng cho cả hai người.",
            view=None
        )
        self.stop()

        # Gửi 2 DM song song thay vì chờ người A xong mới gửi người B.
        await asyncio.gather(
            ww_notify_lover_one(self.game, a, b),
            ww_notify_lover_one(self.game, b, a),
            return_exceptions=True
        )


async def ww_notify_lover_one(game, me_id, partner_id):
    me = game.players[me_id]["member"]
    partner = game.players[partner_id]["member"]
    try:
        embed = discord.Embed(
            title="💘 BẠN ĐÃ CÓ NGƯỜI YÊU",
            description=(
                f"Bạn và **{partner.display_name}** đã được Cupid ghép thành một cặp.\n\n"
                "❤️ Hai người **biết nhau là Người Yêu**.\n"
                "🛡️ Hãy phối hợp, bảo vệ và cân nhắc vote để cả hai sống sót.\n"
                "⚠️ Nếu một người chết, người còn lại cũng sẽ chết theo."
            ),
            color=0xff4d8d
        )
        await me.send(embed=embed)
    except Exception:
        pass


async def ww_notify_lovers(game, a, b):
    await asyncio.gather(
        ww_notify_lover_one(game, a, b),
        ww_notify_lover_one(game, b, a),
        return_exceptions=True
    )


# ---------- Vote: hiện số phiếu realtime ngay trên message đang vote ----------
def _ww_vote_tally_text(game):
    votes = game.night_actions.get("votes", {})
    tally = {}
    blank = 0

    for voter, target in votes.items():
        if target == "blank":
            blank += 1
            continue
        weight = (
            2
            if game.players.get(voter, {}).get("role") == "Trưởng Làng"
            and game.players.get(voter, {}).get("mayor_revealed")
            else 1
        )
        tally[target] = tally.get(target, 0) + weight

    rows = []
    for uid, data in game.players.items():
        if not data.get("alive"):
            continue
        rows.append(
            f"• **{data['member'].display_name[:40]}** — "
            f"**{tally.get(uid, 0)} phiếu**"
        )

    return (
        "\n".join(rows)
        + f"\n• 🏳️ **Phiếu trắng:** **{blank}**"
        if rows
        else f"🏳️ **Phiếu trắng:** **{blank}**"
    )


class WWVoteView(discord.ui.View):
    def __init__(self, game, on_update=None):
        super().__init__(timeout=WW_VOTE_SECONDS_V10)
        self.game = game
        self.on_update = on_update

        for uid, d in game.players.items():
            if not d.get("alive"):
                continue

            button = discord.ui.Button(
                label=f"Treo {d['member'].display_name[:70]}",
                style=discord.ButtonStyle.blurple
            )

            async def cb(interaction, target=uid):
                voter = interaction.user.id
                if (
                    voter not in game.players
                    or not game.players[voter].get("alive")
                ):
                    await interaction.response.send_message(
                        "❌ Bạn không còn quyền vote.", ephemeral=True
                    )
                    return

                # Cho phép đổi vote; tally tự cập nhật theo lựa chọn mới.
                game.night_actions.setdefault("votes", {})[voter] = target

                if self.on_update:
                    try:
                        await self.on_update()
                    except Exception:
                        pass

            button.callback = cb
            self.add_item(button)

        blank = discord.ui.Button(
            label="Phiếu trắng",
            style=discord.ButtonStyle.secondary,
            emoji="🏳️"
        )

        async def blank_cb(interaction):
            voter = interaction.user.id
            if (
                voter not in game.players
                or not game.players[voter].get("alive")
            ):
                await interaction.response.send_message(
                    "❌ Bạn không còn quyền vote.", ephemeral=True
                )
                return

            game.night_actions.setdefault("votes", {})[voter] = "blank"
            
            if self.on_update:
                try:
                    await self.on_update()
                except Exception:
                    pass

        blank.callback = blank_cb
        self.add_item(blank)


async def start_voting(game):
    game.night_actions["votes"] = {}

    msg_holder = {"msg": None}

    async def refresh_vote_message():
        msg = msg_holder["msg"]
        if msg is None:
            return
        try:
            embed.description = (
                "🔐 **Phiếu kín** — tên người vote không hiện.\n"
                "👑 Trưởng Làng đã công khai sẽ có **2 phiếu**.\n\n"
                "📊 **SỐ PHIẾU HIỆN TẠI**\n"
                f"{_ww_vote_tally_text(game)}\n\n"
                + ui_status_line("VOTE", max(0, remaining_holder["value"]))
            )
            await msg.edit(embed=embed, view=view)
        except Exception:
            pass

    remaining_holder = {"value": WW_VOTE_SECONDS_V10}
    embed = ui_panel(
        "BỎ PHIẾU TREO CỔ",
        "",
        color="danger",
        emoji="⚖️"
    )
    view = WWVoteView(game, refresh_vote_message)
    msg = await game.ctx.send(embed=embed, view=view)
    msg_holder["msg"] = msg
    await refresh_vote_message()

    for remaining in range(WW_VOTE_SECONDS_V10 - 1, -1, -1):
        remaining_holder["value"] = remaining
        if remaining % 2 == 0 or remaining <= 5:
            await refresh_vote_message()
        if remaining:
            await asyncio.sleep(1)

    view.stop()
    try:
        await msg.edit(
            embed=discord.Embed(
                title="⚖️ HẾT GIỜ VOTE",
                description=_ww_vote_tally_text(game),
                color=UI_COLORS["danger"]
            ),
            view=None
        )
    except Exception:
        pass

    await asyncio.sleep(1.5)
    try:
        await msg.delete()
    except Exception:
        pass

    await resolve_voting(game)


# ---------- Manh mối: ngắn, dễ hiểu, có liên quan hơn ----------
WW_SIMPLE_CLUES = [
    "Có dấu chân mới quanh nhà {target}, nhưng chưa đủ biết ai để lại.",
    "Có tiếng động lạ gần chỗ {target} vào ban đêm.",
    "Một người đã xuất hiện gần khu vực {target} khá muộn.",
    "Có dấu hiệu cho thấy ai đó đã đi qua khu vực {target}.",
    "Có chuyện bất thường xảy ra gần {target}, nhưng chưa thể kết luận thủ phạm.",
    "Có người đang cố tránh bị chú ý quanh khu vực {target}.",
    "Một hành động ban đêm có vẻ không khớp với lời kể của ai đó.",
    "Có dấu vết liên quan đến khu vực {target}, nhưng vẫn cần suy luận thêm.",
]

async def trigger_random_clue(channel, game):
    alive_players = [
        p for p in game.players.values() if p.get("alive")
    ]
    if not alive_players:
        return

    target = random.choice(alive_players)
    clue_text = random.choice(WW_SIMPLE_CLUES).format(
        target=target["member"].display_name
    )

    embed = ui_panel(
        "MANH MỐI",
        (
            f"📍 **Khu vực:** {target['member'].display_name}\n\n"
            f"🧩 {clue_text}\n\n"
            "💡 *Manh mối chỉ là gợi ý, không phải bằng chứng chắc chắn.*"
        ),
        color="purple",
        emoji="🧩"
    )
    embed.set_footer(text="🧩 Manh mối dễ hiểu hơn • KS-BOT Ma Sói")
    await channel.send(embed=embed)


# ---------- Chat ẩn: DM vẫn giữ nguyên, thêm nút mở bảng riêng bằng ephemeral ----------
class WWHiddenPanelView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(
        label="Mở bảng riêng",
        style=discord.ButtonStyle.primary,
        emoji="🔒"
    )
    async def open_private(self, interaction, button):
        uid = interaction.user.id
        d = self.game.players.get(uid)

        if not d:
            await interaction.response.send_message(
                "❌ Bạn không thuộc ván này.", ephemeral=True
            )
            return

        role = d.get("role", "Dân Làng")
        content = (
            f"🔒 **BẢNG RIÊNG CỦA BẠN**\n"
            f"🎭 Vai trò: **{role}**\n\n"
            "Các lựa chọn kỹ năng vẫn được gửi DM như bình thường."
        )

        # Chỉ hiện cho người bấm; không spam kênh chung.
        await interaction.response.send_message(
            content,
            ephemeral=True
        )


async def run_game(game):
    await ww_assign_roles(game)

    start_embed = ui_panel(
        "MA SÓI ĐÃ BẮT ĐẦU",
        (
            f"👥 **{len(game.players)} người chơi**  •  "
            f"🐺 **{len(game.wolves)} Sói**\n\n"
            "🔐 Vai trò + kỹ năng vẫn gửi **DM như bình thường**.\n"
            "📱 Nếu dùng điện thoại, bấm **Mở bảng riêng** để xem thông tin ẩn ngay trong chat.\n"
            "🌙 Trời đã tối — hãy dùng kỹ năng thật khôn ngoan."
        ),
        color="purple",
        emoji="🎭"
    )
    await game.ctx.send(embed=start_embed, view=WWHiddenPanelView(game))
    await ww_show_role_setup_v10(game)
    await start_night(game)


# ---------- Lobby có GIF wolf dance, chỉ ở đây ----------
try:
    bot.remove_command("masoi")
except Exception:
    pass

@bot.hybrid_command(name="masoi", aliases=["wolf"])
async def cmd_masoi_patched(ctx):
    """Tạo phòng Ma Sói."""
    if ctx.channel.id in game_lobbies:
        game = game_lobbies[ctx.channel.id]
        if game.state != "waiting":
            await ctx.send(
                "❌ Ván chơi đang diễn ra, không thể tạo phòng chờ mới!"
            )
            return
        try:
            if game.lobby_message:
                await game.lobby_message.delete()
        except Exception:
            pass
    else:
        game = WerewolfGame(ctx)
        game_lobbies[ctx.channel.id] = game

    view = JoinGameView(game)
    embed = discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=(
            "Bấm nút bên dưới để tham gia phòng chơi!\n"
            "Chủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5 người).\n\n"
            f"**Danh sách người chơi ({len(game.players)}):**\n"
            + (
                "\n".join(
                    f"• {data['member'].mention}"
                    for data in game.players.values()
                )
                if game.players
                else "Chưa có ai tham gia..."
            )
        ),
        color=UI_COLORS["cyan"]
    )
    embed.set_image(url=WW_LOBBY_GIF_URL)
    embed.set_footer(text="🐺 Wolf dance chỉ ở phòng chờ • KS-BOT")
    game.lobby_message = await ctx.send(embed=embed, view=view)
    add_exp_to_user(ctx.author.id, 20)


# ================================================================
# 🚀 START BOT — TOKEN CŨ GIỮ NGUYÊN
# ================================================================

# ================================================================
# 🚀 START BOT — LUÔN ĐẶT Ở CUỐI FILE ĐỂ TOÀN BỘ V11 OVERRIDE ĐƯỢC NẠP TRƯỚC
# ================================================================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Thiếu biến môi trường DISCORD_TOKEN. Vào Discord Developer Portal "
        "reset token mới, rồi đặt vào Render > Environment > DISCORD_TOKEN "
        "— không hardcode token trong code nữa."
    )

# ================================================================
# PATCH V12 — FIX ROLE DM / WITCH / VOTE / NO HIDDEN CHAT
# Chỉ bổ sung/ghi đè logic Ma Sói; giữ nguyên code và token hiện tại.
# ================================================================

WW_ACTION_TIMEOUT_V12 = 45

async def _ww_dm(member, content=None, *, embed=None, view=None):
    """Gửi thao tác Ma Sói qua DM; không dùng ephemeral trong DM."""
    try:
        return await member.send(content=content, embed=embed, view=view)
    except Exception as exc:
        print(f"[WW DM] Không gửi được DM cho {getattr(member, 'id', '?')}: {exc}")
        return None


class WWWitchView(discord.ui.View):
    """Phù Thủy: Cứu và Độc độc lập; phản hồi bình thường trong DM."""
    def __init__(self, game, uid, victim_id):
        super().__init__(timeout=WW_ACTION_TIMEOUT_V12)
        self.game = game
        self.uid = uid
        self.victim_id = victim_id

        save = discord.ui.Button(label="Cứu nạn nhân", style=discord.ButtonStyle.success, emoji="💚")
        poison = discord.ui.Button(label="Dùng bình Độc", style=discord.ButtonStyle.danger, emoji="☠️")
        done = discord.ui.Button(label="Xong lượt", style=discord.ButtonStyle.secondary, emoji="✅")
        save.callback = self.save
        poison.callback = self.poison
        done.callback = self.done
        self.add_item(save)
        self.add_item(poison)
        self.add_item(done)

    def _ok(self, interaction):
        return interaction.user.id == self.uid

    async def save(self, interaction):
        if not self._ok(interaction):
            return await interaction.response.send_message("❌ Đây không phải lượt của bạn!")
        d = self.game.players.get(self.uid, {})
        if d.get("witch_heal_used"):
            return await interaction.response.send_message("⚠️ Bình Cứu đã dùng rồi.")
        if not self.victim_id or not self.game.players.get(self.victim_id, {}).get("alive"):
            return await interaction.response.send_message("⚠️ Nạn nhân không còn hợp lệ để cứu.")
        d["witch_heal_used"] = True
        self.game.night_actions["witch_save"] = True
        await interaction.response.send_message(
            f"💚 Đã dùng **Bình Cứu** cho **{self.game.players[self.victim_id]['member'].mention}**."
        )

    async def poison(self, interaction):
        if not self._ok(interaction):
            return await interaction.response.send_message("❌ Đây không phải lượt của bạn!")
        d = self.game.players.get(self.uid, {})
        if d.get("witch_poison_used"):
            return await interaction.response.send_message("⚠️ Bình Độc đã dùng rồi.")

        opts = [
            discord.SelectOption(
                label=data["member"].display_name[:100],
                value=str(pid),
                description=f"Đầu độc {data['member'].mention}"[:100],
                emoji="☠️"
            )
            for pid, data in self.game.players.items()
            if data.get("alive") and pid != self.uid
        ][:25]
        if not opts:
            return await interaction.response.send_message("❌ Không còn người sống để dùng Bình Độc.")

        select = discord.ui.Select(
            placeholder="☠️ Chọn người để đầu độc",
            options=opts,
            min_values=1,
            max_values=1,
        )

        async def choose(i):
            if i.user.id != self.uid:
                return await i.response.send_message("❌ Đây không phải lượt của bạn!")
            if d.get("witch_poison_used"):
                return await i.response.send_message("⚠️ Bình Độc đã dùng rồi.")
            target = int(select.values[0])
            if target not in self.game.players or not self.game.players[target].get("alive"):
                return await i.response.send_message("❌ Mục tiêu không còn sống.")
            d["witch_poison_used"] = True
            self.game.night_actions["witch_poison"] = target
            await i.response.send_message(
                f"☠️ Đã dùng **Bình Độc** lên **{self.game.players[target]['member'].mention}**."
            )

        select.callback = choose
        v = discord.ui.View(timeout=WW_ACTION_TIMEOUT_V12)
        v.add_item(select)
        await interaction.response.send_message("☠️ **Bình Độc:** chọn mục tiêu:", view=v)

    async def done(self, interaction):
        if not self._ok(interaction):
            return await interaction.response.send_message("❌ Đây không phải lượt của bạn!")
        d = self.game.players[self.uid]
        await interaction.response.edit_message(
            content=(
                "🧪 **Đã chốt lượt Phù Thủy.**\n"
                f"💚 Bình Cứu: {'đã dùng' if d.get('witch_heal_used') else 'chưa dùng'}\n"
                f"☠️ Bình Độc: {'đã dùng' if d.get('witch_poison_used') else 'chưa dùng'}"
            ),
            view=None,
        )
        self.stop()


class WWVoteView(discord.ui.View):
    """Vote kín; bảng điểm dùng @mention để dễ nhìn."""
    def __init__(self, game, on_update=None):
        super().__init__(timeout=WW_VOTE_SECONDS_V10)
        self.game = game
        self.on_update = on_update
        for uid, d in game.players.items():
            if not d.get("alive"):
                continue
            b = discord.ui.Button(label=f"Treo {d['member'].display_name[:65]}", style=discord.ButtonStyle.blurple)
            async def cb(interaction, target=uid):
                voter = interaction.user.id
                if voter not in game.players or not game.players[voter].get("alive"):
                    return await interaction.response.send_message("❌ Bạn không còn quyền vote.")
                game.night_actions.setdefault("votes", {})[voter] = target
                
                if self.on_update:
                    await self.on_update()
            b.callback = cb
            self.add_item(b)

        blank = discord.ui.Button(label="Phiếu trắng", style=discord.ButtonStyle.secondary, emoji="🏳️")
        async def blank_cb(interaction):
            voter = interaction.user.id
            if voter not in game.players or not game.players[voter].get("alive"):
                return await interaction.response.send_message("❌ Bạn không còn quyền vote.")
            game.night_actions.setdefault("votes", {})[voter] = "blank"
            if self.on_update:
                await self.on_update()
        blank.callback = blank_cb
        self.add_item(blank)


def _ww_vote_tally_text_v12(game):
    votes = game.night_actions.get("votes", {})
    tally, blank = {}, 0
    for voter, target in votes.items():
        if target == "blank":
            blank += 1
            continue
        weight = 2 if game.players.get(voter, {}).get("role") == "Trưởng Làng" and game.players[voter].get("mayor_revealed") else 1
        tally[target] = tally.get(target, 0) + weight
    rows = []
    for uid, d in game.players.items():
        if d.get("alive"):
            rows.append(f"• {d['member'].mention} — **{tally.get(uid, 0)} phiếu**")
    return "\n".join(rows) + f"\n• 🏳️ **Phiếu trắng:** **{blank}**"


async def start_voting(game):
    game.night_actions["votes"] = {}
    holder = {"msg": None, "remaining": WW_VOTE_SECONDS_V10}
    embed = ui_panel("BỎ PHIẾU TREO CỔ", "", color="danger", emoji="⚖️")
    view = WWVoteView(game)
    msg = await game.ctx.send(embed=embed, view=view)
    holder["msg"] = msg

    async def refresh():
        try:
            embed.description = (
                "🔐 **Vote kín** — không hiện ai đã vote ai.\n"
                "👑 Trưởng Làng công khai = **2 phiếu**.\n\n"
                "📊 **SỐ PHIẾU HIỆN TẠI**\n"
                f"{_ww_vote_tally_text_v12(game)}\n\n"
                f"⏱️ Còn **{holder['remaining']}s**"
            )
            await msg.edit(embed=embed, view=view)
        except Exception as exc:
            print(f"[WW VOTE] refresh lỗi: {exc}")

    view.on_update = refresh
    await refresh()
    for remaining in range(WW_VOTE_SECONDS_V10 - 1, -1, -1):
        holder["remaining"] = remaining
        if remaining % 2 == 0 or remaining <= 5:
            await refresh()
        if remaining:
            await asyncio.sleep(1)
    view.stop()
    try:
        await msg.edit(
            embed=discord.Embed(
                title="⚖️ HẾT GIỜ VOTE",
                description=_ww_vote_tally_text_v12(game),
                color=UI_COLORS["danger"],
            ),
            view=None,
        )
    except Exception:
        pass
    await asyncio.sleep(1)
    await resolve_voting(game)


async def start_night(game):
    """Gửi toàn bộ kỹ năng qua DM; không còn bảng ẩn trong kênh chung."""
    game.state = "night"
    game.night_actions = {"wolf_votes": {}}
    alive = ww_alive(game)
    await game.ctx.send(embed=ui_panel(
        "ĐÊM XUỐNG",
        "🌙 Vai trò đang hành động. **Mọi kỹ năng đều dùng riêng qua DM với KS-BOT.**",
        color="dark", emoji="🌙"
    ))

    # Cupid
    if getattr(game, "first_night", False):
        cupid = next((u for u,d in game.players.items() if d.get("role") in ("Cupid", "Thần Tình Yêu") and d.get("alive")), None)
        if cupid:
            await _ww_dm(game.players[cupid]["member"],
                "💘 **CUPID — ĐÊM ĐẦU**\nChọn đúng 2 người rồi bấm **Ghép đôi ngay**.",
                view=WWCupidView(game, cupid))

    # Seer / Guard / Detective / Medium
    if game.seer and game.players.get(game.seer, {}).get("alive"):
        await _ww_dm(game.players[game.seer]["member"], "🔮 **TIÊN TRI:** Chọn 1 người để soi.", view=WWTownNightView(game, game.seer, "seer"))
    if game.guard and game.players.get(game.guard, {}).get("alive"):
        await _ww_dm(game.players[game.guard]["member"], "🛡️ **BẢO VỆ:** Chọn người cần bảo vệ.", view=WWTownNightView(game, game.guard, "guard"))

    detective = next((u for u,d in game.players.items() if d.get("role") == "Thám Tử" and d.get("alive")), None)
    if detective:
        await _ww_dm(game.players[detective]["member"], "🕵️ **THÁM TỬ:** Chọn 2 người để kiểm tra.", view=WWDetectiveView(game, detective))

    medium = next((u for u,d in game.players.items() if d.get("role") == "Nhà Tiên Tri" and d.get("alive")), None)
    if medium:
        dead = [u for u,d in game.players.items() if not d.get("alive")]
        if dead:
            await _ww_dm(game.players[medium]["member"], "📜 **NHÀ TIÊN TRI:** Chọn 1 người đã chết để xem vai trò.", view=WWTownNightView(game, medium, "medium"))

    # Sói: mỗi Sói chọn 1 nạn nhân qua DM.
    for wolf_id in game.wolves:
        if not game.players.get(wolf_id, {}).get("alive"):
            continue
        opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(uid), emoji="🐺")
                for uid,d in game.players.items() if d.get("alive") and not ww_is_wolf(d.get("role"))][:25]
        if not opts:
            continue
        view = discord.ui.View(timeout=WW_ACTION_TIMEOUT_V12)
        sel = discord.ui.Select(placeholder="🐺 Chọn nạn nhân", options=opts, min_values=1, max_values=1)
        async def wolf_cb(interaction, wid=wolf_id, select=sel):
            if interaction.user.id != wid:
                return await interaction.response.send_message("❌ Đây không phải lượt của bạn!")
            target = int(select.values[0])
            game.night_actions.setdefault("wolf_votes", {})[wid] = target
            await interaction.response.send_message(f"🐺 Đã ghi nhận vote kín cho {game.players[target]['member'].mention}.")
        sel.callback = wolf_cb
        view.add_item(sel)
        await _ww_dm(game.players[wolf_id]["member"], "🐺 **HỘI SÓI:** Chọn nạn nhân. Mỗi Sói 1 phiếu.", view=view)

    await asyncio.sleep(WW_ACTION_TIMEOUT_V12)

    # Tính mục tiêu Sói sau khi hết thời gian.
    wolf_votes = game.night_actions.get("wolf_votes", {})
    tally = {}
    for target in wolf_votes.values():
        tally[target] = tally.get(target, 0) + 1
    killed_id = None
    if tally:
        mx = max(tally.values())
        killed_id = random.choice([u for u,v in tally.items() if v == mx])
    game.night_actions["wolf_target"] = killed_id

    # Phù Thủy được báo rõ mục tiêu và có đủ 2 bình.
    witch = next((u for u,d in game.players.items() if d.get("role") == "Phù Thủy" and d.get("alive")), None)
    if witch:
        if killed_id:
            target_mention = game.players[killed_id]["member"].mention
            await _ww_dm(
                game.players[witch]["member"],
                f"🧪 **PHÙ THỦY**\n🐺 Sói đang nhắm vào {target_mention}.\n💚 Cứu và ☠️ Độc là **2 bình độc lập**; có thể dùng cả hai trong cùng đêm.",
                view=WWWitchView(game, witch, killed_id),
            )
        else:
            await _ww_dm(
                game.players[witch]["member"],
                "🧪 **PHÙ THỦY**\nĐêm nay Sói chưa chọn được nạn nhân. Bạn vẫn có thể dùng **Bình Độc** nếu muốn.",
                view=WWWitchView(game, witch, None),
            )
        await asyncio.sleep(WW_ACTION_TIMEOUT_V12)

    await resolve_night(game)


async def run_game(game):
    await ww_assign_roles(game)
    await game.ctx.send(
        f"🎭 **MA SÓI ĐÃ BẮT ĐẦU!**\n"
        f"👥 Người chơi: **{len(game.players)}** • 🐺 Sói: **{len(game.wolves)}**\n"
        "🔐 Vai trò và kỹ năng được gửi **riêng qua DM với KS-BOT**."
    )
    await ww_show_role_setup_v10(game)
    await start_night(game)

# ================================================================
# PATCH V15 — BỔ SUNG ROLE + NÚT COPY + RỜI PHÒNG + MUTE ĐÊM/VOTE
# CHỈ THÊM/OVERRIDE; GIỮ NGUYÊN TOÀN BỘ CODE GỐC PHÍA TRÊN.
# ================================================================

WW_V15_TOWN = [
    "Dân Làng", "Tiên Tri", "Bảo Vệ", "Phù Thủy", "Thợ Săn", "Thị Trưởng",
    "Hiệp Sĩ Thánh", "Già Làng", "Thần Tình Yêu", "Cô Bé Ty Ty", "Thầy Pháp",
    "Dân Làng Oán Hận", "Kẻ Phản Bội", "Nửa Người Nửa Sói", "Hiệp Sĩ Mù",
    "Kẻ Khủng Bố", "Kẻ Bắt Chước", "Người Học Việc", "Người Thêu Dệt Lời Thề",
    "Kẻ Truy Lùng", "Kẻ Báo Mộng", "Kẻ Thấu Thị", "Nhân Chứng", "Thầy Thuốc",
    "Người Vận Chuyển", "Quản Ngục",
]
WW_V15_WOLVES = [
    "Ma Sói", "Sói Con", "Sói Đầu Đàn", "Sói Sát Thủ", "Sói Câm Lặng",
    "Sói Gián Điệp", "Sói Lửa", "Sói Sao Chép", "Sói Cảm Hóa", "Sói Hộ Vệ",
    "Sói Tham Lam", "Sói Độc", "Sói Dọn Xác", "Sói Ảo Thuật", "Sói Thao Túng",
]
WW_V15_THIRD = [
    "Thẩm Phán", "Đạo Tặc", "Sói Trắng", "Kẻ Chán Đời", "Ma Cà Rồng",
    "Kẻ Giết Người Hàng Loạt", "Giáo Chủ", "Kẻ Thu Thập Linh Hồn", "Kẻ Đổi Màu",
    "Trăn Quỷ", "Kẻ Cướp Danh Tính", "Kí Sinh Trùng", "Kẻ Thôi Miên", "Ác Quỷ",
    "Ảnh Tử", "Kẻ Mất Trí Nhớ", "Vũ Khí Sinh Học", "Kẻ Tráo Trở", "Quạ",
]

WW_V15_INFO = {
    # Dân
    "Dân Làng": ("🧑‍🌾", "Phe Dân", "Không có kỹ năng đêm; suy luận và bỏ phiếu."),
    "Tiên Tri": ("🔮", "Phe Dân", "Mỗi đêm soi 1 người để biết có phải Sói hay không."),
    "Bảo Vệ": ("🛡️", "Phe Dân", "Mỗi đêm bảo vệ 1 người; không chặn phát bắn xuyên bảo vệ."),
    "Phù Thủy": ("🧪", "Phe Dân", "Có 1 Bình Cứu và 1 Bình Độc; hai bình dùng độc lập."),
    "Thợ Săn": ("🏹", "Phe Dân", "Khi chết được dùng nút chọn 1 người còn sống để bắn hoặc bỏ qua."),
    "Thị Trưởng": ("👑", "Phe Dân", "Có thể công khai; khi công khai phiếu tính 2."),
    "Trưởng Làng": ("👑", "Phe Dân", "Tên cũ tương thích với code hiện tại; có thể công khai và phiếu tính 2."),
    "Hiệp Sĩ Thánh": ("⚔️", "Phe Dân", "Một lần trong ván có thể thánh trừng 1 mục tiêu; nếu là Sói thì chết."),
    "Già Làng": ("👴", "Phe Dân", "Lần đầu bị Sói tấn công sẽ sống sót."),
    "Thần Tình Yêu": ("💘", "Phe Dân", "Đêm đầu ghép 2 người thành Người Yêu."),
    "Cô Bé Ty Ty": ("👧", "Phe Dân", "Mỗi đêm có thể nhìn trộm và biết một người có phải Sói."),
    "Thầy Pháp": ("🔯", "Phe Dân", "Mỗi đêm khóa kỹ năng của 1 người."),
    "Dân Làng Oán Hận": ("😡", "Phe Dân", "Nếu bị treo, để lại dấu ấn khiến người bỏ phiếu cuối cùng bị trừ lợi thế ở đêm kế."),
    "Kẻ Phản Bội": ("🗡️", "Phe Dân", "Phe Dân nhưng nếu toàn bộ Sói chết, trở thành Sói ở đêm kế."),
    "Nửa Người Nửa Sói": ("🐺", "Phe Dân", "Ban đầu phe Dân; nếu bị Sói tấn công và sống sót, chuyển thành Sói."),
    "Hiệp Sĩ Mù": ("🛡️", "Phe Dân", "Một lần phản công người tấn công mình; không được biết vai trò mục tiêu."),
    "Kẻ Khủng Bố": ("💣", "Phe Dân", "Khi chết có thể kích hoạt nổ, hạ 1 người còn sống được chọn bằng nút."),
    "Kẻ Bắt Chước": ("🪞", "Phe Dân", "Đêm đầu chọn 1 người và sao chép vai trò của họ từ đêm sau."),
    "Người Học Việc": ("📚", "Phe Dân", "Khi Tiên Tri chết, nhận vai trò Tiên Tri."),
    "Người Thêu Dệt Lời Thề": ("🧵", "Phe Dân", "Mỗi đêm liên kết 2 người; một người chết thì người kia bị đánh dấu."),
    "Kẻ Truy Lùng": ("🔎", "Phe Dân", "Theo dõi 1 người và biết họ đã thực hiện kỹ năng hay chưa."),
    "Kẻ Báo Mộng": ("💤", "Phe Dân", "Chọn 1 người; nhận 2 vai trò có thể là của mục tiêu."),
    "Kẻ Thấu Thị": ("👁️", "Phe Dân", "Biết phe của 1 người mỗi đêm."),
    "Nhân Chứng": ("🧾", "Phe Dân", "Đêm đầu biết tên 1 Sói ngẫu nhiên."),
    "Thầy Thuốc": ("💊", "Phe Dân", "Mỗi đêm chữa 1 người; hiệu lực chặn 1 sát thương thường."),
    "Người Vận Chuyển": ("🔄", "Phe Dân", "Đổi chỗ 2 người trong đêm, làm kỹ năng nhắm vào họ bị chuyển hướng."),
    "Quản Ngục": ("⛓️", "Phe Dân", "Giam 1 người: chặn kỹ năng của họ và bảo vệ họ khỏi sát thương thường."),
    # Sói
    "Ma Sói": ("🐺", "Phe Sói", "Cùng bầy chọn nạn nhân mỗi đêm."),
    "Sói Con": ("🐺", "Phe Sói", "Nếu chết, đêm kế bầy Sói được thêm một lượt tấn công."),
    "Sói Đầu Đàn": ("👑🐺", "Phe Sói", "Phiếu săn của Sói Đầu Đàn tính 2 khi chọn nạn nhân."),
    "Sói Sát Thủ": ("🔪🐺", "Phe Sói", "Một lần giết xuyên bảo vệ."),
    "Sói Câm Lặng": ("🤫🐺", "Phe Sói", "Mỗi đêm chọn 1 người; người đó bị cấm nhắn trong đêm/vote kế."),
    "Sói Gián Điệp": ("🕵️🐺", "Phe Sói", "Soi vai trò thật của 1 người."),
    "Sói Lửa": ("🔥🐺", "Phe Sói", "Đòn tấn công bỏ qua bảo vệ và chữa thường."),
    "Sói Sao Chép": ("🪞🐺", "Phe Sói", "Sao chép kỹ năng của 1 mục tiêu cho đêm kế."),
    "Sói Cảm Hóa": ("🧿🐺", "Phe Sói", "Một lần biến 1 Dân còn sống thành Sói."),
    "Sói Hộ Vệ": ("🛡️🐺", "Phe Sói", "Bảo vệ 1 Sói khỏi sát thương thường mỗi đêm."),
    "Sói Tham Lam": ("💰🐺", "Phe Sói", "Phiếu săn của bản thân tính 2."),
    "Sói Độc": ("☠️🐺", "Phe Sói", "Đầu độc 1 người; mục tiêu chết sau một đêm nếu không được giải độc."),
    "Sói Dọn Xác": ("🧹🐺", "Phe Sói", "Một lần làm nạn nhân Sói chết không lộ vai trò."),
    "Sói Ảo Thuật": ("🎩🐺", "Phe Sói", "Khi bị Tiên Tri soi, hiển thị như không phải Sói."),
    "Sói Thao Túng": ("🧠🐺", "Phe Sói", "Một lần đổi mục tiêu vote của 1 người sang mục tiêu khác."),
    # Phe 3
    "Thẩm Phán": ("⚖️", "Phe 3", "Một lần hủy kết quả treo cổ của ngày."),
    "Đạo Tặc": ("🃏", "Phe 3", "Đêm đầu đánh cắp vai trò của 1 người và dùng kỹ năng của vai trò đó."),
    "Sói Trắng": ("🐺⚪", "Phe 3", "Sống riêng với mục tiêu giết cả Sói; một lần có thể giết 1 Sói."),
    "Kẻ Chán Đời": ("😑", "Phe 3", "Thắng ngay nếu chính mình bị treo cổ."),
    "Ma Cà Rồng": ("🧛", "Phe 3", "Một lần biến 1 người thành Ma Cà Rồng; thắng khi phe Vampire chiếm đa số."),
    "Kẻ Giết Người Hàng Loạt": ("🔪", "Phe 3", "Mỗi đêm giết 1 người; thắng khi chỉ còn mình hoặc đồng minh."),
    "Giáo Chủ": ("⛪", "Phe 3", "Mỗi đêm cải đạo 1 người sang Giáo Chủ; phe mình thắng khi chiếm đa số."),
    "Kẻ Thu Thập Linh Hồn": ("👻", "Phe 3", "Đánh dấu người chết; đạt 3 linh hồn thì thắng."),
    "Kẻ Đổi Màu": ("🌈", "Phe 3", "Mỗi đêm đổi phe tạm thời; phe cuối cùng quyết định thắng."),
    "Trăn Quỷ": ("🐍", "Phe 3", "Đầu độc 1 người; mục tiêu chết nếu không được chữa."),
    "Kẻ Cướp Danh Tính": ("🎭", "Phe 3", "Sao chép vai trò của người chết gần nhất."),
    "Kí Sinh Trùng": ("🦠", "Phe 3", "Ký sinh 1 người; nếu vật chủ chết, chuyển sang vật chủ khác."),
    "Kẻ Thôi Miên": ("🌀", "Phe 3", "Khóa kỹ năng 1 người trong đêm."),
    "Ác Quỷ": ("😈", "Phe 3", "Một lần giết xuyên bảo vệ; không thuộc phe Sói."),
    "Ảnh Tử": ("🌑", "Phe 3", "Ẩn khỏi soi phe trong một đêm."),
    "Kẻ Mất Trí Nhớ": ("🧠", "Phe 3", "Khi một người chết, có thể nhận vai trò của họ."),
    "Vũ Khí Sinh Học": ("☣️", "Phe 3", "Nhiễm 1 người; sau 2 đêm bệnh lan sang người sống khác."),
    "Kẻ Tráo Trở": ("🔀", "Phe 3", "Một lần đổi mục tiêu của 2 kỹ năng trong đêm."),
    "Quạ": ("🐦‍⬛", "Phe 3", "Đánh dấu 1 người; người đó nhận thêm 1 phiếu chống trong ngày kế."),
}

# Giữ các tên cũ mà code đang dùng, đồng thời mở rộng toàn bộ role mới.
WW_ROLE_INFO_V2.update(WW_V15_INFO)
# Alias hiển thị: Thị Trưởng dùng chung cơ chế với Trưởng Làng của code cũ.
WW_V15_WOLF_SET = set(WW_V15_WOLVES)
WW_V15_WOLF_SET.add("Sói")

def ww_is_wolf(role):
    return role in WW_V15_WOLF_SET


def ww_distribution(n):
    """Chia role theo số người; role đặc biệt mở dần để lobby nhỏ không quá loạn."""
    n = max(5, int(n))
    wolf_count = 1 if n <= 6 else 2 if n <= 9 else 3 if n <= 13 else 4 if n <= 17 else max(5, n // 4)
    wolf_count = min(wolf_count, max(1, n // 3))
    wolf_pool = ["Ma Sói"]
    if n >= 7: wolf_pool += ["Sói Đầu Đàn", "Sói Câm Lặng"]
    if n >= 10: wolf_pool += ["Sói Gián Điệp", "Sói Hộ Vệ"]
    if n >= 13: wolf_pool += ["Sói Sát Thủ", "Sói Độc", "Sói Tham Lam"]
    if n >= 16: wolf_pool += ["Sói Lửa", "Sói Dọn Xác", "Sói Ảo Thuật", "Sói Sao Chép"]
    if n >= 20: wolf_pool += ["Sói Cảm Hóa", "Sói Thao Túng", "Sói Con"]
    roles = random.sample(wolf_pool, min(wolf_count, len(wolf_pool)))
    while len(roles) < wolf_count:
        roles.append("Ma Sói")

    core = ["Tiên Tri", "Bảo Vệ", "Phù Thủy", "Thợ Săn"]
    if n >= 7: core += ["Thần Tình Yêu"]
    if n >= 9: core += ["Trưởng Làng", "Hiệp Sĩ Thánh"]
    if n >= 11: core += ["Già Làng", "Thầy Pháp", "Thầy Thuốc"]
    if n >= 13: core += ["Kẻ Truy Lùng", "Kẻ Thấu Thị", "Nhân Chứng"]
    if n >= 15: core += ["Quản Ngục", "Người Vận Chuyển", "Người Học Việc"]
    if n >= 18: core += ["Kẻ Bắt Chước", "Cô Bé Ty Ty", "Kẻ Báo Mộng", "Người Thêu Dệt Lời Thề"]
    if n >= 21: core += ["Dân Làng Oán Hận", "Kẻ Phản Bội", "Nửa Người Nửa Sói", "Hiệp Sĩ Mù", "Kẻ Khủng Bố"]

    third = []
    if n >= 10: third += ["Quạ"]
    if n >= 12: third += ["Kẻ Chán Đời", "Thẩm Phán"]
    if n >= 14: third += ["Đạo Tặc", "Sói Trắng", "Kẻ Giết Người Hàng Loạt"]
    if n >= 16: third += ["Ma Cà Rồng", "Giáo Chủ", "Kẻ Thu Thập Linh Hồn"]
    if n >= 18: third += ["Kẻ Thôi Miên", "Ác Quỷ", "Ảnh Tử", "Trăn Quỷ"]
    if n >= 20: third += ["Kẻ Đổi Màu", "Kẻ Cướp Danh Tính", "Kí Sinh Trùng"]
    if n >= 22: third += ["Kẻ Mất Trí Nhớ", "Vũ Khí Sinh Học", "Kẻ Tráo Trở"]

    candidates = core + third
    random.shuffle(candidates)
    slots = n - len(roles)
    picked = candidates[:max(0, slots)]
    roles.extend(picked)
    roles.extend(["Dân Làng"] * max(0, n - len(roles)))
    random.shuffle(roles)
    return roles[:n]


# ---------- Nút Copy lệnh + Rời phòng; không cần mở code dài ----------
WW_COPY_COMMANDS = (
    ".masoi (/masoi) — tạo phòng\n"
    ".masoiroles (/masoiroles) — xem vai trò\n"
    ".mav (/mav) — Trưởng Làng công khai\n"
    ".ksbotchannel #kênh (/ksbotchannel, alias: .kenhksbot) — đặt kênh riêng cho KS-BOT\n"
    ".lixichannel #kênh (/lixichannel, alias: .kenhlixi) — đặt kênh lì xì\n"
    ".help (/help) — xem lệnh chung"
)

class WWCommandCopyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Copy lệnh", style=discord.ButtonStyle.secondary, emoji="📋")
    async def callback(self, interaction):
        await interaction.response.send_message(
            "📋 **Danh sách lệnh KS-BOT — giữ để copy:**\n```text\n" + WW_COPY_COMMANDS + "\n```",
            ephemeral=True,
        )

class WWLeaveButton(discord.ui.Button):
    def __init__(self, game):
        super().__init__(label="Rời phòng", style=discord.ButtonStyle.danger, emoji="🚪")
        self.game = game
    async def callback(self, interaction):
        uid = interaction.user.id
        if self.game.state != "waiting":
            return await interaction.response.send_message("❌ Game đã bắt đầu, không thể rời phòng.", ephemeral=True)
        if uid not in self.game.players:
            return await interaction.response.send_message("❌ Bạn chưa tham gia phòng.", ephemeral=True)
        del self.game.players[uid]
        await interaction.response.send_message("🚪 Bạn đã rời phòng.", ephemeral=True)
        await update_lobby_embed(self.game)

class WWLobbyViewV15(JoinGameView):
    def __init__(self, game):
        super().__init__(game)
        self.add_item(WWLeaveButton(game))
        self.add_item(WWCommandCopyButton())

# Override chỉ phần lobby: bỏ GIF, giữ nguyên cơ chế cũ.
async def update_lobby_embed(game):
    if not game.lobby_message:
        return
    player_list = "\n".join(f"• {data['member'].mention}" for data in game.players.values()) or "Chưa có ai tham gia..."
    embed = discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=(
            "Bấm nút bên dưới để tham gia.\n"
            "Chủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5).\n\n"
            f"**Danh sách người chơi ({len(game.players)}):**\n{player_list}"
        ), color=UI_COLORS["cyan"]
    )
    await game.lobby_message.edit(embed=embed, view=WWLobbyViewV15(game))

try:
    bot.remove_command("masoi")
except Exception:
    pass

@bot.hybrid_command(name="masoi", aliases=["wolf"])
async def cmd_masoi_v15(ctx):
    """Tạo phòng Ma Sói."""
    if ctx.channel.id in game_lobbies:
        game = game_lobbies[ctx.channel.id]
        if game.state != "waiting":
            return await ctx.send("❌ Ván chơi đang diễn ra, không thể tạo phòng mới!")
        try:
            if game.lobby_message:
                await game.lobby_message.delete()
        except Exception:
            pass
    else:
        game = WerewolfGame(ctx)
        game_lobbies[ctx.channel.id] = game
    game.lobby_message = await ctx.send(
        embed=discord.Embed(
            title="🐺 PHÒNG CHỜ MA SÓI",
            description=(
                "Bấm **Tham Gia Ma Sói** để vào game.\n"
                "Chủ phòng bấm **Bắt Đầu Game** khi đủ người (tối thiểu 5).\n\n"
                "📋 Có nút **Copy lệnh** và 🚪 **Rời phòng** bên dưới.\n\n"
                "**Danh sách người chơi:** Chưa có ai tham gia..."
            ), color=UI_COLORS["cyan"]
        ),
        view=WWLobbyViewV15(game),
    )


# ---------- Tắt hoàn toàn manh mối: giữ code cũ, chỉ tháo command khỏi bot ----------
try:
    bot.remove_command("manhmoi")
except Exception:
    pass


# ---------- .kenhksbot: chỉ định kênh của KS-BOT ----------
KS_BOT_CHANNELS = {}
KS_BOT_CHANNEL_FILE = "ksbot_channels.json"
try:
    with open(KS_BOT_CHANNEL_FILE, "r", encoding="utf-8") as _f:
        KS_BOT_CHANNELS = {int(k): int(v) for k, v in json.load(_f).items()}
except Exception:
    KS_BOT_CHANNELS = {}

def _save_ksbot_channels():
    try:
        with open(KS_BOT_CHANNEL_FILE, "w", encoding="utf-8") as _f:
            json.dump({str(k): int(v) for k, v in KS_BOT_CHANNELS.items()}, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass

@bot.hybrid_command(name='ksbotchannel', aliases=['kenhksbot'])
@commands.check(lambda ctx: _server_owner_only(ctx))
async def cmd_kenhksbot(ctx, channel: discord.TextChannel = None):
    """Chọn kênh riêng cho KS-BOT."""
    if channel is None:
        channel = ctx.channel
    KS_BOT_CHANNELS[ctx.guild.id] = channel.id
    _save_ksbot_channels()
    await ctx.send(f"✅ Đã đặt {channel.mention} là **kênh của KS-BOT**. Bot khác nhắn trong đây sẽ bị xóa.", delete_after=6)


@cmd_kenhksbot.error
async def cmd_kenhksbot_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Chỉ **Owner Bot** hoặc **Chủ Server** mới được dùng `.ksbotchannel`.", delete_after=5)


@bot.listen("on_message")
async def _ksbot_v15_message_guard(message):
    if message.author == bot.user:
        return
    # Bot khác nhắn trong kênh KS-BOT -> xóa ngay.
    if message.guild and KS_BOT_CHANNELS.get(message.guild.id) == message.channel.id and message.author.bot:
        try:
            await message.delete()
        except Exception:
            pass
        return
    # Khi đang ĐÊM hoặc VOTE, người chơi nhắn gì trong kênh game đều bị xóa.
    if message.guild and not message.author.bot:
        game = game_lobbies.get(message.channel.id)
        if game and game.state in {"night", "vote", "voting"} and message.author.id in game.players:
            try:
                await message.delete()
            except Exception:
                pass


# ---------- Lì xì: sửa đúng lỗi thiếu history_tx trong update_user_full ----------
class LixiClaimViewV15(discord.ui.View):
    def __init__(self, event_id, guild_id, amount):
        super().__init__(timeout=None)
        self.event_id = event_id; self.guild_id = guild_id; self.amount = amount
    @discord.ui.button(label="🧧 NHẬN LÌ XÌ", style=discord.ButtonStyle.success, emoji="💰")
    async def claim(self, interaction, button):
        uid = str(interaction.user.id)
        if LIXI_ACTIVE_EVENT.get(self.guild_id) != self.event_id:
            return await interaction.response.send_message("⏰ Lì xì này đã hết hiệu lực.", ephemeral=True)
        claimed = LIXI_CLAIMED.setdefault(self.event_id, set())
        if interaction.user.id in claimed:
            return await interaction.response.send_message("❌ Bạn đã nhận lì xì đợt này rồi!", ephemeral=True)
        try:
            data = get_user(uid)
            update_user_full(uid, data[0] + self.amount, data[1], data[2], data[3], data[4], data[5])
            claimed.add(interaction.user.id)
            await interaction.response.send_message(f"🧧 **Bạn nhận được {self.amount:,} 🪙!**", ephemeral=True)
        except Exception as exc:
            print(f"[LIXI V15] {exc}")
            await interaction.response.send_message("❌ Không thể cộng tiền lúc này.", ephemeral=True)


# ---------- Gửi hành động role nâng cao qua DM ----------
class WWAdvancedTargetView(discord.ui.View):
    def __init__(self, game, uid, action):
        super().__init__(timeout=35)
        self.game = game; self.uid = uid; self.action = action
        opts = [
            discord.SelectOption(label=d["member"].display_name[:100], value=str(pid), emoji="🎯")
            for pid, d in game.players.items()
            if d.get("alive") and pid != uid
        ][:25]
        if not opts:
            return
        select = discord.ui.Select(placeholder="🎯 Chọn mục tiêu...", options=opts, min_values=1, max_values=1)
        async def cb(interaction):
            if interaction.user.id != self.uid:
                return await interaction.response.send_message("❌ Đây không phải lượt của bạn!")
            target = int(select.values[0])
            await ww_v15_apply_action(self.game, self.uid, target, self.action)
            await interaction.response.edit_message(content="✅ Đã nhận lựa chọn.", view=None)
            self.stop()
        select.callback = cb
        self.add_item(select)

async def ww_v15_apply_action(game, uid, target, action):
    d = game.players[uid]; td = game.players[target]
    actions = game.night_actions.setdefault("v15", {})
    actions[uid] = {"action": action, "target": target}
    role = d.get("role")
    if action == "inspect":
        shown_role = td.get("role")
        if shown_role == "Sói Ảo Thuật" and role in {"Tiên Tri", "Cô Bé Ty Ty"}:
            result = "🟢 KHÔNG PHẢI SÓI"
        else:
            result = "🐺 CÓ DẤU HIỆU SÓI" if ww_is_wolf(shown_role) else "🟢 KHÔNG PHẢI SÓI"
        await d["member"].send(f"🔮 Kết quả soi **{td['member'].display_name}**: **{result}**")
    elif action == "role":
        await d["member"].send(f"🎭 **{td['member'].display_name}** đang là **{td['role']}**.")
    elif action == "faction":
        faction = WW_V15_INFO.get(td["role"], ("🎭", "Phe Dân", ""))[1]
        await d["member"].send(f"👁️ **{td['member'].display_name}** thuộc **{faction}**.")
    elif action in {"block", "jail"}:
        await d["member"].send(f"⛓️ Đã khóa kỹ năng của **{td['member'].display_name}** trong đêm này.")
    elif action in {"protect", "heal"}:
        await d["member"].send(f"🛡️ Đã bảo vệ/chữa cho **{td['member'].display_name}** trong đêm này.")
    elif action == "kill":
        await d["member"].send(f"☠️ Đã đánh dấu **{td['member'].display_name}** để xử lý trong đêm.")
    elif action == "convert":
        await d["member"].send(f"🧿 Đã đánh dấu **{td['member'].display_name}** để chuyển hóa.")
    elif action == "mark":
        await d["member"].send(f"🐦‍⬛ Đã đánh dấu **{td['member'].display_name}**.")
    elif action == "track":
        await d["member"].send(f"🔎 Theo dõi **{td['member'].display_name}** trong đêm này.")


def _ww_v15_action_for_role(role):
    return {
        "Hiệp Sĩ Thánh": "kill", "Cô Bé Ty Ty": "inspect", "Thầy Pháp": "block",
        "Hiệp Sĩ Mù": "kill", "Kẻ Khủng Bố": "kill", "Kẻ Truy Lùng": "track",
        "Kẻ Báo Mộng": "role", "Kẻ Thấu Thị": "faction", "Nhân Chứng": "inspect",
        "Thầy Thuốc": "heal", "Người Vận Chuyển": "mark", "Quản Ngục": "jail",
        "Sói Gián Điệp": "role", "Sói Sát Thủ": "kill", "Sói Câm Lặng": "mark",
        "Sói Lửa": "kill", "Sói Sao Chép": "role", "Sói Cảm Hóa": "convert",
        "Sói Hộ Vệ": "protect", "Sói Độc": "kill", "Sói Dọn Xác": "kill",
        "Sói Thao Túng": "mark", "Sói Trắng": "kill", "Ma Cà Rồng": "convert",
        "Kẻ Giết Người Hàng Loạt": "kill", "Giáo Chủ": "convert", "Kẻ Thu Thập Linh Hồn": "mark",
        "Kẻ Đổi Màu": "faction", "Trăn Quỷ": "kill", "Kẻ Cướp Danh Tính": "role",
        "Kí Sinh Trùng": "convert", "Kẻ Thôi Miên": "block", "Ác Quỷ": "kill",
        "Ảnh Tử": "faction", "Vũ Khí Sinh Học": "mark", "Kẻ Tráo Trở": "mark", "Quạ": "mark",
        "Đạo Tặc": "role", "Thẩm Phán": "mark",
    }.get(role)

_ww_base_assign_roles_v15 = ww_assign_roles
async def ww_assign_roles(game):
    # Dùng bộ chia mới nhưng vẫn giữ toàn bộ luồng DM/khởi tạo của code cũ.
    return await _ww_base_assign_roles_v15(game)

_ww_base_start_night_v15 = start_night
async def start_night(game):
    # Các vai trò nâng cao được gửi DM song song để bot phản hồi nhanh hơn.
    tasks = []
    for uid, d in list(game.players.items()):
        if not d.get("alive"):
            continue
        action = _ww_v15_action_for_role(d.get("role"))
        if not action:
            continue
        tasks.append(_ww_dm(
            d["member"],
            f"🎭 **{d['role']}**\n{WW_V15_INFO.get(d['role'], ('🎭','Phe Dân',''))[2]}\n\nChọn mục tiêu bên dưới:",
            view=WWAdvancedTargetView(game, uid, action),
        ))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await _ww_base_start_night_v15(game)


# ---------- Passive role effects trước resolve_night ----------
_ww_base_resolve_night_v15 = resolve_night
async def resolve_night(game):
    actions = game.night_actions.get("v15", {})
    # Già Làng: tự cứu lần đầu bị Sói tấn công.
    target = game.night_actions.get("wolf_target")
    if target and game.players.get(target, {}).get("role") == "Già Làng" and not game.players[target].get("elder_used"):
        game.players[target]["elder_used"] = True
        game.night_actions["guard_target"] = target
    # Quản Ngục / Thầy Thuốc: bảo vệ thường.
    for uid, info in actions.items():
        if info.get("action") in {"jail", "heal", "protect"}:
            game.night_actions["guard_target"] = info.get("target")
    # Thầy Pháp / Kẻ Thôi Miên / Quản Ngục khóa kỹ năng.
    blocked = {info.get("target") for info in actions.values() if info.get("action") in {"block", "jail"}}
    game.night_actions["v15_blocked"] = blocked
    # Sói Lửa/Sát Thủ và Ác Quỷ/Sói Trắng có đòn xuyên bảo vệ.
    piercing = []
    for uid, info in actions.items():
        role = game.players.get(uid, {}).get("role")
        if info.get("action") == "kill" and role in {"Sói Lửa", "Sói Sát Thủ", "Ác Quỷ", "Sói Trắng"}:
            piercing.append(info.get("target"))
    game.night_actions["v15_piercing"] = piercing
    # Chuyển hóa.
    for uid, info in actions.items():
        if info.get("action") == "convert":
            target_id = info.get("target")
            if target_id in game.players and game.players[target_id].get("alive"):
                role = game.players[uid].get("role")
                if role in {"Sói Cảm Hóa"}:
                    game.players[target_id]["role"] = "Ma Sói"
                    if target_id not in game.wolves: game.wolves.append(target_id)
                elif role == "Nửa Người Nửa Sói":
                    pass
    # Bệnh sinh học đánh dấu, xử lý ở đêm sau.
    for uid, info in actions.items():
        if game.players.get(uid, {}).get("role") == "Vũ Khí Sinh Học" and info.get("target"):
            game.players[info["target"]]["bio_rounds"] = 2
    await _ww_base_resolve_night_v15(game)
    # Một số kill đặc biệt sau resolve thường.
    for uid, info in actions.items():
        role = game.players.get(uid, {}).get("role")
        target_id = info.get("target")
        if role in {"Kẻ Giết Người Hàng Loạt", "Ác Quỷ", "Sói Trắng"} and info.get("action") == "kill" and target_id in game.players:
            if game.players[target_id].get("alive"):
                game.players[target_id]["alive"] = False
                await game.ctx.send(f"☠️ **{role}** đã hạ {game.players[target_id]['member'].mention}.")
                await ww_cupid_chain(game, target_id)
    # Người Học Việc nhận Tiên Tri nếu Tiên Tri đã chết.
    if not any(d.get("role") == "Tiên Tri" and d.get("alive") for d in game.players.values()):
        for d in game.players.values():
            if d.get("role") == "Người Học Việc" and d.get("alive"):
                d["role"] = "Tiên Tri"
                try: await d["member"].send("📚 Tiên Tri đã chết — bạn trở thành **Tiên Tri**!")
                except Exception: pass


# ---------- Ban ngày: bỏ hoàn toàn manh mối khỏi thông báo ----------
_ww_base_day_discussion_v15 = start_day_discussion
async def start_day_discussion(game):
    game.state = "day"
    game.clue_count_in_round = 0
    mentions = " ".join(d["member"].mention for d in game.players.values() if d.get("alive"))
    await game.ctx.send(f"🗣️ {mentions}\n**☀️ BAN NGÀY** — 60 giây thảo luận.\n💬 Hãy thảo luận tự do.")
    await asyncio.sleep(60)
    await start_voting(game)


# ---------- Bảng vote dùng @mention và không spam xác nhận ----------
try:
    WWVoteViewV15 = WWVoteView
except Exception:
    WWVoteViewV15 = None

# ---------- Kiểm tra thắng: role Sói mới + Kẻ Chán Đời ----------
_ww_base_check_win_v15 = check_win_condition
def check_win_condition(game):
    bored = next((uid for uid,d in game.players.items() if d.get("role") == "Kẻ Chán Đời" and not d.get("alive") and d.get("death_by_vote")), None)
    if bored is not None:
        asyncio.create_task(end_game(game, f"😑 **KẺ CHÁN ĐỜI THẮNG!** {game.players[bored]['member'].mention} đã đạt mục tiêu bị treo cổ."))
        return True
    return _ww_base_check_win_v15(game)

# Đánh dấu chết do vote để Kẻ Chán Đời có thể thắng.
_ww_base_kill_vote_v15 = ww_kill_with_vote
async def ww_kill_with_vote(game, uid):
    if uid in game.players:
        game.players[uid]["death_by_vote"] = True
    await _ww_base_kill_vote_v15(game, uid)


# ---------- Sửa Lì Xì hiện tại: thay View cũ bằng View V15 ----------
# Chỉ thay class được dùng khi phát đợt mới; code cũ vẫn giữ nguyên phía trên.
LixiClaimView = LixiClaimViewV15

# ---------- Trang copy code nhanh (không cần mở editor nặng) ----------

# ================================================================
# 🔗 KẾT NỐI VỚI DASHBOARD (web.py) — stats.json / activity.json / outbox.json
# ================================================================
# Chỉ THÊM mới, không đụng tới on_message/on_ready/TOKEN... đã có phía trên.
# web.py đọc stats.json và activity.json để hiển thị số liệu thật, và ghi
# outbox.json mỗi khi bấm "Gửi tin nhắn qua bot" trên dashboard — bot.py
# đọc hàng chờ đó rồi gửi tin nhắn thật qua Discord.
try:
    import psutil
except ImportError:
    psutil = None  # chưa cài thì RAM hiện None, không làm bot lỗi

DASHBOARD_STATS_FILE = "stats.json"
DASHBOARD_ACTIVITY_FILE = "activity.json"
DASHBOARD_OUTBOX_FILE = "outbox.json"
DASHBOARD_ACTIVITY_MAX = 20
DASHBOARD_ACTIVITY_LOCK = threading.Lock()


def _dashboard_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


async def dashboard_stats_loop():
    """Ghi số server/thành viên/RAM/ping thật ra stats.json mỗi 15 giây để
    dashboard web.py hiển thị thay cho số liệu mẫu."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            guild_list = [
                {
                    "id": str(g.id),
                    "name": g.name,
                    "members": g.member_count,
                    "icon": str(g.icon.replace(size=64).url) if g.icon else None,
                    # Chỉ liệt kê kênh chữ mà bot thật sự gửi được tin, để
                    # dashboard bấm chọn kênh thay vì phải nhập ID tay.
                    "channels": [
                        {"id": str(c.id), "name": c.name}
                        for c in g.text_channels
                        if g.me is not None and c.permissions_for(g.me).send_messages
                    ][:100],
                }
                for g in sorted(bot.guilds, key=lambda g: g.member_count or 0, reverse=True)[:25]
            ]
            ram_mb = None
            if psutil is not None:
                ram_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 1)
            latency = bot.latency
            ping_ms = round(latency * 1000) if latency == latency else None  # loại NaN lúc chưa có heartbeat
            _dashboard_write_json(DASHBOARD_STATS_FILE, {
                "guilds": len(bot.guilds),
                "members": sum(g.member_count or 0 for g in bot.guilds),
                "ram_mb": ram_mb,
                "ping_ms": ping_ms,
                "updated_at": time.time(),
                "guild_list": guild_list,
                # Tên + avatar thật của bot để dashboard hiện đúng thay vì icon mẫu
                "bot_name": bot.user.name if bot.user else None,
                "bot_avatar": str(bot.user.display_avatar.replace(size=128).url) if bot.user else None,
            })
        except Exception as e:
            print(f"[Dashboard] Lỗi ghi stats.json: {e}")
        await asyncio.sleep(15)


def dashboard_log_activity(user, action):
    """Ghi 1 dòng log lệnh vừa dùng vào activity.json cho dashboard hiển thị
    ở mục 'Hoạt động gần đây'. Tự giới hạn 20 dòng gần nhất."""
    try:
        with DASHBOARD_ACTIVITY_LOCK:
            items = []
            if os.path.exists(DASHBOARD_ACTIVITY_FILE):
                try:
                    with open(DASHBOARD_ACTIVITY_FILE, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    if not isinstance(items, list):
                        items = []
                except Exception:
                    items = []
            items.append({"time": time.time(), "user": str(user), "action": str(action)})
            items = items[-DASHBOARD_ACTIVITY_MAX:]
            _dashboard_write_json(DASHBOARD_ACTIVITY_FILE, items)
    except Exception as e:
        print(f"[Dashboard] Lỗi ghi activity.json: {e}")


@bot.event
async def on_command_completion(ctx):
    """Sự kiện riêng discord.py tự gọi sau khi 1 lệnh prefix chạy xong —
    không trùng và không ghi đè on_message/on_ready đã có ở trên."""
    dashboard_log_activity(ctx.author, ctx.message.content)


def _dashboard_error_text(e):
    """Đổi lỗi gửi tin thành 1 câu tiếng Việt ngắn để dashboard hiện lý do.
    Không lưu nguyên văn lỗi vì lỗi 429 của Cloudflare là cả trang HTML dài."""
    text = str(e)
    code = getattr(e, "code", None)
    status = getattr(e, "status", None)
    if status == 429 or "cloudflare" in text.lower():
        return "Discord đang chặn/giới hạn IP của host (429), thử lại sau ít phút"
    if code == 10003:
        return "Không tìm thấy kênh: ID sai hoặc bot không ở server có kênh này"
    if code == 50001:
        return "Bot không có quyền xem kênh này (Missing Access)"
    if code == 50013:
        return "Bot thiếu quyền Gửi tin nhắn trong kênh này (Missing Permissions)"
    if code == 50035:
        return "Nội dung tin nhắn không hợp lệ (quá dài hoặc trống)"
    if isinstance(e, AttributeError) and "send" in text:
        return "ID này không phải kênh chữ (có thể là danh mục, kênh thoại hoặc ID khác)"
    if isinstance(e, ValueError):
        return "ID kênh không hợp lệ"
    return (type(e).__name__ + ": " + text).replace("\n", " ")[:160]


async def dashboard_outbox_loop():
    """Đọc outbox.json mà dashboard ghi vào mỗi khi bấm 'Gửi tin nhắn qua
    bot', gửi tin thật qua Discord rồi đánh dấu lại trạng thái sent/error."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if os.path.exists(DASHBOARD_OUTBOX_FILE):
                with open(DASHBOARD_OUTBOX_FILE, "r", encoding="utf-8") as f:
                    jobs = json.load(f)
                if isinstance(jobs, list):
                    changed = False
                    for job in jobs:
                        if job.get("status") != "pending":
                            continue
                        try:
                            cid = int(job["channel_id"])
                            channel = bot.get_channel(cid) or await bot.fetch_channel(cid)
                            await channel.send(job["content"])
                            job["status"] = "sent"
                            job.pop("error", None)
                        except Exception as e:
                            job["status"] = "error"
                            job["error"] = _dashboard_error_text(e)
                            print(f"[Dashboard] Gửi tin thất bại: {job['error']}")
                        changed = True
                    if changed:
                        _dashboard_write_json(DASHBOARD_OUTBOX_FILE, jobs)
        except Exception as e:
            print(f"[Dashboard] Lỗi xử lý outbox.json: {e}")
        await asyncio.sleep(5)


async def main():
    async with bot:
        await bot.load_extension("noitu")
        await bot.load_extension("ai")
        await bot.load_extension("lixi_admin")
        await bot.load_extension("owner_mute")
        await bot.load_extension("status") 
        await bot.load_extension("server_manager")
        await bot.load_extension("quest")
        await bot.load_extension("fun")
        await bot.load_extension("pingbot")
        await bot.load_extension("gif")
        await bot.load_extension("database")
        asyncio.create_task(dashboard_stats_loop())
        asyncio.create_task(dashboard_outbox_loop())
        try:
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            # 429 từ Cloudflare (lỗi 1015) = IP của host đang bị Discord chặn.
            # Thoát với mã 42 để keep_alive.py chờ lâu rồi mới thử lại, thay vì
            # đăng nhập dồn dập làm lệnh chặn kéo dài thêm.
            if e.status == 429:
                import sys as _sys
                print("[RateLimit] Discord/Cloudflare đang chặn IP host (429). Thoát mã 42 để chờ lâu rồi thử lại.")
                _sys.exit(42)
            raise

asyncio.run(main())
