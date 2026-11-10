"""
top.py - Bảng xếp hạng đại gia dạng ẢNH (vẽ trực tiếp lên khung leaderboard_template.png)

Cần có trong thư mục assets/ (cạnh file này):
    leaderboard_template.png   <- ảnh khung bạn làm
    BeVietnamPro-Bold.ttf      <- font tiếng Việt (không có thì tự dùng DejaVu/Arial)

Cần: pip install pillow aiohttp

3 nút bên dưới ảnh:
    Server : người đang ở server này, xếp theo coin
    Tuần   : tất cả người đã dùng bot trong 7 ngày gần nhất (mọi server), xếp theo coin
    Tháng  : tất cả người đã dùng bot trong 30 ngày gần nhất (mọi server), xếp theo coin

Lưu ý: "đã dùng bot" được ghi từ lúc file này chạy (bảng top_activity trong DB),
nên Tuần/Tháng sẽ đầy dần theo thời gian.
"""
import asyncio
import io
import re
import sqlite3
import time
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from database import DB_NAME

BASE_DIR = Path(__file__).resolve().parent
ASSETS = BASE_DIR / "assets"
TEMPLATE_NAME = "top.png"
TEMPLATE_CANDIDATES = [ASSETS / TEMPLATE_NAME, BASE_DIR / TEMPLATE_NAME]


def template_path():
    for p in TEMPLATE_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Thiếu ảnh khung: hãy upload '{TEMPLATE_NAME}' vào thư mục 'assets' (hoặc cạnh top.py)")
FONT_CANDIDATES = [
    str(ASSETS / "BeVietnamPro-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
]

# ----------------------------------------------------------------------
# HÌNH HỌC KHUNG (tọa độ quy đổi theo template 1811x868 -> 1024x491)
# ----------------------------------------------------------------------
S = 2
W0, H0 = 1024, 491

# Bốn thanh hạng 4-7 ở cột trái
ROW_TOPS = [262, 308, 355, 404]
ROW_H = 41

# Cột trái: avatar + tên + coin
LEFT_TOP3 = {
    1: dict(c=(292, 166), r=41, name_y=232, coin_y=249, wmax=112),
    2: dict(c=(187, 185), r=33, name_y=234, coin_y=249, wmax=91),
    3: dict(c=(394, 185), r=33, name_y=234, coin_y=249, wmax=91),
}

# Cột phải: chỉ avatar, không vẽ tên/coin
RIGHT_AVATARS = {
    1: dict(c=(766, 238), r=67),
    2: dict(c=(608, 262), r=53),
    3: dict(c=(919, 265), r=53),
}

# Avatar nhỏ ở các thanh 4-7
ROW_AVATAR = dict(x=203, r=15)

TABS = [("server", "Server"), ("week", "Tuần"), ("month", "Tháng")]
TAB_DAYS = {"week": 7, "month": 30}

GOLD_TXT = (255, 214, 92)
NAVY_STROKE = (8, 20, 70)


def X(x):
    return int(round(x * S))


def Y(y):
    return int(round(y * S))


# ----------------------------------------------------------------------
# TIỆN ÍCH CHỮ / SỐ
# ----------------------------------------------------------------------
@lru_cache(maxsize=128)
def _font(px):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    return ImageFont.load_default(px)


@lru_cache(maxsize=512)
def _font_for_text(text, px):
    """Chọn font có độ phủ ký tự tốt nhất để hạn chế ô vuông/ký tự bị mất."""
    best_font = _font(px)
    best_score = -1
    for path in FONT_CANDIDATES:
        try:
            candidate = ImageFont.truetype(path, px)
            missing = candidate.getmask("\ufffd")
            score = 0
            for ch in text:
                if ch.isspace() or ord(ch) < 32 or ch in "\ufe0e\ufe0f\u200d":
                    continue
                try:
                    glyph = candidate.getmask(ch)
                    # Font thiếu glyph thường trả về cùng mặt nạ với ký tự thay thế.
                    if glyph.size != missing.size or bytes(glyph) != bytes(missing):
                        score += 1
                except Exception:
                    pass
            if score > best_score:
                best_score, best_font = score, candidate
        except Exception:
            continue
    return best_font


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")


def clean_name(name):
    # Giữ nguyên emoji, ký tự đặc biệt, chữ Nhật/Hàn/Trung...
    # Chỉ bỏ ký tự điều khiển có thể làm hỏng bố cục.
    name = str(name or "")
    name = "".join(ch for ch in name if ch in "\t " or ord(ch) >= 32)
    name = " ".join(name.split())
    return name or "Người chơi"


def fmt_money(n):
    return f"{int(n):,}".replace(",", ".")


def fmt_short(n):
    n = int(n)
    for div, suf in ((10**12, " nghìn tỷ"), (10**9, " tỷ"), (10**6, " tr"), (10**3, "k")):
        if n >= div:
            s = f"{n / div:.2f}".rstrip("0").rstrip(".").replace(".", ",")
            return s + suf
    return str(n)


def _fit_text(d, text, base_px, max_w, min_px):
    """Thu nhỏ chữ cho vừa; giữ nguyên ký tự đặc biệt, chỉ rút gọn khi quá dài."""
    px = int(base_px)
    while px > min_px:
        f = _font_for_text(text, px)
        if d.textlength(text, font=f) <= max_w:
            break
        px -= 1
    f = _font_for_text(text, px)
    while len(text) > 1 and d.textlength(text, font=f) > max_w:
        text = text[:-1].rstrip()
        candidate = text + "…"
        if d.textlength(candidate, font=f) <= max_w:
            text = candidate
            break
    return f, text


def _fit_coin(d, coin, max_w, base_px, min_px):
    for txt in (fmt_money(coin), fmt_short(coin)):
        px = int(base_px)
        while px >= min_px:
            f = _font(px)
            if d.textlength(txt, font=f) <= max_w:
                return f, txt
            px -= 1
    return _font(int(min_px)), fmt_short(coin)


def _draw_coin_icon(d, cx, cy, r):
    """Đồng coin vàng có bóng, viền kim loại và huy hiệu nổi ở giữa."""
    if r <= 1:
        d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(255, 205, 55))
        return
    d.ellipse((cx-r+1, cy-r+2, cx+r+1, cy+r+2), fill=(75, 42, 8))
    d.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(184, 104, 12),
              outline=(255, 229, 132), width=max(1, r//5))
    pad = max(1, int(r * .16))
    d.ellipse((cx-r+pad, cy-r+pad, cx+r-pad, cy+r-pad),
              fill=(255, 190, 35), outline=(255, 238, 165), width=max(1, r//7))
    inner = max(1, int(r * .55))
    d.ellipse((cx-inner, cy-inner, cx+inner, cy+inner),
              fill=(238, 151, 18), outline=(255, 224, 112), width=max(1, r//8))
    import math
    pts = []
    for i in range(10):
        ang = -math.pi/2 + i * math.pi/5
        rad = inner * (.72 if i % 2 == 0 else .34)
        pts.append((cx + math.cos(ang)*rad, cy + math.sin(ang)*rad))
    d.polygon(pts, fill=(255, 245, 190), outline=(255, 255, 225))


def _draw_coin_line(d, x, y, coin, max_w, px, center=False):
    """Vẽ [icon coin] + số tiền. x,y theo toạ độ ảnh đã phóng to."""
    icon_r = int(px * 0.42)
    gap = int(px * 0.3)
    f, txt = _fit_coin(d, coin, max_w - 2 * icon_r - gap, px, int(px * 0.6))
    tw = d.textlength(txt, font=f)
    total = 2 * icon_r + gap + tw
    left = x - total / 2 if center else x
    _draw_coin_icon(d, int(left + icon_r), int(y), icon_r)
    d.text((left + 2 * icon_r + gap, y), txt, font=f, fill=GOLD_TXT, anchor="lm",
           stroke_width=2, stroke_fill=NAVY_STROKE)


# ----------------------------------------------------------------------
# AVATAR
# ----------------------------------------------------------------------
@lru_cache(maxsize=32)
def _circle_mask(d):
    big = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
    return big.resize((d, d), Image.LANCZOS)


def _default_avatar(d):
    im = Image.new("RGBA", (d, d), (14, 40, 110, 255))
    dr = ImageDraw.Draw(im)
    dr.ellipse((d * .34, d * .18, d * .66, d * .50), fill=(249, 180, 43))
    dr.ellipse((d * .20, d * .54, d * .80, d * 1.05), fill=(249, 180, 43))
    return im


def _load_avatar(data, d):
    if data:
        try:
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            return ImageOps.fit(im, (d, d), Image.LANCZOS)
        except Exception:
            pass
    return _default_avatar(d)


def _paste_avatar(img, cx, cy, r, data, ring=None, ring_w=2.0):
    R = int(round(r * S))
    if ring:
        RW = int(round((r + ring_w) * S))
        img.paste(Image.new("RGBA", (2 * RW, 2 * RW), tuple(ring) + (255,)),
                  (X(cx) - RW, Y(cy) - RW), _circle_mask(2 * RW))
    avatar = _load_avatar(data, 2 * R)
    mask = _circle_mask(2 * R)
    img.paste(avatar, (X(cx) - R, Y(cy) - R), mask)



# ----------------------------------------------------------------------
# NỀN KHUNG (xử lý 1 lần rồi cache)
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _base():
    """Nạp duy nhất template trống, chuẩn hóa về hệ tọa độ 1024x491."""
    im = Image.open(template_path()).convert("RGBA")
    if im.size != (W0, H0):
        im = ImageOps.fit(im, (W0, H0), Image.LANCZOS)
    return im.resize((W0 * S, H0 * S), Image.LANCZOS).convert("RGBA")


# ----------------------------------------------------------------------
# VẼ ẢNH
# ----------------------------------------------------------------------
def _draw_header(d, tab):
    """Vẽ lại thanh tab trên cùng một template; mỗi lần chỉ sáng một tab."""
    bx0, by0, bx1, by1 = 156, 51, 426, 80
    d.rounded_rectangle(
        (X(bx0), Y(by0), X(bx1), Y(by1)),
        radius=14 * S,
        fill=(13, 29, 91),
        outline=(35, 43, 135),
        width=2 * S,
    )
    inner_left, inner_right = bx0 + 3, bx1 - 3
    sec = (inner_right - inner_left) / 3
    for i, (key, label) in enumerate(TABS):
        sx0 = inner_left + i * sec
        sx1 = inner_left + (i + 1) * sec
        cx, cy = (sx0 + sx1) / 2, (by0 + by1) / 2
        if key == tab:
            d.rounded_rectangle(
                (X(sx0), Y(by0 + 2), X(sx1), Y(by1 - 2)),
                radius=11 * S,
                fill=(255, 190, 28),
                outline=(255, 218, 91),
                width=1 * S,
            )
            color = (37, 39, 91)
        else:
            color = (245, 245, 255)
        d.text((X(cx), Y(cy)), label, font=_font(19 * S // 2),
               fill=color, anchor="mm")


def _paste_avatar_inside(img, cx, cy, r, data):
    """Đặt avatar trong vòng tròn có sẵn của template, không vẽ thêm viền."""
    if not data:
        return
    R = int(round(r * S))
    avatar = _load_avatar(data, 2 * R)
    img.paste(avatar, (X(cx) - R, Y(cy) - R), _circle_mask(2 * R))


def _draw_left_top3(img, d, rows, avatars):
    for rank, cfg in LEFT_TOP3.items():
        if rank > len(rows):
            continue
        row = rows[rank - 1]
        cx, cy = cfg["c"]
        _paste_avatar_inside(img, cx, cy, cfg["r"], avatars.get(row.get("avatar_url")))

        name_font, name = _fit_text(
            d, clean_name(row["name"]), 14 * S, cfg["wmax"] * S, 9 * S
        )
        d.text((X(cx), Y(cfg["name_y"])), name, font=name_font,
               fill=(255, 255, 255), anchor="mm",
               stroke_width=2 * S, stroke_fill=NAVY_STROKE)
        _draw_coin_line(
            d, X(cx), Y(cfg["coin_y"]), row["coin"], cfg["wmax"] * S,
            13 * S, center=True
        )


def _draw_rows(img, d, rows, avatars):
    for i, top in enumerate(ROW_TOPS):
        idx = 3 + i
        if idx >= len(rows):
            continue  # giữ nguyên avatar mẫu trong thanh trống

        row = rows[idx]
        cy = top + 20
        avatar_url = row.get("avatar_url")
        _paste_avatar_inside(img, ROW_AVATAR["x"], cy, ROW_AVATAR["r"],
                            avatars.get(avatar_url))

        name_font, name = _fit_text(
            d, clean_name(row["name"]), 13 * S, 160 * S, 9 * S
        )
        d.text((X(243), Y(top + 13)), name, font=name_font,
               fill=(255, 255, 255), anchor="lm",
               stroke_width=2 * S, stroke_fill=NAVY_STROKE)
        _draw_coin_line(d, X(243), Y(top + 31), row["coin"],
                        160 * S, 12 * S)


def _draw_right_top3(img, rows, avatars):
    """Cột phải chỉ thay avatar; giữ nguyên số hạng, nhãn và bục trong template."""
    for rank, cfg in RIGHT_AVATARS.items():
        if rank > len(rows):
            continue
        row = rows[rank - 1]
        cx, cy = cfg["c"]
        _paste_avatar_inside(img, cx, cy, cfg["r"],
                             avatars.get(row.get("avatar_url")))


def render_board(rows, tab, avatars):
    """Dùng một template cho Server/Tuần/Tháng; chỉ đổi tab đang sáng."""
    img = _base().copy()
    d = ImageDraw.Draw(img)
    _draw_header(d, tab)
    _draw_left_top3(img, d, rows, avatars)
    _draw_rows(img, d, rows, avatars)
    _draw_right_top3(img, rows, avatars)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------------
# DỮ LIỆU (SQLite)
# ----------------------------------------------------------------------
def ensure_activity_table():
    with closing(sqlite3.connect(DB_NAME)) as c:
        c.execute("CREATE TABLE IF NOT EXISTS top_activity (user_id TEXT PRIMARY KEY, last_active REAL NOT NULL)")
        c.commit()


def touch_activity(user_id):
    with closing(sqlite3.connect(DB_NAME)) as c:
        c.execute("INSERT OR REPLACE INTO top_activity (user_id, last_active) VALUES (?, ?)",
                  (str(user_id), time.time()))
        c.commit()


def query_all_by_coin():
    with closing(sqlite3.connect(DB_NAME)) as c:
        return c.execute("SELECT user_id, coin FROM users ORDER BY coin DESC").fetchall()


def query_active_by_coin(days, limit):
    since = time.time() - days * 86400
    with closing(sqlite3.connect(DB_NAME)) as c:
        return c.execute(
            "SELECT u.user_id, u.coin FROM users u "
            "JOIN top_activity a ON a.user_id = u.user_id "
            "WHERE a.last_active >= ? ORDER BY u.coin DESC LIMIT ?",
            (since, limit)).fetchall()


# ----------------------------------------------------------------------
# TẢI AVATAR (cache 1 giờ)
# ----------------------------------------------------------------------
_avatar_cache = {}
AVATAR_TTL = 3600


async def fetch_avatars(urls):
    urls = [u for u in set(urls) if u]

    async def one(session, url):
        hit = _avatar_cache.get(url)
        if hit and time.time() - hit[0] < AVATAR_TTL:
            return url, hit[1]
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.read()
                _avatar_cache[url] = (time.time(), data)
                return url, data
        except Exception:
            return url, None

    async with aiohttp.ClientSession() as s:
        res = await asyncio.gather(*(one(s, u) for u in urls))
    return dict(res)


# ----------------------------------------------------------------------
# VIEW (3 nút dưới ảnh, không embed)
# ----------------------------------------------------------------------
class TopView(discord.ui.View):
    def __init__(self, cog, author_id, guild, tab="server"):
        super().__init__(timeout=120)
        self.cog, self.author_id, self.guild, self.tab = cog, author_id, guild, tab
        self.message = None
        self._sync()

    def _sync(self):
        for child, key in ((self.b_server, "server"),
                           (self.b_week, "week"),
                           (self.b_month, "month")):
            child.style = (discord.ButtonStyle.primary if key == self.tab
                           else discord.ButtonStyle.secondary)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bảng này của người gọi lệnh, bạn gõ `.top` để mở bảng riêng nhé.",
                                                    ephemeral=True)
            return False
        return True

    async def _switch(self, interaction, tab):
        await interaction.response.defer()
        try:
            png = await self.cog.make_png(tab, self.guild)
        except Exception as e:
            print(f"[Top] lỗi dựng ảnh: {e}")
            return await interaction.followup.send(f"❌ Chưa dựng được bảng: `{e}`", ephemeral=True)
        self.tab = tab
        self._sync()
        await interaction.edit_original_response(
            attachments=[discord.File(io.BytesIO(png), "top.png")], view=self)

    @discord.ui.button(label="Server", emoji="🏠")
    async def b_server(self, interaction, button):
        await self._switch(interaction, "server")

    @discord.ui.button(label="Tuần", emoji="📅")
    async def b_week(self, interaction, button):
        await self._switch(interaction, "week")

    @discord.ui.button(label="Tháng", emoji="🗓️")
    async def b_month(self, interaction, button):
        await self._switch(interaction, "month")

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


# ----------------------------------------------------------------------
# COG
# ----------------------------------------------------------------------
class TopCog(commands.Cog):
    FRESH = 45        # giây: quá thời gian này thì dựng lại ngầm
    MAX_STALE = 600   # giây: cũ hơn nữa thì buộc phải chờ dựng mới

    def __init__(self, bot):
        self.bot = bot
        self._png_cache = {}      # (tab, guild_id) -> (time, bytes)
        self._refreshing = set()

    async def cog_load(self):
        ensure_activity_table()
        try:
            await asyncio.to_thread(_base)      # dựng sẵn nền 1 lần lúc khởi động
        except Exception as e:
            # thiếu ảnh/lỗi ảnh thì KHÔNG làm sập bot, chỉ báo log
            print(f"[Top] ⚠️ Chưa dựng được bảng xếp hạng ảnh: {e}")
            return
        self.refresh_loop.start()

    async def cog_unload(self):
        if self.refresh_loop.is_running():
            self.refresh_loop.cancel()

    @tasks.loop(seconds=60)
    async def refresh_loop(self):
        """Dựng sẵn ảnh Tuần/Tháng (dùng chung mọi server) để bấm là ra liền."""
        for tab in ("week", "month"):
            try:
                await self._build((tab, 0), tab, None)
            except Exception as e:
                print(f"[Top] lỗi dựng sẵn {tab}: {e}")

    @refresh_loop.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        try:
            await asyncio.to_thread(touch_activity, ctx.author.id)
        except Exception:
            pass

    async def _resolve(self, uid, guild):
        uid = int(uid)
        member = guild.get_member(uid) if guild else None
        user = member or self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                return "Người chơi ẩn", None
        try:
            url = user.display_avatar.replace(size=128, format="png").url
        except Exception:
            url = str(user.display_avatar.url)
        return user.display_name, url

    async def _get_rows(self, tab, guild):
        if tab == "server" and guild is not None:
            picked = []
            for uid, coin in await asyncio.to_thread(query_all_by_coin):
                m = guild.get_member(int(uid))
                if m is not None and not m.bot:
                    picked.append((uid, coin))
                    if len(picked) >= 7:
                        break
        elif tab == "server":
            picked = (await asyncio.to_thread(query_all_by_coin))[:7]
        else:
            picked = await asyncio.to_thread(query_active_by_coin, TAB_DAYS[tab], 7)

        g = guild if tab == "server" else None   # Tuần/Tháng dùng chung nên không lấy nick theo server
        results = await asyncio.gather(*(self._resolve(uid, g) for uid, _ in picked))
        return [{"name": n, "coin": coin, "avatar_url": u} for (n, u), (_, coin) in zip(results, picked)]

    async def _build(self, key, tab, guild):
        rows = await self._get_rows(tab, guild)
        avatars = await fetch_avatars([r["avatar_url"] for r in rows])
        png = await asyncio.to_thread(render_board, rows, tab, avatars)
        self._png_cache[key] = (time.time(), png)
        return png

    async def _refresh(self, key, tab, guild):
        try:
            await self._build(key, tab, guild)
        except Exception as e:
            print(f"[Top] lỗi làm mới {key}: {e}")
        finally:
            self._refreshing.discard(key)

    async def make_png(self, tab, guild):
        key = (tab, guild.id if (tab == "server" and guild) else 0)
        hit = self._png_cache.get(key)
        now = time.time()
        if hit:
            if now - hit[0] > self.FRESH and key not in self._refreshing:
                self._refreshing.add(key)
                asyncio.create_task(self._refresh(key, tab, guild))
            if now - hit[0] < self.MAX_STALE:
                return hit[1]          # trả ảnh có sẵn ngay, làm mới chạy ngầm
        return await self._build(key, tab, guild)

    @commands.hybrid_command(name="top")
    async def cmd_top(self, ctx):
        """Xem bảng xếp hạng đại gia."""
        key = ("server", ctx.guild.id if ctx.guild else 0)
        try:
            if key in self._png_cache:
                png = await self.make_png("server", ctx.guild)
            else:
                async with ctx.typing():
                    png = await self.make_png("server", ctx.guild)
        except Exception as e:
            print(f"[Top] lỗi dựng ảnh: {e}")
            return await ctx.send(f"❌ Chưa dựng được bảng xếp hạng: `{e}`", delete_after=10)
        view = TopView(self, ctx.author.id, ctx.guild)
        view.message = await ctx.send(file=discord.File(io.BytesIO(png), "top.png"), view=view)


async def setup(bot):
    await bot.add_cog(TopCog(bot))
