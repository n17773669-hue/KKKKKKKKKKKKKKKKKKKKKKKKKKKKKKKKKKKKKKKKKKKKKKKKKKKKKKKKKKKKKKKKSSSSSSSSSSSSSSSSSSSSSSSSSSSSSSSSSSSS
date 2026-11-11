"""
top.py - Bảng xếp hạng đại gia dạng ẢNH (vẽ lên khung assets/top.png)

Cần có trong thư mục assets/ (cạnh file này):
    top.png                    <- ảnh khung 1811x868
    BeVietnamPro-Bold.ttf      <- font tiếng Việt (không có thì tự dùng DejaVu/Arial)

Cần: pip install pillow aiohttp

3 nút bên dưới ảnh:
    Server : người đang ở server này, xếp theo coin
    Tuần   : tất cả người đã dùng bot trong 7 ngày gần nhất (mọi server), xếp theo coin
    Tháng  : tất cả người đã dùng bot trong 30 ngày gần nhất (mọi server), xếp theo coin

BẢN NHẸ (tối ưu CPU/RAM cho host yếu):
  * Phần tĩnh (cắt khung, thu nhỏ bục, ghép nền, mặt nạ) chỉ làm 1 LẦN lúc bot khởi động.
    Mỗi lần vẽ chỉ copy ảnh đã ghép sẵn rồi vẽ avatar + chữ lên.
  * Lưu JPEG 4:4:4 chất lượng 95 (~6 ms, ~300 KB) thay vì WebP lossless (~750 ms).
    Đổi OUT_FORMAT = "PNG" nếu muốn lossless tuyệt đối (nặng hơn, ~60 ms).
  * Avatar tải size 256 (đủ nét), cache có giới hạn; tên/avatar người ngoài server cache 10 phút
    (không gọi API Discord mỗi lần bấm Tuần/Tháng).
  * Gọi .top xong tự dựng sẵn Tuần/Tháng ở nền -> bấm nút là ra ngay.
  * Cache ảnh gọn (12 bảng), giới hạn cache font.

Xem thử nhanh không cần Discord:  python top.py   -> tạo top_preview_*.jpg
"""
import asyncio
import io
import math
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
# HÌNH HỌC KHUNG - toạ độ THẬT trên ảnh khung 1811x868
# Nếu thấy lệch vài px thì chỉnh ở đây (c = tâm, r = bán kính avatar).
# ----------------------------------------------------------------------
W, H = 1811, 868

CARD_CROP = (236, 14, 806, 822)      # khung bảng bên trái (giữ nguyên cỡ)
POD_X = (930, 1795)                  # cột x lấy phần bục + tiêu đề (lấy full chiều cao khung)
POD_SCALE = 0.78                     # thu nhỏ phần bục (to hơn = bục lớn nhưng ảnh rộng hơn)
CARD_FADE = 16                       # mép phải của bảng mờ dần vào nền (px)
POD_VFADE = 48                       # mép trên/dưới của bục mờ dần vào nền trơn (px)

OUT_FORMAT = "JPEG"                  # "JPEG" (nhẹ, nhanh) hoặc "PNG" (lossless, nặng hơn)
OUT_NAME = "top.png" if OUT_FORMAT == "PNG" else "top.jpg"

# Avatar trên bục bên phải (toạ độ trên khung gốc)
RIGHT_AVATARS = {
    1: dict(c=(1369, 425), r=134),
    2: dict(c=(1085, 470), r=108),
    3: dict(c=(1642, 473), r=105),
}

LEFT_TOP3 = {
    1: dict(c=(521, 300), r=86, name_y=411, coin_y=441, wmax=200, name_px=34, coin_px=24),
    2: dict(c=(335, 332), r=71, name_y=419, coin_y=445, wmax=172, name_px=27, coin_px=21),
    3: dict(c=(705, 332), r=68, name_y=419, coin_y=445, wmax=172, name_px=27, coin_px=21),
}

# Bốn thanh hạng 4-7
ROW_TOPS = [469, 552, 637, 723]
ROW_H = 72
ROW_AVATAR = dict(x=362, r=30)
ROW_TEXT_X = 412
ROW_RIGHT = 767

# Thanh tab
TAB_BAR = (281, 91, 762, 140)
TAB_CENTERS = {"server": 358, "week": 517, "month": 679}
TAB_PILL_W = 148
TABS = [("server", "Server"), ("week", "Tuần"), ("month", "Tháng")]
TAB_DAYS = {"week": 7, "month": 30}

RIM_W = 3             # độ dày viền mảnh ở mép avatar (0 = tắt)
RIM = {1: (255, 205, 70), 2: (200, 218, 255), 3: (242, 156, 98)}   # vàng / bạc / đồng

GOLD_TXT = (255, 221, 104)
NAVY_STROKE = (8, 20, 70)


# ----------------------------------------------------------------------
# TIỆN ÍCH CHỮ / SỐ
# ----------------------------------------------------------------------
_MEASURE = ImageDraw.Draw(Image.new("L", (4, 4)))


def _tw(text, font, stroke=0):
    return _MEASURE.textlength(text, font=font) + 2 * stroke


@lru_cache(maxsize=64)
def _truetype(path, px):
    return ImageFont.truetype(path, px)


@lru_cache(maxsize=64)
def _font(px):
    for path in FONT_CANDIDATES:
        try:
            return _truetype(path, px)
        except Exception:
            continue
    return ImageFont.load_default(px)


@lru_cache(maxsize=256)
def _best_path(text):
    """File font phủ nhiều ký tự của `text` nhất (tính 1 lần cho mỗi tên)."""
    best, best_score = None, -1
    for path in FONT_CANDIDATES:
        try:
            f = _truetype(path, 24)
            missing = f.getmask("\U0010fffe")
            score = 0
            for ch in text:
                if ch.isspace() or ord(ch) < 32 or ch in "\ufe0e\ufe0f\u200d":
                    continue
                g = f.getmask(ch)
                if g.size != missing.size or bytes(g) != bytes(missing):
                    score += 1
            if score > best_score:
                best, best_score = path, score
        except Exception:
            continue
    return best


def _font_for_text(text, px):
    path = _best_path(text)
    if path:
        try:
            return _truetype(path, int(px))
        except Exception:
            pass
    return _font(px)


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


def _stroke_for(px):
    return max(2, int(px * 0.11))


def _drop_missing(text, font):
    """Bỏ ký tự mà font không có glyph (tránh hiện ô vuông xấu); có thể trả về rỗng."""
    try:
        missing = bytes(font.getmask("\U0010fffe"))
        msize = font.getmask("\U0010fffe").size
        keep = []
        for ch in text:
            if ch.isspace() or ch in "\ufe0e\ufe0f\u200d":
                keep.append(ch)
                continue
            g = font.getmask(ch)
            if g.size == msize and bytes(g) == missing:
                continue
            keep.append(ch)
        return " ".join("".join(keep).split())
    except Exception:
        return text


def _fit_text(text, base_px, max_w, min_px, fallback="Người chơi"):
    """Thu nhỏ chữ cho vừa; chỉ rút gọn (…) khi đã xuống cỡ nhỏ nhất mà vẫn dài."""
    text = _drop_missing(text, _font_for_text(text, int(base_px))) or fallback
    px = int(base_px)
    while px > min_px:
        f = _font_for_text(text, px)
        if _tw(text, f, _stroke_for(px)) <= max_w:
            return f, text
        px -= 1
    f = _font_for_text(text, px)
    st = _stroke_for(px)
    if _tw(text, f, st) <= max_w:
        return f, text
    while len(text) > 1:
        text = text[:-1].rstrip()
        if _tw(text + "…", f, st) <= max_w:
            return f, text + "…"
    return f, text


def _fit_coin(coin, max_w, base_px, min_px):
    """Số đầy đủ (giữ cỡ >= 95%), không vừa thì chuyển sang dạng rút gọn (4,5 tỷ)."""
    for txt, ratio in ((fmt_money(coin), 0.95), (fmt_short(coin), 0.7)):
        px = int(base_px)
        while px >= max(int(min_px), int(base_px * ratio)):
            f = _font(px)
            if _tw(txt, f, 0) <= max_w:
                return f, txt
            px -= 1
    return _font(int(min_px)), fmt_short(coin)


# ----------------------------------------------------------------------
# ICON COIN (supersample 6x -> thu nhỏ, viền rất mịn; cache theo bán kính)
# ----------------------------------------------------------------------
@lru_cache(maxsize=16)
def _coin_icon(r):
    k, pad = 6, 4
    size = 2 * r + 2 * pad
    big = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)
    c = size * k / 2
    R = r * k

    def circ(rad, dx=0.0, dy=0.0, **kw):
        g.ellipse((c - rad + dx, c - rad + dy, c + rad + dx, c + rad + dy), **kw)

    circ(R, 0, 2.2 * k, fill=(40, 20, 4, 150))                     # bóng đổ
    circ(R, fill=(150, 78, 6, 255))                                  # thân viền tối
    circ(R, outline=(255, 236, 150, 255), width=max(k, int(R * .13)))  # mép sáng
    face = R * .84
    steps = 14                                                       # mặt coin chuyển sắc
    for i in range(steps):
        t = i / (steps - 1)
        rad = face * (1 - t * .78)
        col = (int(250 + 5 * t), int(176 + 58 * t), int(30 + 80 * t), 255)
        circ(rad, -t * R * .10, -t * R * .12, fill=col)
    ring = face * .70
    circ(ring, outline=(214, 128, 14, 255), width=max(k // 2, int(R * .07)))  # vòng chìm
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = ring * (.62 if i % 2 == 0 else .26)
        pts.append((c + math.cos(ang) * rad, c + math.sin(ang) * rad + R * .02))
    g.polygon(pts, fill=(255, 247, 200, 255), outline=(222, 140, 18, 255))
    g.arc((c - face, c - face, c + face, c + face), 200, 265,
          fill=(255, 255, 235, 235), width=max(k // 2, int(R * .06)))  # bóng loáng
    return big.resize((size, size), Image.LANCZOS), pad


# ----------------------------------------------------------------------
# PAINTER: gom chữ + icon, cuối cùng đổ bóng mềm rồi vẽ chữ sắc nét lên trên
# (ox, oy = góc trái-trên của vùng đang vẽ trên khung gốc; toạ độ truyền vào là toạ độ khung gốc)
# ----------------------------------------------------------------------
def _aa_pill(img, box, fill, outline=None, ow=1.5):
    """Viên thuốc bo tròn, vẽ supersample 4x cho viền mịn."""
    k = 4
    x0, y0 = int(math.floor(box[0])) - 3, int(math.floor(box[1])) - 3
    x1, y1 = int(math.ceil(box[2])) + 3, int(math.ceil(box[3])) + 3
    layer = Image.new("RGBA", ((x1 - x0) * k, (y1 - y0) * k), (0, 0, 0, 0))
    b = ((box[0] - x0) * k, (box[1] - y0) * k, (box[2] - x0) * k, (box[3] - y0) * k)
    ImageDraw.Draw(layer).rounded_rectangle(
        b, radius=(b[3] - b[1]) / 2, fill=fill,
        outline=outline, width=int(ow * k) if outline else 0)
    img.alpha_composite(layer.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0))


def _coin_layout(coin, max_w, px):
    r = int(round(px * 0.5))
    gap = int(round(px * 0.32))
    padx = int(round(px * 0.55))
    ph = int(round(px * 1.4))
    f, txt = _fit_coin(coin, max_w - 2 * padx - 2 * r - gap, px, int(px * 0.7))
    pw = int(math.ceil(2 * padx + 2 * r + gap + _tw(txt, f, 0)))
    return f, txt, r, gap, padx, pw, ph


class _Painter:
    def __init__(self, ox=0, oy=0):
        self.items = []
        self.ox, self.oy = ox, oy

    def text(self, xy, s, font, fill=(255, 255, 255), anchor="mm", stroke=None, shadow=True):
        if stroke is None:
            stroke = _stroke_for(getattr(font, "size", 24))
        xy = (xy[0] - self.ox, xy[1] - self.oy)
        self.items.append(("t", xy, s, font, fill, anchor, stroke, shadow))

    def coin_line(self, x, y, coin, max_w, px, align="center"):
        """Viên thuốc nền tối viền vàng chứa [icon coin] + số tiền. Trả về chiều rộng viên thuốc."""
        f, txt, r, gap, padx, pw, ph = _coin_layout(coin, max_w, px)
        left = {"center": x - pw / 2, "right": x - pw}.get(align, x) - self.ox
        y = y - self.oy
        self.items.append(("p", (left, y - ph / 2, left + pw, y + ph / 2)))
        self.items.append(("c", int(round(left + padx + r)), int(round(y)), r))
        self.items.append(("t", (left + padx + 2 * r + gap, y), txt, f, GOLD_TXT, "lm", 0, False))
        return pw

    def flush(self, img):
        for it in self.items:
            if it[0] == "p":
                _aa_pill(img, it[1], (5, 12, 52, 178), (255, 206, 72, 175))

        boxes = []
        for it in self.items:
            if it[0] == "t" and it[7]:
                _, (x, y), t, f, _fill, anc, st, _sh = it
                boxes.append(_MEASURE.textbbox((x, y + 3), t, font=f, anchor=anc, stroke_width=st + 1))
        if boxes:
            pad = 16
            x0 = max(0, int(min(b[0] for b in boxes)) - pad)
            y0 = max(0, int(min(b[1] for b in boxes)) - pad)
            x1 = min(img.width, int(max(b[2] for b in boxes)) + pad)
            y1 = min(img.height, int(max(b[3] for b in boxes)) + pad)
            shadow = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            for it in self.items:
                if it[0] == "t" and it[7]:
                    _, (x, y), t, f, _fill, anc, st, _sh = it
                    sd.text((x - x0, y + 3 - y0), t, font=f, fill=(3, 8, 40, 200), anchor=anc,
                            stroke_width=st + 1, stroke_fill=(3, 8, 40, 200))
            img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3.5)), (x0, y0))

        d = ImageDraw.Draw(img)
        for it in self.items:
            if it[0] == "c":
                _, cx, cy, r = it
                icon, pad = _coin_icon(r)
                img.alpha_composite(icon, (cx - r - pad, cy - r - pad))
            elif it[0] == "t":
                _, (x, y), s, f, fill, anc, st, _sh = it
                d.text((x, y), s, font=f, fill=fill, anchor=anc,
                       stroke_width=st, stroke_fill=NAVY_STROKE)


# ----------------------------------------------------------------------
# AVATAR
# ----------------------------------------------------------------------
@lru_cache(maxsize=32)
def _circle_mask(d):
    k = 6
    big = Image.new("L", (d * k, d * k), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * k - 1, d * k - 1), fill=255)
    return big.resize((d, d), Image.LANCZOS)


_av_proc = {}
AV_PROC_MAX = 40


def _load_avatar(data, d):
    key = (hash(data), d)
    hit = _av_proc.get(key)
    if hit is not None:
        return hit
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im = ImageOps.fit(im, (d, d), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.1, percent=55, threshold=2))
    except Exception:
        return None
    try:
        if len(_av_proc) >= AV_PROC_MAX:
            _av_proc.pop(next(iter(_av_proc)))
    except Exception:
        _av_proc.clear()
    _av_proc[key] = im
    return im


@lru_cache(maxsize=16)
def _ring_layer(r, color, w):
    """Vòng viền avatar (supersample 6x) - chỉ có vài loại nên cache luôn, khỏi vẽ lại mỗi lần."""
    k, pad = 6, 3
    size = 2 * (r + pad)
    layer = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    c, R = size * k / 2, r * k
    ImageDraw.Draw(layer).ellipse((c - R, c - R, c + R, c + R),
                                  outline=tuple(color) + (255,), width=max(1, int(w * k)))
    return layer.resize((size, size), Image.LANCZOS), pad


def _aa_ring(img, cx, cy, r, color, w):
    ring, pad = _ring_layer(int(r), tuple(color), float(w))
    img.alpha_composite(ring, (int(cx) - int(r) - pad, int(cy) - int(r) - pad))


def _paste_avatar_inside(img, cx, cy, r, data, rim=None, rim_w=RIM_W):
    """Đặt avatar trong vòng tròn có sẵn của khung (giữ nguyên viền/ánh sáng của khung)."""
    if not data:
        return          # không có avatar -> giữ nguyên avatar mẫu của khung
    d = 2 * int(r)
    av = _load_avatar(data, d)
    if av is None:
        return
    img.paste(av, (int(cx) - d // 2, int(cy) - d // 2), _circle_mask(d))
    if rim and rim_w:
        _aa_ring(img, cx, cy, int(r), rim, rim_w)


# ----------------------------------------------------------------------
# PHẦN TĨNH: làm 1 lần duy nhất (cắt khung, thu nhỏ bục, ghép nền, mặt nạ mép)
# ----------------------------------------------------------------------
class _Static:
    __slots__ = ("card", "bg", "mask", "cw", "ch", "top")


def _smooth_fill(strip, w, h):
    """Nền trơn: lấy dải pixel mép, làm mượt theo chiều ngang, rồi lấp đầy theo chiều dọc."""
    small = strip.resize((max(2, w // 70), 1), Image.BOX)
    return small.resize((w, max(1, h)), Image.BICUBIC)


@lru_cache(maxsize=1)
def _static():
    base = Image.open(template_path()).convert("RGB")
    if base.size != (W, H):
        base = ImageOps.fit(base, (W, H), Image.LANCZOS)

    card = base.crop(CARD_CROP)
    cw, ch = card.size

    # Bục: lấy toàn chiều cao khung gốc rồi thu nhỏ
    pod = base.crop((POD_X[0], 0, POD_X[1], base.height))
    pw, ph = int(pod.width * POD_SCALE), int(pod.height * POD_SCALE)
    pod = pod.resize((pw, ph), Image.LANCZOS)
    top = max(0, (ch - ph) // 2)
    bot = max(0, ch - top - ph)

    right = Image.new("RGB", (pw, ch))
    if top > 0:      # dải trên/dưới bục: nền tím trơn (không kéo dãn hoa văn -> không vệt mờ)
        right.paste(_smooth_fill(pod.crop((0, 0, pw, 3)), pw, top + POD_VFADE), (0, 0))
    if bot > 0:
        right.paste(_smooth_fill(pod.crop((0, ph - 3, pw, ph)), pw, bot + POD_VFADE),
                    (0, ch - bot - POD_VFADE))
    m = Image.new("L", (pw, ph), 255)       # mép trên/dưới của bục mờ dần vào nền trơn
    d = ImageDraw.Draw(m)
    for i in range(POD_VFADE):
        a = int(255 * i / POD_VFADE)
        if top > 0:
            d.line((0, i, pw, i), fill=a)
        if bot > 0:
            d.line((0, ph - 1 - i, pw, ph - 1 - i), fill=a)
    right.paste(pod, (0, top), m)

    canvas = Image.new("RGB", (cw + pw, ch))
    canvas.paste(right, (cw, 0))
    # Khoảng giữa bảng và bục: kéo dãn cột nền ngoài cùng bên trái của bục
    edge = right.crop((0, 0, 2, ch)).resize((cw, ch), Image.BILINEAR).filter(ImageFilter.GaussianBlur(12))
    canvas.paste(edge, (0, 0))

    # Mặt nạ: mép phải của bảng mờ dần vào nền
    mask = Image.new("L", (cw, ch), 255)
    ImageDraw.Draw(mask).rectangle((cw - CARD_FADE, 0, cw, ch), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(CARD_FADE / 3))

    s = _Static()
    s.card = card.convert("RGBA")
    s.bg = canvas.convert("RGBA")
    s.mask, s.cw, s.ch, s.top = mask, cw, ch, top
    return s


# ----------------------------------------------------------------------
# VẼ ẢNH (mỗi lần chỉ vẽ avatar + chữ lên bản copy của ảnh đã ghép sẵn)
# ----------------------------------------------------------------------
def _draw_header(img, p, tab):
    """Vẽ lại thanh tab (supersample 4x cho viền mịn); mỗi lần chỉ sáng một tab."""
    ox, oy = CARD_CROP[0], CARD_CROP[1]
    k = 4
    x0, y0, x1, y1 = 268, 78, 776, 154
    layer = Image.new("RGBA", ((x1 - x0) * k, (y1 - y0) * k), (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)

    def rr(box, rad, **kw):
        g.rounded_rectangle(((box[0] - x0) * k, (box[1] - y0) * k,
                             (box[2] - x0) * k, (box[3] - y0) * k), radius=rad * k, **kw)

    bx0, by0, bx1, by1 = TAB_BAR
    rr(TAB_BAR, (by1 - by0) / 2, fill=(14, 28, 92, 255), outline=(38, 48, 142, 255), width=2 * k)
    for key, _label in TABS:
        if key != tab:
            continue
        cx = TAB_CENTERS[key]
        pill = (cx - TAB_PILL_W / 2, by0 + 4, cx + TAB_PILL_W / 2, by1 - 4)
        rr(pill, (pill[3] - pill[1]) / 2, fill=(255, 190, 28, 255), outline=(255, 224, 108, 255), width=2 * k)
    layer = layer.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    img.alpha_composite(layer, (x0 - ox, y0 - oy))

    cy = (by0 + by1) / 2 + 1
    f = _font(28)
    for key, label in TABS:
        active = key == tab
        p.text((TAB_CENTERS[key], cy), label, f,
               fill=(38, 30, 86) if active else (240, 244, 255),
               anchor="mm", stroke=0, shadow=False)


def _draw_left_top3(img, p, rows, avatars):
    ox, oy = CARD_CROP[0], CARD_CROP[1]
    for rank, cfg in LEFT_TOP3.items():
        if rank > len(rows):
            continue
        row = rows[rank - 1]
        cx, cy = cfg["c"]
        _paste_avatar_inside(img, cx - ox, cy - oy, cfg["r"], avatars.get(row.get("avatar_url")), RIM[rank])

        f, name = _fit_text(clean_name(row["name"]), cfg["name_px"], cfg["wmax"], cfg["name_px"] - 6,
                            row.get("fallback") or "Người chơi")
        p.text((cx, cfg["name_y"]), name, f, stroke=0)
        p.coin_line(cx, cfg["coin_y"], row["coin"], cfg["wmax"], cfg["coin_px"], align="center")


def _draw_rows(img, p, rows, avatars):
    ox, oy = CARD_CROP[0], CARD_CROP[1]
    for i, top in enumerate(ROW_TOPS):
        idx = 3 + i
        if idx >= len(rows):
            continue  # giữ nguyên avatar mẫu trong thanh trống
        row = rows[idx]
        cy = top + ROW_H / 2
        _paste_avatar_inside(img, ROW_AVATAR["x"] - ox, cy - oy, ROW_AVATAR["r"],
                             avatars.get(row.get("avatar_url")))
        pw = p.coin_line(ROW_RIGHT, cy, row["coin"], 200, 26, align="right")   # coin ở bên phải
        f, name = _fit_text(clean_name(row["name"]), 35, ROW_RIGHT - pw - 16 - ROW_TEXT_X, 24,
                            row.get("fallback") or "Người chơi")
        p.text((ROW_TEXT_X, cy), name, f, anchor="lm", stroke=0)               # tên ở giữa-trái


def _draw_right_top3(canvas, st, rows, avatars):
    """Bục bên phải (đã thu nhỏ sẵn): vẽ avatar thẳng ở cỡ cuối -> nét, không phải resize cả ảnh."""
    s = POD_SCALE
    for rank, cfg in RIGHT_AVATARS.items():
        if rank > len(rows):
            continue
        row = rows[rank - 1]
        cx = st.cw + (cfg["c"][0] - POD_X[0]) * s
        cy = st.top + cfg["c"][1] * s
        r = int(round(cfg["r"] * s))
        _paste_avatar_inside(canvas, cx, cy, r, avatars.get(row.get("avatar_url")),
                             RIM[rank], max(2.0, RIM_W * s))


def _encode(img):
    buf = io.BytesIO()
    if OUT_FORMAT == "PNG":
        img.save(buf, "PNG", compress_level=1)
    else:
        img.save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def render_board(rows, tab, avatars):
    st = _static()
    ox, oy = CARD_CROP[0], CARD_CROP[1]

    card = st.card.copy()                       # bảng (cỡ gốc, ~570x808)
    p = _Painter(ox, oy)
    _draw_header(card, p, tab)
    _draw_left_top3(card, p, rows, avatars)
    _draw_rows(card, p, rows, avatars)
    p.flush(card)

    canvas = st.bg.copy()                       # nền + bục đã ghép sẵn
    _draw_right_top3(canvas, st, rows, avatars)
    canvas.paste(card, (0, 0), st.mask)         # đặt bảng lên, mép phải mờ vào nền
    return _encode(canvas.convert("RGB"))


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
        return c.execute("SELECT user_id, coin FROM users ORDER BY coin DESC LIMIT 500").fetchall()


def query_active_by_coin(days, limit):
    since = time.time() - days * 86400
    with closing(sqlite3.connect(DB_NAME)) as c:
        return c.execute(
            "SELECT u.user_id, u.coin FROM users u "
            "JOIN top_activity a ON a.user_id = u.user_id "
            "WHERE a.last_active >= ? ORDER BY u.coin DESC LIMIT ?",
            (since, limit)).fetchall()


# ----------------------------------------------------------------------
# TẢI AVATAR (size 256 là đủ nét cho ảnh gửi đi; cache có giới hạn)
# ----------------------------------------------------------------------
_avatar_cache = {}
AVATAR_TTL = 3600
AVATAR_CACHE_MAX = 40


async def fetch_avatars(urls):
    urls = [u for u in set(urls) if u]

    async def one(session, url):
        hit = _avatar_cache.get(url)
        if hit and time.time() - hit[0] < AVATAR_TTL:
            return url, hit[1]
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                data = await r.read()
                _avatar_cache[url] = (time.time(), data)
                if len(_avatar_cache) > AVATAR_CACHE_MAX:
                    oldest = min(_avatar_cache, key=lambda k: _avatar_cache[k][0])
                    _avatar_cache.pop(oldest, None)
                return url, data
        except Exception:
            return url, None

    need = [u for u in urls if not (_avatar_cache.get(u) and time.time() - _avatar_cache[u][0] < AVATAR_TTL)]
    if not need:
        return {u: _avatar_cache[u][1] for u in urls}
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
            attachments=[discord.File(io.BytesIO(png), OUT_NAME)], view=self)

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
_user_cache = {}          # uid -> (time, name, avatar_url, fallback)  (chỉ cho người ngoài server)
USER_TTL = 600
USER_CACHE_MAX = 200


class TopCog(commands.Cog):
    FRESH = 120       # giây: mỗi bảng tối đa 1 lần vẽ lại / 2 phút (chỉ khi có người gọi)
    MAX_STALE = 900   # giây: cũ hơn nữa thì buộc phải chờ dựng mới
    CACHE_MAX = 12    # số bảng tối đa giữ trong RAM

    def __init__(self, bot):
        self.bot = bot
        self._png_cache = {}      # (tab, guild_id) -> (time, bytes, sig)
        self._refreshing = set()
        self._prewarmed = False
        self._ok = False
        self._render_sem = asyncio.Semaphore(1)   # mỗi lúc chỉ vẽ 1 ảnh, không dồn CPU

    async def cog_load(self):
        ensure_activity_table()
        try:
            await asyncio.to_thread(_static)    # ghép sẵn phần tĩnh 1 lần lúc khởi động
        except Exception as e:
            print(f"[Top] ⚠️ Chưa dựng được bảng xếp hạng ảnh: {e}")
            return
        self._ok = True

    async def cog_unload(self):
        self._ok = False

    @commands.Cog.listener()
    async def on_ready(self):
        """Khởi động: dựng sẵn bảng Server cho tối đa 3 server đầu (giãn cách, chỉ 1 lần)."""
        if self._prewarmed or not self._ok:
            return
        self._prewarmed = True
        for guild in list(self.bot.guilds)[:3]:
            try:
                await self._build(("server", guild.id), "server", guild)
            except Exception as e:
                print(f"[Top] lỗi dựng sẵn server {guild.id}: {e}")
            await asyncio.sleep(3)

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        try:
            await asyncio.to_thread(touch_activity, ctx.author.id)
        except Exception:
            pass

    async def _resolve(self, uid, guild):
        uid = int(uid)
        member = guild.get_member(uid) if guild else None
        if member is None:
            hit = _user_cache.get(uid)
            if hit and time.time() - hit[0] < USER_TTL:
                return hit[1], hit[2], hit[3]
        user = member or self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                res = ("Người chơi ẩn", None, "Người chơi ẩn")
                self._remember(uid, res)
                return res
        try:
            url = user.display_avatar.replace(size=256, format="png").url
        except Exception:
            url = str(user.display_avatar.url)
        res = (user.display_name, url, user.name)
        if member is None:
            self._remember(uid, res)
        return res

    @staticmethod
    def _remember(uid, res):
        if len(_user_cache) >= USER_CACHE_MAX:
            _user_cache.clear()
        _user_cache[uid] = (time.time(),) + res

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
        return [{"name": n, "coin": coin, "avatar_url": u, "fallback": fb}
                for (n, u, fb), (_, coin) in zip(results, picked)]

    async def _build(self, key, tab, guild):
        rows = await self._get_rows(tab, guild)
        sig = tuple((r["name"], r["coin"], r["avatar_url"]) for r in rows)
        hit = self._png_cache.get(key)
        if hit and len(hit) > 2 and hit[2] == sig:
            self._png_cache[key] = (time.time(), hit[1], sig)   # dữ liệu y nguyên -> khỏi vẽ lại
            return hit[1]
        avatars = await fetch_avatars([r["avatar_url"] for r in rows])
        async with self._render_sem:
            png = await asyncio.to_thread(render_board, rows, tab, avatars)
        self._png_cache[key] = (time.time(), png, sig)
        while len(self._png_cache) > self.CACHE_MAX:     # giữ cache gọn
            oldest = min(self._png_cache, key=lambda k: self._png_cache[k][0])
            self._png_cache.pop(oldest, None)
        return png

    async def _refresh(self, key, tab, guild):
        try:
            await self._build(key, tab, guild)
        except Exception as e:
            print(f"[Top] lỗi làm mới {key}: {e}")
        finally:
            self._refreshing.discard(key)

    async def _warm_tabs(self):
        """Dựng sẵn Tuần/Tháng ở nền để bấm nút là ra ngay (bỏ qua nếu còn mới)."""
        for tab in ("week", "month"):
            key = (tab, 0)
            hit = self._png_cache.get(key)
            if hit and time.time() - hit[0] < self.FRESH:
                continue
            try:
                await self._build(key, tab, None)
            except Exception as e:
                print(f"[Top] lỗi dựng sẵn {tab}: {e}")
            await asyncio.sleep(1)

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
        view.message = await ctx.send(file=discord.File(io.BytesIO(png), OUT_NAME), view=view)
        asyncio.create_task(self._warm_tabs())


async def setup(bot):
    await bot.add_cog(TopCog(bot))


# ----------------------------------------------------------------------
# XEM THỬ NHANH (không cần Discord):  python top.py
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import random

    def _fake_avatar(seed):
        rnd = random.Random(seed)
        im = Image.new("RGB", (256, 256), tuple(rnd.randint(40, 220) for _ in range(3)))
        dr = ImageDraw.Draw(im)
        dr.ellipse((85, 45, 171, 131), fill=(255, 255, 255))
        dr.ellipse((45, 145, 211, 310), fill=(255, 255, 255))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    names = ["Khánh Sơn 👑", "Nguyễn Thị Hương Giang", "Minh Tú", "Ông Trùm Server Vĩ Đại",
             "ツ Sakura", "Trần Văn A", "bé Mèo"]
    coins = [12_345_678_900, 4_500_000_000, 987_654_321, 120_500_000, 8_800_000, 250_000, 1_200]
    rows = [{"name": n, "coin": c, "avatar_url": f"u{i}"} for i, (n, c) in enumerate(zip(names, coins))]
    av = {f"u{i}": _fake_avatar(i) for i in range(7)}
    for t in ("server", "week", "month"):
        t0 = time.time()
        data = render_board(rows, t, av)
        fn = f"top_preview_{t}.{OUT_NAME.split('.')[-1]}"
        Path(fn).write_bytes(data)
        print(f"đã tạo {fn}  ({(time.time() - t0) * 1000:.0f} ms, {len(data) // 1024} KB)")
