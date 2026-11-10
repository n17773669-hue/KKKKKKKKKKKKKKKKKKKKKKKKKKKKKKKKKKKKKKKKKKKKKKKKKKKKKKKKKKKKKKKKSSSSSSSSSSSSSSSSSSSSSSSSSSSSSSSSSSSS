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
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from database import DB_NAME

BASE_DIR = Path(__file__).resolve().parent
ASSETS = BASE_DIR / "assets"
TEMPLATE_PATH = ASSETS / "leaderboard_template.png"
FONT_CANDIDATES = [
    str(ASSETS / "BeVietnamPro-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "Arial Bold.ttf",
]

# ----------------------------------------------------------------------
# HÌNH HỌC KHUNG (toạ độ theo ảnh template gốc 1024x682)
# ----------------------------------------------------------------------
S = 2                                   # phóng to 2x (ảnh ra 2048x1364)
W0, H0 = 1024, 682                      # kích thước ảnh template gốc
ROW_TOPS = [387, 446, 505, 563]         # đầu thanh hạng 4,5,6,7
ROW_H = 47

# top 1/2/3: tâm avatar, bán kính avatar, vị trí chữ, bề rộng tối đa (px gốc)
TOP3 = {
    1: dict(c=(312.5, 240), r=43, name_y=304, coin_y=320, wmax=92, ring=(255, 196, 40)),
    2: dict(c=(234, 270.5), r=32, name_y=338, coin_y=354, wmax=104, ring=(205, 212, 232)),
    3: dict(c=(394.5, 270.5), r=32, name_y=338, coin_y=354, wmax=104, ring=(214, 140, 70)),
}
# bên phải (bục podium): tâm avatar, bán kính, vị trí chữ
RIGHT3 = {
    1: dict(c=(736, 355), r=49, name_y=430, coin_y=451, wmax=140, ring=(255, 196, 40)),
    2: dict(c=(603, 385), r=34, name_y=447, coin_y=468, wmax=118, ring=(205, 212, 232)),
    3: dict(c=(866, 385), r=34, name_y=447, coin_y=468, wmax=118, ring=(214, 140, 70)),
}
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
@lru_cache(maxsize=64)
def _font(px):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, px)
        except Exception:
            continue
    return ImageFont.load_default(px)


_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d]")


def clean_name(name):
    name = _EMOJI_RE.sub("", name or "")
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
    """Thu nhỏ chữ cho vừa; vẫn dài quá thì cắt bằng '…'."""
    px = int(base_px)
    while px > min_px and d.textlength(text, font=_font(px)) > max_w:
        px -= 1
    f = _font(px)
    while len(text) > 1 and d.textlength(text, font=f) > max_w:
        text = text[:-1].rstrip()
        if d.textlength(text + "…", font=f) <= max_w:
            text += "…"
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
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 196, 40), outline=(168, 104, 8), width=max(1, r // 4))
    ri = int(r * 0.55)
    d.ellipse((cx - ri, cy - ri, cx + ri, cy + ri), outline=(255, 234, 150), width=max(1, r // 6))


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
    img.paste(_load_avatar(data, 2 * R), (X(cx) - R, Y(cy) - R), _circle_mask(2 * R))


# ----------------------------------------------------------------------
# NỀN KHUNG (xử lý 1 lần rồi cache)
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _base():
    im = Image.open(TEMPLATE_PATH).convert("RGB")

    # xoá chữ "LEADERBOARD": kéo giãn 1 dải gradient mỏng ở phía trên đè lên
    x0, x1, y0, y1 = 236, 392, 82, 104
    strip = im.crop((x0, 68, x1, 72)).resize((x1 - x0, y1 - y0), Image.BICUBIC)
    im.paste(strip, (x0, y0))

    # xoá avatar mẫu (hình người cam) ở các hàng 4-7: chép đoạn thanh trống bên phải đè lên
    for top in ROW_TOPS:
        im.paste(im.crop((330, top + 3, 374, top + 44)), (214, top + 3))

    # xoá chữ "Leaderboard" to bên phải: kéo giãn dải nền phía trên đè lên
    # (điền nền rồi làm mịn dần từ mép vào trong, không cần numpy)
    box = (540, 90, 933, 170)
    region = im.crop(box)
    hole = Image.new("L", region.size, 0)
    ImageDraw.Draw(hole).rectangle((16, 12, region.width - 16, region.height - 12), fill=255)
    fill = region.crop((16, 2, region.width - 16, 6)).resize((region.width - 32, region.height - 24), Image.BICUBIC)
    region.paste(fill, (16, 12))
    for _ in range(70):
        region = Image.composite(region.filter(ImageFilter.GaussianBlur(5)), region, hole)
    im.paste(region, box[:2])

    return im.resize((W0 * S, H0 * S), Image.LANCZOS).convert("RGBA")


# ----------------------------------------------------------------------
# VẼ ẢNH
# ----------------------------------------------------------------------
def _draw_header(d, tab):
    # tiêu đề
    f, t = _fit_text(d, "BẢNG XẾP HẠNG ĐẠI GIA", 12 * S, 180 * S, 8 * S)
    d.text((X(314.5), Y(93.5)), t, font=f, fill=(240, 232, 255), anchor="mm",
           stroke_width=1, stroke_fill=(70, 20, 130))

    # thanh tab (vẽ đè lên thanh tab cũ trong ảnh)
    bx0, by0, bx1, by1 = 196, 111, 440, 140
    d.rounded_rectangle((X(bx0), Y(by0), X(bx1), Y(by1)), radius=14 * S, fill=(14, 34, 102))
    sec = (bx1 - bx0 - 6) / 3
    for i, (key, label) in enumerate(TABS):
        sx0 = bx0 + 3 + i * sec
        sx1 = sx0 + sec
        cx, cy = (sx0 + sx1) / 2, (by0 + by1) / 2
        if key == tab:
            d.rounded_rectangle((X(sx0), Y(by0 + 2.5), X(sx1), Y(by1 - 2.5)), radius=11 * S,
                                fill=(249, 180, 43), outline=(60, 36, 30), width=S)
            color = (70, 38, 16)
        else:
            color = (226, 232, 255)
        d.text((X(cx), Y(cy)), label, font=_font(int(10.5 * S)), fill=color, anchor="mm")


def _draw_top3(img, d, rows, avatars, cfgs, k=1.0):
    for rank, cfg in cfgs.items():
        if rank > len(rows):
            continue
        r = rows[rank - 1]
        cx, cy = cfg["c"]
        _paste_avatar(img, cx, cy, cfg["r"], avatars.get(r.get("avatar_url")), ring=cfg["ring"], ring_w=2.2 * k)

        f, name = _fit_text(d, clean_name(r["name"]), (13 if rank == 1 else 11.5) * k * S,
                            cfg["wmax"] * S, 7 * S)
        d.text((X(cx), Y(cfg["name_y"])), name, font=f, fill="white", anchor="mm",
               stroke_width=2, stroke_fill=NAVY_STROKE)
        _draw_coin_line(d, X(cx), Y(cfg["coin_y"]), r["coin"], cfg["wmax"] * S,
                        int((11 if rank == 1 else 10) * k * S), center=True)


def _draw_rows(img, d, rows, avatars):
    for i, top in enumerate(ROW_TOPS):
        idx = 3 + i
        cy = top + 24
        if idx >= len(rows):
            # hàng trống
            _paste_avatar(img, 236, cy, 18, None, ring=(90, 105, 190), ring_w=1.2)
            d.text((X(263), Y(cy)), "Chưa có ai", font=_font(int(11 * S)), fill=(150, 165, 220), anchor="lm")
            continue
        r = rows[idx]
        _paste_avatar(img, 236, cy, 17.5, avatars.get(r.get("avatar_url")), ring=(249, 180, 43), ring_w=1.5)
        f, name = _fit_text(d, clean_name(r["name"]), 12.5 * S, (440 - 263) * S, 8 * S)
        d.text((X(263), Y(top + 15)), name, font=f, fill="white", anchor="lm",
               stroke_width=2, stroke_fill=NAVY_STROKE)
        _draw_coin_line(d, X(263), Y(top + 33), r["coin"], (440 - 263) * S, int(11 * S))


def _draw_right_title(d):
    text = "BẢNG XẾP HẠNG"
    f, t = _fit_text(d, text, 40 * S, 340 * S, 20 * S)
    cx, cy = X(736), Y(128)
    d.text((cx + 3 * S, cy + 4 * S), t, font=f, fill=(70, 20, 140), anchor="mm")
    d.text((cx, cy), t, font=f, fill=(255, 186, 48), anchor="mm", stroke_width=S, stroke_fill=(226, 128, 18))


def render_board(rows, tab, avatars):
    """rows: [{"name","coin","avatar_url"}...] (tối đa 7) ; avatars: {url: bytes}. Trả về bytes PNG."""
    img = _base().copy()
    d = ImageDraw.Draw(img)
    _draw_header(d, tab)
    _draw_top3(img, d, rows, avatars, TOP3)
    _draw_rows(img, d, rows, avatars)
    _draw_right_title(d)
    _draw_top3(img, d, rows, avatars, RIGHT3, k=1.18)
    buf = io.BytesIO()
    img.save(buf, "PNG")
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
        for child, (key, _) in zip(self.children, TABS):
            child.style = discord.ButtonStyle.primary if key == self.tab else discord.ButtonStyle.secondary

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Bảng này của người gọi lệnh, bạn gõ `.top` để mở bảng riêng nhé.",
                                                    ephemeral=True)
            return False
        return True

    async def _switch(self, interaction, tab):
        await interaction.response.defer()
        png = await self.cog.make_png(tab, self.guild)
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
    def __init__(self, bot):
        self.bot = bot
        self._png_cache = {}   # (tab, guild_id) -> (time, bytes)

    async def cog_load(self):
        ensure_activity_table()

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

        rows = []
        for uid, coin in picked:
            name, url = await self._resolve(uid, guild)
            rows.append({"name": name, "coin": coin, "avatar_url": url})
        return rows

    async def make_png(self, tab, guild):
        key = (tab, guild.id if (tab == "server" and guild) else 0)
        hit = self._png_cache.get(key)
        if hit and time.time() - hit[0] < 45:
            return hit[1]
        rows = await self._get_rows(tab, guild)
        avatars = await fetch_avatars([r["avatar_url"] for r in rows])
        png = await asyncio.to_thread(render_board, rows, tab, avatars)
        self._png_cache[key] = (time.time(), png)
        return png

    @commands.hybrid_command(name="top")
    async def cmd_top(self, ctx):
        """Xem bảng xếp hạng đại gia."""
        async with ctx.typing():
            png = await self.make_png("server", ctx.guild)
        view = TopView(self, ctx.author.id, ctx.guild)
        view.message = await ctx.send(file=discord.File(io.BytesIO(png), "top.png"), view=view)


async def setup(bot):
    await bot.add_cog(TopCog(bot))
