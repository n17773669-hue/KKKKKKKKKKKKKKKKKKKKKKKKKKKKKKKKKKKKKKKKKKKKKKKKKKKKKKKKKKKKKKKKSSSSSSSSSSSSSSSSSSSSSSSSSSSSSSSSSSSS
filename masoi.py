"""
masoi.py — Ma Sói (bản gộp duy nhất, thay cho toàn bộ V2 → V15 cũ) — ĐỦ 50 VAI TRÒ.

Điểm chính:
  • Đêm kết thúc NGAY khi mọi vai trò cần hành động đã chọn xong (có thời gian tối đa).
  • Vote kết thúc NGAY khi mọi người còn sống đã bỏ phiếu.
  • Ai không chọn / không vote khi hết giờ  → bị cấm chơi Ma Sói 10 phút (lưu SQLite).
  • Mọi tin nhắn DM / select dùng TÊN hiển thị (không còn <@id>).
  • Mọi nút / select đều phản hồi interaction → không còn "ứng dụng không phản hồi".
  • Không xóa tin nhắn chat của người chơi ban đêm nữa.
  • 50 vai trò, mở dần theo số người chơi (xem WW_ROLE_INFO / OPTIONAL_ROLES).

Nạp bằng: await bot.load_extension("masoi")
"""
import asyncio
import random
import sqlite3
import time
import traceback

import discord
from discord.ext import commands

from database import DB_NAME, UI_COLORS, ui_panel, add_exp_to_user

# ==========================================
# CẤU HÌNH
# ==========================================
MIN_PLAYERS = 5
MAX_PLAYERS = 24            # select menu của Discord tối đa 25 mục (24 người + 1 "phiếu trắng")
NIGHT_TIMEOUT = 45          # tối đa cho các vai trò ban đêm
WITCH_TIMEOUT = 30          # tối đa cho Phù Thủy
HUNTER_TIMEOUT = 25         # tối đa cho Thợ Săn
DAY_SECONDS = 60            # thảo luận
VOTE_SECONDS = 40           # tối đa cho vote
MIN_NIGHT_SECONDS = 6       # đêm tối thiểu, tránh lộ vai trò qua việc "đêm kết thúc quá nhanh"
BAN_MINUTES = 10

# ==========================================
# 50 VAI TRÒ:  tên -> (emoji, phe, mô tả)
# ==========================================
WW_ROLE_INFO = {
    # ---------- PHE SÓI (7) ----------
    "Sói": ("🐺", "Phe Sói", "Mỗi đêm bỏ phiếu kín chọn nạn nhân. Nhiều Sói thì mục tiêu nhiều phiếu nhất bị cắn."),
    "Sói Con": ("🐶", "Phe Sói", "Vote cắn như Sói thường. Khi Sói Con chết, đêm kế tiếp bầy Sói cắn 2 người."),
    "Sói Chúa": ("👹", "Phe Sói", "Vote cắn như Sói thường nhưng phiếu của Sói Chúa tính 2."),
    "Sói Tiên Tri": ("🔭", "Phe Sói", "Vote cắn cùng bầy Sói, đồng thời mỗi đêm soi vai trò chính xác của 1 người."),
    "Sói Ẩn Mình": ("🌫️", "Phe Sói", "Vote cắn cùng bầy Sói. Tiên Tri và Thám Tử thấy bạn như một người bình thường."),
    "Sói Bịt Miệng": ("🤐", "Phe Sói", "Vote cắn cùng bầy Sói, đồng thời mỗi đêm chọn 1 người: người đó không thể bỏ phiếu ban ngày kế tiếp."),
    "Sói Báo Thù": ("🩸", "Phe Sói", "Vote cắn cùng bầy Sói. Nếu bị treo cổ, kéo theo 1 người đã vote cho mình."),
    # ---------- PHE THỨ BA (2) ----------
    "Sát Nhân Hàng Loạt": ("🔪", "Phe Thứ Ba", "Mỗi đêm giết 1 người. Thắng khi là người sống sót cuối cùng (hoặc chỉ còn cùng 1 người)."),
    "Kẻ Chán Đời": ("🤡", "Phe Thứ Ba", "Thắng NGAY nếu bị làng treo cổ. Hãy khiến mọi người nghi ngờ mình!"),
    # ---------- PHE DÂN: THÔNG TIN (11) ----------
    "Tiên Tri": ("🔮", "Phe Dân", "Mỗi đêm soi 1 người để biết có phải Sói hay không."),
    "Tiên Tri Say": ("🥴", "Phe Dân", "Tưởng mình là Tiên Tri nhưng kết quả soi chỉ đúng 50%. Mọi người thấy bạn là Tiên Tri."),
    "Thám Tử": ("🕵️", "Phe Dân", "Mỗi đêm kiểm tra 2 người: cùng phe hay khác phe."),
    "Nhà Tiên Tri": ("📜", "Phe Dân", "Mỗi đêm xem vai trò của 1 người đã chết."),
    "Người Đào Mộ": ("⚰️", "Phe Dân", "Mỗi đêm chọn 1 người đã chết để biết họ thuộc phe nào."),
    "Người Gác Cổng": ("🚪", "Phe Dân", "Biết mình có bị nhắm tới đêm qua hay không (không biết ai nhắm)."),
    "Pháp Sư": ("🪄", "Phe Dân", "1 lần/ván: xem vai trò chính xác của 1 người còn sống."),
    "Người Quan Sát": ("👁️", "Phe Dân", "Mỗi đêm theo dõi 1 người: biết ai đã ghé thăm họ."),
    "Thợ Dò": ("🐾", "Phe Dân", "Mỗi đêm theo dấu 1 người: biết họ đã ghé thăm ai."),
    "Nhà Toán Học": ("🧮", "Phe Dân", "Mỗi sáng biết còn bao nhiêu Sói đang sống."),
    "Nhà Báo": ("📰", "Phe Dân", "1 lần/ván: đăng báo ẩn danh cho cả làng biết 1 người có phải Sói hay không."),
    "Cô Bé Rình Mò": ("👧", "Phe Dân", "Mỗi đêm có 25% cơ hội thấy tên của 1 Sói ngẫu nhiên."),
    "Nhân Chứng": ("🧾", "Phe Dân", "Đêm đầu biết chắc 1 người là Dân Làng thuần túy."),
    # ---------- PHE DÂN: BẢO VỆ / CHỊU ĐÒN (8) ----------
    "Bảo Vệ": ("🛡️", "Phe Dân", "Mỗi đêm bảo vệ 1 người. Không bảo vệ cùng 1 người 3 đêm liên tiếp."),
    "Phù Thủy": ("🧪", "Phe Dân", "Có 1 bình Cứu và 1 bình Độc, mỗi bình dùng 1 lần trong ván."),
    "Bác Sĩ": ("💉", "Phe Dân", "Mỗi đêm cứu 1 người khỏi mọi đòn tấn công (trừ độc). Được tự cứu mình 1 lần."),
    "Linh Mục": ("✝️", "Phe Dân", "1 lần/ván: ban phước cho 1 người — họ miễn nhiễm MỌI cái chết đêm đó (kể cả độc)."),
    "Thợ Rèn": ("🔨", "Phe Dân", "1 lần/ván: rèn dao bạc — đêm đó Sói không thể tấn công ai."),
    "Bà Tiên": ("🧚", "Phe Dân", "1 lần/ván: ban lớp bảo hộ cho 1 người, chặn 1 đòn tấn công ban đêm nhắm vào họ."),
    "Lão Già": ("👴", "Phe Dân", "Sống sót qua lần đầu bị tấn công ban đêm (chỉ 1 lần)."),
    "Người Nếm Thử": ("🍷", "Phe Dân", "Miễn nhiễm với bình Độc của Phù Thủy."),
    "Anh Ngốc": ("🤪", "Phe Dân", "Nếu bị làng treo cổ: được tha chết nhưng lộ vai trò và mất quyền bỏ phiếu."),
    # ---------- PHE DÂN: TẤN CÔNG / TRẢ ĐŨA (6) ----------
    "Thợ Săn": ("🏹", "Phe Dân", "Khi chết được bắn 1 người còn sống (hoặc bỏ qua)."),
    "Xạ Thủ": ("🔫", "Phe Dân", "Có 2 viên đạn, mỗi đêm được bắn 1 người. Tiếng súng sẽ lộ danh tính bạn."),
    "Thợ Săn Sói": ("🪓", "Phe Dân", "1 lần/ván: bắn 1 người ban đêm. Trúng phe Sói thì họ chết, bắn nhầm thì bạn chết."),
    "Kẻ Báo Thù": ("⚔️", "Phe Dân", "Nếu bị Sói cắn chết, 1 Sói ngẫu nhiên chết theo ngay lập tức."),
    "Hiệp Sĩ Gỉ Sét": ("🗡️", "Phe Dân", "Nếu bị Sói cắn chết, đêm kế tiếp 1 Sói ngẫu nhiên chết vì lưỡi kiếm gỉ."),
    "Người Hiến Thân": ("🕯️", "Phe Dân", "Mỗi đêm chọn 1 người: nếu người đó bị Sói cắn, bạn chết thay."),
    # ---------- PHE DÂN: XÃ HỘI / BỎ PHIẾU (5) ----------
    "Trưởng Làng": ("👑", "Phe Dân", "Có thể công khai thân phận; khi công khai phiếu vote tính 2."),
    "Người Kế Vị": ("🎖️", "Phe Dân", "Khi Trưởng Làng chết, bạn lên thay (công khai, phiếu tính 2)."),
    "Thẩm Phán": ("⚖️", "Phe Dân", "Khi vote hòa, lá phiếu của bạn quyết định (nếu bạn bỏ cho 1 trong những người hòa)."),
    "Luật Sư": ("👨‍⚖️", "Phe Dân", "1 lần/ván: bào chữa cho 1 người — ngày kế tiếp họ không thể bị treo cổ."),
    "Người Hòa Giải": ("☮️", "Phe Dân", "1 lần/ván: kêu gọi hòa bình — ngày kế tiếp không ai bị treo cổ."),
    # ---------- PHE DÂN: LIÊN KẾT / BIẾN ĐỔI (6) ----------
    "Cupid": ("💘", "Phe Dân", "Đêm đầu ghép 2 người thành Người Yêu; một người chết người kia chết theo."),
    "Anh Em": ("👬", "Phe Dân", "Luôn có 2 Anh Em, biết mặt nhau ngay từ đầu ván."),
    "Đứa Trẻ Hoang": ("🧒", "Phe Dân", "Đêm đầu chọn 1 hình mẫu. Hình mẫu chết thì bạn hóa thành Sói."),
    "Kẻ Bắt Chước": ("🎭", "Phe Dân", "Đêm đầu chọn 1 người. Khi người đó chết, bạn nhận vai trò của họ."),
    "Kẻ Bị Nguyền": ("🌑", "Phe Dân", "Bị Sói cắn thì không chết mà hóa thành Sói."),
    "Tên Trộm": ("🥷", "Phe Dân", "Đêm đầu cướp vai trò của 1 người; nạn nhân trở thành Dân Làng."),
    # ---------- KHÁC (2) ----------
    "Kẻ Nội Gián": ("🎩", "Phe Sói", "Trông như Dân (Tiên Tri soi không ra) nhưng thắng cùng Sói; hóa thành Sói khi Sói chết hết."),
    "Dân Làng": ("🧑‍🌾", "Phe Dân", "Không có kỹ năng đêm. Thảo luận, suy luận và bỏ phiếu."),
}

WOLF_ROLES = {"Sói", "Sói Con", "Sói Chúa", "Sói Tiên Tri", "Sói Ẩn Mình", "Sói Bịt Miệng", "Sói Báo Thù"}
WOLF_SPECIALS = ["Sói Chúa", "Sói Tiên Tri", "Sói Ẩn Mình", "Sói Bịt Miệng", "Sói Con", "Sói Báo Thù"]
CORE_ROLES = ["Tiên Tri", "Bảo Vệ", "Phù Thủy", "Thợ Săn"]

# Vai trò mở dần: role -> số người tối thiểu
OPTIONAL_ROLES = {
    "Cupid": 7, "Lão Già": 7,
    "Bác Sĩ": 8, "Kẻ Chán Đời": 8, "Nhân Chứng": 8, "Người Nếm Thử": 8,
    "Sát Nhân Hàng Loạt": 9, "Thợ Rèn": 9, "Xạ Thủ": 9,
    "Trưởng Làng": 10, "Người Quan Sát": 10, "Thợ Dò": 10, "Anh Ngốc": 10,
    "Tiên Tri Say": 11, "Kẻ Bị Nguyền": 11, "Bà Tiên": 11,
    "Thám Tử": 12, "Thẩm Phán": 12, "Linh Mục": 12,
    "Nhà Báo": 13, "Người Hiến Thân": 13, "Kẻ Báo Thù": 13,
    "Nhà Tiên Tri": 14, "Người Đào Mộ": 14, "Hiệp Sĩ Gỉ Sét": 14,
    "Đứa Trẻ Hoang": 15, "Kẻ Bắt Chước": 15, "Kẻ Nội Gián": 15,
    "Người Gác Cổng": 16, "Pháp Sư": 16, "Luật Sư": 16,
    "Nhà Toán Học": 17, "Cô Bé Rình Mò": 17, "Người Hòa Giải": 17,
    "Tên Trộm": 18, "Thợ Săn Sói": 18, "Người Kế Vị": 18, "Anh Em": 18,
}
# Các vai trò của bản cũ luôn được ưu tiên khi đủ người (giữ nguyên đội hình cũ)
PRIORITY_ROLES = ["Cupid", "Trưởng Làng", "Thám Tử", "Nhà Tiên Tri", "Người Gác Cổng"]


def is_wolf(role):
    return role in WOLF_ROLES


def appears_wolf(role):
    """Tiên Tri / Thám Tử nhìn thấy là Sói? (Sói Ẩn Mình qua mặt được)"""
    return role in WOLF_ROLES and role != "Sói Ẩn Mình"


def is_wolf_side(role):
    return role in WOLF_ROLES or role == "Kẻ Nội Gián"


def public_role(role):
    """Vai trò hiển thị công khai (Tiên Tri Say luôn hiện là Tiên Tri)."""
    return "Tiên Tri" if role == "Tiên Tri Say" else role


def faction_of(role):
    if is_wolf_side(role):
        return "Phe Sói"
    if role in ("Sát Nhân Hàng Loạt", "Kẻ Chán Đời"):
        return "Phe Thứ Ba"
    return "Phe Dân"


def role_emoji(role):
    return WW_ROLE_INFO.get(public_role(role), ("🎭",))[0]


def distribution(n):
    wolves_n = 1 if n <= 6 else 2 if n <= 9 else 3 if n <= 13 else 4 if n <= 17 else 5
    if n >= 8:
        roles = ["Sói"] + random.sample(WOLF_SPECIALS, min(wolves_n - 1, len(WOLF_SPECIALS)))
    else:
        roles = ["Sói"] * wolves_n
    roles.extend(CORE_ROLES)

    reserve = 0 if n < 7 else 1 if n < 10 else 2 if n < 16 else 3   # chỗ chừa cho Dân Làng
    room = n - len(roles) - reserve

    eligible = [r for r, m in OPTIONAL_ROLES.items() if n >= m]
    prio = [r for r in PRIORITY_ROLES if r in eligible]
    rest = [r for r in eligible if r not in prio]
    random.shuffle(rest)

    chosen = []
    for r in prio + rest:
        need = 2 if r == "Anh Em" else 1
        if room < need:
            continue
        chosen.extend([r] * need)
        room -= need
    if "Người Kế Vị" in chosen and "Trưởng Làng" not in chosen:
        chosen.remove("Người Kế Vị")     # không có Trưởng Làng thì Kế Vị vô nghĩa → thành Dân Làng
    roles.extend(chosen)
    roles.extend(["Dân Làng"] * max(0, n - len(roles)))
    random.shuffle(roles)
    return roles[:n]


def apply_role(d, role):
    """Gán vai trò + khôi phục trạng thái kỹ năng của vai đó."""
    d.update({
        "role": role,
        "heal_used": False, "poison_used": False,
        "hunter_used": False, "mayor_revealed": False,
        "used": False,                       # kỹ năng 1 lần/ván
        "bullets": 2 if role == "Xạ Thủ" else 0,
        "shield": role == "Lão Già",         # chặn 1 đòn tấn công ban đêm
        "self_used": False,                  # Bác Sĩ tự cứu mình
        "silenced": False,                   # Anh Ngốc đã lộ → mất quyền vote
    })


# ==========================================
# CẤM CHƠI (AFK) — lưu SQLite
# ==========================================
def _ban_table():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("CREATE TABLE IF NOT EXISTS ww_bans (user_id TEXT PRIMARY KEY, until REAL)")
    conn.commit()
    conn.close()


def ban_user(uid, minutes=BAN_MINUTES):
    try:
        _ban_table()
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR REPLACE INTO ww_bans (user_id, until) VALUES (?, ?)",
                     (str(uid), time.time() + minutes * 60))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WW] Lỗi ghi ban: {e}")


def ban_remaining(uid):
    """Số giây còn bị cấm (0 nếu không bị)."""
    try:
        _ban_table()
        conn = sqlite3.connect(DB_NAME)
        row = conn.execute("SELECT until FROM ww_bans WHERE user_id = ?", (str(uid),)).fetchone()
        conn.close()
        if row and row[0] > time.time():
            return int(row[0] - time.time())
    except Exception:
        pass
    return 0


def fmt_remaining(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m} phút {s:02d} giây" if m else f"{s} giây"


# ==========================================
# TRẠNG THÁI VÁN
# ==========================================
class WerewolfGame:
    def __init__(self, ctx, bot):
        self.ctx = ctx
        self.bot = bot
        self.host_id = ctx.author.id
        self.players = {}          # uid -> dict
        self.state = "waiting"     # waiting / starting / night / day / vote / ended
        self.wolves = []
        self.lobby_message = None
        self.first_night = True
        self.guard_history = []
        self.night = {}
        self.votes = {}
        self.task = None
        # --- trạng thái xuyên đêm/ngày của các vai trò ---
        self.muted = set()             # bị Sói Bịt Miệng: không được vote hôm nay
        self.lawyer_shield = None      # người được Luật Sư bào chữa hôm nay
        self.peace_today = False       # Người Hòa Giải: hôm nay không treo cổ
        self.knight_pending = 0        # Hiệp Sĩ Gỉ Sét: số Sói sẽ chết đêm tới
        self.cub_revenge = False       # Sói Con đã chết: đêm tới cắn 2 người
        self.special_win = None        # Kẻ Chán Đời thắng
        # --- theo dõi "ai chưa chọn" trong mỗi phase ---
        self.token = 0
        self.pending = {}              # uid -> số lựa chọn còn chờ
        self.event = asyncio.Event()
        self.views = []
        self.dm_msgs = []
        self.vote_event = asyncio.Event()

    # ---------- tiện ích ----------
    @property
    def channel(self):
        return self.ctx.channel

    def name(self, uid):
        d = self.players.get(uid)
        return d["member"].display_name if d else "Không rõ"

    def mention(self, uid):
        d = self.players.get(uid)
        return d["member"].mention if d else "Không rõ"

    def alive_ids(self):
        return [u for u, d in self.players.items() if d["alive"]]

    def visit(self, actor, target):
        """Ghi nhận 'actor đã ghé thăm target' (cho Người Quan Sát / Thợ Dò)."""
        self.night.setdefault("visits", []).append((actor, target))

    # ---------- phase ----------
    def begin_phase(self):
        self.token += 1
        self.pending = {}
        self.event = asyncio.Event()
        self.views = []
        self.dm_msgs = []

    def expect(self, uid):
        self.pending[uid] = self.pending.get(uid, 0) + 1
        self.event.clear()

    def done(self, uid):
        left = self.pending.get(uid, 0) - 1
        if left <= 0:
            self.pending.pop(uid, None)
        else:
            self.pending[uid] = left
        if not self.pending:
            self.event.set()

    async def wait_pending(self, timeout):
        """Chờ tới khi mọi người đã chọn xong hoặc hết giờ. Trả về tập người chưa chọn."""
        if not self.pending:
            return set()
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return {u for u, c in self.pending.items() if c > 0}

    async def end_phase(self):
        for v in self.views:
            try:
                v.stop()
            except Exception:
                pass
        msgs = self.dm_msgs
        self.views = []
        self.dm_msgs = []

        async def clear(m):
            try:
                await m.edit(view=None)
            except discord.HTTPException:
                pass

        if msgs:
            await asyncio.gather(*(clear(m) for m in msgs))


async def send_dm(game, uid, content=None, embed=None, view=None):
    member = game.players[uid]["member"]
    try:
        msg = await member.send(content=content, embed=embed, view=view)
        if view is not None:
            game.dm_msgs.append(msg)
        return msg
    except discord.HTTPException:
        return None


async def ask(game, uid, text, view):
    """Gửi lựa chọn qua DM và đăng ký người này vào danh sách 'cần chọn'."""
    game.expect(uid)
    game.views.append(view)
    msg = await send_dm(game, uid, content=text, view=view)
    if msg is None:
        game.done(uid)
        await game.channel.send(f"⚠️ Không gửi được DM cho **{game.name(uid)}** (đã tắt tin nhắn riêng?). Bỏ qua lượt của họ.")


async def penalize(game, uids, reason):
    uids = [u for u in uids if u in game.players]
    if not uids:
        return
    for u in uids:
        ban_user(u)
    names = ", ".join(game.mention(u) for u in uids)
    await game.channel.send(embed=ui_panel(
        "PHẠT AFK / PHÁ GAME",
        f"🚫 {names} {reason}\n⛔ Bị **cấm chơi Ma Sói {BAN_MINUTES} phút**.",
        color="danger", emoji="🚫"))


def role_embed(game, uid):
    """Thẻ vai trò gửi qua DM."""
    d = game.players[uid]
    role = d["role"]
    shown = public_role(role)
    icon, faction, desc = WW_ROLE_INFO[shown]
    embed = discord.Embed(title=f"{icon} VAI TRÒ CỦA BẠN: {shown}", color=0x5865f2)
    embed.add_field(name="⚔️ Phe", value=faction, inline=True)
    embed.add_field(name="📖 Chức năng", value=desc, inline=False)
    if is_wolf(role):
        mates = [game.name(x) for x in game.wolves if x != uid and game.players[x]["alive"]]
        embed.add_field(name="🐺 Đồng đội Sói", value=", ".join(mates) if mates else "Bạn là Sói duy nhất.", inline=False)
    if role == "Anh Em":
        bros = [game.name(x) for x, y in game.players.items() if y["role"] == "Anh Em" and x != uid]
        embed.add_field(name="👬 Anh Em của bạn", value=", ".join(bros) if bros else "Không rõ", inline=False)
    return embed


async def become_wolf(game, uid, why):
    d = game.players[uid]
    apply_role(d, "Sói")
    if uid not in game.wolves:
        game.wolves.append(uid)
    embed = role_embed(game, uid)
    embed.description = why
    await send_dm(game, uid, embed=embed)


# ==========================================
# VIEW CHUNG CHO LỰA CHỌN TRONG DM
# ==========================================
def alive_options(game, exclude=(), emoji="🎯"):
    opts = []
    for uid, d in game.players.items():
        if d["alive"] and uid not in exclude:
            opts.append(discord.SelectOption(label=d["member"].display_name[:100], value=str(uid), emoji=emoji))
    return opts[:25]


def dead_options(game, exclude=(), emoji="📜"):
    opts = []
    for uid, d in game.players.items():
        if not d["alive"] and uid not in exclude:
            opts.append(discord.SelectOption(label=d["member"].display_name[:100], value=str(uid), emoji=emoji))
    return opts[:25]


class NightView(discord.ui.View):
    def __init__(self, game, uid):
        super().__init__(timeout=None)
        self.game = game
        self.uid = uid
        self.token = game.token

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
            return False
        if self.token != self.game.token:
            await interaction.response.send_message("⌛ Đã hết giờ cho lựa chọn này.", ephemeral=True)
            return False
        return True


class PickView(NightView):
    """View chung: chọn 1 (hoặc nhiều) người rồi gọi handler(values) -> chuỗi kết quả.
    Handler có thể raise ValueError("lý do") để từ chối và cho chọn lại."""

    def __init__(self, game, uid, placeholder, options, handler, skip=False, vmin=1, vmax=1):
        super().__init__(game, uid)
        self.handler = handler
        self.sel = discord.ui.Select(placeholder=placeholder, options=options[:25], min_values=vmin, max_values=vmax)
        self.sel.callback = self.pick
        self.add_item(self.sel)
        if skip:
            b = discord.ui.Button(label="Bỏ qua", style=discord.ButtonStyle.secondary, emoji="🕊️")
            b.callback = self.skip
            self.add_item(b)

    async def pick(self, interaction):
        try:
            text = self.handler([int(v) for v in self.sel.values])
        except ValueError as e:
            await interaction.response.send_message(f"🚫 {e}", ephemeral=True)
            return
        await interaction.response.edit_message(content=text, view=None)
        self.game.done(self.uid)

    async def skip(self, interaction):
        await interaction.response.edit_message(content="🕊️ Bạn đã bỏ qua lượt này.", view=None)
        self.game.done(self.uid)


class ConfirmView(NightView):
    """View chung cho kỹ năng 'dùng / không dùng' (không cần chọn người)."""

    def __init__(self, game, uid, yes_label, yes_emoji, handler):
        super().__init__(game, uid)
        self.handler = handler
        yes = discord.ui.Button(label=yes_label, style=discord.ButtonStyle.success, emoji=yes_emoji)
        yes.callback = self.yes
        no = discord.ui.Button(label="Để lần sau", style=discord.ButtonStyle.secondary, emoji="🕊️")
        no.callback = self.no
        self.add_item(yes)
        self.add_item(no)

    async def yes(self, interaction):
        text = self.handler()
        await interaction.response.edit_message(content=text, view=None)
        self.game.done(self.uid)

    async def no(self, interaction):
        await interaction.response.edit_message(content="🕊️ Bạn chưa dùng kỹ năng.", view=None)
        self.game.done(self.uid)


class SeerView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        self.sel = discord.ui.Select(placeholder="🔮 Chọn người để soi", options=alive_options(game, {uid}, "🔮"))
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        g = self.game
        t = int(self.sel.values[0])
        wolf = appears_wolf(g.players[t]["role"])
        if g.players[self.uid]["role"] == "Tiên Tri Say":
            wolf = random.random() < 0.5          # say rượu: kết quả ngẫu nhiên
        res = "🐺 **CÓ DẤU HIỆU SÓI**" if wolf else "🟢 **KHÔNG PHẢI SÓI**"
        g.visit(self.uid, t)
        await interaction.response.edit_message(content=f"🔮 Kết quả soi **{g.name(t)}**: {res}", view=None)
        g.done(self.uid)


class GuardView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        self.sel = discord.ui.Select(placeholder="🛡️ Chọn người để bảo vệ", options=alive_options(game, {uid}, "🛡️"))
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        t = int(self.sel.values[0])
        h = self.game.guard_history
        if len(h) >= 2 and h[-1] == t and h[-2] == t:
            await interaction.response.send_message("🚫 Không thể bảo vệ người này 3 đêm liên tiếp. Hãy chọn người khác.", ephemeral=True)
            return
        self.game.night["guard"] = t
        self.game.visit(self.uid, t)
        await interaction.response.edit_message(content=f"🛡️ Đã chọn bảo vệ **{self.game.name(t)}**.", view=None)
        self.game.done(self.uid)


class WolfView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(u), emoji="🐺")
                for u, d in game.players.items() if d["alive"] and not is_wolf(d["role"])][:25]
        self.sel = discord.ui.Select(placeholder="🐺 Chọn nạn nhân", options=opts)
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        t = int(self.sel.values[0])
        self.game.night["wolf_votes"][self.uid] = t
        await interaction.response.edit_message(content=f"🐺 Đã chọn cắn **{self.game.name(t)}**.", view=None)
        self.game.done(self.uid)


class DetectiveView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        opts = alive_options(game, {uid}, "🕵️")
        self.sel = discord.ui.Select(placeholder="🕵️ Chọn đúng 2 người", options=opts,
                                     min_values=2, max_values=2)
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        a, b = (int(v) for v in self.sel.values)
        same = appears_wolf(self.game.players[a]["role"]) == appears_wolf(self.game.players[b]["role"])
        res = "🟢 **CÙNG PHE**" if same else "🔴 **KHÁC PHE**"
        await interaction.response.edit_message(
            content=f"🕵️ **{self.game.name(a)}** + **{self.game.name(b)}** → {res}", view=None)
        self.game.done(self.uid)


class MediumView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(u), emoji="📜")
                for u, d in game.players.items() if not d["alive"] and u != uid][:25]
        self.sel = discord.ui.Select(placeholder="📜 Chọn người đã chết", options=opts)
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        t = int(self.sel.values[0])
        await interaction.response.edit_message(
            content=f"📜 **{self.game.name(t)}** từng là **{public_role(self.game.players[t]['role'])}**.", view=None)
        self.game.done(self.uid)


class CupidView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        self.sel = discord.ui.Select(placeholder="💘 Chọn đúng 2 người để ghép đôi",
                                     options=alive_options(game, {uid}, "💘"),
                                     min_values=2, max_values=2)
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        a, b = (int(v) for v in self.sel.values)
        g = self.game
        g.players[a]["lover"] = b
        g.players[b]["lover"] = a
        await interaction.response.edit_message(
            content=f"💘 Đã ghép đôi **{g.name(a)}** và **{g.name(b)}**.", view=None)
        g.done(self.uid)
        for me, other in ((a, b), (b, a)):
            await send_dm(g, me, embed=discord.Embed(
                title="💘 BẠN ĐÃ CÓ NGƯỜI YÊU",
                description=(f"Bạn và **{g.name(other)}** được Cupid ghép thành một cặp.\n\n"
                             "❤️ Hai người biết nhau là Người Yêu.\n"
                             "⚠️ Nếu một người chết, người còn lại cũng chết theo."),
                color=0xff4d8d))


class HunterView(NightView):
    def __init__(self, game, uid):
        super().__init__(game, uid)
        self.target = None
        self.sel = discord.ui.Select(placeholder="🏹 Chọn người để bắn", options=alive_options(game, {uid}, "🏹"))
        self.sel.callback = self.pick
        self.add_item(self.sel)
        skip = discord.ui.Button(label="Bỏ qua phát bắn", style=discord.ButtonStyle.secondary, emoji="🕊️")
        skip.callback = self.skip
        self.add_item(skip)

    async def pick(self, interaction):
        self.target = int(self.sel.values[0])
        await interaction.response.edit_message(content=f"🏹 Đã bắn **{self.game.name(self.target)}**.", view=None)
        self.game.done(self.uid)

    async def skip(self, interaction):
        await interaction.response.edit_message(content="🕊️ Bạn đã bỏ qua phát bắn.", view=None)
        self.game.done(self.uid)


class WitchView(NightView):
    """Phù Thủy: Cứu và Độc là 2 bình độc lập, có thể dùng cả hai trong 1 đêm."""

    def __init__(self, game, uid, victim):
        super().__init__(game, uid)
        self.victim = victim
        d = game.players[uid]
        self.can_heal = bool(victim) and not d["heal_used"] and not game.night.get("save")
        self.can_poison = not d["poison_used"] and game.night.get("poison") is None
        if self.can_heal:
            b = discord.ui.Button(label="Cứu nạn nhân", style=discord.ButtonStyle.success, emoji="💚")
            b.callback = self.save
            self.add_item(b)
        if self.can_poison:
            b = discord.ui.Button(label="Dùng bình Độc", style=discord.ButtonStyle.danger, emoji="☠️")
            b.callback = self.poison
            self.add_item(b)
        b = discord.ui.Button(label="Xong lượt", style=discord.ButtonStyle.secondary, emoji="✅")
        b.callback = self.finish
        self.add_item(b)

    def status(self):
        d = self.game.players[self.uid]
        return (f"💚 Bình Cứu: {'đã dùng' if d['heal_used'] else 'còn'} • "
                f"☠️ Bình Độc: {'đã dùng' if d['poison_used'] else 'còn'}")

    async def _next(self, interaction, note):
        """Sau mỗi hành động: nếu còn việc để làm thì hiện lại menu, hết việc thì chốt luôn."""
        g = self.game
        self.stop()
        nxt = WitchView(g, self.uid, self.victim)
        if not nxt.can_heal and not nxt.can_poison:
            await interaction.response.edit_message(content=f"{note}\n🧪 Đã xong lượt Phù Thủy.", view=None)
            g.done(self.uid)
        else:
            g.views.append(nxt)
            await interaction.response.edit_message(content=f"{note}\n{nxt.status()}", view=nxt)

    async def save(self, interaction):
        g = self.game
        g.players[self.uid]["heal_used"] = True
        g.night["save"] = True
        g.visit(self.uid, self.victim)
        await self._next(interaction, f"💚 Đã dùng **Bình Cứu** cho **{g.name(self.victim)}**.")

    async def poison(self, interaction):
        v = PoisonPickView(self.game, self.uid, self.victim, self)
        self.game.views.append(v)
        await interaction.response.edit_message(content="☠️ Chọn mục tiêu cho **Bình Độc**:", view=v)

    async def finish(self, interaction):
        await interaction.response.edit_message(content=f"🧪 Đã chốt lượt Phù Thủy.\n{self.status()}", view=None)
        self.game.done(self.uid)


class PoisonPickView(NightView):
    def __init__(self, game, uid, victim, parent):
        super().__init__(game, uid)
        self.victim = victim
        self.parent = parent
        self.sel = discord.ui.Select(placeholder="☠️ Chọn người để đầu độc", options=alive_options(game, {uid}, "☠️"))
        self.sel.callback = self.pick
        self.add_item(self.sel)
        back = discord.ui.Button(label="Quay lại", style=discord.ButtonStyle.secondary)
        back.callback = self.back
        self.add_item(back)

    async def pick(self, interaction):
        g = self.game
        t = int(self.sel.values[0])
        g.players[self.uid]["poison_used"] = True
        g.night["poison"] = t
        g.visit(self.uid, t)
        self.stop()
        nxt = WitchView(g, self.uid, self.victim)
        note = f"☠️ Đã dùng **Bình Độc** lên **{g.name(t)}**."
        if not nxt.can_heal and not nxt.can_poison:
            await interaction.response.edit_message(content=f"{note}\n🧪 Đã xong lượt Phù Thủy.", view=None)
            g.done(self.uid)
        else:
            g.views.append(nxt)
            await interaction.response.edit_message(content=f"{note}\n{nxt.status()}", view=nxt)

    async def back(self, interaction):
        self.stop()
        nxt = WitchView(self.game, self.uid, self.victim)
        self.game.views.append(nxt)
        await interaction.response.edit_message(content=f"🧪 {nxt.status()}", view=nxt)


class MayorView(discord.ui.View):
    """Không chặn đêm; Trưởng Làng bấm lúc nào cũng được."""

    def __init__(self, game, uid):
        super().__init__(timeout=300)
        self.game = game
        self.uid = uid

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("❌ Đây không phải lựa chọn của bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Lộ thân phận", style=discord.ButtonStyle.success, emoji="👑")
    async def reveal(self, interaction, button):
        ok = await reveal_mayor(self.game, self.uid)
        text = ("👑 Bạn đã **công khai**. Từ giờ phiếu của bạn tính **2**." if ok
                else "⚠️ Bạn không thể công khai lúc này.")
        await interaction.response.edit_message(content=text, view=None)
        self.stop()

    @discord.ui.button(label="Chưa lộ", style=discord.ButtonStyle.secondary, emoji="🤫")
    async def hide(self, interaction, button):
        await interaction.response.edit_message(content="🤫 Bạn chọn **chưa lộ**.", view=None)
        self.stop()


async def reveal_mayor(game, uid):
    d = game.players.get(uid)
    if (not d or not d["alive"] or d["role"] != "Trưởng Làng"
            or d["mayor_revealed"] or game.state == "ended"):
        return False
    d["mayor_revealed"] = True
    await game.channel.send(f"👑 **{d['member'].mention} đã công khai là TRƯỞNG LÀNG!** Phiếu của họ từ giờ tính **2**.")
    return True


# ==========================================
# HANDLER KỸ NĂNG ĐÊM (dùng với PickView / ConfirmView)
# ==========================================
def h_model(game, u, label):
    def h(vals):
        game.players[u]["model"] = vals[0]
        return f"✅ Đã chọn **{game.name(vals[0])}** làm {label}."
    return h


def h_steal(game, u):
    def h(vals):
        game.night["steal"] = (u, vals[0])
        return f"🥷 Bạn sẽ cướp vai trò của **{game.name(vals[0])}** khi đêm kết thúc."
    return h


def h_doctor(game, u):
    def h(vals):
        t = vals[0]
        d = game.players[u]
        if t == u:
            if d["self_used"]:
                raise ValueError("Bạn đã tự cứu mình 1 lần rồi. Hãy chọn người khác.")
            d["self_used"] = True
        game.night["protected"].add(t)
        game.visit(u, t)
        return f"💉 Đã cứu **{game.name(t)}** đêm nay."
    return h


def h_priest(game, u):
    def h(vals):
        t = vals[0]
        game.players[u]["used"] = True
        game.night["blessed"].add(t)
        game.visit(u, t)
        return f"✝️ Đã ban phước cho **{game.name(t)}**."
    return h


def h_fairy(game, u):
    def h(vals):
        t = vals[0]
        game.players[u]["used"] = True
        game.players[t]["shield"] = True
        game.visit(u, t)
        return f"🧚 Đã ban lớp bảo hộ cho **{game.name(t)}**."
    return h


def h_martyr(game, u):
    def h(vals):
        game.night["martyr"][u] = vals[0]
        game.visit(u, vals[0])
        return f"🕯️ Đêm nay bạn sẵn sàng chết thay cho **{game.name(vals[0])}**."
    return h


def h_gunner(game, u):
    def h(vals):
        game.players[u]["bullets"] -= 1
        game.night["shots"].append((u, vals[0]))
        game.visit(u, vals[0])
        return f"🔫 Đã nhắm bắn **{game.name(vals[0])}**. Còn **{game.players[u]['bullets']}** viên đạn."
    return h


def h_hunt(game, u):
    def h(vals):
        game.players[u]["used"] = True
        game.night["hunts"].append((u, vals[0]))
        game.visit(u, vals[0])
        return f"🪓 Đã nhắm vào **{game.name(vals[0])}**. Hy vọng họ là Sói..."
    return h


def h_slay(game, u):
    def h(vals):
        game.night["slay"] = vals[0]
        game.visit(u, vals[0])
        return f"🔪 Đêm nay bạn sẽ giết **{game.name(vals[0])}**."
    return h


def h_watch(game, u):
    def h(vals):
        game.night["watch"][u] = vals[0]
        return f"👁️ Đêm nay bạn theo dõi **{game.name(vals[0])}**."
    return h


def h_track(game, u):
    def h(vals):
        game.night["track"][u] = vals[0]
        return f"🐾 Đêm nay bạn theo dấu **{game.name(vals[0])}**."
    return h


def h_press(game, u):
    def h(vals):
        game.players[u]["used"] = True
        game.night["press"] = vals[0]
        return f"📰 Bài báo về **{game.name(vals[0])}** sẽ lên trang vào sáng mai."
    return h


def h_exact_role(game, u, icon, use_once=False):
    def h(vals):
        t = vals[0]
        if use_once:
            game.players[u]["used"] = True
        return f"{icon} **{game.name(t)}** là **{public_role(game.players[t]['role'])}**."
    return h


def h_muter(game, u):
    def h(vals):
        game.night["muted"].add(vals[0])
        return f"🤐 **{game.name(vals[0])}** sẽ không thể bỏ phiếu ngày mai."
    return h


def h_lawyer(game, u):
    def h(vals):
        game.players[u]["used"] = True
        game.night["lawyer"] = vals[0]
        return f"👨‍⚖️ Ngày mai **{game.name(vals[0])}** sẽ không thể bị treo cổ."
    return h


def h_grave(game, u):
    def h(vals):
        t = vals[0]
        return f"⚰️ **{game.name(t)}** thuộc **{faction_of(game.players[t]['role'])}**."
    return h


def hc_smith(game, u):
    def h():
        game.players[u]["used"] = True
        game.night["no_wolf"] = True
        return "🔨 Dao bạc đã được rèn. Đêm nay Sói không thể tấn công ai."
    return h


def hc_peace(game, u):
    def h():
        game.players[u]["used"] = True
        game.night["peace"] = True
        return "☮️ Bạn đã kêu gọi hòa bình. Ngày mai sẽ không ai bị treo cổ."
    return h


# ==========================================
# CÁI CHẾT: người yêu, thợ săn, các hiệu ứng khi chết
# ==========================================
async def hunter_prompt(game, hid):
    if len(game.alive_ids()) == 0:
        return None
    await game.channel.send(f"🏹 Thợ Săn **{game.mention(hid)}** đã chết và đang chọn phát bắn cuối...")
    game.begin_phase()
    view = HunterView(game, hid)
    await ask(game, hid,
              "🏹 **THỢ SĂN — PHÁT BẮN CUỐI**\nBạn đã chết nhưng còn 1 phát bắn. Chọn người còn sống hoặc bỏ qua.",
              view)
    left = await game.wait_pending(HUNTER_TIMEOUT)
    await game.end_phase()
    if hid in left:
        await penalize(game, [hid], "không dùng phát bắn Thợ Săn (hết giờ).")
        return None
    return view.target


async def on_player_death(game, uid):
    """Hiệu ứng khi 1 người chết: Sói Con, Người Kế Vị, Đứa Trẻ Hoang, Kẻ Bắt Chước."""
    P = game.players
    role = P[uid]["role"]

    if role == "Sói Con":
        game.cub_revenge = True
        await game.channel.send("😡 **Sói Con đã chết!** Bầy Sói nổi cơn thịnh nộ — đêm tới chúng sẽ cắn **2 người**.")

    if role == "Trưởng Làng":
        heir = next((u for u, x in P.items() if x["alive"] and x["role"] == "Người Kế Vị"), None)
        if heir is not None:
            P[heir]["role"] = "Trưởng Làng"
            P[heir]["mayor_revealed"] = True
            await game.channel.send(
                f"🎖️ **{game.mention(heir)}** là **Người Kế Vị** và đã lên thay làm **TRƯỞNG LÀNG**! Phiếu tính **2**.")

    for u, x in list(P.items()):
        if not x["alive"]:
            continue
        if x["role"] == "Đứa Trẻ Hoang" and x.get("model") == uid:
            await become_wolf(game, u, "🧒 Hình mẫu của bạn đã chết — bạn **hóa thành Sói**!")
        elif x["role"] == "Kẻ Bắt Chước" and x.get("model") == uid:
            apply_role(x, role)
            if is_wolf(role) and u not in game.wolves:
                game.wolves.append(u)
            embed = role_embed(game, u)
            embed.description = f"🎭 **{game.name(uid)}** đã chết — bạn nhận lấy vai trò của họ!"
            await send_dm(game, u, embed=embed)


async def convert_traitors(game):
    """Hết Sói thật thì Kẻ Nội Gián lộ nguyên hình thành Sói."""
    P = game.players
    if any(d["alive"] and is_wolf(d["role"]) for d in P.values()):
        return
    for u, d in P.items():
        if d["alive"] and d["role"] == "Kẻ Nội Gián":
            await become_wolf(game, u, "🎩 Bầy Sói đã tàn lụi — bạn lộ nguyên hình và **trở thành Sói**!")


async def process_deaths(game, first_deaths):
    """Xử lý dây chuyền: hiệu ứng vai trò, người yêu chết theo, thợ săn bắn."""
    queue = list(first_deaths)
    while queue:
        uid = queue.pop(0)
        d = game.players[uid]
        await on_player_death(game, uid)
        lover = d.get("lover")
        if lover and game.players[lover]["alive"]:
            game.players[lover]["alive"] = False
            await game.channel.send(f"💔 **BI KỊCH TÌNH YÊU:** {game.mention(lover)} đã chết theo người yêu {game.mention(uid)}.")
            queue.append(lover)
        if d["role"] == "Thợ Săn" and not d["hunter_used"]:
            d["hunter_used"] = True
            target = await hunter_prompt(game, uid)
            if target and game.players[target]["alive"]:
                game.players[target]["alive"] = False
                await game.channel.send(
                    f"🏹 **THỢ SĂN PHẢN CÔNG!** {game.mention(uid)} bắn hạ {game.mention(target)}.\n"
                    f"🎭 Vai trò: **{public_role(game.players[target]['role'])}**")
                queue.append(target)
    await convert_traitors(game)


def check_win(game):
    if game.special_win:
        return game.special_win
    alive = [d for d in game.players.values() if d["alive"]]
    if not alive:
        return "🌑 **HÒA!** Không còn ai sống sót."
    w = sum(1 for d in alive if is_wolf_side(d["role"]))
    k = sum(1 for d in alive if d["role"] == "Sát Nhân Hàng Loạt")
    o = len(alive) - w - k
    if w == 0 and k == 0:
        return "🎉 **PHE DÂN CHIẾN THẮNG!** Tất cả Sói đã bị loại."
    if w == 0 and k > 0 and o <= 1:
        return "🔪 **SÁT NHÂN HÀNG LOẠT CHIẾN THẮNG!** Hắn là kẻ cuối cùng còn đứng vững."
    if k == 0 and w >= o:
        return "🐺 **PHE SÓI CHIẾN THẮNG!** Sói đã đạt thế áp đảo."
    return None


# ==========================================
# BAN ĐÊM
# ==========================================
def build_night_jobs(game):
    """Tạo danh sách lựa chọn DM cho mọi vai trò cần hành động đêm nay (trừ Phù Thủy)."""
    P, jobs = game.players, []
    first = game.first_night

    def A(u, text, view):
        jobs.append(ask(game, u, text, view))

    def alive_pick(u, text, placeholder, emoji, handler, skip=False, include_self=False):
        opts = alive_options(game, () if include_self else {u}, emoji)
        if opts:
            A(u, text, PickView(game, u, placeholder, opts, handler, skip=skip))

    def dead_exists(u):
        return any(not x["alive"] and k != u for k, x in P.items())

    for u, d in P.items():
        if not d["alive"]:
            continue
        r = d["role"]

        # ---------- chỉ đêm đầu ----------
        if first:
            if r == "Cupid":
                if len(alive_options(game, {u})) >= 2:
                    A(u, "💘 **CUPID — ĐÊM ĐẦU**\nChọn đúng 2 người để ghép đôi.", CupidView(game, u))
            elif r == "Đứa Trẻ Hoang":
                alive_pick(u, "🧒 **ĐỨA TRẺ HOANG — ĐÊM ĐẦU**\nChọn 1 người làm hình mẫu. Hình mẫu chết thì bạn hóa thành Sói.",
                           "🧒 Chọn hình mẫu", "🧒", h_model(game, u, "hình mẫu"))
            elif r == "Kẻ Bắt Chước":
                alive_pick(u, "🎭 **KẺ BẮT CHƯỚC — ĐÊM ĐẦU**\nChọn 1 người. Khi họ chết, bạn nhận vai trò của họ.",
                           "🎭 Chọn người để bắt chước", "🎭", h_model(game, u, "đối tượng bắt chước"))
            elif r == "Tên Trộm":
                alive_pick(u, "🥷 **TÊN TRỘM — ĐÊM ĐẦU**\nChọn 1 người để cướp vai trò (họ sẽ thành Dân Làng).",
                           "🥷 Chọn nạn nhân", "🥷", h_steal(game, u))
            elif r == "Nhân Chứng":
                pure = [x for x, y in P.items() if y["alive"] and y["role"] == "Dân Làng" and x != u]
                if pure:
                    jobs.append(send_dm(game, u, content=(
                        f"🧾 **NHÂN CHỨNG:** **{game.name(random.choice(pure))}** chắc chắn là Dân Làng thuần túy.")))

        # ---------- mọi đêm ----------
        if r in ("Tiên Tri", "Tiên Tri Say"):
            A(u, "🔮 **TIÊN TRI:** Chọn 1 người để soi.", SeerView(game, u))
        elif r == "Bảo Vệ":
            A(u, "🛡️ **BẢO VỆ:** Chọn người cần bảo vệ (không quá 3 đêm liên tiếp cùng 1 người).", GuardView(game, u))
        elif r == "Bác Sĩ":
            alive_pick(u, "💉 **BÁC SĨ:** Chọn người cần cứu đêm nay (được tự cứu mình 1 lần).",
                       "💉 Chọn người cần cứu", "💉", h_doctor(game, u), include_self=not d["self_used"])
        elif r == "Thám Tử":
            if len(alive_options(game, {u})) >= 2:
                A(u, "🕵️ **THÁM TỬ:** Chọn 2 người để biết cùng phe hay khác phe.", DetectiveView(game, u))
        elif r == "Nhà Tiên Tri":
            if dead_exists(u):
                A(u, "📜 **NHÀ TIÊN TRI:** Chọn 1 người đã chết để xem vai trò.", MediumView(game, u))
        elif r == "Người Đào Mộ":
            opts = dead_options(game, {u}, "⚰️")
            if opts:
                A(u, "⚰️ **NGƯỜI ĐÀO MỘ:** Chọn 1 người đã chết để biết họ thuộc phe nào.",
                  PickView(game, u, "⚰️ Chọn người đã chết", opts, h_grave(game, u)))
        elif r == "Pháp Sư" and not d["used"]:
            alive_pick(u, "🪄 **PHÁP SƯ (1 lần/ván):** Xem vai trò chính xác của 1 người, hoặc bỏ qua để dành.",
                       "🪄 Chọn người để xem vai trò", "🪄", h_exact_role(game, u, "🪄", use_once=True), skip=True)
        elif r == "Người Quan Sát":
            alive_pick(u, "👁️ **NGƯỜI QUAN SÁT:** Chọn 1 người để biết đêm nay ai ghé thăm họ.",
                       "👁️ Chọn người theo dõi", "👁️", h_watch(game, u))
        elif r == "Thợ Dò":
            alive_pick(u, "🐾 **THỢ DÒ:** Chọn 1 người để biết đêm nay họ ghé thăm ai.",
                       "🐾 Chọn người theo dấu", "🐾", h_track(game, u))
        elif r == "Nhà Báo" and not d["used"]:
            alive_pick(u, "📰 **NHÀ BÁO (1 lần/ván):** Chọn 1 người để đăng báo họ có phải Sói không, hoặc bỏ qua.",
                       "📰 Chọn người để điều tra", "📰", h_press(game, u), skip=True)
        elif r == "Linh Mục" and not d["used"]:
            alive_pick(u, "✝️ **LINH MỤC (1 lần/ván):** Ban phước cho 1 người (miễn nhiễm mọi cái chết đêm nay), hoặc bỏ qua.",
                       "✝️ Chọn người được ban phước", "✝️", h_priest(game, u), skip=True, include_self=True)
        elif r == "Thợ Rèn" and not d["used"]:
            A(u, "🔨 **THỢ RÈN (1 lần/ván):** Rèn dao bạc để Sói không thể tấn công đêm nay?",
              ConfirmView(game, u, "Rèn dao bạc", "🔨", hc_smith(game, u)))
        elif r == "Bà Tiên" and not d["used"]:
            alive_pick(u, "🧚 **BÀ TIÊN (1 lần/ván):** Ban lớp bảo hộ (chặn 1 đòn tấn công ban đêm) cho 1 người, hoặc bỏ qua.",
                       "🧚 Chọn người được bảo hộ", "🧚", h_fairy(game, u), skip=True)
        elif r == "Người Hiến Thân":
            alive_pick(u, "🕯️ **NGƯỜI HIẾN THÂN:** Chọn 1 người — nếu họ bị Sói cắn, bạn chết thay. Hoặc bỏ qua.",
                       "🕯️ Chọn người để hy sinh vì", "🕯️", h_martyr(game, u), skip=True)
        elif r == "Xạ Thủ" and d["bullets"] > 0:
            alive_pick(u, f"🔫 **XẠ THỦ:** Còn **{d['bullets']}** viên đạn. Chọn người để bắn, hoặc bỏ qua.",
                       "🔫 Chọn mục tiêu", "🔫", h_gunner(game, u), skip=True)
        elif r == "Thợ Săn Sói" and not d["used"]:
            alive_pick(u, "🪓 **THỢ SĂN SÓI (1 lần/ván):** Bắn 1 người. Trúng Sói thì họ chết, bắn nhầm thì BẠN chết. Hoặc bỏ qua.",
                       "🪓 Chọn mục tiêu", "🪓", h_hunt(game, u), skip=True)
        elif r == "Sát Nhân Hàng Loạt":
            alive_pick(u, "🔪 **SÁT NHÂN HÀNG LOẠT:** Chọn người để giết đêm nay.",
                       "🔪 Chọn nạn nhân", "🔪", h_slay(game, u))
        elif r == "Người Hòa Giải" and not d["used"]:
            A(u, "☮️ **NGƯỜI HÒA GIẢI (1 lần/ván):** Kêu gọi hòa bình để ngày mai không ai bị treo cổ?",
              ConfirmView(game, u, "Kêu gọi hòa bình", "☮️", hc_peace(game, u)))
        elif r == "Luật Sư" and not d["used"]:
            alive_pick(u, "👨‍⚖️ **LUẬT SƯ (1 lần/ván):** Bào chữa cho 1 người (ngày mai họ không thể bị treo cổ), hoặc bỏ qua.",
                       "👨‍⚖️ Chọn người được bào chữa", "👨‍⚖️", h_lawyer(game, u), skip=True, include_self=True)
        elif is_wolf(r):
            A(u, "🐺 **HỘI SÓI:** Chọn nạn nhân. Mỗi Sói 1 phiếu.", WolfView(game, u))
            if r == "Sói Tiên Tri":
                alive_pick(u, "🔭 **SÓI TIÊN TRI:** Chọn 1 người để xem vai trò chính xác.",
                           "🔭 Chọn người để soi", "🔭", h_exact_role(game, u, "🔭"))
            elif r == "Sói Bịt Miệng":
                opts = [discord.SelectOption(label=x["member"].display_name[:100], value=str(k), emoji="🤐")
                        for k, x in P.items() if x["alive"] and not is_wolf(x["role"])][:25]
                if opts:
                    A(u, "🤐 **SÓI BỊT MIỆNG:** Chọn 1 người — họ không thể bỏ phiếu ngày mai.",
                      PickView(game, u, "🤐 Chọn người bị bịt miệng", opts, h_muter(game, u)))
    return jobs


def pick_wolf_targets(game):
    """Chốt nạn nhân của bầy Sói (2 người nếu Sói Con đã chết)."""
    P, n = game.players, game.night
    if n["no_wolf"]:
        return []
    tally = {}
    for voter, t in n["wolf_votes"].items():
        w = 2 if P[voter]["role"] == "Sói Chúa" else 1
        tally[t] = tally.get(t, 0) + w
    order = sorted(tally, key=lambda u: (-tally[u], random.random()))
    targets = order[:2] if game.cub_revenge else order[:1]
    if game.cub_revenge and targets and len(targets) < 2:
        extras = [u for u, d in P.items() if d["alive"] and not is_wolf(d["role"]) and u not in targets]
        if extras:
            targets.append(random.choice(extras))
    return targets


async def night_phase(game):
    game.state = "night"
    game.begin_phase()
    game.night = {
        "wolf_votes": {}, "guard": None, "save": False, "poison": None,
        "wolf_target": None, "wolf_targets": [],
        "protected": set(), "blessed": set(), "no_wolf": False,
        "slay": None, "shots": [], "hunts": [],
        "watch": {}, "track": {}, "visits": [], "martyr": {},
        "muted": set(), "press": None, "lawyer": None, "peace": False, "steal": None,
    }
    started = time.time()

    await game.channel.send(embed=ui_panel(
        "ĐÊM XUỐNG",
        "🌙 Các vai trò đang hành động **qua DM với KS-BOT**.\n"
        "⚡ Đêm sẽ kết thúc ngay khi mọi người đã chọn xong.\n"
        f"⏳ Tối đa **{NIGHT_TIMEOUT}s** — ai không chọn sẽ bị cấm chơi {BAN_MINUTES} phút.",
        color="dark", emoji="🌙"))

    P = game.players
    jobs = build_night_jobs(game)
    if jobs:
        await asyncio.gather(*jobs)

    idle = await game.wait_pending(NIGHT_TIMEOUT)
    await game.end_phase()

    # --- chốt nạn nhân của Sói ---
    targets = pick_wolf_targets(game)
    game.night["wolf_targets"] = targets
    game.night["wolf_target"] = targets[0] if targets else None
    victim = game.night["wolf_target"]

    # --- Phù Thủy (cần biết nạn nhân nên chạy sau) ---
    witch = next((u for u, d in P.items() if d["alive"] and d["role"] == "Phù Thủy"), None)
    if witch:
        d = P[witch]
        can_heal = bool(victim) and not d["heal_used"]
        can_poison = not d["poison_used"]
        if can_heal or can_poison:
            game.begin_phase()
            if victim:
                if len(targets) > 1:
                    txt = (f"🧪 **PHÙ THỦY**\n🐺 Sói đang nhắm vào **{game.name(targets[0])}** và **{game.name(targets[1])}**.\n"
                           f"💚 Bình Cứu chỉ cứu được **{game.name(victim)}**.\n"
                           "💚 Cứu và ☠️ Độc là 2 bình độc lập, có thể dùng cả hai trong cùng đêm.")
                else:
                    txt = (f"🧪 **PHÙ THỦY**\n🐺 Sói đang nhắm vào **{game.name(victim)}**.\n"
                           "💚 Cứu và ☠️ Độc là 2 bình độc lập, có thể dùng cả hai trong cùng đêm.")
            else:
                txt = "🧪 **PHÙ THỦY**\nĐêm nay Sói chưa chọn được nạn nhân. Bạn vẫn có thể dùng ☠️ Bình Độc."
            await ask(game, witch, txt, WitchView(game, witch, victim))
            idle |= await game.wait_pending(WITCH_TIMEOUT)
            await game.end_phase()

    if idle:
        await penalize(game, idle, "không chọn kỹ năng ban đêm (hết giờ).")

    # --- đêm tối thiểu vài giây ---
    wait = MIN_NIGHT_SECONDS - (time.time() - started)
    if wait > 0:
        await asyncio.sleep(wait)

    await resolve_night(game)


async def resolve_night(game):
    P = game.players
    n = game.night
    victims = n["wolf_targets"]
    guard = n["guard"]
    protected = set(n["protected"])
    if guard is not None:
        protected.add(guard)
    blessed = n["blessed"]

    deaths = {}          # uid -> lý do (giữ thứ tự)
    targeted = set()     # những người bị nhắm tới (cho Người Gác Cổng)
    wolf_killed = set()  # những người chết vì Sói

    async def attack(v, reason, by_wolf=False, first=False):
        if v is None or not P[v]["alive"] or v in deaths:
            return
        targeted.add(v)
        if v in blessed or v in protected:
            return
        if by_wolf:
            if first and n["save"]:
                return
            mart = next((m for m, t in n["martyr"].items()
                         if t == v and P[m]["alive"] and m != v and m not in deaths), None)
            if mart is not None:
                deaths[mart] = f"đã hy sinh thay cho {game.name(v)}"
                wolf_killed.add(mart)
                return
            if P[v]["role"] == "Kẻ Bị Nguyền":
                await become_wolf(game, v, "🌑 Bạn bị Sói cắn nhưng không chết — lời nguyền biến bạn thành **Sói**!")
                return
        if P[v]["shield"]:
            P[v]["shield"] = False
            await send_dm(game, v, content="🛡️ Bạn vừa thoát chết nhờ lớp bảo hộ đặc biệt (chỉ dùng được 1 lần).")
            return
        deaths[v] = reason
        if by_wolf:
            wolf_killed.add(v)

    # Hiệp Sĩ Gỉ Sét: lưỡi kiếm gỉ phát tác từ đêm trước
    for _ in range(game.knight_pending):
        ws = [u for u, d in P.items() if d["alive"] and is_wolf(d["role"]) and u not in deaths]
        if ws:
            deaths[random.choice(ws)] = "chết vì lưỡi kiếm gỉ của Hiệp Sĩ"
    game.knight_pending = 0

    # Sói tấn công
    for i, v in enumerate(victims):
        for w, t in n["wolf_votes"].items():
            if t == v:
                game.visit(w, v)
        await attack(v, "bị Sói tấn công", by_wolf=True, first=(i == 0))

    # Sát Nhân Hàng Loạt
    await attack(n["slay"], "bị Sát Nhân Hàng Loạt giết")

    # Xạ Thủ
    for shooter, t in n["shots"]:
        await attack(t, f"bị Xạ Thủ {game.name(shooter)} bắn hạ")

    # Thợ Săn Sói
    for hunter, t in n["hunts"]:
        if not P[t]["alive"]:
            continue
        targeted.add(t)
        if is_wolf_side(P[t]["role"]):
            deaths.setdefault(t, f"bị Thợ Săn Sói {game.name(hunter)} hạ gục")
        else:
            deaths.setdefault(hunter, "bắn nhầm người vô tội và gục ngã vì hối hận")

    # Bình Độc của Phù Thủy
    poison = n["poison"]
    if poison is not None and P[poison]["alive"] and poison not in deaths and poison not in blessed:
        targeted.add(poison)
        if P[poison]["role"] == "Người Nếm Thử":
            await send_dm(game, poison, content="🍷 **NGƯỜI NẾM THỬ:** Bạn nhận ra vị đắng trong ly nước — bạn miễn nhiễm với độc!")
        else:
            deaths[poison] = "trúng độc"

    # Hiệu ứng khi bị Sói cắn chết
    for v in list(wolf_killed):
        role = P[v]["role"]
        if role == "Hiệp Sĩ Gỉ Sét":
            game.knight_pending += 1
        elif role == "Kẻ Báo Thù":
            ws = [u for u, d in P.items() if d["alive"] and is_wolf(d["role"]) and u not in deaths]
            if ws:
                deaths[random.choice(ws)] = "bị Kẻ Báo Thù kéo theo xuống mồ"

    # Người Gác Cổng
    for u, d in P.items():
        if d["alive"] and d["role"] == "Người Gác Cổng" and u in targeted:
            await send_dm(game, u, content="🚪 **GÁC CỔNG:** Đêm qua bạn cảm nhận có người nhắm tới mình. Bạn không biết ai.")

    if guard is not None:
        game.guard_history.append(guard)
        game.guard_history = game.guard_history[-2:]

    # Áp dụng cái chết
    lines = []
    dead_ids = []
    for u, reason in deaths.items():
        if P[u]["alive"]:
            P[u]["alive"] = False
            dead_ids.append(u)
            lines.append(f"• {game.mention(u)} — {reason}")

    if lines:
        text = "☀️ **BÌNH MINH** — đêm qua có người đã chết:\n" + "\n".join(lines)
    else:
        text = "☀️ **BÌNH MINH** — đêm qua không có ai chết. Một đêm hiếm hoi bình yên..."
    if n["no_wolf"]:
        text += "\n🔨 *Một lưỡi dao bạc đã được rèn trong đêm — Sói không thể ra tay.*"
    await game.channel.send(embed=ui_panel("BÌNH MINH", text, color="warning", emoji="☀️"))

    # Nhà Báo
    if n["press"] is not None:
        t = n["press"]
        verdict = "**CÓ DẤU HIỆU SÓI** 🐺" if appears_wolf(P[t]["role"]) else "**KHÔNG PHẢI SÓI** 🟢"
        await game.channel.send(f"📰 **SỐ BÁO SÁNG NAY (ẩn danh):** {game.mention(t)} — {verdict}")

    await process_deaths(game, dead_ids)

    # Tên Trộm cướp vai
    if n["steal"]:
        thief, t = n["steal"]
        if thief != t and P[thief]["alive"] and P[t]["alive"]:
            stolen = P[t]["role"]
            apply_role(P[thief], stolen)
            apply_role(P[t], "Dân Làng")
            game.wolves = [u for u, d in P.items() if is_wolf(d["role"])]
            embed = role_embed(game, thief)
            embed.description = f"🥷 Bạn đã cướp vai trò của **{game.name(t)}**!"
            await send_dm(game, thief, embed=embed)
            await send_dm(game, t, content="🥷 Vai trò của bạn đã bị Tên Trộm cướp mất. Bạn giờ là **Dân Làng**.")

    # Người Quan Sát / Thợ Dò
    for u, t in n["watch"].items():
        if P[u]["alive"]:
            vis = list(dict.fromkeys(game.name(a) for a, b in n["visits"] if b == t and a != u))
            txt = (f"👁️ **NGƯỜI QUAN SÁT:** Đêm qua **{game.name(t)}** được ghé thăm bởi: " + ", ".join(vis)) if vis \
                else f"👁️ **NGƯỜI QUAN SÁT:** Đêm qua không ai ghé thăm **{game.name(t)}**."
            await send_dm(game, u, content=txt)
    for u, t in n["track"].items():
        if P[u]["alive"]:
            where = list(dict.fromkeys(game.name(b) for a, b in n["visits"] if a == t))
            txt = (f"🐾 **THỢ DÒ:** Đêm qua **{game.name(t)}** đã ghé thăm: " + ", ".join(where)) if where \
                else f"🐾 **THỢ DÒ:** Đêm qua **{game.name(t)}** không đi đâu cả."
            await send_dm(game, u, content=txt)

    # Nhà Toán Học / Cô Bé Rình Mò
    alive_wolves = [u for u, d in P.items() if d["alive"] and is_wolf(d["role"])]
    for u, d in P.items():
        if not d["alive"]:
            continue
        if d["role"] == "Nhà Toán Học":
            await send_dm(game, u, content=f"🧮 **NHÀ TOÁN HỌC:** Hiện còn **{len(alive_wolves)}** Sói đang sống.")
        elif d["role"] == "Cô Bé Rình Mò" and alive_wolves and random.random() < 0.25:
            await send_dm(game, u, content=f"👧 **CÔ BÉ RÌNH MÒ:** Bạn hé mắt nhìn và thấy một bóng đen... đó là **{game.name(random.choice(alive_wolves))}**!")

    # Hiệu ứng kéo sang ban ngày
    game.muted = {u for u in n["muted"] if P[u]["alive"]}
    for u in game.muted:
        await send_dm(game, u, content="🤐 Bạn bị Sói bịt miệng: hôm nay bạn **không thể bỏ phiếu**.")
    game.lawyer_shield = n["lawyer"]
    game.peace_today = bool(n["peace"])
    game.first_night = False

    # Trưởng Làng được hỏi có muốn lộ không (không chặn game)
    for u, d in P.items():
        if d["alive"] and d["role"] == "Trưởng Làng" and not d["mayor_revealed"]:
            await send_dm(game, u, embed=discord.Embed(
                title="👑 TRƯỞNG LÀNG — HẾT ĐÊM",
                description=("Bạn có muốn **lộ thân phận** không?\n\n"
                             "👑 **Lộ:** mọi người biết bạn là Trưởng Làng, phiếu của bạn tính **2**.\n"
                             "🤫 **Chưa lộ:** tiếp tục giữ bí mật.\n\n"
                             "⚠️ Lộ sớm có thể khiến Sói nhắm tới bạn."),
                color=0xf1c40f), view=MayorView(game, u))


# ==========================================
# BAN NGÀY + VOTE
# ==========================================
async def day_phase(game):
    game.state = "day"
    mentions = " ".join(game.mention(u) for u in game.alive_ids())
    embed = ui_panel("BAN NGÀY — THẢO LUẬN",
                     f"🗣️ **Còn sống:** {mentions}\n\n💬 Thảo luận tự do để tìm ra Sói.",
                     color="warning", emoji="☀️")
    msg = await game.channel.send(embed=embed)
    remaining = DAY_SECONDS
    while remaining > 0:
        embed.description = (f"🗣️ **Còn sống:** {mentions}\n\n💬 Thảo luận tự do để tìm ra Sói.\n"
                             f"⏱️ Còn **{remaining}s** rồi bỏ phiếu.")
        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            pass
        step = min(5, remaining)
        await asyncio.sleep(step)
        remaining -= step
    try:
        await msg.delete()
    except discord.HTTPException:
        pass


def voters(game):
    """Những người đang có quyền bỏ phiếu hôm nay."""
    return [u for u in game.alive_ids()
            if u not in game.muted and not game.players[u].get("silenced")]


def vote_tally(game):
    tally, blank = {}, 0
    for voter, target in game.votes.items():
        if target == "blank":
            blank += 1
            continue
        d = game.players.get(voter, {})
        w = 2 if d.get("role") == "Trưởng Làng" and d.get("mayor_revealed") else 1
        tally[target] = tally.get(target, 0) + w
    return tally, blank


def vote_text(game):
    tally, blank = vote_tally(game)
    rows = [f"• {game.mention(u)} — **{tally.get(u, 0)} phiếu**" for u in game.alive_ids()]
    rows.append(f"• 🏳️ Phiếu trắng — **{blank}**")
    return "\n".join(rows)


class VoteView(discord.ui.View):
    def __init__(self, game, on_change):
        super().__init__(timeout=None)
        self.game = game
        self.on_change = on_change
        opts = [discord.SelectOption(label=d["member"].display_name[:100], value=str(u), emoji="⚖️")
                for u, d in game.players.items() if d["alive"]][:24]
        opts.append(discord.SelectOption(label="Phiếu trắng", value="blank", emoji="🏳️"))
        self.sel = discord.ui.Select(placeholder="⚖️ Chọn người để treo cổ (hoặc phiếu trắng)", options=opts)
        self.sel.callback = self.pick
        self.add_item(self.sel)

    async def pick(self, interaction):
        g = self.game
        voter = interaction.user.id
        if voter not in g.players or not g.players[voter]["alive"]:
            await interaction.response.send_message("❌ Bạn không có quyền vote.", ephemeral=True)
            return
        if voter not in voters(g):
            await interaction.response.send_message("🤐 Hôm nay bạn không thể bỏ phiếu (bị bịt miệng hoặc đã mất quyền vote).", ephemeral=True)
            return
        v = self.sel.values[0]
        target = "blank" if v == "blank" else int(v)
        g.votes[voter] = target
        label = "🏳️ **Phiếu trắng**" if target == "blank" else f"**{g.name(target)}**"
        await interaction.response.send_message(f"✅ Đã ghi nhận phiếu của bạn: {label}. (Chọn lại để đổi phiếu)", ephemeral=True)
        if all(u in g.votes for u in voters(g)):
            g.vote_event.set()
        await self.on_change()


async def vote_phase(game):
    game.state = "vote"
    game.votes = {}
    game.vote_event = asyncio.Event()
    if not voters(game):
        game.vote_event.set()
    deadline = time.time() + VOTE_SECONDS
    embed = ui_panel("BỎ PHIẾU TREO CỔ", "", color="danger", emoji="⚖️")

    def render():
        left = max(0, int(deadline - time.time()))
        extra = ""
        if game.muted:
            extra = f"\n🤐 Có **{len(game.muted)}** người bị bịt miệng, không được vote hôm nay."
        embed.description = ("🔐 **Vote kín** — không hiện ai vote ai, chỉ hiện số phiếu.\n"
                             "👑 Trưởng Làng đã công khai = **2 phiếu**.\n"
                             "⚡ Vote kết thúc sớm khi mọi người đã bỏ phiếu."
                             f"{extra}\n\n"
                             f"📊 **SỐ PHIẾU HIỆN TẠI**\n{vote_text(game)}\n\n⏱️ Còn **{left}s**")

    msg_ref = {"msg": None}

    async def refresh():
        if msg_ref["msg"] is None:
            return
        render()
        try:
            await msg_ref["msg"].edit(embed=embed)
        except discord.HTTPException:
            pass

    view = VoteView(game, refresh)
    render()
    msg = await game.channel.send(embed=embed, view=view)
    msg_ref["msg"] = msg

    while time.time() < deadline and not game.vote_event.is_set():
        try:
            await asyncio.wait_for(game.vote_event.wait(), timeout=min(5, max(0.1, deadline - time.time())))
        except asyncio.TimeoutError:
            pass
        await refresh()

    view.stop()
    render()
    embed.title = "⚖️ HẾT GIỜ VOTE" if not game.vote_event.is_set() else "⚖️ ĐÃ VOTE XONG"
    try:
        await msg.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass

    # phạt người không vote (người bị bịt miệng / mất quyền vote không bị phạt)
    idle = [u for u in voters(game) if u not in game.votes]
    if idle:
        await penalize(game, idle, "không bỏ phiếu (kể cả phiếu trắng).")

    await resolve_voting(game)


async def resolve_voting(game):
    P = game.players
    tally, blank = vote_tally(game)
    dead = []
    if not tally:
        await game.channel.send(f"🏳️ Không ai bị treo cổ. Phiếu trắng: **{blank}**.")
    else:
        mx = max(tally.values())
        tops = [u for u, v in tally.items() if v == mx]

        # Thẩm Phán phá thế hòa
        if len(tops) > 1:
            judge = next((u for u, d in P.items()
                          if d["alive"] and d["role"] == "Thẩm Phán" and game.votes.get(u) in tops), None)
            if judge is not None:
                tops = [game.votes[judge]]
                await game.channel.send("⚖️ Hòa phiếu! Một **Thẩm Phán** bí mật đã đưa ra phán quyết phá thế hòa...")

        if len(tops) == 1:
            uid = tops[0]
            d = P[uid]
            if uid == game.lawyer_shield:
                await game.channel.send(
                    f"👨‍⚖️ {game.mention(uid)} nhận **{mx} phiếu** nhưng đã được **Luật Sư bào chữa** nên thoát án treo cổ. "
                    f"(Phiếu trắng: {blank})")
            elif d["role"] == "Anh Ngốc" and not d.get("silenced"):
                d["silenced"] = True
                await game.channel.send(
                    f"🤪 {game.mention(uid)} nhận **{mx} phiếu** nhưng hóa ra là **ANH NGỐC** — làng tha chết cho anh ta! "
                    f"Từ giờ anh ta **mất quyền bỏ phiếu**. (Phiếu trắng: {blank})")
            else:
                d["alive"] = False
                dead.append(uid)
                await game.channel.send(
                    f"⚖️ {game.mention(uid)} nhận **{mx} phiếu** và bị treo cổ. (Phiếu trắng: {blank})\n"
                    f"🎭 Vai trò: **{public_role(d['role'])}**")
                if d["role"] == "Kẻ Chán Đời":
                    game.special_win = f"🤡 **KẺ CHÁN ĐỜI CHIẾN THẮNG!** {d['member'].mention} đã khiến cả làng treo cổ mình!"
                if d["role"] == "Sói Báo Thù":
                    backers = [v for v, t in game.votes.items() if t == uid and P[v]["alive"] and v != uid]
                    if backers:
                        pick = random.choice(backers)
                        P[pick]["alive"] = False
                        dead.append(pick)
                        await game.channel.send(
                            f"🩸 **SÓI BÁO THÙ** kéo {game.mention(pick)} xuống mồ cùng mình!\n"
                            f"🎭 Vai trò: **{public_role(P[pick]['role'])}**")
        else:
            await game.channel.send(f"⚖️ Hòa phiếu ({mx} phiếu), không ai bị treo cổ. (Phiếu trắng: {blank})")
    game.lawyer_shield = None
    await process_deaths(game, dead)


# ==========================================
# VÒNG LẶP VÁN CHƠI
# ==========================================
async def end_game(game, win_message):
    game.state = "ended"
    await game.channel.send(win_message)
    lines = []
    for u, d in game.players.items():
        status = "🟢 Sống" if d["alive"] else "🔴 Chết"
        lines.append(f"{WW_ROLE_INFO[d['role']][0]} {d['member'].mention} — **{d['role']}** — {status}")
    await game.channel.send(embed=discord.Embed(
        title="📜 KẾT QUẢ VÁN MA SÓI", description="\n".join(lines), color=0x8e44ad))


async def play(game):
    try:
        await game.channel.send(embed=ui_panel(
            "MA SÓI ĐÃ BẮT ĐẦU",
            (f"👥 **{len(game.players)} người chơi**  •  🐺 **{len(game.wolves)} Sói**\n\n"
             "🔐 Vai trò + kỹ năng được gửi riêng qua **DM**.\n"
             f"📋 **Đội hình:**\n{role_count_text(game)}"),
            color="purple", emoji="🎭"))
        while True:
            await night_phase(game)
            win = check_win(game)
            if win:
                await end_game(game, win)
                return
            await day_phase(game)
            if game.peace_today:
                game.peace_today = False
                await game.channel.send(embed=ui_panel(
                    "NGÀY HÒA BÌNH",
                    "☮️ Có người đã kêu gọi hòa bình — **hôm nay không ai bị treo cổ**.",
                    color="purple", emoji="☮️"))
            else:
                await vote_phase(game)
            game.muted = set()
            game.lawyer_shield = None
            win = check_win(game)
            if win:
                await end_game(game, win)
                return
    except Exception:
        traceback.print_exc()
        try:
            await game.channel.send("❌ Đã có lỗi xảy ra, ván Ma Sói bị hủy.")
        except Exception:
            pass
    finally:
        game.state = "ended"
        try:
            await game.end_phase()
        except Exception:
            pass
        game.bot.game_lobbies.pop(game.channel.id, None)


def role_count_text(game):
    counts = {}
    for d in game.players.values():
        r = public_role(d["role"])
        counts[r] = counts.get(r, 0) + 1
    return "\n".join(f"{role_emoji(r)} **{r} × {c}**" for r, c in counts.items())


# ==========================================
# PHÒNG CHỜ
# ==========================================
def lobby_embed(game):
    plist = "\n".join(f"• {d['member'].mention}" for d in game.players.values()) or "Chưa có ai tham gia..."
    return discord.Embed(
        title="🐺 PHÒNG CHỜ MA SÓI",
        description=(f"Bấm **Tham Gia** để vào game.\n"
                     f"Chủ phòng bấm **Bắt Đầu** khi đủ người (tối thiểu {MIN_PLAYERS}, tối đa {MAX_PLAYERS}).\n"
                     "📩 Bạn phải **bật tin nhắn riêng (DM)** để nhận vai trò.\n"
                     f"🚫 Ai AFK / không chọn trong ván sẽ bị cấm chơi {BAN_MINUTES} phút.\n\n"
                     f"**Danh sách người chơi ({len(game.players)}/{MAX_PLAYERS}):**\n{plist}"),
        color=UI_COLORS["cyan"])


async def refresh_lobby(game):
    if game.lobby_message:
        try:
            await game.lobby_message.edit(embed=lobby_embed(game))
        except discord.HTTPException:
            pass


async def assign_roles(game):
    ids = list(game.players)
    random.shuffle(ids)
    roles = distribution(len(ids))
    game.wolves = []
    game.guard_history = []
    game.first_night = True
    game.muted = set()
    game.lawyer_shield = None
    game.peace_today = False
    game.knight_pending = 0
    game.cub_revenge = False
    game.special_win = None
    for uid, role in zip(ids, roles):
        d = game.players[uid]
        d.update({"alive": True, "lover": None, "model": None})
        apply_role(d, role)
        if is_wolf(role):
            game.wolves.append(uid)


async def send_role_dms(game):
    """Gửi vai trò qua DM. Trả về danh sách thành viên KHÔNG nhận được DM."""
    failed = []

    async def one(uid, d):
        try:
            await d["member"].send(embed=role_embed(game, uid))
        except discord.HTTPException:
            failed.append(d["member"])

    await asyncio.gather(*(one(u, d) for u, d in game.players.items()))
    return failed


class LobbyView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=None)
        self.game = game

    @discord.ui.button(label="Tham Gia", style=discord.ButtonStyle.green, emoji="🐺")
    async def join(self, interaction, button):
        g = self.game
        uid = interaction.user.id
        if g.state != "waiting":
            return await interaction.response.send_message("❌ Trò chơi đã bắt đầu, không thể tham gia lúc này!", ephemeral=True)
        if uid in g.players:
            return await interaction.response.send_message("❌ Bạn đã có trong danh sách rồi!", ephemeral=True)
        if len(g.players) >= MAX_PLAYERS:
            return await interaction.response.send_message(f"❌ Phòng đã đủ {MAX_PLAYERS} người.", ephemeral=True)
        left = ban_remaining(uid)
        if left:
            return await interaction.response.send_message(
                f"🚫 Bạn đang bị cấm chơi Ma Sói vì AFK. Còn **{fmt_remaining(left)}**.", ephemeral=True)
        g.players[uid] = {"member": interaction.user, "role": "Dân Làng", "alive": True}
        await interaction.response.send_message("✅ Bạn đã tham gia phòng chờ!", ephemeral=True)
        await refresh_lobby(g)

    @discord.ui.button(label="Rời Phòng", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction, button):
        g = self.game
        if g.state != "waiting":
            return await interaction.response.send_message("❌ Game đã bắt đầu, không thể rời phòng.", ephemeral=True)
        if interaction.user.id not in g.players:
            return await interaction.response.send_message("❌ Bạn chưa tham gia phòng.", ephemeral=True)
        del g.players[interaction.user.id]
        await interaction.response.send_message("🚪 Bạn đã rời phòng.", ephemeral=True)
        await refresh_lobby(g)

    @discord.ui.button(label="Bắt Đầu", style=discord.ButtonStyle.blurple, emoji="🎮")
    async def start(self, interaction, button):
        g = self.game
        if interaction.user.id != g.host_id:
            return await interaction.response.send_message("❌ Chỉ chủ phòng mới được bấm bắt đầu!", ephemeral=True)
        if g.state != "waiting":
            return await interaction.response.send_message("❌ Ván đang được khởi động.", ephemeral=True)
        if len(g.players) < MIN_PLAYERS:
            return await interaction.response.send_message(f"❌ Cần ít nhất {MIN_PLAYERS} người chơi!", ephemeral=True)

        g.state = "starting"
        await interaction.response.defer()
        await assign_roles(g)
        failed = await send_role_dms(g)
        if failed:
            g.state = "waiting"
            names = ", ".join(m.mention for m in failed)
            await interaction.followup.send(
                f"❌ Không gửi được DM cho: {names}\n"
                "👉 Hãy mở **Cài đặt → Quyền riêng tư → Cho phép tin nhắn riêng từ thành viên server** rồi bấm **Bắt Đầu** lại.")
            return

        try:
            await g.lobby_message.edit(view=None)
        except discord.HTTPException:
            pass
        g.task = asyncio.create_task(play(g))


# ==========================================
# COG
# ==========================================
class Werewolf(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, "game_lobbies"):
            bot.game_lobbies = {}

    @commands.hybrid_command(name="masoi", aliases=["wolf"])
    async def cmd_masoi(self, ctx):
        """Tạo phòng Ma Sói."""
        lobbies = self.bot.game_lobbies
        left = ban_remaining(ctx.author.id)
        if left:
            return await ctx.send(f"🚫 Bạn đang bị cấm chơi Ma Sói vì AFK. Còn **{fmt_remaining(left)}**.", delete_after=8)

        if ctx.channel.id in lobbies:
            game = lobbies[ctx.channel.id]
            if game.state != "waiting":
                return await ctx.send("❌ Ván chơi đang diễn ra, không thể tạo phòng mới!")
            try:
                if game.lobby_message:
                    await game.lobby_message.delete()
            except discord.HTTPException:
                pass
            game.ctx = ctx
            game.host_id = ctx.author.id
        else:
            game = WerewolfGame(ctx, self.bot)
            lobbies[ctx.channel.id] = game

        game.lobby_message = await ctx.send(embed=lobby_embed(game), view=LobbyView(game))
        add_exp_to_user(ctx.author.id, 20)

    @commands.hybrid_command(name="masoiroles", aliases=["msroles", "roles"])
    async def cmd_roles(self, ctx):
        """Xem bộ 50 vai trò Ma Sói."""
        colors = {"Phe Sói": 0xe74c3c, "Phe Dân": 0x2ecc71, "Phe Thứ Ba": 0xf1c40f}
        groups = {}
        for role, (emoji, faction, desc) in WW_ROLE_INFO.items():
            groups.setdefault(faction, []).append((f"{emoji} **{role}** (mở từ {OPTIONAL_ROLES.get(role, MIN_PLAYERS)}+ người)"
                                                   if role in OPTIONAL_ROLES else f"{emoji} **{role}**", desc))
        total = len(WW_ROLE_INFO)
        first = True
        for faction, items in groups.items():
            chunks, cur, size = [], [], 0
            for title, desc in items:
                line = f"{title}\n└ {desc}"
                if size + len(line) > 3600:
                    chunks.append(cur)
                    cur, size = [], 0
                cur.append(line)
                size += len(line) + 2
            if cur:
                chunks.append(cur)
            for i, chunk in enumerate(chunks, 1):
                suffix = f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""
                embed = discord.Embed(
                    title=f"🐺 BỘ VAI TRÒ MA SÓI — {faction.upper()}{suffix}",
                    description="\n\n".join(chunk), color=colors.get(faction, 0x5865f2))
                if first:
                    embed.set_author(name=f"Tổng cộng {total} vai trò • mở dần theo số người chơi")
                    first = False
                await ctx.send(embed=embed)

    @commands.hybrid_command(name="mav")
    async def cmd_mav(self, ctx):
        """Trưởng Làng tự công khai thân phận."""
        game = self.bot.game_lobbies.get(ctx.channel.id)
        if not game or game.state not in ("day", "vote"):
            return await ctx.send("❌ Lệnh này chỉ dùng trong ban ngày của Ma Sói.", delete_after=5)
        d = game.players.get(ctx.author.id)
        if not d or not d["alive"] or d["role"] != "Trưởng Làng":
            return await ctx.send("❌ Bạn không phải Trưởng Làng đang sống.", delete_after=5)
        if d["mayor_revealed"]:
            return await ctx.send("⚠️ Bạn đã công khai rồi.", delete_after=5)
        await reveal_mayor(game, ctx.author.id)


async def setup(bot):
    await bot.add_cog(Werewolf(bot))
