"""
database.py — hàm DB + hằng số + helper UI dùng chung.

Tách ra khỏi bot.py để các file game (casino.py, masoi.py...) import được
mà không bị lỗi vòng import. Các hàm giữ NGUYÊN logic như bản cũ.
"""
import time
import sqlite3
import discord

DB_NAME = "ksbot_master.db"

COIN_EMOJI = "<:cat_coin:1536695423014535268>"
MAIN_COLOR = 0xb388ff

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
    return ui_embed(f"{emoji} {title}", description, UI_COLORS.get(color, UI_COLORS["primary"]))


def ui_status_line(label, seconds):
    return f"⏱️ **{label}**  ·  còn **{seconds:02d}s**"


# ==========================================
# DATABASE SQLITE
# ==========================================
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
    conn.commit()
    conn.close()


init_db()


def get_user(user_id):
    uid_str = str(user_id)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT coin, level, exp, streak, last_streak_time, history_tx FROM users WHERE user_id = ?',
        (uid_str,),
    )
    row = cursor.fetchone()
    current_time = time.time()

    if row is None:
        default_coin = 100000
        cursor.execute(
            'INSERT INTO users (user_id, coin, level, exp, streak, last_streak_time, history_tx) '
            'VALUES (?, ?, 1, 0, 1, ?, ?)',
            (uid_str, default_coin, current_time, ""),
        )
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


def add_exp_to_user(user_id, exp_gain):
    uid_str = str(user_id)
    coin, level, exp, streak, last_time, history = get_user(uid_str)
    exp += exp_gain
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
        return int(raw_val)
    return None
