import os
import re
import time
import random
import sqlite3
import asyncio
import unicodedata
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands


# ============================================================
# KS-BOT - NỐI TỪ + VUA TIẾNG VIỆT
# Giao diện chat gọn, reaction custom, kho từ nằm ngoài code + bot tự chơi.
# (2 game riêng biệt, KHÔNG liên quan tới nhau)
#
# ---------------- 1) NỐI TỪ ----------------
#   .setnoitu    -> cài kênh Nối Từ (QTV/Manage Server/Owner)
#   .startnoi    -> bắt đầu game
#   Gõ thẳng "cá heo", "heo rừng", "rừng cây xanh", ... để nối (2-3 TIẾNG)
#   .stop        -> đổi sang từ mở đầu khác, game vẫn chạy
#   .stopnoitu   -> tắt hẳn game (QTV/Manage Server/Owner)
#   .noibot on/off -> bật/tắt bot tự chơi cùng
#   .noitu       -> xem từ hiện tại
#   .me [@user]  -> hồ sơ Nối Từ
#   .rank        -> BXH Nối Từ
#   .gold        -> xem coin
#
# ---------------- 2) VUA TIẾNG VIỆT (kiểu Neko) ----------------
#   .vtvset      -> cài kênh (QTV/Manage Server/Owner)
#   .vtvstart    -> bắt đầu game
#   .vtvstop     -> tắt hẳn game
#   .vtv         -> xem lại câu đố hiện tại
#
# ---------------- QUẢN LÝ KHO TỪ (dùng chung 2 game) ----------------
#   .ntsync      -> tải lại kho từ online (GitHub + 2 file riêng)
#   .ntload      -> nạp lại file trong thư mục word_data/
#   .ntdel <cụm> -> xóa 1 cụm khỏi kho
#   .ntstats     -> xem tổng quan kho từ
#
# NGUỒN TỪ:
#   1) Kho online lớn (Viet74K, Viet39K, ...)    -> EXTERNAL_WORD_SOURCES
#   2) 2 file riêng trên GitHub của chủ bot      -> MY_WORD_FILES
#   3) Thêm link tùy ý qua biến môi trường       -> NOITU_EXTRA_URLS (cách nhau dấu phẩy)
#   4) File .txt/.csv/.tsv đặt trong word_data/  -> .ntload
#
# requirements:
#   discord.py, aiohttp
#
# DB:
#   NOITU_DB  -> database từ nối (dùng chung cho cả 2 game)
#   KSBOT_DB  -> database coin hiện có của KS-BOT (đồng bộ với bot.py)
# ============================================================


OWNER_ID = 1404399385965498479  # đồng bộ với OWNER_USER_ID trong bot.py

WORDS_DB = os.getenv("NOITU_DB", "noitu_words.db")
COIN_DB = os.getenv("KSBOT_DB", "ksbot_master.db")

VALID_CHARS = re.compile(r"^[a-zà-ỹđ0-9\s\-]+$", re.IGNORECASE)

# Nối Từ chấp nhận cụm 2 hoặc 3 tiếng.
ALLOWED_PHRASE_COUNTS = (2, 3)

# ---- NỐI TỪ ----
PER_VALID_REWARD = 1000      # thưởng mỗi lần nối đúng
WIN_REWARD = 5000            # thưởng khi thắng ván
ERROR_DELETE_AFTER = 4       # BOT báo lỗi xong tự xóa sau số giây này

# ---- VUA TIẾNG VIỆT ----
VTV_REWARD = 500             # thưởng mỗi lần đoán đúng
VTV_ROUND_SECONDS = 90       # số giây mỗi câu đố
VTV_MAX_FAIL_STREAK = 5      # dừng game sau số lượt liên tiếp không ai đoán đúng

# Reaction Nối Từ: custom emoji Discord.
RIGHT_REACTION = discord.PartialEmoji(name="3_", id=1548249974611910687, animated=True)
WRONG_REACTION = discord.PartialEmoji(name="nope", id=1548520326009327616, animated=True)

# Thư mục chứa file từ riêng (cạnh file cog), bot tự tạo nếu chưa có.
BASE_DIR = Path(__file__).resolve().parent
WORD_DATA_DIR = Path(os.getenv("NOITU_WORD_DATA_DIR", str(BASE_DIR / "word_data")))
AUTO_BOT_DELAY = float(os.getenv("NOITU_BOT_DELAY", "0.2"))  # bot nối gần như tức thì

# Chỉ dùng nếu repo GitHub của bạn để PRIVATE (Personal Access Token, quyền Contents: Read).
# Đặt qua biến môi trường, KHÔNG dán thẳng vào code.
GITHUB_TOKEN = os.getenv("NOITU_GITHUB_TOKEN", "")


# ============================================================
# NGUỒN TỪ ONLINE
# Mỗi nguồn có nhiều link dự phòng, thử lần lượt tới khi tải được.
# ============================================================

EXTERNAL_WORD_SOURCES = [
    (
        "duyet:Viet74K",
        [
            "https://vietnamese-wordlist.duyet.net/Viet74K.txt",
            "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/refs/heads/master/Viet74K.txt",
        ],
    ),
    (
        "duyet:Viet39K",
        [
            "https://vietnamese-wordlist.duyet.net/Viet39K.txt",
            "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/refs/heads/master/Viet39K.txt",
        ],
    ),
    (
        "lingskr:vi_dict",
        [
            "https://raw.githubusercontent.com/duyet/vietnamese-wordlist/refs/heads/master/Viet22K.txt",
        ],
    ),
]

# 2 file riêng của chủ bot trên GitHub.
# Link đầu là link "sạch" (dùng được khi repo public hoặc có NOITU_GITHUB_TOKEN).
# Link thứ 2 là link có ?token=... do GitHub cấp tạm thời (hết hạn sau một thời gian,
# hết hạn thì bot tự bỏ qua; khi đó hãy để repo public hoặc dùng NOITU_GITHUB_TOKEN).
MY_WORD_FILES = [
    (
        "mine:tuthuong_luutru",
        [
            "https://raw.githubusercontent.com/ducevo8-ship-it/ahhhhhhhhhhhhhhhhhhhhhhhhhhhhahahahahahajha/refs/heads/main/tuthuong_luutru.txt"
        ],
    ),
    (
        "mine:tubi_luutru",
        [
            "https://raw.githubusercontent.com/ducevo8-ship-it/ahhhhhhhhhhhhhhhhhhhhhhhhhhhhahahahahahajha/refs/heads/main/tubi_luutru.txt"
        ],
    ),
]

EXTERNAL_SYNC_TIMEOUT = 90


def all_word_sources():
    """Gộp kho lớn + file riêng + link thêm từ biến môi trường."""
    sources = list(EXTERNAL_WORD_SOURCES) + list(MY_WORD_FILES)

    for url in os.getenv("NOITU_EXTRA_URLS", "").split(","):
        url = url.strip()
        if url:
            sources.append((f"url:{url.rsplit('/', 1)[-1].split('?')[0]}", [url]))

    return sources


# ============================================================
# TEXT / NORMALIZE
# ============================================================

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def last_syllable(text: str) -> str:
    parts = normalize_text(text).split()
    return parts[-1] if parts else ""


def first_syllable(text: str) -> str:
    parts = normalize_text(text).split()
    return parts[0] if parts else ""


def parse_phrase(line: str):
    """Trả về (phrase, parts) nếu dòng là cụm 2-3 tiếng hợp lệ, không thì None."""
    text = (line or "").replace("\ufeff", "")
    text = text.split("\t", 1)[0].split(",", 1)[0].split(";", 1)[0]
    text = text.replace("_", " ").strip().strip('"')

    phrase = normalize_text(text)
    parts = phrase.split()

    if len(parts) not in ALLOWED_PHRASE_COUNTS:
        return None

    if len(phrase) > 100 or not VALID_CHARS.fullmatch(phrase):
        return None

    return phrase, parts


# ============================================================
# WORD DATABASE
# ============================================================

def open_words_db():
    conn = sqlite3.connect(WORDS_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_words_db():
    conn = open_words_db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS noitu_words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT NOT NULL,
        normalized TEXT NOT NULL UNIQUE,
        first_word TEXT NOT NULL,
        last_word TEXT NOT NULL,
        word_count INTEGER NOT NULL DEFAULT 1,
        source TEXT DEFAULT 'dataset',
        added_by TEXT,
        added_at REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_noitu_first
        ON noitu_words(first_word);

    CREATE INDEX IF NOT EXISTS idx_noitu_last
        ON noitu_words(last_word);

    CREATE INDEX IF NOT EXISTS idx_noitu_normalized
        ON noitu_words(normalized);

    CREATE TABLE IF NOT EXISTS noitu_settings (
        guild_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS vtv_settings (
        guild_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS noitu_stats (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        wins INTEGER NOT NULL DEFAULT 0,
        games INTEGER NOT NULL DEFAULT 0,
        correct INTEGER NOT NULL DEFAULT 0,
        wrong INTEGER NOT NULL DEFAULT 0,
        earned INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );
    """)

    conn.commit()
    conn.close()


init_words_db()


INSERT_SQL = """
    INSERT OR IGNORE INTO noitu_words
    (phrase, normalized, first_word, last_word, word_count, source, added_by, added_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def insert_rows(rows) -> int:
    """Chèn hàng loạt, trả về số dòng thực sự được thêm (bỏ qua trùng)."""
    if not rows:
        return 0

    conn = open_words_db()
    try:
        before = conn.total_changes
        conn.executemany(INSERT_SQL, rows)
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def load_external_word_files() -> dict:
    """
    Nạp mọi file .txt/.csv/.tsv trong word_data/.
    Trả về {tên_file: (số dòng, thêm mới, bỏ qua)}.
    """
    report = {}

    try:
        WORD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return report

    for path in sorted(WORD_DATA_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".csv", ".tsv"}:
            continue

        try:
            raw = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue

        lines = raw.splitlines()
        rows = []
        skipped = 0

        for line in lines:
            parsed = parse_phrase(line)

            if not parsed:
                skipped += 1
                continue

            phrase, parts = parsed
            rows.append((
                phrase, phrase, parts[0], parts[-1], len(parts),
                f"external:{path.name}", "external", time.time(),
            ))

        report[path.name] = (len(lines), insert_rows(rows), skipped)

    return report


async def sync_external_word_bank() -> dict:
    """
    Tải tất cả nguồn online. Trả về {tên_nguồn: số cụm thêm mới, hoặc None nếu lỗi tải}.
    """
    report = {}
    timeout = aiohttp.ClientTimeout(total=EXTERNAL_SYNC_TIMEOUT)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, urls in all_word_sources():
            raw = None

            for url in urls:
                headers = {}

                # Chỉ gửi token cho đúng host GitHub raw.
                if GITHUB_TOKEN and "raw.githubusercontent.com" in url:
                    headers["Authorization"] = f"token {GITHUB_TOKEN}"

                try:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status != 200:
                            print(f"[NoiTu] {name}: HTTP {resp.status}")
                            continue

                        raw = await resp.text(encoding="utf-8", errors="ignore")
                        break

                except Exception as e:
                    print(f"[NoiTu] {name}: lỗi tải: {e}")

            if raw is None:
                report[name] = None
                continue

            rows = []

            for line in raw.splitlines():
                parsed = parse_phrase(line)

                if not parsed:
                    continue

                phrase, parts = parsed
                rows.append((
                    phrase, phrase, parts[0], parts[-1], len(parts),
                    name, "github", time.time(),
                ))

            # chạy trong thread để không làm đứng bot
            report[name] = await asyncio.to_thread(insert_rows, rows)
            print(f"[NoiTu] {name}: +{report[name]:,} cụm")

    return report


load_external_word_files()


def configured_channel(guild_id: int):
    conn = open_words_db()

    row = conn.execute(
        "SELECT channel_id FROM noitu_settings WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()

    conn.close()

    return int(row[0]) if row else None


def set_configured_channel(guild_id: int, channel_id: int):
    conn = open_words_db()

    conn.execute("""
        INSERT INTO noitu_settings(guild_id, channel_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET channel_id = excluded.channel_id
    """, (str(guild_id), str(channel_id)))

    conn.commit()
    conn.close()


def vtv_configured_channel(guild_id: int):
    conn = open_words_db()

    row = conn.execute(
        "SELECT channel_id FROM vtv_settings WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchone()

    conn.close()

    return int(row[0]) if row else None


def set_vtv_channel(guild_id: int, channel_id: int):
    conn = open_words_db()

    conn.execute("""
        INSERT INTO vtv_settings(guild_id, channel_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET channel_id = excluded.channel_id
    """, (str(guild_id), str(channel_id)))

    conn.commit()
    conn.close()


def delete_phrase(phrase: str) -> bool:
    conn = open_words_db()

    cur = conn.execute(
        "DELETE FROM noitu_words WHERE normalized = ?",
        (normalize_text(phrase),),
    )

    conn.commit()
    conn.close()

    return cur.rowcount > 0


def count_words():
    conn = open_words_db()

    total = conn.execute(
        "SELECT COUNT(*) FROM noitu_words"
    ).fetchone()[0]

    playable = conn.execute(
        "SELECT COUNT(*) FROM noitu_words WHERE word_count IN (2,3)"
    ).fetchone()[0]

    conn.close()

    return total, playable


def random_start_phrase():
    conn = open_words_db()

    row = conn.execute(
        """
        SELECT phrase
        FROM noitu_words
        WHERE word_count IN (2,3)
        ORDER BY RANDOM()
        LIMIT 1
        """,
    ).fetchone()

    conn.close()

    return row[0] if row else None


def find_phrase(required_start: str, submitted: str):
    """
    Chỉ chấp nhận cụm 2-3 tiếng tồn tại trong kho từ
    và tiếng đầu tiên phải đúng tiếng cần nối.
    """

    conn = open_words_db()

    row = conn.execute(
        """
        SELECT phrase
        FROM noitu_words
        WHERE normalized = ?
          AND first_word = ?
          AND word_count IN (2,3)
        LIMIT 1
        """,
        (
            normalize_text(submitted),
            normalize_text(required_start),
        ),
    ).fetchone()

    conn.close()

    return row[0] if row else None


def candidate_phrases(required_start: str, used: set[str], limit: int = 500):
    """Lấy các cụm hợp lệ cho tiếng bắt buộc, dùng cho bot."""
    conn = open_words_db()
    rows = conn.execute("""
        SELECT normalized, phrase
        FROM noitu_words
        WHERE first_word = ?
          AND word_count IN (2,3)
        LIMIT ?
    """, (
        normalize_text(required_start),
        int(limit),
    )).fetchall()
    conn.close()

    return [
        phrase for normalized, phrase in rows
        if normalized not in used
    ]


def has_candidate(required_start: str, used: set[str]):
    """Còn ít nhất 1 cụm chưa dùng bắt đầu bằng tiếng required_start hay không."""

    conn = open_words_db()

    rows = conn.execute(
        """
        SELECT normalized
        FROM noitu_words
        WHERE first_word = ?
          AND word_count IN (2,3)
        """,
        (normalize_text(required_start),),
    ).fetchall()

    conn.close()

    return any(row[0] not in used for row in rows)


def random_unused_start(used: set[str]):
    conn = open_words_db()

    rows = conn.execute(
        """
        SELECT normalized, phrase
        FROM noitu_words
        WHERE word_count IN (2,3)
        ORDER BY RANDOM()
        LIMIT 200
        """,
    ).fetchall()

    conn.close()

    for normalized, phrase in rows:
        if normalized not in used:
            return phrase

    return random_start_phrase()


def smart_pick_start(used: set[str] | None = None, attempts: int = 5):
    """
    Chọn từ mở đầu "thông minh": ưu tiên cụm mà sau khi chọn vẫn còn
    ít nhất 1 hướng để người chơi nối tiếp.
    """

    used = used or set()
    fallback = None

    for _ in range(attempts):
        candidate = (
            random_unused_start(used) if used else random_start_phrase()
        )

        if not candidate:
            break

        fallback = candidate

        check_used = used | {normalize_text(candidate)}
        if has_candidate(last_syllable(candidate), check_used):
            return candidate

    return fallback


# ============================================================
# STATS
# ============================================================

def add_stats(guild_id, user_id, **delta):
    allowed = {"wins", "games", "correct", "wrong", "earned"}

    delta = {k: int(v) for k, v in delta.items() if k in allowed}

    if not delta:
        return

    cols = ["wins", "games", "correct", "wrong", "earned"]
    vals = [delta.get(c, 0) for c in cols]

    conn = open_words_db()

    conn.execute(
        """
        INSERT INTO noitu_stats
        (guild_id, user_id, wins, games, correct, wrong, earned)
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET
            wins = wins + excluded.wins,
            games = games + excluded.games,
            correct = correct + excluded.correct,
            wrong = wrong + excluded.wrong,
            earned = earned + excluded.earned
        """,
        (str(guild_id), str(user_id), *vals),
    )

    conn.commit()
    conn.close()


def get_stats(guild_id, user_id):
    conn = open_words_db()

    row = conn.execute(
        """
        SELECT wins, games, correct, wrong, earned
        FROM noitu_stats
        WHERE guild_id = ? AND user_id = ?
        """,
        (str(guild_id), str(user_id)),
    ).fetchone()

    conn.close()

    return row or (0, 0, 0, 0, 0)


def get_rank(guild_id, limit=10):
    conn = open_words_db()

    rows = conn.execute(
        """
        SELECT user_id, wins, games, correct, wrong, earned
        FROM noitu_stats
        WHERE guild_id = ?
        ORDER BY wins DESC, earned DESC, correct DESC
        LIMIT ?
        """,
        (str(guild_id), int(limit)),
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# COIN (đồng bộ đúng schema bảng `users` trong bot.py)
# ============================================================

def get_coin(user_id):
    conn = sqlite3.connect(COIN_DB, timeout=10)

    row = conn.execute(
        "SELECT coin FROM users WHERE user_id = ?",
        (str(user_id),),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO users
            (user_id, coin, level, exp, streak, last_streak_time, history_tx)
            VALUES (?, 100000, 1, 0, 1, ?, '')
            """,
            (str(user_id), time.time()),
        )

        conn.commit()
        coin = 100000

    else:
        coin = int(row[0])

    conn.close()

    return coin


def add_coin(user_id, amount):
    amount = int(amount)

    conn = sqlite3.connect(COIN_DB, timeout=10)

    conn.execute("BEGIN IMMEDIATE")

    row = conn.execute(
        "SELECT coin FROM users WHERE user_id = ?",
        (str(user_id),),
    ).fetchone()

    if row is None:
        new_coin = 100000 + amount

        conn.execute(
            """
            INSERT INTO users
            (user_id, coin, level, exp, streak, last_streak_time, history_tx)
            VALUES (?, ?, 1, 0, 1, ?, '')
            """,
            (str(user_id), new_coin, time.time()),
        )

    else:
        new_coin = int(row[0]) + amount

        conn.execute(
            "UPDATE users SET coin = ? WHERE user_id = ?",
            (new_coin, str(user_id)),
        )

    conn.commit()
    conn.close()

    return new_coin


# ============================================================
# GAME STATE
# ============================================================

class NoituGame:
    def __init__(self, guild_id, channel_id):
        self.guild_id = guild_id
        self.channel_id = channel_id

        self.current_phrase = None
        self.required_start = None

        self.used = set()

        self.running = False

        # Bot tự chơi (.noibot on/off)
        self.auto_bot = False

        # Người vừa nối thành công. Không được nối tiếp ngay lượt kế.
        self.last_user_id = None

        self.lock = asyncio.Lock()

    def set_phrase(self, phrase):
        self.current_phrase = normalize_text(phrase)
        self.required_start = last_syllable(phrase)


class VuaTVGame:
    """Trạng thái 1 ván Vua Tiếng Việt. Mỗi (guild, channel) chỉ có 1 ván."""

    def __init__(self, guild_id, channel_id, channel):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.channel = channel

        self.running = False

        self.answer = None
        self.answer_display = None
        self.scrambled = None

        # Tăng mỗi khi mở câu đố mới, để phân biệt câu đố cũ/mới.
        self.round_token = 0

        # Số lượt LIÊN TIẾP không ai đoán đúng.
        self.fail_streak = 0

        self.expire_task = None

        self.lock = asyncio.Lock()


# ============================================================
# COG
# ============================================================

class NoituCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.games = {}
        self.vtv_games = {}
        self._lock = asyncio.Lock()
        self.auto_tasks = {}
        self.word_sync_task = asyncio.create_task(self._sync_words_background())

    def cog_unload(self):
        for game in self.vtv_games.values():
            if game.expire_task:
                game.expire_task.cancel()

        for task in self.auto_tasks.values():
            task.cancel()

        if getattr(self, "word_sync_task", None):
            self.word_sync_task.cancel()

    # --------------------------------------------------------
    # ĐỒNG BỘ KHO TỪ NỀN
    # --------------------------------------------------------

    async def _sync_words_background(self):
        await asyncio.sleep(2)

        try:
            report = await sync_external_word_bank()
            total = sum(n for n in report.values() if n)
            failed = [name for name, n in report.items() if n is None]

            if total:
                print(f"[NoiTu] Đã nạp thêm {total:,} cụm từ online.")
            if failed:
                print(f"[NoiTu] Nguồn lỗi tải: {', '.join(failed)}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[NoiTu] Đồng bộ kho từ lỗi: {e}")

    # --------------------------------------------------------
    # PERMISSION
    # --------------------------------------------------------

    def is_owner(self, ctx):
        return ctx.author.id == OWNER_ID

    def can_manage(self, ctx):
        return (
            self.is_owner(ctx)
            or ctx.author.guild_permissions.manage_guild
        )

    async def require_manage(self, ctx):
        if self.can_manage(ctx):
            return True

        await ctx.send(
            "❌ Bạn không có quyền quản trị Nối Từ.",
            delete_after=5,
        )

        return False

    # --------------------------------------------------------
    # CHANNEL
    # --------------------------------------------------------

    async def configured(self, ctx):
        if not ctx.guild:
            return False

        channel_id = configured_channel(ctx.guild.id)

        if channel_id != ctx.channel.id:

            if channel_id:
                await ctx.send(
                    f"❌ Kênh Nối Từ hiện tại là <#{channel_id}>.",
                    delete_after=6,
                )
            else:
                await ctx.send(
                    "❌ Server chưa cài kênh Nối Từ. "
                    "Dùng `.setnoitu`.",
                    delete_after=6,
                )

            return False

        return True

    # --------------------------------------------------------
    # BOT NOTICE
    # --------------------------------------------------------

    async def send_notice(
        self,
        channel,
        text,
        delete_after=ERROR_DELETE_AFTER,
    ):
        """Gửi thông báo nhỏ rồi tự xóa. Không spam chat lâu."""

        try:
            return await channel.send(text, delete_after=delete_after)
        except discord.HTTPException:
            return None

    async def wrong(self, message, text):
        """
        Nối sai: thả ❌ vào tin nhắn người chơi, BOT báo lý do,
        thông báo tự xóa sau vài giây.
        """

        try:
            await message.add_reaction(WRONG_REACTION)
        except discord.HTTPException:
            pass

        await self.send_notice(
            message.channel,
            text,
            ERROR_DELETE_AFTER,
        )

    # --------------------------------------------------------
    # TIN NHẮN THẮNG VÁN (kiểu Neko: gọn, 1 khối, dễ đọc)
    # --------------------------------------------------------

    def win_text(self, winner=None, reward=0, new_phrase=None, required=None):
        """
        Tạo tin nhắn thắng ván gọn gàng.
        - winner: member thắng (None nếu bot thắng)
        - reward: số coin thưởng
        - new_phrase: từ mở đầu lượt mới (None nếu kho hết sạch)
        """

        if winner is None:
            head = "🏆 Không còn từ để nối tiếp. **KS-BOT** thắng!"
        else:
            head = (
                "🏆 Không còn từ để nối tiếp. "
                f"{winner.mention} thắng và nhận **{reward:,}** 🪙!"
            )

        if new_phrase:
            return (
                f"{head}\n"
                f"🔄 Lượt mới bắt đầu với từ: **{new_phrase}** "
                f"→ nối bằng **{(required or last_syllable(new_phrase)).upper()}**"
            )

        return (
            f"{head}\n"
            "📕 Kho từ đã hết sạch, Nối Từ tự dừng."
        )

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    @commands.hybrid_command(name="setnoitu")
    @commands.guild_only()
    async def setnoitu(self, ctx):

        if not await self.require_manage(ctx):
            return

        set_configured_channel(ctx.guild.id, ctx.channel.id)

        await ctx.send(
            f"✅ Đã đặt {ctx.channel.mention} "
            f"làm **kênh Nối Từ**.\n"
            f"🎮 Dùng `.startnoi` để bắt đầu."
        )

    @commands.hybrid_command(name="startnoi", aliases=["startnoitu"])
    @commands.guild_only()
    async def startnoi(self, ctx):

        if not await self.configured(ctx):
            return

        key = (ctx.guild.id, ctx.channel.id)

        async with self._lock:

            game = self.games.get(key)

            if game and game.running:
                await self.send_notice(
                    ctx.channel,
                    "❌ Nối Từ đang chạy rồi.",
                    4,
                )
                return

            phrase = smart_pick_start()

            if not phrase:
                await ctx.send(
                    "❌ Kho từ đang trống.\n"
                    "Dùng `.ntsync` để tải kho online, hoặc bỏ file .txt "
                    "vào thư mục `word_data/` rồi dùng `.ntload`."
                )
                return

            game = NoituGame(*key)

            game.running = True
            game.set_phrase(phrase)
            game.used.add(normalize_text(phrase))

            self.games[key] = game

        await ctx.send(
            "🎮 **NỐI TỪ**\n"
            f"**{game.current_phrase}** → nối bằng **{game.required_start}**"
        )

    # --------------------------------------------------------
    # STOP / CHANGE START WORD
    # --------------------------------------------------------

    @commands.hybrid_command(name="stop")
    @commands.guild_only()
    async def stop(self, ctx):

        if not await self.configured(ctx):
            return

        key = (ctx.guild.id, ctx.channel.id)

        game = self.games.get(key)

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Nối Từ chưa chạy.",
                4,
            )
            return

        async with game.lock:

            new_phrase = smart_pick_start(game.used)

            if not new_phrase:
                await self.send_notice(
                    ctx.channel,
                    "❌ Kho từ đang trống.",
                    4,
                )
                return

            game.set_phrase(new_phrase)
            game.used.add(normalize_text(new_phrase))
            game.last_user_id = None

        await ctx.send(
            f"🔄 **{game.current_phrase}** → nối bằng **{game.required_start}**"
        )

    @commands.hybrid_command(name="noibot")
    @commands.guild_only()
    async def noibot(self, ctx, mode: str = None):
        """Bật/tắt bot tự tham gia Nối Từ."""
        if not await self.configured(ctx):
            return

        key = (ctx.guild.id, ctx.channel.id)
        game = self.games.get(key)

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Chưa có ván Nối Từ. Dùng `.startnoi` trước.",
                4,
            )
            return

        value = normalize_text(mode or "on")

        if value in {"off", "0", "false", "tat", "tắt"}:
            game.auto_bot = False
            task = self.auto_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            await ctx.send("🤖 Bot tự chơi: **TẮT**")
            return

        if value not in {"on", "1", "true", "bat", "bật"}:
            await self.send_notice(
                ctx.channel,
                "Dùng: `.noibot on` hoặc `.noibot off`",
                5,
            )
            return

        game.auto_bot = True
        await ctx.send(
            "🤖 Bot tự chơi: **BẬT**\n"
            "Bot sẽ nối từ như người chơi. Bot chỉ lên tiếng khi bị bí."
        )

        # Nếu đang tới lượt "người khác" và bot chưa nối, cho bot nối luôn.
        if game.last_user_id is not None and game.last_user_id != (
            self.bot.user.id if self.bot.user else 0
        ):
            self.schedule_bot_turn(game)

    # --------------------------------------------------------
    # STOP GAME
    # --------------------------------------------------------

    @commands.hybrid_command(name="stopnoitu")
    @commands.guild_only()
    async def stopnoitu(self, ctx):

        if not await self.configured(ctx):
            return

        if not await self.require_manage(ctx):
            return

        key = (ctx.guild.id, ctx.channel.id)

        game = self.games.pop(key, None)

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Hiện không có ván Nối Từ.",
                4,
            )
            return

        game.running = False

        task = self.auto_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

        await ctx.send(
            "🛑 **Đã tắt Nối Từ.**\n"
            "Dùng `.startnoi` để bật lại."
        )

    # ========================================================
    # AUTO BOT - chơi cùng người như một người chơi thật
    # ========================================================

    def choose_bot_phrase(self, game: "NoituGame"):
        options = candidate_phrases(game.required_start, game.used, 500)

        if not options:
            return None

        # Ưu tiên nước đi không làm bot tự bí ngay sau đó.
        safe = []
        for phrase in options:
            next_used = game.used | {normalize_text(phrase)}
            if has_candidate(last_syllable(phrase), next_used):
                safe.append(phrase)

        pool = safe or options
        return random.choice(pool)

    async def bot_take_turn(self, game: "NoituGame"):
        """Bot tự gửi một cụm và xử lý như một lượt chơi."""
        key = (game.guild_id, game.channel_id)

        try:
            await asyncio.sleep(AUTO_BOT_DELAY)

            async with game.lock:
                if not game.running or not game.auto_bot:
                    return

                channel = self.bot.get_channel(game.channel_id)
                if channel is None:
                    return

                phrase = self.choose_bot_phrase(game)

                if not phrase:
                    # Chỉ khi bí mới thông báo; không spam khi đang chơi bình thường.
                    await channel.send(
                        "🤖 **Bot bí rồi!** Không tìm được từ để nối.\n"
                        "Bạn có thể tiếp tục hoặc dùng `.stop` để đổi từ."
                    )
                    return

                # Bot nối kiểu Neko: có icon + tên bot + cụm từ in đậm cho dễ nhìn.
                await channel.send(f"🤖 KS-BOT nối: **{phrase}**")

                game.used.add(normalize_text(phrase))
                game.set_phrase(phrase)

                # Dùng bot user id để giữ luật "không 2 lượt cùng người".
                game.last_user_id = self.bot.user.id if self.bot.user else 0

                # Bot nối xong mà người chơi hết đường -> bot thắng, mở lượt mới.
                if not has_candidate(game.required_start, game.used):
                    new_phrase = smart_pick_start(game.used)

                    if new_phrase:
                        game.set_phrase(new_phrase)
                        game.used = {normalize_text(new_phrase)}
                        game.last_user_id = None
                    else:
                        game.running = False

                    await channel.send(
                        self.win_text(
                            None,
                            0,
                            new_phrase,
                            game.required_start,
                        )
                    )

        except asyncio.CancelledError:
            raise
        finally:
            # Bot vừa xong -> chờ người chơi, KHÔNG tự đánh tiếp.
            if self.auto_tasks.get(key) is asyncio.current_task():
                self.auto_tasks.pop(key, None)

    def schedule_bot_turn(self, game: "NoituGame"):
        key = (game.guild_id, game.channel_id)

        old = self.auto_tasks.get(key)
        if old and not old.done():
            old.cancel()

        self.auto_tasks[key] = asyncio.create_task(self.bot_take_turn(game))

    # ========================================================
    # MAIN GAME
    # ========================================================

    @commands.Cog.listener()
    async def on_message(self, message):

        # Bỏ qua bot / DM
        if message.author.bot:
            return

        if not message.guild:
            return

        key = (message.guild.id, message.channel.id)

        game = self.games.get(key)

        if not game or not game.running:
            return

        # Các command bắt đầu bằng . không tính là lượt chơi.
        if message.content.startswith("."):
            return

        submitted = normalize_text(message.content)
        parts = submitted.split()

        # CHỈ xử lý câu 2-3 tiếng. Chat bình thường (1 tiếng
        # hoặc 4+ tiếng) bị lờ đi hoàn toàn, không tính là sai.
        if len(parts) not in ALLOWED_PHRASE_COUNTS:
            return

        if len(submitted) > 100:
            return

        if not VALID_CHARS.fullmatch(submitted):
            return

        async with game.lock:

            if not game.running:
                return

            required = game.required_start

            # ------------------------------------------------
            # KHÔNG ĐƯỢC 2 LƯỢT LIÊN TIẾP
            # ------------------------------------------------

            if game.last_user_id == message.author.id:

                add_stats(message.guild.id, message.author.id, wrong=1)

                await self.wrong(
                    message,
                    "⛔ Bạn vừa nối rồi, chờ người khác nối tiếp.",
                )

                return

            # ------------------------------------------------
            # KIỂM TRA TIẾNG ĐẦU
            # ------------------------------------------------

            submitted_first = first_syllable(submitted)

            if submitted_first != required:

                add_stats(message.guild.id, message.author.id, wrong=1)

                await self.wrong(
                    message,
                    f"❌ Sai từ đầu! Từ này không bắt đầu bằng "
                    f"tiếng **{required.upper()}**.",
                )

                return

            # ------------------------------------------------
            # ĐÃ DÙNG
            # ------------------------------------------------

            if submitted in game.used:

                add_stats(message.guild.id, message.author.id, wrong=1)

                await self.wrong(message, "❌ Từ này đã được dùng rồi.")

                return

            # ------------------------------------------------
            # KIỂM TRA KHO TỪ
            # ------------------------------------------------

            phrase = find_phrase(required, submitted)

            if not phrase:

                add_stats(message.guild.id, message.author.id, wrong=1)

                await self.wrong(
                    message,
                    f"❌ Không có từ **{submitted}** trong kho.",
                )

                return

            # =================================================
            # NỐI ĐÚNG
            # =================================================

            game.used.add(submitted)
            game.set_phrase(phrase)
            game.last_user_id = message.author.id

            add_coin(message.author.id, PER_VALID_REWARD)

            add_stats(
                message.guild.id,
                message.author.id,
                correct=1,
                earned=PER_VALID_REWARD,
            )

            # CHỈ REACTION, KHÔNG BOT NHẮN "NỐI BẰNG..."
            try:
                await message.add_reaction(RIGHT_REACTION)
            except discord.HTTPException:
                pass

            # =================================================
            # HẾT ĐƯỜNG NỐI -> THẮNG VÁN
            # =================================================

            if not has_candidate(game.required_start, game.used):

                add_coin(message.author.id, WIN_REWARD)

                add_stats(
                    message.guild.id,
                    message.author.id,
                    wins=1,
                    games=1,
                    earned=WIN_REWARD,
                )

                new_phrase = smart_pick_start(game.used)

                if new_phrase:

                    game.set_phrase(new_phrase)

                    # Lượt mới có bộ used mới.
                    game.used = {normalize_text(new_phrase)}

                    # Người thắng không bị khóa ở lượt mới.
                    game.last_user_id = None

                else:

                    game.running = False

                # Tin thắng ván gọn kiểu Neko (1 khối, dễ nhìn).
                await message.channel.send(
                    self.win_text(
                        message.author,
                        WIN_REWARD,
                        new_phrase,
                        game.required_start,
                    )
                )

            elif game.auto_bot:
                # Còn đường nối và bot đang bật -> tới lượt bot.
                self.schedule_bot_turn(game)

    # ========================================================
    # VUA TIẾNG VIỆT (kiểu Neko) - HELPER
    # ========================================================

    async def vtv_new_round(self, game: "VuaTVGame"):
        """Mở 1 câu đố mới: chọn cụm từ, xáo ký tự, đăng lên kênh, hẹn giờ hết hạn."""

        phrase = random_start_phrase()

        if not phrase:
            game.running = False

            await game.channel.send(
                "❌ Kho từ đang trống, không thể chơi Vua Tiếng Việt.\n"
                "Dùng `.ntsync` để tải kho online, hoặc bỏ file .txt "
                "vào thư mục `word_data/` rồi dùng `.ntload`."
            )
            return

        game.answer = normalize_text(phrase)
        game.answer_display = normalize_text(phrase)

        letters = list(game.answer.replace(" ", ""))
        random.shuffle(letters)

        game.scrambled = "/".join(letters)

        game.round_token += 1
        token = game.round_token

        embed = discord.Embed(
            title="✨ Vua Tiếng Việt",
            description=(
                f"Từ cần đoán: **{game.scrambled}** "
                f"(gồm {len(letters)} ký tự).\n"
                f"Kết thúc: "
                f"<t:{int(time.time() + VTV_ROUND_SECONDS)}:R>"
            ),
            color=discord.Color.purple(),
        )

        embed.timestamp = discord.utils.utcnow()

        await game.channel.send(embed=embed)

        # Không tự hủy chính task đang chạy (khi được gọi từ vtv_expire_round).
        if game.expire_task and game.expire_task is not asyncio.current_task():
            game.expire_task.cancel()

        game.expire_task = asyncio.create_task(
            self.vtv_expire_round(game, token)
        )

    async def vtv_expire_round(self, game: "VuaTVGame", token: int):
        try:
            await asyncio.sleep(VTV_ROUND_SECONDS)
        except asyncio.CancelledError:
            return

        async with game.lock:

            if not game.running:
                return

            # Câu đố này đã được trả lời / thay thế trước khi hết giờ.
            if game.round_token != token:
                return

            game.fail_streak += 1

            if game.fail_streak >= VTV_MAX_FAIL_STREAK:
                game.running = False

                await game.channel.send(
                    f"😥 Không ai đoán đúng từ là "
                    f"**{game.answer_display}**.\n"
                    f"🛑 Đã dừng **Vua Tiếng Việt** sau "
                    f"{VTV_MAX_FAIL_STREAK} lượt liên tiếp không ai "
                    "đoán đúng.\nDùng `.vtvstart` để chơi lại."
                )
                return

            await game.channel.send(
                f"😥 Không ai đoán đúng từ là "
                f"**{game.answer_display}**."
            )

            await self.vtv_new_round(game)

    # --------------------------------------------------------
    # VUA TIẾNG VIỆT - LẮNG NGHE ĐOÁN TỪ
    # --------------------------------------------------------

    @commands.Cog.listener(name="on_message")
    async def on_message_vtv(self, message):

        if message.author.bot:
            return

        if not message.guild:
            return

        if message.content.startswith("."):
            return

        key = (message.guild.id, message.channel.id)

        game = self.vtv_games.get(key)

        if not game or not game.running:
            return

        guess = normalize_text(message.content)

        if not guess or guess != game.answer:
            return

        async with game.lock:

            # Có thể vừa bị người khác trả lời đúng / câu đố vừa hết hạn.
            if not game.running or guess != game.answer:
                return

            if game.expire_task:
                game.expire_task.cancel()

            answer_text = game.answer_display

            game.fail_streak = 0

            add_coin(message.author.id, VTV_REWARD)

            await message.channel.send(
                f"{message.author.mention} đoán đúng "
                f"**{answer_text}** và nhận **{VTV_REWARD}** 🪙!"
            )

            await self.vtv_new_round(game)

    # --------------------------------------------------------
    # VUA TIẾNG VIỆT - LỆNH
    # --------------------------------------------------------

    @commands.hybrid_command(name="vtvset")
    @commands.guild_only()
    async def vtvset(self, ctx):

        if not await self.require_manage(ctx):
            return

        set_vtv_channel(ctx.guild.id, ctx.channel.id)

        await ctx.send(
            f"✅ Đã đặt {ctx.channel.mention} "
            f"làm **kênh Vua Tiếng Việt**.\n"
            f"🎮 Dùng `.vtvstart` để bắt đầu."
        )

    @commands.hybrid_command(name="vtvstart")
    @commands.guild_only()
    async def vtvstart(self, ctx):

        channel_id = vtv_configured_channel(ctx.guild.id)

        if channel_id != ctx.channel.id:

            if channel_id:
                await ctx.send(
                    f"❌ Kênh Vua Tiếng Việt hiện tại là "
                    f"<#{channel_id}>.",
                    delete_after=6,
                )
            else:
                await ctx.send(
                    "❌ Server chưa cài kênh Vua Tiếng Việt. "
                    "Dùng `.vtvset`.",
                    delete_after=6,
                )

            return

        key = (ctx.guild.id, ctx.channel.id)

        game = self.vtv_games.get(key)

        if game and game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Vua Tiếng Việt đang chạy rồi.",
                4,
            )
            return

        game = VuaTVGame(ctx.guild.id, ctx.channel.id, ctx.channel)

        game.running = True

        self.vtv_games[key] = game

        await ctx.send(
            "🎉 **VUA TIẾNG VIỆT BẮT ĐẦU!**\n"
            f"Đoán đúng cụm từ để nhận **{VTV_REWARD:,}** 🪙 "
            "mỗi lượt.\n"
            f"Sau **{VTV_MAX_FAIL_STREAK}** lượt liên tiếp không ai "
            "đoán đúng, game tự dừng."
        )

        await self.vtv_new_round(game)

    @commands.hybrid_command(name="vtvstop")
    @commands.guild_only()
    async def vtvstop(self, ctx):

        if not await self.require_manage(ctx):
            return

        key = (ctx.guild.id, ctx.channel.id)

        game = self.vtv_games.pop(key, None)

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Hiện không có ván Vua Tiếng Việt.",
                4,
            )
            return

        game.running = False

        if game.expire_task:
            game.expire_task.cancel()

        await ctx.send(
            "🛑 **Đã tắt Vua Tiếng Việt.**\n"
            "Dùng `.vtvstart` để bật lại."
        )

    @commands.hybrid_command(name="vtv")
    @commands.guild_only()
    async def vtv(self, ctx):

        game = self.vtv_games.get((ctx.guild.id, ctx.channel.id))

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Chưa có ván Vua Tiếng Việt.",
                ERROR_DELETE_AFTER,
            )
            return

        letters_count = len(game.answer.replace(" ", ""))

        await self.send_notice(
            ctx.channel,
            f"🔤 Từ cần đoán: **{game.scrambled}** "
            f"(gồm {letters_count} ký tự).",
            6,
        )

    # ========================================================
    # XEM TỪ HIỆN TẠI
    # ========================================================

    @commands.hybrid_command(name="noitu")
    @commands.guild_only()
    async def noitu(self, ctx):

        if not await self.configured(ctx):
            return

        game = self.games.get((ctx.guild.id, ctx.channel.id))

        if not game or not game.running:
            await self.send_notice(
                ctx.channel,
                "❌ Chưa có ván Nối Từ.",
                ERROR_DELETE_AFTER,
            )
            return

        await self.send_notice(
            ctx.channel,
            f"🔗 **{game.current_phrase}**\n"
            f"👉 Nối bằng **{game.required_start.upper()}**",
            ERROR_DELETE_AFTER,
        )

    # ========================================================
    # PROFILE
    # ========================================================

    @commands.hybrid_command(name="me")
    @commands.guild_only()
    async def me(self, ctx, member: discord.Member = None):

        member = member or ctx.author

        wins, games, correct, wrong, earned = get_stats(ctx.guild.id, member.id)

        total = correct + wrong
        rate = correct / total * 100 if total else 0

        embed = discord.Embed(
            title="📖 HỒ SƠ NỐI TỪ",
            description=f"👤 {member.mention}",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="🏆 Lần thắng", value=f"{wins:,}", inline=True)
        embed.add_field(name="🎮 Ván", value=f"{games:,}", inline=True)
        embed.add_field(name="✅ Đúng", value=f"{correct:,}", inline=True)
        embed.add_field(name="❌ Sai", value=f"{wrong:,}", inline=True)
        embed.add_field(name="🎯 Tỉ lệ đúng", value=f"{rate:.1f}%", inline=True)
        embed.add_field(name="🪙 Coin từ Nối Từ", value=f"{earned:,}", inline=True)

        await ctx.send(embed=embed)

    # ========================================================
    # RANK
    # ========================================================

    @commands.hybrid_command(name="rank")
    @commands.guild_only()
    async def rank(self, ctx):

        rows = get_rank(ctx.guild.id, 10)

        if not rows:
            await ctx.send("📊 Server chưa có dữ liệu Nối Từ.")
            return

        lines = []

        for i, (uid, wins, games, correct, wrong, earned) in enumerate(rows, 1):

            member = ctx.guild.get_member(int(uid))

            name = member.display_name if member else f"User {uid}"

            crown = " 🥇" if i == 1 and wins > 0 else ""

            lines.append(
                f"**{i}.** {name}{crown} "
                f"— 🏆 `{wins}` "
                f"| 🪙 `{earned:,}`"
            )

        embed = discord.Embed(
            title="🏆 BXH NỐI TỪ",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

        await ctx.send(embed=embed)

    # ========================================================
    # COIN
    # ========================================================

    @commands.hybrid_command(name="gold")
    @commands.guild_only()
    async def gold(self, ctx):

        await ctx.send(
            f"🪙 {ctx.author.mention} "
            f"đang có **{get_coin(ctx.author.id):,} Coin** "
            f"trong ví KS-BOT."
        )

    # ========================================================
    # ĐỒNG BỘ / NẠP KHO TỪ
    # ========================================================

    @commands.hybrid_command(name="ntsync")
    @commands.guild_only()
    async def ntsync(self, ctx):
        """Tải lại kho từ online (kho lớn + 2 file riêng + link thêm)."""
        if not await self.require_manage(ctx):
            return

        # Slash command phải phản hồi trong 3 giây -> defer trước khi tải kho.
        await ctx.defer()

        msg = await ctx.send("📚 Đang cập nhật kho từ tiếng Việt...")

        report = await sync_external_word_bank()

        lines = [
            f"• {name}: " + ("❌ lỗi tải" if n is None else f"+{n:,}")
            for name, n in report.items()
        ]

        try:
            await msg.edit(content=(
                "📚 **Đồng bộ xong!**\n"
                + "\n".join(lines)
                + f"\n• Tổng kho chơi được: **{count_words()[1]:,}** cụm"
            ))
        except discord.HTTPException:
            pass

    @commands.hybrid_command(name="ntload")
    @commands.guild_only()
    async def ntload(self, ctx):
        """Nạp lại file .txt/.csv/.tsv trong thư mục word_data/."""
        if not await self.require_manage(ctx):
            return

        await ctx.defer()

        report = await asyncio.to_thread(load_external_word_files)

        if not report:
            await ctx.send(
                f"❌ Chưa có file .txt/.csv/.tsv nào trong `{WORD_DATA_DIR}`."
            )
            return

        lines = [
            f"• `{name}`: {n_lines:,} dòng → thêm **{added:,}**, bỏ qua {skipped:,}"
            for name, (n_lines, added, skipped) in report.items()
        ]

        await ctx.send(
            "📚 **NẠP FILE TRONG word_data/**\n"
            + "\n".join(lines)
            + f"\n• Tổng kho chơi được: **{count_words()[1]:,}** cụm"
        )

    # ========================================================
    # ĐỒNG BỘ SLASH COMMAND (chỉ chủ bot)
    # ========================================================

    @commands.command(name="ntslash")
    async def ntslash(self, ctx):
        """Đẩy slash command (/) lên Discord. Chỉ chủ bot dùng, chạy 1 lần sau khi cập nhật code."""
        if not self.is_owner(ctx):
            await ctx.send("❌ Chỉ chủ bot dùng được lệnh này.", delete_after=5)
            return

        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as e:
            await ctx.send(f"❌ Đồng bộ slash lỗi: {e}")
            return

        await ctx.send(
            f"✅ Đã đồng bộ **{len(synced)}** slash command. "
            "Nếu chưa thấy, tải lại Discord (Ctrl+R)."
        )

    # ========================================================
    # DELETE WORD
    # ========================================================

    @commands.hybrid_command(name="ntdel")
    @commands.guild_only()
    async def ntdel(self, ctx, *, phrase: str):

        if not await self.require_manage(ctx):
            return

        if delete_phrase(phrase):
            await ctx.send(f"🗑️ Đã xóa **{normalize_text(phrase)}**.")
        else:
            await ctx.send("❌ Không có từ này trong kho.")

    # ========================================================
    # STATS
    # ========================================================

    @commands.hybrid_command(name="ntstats")
    @commands.guild_only()
    async def ntstats(self, ctx):

        if not await self.require_manage(ctx):
            return

        total, playable = count_words()

        channel_id = configured_channel(ctx.guild.id)

        game = self.games.get((ctx.guild.id, ctx.channel.id))

        status = (
            "🟢 Đang chạy"
            if game and game.running
            else "🔴 Đang tắt"
        )

        await ctx.send(
            "📚 **KHO NỐI TỪ**\n"
            f"• Tổng mục từ: **{total:,}**\n"
            f"• Cụm 2-3 tiếng chơi được: **{playable:,}**\n"
            f"• Kênh: "
            f"{f'<#{channel_id}>' if channel_id else 'chưa cài'}\n"
            f"• Trạng thái: **{status}**\n"
            f"• Nguồn online: **{len(all_word_sources())}** "
            f"(trong đó {len(MY_WORD_FILES)} file riêng) + thư mục `word_data/`"
        )


# ============================================================
# SETUP
# ============================================================

async def setup(bot):
    await bot.add_cog(NoituCog(bot))
