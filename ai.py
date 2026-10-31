# ============================================================
# ai.py
# AI CHAT cho Discord - Gemini API
#
# Tính năng:
#   @Bot <câu hỏi>       -> gọi AI ở bất kỳ kênh nào
#   Reply tin nhắn bot   -> bot trả lời tiếp
#   .kenhai              -> bật kênh AI hiện tại
#   .kenhaioff           -> tắt kênh AI hiện tại
#   .xoakenhai           -> xóa kênh AI của server
#   .camai               -> cấm AI trong kênh hiện tại
#   .ai <nội dung>       -> gọi AI trực tiếp
#   .aiclear             -> xóa memory kênh hiện tại
#   .aistatus            -> xem trạng thái
#   .kmodel [1/2/3]      -> đổi model (không nhập số = tự chọn model
#                            còn hoạt động kế tiếp; có nhập số = đổi
#                            đúng model đó NẾU nó không đang lỗi/cooldown)
#   .key+1 / .key+2 / .key+3 -> đổi API key đang dùng (key 1 tới 3)
#   .kenhchao            -> đặt kênh hiện tại làm kênh chào thành viên mới
#   .kenhchaooff         -> tắt kênh chào thành viên mới
#
# ẢNH (VISION):
#   - Bot đọc được ảnh đính kèm trong tin nhắn (kể cả trong .ai và khi
#     tag bot/reply bot) nhờ các model Gemini ở trên hỗ trợ đa phương
#     thức (multimodal) sẵn, không cần key riêng cho ảnh.
#
# CHÀO THÀNH VIÊN MỚI:
#   - Khi có người mới vào server, nếu server đã đặt kênh chào bằng
#     .kenhchao, bot sẽ nhờ AI tạo 1 câu chào NGẪU NHIÊN, KHÁC NHAU cho
#     từng người (không dùng công thức cố định) và gửi vào kênh đó.
#
# MODEL:
#   1. Gemini 3.5 Flash-Lite
#   2. Gemini 3.7 Flash
#   3. Gemini 3.6 Flash
#
# FALLBACK:
#   - Hỗ trợ tối đa 3 API KEY, đổi bằng .key+1 / .key+2 / .key+3.
#   - Model hiện tại hoạt động -> KHÔNG ĐỔI.
#   - Model lỗi/quá tải -> đổi model NGAY.
#   - Không retry lại model vừa lỗi.
#   - Không tự động quay vòng key (đổi key phải dùng lệnh .key+…).
#   - Thành công ở model nào -> ghi nhớ model đó.
#   - Đổi model KHÔNG làm mất memory.
#   - CHỈ dùng Gemini (không còn dịch vụ AI dự phòng nào khác).
#
# MEMORY:
#   - Lưu SQLite.
#   - Restart Railway vẫn còn memory.
#   - Memory riêng theo server + channel.
#
# EMOJI SERVER:
#   - AI được cho biết danh sách emoji tuỳ chỉnh (custom emoji) của
#     server đang chat, và có thể chèn thẳng emoji đó vào câu trả lời
#     khi phù hợp (xem build_emoji_hint()).
#   - Trước khi gửi, bot tự kiểm tra + SỬA LẠI đúng id/tên emoji
#     (xem fix_emoji_tags()) để tránh lỗi hiện chữ thô <:ten:id>
#     ngoài Discord khi AI gõ sai id.
#
# HÀNH VI ĐẶC BIỆT CỦA AI:
#   - AI có thể chọn CHỈ THẢ REACTION (không nhắn chữ) bằng token
#     [[REACT:ten_emoji]].
#   - AI có thể chọn IM LẶNG, không trả lời, bằng token [[SILENT]].
#   - AI có thể xin gửi kèm GIF (qua Tenor) bằng token [[GIF:từ khoá]].
#   - Các token này được bot bóc tách ở tầng xử lý, KHÔNG hiện ra
#     cho người dùng thấy.
#
# GIF (Tenor):
#   - Tenor là kho GIF Google dùng ngay trong trình chọn GIF gốc của
#     Discord, nên đây cũng là 1 trong những kho GIF lớn nhất hiện có.
#   - Cần điền TENOR_API_KEY (miễn phí) ở phần cấu hình bên dưới. Lấy
#     key tại: https://developers.google.com/tenor/guides/quickstart
#   - Nếu để trống -> tính năng GIF tự tắt, AI không được dùng
#     token [[GIF:...]] nữa.
#   - AI CHỈ được gửi GIF khi người dùng CHỦ ĐỘNG xin GIF một cách rõ
#     ràng (vd: "cho xin cái gif", "gửi gif đi"), KHÔNG tự ý chèn GIF
#     khi không ai yêu cầu, để tránh spam GIF liên tục.
#
# Cài:
#   pip install discord.py
#
# API KEY:
#   Điền tối đa 3 key Gemini vào GEMINI_API_KEYS bên dưới. Lấy key
#   MIỄN PHÍ tại: https://aistudio.google.com/apikey
#   Dùng lệnh .key+1 / .key+2 / .key+3 để đổi qua key khác lúc nào
#   cũng được (vd: key 1 hết quota thì đổi sang key 2).
#
# QUYỀN:
#   - Manage Server dùng được lệnh quản trị AI.
#   - Owner ID được bot.py công nhận cũng dùng được.
#
# ============================================================
 
import asyncio
import base64
import contextlib
import json
import os
import random
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
 
import discord
from discord.ext import commands
 
 
# ============================================================
# API KEY
# ============================================================
#
# HỖ TRỢ TỐI ĐA 3 KEY (key 1, key 2, key 3).
#
# Bot mặc định dùng KEY 1. Dùng lệnh .key+1 / .key+2 / .key+3 để
# đổi qua key khác bất cứ lúc nào (vd: key 1 hết quota thì đổi
# sang key 2). Bot KHÔNG tự động quay vòng key, phải đổi bằng lệnh.
#
# Lấy API key Gemini MIỄN PHÍ tại:
# https://aistudio.google.com/apikey
#
# Điền key vào từng dòng bên dưới. Key nào chưa dùng thì cứ để
# nguyên placeholder (bắt đầu bằng "DAN_KEY_"), bot sẽ tự bỏ qua,
# không lỗi.
#
# Ví dụ:
#
# GEMINI_API_KEYS = [
#     "AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxx",  # Key 1
#     "AIzaSyyyyyyyyyyyyyyyyyyyyyyyyyyyy",  # Key 2
#     "AIzaSyzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # Key 3
# ]
#
# ============================================================
 
GEMINI_API_KEYS = [
    "AQ.Ab8RN6I8zl4QVDsJTkvtD3huF7LUOpEHe0_YHLgQP-9lkcoJug",  # Key 1
    "AQ.Ab8RN6JbK25j7G_IETcFZIBcyqGHm58l6iJq6D6ovsRimNwI5A",  # Key 2
]
 
 
# ============================================================
# MODELS
# ============================================================
 
GEMINI_MODELS = [
    # Xep Flash Lite len DAU vi quota RPD (request/ngay) cao
    # hon han 2 model Flash thuong (~500/ngay so voi ~20/ngay,
    # do chinh minh kiem tra thuc te tren Google AI Studio
    # 19/09/2026). Model chinh cu (3.7-flash) da bi khoa het
    # quota (RPD 21/20) nen bot lien tuc bao loi truoc day.
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]
 
 
# Model mặc định.
#
# Bot sẽ dùng model này cho tới khi model thật sự lỗi.
#
CURRENT_MODEL_INDEX = 0
 
 
# ============================================================
# CẤU HÌNH
# ============================================================
 
AI_DB = os.getenv(
    "AI_DB",
    "ai_config.db"
)
 
COOLDOWN_SECONDS = 3
 
# Số message memory.
# 20 = khoảng 10 lượt hỏi + đáp.
MAX_HISTORY = 20
 
MAX_MESSAGE_LENGTH = 4000
 
# Số token trả lời.
#
# Giảm xuống để Gemini trả lời NHANH hơn (ít token hơn = generate
# nhanh hơn). Nếu thấy câu trả lời hay bị cụt giữa chừng thì tăng
# lại số này lên.
MAX_OUTPUT_TOKENS = 300
 
 
# ============================================================
# DANH TÍNH BOT
# ============================================================
#
# ID thật của bot (user id Discord của chính con bot) được LẤY TỰ ĐỘNG
# lúc chạy (self.bot.user.id), không hardcode ở đây vì hardcode rất dễ
# bị sai/lệch khi đổi bot.
#
# ID dưới đây là ID của CHỦ BOT (người quản lý/chủ sở hữu bot), dùng
# để AI trả lời đúng khi có người hỏi "ai là chủ bot / admin bot".
#
# ============================================================
 
BOT_NAME = "KS-BOT"
 
OWNER_DISCORD_ID = "1404399385965498479"
 
# Gemini 3:
# low = ưu tiên tốc độ.
THINKING_LEVEL = "low"
 
# Số lượng emoji tuỳ chỉnh tối đa gửi cho AI biết (tránh prompt quá dài
# nếu server có hàng trăm emoji).
MAX_EMOJI_HINTS = 60
 
# Số ảnh đính kèm tối đa mỗi tin nhắn mà bot sẽ đọc (tránh payload quá
# nặng / quá lâu nếu người dùng gửi quá nhiều ảnh cùng lúc).
MAX_IMAGE_ATTACHMENTS = 3
 
# Dung lượng tối đa (byte) của 1 ảnh mà bot chịu tải để đọc.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
 
 
# ============================================================
# GIF (Tenor)
# ============================================================
#
# Cho phép AI tự chèn GIF vào câu trả lời khi phù hợp (vd: có người
# xin gif, đùa giỡn bằng gif cho sinh động).
#
# Lấy API key Tenor miễn phí tại:
# https://developers.google.com/tenor/guides/quickstart
#
# Điền key vào đây. Nếu để nguyên placeholder (bắt đầu bằng "DAN_KEY_")
# thì tính năng GIF sẽ tự động TẮT, AI sẽ không được gợi ý dùng
# token [[GIF:...]] nữa (không lỗi, chỉ im lặng bỏ qua tính năng).
#
# ============================================================
 
TENOR_API_KEY = "DAN_KEY_TENOR_O_DAY"
 
TENOR_SEARCH_URL = "https://tenor.googleapis.com/v2/search"
 
# Lấy ngẫu nhiên 1 trong số này để đỡ bị lặp gif mỗi lần hỏi cùng từ khoá.
MAX_GIF_RESULTS = 8
 
 
# ============================================================
# QUAN TRỌNG
# ============================================================
#
# KHÔNG RETRY MODEL VỪA LỖI.
#
# Khi lỗi:
#
# Model 1 -> Model 2 NGAY
# Model 2 -> Model 3 NGAY
#
# Không quay lại model vừa lỗi trong cùng request.
#
# Cooldown chỉ dùng để tránh model lỗi bị gọi lại liên tục
# ở những request sau.
#
# ============================================================
 
MODEL_COOLDOWN_SECONDS = 60

# Cooldown rieng, LAU HON HAN, danh cho loi HET QUOTA (429 /
# RESOURCE_EXHAUSTED). Loi nay khong tu het trong vai giay/phut nhu
# 503 qua tai thong thuong -- quota chi reset theo ngay/thang cua
# Google -- nen goi lai model ngay sau 60s (MODEL_COOLDOWN_SECONDS)
# chi ton them request vo ich va van chac chan loi.
#
# 900s = 15 phut. Trong 15 phut do, moi cau hoi se tu dong bo qua
# thang model nay va chuyen sang model/key khac ngay, khong lam
# nguoi dung phai cho lau hay bot spam goi API vo ich.
QUOTA_COOLDOWN_SECONDS = 900
 
# Timeout request.
REQUEST_TIMEOUT = 35


# ============================================================
# WEB SEARCH (Google Search grounding)
# ============================================================
#
# Khi bat, Gemini co the tu CHU DONG len mang tim thong tin (web,
# app, trang Google, video YouTube, v.v...) truoc khi tra loi, thay
# vi chi tra loi dua vao du lieu huan luyen cu. Day la tinh nang
# CHINH THUC cua Gemini API ("Grounding with Google Search"), khong
# can them key hay dich vu nao khac ngoai GEMINI_API_KEYS da co.
#
# Tat bang cach doi thanh False neu khong muon AI tu search.
#
# ============================================================

ENABLE_WEB_SEARCH = True


# ============================================================
# TỰ PHỤC HỒI KHI LỖI (quá tải / hết quota)
# ============================================================
#
# 1) RETRY_OVERLOADED:
#    Lỗi 500/502/503/504 ("model is overloaded") là do máy chủ Google
#    bận trong chốc lát, thường thử lại sau 1-2 giây là được. Bật thì
#    bot thử lại ĐÚNG 1 LẦN sau OVERLOAD_RETRY_DELAY giây trước khi
#    bỏ sang model khác.
#
# 2) FALLBACK_NO_SEARCH:
#    Quota Google Search grounding tính RIÊNG và thấp hơn quota chat
#    thường. Nếu bị 429 khi đang bật search, bot tự thử lại cùng model
#    NHƯNG TẮT search (trả lời bằng kiến thức có sẵn) thay vì báo lỗi.
#
# 3) AUTO_ROTATE_KEYS:
#    Khi key đang dùng bị 429/lỗi key, tự thử các key còn lại trong
#    GEMINI_API_KEYS. Chỉ có ích nếu các key nằm ở các PROJECT Google
#    khác nhau (quota tính theo project). Key nào chạy được sẽ được
#    ghi nhớ làm key hiện tại. Tắt = quay về kiểu đổi tay bằng .key+N.
#
# ============================================================

RETRY_OVERLOADED = True
OVERLOAD_RETRY_DELAY = 2

FALLBACK_NO_SEARCH = True

AUTO_ROTATE_KEYS = True


# ============================================================
# ICON "DANG SUY NGHI"
# ============================================================
#
# Vi AI co luc tra loi kha lau (nhat la khi vua bat web search o
# tren), bot se gui ngay 1 tin nhan bao "dang suy nghi" kem icon ben
# duoi de nguoi dung biet bot da nhan duoc cau hoi. Khi AI tra loi
# xong, tin nhan nay se TU XOA va bot gui cau tra loi that ra.
#
# Doi icon bang emoji tuy chinh khac cua server neu muon (giu dung
# dinh dang <a:ten:id> cho emoji dong hoac <:ten:id> cho emoji tinh).
# De trong ("") neu khong muon icon.
#
# ============================================================

THINKING_TEXT = "Đang suy nghĩ..."
THINKING_EMOJI = "<a:Chatbot:1550835864924328017>"


# ============================================================
# URL GEMINI
# ============================================================
 
def gemini_api_url(model: str):
 
    return (
        "https://generativelanguage.googleapis.com/"
        "v1beta/models/"
        f"{model}:generateContent"
    )
 
 
# ============================================================
# EMOJI SERVER
# ============================================================
 
def build_emoji_hint(guild):
    """Trả về đoạn text mô tả emoji tuỳ chỉnh của server, để nhét vào
    system prompt cho AI biết và có thể dùng trực tiếp trong câu trả lời.
 
    Chỉ lấy emoji còn "available" (server dưới mức boost cần thiết sẽ
    có emoji bị khoá, is_usable/available = False, không nên gợi ý dùng
    vì bot gõ ra sẽ hiện lỗi/không hiện được).
    """
 
    if not guild:
        return ""
 
    emojis = [
        e for e in guild.emojis
        if getattr(e, "available", True)
    ]
 
    if not emojis:
        return ""
 
    emojis = emojis[:MAX_EMOJI_HINTS]
 
    lines = []
 
    for e in emojis:
 
        tag = (
            f"<a:{e.name}:{e.id}>"
            if e.animated
            else f"<:{e.name}:{e.id}>"
        )
 
        lines.append(f"{e.name} = {tag}")
 
    return (
        "\n\nServer Discord này có các emoji tuỳ chỉnh sau. Khi phù hợp "
        "và tự nhiên, bạn có thể chèn thẳng emoji này vào câu trả lời. "
        "Chỉ cần gõ ĐÚNG TÊN emoji theo cú pháp bên trái dấu '=' bên "
        "trong ngoặc nhọn, ví dụ nếu tên là 'echcuoi' thì gõ "
        "<:echcuoi:0>, bot sẽ TỰ SỬA lại id đúng trước khi gửi, nên bạn "
        "không cần lo id có đúng hay không, chỉ cần đúng TÊN. Không lạm "
        "dụng, chỉ dùng khi hợp ngữ cảnh:\n"
        + "\n".join(lines)
    )
 
 
# ============================================================
# SỬA EMOJI TAG (chống lỗi hiện chữ thô ngoài Discord)
# ============================================================
#
# AI thỉnh thoảng gõ sai id của emoji (bịa id, hoặc nhớ nhầm id cũ),
# khiến Discord không nhận ra emoji đó và hiện nguyên văn dạng chữ
# <a:ten:12345> rất xấu thay vì hiện ảnh emoji.
#
# Hàm dưới đây quét toàn bộ tag dạng <a:ten:id> hoặc <:ten:id> trong
# câu trả lời, đối chiếu với danh sách emoji THẬT của server theo TÊN,
# rồi:
#   - Nếu tên khớp -> viết lại tag với đúng id + đúng loại
#     animated/không animated.
#   - Nếu tên không khớp emoji nào -> bỏ tag đó (tránh hiện chữ lỗi).
#
# ============================================================
 
EMOJI_TAG_RE = re.compile(
    r"<a?:([a-zA-Z0-9_]{2,32}):(\d+)>"       # tag day du: <a:ten:id>
    r"|<a?:([a-zA-Z0-9_]{2,32})>"             # thieu id: <a:ten> hoac <:ten>
    r"|:([a-zA-Z0-9_]{2,32}):"                # shortcode tran: :ten:
)

# Don khoang trang thua de lai sau khi bo 1 tag emoji khong khop
# (vd "Chao  ban" -> "Chao ban", "vui !" -> "vui!").
EXTRA_SPACE_RE = re.compile(r"[ \t]{2,}")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")


def fix_emoji_tags(text, guild):
    """Sửa lại các tag emoji tuỳ chỉnh mà AI viết ra cho đúng id thật
    của server, kể cả khi AI viết thiếu ngoặc/thiếu id/chỉ viết
    shortcode trần (:ten:). Quét 1 LƯỢT DUY NHẤT (1 regex gộp cả 3
    kiểu tag) để không bị xử lý chồng lên tag vừa sửa đúng ở bước
    trước đó. Tag dạng ngoặc nhọn không khớp emoji thật nào của
    server thì bị bỏ hẳn (tránh hiện chữ thô lỗi); shortcode trần
    không khớp thì giữ nguyên (có thể chỉ là dấu ':' bình thường
    trong câu, không phải AI đang cố gõ emoji)."""

    if not guild or not text:
        return text

    emoji_by_name = {
        e.name.lower(): e
        for e in guild.emojis
        if getattr(e, "available", True)
    }

    def to_tag(emoji):

        return (
            f"<a:{emoji.name}:{emoji.id}>"
            if emoji.animated
            else f"<:{emoji.name}:{emoji.id}>"
        )

    def replace(match):

        # group 1 = ten trong tag day du (<a:ten:id>)
        # group 3 = ten trong tag thieu id (<a:ten> / <:ten>)
        # group 4 = ten trong shortcode tran (:ten:)
        name = (
            match.group(1)
            or match.group(3)
            or match.group(4)
        )

        emoji = emoji_by_name.get(
            name.lower()
        )

        if emoji:
            return to_tag(emoji)

        # Shortcode tran khong khop -> giu nguyen, có thể chỉ là
        # dấu ':' bình thường, không phải cú pháp emoji hỏng.
        if match.group(4) is not None:
            return match.group(0)

        # Tag dạng <...> khong khop emoji nao -> bỏ hẳn, tránh hiện
        # chữ thô lỗi kiểu <a:ten_bia:12345> ngoài Discord.
        return ""

    fixed = EMOJI_TAG_RE.sub(replace, text)

    # Dọn khoảng trắng thừa còn sót lại sau khi bỏ tag lỗi.
    fixed = EXTRA_SPACE_RE.sub(" ", fixed)
    fixed = SPACE_BEFORE_PUNCT_RE.sub(r"\1", fixed)

    return fixed.strip()

 
 
# ============================================================
# ACTION TOKEN CỦA AI (REACT / SILENT / GIF)
# ============================================================
#
# AI có thể trả lời bằng 1 trong các token đặc biệt dưới đây thay vì
# (hoặc kèm theo) câu trả lời chữ bình thường. Bot bóc tách các token
# này ở tầng xử lý tin nhắn, người dùng KHÔNG thấy token thô.
#
#   [[SILENT]]          -> bot không nhắn gì cả.
#   [[REACT:ten_emoji]] -> bot chỉ thả reaction, không nhắn chữ.
#   [[GIF:từ khoá]]     -> bot tìm 1 gif theo từ khoá và gửi kèm.
#
# ============================================================
 
SILENT_TOKEN_RE = re.compile(
    r"\[\[SILENT\]\]",
    re.IGNORECASE
)
 
REACT_TOKEN_RE = re.compile(
    r"\[\[REACT:(.+?)\]\]",
    re.IGNORECASE
)
 
GIF_TOKEN_RE = re.compile(
    r"\[\[GIF:(.+?)\]\]",
    re.IGNORECASE
)
 
 
def parse_ai_action(text):
    """Phân tích xem AI muốn làm gì với câu trả lời vừa nhận được.
 
    Trả về tuple (action, payload):
 
        ("silent", None)
        ("react", "ten_emoji_hoac_unicode")
        ("gif", (clean_text, query))
        ("text", text)
    """
 
    stripped = text.strip()
 
    if SILENT_TOKEN_RE.fullmatch(stripped):
        return ("silent", None)
 
    react_match = REACT_TOKEN_RE.fullmatch(
        stripped
    )
 
    if react_match:
        return (
            "react",
            react_match.group(1).strip()
        )
 
    gif_match = GIF_TOKEN_RE.search(text)
 
    if gif_match:
 
        query = gif_match.group(1).strip()
 
        clean_text = GIF_TOKEN_RE.sub(
            "",
            text
        ).strip()
 
        return (
            "gif",
            (clean_text, query)
        )
 
    return ("text", text)
 
 
def strip_action_tokens(text):
    """Bỏ hết các action token, dùng khi lưu vào memory, để lịch sử
    hội thoại lưu lại sạch sẽ, không làm model học nhầm cú pháp lạ."""
 
    cleaned = SILENT_TOKEN_RE.sub(
        "",
        text
    )
 
    cleaned = REACT_TOKEN_RE.sub(
        "",
        cleaned
    )
 
    cleaned = GIF_TOKEN_RE.sub(
        "",
        cleaned
    )
 
    return cleaned.strip()
 
 
# ============================================================
# DATABASE
# ============================================================
 
def db_connect():
 
    conn = sqlite3.connect(
        AI_DB,
        timeout=10
    )
 
    conn.execute(
        "PRAGMA journal_mode=WAL"
    )
 
    conn.execute(
        "PRAGMA synchronous=NORMAL"
    )
 
    return conn
 
 
def init_db():
 
    conn = db_connect()
 
    # Kênh AI
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL
        )
    """)
 
    # Kênh chào thành viên mới
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_welcome_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL
        )
    """)
 
    # Kênh cấm AI
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_blocked_channels (
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            PRIMARY KEY (
                guild_id,
                channel_id
            )
        )
    """)
 
    # Memory AI
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
 
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_memory_channel
        ON ai_memory (
            guild_id,
            channel_id,
            id
        )
    """)
 
    conn.commit()
    conn.close()
 
 
init_db()
 
 
# ============================================================
# AI CHANNEL
# ============================================================
 
def get_ai_channel(guild_id: int):
 
    conn = db_connect()
 
    row = conn.execute(
        """
        SELECT channel_id
        FROM ai_settings
        WHERE guild_id = ?
        """,
        (
            str(guild_id),
        )
    ).fetchone()
 
    conn.close()
 
    if not row:
        return None
 
    return int(row[0])
 
 
def set_ai_channel(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    conn.execute(
        """
        INSERT INTO ai_settings(
            guild_id,
            channel_id
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id
        """,
        (
            str(guild_id),
            str(channel_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
def remove_ai_channel(guild_id: int):
 
    conn = db_connect()
 
    conn.execute(
        """
        DELETE FROM ai_settings
        WHERE guild_id = ?
        """,
        (
            str(guild_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
# ============================================================
# KÊNH CHÀO THÀNH VIÊN MỚI
# ============================================================
 
def get_welcome_channel(guild_id: int):
 
    conn = db_connect()
 
    row = conn.execute(
        """
        SELECT channel_id
        FROM ai_welcome_settings
        WHERE guild_id = ?
        """,
        (
            str(guild_id),
        )
    ).fetchone()
 
    conn.close()
 
    if not row:
        return None
 
    return int(row[0])
 
 
def set_welcome_channel(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    conn.execute(
        """
        INSERT INTO ai_welcome_settings(
            guild_id,
            channel_id
        )
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET
            channel_id = excluded.channel_id
        """,
        (
            str(guild_id),
            str(channel_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
def remove_welcome_channel(guild_id: int):
 
    conn = db_connect()
 
    conn.execute(
        """
        DELETE FROM ai_welcome_settings
        WHERE guild_id = ?
        """,
        (
            str(guild_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
# ============================================================
# BLOCK CHANNEL
# ============================================================
 
def set_ai_blocked(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    conn.execute(
        """
        INSERT OR IGNORE INTO ai_blocked_channels(
            guild_id,
            channel_id
        )
        VALUES (?, ?)
        """,
        (
            str(guild_id),
            str(channel_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
def is_ai_blocked(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    row = conn.execute(
        """
        SELECT 1
        FROM ai_blocked_channels
        WHERE guild_id = ?
        AND channel_id = ?
        """,
        (
            str(guild_id),
            str(channel_id),
        )
    ).fetchone()
 
    conn.close()
 
    return row is not None
 
 
# ============================================================
# MEMORY DATABASE
# ============================================================
 
def load_memory(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    rows = conn.execute(
        """
        SELECT role, content
        FROM ai_memory
        WHERE guild_id = ?
        AND channel_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            str(guild_id),
            str(channel_id),
            MAX_HISTORY,
        )
    ).fetchall()
 
    conn.close()
 
    rows.reverse()
 
    return [
        {
            "role": role,
            "content": content,
        }
        for role, content in rows
    ]
 
 
def save_memory(
    guild_id: int,
    channel_id: int,
    role: str,
    content: str
):
 
    conn = db_connect()
 
    conn.execute(
        """
        INSERT INTO ai_memory(
            guild_id,
            channel_id,
            role,
            content,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(guild_id),
            str(channel_id),
            role,
            content,
            time.time(),
        )
    )
 
    # Giữ lại đúng MAX_HISTORY message gần nhất.
    conn.execute(
        """
        DELETE FROM ai_memory
        WHERE guild_id = ?
        AND channel_id = ?
        AND id NOT IN (
            SELECT id
            FROM ai_memory
            WHERE guild_id = ?
            AND channel_id = ?
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (
            str(guild_id),
            str(channel_id),
            str(guild_id),
            str(channel_id),
            MAX_HISTORY,
        )
    )
 
    conn.commit()
    conn.close()
 
 
def clear_memory(
    guild_id: int,
    channel_id: int
):
 
    conn = db_connect()
 
    conn.execute(
        """
        DELETE FROM ai_memory
        WHERE guild_id = ?
        AND channel_id = ?
        """,
        (
            str(guild_id),
            str(channel_id),
        )
    )
 
    conn.commit()
    conn.close()
 
 
# ============================================================
# COG
# ============================================================
 
class AICog(commands.Cog):
 
    def __init__(self, bot):
 
        self.bot = bot
 
        # ----------------------------------------------------
        # MEMORY CACHE
        # ----------------------------------------------------
 
        self.history = defaultdict(
            lambda: deque(
                maxlen=MAX_HISTORY
            )
        )
 
        self.history_loaded = set()
 
        # ----------------------------------------------------
        # COOLDOWN USER
        # ----------------------------------------------------
 
        self.cooldowns = {}
 
        # ----------------------------------------------------
        # CHANNEL LOCK
        # ----------------------------------------------------
 
        self.channel_locks = defaultdict(
            asyncio.Lock
        )
 
        # ----------------------------------------------------
        # MODEL STATE
        # ----------------------------------------------------
 
        # Key đang dùng (đổi bằng .key+1 / .key+2 / .key+3).
        self.current_key_index = 0
 
        # Model hiện tại.
        self.current_model_index = CURRENT_MODEL_INDEX
 
        # model -> timestamp
        self.model_unavailable_until = {}
 
        # lock trạng thái fallback
        self.state_lock = asyncio.Lock()
 
    # ========================================================
    # API READY
    # ========================================================
 
    def api_ready(self):
 
        for key in GEMINI_API_KEYS:
 
            if (
                key
                and key != "DAN_KEY_MOI_VAO_DAY"
                and not key.startswith("DAN_KEY_")
            ):
 
                return True
 
        return False
 
    # ========================================================
    # GET ACTIVE KEY
    # ========================================================
 
    def get_api_key(self):
 
        if not GEMINI_API_KEYS:
            return None
 
        index = (
            self.current_key_index
            % len(GEMINI_API_KEYS)
        )
 
        key = GEMINI_API_KEYS[index]
 
        if (
            not key
            or key.startswith("DAN_KEY_")
        ):
            return None
 
        return key
 
    # ========================================================
    # SWITCH KEY
    # ========================================================
 
    async def switch_key(
        self,
        ctx,
        index
    ):
 
        if (
            index < 0
            or index >= len(GEMINI_API_KEYS)
        ):
 
            await ctx.send(
                f"❌ Chỉ có {len(GEMINI_API_KEYS)} key "
                f"(dùng `.key+1` đến `.key+{len(GEMINI_API_KEYS)}`).",
                delete_after=6
            )
 
            return
 
        key = GEMINI_API_KEYS[index]
 
        if (
            not key
            or key.startswith("DAN_KEY_")
        ):
 
            await ctx.send(
                f"❌ Key {index + 1} chưa được điền trong "
                "`GEMINI_API_KEYS`.",
                delete_after=6
            )
 
            return
 
        async with self.state_lock:
 
            self.current_key_index = index
 
        await ctx.send(
            f"🔑 Đã chuyển sang **Key {index + 1}**.",
            delete_after=6
        )
 
    # ========================================================
    # "DANG SUY NGHI..."
    # ========================================================
    #
    # Thay vi chi hien dau 3 cham "dang go" cua Discord (de mat chu
    # y / kho biet bot co dang xu ly hay bi dung), bot gui han 1 tin
    # nhan kem icon THINKING_EMOJI ngay khi nhan cau hoi. Khi AI tra
    # loi xong (du nhanh hay cham, ke ca khi co goi web search), tin
    # nhan nay tu xoa va bot gui cau tra loi that, giup kenh chat gon
    # gang, de doc hon khi co nhieu loai noi dung (link web, app,
    # Google Docs/Sheets, YouTube...) xen ke.
    # ========================================================

    @contextlib.asynccontextmanager
    async def thinking_indicator(self, channel):

        thinking_msg = None

        try:

            text = THINKING_TEXT

            if THINKING_EMOJI:

                text = f"{THINKING_TEXT} {THINKING_EMOJI}"

            thinking_msg = await channel.send(text)

        except Exception as e:

            print(
                "[THINKING SEND ERROR] "
                f"{repr(e)}"
            )

        try:

            yield thinking_msg

        finally:

            if thinking_msg:

                try:

                    await thinking_msg.delete()

                except Exception as e:

                    print(
                        "[THINKING DELETE ERROR] "
                        f"{repr(e)}"
                    )

    # ========================================================
    # GIF READY / SEARCH
    # ========================================================
 
    def tenor_ready(self):
 
        return bool(
            TENOR_API_KEY
            and not TENOR_API_KEY.startswith("DAN_KEY_")
        )
 
    async def search_gif(self, query):
        """Tìm gif theo từ khoá qua Tenor, trả về 1 URL gif ngẫu nhiên
        trong top kết quả, hoặc None nếu không tìm được / chưa cấu hình
        key."""
 
        if not self.tenor_ready():
            return None
 
        if not query:
            return None
 
        params = urllib.parse.urlencode({
            "q": query,
            "key": TENOR_API_KEY,
            "client_key": "ks_bot_discord",
            "limit": MAX_GIF_RESULTS,
            "media_filter": "gif",
            "contentfilter": "medium",
        })
 
        url = f"{TENOR_SEARCH_URL}?{params}"
 
        def do_request():
 
            try:
 
                with urllib.request.urlopen(
                    url,
                    timeout=REQUEST_TIMEOUT
                ) as response:
 
                    raw = response.read().decode(
                        "utf-8",
                        errors="replace"
                    )
 
                    return json.loads(raw)
 
            except Exception as e:
 
                print(
                    "[GIF ERROR] "
                    f"{repr(e)}"
                )
 
                return None
 
        data = await asyncio.to_thread(
            do_request
        )
 
        if not data:
            return None
 
        results = data.get("results") or []
 
        if not results:
            return None
 
        choice = random.choice(results)
 
        media_formats = (
            choice.get("media_formats")
            or {}
        )
 
        gif_info = (
            media_formats.get("gif")
            or {}
        )
 
        return gif_info.get("url")
 
    # ========================================================
    # REACTION
    # ========================================================
 
    async def do_reaction(
        self,
        message,
        guild,
        emoji_name
    ):
        """Thả reaction vào tin nhắn thay vì trả lời bằng chữ.
 
        Ưu tiên tìm emoji tuỳ chỉnh của server theo tên. Nếu không có,
        coi emoji_name là 1 emoji Unicode bình thường (vd: 😂)."""
 
        emoji_to_use = emoji_name
 
        if guild:
 
            for e in guild.emojis:
 
                if (
                    getattr(e, "available", True)
                    and e.name.lower() == emoji_name.lower()
                ):
 
                    emoji_to_use = e
                    break
 
        try:
 
            await message.add_reaction(
                emoji_to_use
            )
 
        except Exception as e:
 
            print(
                "[AI REACT ERROR] "
                f"{repr(e)}"
            )
 
    # ========================================================
    # ẢNH (VISION)
    # ========================================================
 
    async def build_image_parts(
        self,
        attachments
    ):
        """Tải các ảnh đính kèm trong tin nhắn Discord và đóng gói
        thành các part inlineData để gửi kèm cho Gemini (model hỗ trợ
        đa phương thức nên đọc được ảnh mà không cần API/key riêng)."""
 
        parts = []
 
        if not attachments:
            return parts
 
        for attachment in attachments:
 
            content_type = (
                attachment.content_type
                or ""
            )
 
            if not content_type.startswith(
                "image/"
            ):
                continue
 
            if (
                attachment.size
                and attachment.size > MAX_IMAGE_BYTES
            ):
 
                print(
                    "[AI IMAGE SKIP] "
                    "Ảnh quá lớn, bỏ qua: "
                    f"{attachment.filename}"
                )
 
                continue
 
            try:
 
                data = await attachment.read()
 
            except Exception as e:
 
                print(
                    "[AI IMAGE ERROR] "
                    f"{repr(e)}"
                )
 
                continue
 
            b64 = base64.b64encode(
                data
            ).decode("utf-8")
 
            parts.append({
                "inlineData": {
                    "mimeType": content_type,
                    "data": b64,
                }
            })
 
            if len(parts) >= MAX_IMAGE_ATTACHMENTS:
                break
 
        return parts
 
    # ========================================================
    # MEMORY CACHE
    # ========================================================
 
    def get_history(
        self,
        guild_id,
        channel_id
    ):
 
        key = (
            guild_id,
            channel_id
        )
 
        if key not in self.history_loaded:
 
            rows = load_memory(
                guild_id,
                channel_id
            )
 
            self.history[key].clear()
 
            for row in rows:
 
                self.history[key].append(
                    row
                )
 
            self.history_loaded.add(
                key
            )
 
        return self.history[key]
 
    def add_history(
        self,
        guild_id,
        channel_id,
        role,
        content
    ):
 
        key = (
            guild_id,
            channel_id
        )
 
        self.get_history(
            guild_id,
            channel_id
        )
 
        self.history[key].append({
            "role": role,
            "content": content,
        })
 
        save_memory(
            guild_id,
            channel_id,
            role,
            content
        )
 
    # ========================================================
    # MODEL HEALTH
    # ========================================================
 
    async def model_available(
        self,
        model
    ):
 
        async with self.state_lock:
 
            until = (
                self.model_unavailable_until.get(
                    model,
                    0
                )
            )
 
        return (
            time.monotonic()
            >= until
        )
 
    async def cooldown_model(
        self,
        model,
        seconds=MODEL_COOLDOWN_SECONDS
    ):
 
        async with self.state_lock:
 
            self.model_unavailable_until[
                model
            ] = (
                time.monotonic()
                + seconds
            )
 
    # ========================================================
    # SWITCH MODEL
    # ========================================================
 
    async def switch_to_next_model(
        self,
        failed_model_index
    ):
 
        next_index = (
            failed_model_index + 1
        ) % len(GEMINI_MODELS)
 
        async with self.state_lock:
 
            self.current_model_index = (
                next_index
            )
 
        print(
            "[AI FALLBACK] "
            f"{GEMINI_MODELS[failed_model_index]} "
            "-> "
            f"{GEMINI_MODELS[next_index]}"
        )
 
        return next_index
 
    # ========================================================
    # BUILD PROMPT
    # ========================================================
 
    def build_contents(
        self,
        guild,
        channel,
        user,
        message,
        image_parts=None
    ):
 
        guild_id = (
            guild.id
            if guild
            else 0
        )
 
        history = self.get_history(
            guild_id,
            channel.id
        )
 
        # ID thật của bot lấy tự động lúc chạy (không hardcode).
        bot_id_text = (
            str(self.bot.user.id)
            if self.bot.user
            else "không rõ"
        )
 
        system_text = (
            f"Bạn là {BOT_NAME}, một AI chatbot trong Discord.\n"
            f"User ID Discord của chính bạn là {bot_id_text}.\n"
            f"Chủ bot (người quản lý/sở hữu bot này) có user ID "
            f"Discord là {OWNER_DISCORD_ID}. Chỉ nói ra ID này khi có "
            "người hỏi cụ thể ai là chủ/admin của bot.\n"
            "Nếu có người hỏi bạn là ai / tên gì / id bạn bao nhiêu, "
            "trả lời đúng thông tin trên, không bịa tên hay id khác.\n"
            "Nói chuyện tự nhiên như một người bạn.\n"
            "Có thể nói tiếng Việt.\n"
            "Không cần quá trang trọng.\n"
            "Trả lời NGẮN GỌN, súc tích, đi thẳng vào ý chính, dễ đọc "
            "trên Discord (ưu tiên 1-3 câu, chỉ viết dài hơn khi câu "
            "hỏi thật sự cần giải thích kỹ).\n"
            "Có thể dùng emoji khi phù hợp.\n"
            "Bạn được phép tag (nhắc tên) người dùng khác ngay trong "
            "câu trả lời bằng cú pháp <@user_id> khi điều đó tự nhiên "
            "và hợp ngữ cảnh (vd: nhắc tới ai đó, gọi tên ai đó vào "
            "cuộc trò chuyện) — cứ dùng thoải mái như một thành viên "
            "bình thường trong nhóm chat, không cần rào trước rào sau.\n"
            "Không tự nhận mình là người thật.\n"
            "Nếu không biết thì nói rõ là không chắc, "
            "không được bịa.\n"
            "Không cần nhắc lại câu hỏi.\n"
            "Ưu tiên trả lời trực tiếp.\n"
            "Không nói các câu từ chối vô lý nếu câu hỏi "
            "hoàn toàn bình thường.\n"
            "Nếu người dùng nói chuyện casual thì trả lời "
            "tự nhiên, thân thiện, không cứng nhắc.\n"
            "Không tự động nói 'Sorry bro, I can't help with that' "
            "trừ khi yêu cầu thực sự không thể hỗ trợ.\n"
            "Nếu tin nhắn có kèm ảnh, bạn XEM ĐƯỢC nội dung ảnh đó, "
            "hãy mô tả/trả lời dựa trên đúng nội dung trong ảnh, "
            "không bịa thêm chi tiết không có trong ảnh.\n"
            "QUAN TRỌNG VỀ TRA CỨU WEB: bạn CÓ khả năng tự động tra "
            "cứu Google/internet theo thời gian thực khi cần (tin tức "
            "mới, giá cả, số liệu hiện tại, sub một kênh YouTube, tình "
            "trạng 1 trang web/app, v.v...) — khả năng này LUÔN BẬT "
            "SẴN, không cần ai bật giúp. TUYỆT ĐỐI KHÔNG được nói các "
            "câu như 'mình không thể duyệt web', 'mình không truy cập "
            "được internet', 'mình không có khả năng đó' — những câu "
            "đó SAI, vì bạn có khả năng này thật. Cứ tra cứu và trả "
            "lời bình thường. Nếu tra xong vẫn không tìm ra thông tin "
            "cụ thể (VD trang quá nhỏ/mới, không có dữ liệu công khai), "
            "hãy nói rõ là 'không tìm thấy thông tin cụ thể về việc "
            "này' thay vì đổ lỗi là bản thân không tra được web."
        )
 
        # Cho AI biết + được phép dùng emoji tuỳ chỉnh của server.
        system_text += build_emoji_hint(guild)
 
        # --------------------------------------------------------
        # HÀNH VI ĐẶC BIỆT: REACT / SILENT / GIF
        # --------------------------------------------------------
 
        system_text += (
            "\n\nMẶC ĐỊNH LUÔN LUÔN trả lời bằng chữ bình thường như "
            "một cuộc trò chuyện thật. Bạn có thêm 3 khả năng đặc "
            "biệt dưới đây, nhưng đây là NGOẠI LỆ HIẾM, chỉ dùng khi "
            "thật sự rõ ràng cần thiết — KHÔNG dùng thường xuyên, "
            "không dùng liên tục nhiều lượt liền nhau, vì sẽ làm cuộc "
            "trò chuyện nhàm chán. Khi không chắc nên dùng cái nào thì "
            "cứ trả lời bằng chữ như bình thường:\n"
            "1) CHỈ khi tin nhắn thật sự chỉ đáng 1 cái emoji chứ "
            "không đáng 1 câu trả lời nào cả (vd: ai đó chỉ thả 1 "
            "emoji/1 từ trêu chọc rất ngắn cho bạn), mới trả lời DUY "
            "NHẤT (không kèm chữ nào khác) dòng sau:\n"
            "[[REACT:ten_emoji]]\n"
            "(ten_emoji là TÊN một emoji tuỳ chỉnh của server ở trên, "
            "hoặc một emoji Unicode thường như 😂, 👍, ❤️).\n"
            "2) CHỈ khi tin nhắn rõ ràng KHÔNG hướng tới bạn (vd: 2 "
            "người khác đang nói chuyện riêng với nhau, không nhắc "
            "tới bạn, không hỏi bạn gì cả), mới trả lời DUY NHẤT "
            "dòng sau:\n"
            "[[SILENT]]\n"
            "3) CHỈ khi người dùng CHỦ ĐỘNG và RÕ RÀNG xin gửi gif "
            "(vd: 'cho xin cái gif', 'gửi gif đi', 'có gif không'), "
            "mới chèn thêm 1 dòng RIÊNG ở cuối câu trả lời (có thể kèm "
            "chữ bình thường phía trước):\n"
            "[[GIF:từ khoá tìm gif, viết bằng tiếng Anh]]\n"
            "TUYỆT ĐỐI KHÔNG tự ý chèn gif khi không ai yêu cầu, kể cả "
            "khi bạn nghĩ gif sẽ hợp cảnh — chỉ dùng khi được xin.\n"
            "Không giải thích các token trên, không nhắc rằng bạn đang "
            "dùng chúng, chỉ gõ đúng cú pháp ở trên khi thật sự cần.\n"
        )
 
        if not self.tenor_ready():
 
            system_text += (
                "(Lưu ý: tính năng gửi GIF hiện KHÔNG khả dụng. "
                "TUYỆT ĐỐI không dùng token [[GIF:...]] cho tới khi "
                "được thông báo tính năng này đã bật lại.)\n"
            )
 
        contents = []
 
        # System instruction được xử lý riêng.
        # Contents chỉ chứa conversation.
 
        for item in history:
 
            contents.append({
                "role": item["role"],
                "parts": [
                    {
                        "text": item["content"]
                    }
                ],
            })
 
        final_parts = [
            {
                "text": (
                    f"Người dùng Discord: "
                    f"{user.display_name} "
                    f"(mention: <@{user.id}>)\n"
                    f"Tin nhắn: {message}"
                )
            }
        ]
 
        if image_parts:
            final_parts.extend(image_parts)
 
        contents.append({
            "role": "user",
            "parts": final_parts,
        })
 
        return system_text, contents
 
    # ========================================================
    # HTTP POST
    # ========================================================
 
    async def http_post(
        self,
        url,
        headers,
        payload
    ):
 
        def do_request():
 
            data = json.dumps(
                payload,
                ensure_ascii=False
            ).encode(
                "utf-8"
            )
 
            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
 
            try:
 
                with urllib.request.urlopen(
                    request,
                    timeout=REQUEST_TIMEOUT
                ) as response:
 
                    raw = response.read().decode(
                        "utf-8",
                        errors="replace"
                    )
 
                    try:
 
                        body = json.loads(
                            raw
                        )
 
                    except Exception:
 
                        body = {
                            "raw": raw
                        }
 
                    return (
                        response.status,
                        body
                    )
 
            except urllib.error.HTTPError as e:
 
                raw = e.read().decode(
                    "utf-8",
                    errors="replace"
                )
 
                try:
 
                    body = json.loads(
                        raw
                    )
 
                except Exception:
 
                    body = {
                        "error": {
                            "message": raw
                        }
                    }
 
                return (
                    e.code,
                    body
                )
 
            except urllib.error.URLError as e:
 
                return (
                    599,
                    {
                        "error": {
                            "message": str(e)
                        }
                    }
                )
 
            except TimeoutError:
 
                return (
                    598,
                    {
                        "error": {
                            "message": "request timeout"
                        }
                    }
                )
 
        return await asyncio.to_thread(
            do_request
        )
 
    # ========================================================
    # PARSE GEMINI
    # ========================================================
 
    def parse_gemini_response(
        self,
        data
    ):
 
        if not isinstance(
            data,
            dict
        ):
 
            return ""
 
        candidates = (
            data.get("candidates")
            or []
        )
 
        if not candidates:
 
            return ""
 
        candidate = candidates[0] or {}
 
        content = (
            candidate.get("content")
            or {}
        )
 
        parts = (
            content.get("parts")
            or []
        )
 
        text_parts = []
 
        for part in parts:
 
            if not isinstance(
                part,
                dict
            ):
 
                continue
 
            text = part.get(
                "text"
            )
 
            if text:
 
                text_parts.append(
                    str(text)
                )
 
        return "\n".join(
            text_parts
        ).strip()
 
    # ========================================================
    # ERROR INFO
    # ========================================================
 
    def error_info(
        self,
        status,
        data
    ):
 
        error = {}
 
        if isinstance(
            data,
            dict
        ):
 
            error = (
                data.get("error")
                or {}
            )
 
        if isinstance(
            error,
            dict
        ):
 
            message = str(
                error.get("message")
                or error.get("status")
                or data
            )
 
        else:
 
            message = str(
                error
                or data
            )
 
        return (
            status,
            message,
            message.lower()
        )
 
    # ========================================================
    # ERROR CLASSIFICATION
    # ========================================================
 
    def is_key_error(
        self,
        status,
        lower
    ):
 
        # 401 luôn là authentication/key.
        if status == 401:
            return True
 
        # 403 thường là key/permission.
        if status == 403:
 
            return (
                "api key" in lower
                or "apikey" in lower
                or "authentication" in lower
                or "unauthenticated" in lower
                or "permission denied" in lower
                or "invalid key" in lower
                or "api_key" in lower
                or "credential" in lower
            )
 
        return (
            "api key" in lower
            or "apikey" in lower
            or "authentication" in lower
            or "unauthenticated" in lower
            or "invalid key" in lower
            or "invalid api" in lower
            or "credential" in lower
        )
 
    def is_model_error(
        self,
        status,
        lower
    ):
 
        return (
            status == 404
            or "model not found" in lower
            or "model is not found" in lower
            or "unsupported model" in lower
            or "model unavailable" in lower
            or "model is unavailable" in lower
            or "model not available" in lower
            or "model does not exist" in lower
        )
 
    def is_temporary_error(
        self,
        status,
        lower
    ):
 
        return (
            status in {
                408,
                409,
                429,
                500,
                502,
                503,
                504,
                598,
                599,
            }
            or "quota" in lower
            or "rate limit" in lower
            or "resource exhausted" in lower
            or "overloaded" in lower
            or "too many requests" in lower
            or "capacity" in lower
            or "temporarily unavailable" in lower
            or "server error" in lower
            or "timeout" in lower
            or "timed out" in lower
        )

    def is_quota_error(
        self,
        status,
        lower
    ):
        """Phan biet loi HET QUOTA (429/RESOURCE_EXHAUSTED/quota) voi
        cac loi tam thoi khac (503 qua tai, 500 loi server...).

        Loi het quota thuong KHONG tu het trong vai giay/phut nhu
        503 qua tai -- quota chi reset theo ngay/thang cua Google,
        nen can cooldown model lau hon han de tranh goi lai vo ich,
        ton them request ma chac chan van loi."""

        return (
            status == 429
            or "resource_exhausted" in lower
            or "resource exhausted" in lower
            or "quota" in lower
        )
 
    # ========================================================
    # GEMINI REQUEST
    # ========================================================
 
    async def request_gemini(
        self,
        model,
        api_key,
        system_text,
        contents,
        use_search=True
    ):

        payload = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": system_text
                    }
                ]
            },
 
            "contents": contents,
 
            "generationConfig": {
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
 
                "thinkingConfig": {
                    "thinkingLevel": THINKING_LEVEL
                },
            },
        }

        # ====================================================
        # WEB SEARCH (Google Search grounding)
        #
        # Cho phep Gemini tu tra cuu Google khi can (tin tuc moi,
        # gia ca, link web/app/video YouTube, v.v...) thay vi chi
        # tra loi bang du lieu huan luyen cu.
        # ====================================================

        if ENABLE_WEB_SEARCH and use_search:

            payload["tools"] = [
                {
                    "google_search": {}
                }
            ]
 
        url = gemini_api_url(
            model
        )
 
        # ====================================================
        # QUAN TRỌNG:
        #
        # Không nhét key vào URL nữa.
        # Dùng header chính thức của Gemini API.
        #
        # ====================================================
 
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
 
        return await self.http_post(
            url,
            headers,
            payload
        )
 
    # ========================================================
    # REQUEST + TỰ PHỤC HỒI
    # ========================================================

    async def request_one_key(
        self,
        model,
        api_key,
        system_text,
        contents
    ):
        """Gọi 1 model bằng 1 key, tự cứu khi gặp lỗi tạm thời:
        - 5xx quá tải  -> thử lại đúng 1 lần sau vài giây.
        - 429 hết quota khi đang bật search -> thử lại không search."""

        status, data = await self.request_gemini(
            model,
            api_key,
            system_text,
            contents
        )

        if status < 400:
            return status, data

        _, _, lower = self.error_info(
            status,
            data
        )

        if (
            RETRY_OVERLOADED
            and status in {500, 502, 503, 504}
            and not self.is_quota_error(status, lower)
        ):

            print(
                "[AI RETRY] "
                f"Model={model} HTTP={status} "
                f"-> thử lại sau {OVERLOAD_RETRY_DELAY}s."
            )

            await asyncio.sleep(
                OVERLOAD_RETRY_DELAY
            )

            status, data = await self.request_gemini(
                model,
                api_key,
                system_text,
                contents
            )

            if status < 400:
                return status, data

            _, _, lower = self.error_info(
                status,
                data
            )

        if (
            FALLBACK_NO_SEARCH
            and ENABLE_WEB_SEARCH
            and self.is_quota_error(status, lower)
        ):

            print(
                "[AI RETRY] "
                f"Model={model} hết quota khi bật search "
                "-> thử lại KHÔNG search."
            )

            status2, data2 = await self.request_gemini(
                model,
                api_key,
                system_text,
                contents,
                use_search=False
            )

            if status2 < 400:
                return status2, data2

            return status2, data2

        return status, data

    async def request_with_recovery(
        self,
        model,
        system_text,
        contents
    ):
        """Gọi model, nếu key hiện tại bị 429/lỗi key thì (khi
        AUTO_ROTATE_KEYS bật) thử tiếp các key còn lại."""

        total = len(GEMINI_API_KEYS)

        key_order = [
            self.current_key_index % total
        ]

        if AUTO_ROTATE_KEYS:

            key_order += [
                i for i in range(total)
                if i not in key_order
            ]

        status = 599

        data = {
            "error": {
                "message": "khong co key hop le"
            }
        }

        for key_index in key_order:

            key = GEMINI_API_KEYS[key_index]

            if (
                not key
                or key.startswith("DAN_KEY_")
            ):
                continue

            status, data = await self.request_one_key(
                model,
                key,
                system_text,
                contents
            )

            if status < 400:

                if key_index != self.current_key_index:

                    print(
                        "[AI KEY] Tự chuyển sang "
                        f"Key {key_index + 1}."
                    )

                    self.current_key_index = key_index

                return status, data

            _, _, lower = self.error_info(
                status,
                data
            )

            # Hết quota / lỗi key -> thử key kế tiếp.
            if (
                self.is_quota_error(status, lower)
                or self.is_key_error(status, lower)
            ):
                continue

            # Lỗi khác (quá tải, 400...) -> đổi key không giúp.
            return status, data

        return status, data

    # ========================================================
    # ASK AI
    # ========================================================
 
    async def ask_ai(
        self,
        guild,
        channel,
        user,
        message,
        attachments=None
    ):
 
        if not self.api_ready():
 
            return (
                "❌ Chưa có Gemini API key.\n"
                "Hãy điền key mới vào "
                "`GEMINI_API_KEYS` ở đầu file."
            )
 
        guild_id = (
            guild.id
            if guild
            else 0
        )
 
        channel_id = channel.id
 
        image_parts = await self.build_image_parts(
            attachments
        )
 
        system_text, contents = (
            self.build_contents(
                guild,
                channel,
                user,
                message,
                image_parts
            )
        )
 
        key_history_key = (
            guild_id,
            channel_id
        )
 
        async with self.channel_locks[
            key_history_key
        ]:
 
            # ==================================================
            # LẤY KEY ĐANG ĐƯỢC CHỌN
            #
            # Đổi key bằng lệnh .key+1 / .key+2 / .key+3.
            # ==================================================
 
            api_key = self.get_api_key()
 
            if not api_key:
 
                return (
                    "❌ Key hiện tại chưa được điền hoặc không hợp lệ.\n"
                    "Hãy điền key trong `GEMINI_API_KEYS` hoặc đổi "
                    "sang key khác bằng `.key+1` / `.key+2` / `.key+3`."
                )
 
            # ==================================================
            # FALLBACK MODEL
            #
            # Model hiện tại được ưu tiên.
            #
            # Nếu lỗi: chuyển sang model kế tiếp NGAY.
            #
            # KHÔNG RETRY MODEL CŨ.
            #
            # ==================================================
 
            start_model_index = (
                self.current_model_index
                % len(GEMINI_MODELS)
            )
 
            attempted_models = set()

            # Ghi lại lý do lỗi thật của TỪNG model đã thử, để nếu
            # tất cả đều lỗi thì báo cho người dùng biết chính xác
            # nguyên nhân (429 hết quota? 503 quá tải? 400 sai
            # request?...) thay vì chỉ nói chung chung "đang lỗi".
            attempt_log = []
 
            for model_round in range(
                len(GEMINI_MODELS)
            ):
 
                model_index = (
                    start_model_index
                    + model_round
                ) % len(
                    GEMINI_MODELS
                )
 
                if model_index in attempted_models:
                    continue
 
                attempted_models.add(
                    model_index
                )
 
                model = GEMINI_MODELS[
                    model_index
                ]
 
                # ------------------------------------------------
                # Model đang cooldown.
                #
                # Không gọi lại.
                # Chuyển model tiếp theo ngay.
                # ------------------------------------------------
 
                if not await self.model_available(
                    model
                ):
 
                    print(
                        "[AI SKIP] "
                        f"Model cooldown: {model}"
                    )
 
                    continue
 
                print(
                    "[AI TRY] "
                    f"Model={model} "
                    f"Key={self.current_key_index + 1}"
                )
 
                try:
 
                    status, data = (
                        await self.request_with_recovery(
                            model,
                            system_text,
                            contents
                        )
                    )
 
                    # ==========================================
                    # HTTP ERROR
                    # ==========================================
 
                    if status >= 400:
 
                        (
                            code,
                            error_message,
                            lower
                        ) = self.error_info(
                            status,
                            data
                        )
 
                        print(
                            "[AI ERROR] "
                            f"Model={model} "
                            f"HTTP={code}: "
                            f"{error_message}"
                        )
 
                        # ======================================
                        # KEY LỖI
                        #
                        # Đổi model không giúp được, báo người
                        # dùng đổi key bằng lệnh.
                        #
                        # ======================================
 
                        if self.is_key_error(
                            code,
                            lower
                        ):
 
                            print(
                                "[AI KEY ERROR] "
                                "Gemini API key có vấn đề."
                            )
 
                            key_hint = (
                                "\nThử đổi key khác bằng `.key+1`, "
                                "`.key+2` hoặc `.key+3`."
                                if len(GEMINI_API_KEYS) > 1
                                else ""
                            )
 
                            return (
                                "❌ Gemini API key đang lỗi "
                                f"HTTP `{code}`.\n"
                                "Hãy kiểm tra hoặc thay API key "
                                "trong `GEMINI_API_KEYS`."
                                f"{key_hint}"
                            )
 
                        # ======================================
                        # MODEL KHÔNG TỒN TẠI / KHÔNG HỖ TRỢ
                        #
                        # Đổi model NGAY.
                        # Không retry model cũ.
                        # ======================================
 
                        if self.is_model_error(
                            code,
                            lower
                        ):
 
                            await self.cooldown_model(
                                model
                            )

                            attempt_log.append(
                                f"{model}: HTTP {code} (model loi/khong ho tro) - {error_message[:150]}"
                            )
 
                            print(
                                "[AI FALLBACK] "
                                f"Model {model} lỗi "
                                "-> model tiếp theo."
                            )
 
                            continue
 
                        # ======================================
                        # QUÁ TẢI / RATE LIMIT / SERVER ERROR
                        #
                        # Đổi model NGAY.
                        # Không retry.
                        # ======================================
 
                        if self.is_temporary_error(
                            code,
                            lower
                        ):
 
                            is_quota = self.is_quota_error(
                                code,
                                lower
                            )

                            await self.cooldown_model(
                                model,
                                seconds=(
                                    QUOTA_COOLDOWN_SECONDS
                                    if is_quota
                                    else MODEL_COOLDOWN_SECONDS
                                )
                            )

                            attempt_log.append(
                                f"{model}: HTTP {code} "
                                f"({'HET QUOTA' if is_quota else 'qua tai/loi may chu'}) "
                                f"- {error_message[:150]}"
                            )
 
                            print(
                                "[AI FALLBACK] "
                                f"Model {model} quá tải/lỗi "
                                "-> model tiếp theo."
                            )
 
                            continue
 
                        # ======================================
                        # 400 / BAD REQUEST / LỖI PAYLOAD
                        #
                        # Đổi model không giúp được.
                        # Trả lỗi luôn để tránh spam API.
                        # ======================================
 
                        return (
                            "❌ Gemini lỗi "
                            f"HTTP `{code}`:\n"
                            f"`{error_message[:500]}`"
                        )
 
                    # ==========================================
                    # RESPONSE
                    # ==========================================
 
                    text = (
                        self.parse_gemini_response(
                            data
                        )
                    )
 
                    # Response rỗng:
                    # coi model hiện tại có vấn đề.
                    # Đổi model ngay.
                    if not text:
 
                        print(
                            "[AI ERROR] "
                            f"Model={model} "
                            "trả response rỗng "
                            "-> đổi model."
                        )
 
                        await self.cooldown_model(
                            model
                        )

                        attempt_log.append(
                            f"{model}: response rong (khong co text tra ve)"
                        )
 
                        continue
 
                    # ==========================================
                    # SUCCESS
                    # ==========================================
 
                    # Ghi nhớ model vừa thành công.
                    #
                    # Lần request tiếp theo sẽ dùng model này
                    # trước.
                    #
                    self.current_model_index = (
                        model_index
                    )
 
                    # Không reset key index ở đây — key đang dùng do
                    # người dùng chọn bằng .key+1 / .key+2 / .key+3.
 
                    # Sửa lại đúng id các emoji tuỳ chỉnh trước khi
                    # gửi/lưu, tránh hiện chữ thô ngoài Discord.
                    text = fix_emoji_tags(
                        text,
                        guild
                    )
 
                    # Lưu memory bản đã bỏ các action token đặc biệt,
                    # để lịch sử hội thoại không bị dính token lạ.
                    memory_text = strip_action_tokens(
                        text
                    )
 
                    self.add_history(
                        guild_id,
                        channel_id,
                        "user",
                        message
                    )
 
                    self.add_history(
                        guild_id,
                        channel_id,
                        "model",
                        memory_text or text
                    )
 
                    print(
                        "[AI OK] "
                        f"Model={model} "
                        f"Key={self.current_key_index + 1}"
                    )
 
                    return text
 
                # ==============================================
                # TIMEOUT
                #
                # Không retry.
                # Đổi model ngay.
                # ==============================================
 
                except asyncio.TimeoutError:
 
                    print(
                        "[AI ERROR] "
                        f"Model={model} timeout "
                        "-> đổi model."
                    )
 
                    await self.cooldown_model(
                        model
                    )

                    attempt_log.append(
                        f"{model}: timeout (qua {REQUEST_TIMEOUT}s khong phan hoi)"
                    )
 
                    continue
 
                # ==============================================
                # LỖI MẠNG / EXCEPTION
                #
                # Không retry.
                # Đổi model ngay.
                # ==============================================
 
                except Exception as e:
 
                    print(
                        "[AI ERROR] "
                        f"Model={model}: "
                        f"{repr(e)} "
                        "-> đổi model."
                    )
 
                    await self.cooldown_model(
                        model
                    )

                    attempt_log.append(
                        f"{model}: exception - {repr(e)[:150]}"
                    )
 
                    continue
 
            # ==================================================
            # TẤT CẢ MODEL ĐỀU LỖI
            # ==================================================
 
            return (
                "⏳ Gemini đang lỗi hoặc quá tải "
                "ở các model dự phòng.\n"
                "Bot đã tự chuyển model, không retry "
                "model vừa lỗi.\n"
                "\n"
                "📋 **Chi tiết từng model đã thử:**\n"
                + "\n".join(
                    f"• `{line}`"
                    for line in attempt_log
                )
                + "\n\n"
                + (
                    "💡 Nếu thấy `HTTP 429` ở trên: bạn đã hết "
                    "quota MIỄN PHÍ trong ngày (hoặc quota "
                    "Google Search grounding trong tháng). Quota "
                    "này tính theo PROJECT Google, KHÔNG phải "
                    "theo từng key, nên đổi `.key+1/.key+2/.key+3` "
                    "sẽ KHÔNG giúp gì nếu 3 key đó cùng 1 tài "
                    "khoản Google. Đợi quota reset (nửa đêm giờ "
                    "Thái Bình Dương, ~14-15h chiều giờ VN) hoặc "
                    "bật billing trong Google AI Studio để có "
                    "quota cao hơn.\n"
                    if any(
                        "429" in line
                        or "quá tải" in line
                        or "rate limit" in line.lower()
                        for line in attempt_log
                    )
                    else ""
                )
            )
 
    # ========================================================
    # COOLDOWN
    # ========================================================
 
    def check_cooldown(
        self,
        user_id,
        channel_id
    ):
 
        now = time.monotonic()
 
        key = (
            user_id,
            channel_id
        )
 
        last = self.cooldowns.get(
            key,
            0
        )
 
        remaining = (
            COOLDOWN_SECONDS
            - (
                now
                - last
            )
        )
 
        if remaining > 0:
 
            return remaining
 
        self.cooldowns[key] = now
 
        return 0
 
    # ========================================================
    # QUYỀN ADMIN AI
    # ========================================================
 
    async def has_ai_admin_access(
        self,
        ctx
    ):
 
        if not ctx.guild:
 
            return False
 
        # Manage Server
        if ctx.author.guild_permissions.manage_guild:
 
            return True
 
        # Owner được bot.py công nhận.
        try:
 
            if await self.bot.is_owner(
                ctx.author
            ):
 
                return True
 
        except Exception:
            pass
 
        # Một số bot.py có owner_ids.
        owner_ids = getattr(
            self.bot,
            "owner_ids",
            set()
        )
 
        try:
 
            if ctx.author.id in owner_ids:
 
                return True
 
        except Exception:
            pass
 
        # Một số bot.py có owner_id đơn.
        owner_id = getattr(
            self.bot,
            "owner_id",
            None
        )
 
        if (
            owner_id is not None
            and ctx.author.id == owner_id
        ):
 
            return True
 
        return False
 
    # ========================================================
    # .kenhai
    # ========================================================
 
    @commands.command(
        name="kenhai"
    )
    @commands.guild_only()
    async def kenhai(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        set_ai_channel(
            ctx.guild.id,
            ctx.channel.id
        )
 
        await ctx.send(
            "🤖 **Đã bật kênh AI!**\n"
            f"📍 Kênh: {ctx.channel.mention}\n"
            "Từ giờ mọi người nhắn ở đây "
            "**không cần tag bot**, "
            "AI sẽ tự trả lời.",
            delete_after=8
        )
 
    # ========================================================
    # .kenhaioff
    # ========================================================
 
    @commands.command(
        name="kenhaioff"
    )
    @commands.guild_only()
    async def kenhaioff(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        remove_ai_channel(
            ctx.guild.id
        )
 
        await ctx.send(
            "🔴 Đã tắt **kênh AI tự động**.",
            delete_after=5
        )
 
    # ========================================================
    # .xoakenhai
    # ========================================================
 
    @commands.command(
        name="xoakenhai"
    )
    @commands.guild_only()
    async def xoakenhai(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        remove_ai_channel(
            ctx.guild.id
        )
 
        await ctx.send(
            "🔴 Đã xóa **kênh AI tự động** "
            "của server.",
            delete_after=5
        )
 
    # ========================================================
    # .kenhchao
    # ========================================================
 
    @commands.command(
        name="kenhchao"
    )
    @commands.guild_only()
    async def kenhchao(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        set_welcome_channel(
            ctx.guild.id,
            ctx.channel.id
        )
 
        await ctx.send(
            "👋 **Đã đặt kênh chào thành viên mới!**\n"
            f"📍 Kênh: {ctx.channel.mention}\n"
            "Từ giờ có người mới vào server, AI sẽ tự tạo "
            "1 câu chào riêng, khác nhau cho từng người và "
            "gửi vào đây.",
            delete_after=8
        )
 
    # ========================================================
    # .kenhchaooff
    # ========================================================
 
    @commands.command(
        name="kenhchaooff"
    )
    @commands.guild_only()
    async def kenhchaooff(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        remove_welcome_channel(
            ctx.guild.id
        )
 
        await ctx.send(
            "🔴 Đã tắt **kênh chào thành viên mới**.",
            delete_after=5
        )
 
    # ========================================================
    # .camai
    # ========================================================
 
    @commands.command(
        name="camai"
    )
    @commands.guild_only()
    async def camai(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        set_ai_blocked(
            ctx.guild.id,
            ctx.channel.id
        )
 
        await ctx.send(
            "🚫 Đã **cấm AI trả lời** "
            "trong kênh này.",
            delete_after=5
        )
 
    # ========================================================
    # .kmodel
    # ========================================================
 
    @commands.command(
        name="kmodel"
    )
    @commands.guild_only()
    async def kmodel(
        self,
        ctx,
        index: str = None
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        # --------------------------------------------------------
        # KHÔNG NHẬP SỐ:
        #
        # Tự động chọn model TIẾP THEO còn hoạt động (không đang
        # cooldown/lỗi), bỏ qua model đang lỗi.
        # --------------------------------------------------------
 
        if index is None:
 
            start = self.current_model_index
 
            for step in range(
                1,
                len(GEMINI_MODELS) + 1
            ):
 
                candidate = (
                    start + step
                ) % len(GEMINI_MODELS)
 
                if await self.model_available(
                    GEMINI_MODELS[candidate]
                ):
 
                    async with self.state_lock:
 
                        self.current_model_index = (
                            candidate
                        )
 
                    await ctx.send(
                        "🔁 Đã chuyển sang model "
                        f"`{GEMINI_MODELS[candidate]}`.",
                        delete_after=6
                    )
 
                    return
 
            await ctx.send(
                "⏳ Tất cả model hiện đang lỗi/cooldown, "
                "chưa đổi được. Thử lại sau ít phút.",
                delete_after=8
            )
 
            return
 
        # --------------------------------------------------------
        # CÓ NHẬP SỐ:
        #
        # Chỉ đổi qua ĐÚNG model đó NẾU model đó KHÔNG đang lỗi/
        # cooldown. Nếu đang lỗi thì báo, không đổi liều.
        # --------------------------------------------------------
 
        try:
 
            num = int(index)
 
        except ValueError:
 
            await ctx.send(
                "❌ Dùng: `.kmodel <1/2/3>` hoặc `.kmodel` "
                "(không nhập số) để bot tự chọn model kế tiếp "
                "còn hoạt động.",
                delete_after=8
            )
 
            return
 
        if (
            num < 1
            or num > len(GEMINI_MODELS)
        ):
 
            await ctx.send(
                f"❌ Chỉ có model 1-{len(GEMINI_MODELS)}.",
                delete_after=5
            )
 
            return
 
        target = num - 1
        model_name = GEMINI_MODELS[target]
 
        if not await self.model_available(
            model_name
        ):
 
            await ctx.send(
                f"⚠️ Model `{model_name}` đang lỗi/cooldown, "
                "chưa đổi qua được.\n"
                "Dùng `.kmodel` (không nhập số) để bot tự chọn "
                "model còn hoạt động.",
                delete_after=8
            )
 
            return
 
        async with self.state_lock:
 
            self.current_model_index = target
 
        await ctx.send(
            f"🔁 Đã chuyển sang model `{model_name}`.",
            delete_after=6
        )
 
    # ========================================================
    # .key+1 / .key+2 / .key+3
    # ========================================================
 
    @commands.command(
        name="key+1"
    )
    @commands.guild_only()
    async def key_plus_1(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        await self.switch_key(
            ctx,
            0
        )
 
    @commands.command(
        name="key+2"
    )
    @commands.guild_only()
    async def key_plus_2(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        await self.switch_key(
            ctx,
            1
        )
 
    @commands.command(
        name="key+3"
    )
    @commands.guild_only()
    async def key_plus_3(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        await self.switch_key(
            ctx,
            2
        )
 
    # ========================================================
    # .ai
    # ========================================================
 
    @commands.command(
        name="ai"
    )
    @commands.guild_only()
    async def ai_command(
        self,
        ctx,
        *,
        prompt: str = None
    ):
 
        if not prompt:
 
            if ctx.message.attachments:
 
                prompt = "Xem giúp mình ảnh vừa gửi kèm."
 
            else:
 
                await ctx.send(
                    "❌ Dùng: `.ai <nội dung>` (có thể đính kèm ảnh).",
                    delete_after=5
                )
 
                return
 
        prompt = prompt.strip()
 
        if len(prompt) > MAX_MESSAGE_LENGTH:
 
            await ctx.send(
                "❌ Tin nhắn quá dài.",
                delete_after=5
            )
 
            return
 
        if is_ai_blocked(
            ctx.guild.id,
            ctx.channel.id
        ):
 
            return
 
        remaining = self.check_cooldown(
            ctx.author.id,
            ctx.channel.id
        )
 
        if remaining:
 
            return
 
        async with self.thinking_indicator(ctx.channel):
 
            answer = await self.ask_ai(
                ctx.guild,
                ctx.channel,
                ctx.author,
                prompt,
                ctx.message.attachments
            )
 
        action, payload = parse_ai_action(
            answer
        )
 
        # ----------------------------------------------------
        # IM LẶNG: không gửi gì cả.
        # ----------------------------------------------------
 
        if action == "silent":
            return
 
        # ----------------------------------------------------
        # CHỈ THẢ REACTION.
        # ----------------------------------------------------
 
        if action == "react":
 
            await self.do_reaction(
                ctx.message,
                ctx.guild,
                payload
            )
 
            return
 
        # ----------------------------------------------------
        # KÈM GIF.
        # ----------------------------------------------------
 
        if action == "gif":
 
            clean_text, query = payload
 
            gif_url = await self.search_gif(
                query
            )
 
            if gif_url:
 
                send_text = (
                    f"{clean_text}\n{gif_url}"
                    if clean_text
                    else gif_url
                )
 
            else:
 
                send_text = (
                    clean_text
                    or "..."
                )
 
            for i in range(
                0,
                len(send_text),
                1900
            ):
 
                await ctx.reply(
                    send_text[
                        i:i + 1900
                    ],
                    mention_author=True
                )
 
            return
 
        # ----------------------------------------------------
        # TRẢ LỜI CHỮ BÌNH THƯỜNG.
        # ----------------------------------------------------
 
        for i in range(
            0,
            len(answer),
            1900
        ):
 
            await ctx.reply(
                answer[
                    i:i + 1900
                ],
                mention_author=True
            )
 
    # ========================================================
    # .aiclear
    # ========================================================
 
    @commands.command(
        name="aiclear"
    )
    @commands.guild_only()
    async def aiclear(
        self,
        ctx
    ):
 
        if not await self.has_ai_admin_access(
            ctx
        ):
 
            await ctx.send(
                "❌ Bạn không có quyền dùng lệnh này.",
                delete_after=5
            )
 
            return
 
        clear_memory(
            ctx.guild.id,
            ctx.channel.id
        )
 
        key = (
            ctx.guild.id,
            ctx.channel.id
        )
 
        self.history.pop(
            key,
            None
        )
 
        self.history_loaded.discard(
            key
        )
 
        await ctx.send(
            "🧹 Đã xóa toàn bộ memory AI "
            "của kênh này.",
            delete_after=5
        )
 
    # ========================================================
    # .aistatus
    # ========================================================
 
    @commands.command(
        name="aistatus"
    )
    @commands.guild_only()
    async def aistatus(
        self,
        ctx
    ):
 
        channel_id = get_ai_channel(
            ctx.guild.id
        )
 
        if channel_id:
 
            channel = ctx.guild.get_channel(
                channel_id
            )
 
            if channel:
 
                channel_text = (
                    channel.mention
                )
 
            else:
 
                channel_text = (
                    f"<#{channel_id}>"
                )
 
        else:
 
            channel_text = "Chưa cài"
 
        valid_keys = 0
 
        for key in GEMINI_API_KEYS:
 
            if (
                key
                and not key.startswith(
                    "DAN_KEY_"
                )
            ):
 
                valid_keys += 1
 
        current_model = GEMINI_MODELS[
            self.current_model_index
        ]
 
        emoji_count = len(
            [
                e for e in ctx.guild.emojis
                if getattr(e, "available", True)
            ]
        )
 
        gif_status = (
            "Bật"
            if self.tenor_ready()
            else "Chưa cấu hình key"
        )
 
        welcome_channel_id = get_welcome_channel(
            ctx.guild.id
        )
 
        if welcome_channel_id:
 
            welcome_channel = ctx.guild.get_channel(
                welcome_channel_id
            )
 
            welcome_text = (
                welcome_channel.mention
                if welcome_channel
                else f"<#{welcome_channel_id}>"
            )
 
        else:
 
            welcome_text = "Chưa cài"
 
        current_key_num = (
            self.current_key_index + 1
        )
 
        await ctx.send(
            "🤖 **AI Status**\n"
            f"• Key đang dùng: `Key {current_key_num}` "
            f"(tổng `{valid_keys}`/`{len(GEMINI_API_KEYS)}` key hợp lệ)\n"
            f"• Model hiện tại: `{current_model}`\n"
            f"• Model 1: `{GEMINI_MODELS[0]}`\n"
            f"• Model 2: `{GEMINI_MODELS[1]}`\n"
            f"• Model 3: `{GEMINI_MODELS[2]}`\n"
            f"• Kênh AI: {channel_text}\n"
            f"• Kênh chào thành viên mới: {welcome_text}\n"
            f"• Memory: `{MAX_HISTORY}` messages\n"
            f"• Cooldown user: `{COOLDOWN_SECONDS}s`\n"
            f"• Emoji server khả dụng cho AI dùng: `{emoji_count}`\n"
            f"• GIF (Tenor): `{gif_status}` (chỉ dùng khi được xin)\n"
            "• Đọc ảnh đính kèm (vision): `Bật`\n"
            "• AI có thể: chỉ react, im lặng, hoặc gửi kèm gif\n"
            "• Đổi model nhanh: `.kmodel [1/2/3]`\n"
            "• Đổi key nhanh: `.key+1` `.key+2` `.key+3`\n"
            "• Fallback: lỗi/quá tải -> đổi model ngay\n"
            "• Retry model lỗi: `KHÔNG`",
            delete_after=15
        )
 
    # ========================================================
    # ON MESSAGE
    # ========================================================
 
    @commands.Cog.listener()
    async def on_message(
        self,
        message
    ):
 
        if message.author.bot:
 
            return
 
        if not message.guild:
 
            return
 
        content = (
            message.content.strip()
        )
 
        # Tin nhắn không có chữ nhưng có ảnh đính kèm vẫn cho AI xử lý
        # (vd: chỉ gửi ảnh + tag bot để hỏi "ảnh này là gì").
        if (
            not content
            and not message.attachments
        ):
 
            return
 
        # .camai
        if is_ai_blocked(
            message.guild.id,
            message.channel.id
        ):
 
            return
 
        # Không bắt command.
        if content.startswith("."):
 
            return
 
        # ====================================================
        # TAG BOT
        # ====================================================
 
        mentioned = (
            self.bot.user is not None
            and self.bot.user in message.mentions
        )
 
        # ====================================================
        # REPLY BOT
        # ====================================================
 
        replied_to_bot = False
 
        if (
            message.reference
            and message.reference.message_id
        ):
 
            referenced = (
                message.reference.resolved
            )
 
            if referenced is None:
 
                try:
 
                    referenced = (
                        await message.channel.fetch_message(
                            message.reference.message_id
                        )
                    )
 
                except (
                    discord.NotFound,
                    discord.Forbidden,
                    discord.HTTPException
                ):
 
                    referenced = None
 
            if (
                referenced
                and self.bot.user
                and referenced.author.id
                == self.bot.user.id
            ):
 
                replied_to_bot = True
 
        # ====================================================
        # KÊNH AI
        # ====================================================
 
        configured_channel = (
            get_ai_channel(
                message.guild.id
            )
        )
 
        in_ai_channel = (
            configured_channel
            == message.channel.id
        )
 
        if (
            not mentioned
            and not in_ai_channel
            and not replied_to_bot
        ):
 
            return
 
        # ====================================================
        # XÓA MENTION
        # ====================================================
 
        prompt = content
 
        if (
            mentioned
            and self.bot.user
        ):
 
            prompt = prompt.replace(
                self.bot.user.mention,
                ""
            ).strip()
 
        if not prompt:
 
            prompt = (
                "Xem giúp mình ảnh vừa gửi kèm."
                if message.attachments
                else "Xin chào!"
            )
 
        if len(prompt) > MAX_MESSAGE_LENGTH:
 
            await message.reply(
                "❌ Tin nhắn quá dài.",
                delete_after=5,
                mention_author=False
            )
 
            return
 
        # ====================================================
        # COOLDOWN
        # ====================================================
 
        remaining = self.check_cooldown(
            message.author.id,
            message.channel.id
        )
 
        if remaining:
 
            return
 
        # ====================================================
        # AI
        # ====================================================
 
        async with self.thinking_indicator(message.channel):
 
            answer = await self.ask_ai(
                message.guild,
                message.channel,
                message.author,
                prompt,
                message.attachments
            )
 
        # ====================================================
        # XỬ LÝ ACTION (silent / react / gif / text)
        # ====================================================
 
        action, payload = parse_ai_action(
            answer
        )
 
        # ----------------------------------------------------
        # IM LẶNG: bot có quyền không nhắn gì cả.
        # ----------------------------------------------------
 
        if action == "silent":
            return
 
        # ----------------------------------------------------
        # CHỈ THẢ REACTION, không nhắn chữ.
        # ----------------------------------------------------
 
        if action == "react":
 
            await self.do_reaction(
                message,
                message.guild,
                payload
            )
 
            return
 
        # ----------------------------------------------------
        # KÈM GIF.
        # ----------------------------------------------------
 
        if action == "gif":
 
            clean_text, query = payload
 
            gif_url = await self.search_gif(
                query
            )
 
            if gif_url:
 
                send_text = (
                    f"{clean_text}\n{gif_url}"
                    if clean_text
                    else gif_url
                )
 
            else:
 
                send_text = (
                    clean_text
                    or "..."
                )
 
            chunks = [
                send_text[i:i + 1900]
                for i in range(
                    0,
                    len(send_text),
                    1900
                )
            ]
 
            for chunk in chunks:
 
                await message.reply(
                    chunk,
                    mention_author=True
                )
 
            return
 
        # ----------------------------------------------------
        # TRẢ LỜI CHỮ BÌNH THƯỜNG.
        # ----------------------------------------------------
 
        chunks = [
            answer[i:i + 1900]
            for i in range(
                0,
                len(answer),
                1900
            )
        ]
 
        for chunk in chunks:
 
            await message.reply(
                chunk,
                mention_author=True
            )
 
 
    # ========================================================
    # ON MEMBER JOIN (CHÀO THÀNH VIÊN MỚI BẰNG AI)
    # ========================================================
 
    @commands.Cog.listener()
    async def on_member_join(
        self,
        member
    ):
 
        if member.bot:
 
            return
 
        channel_id = get_welcome_channel(
            member.guild.id
        )
 
        if not channel_id:
 
            return
 
        channel = member.guild.get_channel(
            channel_id
        )
 
        if not channel:
 
            return
 
        if not self.api_ready():
 
            return
 
        prompt = (
            f"Có thành viên mới tên {member.display_name} "
            f"(mention: <@{member.id}>) vừa tham gia server. "
            "Hãy viết một câu chào mừng NGẮN GỌN (1-2 câu), vui vẻ, "
            "TỰ NHIÊN và MỖI LẦN KHÁC NHAU (không lặp lại đúng công "
            "thức/câu chữ những lần chào trước), có thể nhắc tên hoặc "
            "tag người đó bằng <@user_id>, có thể dùng emoji cho sinh "
            "động. Chỉ trả lời đúng câu chào, không giải thích gì "
            "thêm."
        )
 
        try:
 
            answer = await self.ask_ai(
                member.guild,
                channel,
                member,
                prompt
            )
 
        except Exception as e:
 
            print(
                "[WELCOME ERROR] "
                f"{repr(e)}"
            )
 
            return
 
        action, payload = parse_ai_action(
            answer
        )
 
        # Chào mừng thì luôn ưu tiên gửi chữ, bỏ qua các hành vi
        # silent/react/gif đặc biệt để tránh im lặng bỏ lỡ người mới.
        if action != "text":
 
            answer = (
                strip_action_tokens(answer)
                or f"Chào mừng {member.mention} đã đến với server! 🎉"
            )
 
        try:
 
            await channel.send(
                answer
            )
 
        except Exception as e:
 
            print(
                "[WELCOME SEND ERROR] "
                f"{repr(e)}"
            )
 
 
# ============================================================
# SETUP
# ============================================================
 
async def setup(bot):
 
    await bot.add_cog(
        AICog(bot)
    )
