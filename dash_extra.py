"""
dash_extra.py — phần mở rộng cho web.py (BẢN V3). Để cùng thư mục với web.py.

Gồm:
  1. Bảo mật đăng nhập: phiên ngẫu nhiên lưu ở server, khóa IP khi nhập sai, kiểm tra nguồn gốc request, CSP.
  2. GIF yêu thích lưu trên web (gif_favs.json).
  3. Giao diện Chat kiểu Discord (HTML + CSS + JS).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

# ======================================================================
# 1. BẢO MẬT
# ======================================================================
SESSION_TTL = 7 * 24 * 3600          # phiên đăng nhập sống 7 ngày
MAX_SESSIONS = 30
MAX_FAILS = 5                        # sai 5 lần
LOCK_SECONDS = 15 * 60               # bị khóa IP 15 phút
GLOBAL_FAILS = 40                    # cả web sai 40 lần / 10 phút (đổi IP để dò) thì khóa đăng nhập 5 phút
_SESS = {}                           # sha256(token) -> hạn
_FAILS = {}                          # ip -> {"n": số lần sai, "until": khóa tới}
_GLOBAL = []                         # mốc thời gian các lần sai
_LOCK = threading.Lock()


def _h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def session_new():
    """Tạo token ngẫu nhiên 256 bit. Server chỉ lưu bản băm nên rò token cũng không suy ra được mật khẩu."""
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _SESS.items() if v < now]:
            del _SESS[k]
        while len(_SESS) >= MAX_SESSIONS:
            del _SESS[min(_SESS, key=_SESS.get)]
        _SESS[_h(tok)] = now + SESSION_TTL
    return tok


def session_valid(tok):
    if not tok or len(tok) > 100:
        return False
    k = _h(tok)
    with _LOCK:
        exp = _SESS.get(k)
        if exp is None:
            return False
        if exp < time.time():
            del _SESS[k]
            return False
    return True


def session_drop(tok):
    if tok:
        with _LOCK:
            _SESS.pop(_h(tok), None)


def client_ip(handler):
    # Sau proxy (Railway/Render/bot-hosting) lấy IP ở cuối chuỗi X-Forwarded-For (do proxy thêm, không giả được)
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()[:64] or "?"
    return handler.client_address[0]


def lock_remaining(ip):
    """Số giây còn bị khóa (0 = được thử)."""
    now = time.time()
    with _LOCK:
        recent = [t for t in _GLOBAL if now - t < 600]
        _GLOBAL[:] = recent
        if len(recent) >= GLOBAL_FAILS:
            return max(1, int(300 - (now - recent[-1])))
        e = _FAILS.get(ip)
        if e and e["until"] > now:
            return int(e["until"] - now)
    return 0


def login_fail(ip):
    now = time.time()
    with _LOCK:
        _GLOBAL.append(now)
        if len(_FAILS) > 500:
            _FAILS.clear()
        e = _FAILS.setdefault(ip, {"n": 0, "until": 0})
        e["n"] += 1
        if e["n"] >= MAX_FAILS:
            e["n"] = 0
            e["until"] = now + LOCK_SECONDS


def login_ok(ip):
    with _LOCK:
        _FAILS.pop(ip, None)


def check_password(pw, real):
    # So sánh băm để thời gian không phụ thuộc độ dài / vị trí ký tự sai
    return hmac.compare_digest(hashlib.sha256(pw.encode()).digest(), hashlib.sha256(real.encode()).digest())


def same_origin(headers):
    """Chặn request POST từ trang web khác (CSRF). Trình duyệt tự gắn Sec-Fetch-Site/Origin, JS không giả được."""
    if os.environ.get("DASHBOARD_SKIP_ORIGIN"):
        return True
    sfs = headers.get("Sec-Fetch-Site")
    if sfs:
        return sfs in ("same-origin", "none")
    origin = headers.get("Origin")
    if origin:
        if origin == "null":
            return False
        host = urllib.parse.urlsplit(origin).netloc.lower()
        allowed = {headers.get("Host", "").lower(), headers.get("X-Forwarded-Host", "").split(",")[0].strip().lower()}
        allowed.discard("")
        return host in allowed
    return True


def make_nonce():
    return secrets.token_hex(16)


def csp(nonce=""):
    """Content-Security-Policy: chỉ chạy script có nonce của server => script lạ chèn vào trang (XSS) bị chặn."""
    script = ("script-src 'nonce-%s'" % nonce) if nonce else "script-src 'none'"
    return "; ".join([
        "default-src 'self'", script,
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com data:",
        "img-src 'self' data: https:", "media-src https:", "connect-src 'self'",
        "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'", "object-src 'none'",
    ])


# ======================================================================
# 2. GIF YÊU THÍCH (lưu trên web, dùng chung mọi thiết bị)
# ======================================================================
GIF_FAV_FILE = "gif_favs.json"
GIF_FAV_MAX = 300
_FAV_LOCK = threading.Lock()
_URL_RE = re.compile(r"^https://[^\s\"'<>\\]{4,480}$")


def _fav_load():
    try:
        with open(GIF_FAV_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, dict) and _URL_RE.match(str(x.get("url", ""))) and _URL_RE.match(str(x.get("preview", "")))]
    except Exception:
        return []


def _fav_save(items):
    tmp = GIF_FAV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    os.replace(tmp, GIF_FAV_FILE)


def fav_list():
    with _FAV_LOCK:
        return _fav_load()


def fav_change(action, url, preview):
    """Trả (danh sách mới, lỗi)."""
    url, preview = str(url or ""), str(preview or "")
    if not _URL_RE.match(url):
        return None, "Link GIF không hợp lệ"
    with _FAV_LOCK:
        items = [x for x in _fav_load() if x["url"] != url]
        if action == "add":
            if not _URL_RE.match(preview):
                return None, "Thiếu ảnh xem trước"
            items.insert(0, {"url": url, "preview": preview})
            items = items[:GIF_FAV_MAX]
        elif action != "remove":
            return None, "Lệnh không hợp lệ"
        try:
            _fav_save(items)
        except OSError as e:
            return None, "Không lưu được: %s" % e
        return items, None


# ======================================================================
# 3. GIAO DIỆN CHAT
# ======================================================================
CHAT_BLOCK = """
<section class="dc" id="dc" data-show="chat">
    <nav class="dc-rail" id="dcRail" aria-label="Server"></nav>
    <aside class="dc-side">
        <div class="dc-side-head"><span id="dcSideHead">Chọn server</span><button type="button" class="ico-btn" id="dcMarkG" title="Đánh dấu đã đọc mục này"></button></div>
        <div class="dc-chans" id="dcChans"><div class="dc-note">Chọn một server ở cột bên trái, hoặc biểu tượng tin nhắn để xem tin nhắn riêng.</div></div>
    </aside>
    <div class="dc-main">
        <div class="dc-top">
            <button type="button" class="dc-back" id="dcBack" aria-label="Quay lại">‹</button>
            <span id="dcTitle" class="dim">Chọn một kênh để bắt đầu</span>
            <span class="dc-bot" title="Bạn đang chat bằng tên bot này"><span class="dc-bot-ico" id="dcBotIco">🤖</span><span id="dcBotName">Bot</span></span>
            <button type="button" class="ico-btn" id="dcSmall" title="Chữ nhỏ lại">A−</button>
            <button type="button" class="ico-btn" id="dcBig" title="Chữ to lên">A+</button>
            <button type="button" class="ico-btn" id="dcMemBtn" title="Thành viên / thông tin người dùng"></button>
            <button type="button" class="ico-btn" id="dcFull" title="Toàn màn hình"></button>
            <button type="button" class="ico-btn" id="dcLock" aria-label="Khóa cuộn trang"></button>
        </div>
        <div class="dc-msgs" id="dcMsgs"><div class="dc-empty">Chưa chọn kênh nào</div></div>
        <button type="button" class="dc-new" id="dcNew" hidden>⬇ Có tin nhắn mới</button>
        <div class="dc-comp">
            <div class="dc-ac" id="dcAc" hidden></div>
            <div class="dc-reply" id="dcReply" hidden></div>
            <div class="dc-files" id="dcFiles"></div>
            <div class="cbox">
                <button type="button" id="dcAttach" title="Đính kèm file (hoặc kéo thả / dán ảnh)"></button>
                <textarea id="dcInput" rows="1" placeholder="Chọn kênh để chat" disabled></textarea>
                <button type="button" id="dcGif" title="Gửi GIF" disabled>GIF</button>
                <button type="button" id="dcEmoji" title="Emoji"></button>
                <button type="button" id="dcSend" title="Gửi" disabled></button>
            </div>
            <input type="file" id="dcFile" multiple hidden>
            <div class="dc-pop" id="dcPop"></div>
        </div>
        <div class="dc-modal" id="dcProf" hidden></div>
    </div>
    <aside class="dc-members" id="dcMembers"></aside>
    <div class="dc-tip" id="dcTip" hidden></div>
</section>
"""

CHAT_CSS = r"""
:root { --cs: 1.2; }
body[data-tab="chat"] .wrap { max-width: none; padding: 8px 10px 0; }
body[data-tab="chat"] .top { display: none; }
body[data-tab="chat"] .tabs { margin-bottom: 8px; }
html.chat-full body[data-tab="chat"] .tabs { display: none; }
html.chat-full body[data-tab="chat"] .wrap { padding: 0; }

/* Cỡ chữ gốc = 15px x --cs; mọi thứ trong khung chat tính theo em nên nút A- / A+ phóng to cả avatar, cột, icon */
.dc { position: relative; display: grid; grid-template-columns: 5.2em 16.5em minmax(0, 1fr); grid-template-rows: minmax(0, 1fr);
      height: calc(100vh - 64px); height: calc(100dvh - 64px); min-height: 420px; border-radius: 14px; overflow: hidden;
      border: 1px solid #1e1f22; background: #313338; color: #dbdee1; margin-bottom: 0; font-size: calc(15px * var(--cs)); }
.dc.show-mem { grid-template-columns: 5.2em 16.5em minmax(0, 1fr) 17em; }
html.chat-lock .dc { height: calc(100vh - 64px); height: calc(100dvh - 64px); }
html.chat-full body[data-tab="chat"] .dc { height: 100vh; height: 100dvh; border-radius: 0; border: 0; }
.dc button { font-family: inherit; }
.dc .ic { width: 1.25em; height: 1.25em; fill: currentColor; display: block; flex: none; }
.ico-btn { background: #2b2d31; border: 0; border-radius: 8px; padding: .4em .6em; cursor: pointer; color: #dbdee1; font-size: .85em; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; flex: none; line-height: 1; }
.ico-btn:hover { background: #3a3c42; }
.ico-btn.on { background: var(--accent); color: var(--accent-ink); }
.ico-btn:disabled { opacity: .4; cursor: not-allowed; }

/* ---- cột server ---- */
.dc-rail { background: #1e1f22; overflow-y: auto; padding: .7em 0; display: flex; flex-direction: column; align-items: center; gap: .6em; scrollbar-width: none; min-height: 0; overscroll-behavior: contain; }
.dc-rail::-webkit-scrollbar { display: none; }
.rw { position: relative; flex: none; }
.dc .rail-ico { width: 3.2em; height: 3.2em; border-radius: 50%; background: #313338; display: grid; place-items: center; cursor: pointer; overflow: hidden; font-weight: 600; font-size: 1em; color: #dbdee1; border: 0; padding: 0; transition: border-radius .15s, background .15s; position: relative; flex: none; }
.dc .rail-ico .ic { width: 1.7em; height: 1.7em; }
.dc .rail-ico:hover, .dc .rail-ico.on { border-radius: 32%; background: var(--accent); color: var(--accent-ink); }
.dc .rail-ico.mini { width: 2.6em; height: 2.6em; background: #2b2d31; color: #23a55a; }
.dc .rail-ico.mini .ic { width: 1.4em; height: 1.4em; }
.dc .rail-ico.mini:hover { background: #23a55a; color: #fff; border-radius: 32%; }
.dc .rail-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.rail-sep { width: 2.2em; height: 2px; background: #35373c; flex: none; border-radius: 2px; }
.rw .dotu { position: absolute; left: -.75em; top: 50%; width: .5em; height: .5em; margin-top: -.25em; background: #fff; border-radius: 50%; }
.bdg { background: #f23f43; color: #fff; font-size: .75em; font-weight: 700; border-radius: 1em; min-width: 1.6em; height: 1.6em; line-height: 1.6em; text-align: center; padding: 0 .4em; flex: none; }
.rw .bdg { position: absolute; right: -.35em; bottom: -.35em; border: .2em solid #1e1f22; box-sizing: content-box; }
.newp { position: absolute; left: 50%; bottom: -.5em; transform: translateX(-50%); background: #f23f43; color: #fff; font-size: .62em; font-weight: 800; border-radius: 1em; padding: .15em .7em; border: .3em solid #1e1f22; box-sizing: content-box; white-space: nowrap; letter-spacing: .03em; pointer-events: none; }
.dc-tip { position: fixed; z-index: 300; transform: translateY(-50%); background: #111214; color: #dbdee1; border-radius: 8px; padding: .55em .85em; box-shadow: 0 8px 24px rgba(0,0,0,.55); font-size: calc(15px * var(--cs)); pointer-events: none; max-width: 300px; display: flex; flex-direction: column; gap: 2px; }
.dc-tip b { color: #fff; font-size: 1em; }
.dc-tip span { color: #b5bac1; font-size: .88em; }

/* ---- cột kênh / DM ---- */
.dc-side { background: #2b2d31; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
.dc-side-head { padding: .75em .9em; font-weight: 700; border-bottom: 1px solid #1e1f22; box-shadow: 0 1px 0 rgba(0,0,0,.15); flex: none; display: flex; align-items: center; gap: .5em; font-size: 1.05em; }
.dc-side-head span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dc-side-head .ico-btn { color: #23a55a; padding: .3em .45em; }
.dc-chans { overflow-y: auto; padding: .5em; flex: 1; min-height: 0; overscroll-behavior: contain; scrollbar-width: thin; }
.dc .grp { font-size: .75em; font-weight: 700; text-transform: uppercase; color: #949ba4; padding: 1.1em .5em .3em; letter-spacing: .03em; }
.dc .chan { position: relative; display: flex; align-items: center; gap: .5em; width: 100%; background: none; border: 0; color: #949ba4; padding: .42em .55em; border-radius: 6px; cursor: pointer; text-align: left; font-size: 1em; line-height: 1.3; }
.dc .chan:hover { background: #35373c; color: #dbdee1; }
.dc .chan.on { background: #404249; color: #fff; }
.dc .chan .h { opacity: .7; font-weight: 600; flex: none; }
.dc .chan .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dc .chan.unr { color: #fff; font-weight: 700; }
.dc .chan.unr::before { content: ""; position: absolute; left: -.45em; top: 50%; width: .3em; height: .6em; margin-top: -.3em; background: #fff; border-radius: 0 4px 4px 0; }
.dc .chan .bdg { margin-left: auto; }
.dc .chan.dmi { gap: .7em; padding: .4em .5em; }
.dav { position: relative; width: 2.4em; height: 2.4em; flex: none; }
.dc .chan .dav img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; display: block; }
.dtx { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.dtx .t { font-size: 1em; }
.dtx small { font-size: .78em; color: #949ba4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 400; }
.dc .chan.unr .dtx small { color: #b5bac1; }
.dc .st { position: absolute; right: -.15em; bottom: -.15em; width: .62em; height: .62em; border-radius: 50%; border: .2em solid #2b2d31; background: #80848e; box-sizing: content-box; }
.dc .st.s-online { background: #23a55a; } .dc .st.s-idle { background: #f0b232; } .dc .st.s-dnd { background: #f23f43; }
.dm-open { display: flex; gap: .4em; padding: .5em .4em; }
.dm-open input { background: #1e1f22; border: 0; border-radius: 6px; padding: .55em .7em; font-size: .9em; width: 100%; color: #dbdee1; outline: none; }
.dm-open button { background: #5865f2; border: 0; border-radius: 6px; color: #fff; padding: 0 .9em; cursor: pointer; font-weight: 600; font-size: .9em; }
.dc-note { padding: .9em .7em; font-size: .88em; color: #949ba4; }
.dc-warn { background: #3a2f1d; border: 1px solid #6b5420; color: #f0d9a0; border-radius: 8px; padding: .7em .8em; font-size: .85em; margin-bottom: .6em; line-height: 1.45; }
.dc-warn b { display: block; color: #ffd36a; margin-bottom: .2em; }
.dc-warn code { background: #1e1f22; padding: 1px 5px; border-radius: 4px; }

/* ---- khung chat ---- */
.dc-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: #313338; position: relative; }
.dc-top { padding: .65em 1em; font-weight: 700; border-bottom: 1px solid #26272b; display: flex; gap: .45em; align-items: center; box-shadow: 0 1px 0 rgba(0,0,0,.15); flex: none; font-size: 1.1em; }
.dc-top #dcTitle { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dc-top .dim { color: #949ba4; font-weight: 400; }
.dc-top #dcTitle .dim { color: #949ba4; }
.dc-bot { margin-left: auto; display: flex; align-items: center; gap: .5em; font-size: .78em; font-weight: 600; color: #b5bac1; min-width: 0; }
.dc-bot #dcBotName { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 9em; }
.dc-bot-ico { width: 1.9em; height: 1.9em; border-radius: 50%; background: #5865f2; display: grid; place-items: center; font-size: 1em; overflow: hidden; flex: none; }
.dc-bot-ico img { width: 100%; height: 100%; object-fit: cover; display: block; }
.dc-back { display: none; background: none; border: 0; color: #dbdee1; font-size: 1.6em; cursor: pointer; padding: 0 .2em; line-height: 1; }
.dc-msgs { flex: 1; overflow-y: auto; padding: .5em 0 .8em; min-height: 0; overscroll-behavior: contain; }
.dc-empty { padding: 2.5em 1.2em; text-align: center; color: #949ba4; font-size: .95em; }
.dc-older { display: block; margin: .5em auto; background: #2b2d31; border: 0; color: #b5bac1; padding: .4em 1em; border-radius: 8px; cursor: pointer; font-size: .8em; }
.dc-older:hover { color: #fff; }
.dc .m { position: relative; display: flex; gap: 1em; padding: .12em 1em; line-height: 1.45; }
.dc .m:hover { background: #2e3035; }
.dc .m.first { margin-top: 1em; }
.dc .m .av { width: 2.8em; height: 2.8em; border-radius: 50%; flex: none; cursor: pointer; object-fit: cover; margin-top: .15em; background: #1e1f22; }
.dc .m .gut { width: 2.8em; flex: none; font-size: .62em; color: #949ba4; text-align: center; padding-top: .7em; visibility: hidden; }
.dc .m:hover .gut { visibility: visible; }
.dc .m .body { min-width: 0; flex: 1; }
.dc .m .head { display: flex; align-items: baseline; gap: .5em; flex-wrap: wrap; }
.dc .m .nm { font-weight: 600; cursor: pointer; color: #fff; font-size: 1em; }
.dc .m .nm:hover { text-decoration: underline; }
.dc .m .ts { font-size: .72em; color: #949ba4; }
.dc .tag { font-size: .62em; padding: .12em .5em; align-self: center; background: #5865f2; color: #fff; border-radius: 4px; font-weight: 600; }
.dc .m .txt { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 1em; color: #dbdee1; }
.dc .m .txt a, .dc .emb a { color: #00a8fc; text-decoration: none; }
.dc .m .txt a:hover { text-decoration: underline; }
.dc .m .txt code { background: #1e1f22; padding: 1px 5px; border-radius: 4px; font-size: .9em; }
.dc .m .ed { color: #949ba4; font-size: .7em; }
.dc .m .rep { font-size: .78em; color: #949ba4; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dc .m .rep b { color: #b5bac1; font-weight: 600; }
.dc .m .tools { position: absolute; right: 1em; top: -1.3em; display: none; background: #313338; border: 1px solid #26272b; border-radius: 8px; overflow: hidden; z-index: 3; }
.dc .m:hover .tools, .dc .m.sel .tools { display: flex; }
.dc .tools button { background: none; border: 0; color: #b5bac1; padding: .4em .6em; cursor: pointer; display: flex; align-items: center; }
.dc .tools button:hover { background: #3a3c42; color: #fff; }
.dc .tools .ic { width: 1.15em; height: 1.15em; }
.dc .att img { max-width: min(26em, 100%); max-height: 20em; border-radius: 8px; margin-top: 6px; display: block; }
.dc .att video { max-width: min(26em, 100%); border-radius: 8px; margin-top: 6px; display: block; }
.dc .file { display: inline-flex; gap: 6px; align-items: center; margin-top: 6px; background: #2b2d31; border: 1px solid #1e1f22; border-radius: 8px; padding: .5em .8em; color: #00a8fc; text-decoration: none; font-size: .92em; }
.dc .emb { border-left: 4px solid var(--accent); background: #2b2d31; border-radius: 4px; padding: .5em .8em .6em; margin-top: 6px; max-width: 34em; font-size: .93em; }
.dc .emb .et { font-weight: 700; color: #fff; margin: 2px 0; }
.dc .emb .ea { font-size: .8em; color: #b5bac1; }
.dc .emb .ed2 { white-space: pre-wrap; overflow-wrap: anywhere; color: #dbdee1; }
.dc .emb .ef { margin-top: 6px; font-size: .92em; } .dc .emb .ef b { display: block; }
.dc .emb img.big { max-width: 100%; border-radius: 6px; margin-top: 8px; display: block; }
.dc .emb .ft { font-size: .75em; color: #949ba4; margin-top: 6px; }
.dc .emb video.gifv, .dc .att video.gifv { max-width: 100%; max-height: 20em; border-radius: 6px; margin-top: 8px; display: block; }
.dc .comp-note { font-size: .78em; color: #949ba4; margin-top: 4px; font-style: italic; }
.dc .rx { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.dc .rxb { display: inline-flex; align-items: center; gap: 5px; background: #2b2d31; border: 1px solid transparent; border-radius: 8px; padding: .12em .55em; font-size: .93em; color: #b5bac1; cursor: pointer; }
.dc .rxb:hover { border-color: #4e5058; }
.dc .rxb.me { background: rgba(88,101,242,.25); border-color: #5865f2; color: #dee0fc; }
.dc .ce { width: 1.5em; height: 1.5em; vertical-align: -.35em; object-fit: contain; }
.dc .rxb .ce { width: 1.2em; height: 1.2em; vertical-align: middle; }
.dc-new { position: absolute; left: 50%; bottom: 5.6em; transform: translateX(-50%); z-index: 6; background: #5865f2; color: #fff; border: 0; border-radius: 999px; padding: .4em 1.1em; font-size: .85em; font-weight: 600; cursor: pointer; box-shadow: 0 4px 14px rgba(0,0,0,.5); }

/* ---- ô soạn tin ---- */
.dc-comp { padding: 0 1em 1em; position: relative; flex: none; }
.dc-reply { background: #2b2d31; border-radius: 8px 8px 0 0; padding: .4em .8em; font-size: .82em; color: #b5bac1; display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.dc-reply button { background: none; border: 0; color: #b5bac1; cursor: pointer; font-size: 1em; }
.dc-reply .rbtns { display: flex; align-items: center; gap: 8px; flex: none; }
.dc-reply .pingbtn { font-size: .9em; font-weight: 700; padding: 3px 9px; border-radius: 6px; background: #1e1f22; color: #949ba4; }
.dc-reply .pingbtn.on { background: #5865f2; color: #fff; }
.dc-files { display: flex; flex-wrap: wrap; gap: 8px; padding: 0 0 8px; }
.dc-files:empty { display: none; }
.fchip { display: flex; align-items: center; gap: 8px; background: #2b2d31; border-radius: 8px; padding: 6px 10px; font-size: .8em; max-width: 16em; }
.fchip span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fchip button { background: none; border: 0; color: #f23f43; cursor: pointer; }
.dc .cbox { display: flex; align-items: flex-end; gap: .15em; background: #383a40; border-radius: 10px; padding: .1em .4em; }
.dc .cbox textarea { flex: 1; background: none; border: 0; outline: none; resize: none; max-height: 10em; padding: .7em .4em; color: #dbdee1; font-size: 1em; line-height: 1.35; font-family: inherit; }
.dc .cbox textarea:disabled { opacity: .5; }
.dc .cbox button { background: none; border: 0; color: #b5bac1; cursor: pointer; padding: .55em; border-radius: 6px; display: flex; align-items: center; }
.dc .cbox button:hover:not(:disabled) { color: #fff; }
.dc .cbox button:disabled { opacity: .4; cursor: not-allowed; }
.dc .cbox #dcSend { color: var(--accent); }
.dc .cbox #dcGif { font-size: .72em; font-weight: 800; border: 2px solid currentColor; border-radius: 6px; padding: .1em .45em; margin: .8em .3em; line-height: 1.5; }
.dc-pop { position: absolute; bottom: 5em; right: 1em; width: 27em; max-width: calc(100% - 2em); max-height: 26em; overflow: auto; background: #2b2d31; border: 1px solid #1e1f22; border-radius: 10px; padding: 8px; display: none; z-index: 10; box-shadow: 0 8px 24px rgba(0,0,0,.5); font-size: .9em; }
.dc-pop.show { display: block; }
.dc-pop.wide { width: 36em; max-height: 30em; }
.pop-tabs { display: flex; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
.pop-tabs button { background: #1e1f22; border: 0; color: #b5bac1; border-radius: 6px; padding: 4px 10px; cursor: pointer; font-size: 1.1em; }
.pop-tabs button.on { background: #5865f2; color: #fff; }
.emo-grid { display: grid; grid-template-columns: repeat(9, 1fr); gap: 2px; }
.emo-grid button { font-size: 1.6em; background: none; border: 0; cursor: pointer; border-radius: 6px; padding: 3px; line-height: 1.2; }
.emo-grid button:hover { background: #3a3c42; }
.emo-grid img { width: 1.7em; height: 1.7em; object-fit: contain; }
.gif-search { width: 100%; background: #1e1f22; border: 0; border-radius: 6px; padding: .6em .75em; color: #dbdee1; font-size: 1em; outline: none; margin-bottom: 8px; }
.gif-grid { columns: 3; column-gap: 6px; }
.gif-grid .gi { position: relative; margin: 0 0 6px; break-inside: avoid; }
.gif-grid .gi .gpick { display: block; width: 100%; margin: 0; padding: 0; border: 0; background: #1e1f22; border-radius: 6px; overflow: hidden; cursor: pointer; }
.gif-grid .gi .gpick:hover { outline: 2px solid #5865f2; }
.gif-grid .gi img { width: 100%; display: block; }
.gif-grid .gi .gstar { position: absolute; top: 4px; right: 4px; width: 2em; height: 2em; margin: 0; padding: 0; border: 0; border-radius: 50%; background: rgba(0,0,0,.6); color: #fff; cursor: pointer; display: grid; place-items: center; opacity: 0; transition: opacity .12s; }
.gif-grid .gi:hover .gstar, .gif-grid .gi .gstar.on { opacity: 1; }
.gif-grid .gi .gstar.on { color: #ffd23f; }
.gif-grid .gi .gstar .ic { width: 1.2em; height: 1.2em; }
.gif-more { display: block; margin: 8px auto 0; background: #1e1f22; border: 0; border-radius: 8px; color: #b5bac1; padding: .5em 1.2em; cursor: pointer; font-size: .95em; }
.gif-more:hover { color: #fff; }
.dc.drag .dc-main::after { content: "Thả file vào đây để đính kèm"; position: absolute; inset: 0; background: rgba(88,101,242,.35); display: grid; place-items: center; font-weight: 700; z-index: 20; }
html.chat-lock, html.chat-lock body { overflow: hidden; }
html.chat-lock .wrap { padding-bottom: 0; }

/* ---- gõ @ ---- */
.dc-ac { position: absolute; left: 1em; right: 1em; bottom: 4.6em; background: #2b2d31; border: 1px solid #1e1f22; border-radius: 10px; padding: 6px; z-index: 12; box-shadow: 0 8px 24px rgba(0,0,0,.5); max-height: 18em; overflow: auto; }
.dc-ac button { display: flex; align-items: center; gap: 10px; width: 100%; background: none; border: 0; color: #dbdee1; padding: .4em .55em; border-radius: 6px; cursor: pointer; text-align: left; font-size: .95em; }
.dc-ac button.on, .dc-ac button:hover { background: #404249; }
.dc-ac img { width: 1.6em; height: 1.6em; border-radius: 50%; }
.dc-ac .ai { width: 1.6em; text-align: center; font-weight: 700; color: #949ba4; }
.dc-ac small { color: #949ba4; margin-left: auto; }

/* ---- cột phải: thành viên / thông tin DM ---- */
.dc-members { display: none; background: #2b2d31; flex-direction: column; overflow-y: auto; padding: .5em; min-height: 0; overscroll-behavior: contain; }
.dc.show-mem .dc-members { display: flex; }
.dc .mem { display: flex; align-items: center; gap: .7em; width: 100%; background: none; border: 0; padding: .3em .5em; border-radius: 6px; cursor: pointer; color: #dbdee1; text-align: left; font-size: 1em; }
.dc .mem:hover { background: #35373c; }
.dc .mem.off { opacity: .55; }
.dc .mem .mav { position: relative; width: 2.3em; height: 2.3em; flex: none; }
.dc .mem .mav img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; display: block; }
.dc .mem .mtx { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.dc .mem .mn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dc .mem small { font-size: .78em; color: #949ba4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- hồ sơ ---- */
.dc-modal { position: absolute; inset: 0; background: rgba(0,0,0,.6); display: grid; place-items: center; z-index: 30; padding: 12px; }
.dc .prof { width: 22em; max-width: 100%; max-height: 100%; overflow: auto; background: #111214; border-radius: 12px; }
.dc-members .prof { width: 100%; }
.dc .prof .pb { height: 6.5em; background: #5865f2 center / cover no-repeat; }
.dc .prof .pbody { padding: 0 1em 1em; }
.dc .prof .pavw { position: relative; width: 5.5em; height: 5.5em; margin-top: -2.8em; }
.dc .prof .pav { width: 100%; height: 100%; border-radius: 50%; border: .4em solid #111214; background: #1e1f22; object-fit: cover; display: block; }
.dc .prof .pavw .st { border-color: #111214; width: 1em; height: 1em; right: .1em; bottom: .1em; }
.dc .prof h3 { font-size: 1.3em; margin-top: .3em; display: flex; gap: .5em; align-items: center; flex-wrap: wrap; }
.dc .prof .un { color: #b5bac1; font-size: .88em; }
.dc .prof .cust { margin-top: .5em; font-size: .92em; color: #dbdee1; }
.dc .prof .box { background: #1e1f22; border-radius: 8px; padding: .7em .85em; margin-top: .8em; font-size: .88em; line-height: 1.45; }
.dc .prof .box b { display: block; font-size: .78em; text-transform: uppercase; color: #b5bac1; margin: .6em 0 .1em; }
.dc .prof .box b:first-child { margin-top: 0; }
.dc .prof .box i.note { display: block; margin-top: .7em; font-size: .85em; color: #80848e; }
.dc .prof code { background: #2b2d31; padding: 1px 6px; border-radius: 4px; user-select: all; }
.dc .role { display: inline-flex; align-items: center; gap: 5px; background: #2b2d31; border-radius: 6px; padding: 2px 8px; margin: 2px 4px 2px 0; font-size: .9em; }
.dc .role i { width: .75em; height: .75em; border-radius: 50%; background: #99aab5; }
.dc .pbtns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: .8em; }
.dc .pbtn { background: #2b2d31; border: 0; color: #dbdee1; border-radius: 8px; padding: .5em .8em; cursor: pointer; font-size: .88em; font-weight: 600; }
.dc .pbtn:hover { background: #3a3c42; } .dc .pbtn.pri { background: #5865f2; color: #fff; }

@media (max-width: 800px) {
    .dc, .dc.show-mem { grid-template-columns: 4em minmax(0, 1fr); }
    .dc .rail-ico { width: 2.8em; height: 2.8em; }
    .dc:not(.in-chat) .dc-main { display: none; }
    .dc.in-chat .dc-side { display: none; }
    .dc-back { display: block; }
    .dc.show-mem .dc-members { position: absolute; right: 0; top: 0; bottom: 0; width: 17em; z-index: 15; box-shadow: -8px 0 24px rgba(0,0,0,.5); }
    .dc-pop, .dc-pop.wide { width: calc(100% - 1em); right: .5em; }
    .gif-grid { columns: 2; }
    .dc-bot #dcBotName { display: none; }
    .dc-top { flex-wrap: wrap; }
}
"""

CHAT_JS = r"""
    function initChat2() {
        var dc = document.getElementById('dc');
        if (!dc) return;
        var $ = function (id) { return document.getElementById(id); };
        var rail = $('dcRail'), sideHead = $('dcSideHead'), chans = $('dcChans'), msgs = $('dcMsgs'),
            title = $('dcTitle'), ta = $('dcInput'), fileIn = $('dcFile'), pop = $('dcPop'),
            replyBar = $('dcReply'), filesBar = $('dcFiles'), sendBtn = $('dcSend'),
            tip = $('dcTip'), members = $('dcMembers'), newBar = $('dcNew');

        /* ---- icon SVG (thay cho emoji xấu) ---- */
        var P = {
            chat: 'M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z',
            people: 'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
            lock: 'M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z',
            unlock: 'M12 17c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm6-9h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6h1.9c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm0 12H6V10h12v10z',
            fs: 'M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z',
            fsx: 'M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z',
            doneall: 'M18 7l-1.41-1.41-6.34 6.34 1.41 1.41L18 7zm4.24-1.41L11.66 16.17 7.48 12l-1.41 1.41L11.66 19l12-12-1.42-1.41zM.41 13.41L6 19l1.41-1.41L1.83 12 .41 13.41z',
            emoji: 'M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm3.5-10c.83 0 1.5-.67 1.5-1.5S16.33 7 15.5 7 14 7.67 14 8.5s.67 1.5 1.5 1.5zm-7 0C9.33 10 10 9.33 10 8.5S9.33 7 8.5 7 7 7.67 7 8.5 7.67 10 8.5 10zm3.5 7.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z',
            plus: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm5 11h-4v4h-2v-4H7v-2h4V7h2v4h4v2z',
            send: 'M2.01 21L23 12 2.01 3 2 10l15 2-15 2z',
            reply: 'M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z',
            del: 'M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z',
            star: 'M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z',
            starO: 'M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z'
        };
        function ic(n) { return '<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="' + P[n] + '"/></svg>'; }
        [['dcMemBtn', 'people'], ['dcFull', 'fs'], ['dcMarkG', 'doneall'], ['dcAttach', 'plus'], ['dcEmoji', 'emoji'], ['dcSend', 'send']].forEach(function (x) { $(x[0]).innerHTML = ic(x[1]); });

        var rng = function (ranges) {
            var seen = {}, out = [];
            ranges.forEach(function (r) {
                for (var i = r[0]; i <= r[1]; i++) {
                    var ch = String.fromCodePoint(i);
                    if (/\p{Emoji_Presentation}/u.test(ch)) { if (!seen[ch]) { seen[ch] = 1; out.push(ch); } }
                    else if (i > 0xFF && /\p{Emoji}/u.test(ch)) { ch += '\uFE0F'; if (!seen[ch]) { seen[ch] = 1; out.push(ch); } }
                }
            });
            return out;
        };
        var CATS = [
            ['😀', 'Mặt', [[0x1F600, 0x1F64F], [0x1F910, 0x1F93F], [0x1F970, 0x1F97F], [0x1FAE0, 0x1FAEF]]],
            ['👋', 'Người & tay', [[0x1F440, 0x1F49F], [0x1F90C, 0x1F90F], [0x1F9B0, 0x1F9BF], [0x261D, 0x261D], [0x270A, 0x270D]]],
            ['🐶', 'Thiên nhiên & đồ ăn', [[0x1F300, 0x1F37F], [0x1F400, 0x1F43F], [0x1F980, 0x1F9AF]]],
            ['🎮', 'Hoạt động & vật', [[0x1F380, 0x1F3FA], [0x1F4A0, 0x1F4FF], [0x1F9E0, 0x1F9FF], [0x1FA70, 0x1FAFF]]],
            ['🚗', 'Du lịch', [[0x1F680, 0x1F6FF], [0x1F3E0, 0x1F3F0]]],
            ['❤️', 'Ký hiệu', [[0x2600, 0x27BF], [0x2B1B, 0x2B55], [0x1F500, 0x1F53D], [0x1F7E0, 0x1F7EB]]]
        ];
        var CAT_CACHE = {};

        var C = { guild: null, chan: null, name: '', isDM: false, canSend: true, list: [], lastId: null,
                  reply: null, pending: [], tick: 0, busy: false, sending: 0, seq: 0, railSig: '',
                  chCache: {}, popMode: 'compose', popMsg: null, popTab: 'u0', guilds: [],
                  members: {}, unread: {}, me: null, fullGuilds: false, showMem: false, autoPanel: false,
                  srvEmoji: null, mentionMap: {}, prof: null, dmUser: null, dmSig: '', unSig: '', up: 0,
                  cs: 1.2, favs: null, favSet: {}, prepended: false };
        try { var cs0 = parseFloat(localStorage.getItem('ks_chat_cs')); if (cs0 >= 0.8 && cs0 <= 1.8) C.cs = cs0; } catch (e) {}

        function h(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
        function hex6(c) { return ('000000' + Number(c).toString(16)).slice(-6); }
        function rpc(type, data) {
            return authFetch('/api/rpc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(Object.assign({ type: type }, data || {})) })
                .then(function (r) { return r.json(); })
                .then(function (d) { if (!d.ok) throw new Error(d.error || 'Lỗi không rõ'); return d.data; });
        }
        function saveLast() { try { localStorage.setItem('ks_chat_last', JSON.stringify({ g: C.guild, c: C.chan, n: C.name, d: C.isDM, u: C.dmUser })); } catch (e) {} }
        function loadLast() { try { return JSON.parse(localStorage.getItem('ks_chat_last') || 'null') || {}; } catch (e) { return {}; } }
        function openNow() { return (document.body.getAttribute('data-tab') === 'chat' && C.chan && !document.hidden) ? C.chan : null; }

        /* ---- cỡ chữ ---- */
        function applyCs() { document.documentElement.style.setProperty('--cs', C.cs); try { localStorage.setItem('ks_chat_cs', String(C.cs)); } catch (e) {} }
        applyCs();
        $('dcSmall').addEventListener('click', function () { C.cs = Math.max(0.8, Math.round((C.cs - 0.1) * 10) / 10); applyCs(); });
        $('dcBig').addEventListener('click', function () { C.cs = Math.min(1.8, Math.round((C.cs + 0.1) * 10) / 10); applyCs(); });

        function fmt(text) {
            var s = h(text);
            s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
            s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
            s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<i>$2</i>');
            s = s.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
            s = s.replace(/&lt;(a?):(\w+):(\d+)&gt;/g, function (_m, a, n, id) {
                return '<img class="ce" alt=":' + n + ':" src="https://cdn.discordapp.com/emojis/' + id + (a ? '.gif' : '.png') + '?size=48">';
            });
            return s;
        }
        function p2(n) { return ('0' + n).slice(-2); }
        function tfmt(ts) {
            var d = new Date(ts * 1000), t = p2(d.getHours()) + ':' + p2(d.getMinutes());
            return d.toDateString() === new Date().toDateString() ? 'Hôm nay ' + t : p2(d.getDate()) + '/' + p2(d.getMonth() + 1) + ' ' + t;
        }
        function fsize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }
        function httpsOnly(u) { return /^https:\/\//.test(u || '') ? u : ''; }

        function embedHtml(e) {
            var o = '<div class="emb"' + (e.color != null ? ' style="border-left-color:#' + hex6(e.color) + '"' : '') + '>';
            if (e.author) o += '<div class="ea">' + h(e.author) + '</div>';
            if (e.title) o += '<div class="et">' + (e.url ? '<a href="' + h(httpsOnly(e.url)) + '" target="_blank" rel="noopener noreferrer">' + h(e.title) + '</a>' : h(e.title)) + '</div>';
            if (e.description) o += '<div class="ed2">' + fmt(e.description) + '</div>';
            (e.fields || []).forEach(function (f) { o += '<div class="ef"><b>' + h(f.name) + '</b>' + fmt(f.value) + '</div>'; });
            if (e.video && httpsOnly(e.video)) o += '<video class="gifv" autoplay loop muted playsinline preload="metadata" src="' + h(e.video) + '"></video>';
            else if (httpsOnly(e.image)) o += '<img class="big" loading="lazy" src="' + h(e.image) + '" alt="">';
            else if (httpsOnly(e.thumb)) o += '<img class="big" loading="lazy" src="' + h(e.thumb) + '" alt="">';
            if (e.footer) o += '<div class="ft">' + h(e.footer) + '</div>';
            return o + '</div>';
        }

        function msgHtml(m, prev) {
            var grp = prev && prev.author.id === m.author.id && !m.reply && (m.ts - prev.ts) < 420;
            var o = '<div class="m' + (grp ? '' : ' first') + '" data-id="' + h(m.id) + '" data-act="sel">';
            o += grp ? '<div class="gut">' + tfmt(m.ts).slice(-5) + '</div>'
                     : '<img class="av" data-act="prof" data-uid="' + h(m.author.id) + '" loading="lazy" alt="" src="' + h(m.author.avatar) + '">';
            o += '<div class="body">';
            if (m.reply) o += '<div class="rep">↪ <b>' + h(m.reply.author || '?') + '</b> ' + h(m.reply.content || '(tin nhắn gốc)') + '</div>';
            if (!grp) o += '<div class="head"><span class="nm" data-act="prof" data-uid="' + h(m.author.id) + '">' + h(m.author.name) + '</span>' + (m.author.bot ? '<span class="tag">BOT</span>' : '') + '<span class="ts">' + tfmt(m.ts) + '</span></div>';
            var gifOnly = (m.embeds || []).length && /^https?:\/\/\S*(tenor|giphy)\.com\S*$/i.test((m.content || '').trim());
            if (m.content && !gifOnly) o += '<div class="txt">' + fmt(m.content) + (m.edited ? ' <span class="ed">(đã sửa)</span>' : '') + '</div>';
            (m.atts || []).forEach(function (a) {
                var u = h(httpsOnly(a.url));
                if (/^image\//.test(a.type)) o += '<div class="att"><a href="' + u + '" target="_blank" rel="noopener noreferrer"><img loading="lazy" src="' + u + '" alt=""></a></div>';
                else if (/^video\//.test(a.type)) o += '<div class="att"><video controls preload="none" src="' + u + '"></video></div>';
                else o += '<a class="file" href="' + u + '" target="_blank" rel="noopener noreferrer">📎 ' + h(a.name) + ' <small>(' + fsize(a.size) + ')</small></a>';
            });
            (m.embeds || []).forEach(function (e) { o += embedHtml(e); });
            if (m.components) o += '<div class="comp-note">(tin nhắn có nút bấm/menu, chỉ dùng được trên Discord)</div>';
            if (m.reactions && m.reactions.length) {
                o += '<div class="rx">';
                m.reactions.forEach(function (r) {
                    o += '<button type="button" class="rxb' + (r.me ? ' me' : '') + '" data-act="rx" data-emoji="' + h(r.emoji) + '" data-me="' + (r.me ? 1 : 0) + '">' +
                        (r.url ? '<img class="ce" src="' + h(r.url) + '" alt="">' : h(r.emoji)) + ' <span>' + r.count + '</span></button>';
                });
                o += '</div>';
            }
            o += '</div><div class="tools"><button type="button" data-act="rxadd" title="Thả icon">' + ic('emoji') + '</button><button type="button" data-act="reply" title="Trả lời">' + ic('reply') + '</button><button type="button" data-act="del" title="Xóa tin">' + ic('del') + '</button></div></div>';
            return o;
        }

        function renderMsgs(stick) {
            var atBottom = msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90, top = msgs.scrollTop, oldH = msgs.scrollHeight;
            var o = C.list.length ? '<button type="button" class="dc-older" data-act="older">⬆ Tải tin cũ hơn</button>' : '';
            for (var i = 0; i < C.list.length; i++) o += msgHtml(C.list[i], C.list[i - 1]);
            msgs.innerHTML = o || '<div class="dc-empty">Chưa có tin nhắn nào trong kênh này</div>';
            C.lastId = C.list.length ? C.list[C.list.length - 1].id : null;
            if (stick || atBottom) msgs.scrollTop = msgs.scrollHeight; else msgs.scrollTop = top + (msgs.scrollHeight - oldH > 0 && C.prepended ? msgs.scrollHeight - oldH : 0);
            C.prepended = false;
        }
        function mergeById(a, b) {
            var seen = {}, out = [];
            a.concat(b).forEach(function (m) { if (!seen[m.id]) { seen[m.id] = 1; out.push(m); } });
            out.sort(function (x, y) { return x.ts - y.ts; });
            return out;
        }
        function nearBottom() { return msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 90; }
        function loadHist(incremental, stick) {
            if (!C.chan || C.busy) return Promise.resolve();
            C.busy = true;
            var seq = C.seq, inc = incremental && C.lastId,
                req = { channel_id: C.chan, limit: inc ? 50 : Math.min(100, Math.max(50, C.list.length)) };
            if (inc) req.after = C.lastId;
            return rpc('history', req).then(function (d) {
                if (seq !== C.seq) return;
                C.canSend = d.channel.can_send;
                syncComposer();
                if (inc) {
                    if (!d.messages.length) return;
                    var was = nearBottom();
                    C.list = mergeById(C.list, d.messages).slice(-300);
                    renderMsgs(stick);
                    if (!was && !stick) newBar.hidden = false;
                } else {
                    C.list = d.messages;
                    renderMsgs(stick);
                }
            }).catch(function (e) {
                if (seq !== C.seq) return;
                if (!C.list.length) msgs.innerHTML = '<div class="dc-empty">' + h(e.message) + '</div>';
                else notify(e.message, true);
            }).then(function () { C.busy = false; });
        }
        function syncComposer() {
            ta.disabled = !C.chan || !C.canSend;
            sendBtn.disabled = ta.disabled;
            $('dcGif').disabled = ta.disabled;
            ta.placeholder = !C.chan ? 'Chọn kênh để chat' : (C.canSend ? 'Nhắn vào ' + (C.isDM ? '@' : '#') + C.name + ' bằng tên bot' : 'Bot không có quyền gửi tin ở kênh này');
        }

        function openChannel(id, name, canSend, isDM) {
            C.seq++; C.chan = id; C.name = name; C.isDM = !!isDM; C.canSend = canSend !== false; C.list = []; C.lastId = null; C.busy = false;
            clearReply();
            title.innerHTML = '<span class="dim">' + (isDM ? '@' : '#') + '</span>' + h(name);
            msgs.innerHTML = '<div class="dc-empty">Đang tải tin nhắn...</div>';
            dc.classList.add('in-chat');
            syncComposer(); markChan(); saveLast();
            newBar.hidden = true;
            if (C.unread[id]) { delete C.unread[id]; C.railSig = ''; renderRail(); paintChans(); updateTabBadge(); }
            pollUnread();
            loadHist(false, true);
        }
        function markChan() {
            chans.querySelectorAll('.chan').forEach(function (b) {
                var on = b.hasAttribute('data-dm') ? (C.isDM && b.getAttribute('data-dm') === C.dmUser) : b.getAttribute('data-id') === C.chan;
                b.classList.toggle('on', on);
            });
            paintChans();
        }

        /* ---- cột server: chấm trắng, "MỚI", số mention, tooltip khi rê chuột ---- */
        function unreadFor(pred) {
            var n = 0, m = 0, best = null;
            for (var k in C.unread) { var u = C.unread[k]; if (pred(u)) { n += u.n; m += u.m; if (!best || (u.t || 0) > (best.u.t || 0)) best = { k: k, u: u }; } }
            return { n: n, m: m, best: best };
        }
        function badge(x) {
            var o = '';
            if (x.n || x.m) o += '<i class="dotu"></i>';
            if (x.m) o += '<span class="bdg">' + (x.m > 99 ? '99+' : x.m) + '</span>';
            else if (x.n) o += '<span class="newp">MỚI</span>';
            return o;
        }
        function renderRail(list) {
            if (list) C.guilds = list.filter(function (g) { return g && g.id != null; });
            var sig = JSON.stringify(C.guilds.map(function (g) { return [g.id, g.icon]; })) + C.guild + JSON.stringify(C.unread) + (C.me ? C.me.avatar : '');
            if (sig === C.railSig) return;
            C.railSig = sig; tip.hidden = true;
            var keep = rail.scrollTop, o = '';
            if (C.me) o += '<div class="rw"><button type="button" class="rail-ico" data-g="@me" data-tn="Hồ sơ bot"><img src="' + h(C.me.avatar) + '" alt=""></button></div>';
            o += '<div class="rw"><button type="button" class="rail-ico' + (C.guild === '@dm' ? ' on' : '') + '" data-g="@dm" data-tn="Tin nhắn riêng">' + ic('chat') + '</button>' + badge(unreadFor(function (u) { return !u.g; })) + '</div>';
            o += '<div class="rw"><button type="button" class="rail-ico mini" data-g="@read" data-tn="Đánh dấu đã đọc tất cả">' + ic('doneall') + '</button></div><div class="rail-sep"></div>';
            C.guilds.forEach(function (g) {
                var ico = httpsOnly(g.icon) ? '<img src="' + h(g.icon) + '" alt="">' : h((g.name || '?').charAt(0).toUpperCase());
                var x = unreadFor(function (u) { return u.g === String(g.id); });
                o += '<div class="rw"><button type="button" class="rail-ico' + (String(g.id) === String(C.guild) ? ' on' : '') + '" data-g="' + h(g.id) + '" data-tn="' + h(g.name) + '">' + ico + '</button>' + badge(x) + '</div>';
            });
            rail.innerHTML = o;
            rail.scrollTop = keep;
        }
        function showTip(btn) {
            var g = btn.getAttribute('data-g'), name = btn.getAttribute('data-tn') || '', sub = '', x;
            if (g === '@dm') {
                x = unreadFor(function (u) { return !u.g; });
                if (x.best) sub = 'Tin nhắn mới từ ' + h(x.best.u.un || 'ai đó') + (x.n > 1 ? ' • ' + x.n + ' tin' : '');
            } else if (g !== '@me' && g !== '@read') {
                x = unreadFor(function (u) { return u.g === g; });
                if (x.best) sub = 'Hoạt động mới trong #' + h(x.best.u.c || 'kênh') + (x.n > 1 ? ' • ' + x.n + ' tin' : '');
            }
            tip.innerHTML = '<b>' + h(name) + '</b>' + (sub ? '<span>' + sub + '</span>' : '');
            var r = btn.getBoundingClientRect();
            tip.style.left = (r.right + 12) + 'px'; tip.style.top = (r.top + r.height / 2) + 'px';
            tip.hidden = false;
        }
        rail.addEventListener('mouseover', function (e) { var b = e.target.closest('.rail-ico'); if (b) showTip(b); });
        rail.addEventListener('mouseout', function (e) { if (e.target.closest('.rail-ico')) tip.hidden = true; });
        rail.addEventListener('scroll', function () { tip.hidden = true; });
        rail.addEventListener('click', function (e) {
            var b = e.target.closest('.rail-ico'); if (!b) return;
            var g = b.getAttribute('data-g');
            if (g === '@me') { if (C.me) openProfile(C.me.id, null); }
            else if (g === '@read') markRead('all');
            else if (g === '@dm') selectDM(); else selectGuild(g);
        });
        function loadGuilds() {
            rpc('guilds').then(function (d) { C.me = d.me; C.fullGuilds = true; C.railSig = ''; renderRail(d.guilds); }).catch(function () {});
        }
        function paintChans() {
            chans.querySelectorAll('.chan').forEach(function (b) {
                var u = null, cid = b.getAttribute('data-id');
                if (cid) u = C.unread[cid];
                else if (b.hasAttribute('data-dm')) { var did = b.getAttribute('data-dm'); for (var k in C.unread) if (C.unread[k].u === did) u = C.unread[k]; }
                b.classList.toggle('unr', !!(u && u.n));
                var old = b.querySelector('.bdg'); if (old) old.remove();
                if (u && u.m) { var s = document.createElement('span'); s.className = 'bdg'; s.textContent = u.m > 99 ? '99+' : u.m; b.appendChild(s); }
            });
        }
        function updateTabBadge() {
            var tn = 0, tm = 0; for (var k in C.unread) { tn += C.unread[k].n; tm += C.unread[k].m; }
            var tab = document.querySelector('.tab[data-tab="chat"]');
            if (tab) tab.textContent = 'Chat' + (tm ? ' (' + tm + ')' : (tn ? ' •' : ''));
        }
        function pollUnread() {
            if (document.hidden) return;
            rpc('unread', { open: openNow() }).then(function (u) {
                C.unread = u || {};
                var sig = JSON.stringify(C.unread), changed = sig !== C.unSig; C.unSig = sig;
                updateTabBadge();
                C.railSig = ''; renderRail(); paintChans();
                C.up++;
                if (C.guild === '@dm' && (changed || C.up % 8 === 0)) loadDmList();
            }).catch(function () {});
        }
        setInterval(pollUnread, 4000);
        function markRead(scope) {
            rpc('unread', { clear: scope, open: openNow() }).then(function (u) {
                C.unread = u || {}; C.unSig = JSON.stringify(C.unread); C.railSig = ''; renderRail(); paintChans(); updateTabBadge();
                notify(scope === 'all' ? 'Đã đánh dấu đã đọc tất cả' : 'Đã đánh dấu đã đọc');
            }).catch(function (e) { notify(e.message, true); });
        }
        $('dcMarkG').addEventListener('click', function () { if (C.guild) markRead(C.guild); });

        /* ---- chọn server / DM ---- */
        function selectGuild(id, thenChan) {
            C.guild = id; C.dmUser = null; C.railSig = ''; renderRail(C.guilds);
            dc.classList.remove('in-chat');
            if (C.autoPanel) { C.autoPanel = false; setPanel(false); }
            var g = C.guilds.filter(function (x) { return String(x.id) === String(id); })[0];
            sideHead.textContent = g ? g.name : 'Server';
            chans.innerHTML = '<div class="dc-note">Đang tải kênh...</div>';
            if (C.showMem) drawPanel(); else loadMembers(id).catch(function () {});
            var show = function (d) {
                if (C.guild !== id) return;
                var o = '';
                d.groups.forEach(function (gr) {
                    if (gr.name) o += '<div class="grp">' + h(gr.name) + '</div>';
                    gr.channels.forEach(function (c) { o += '<button type="button" class="chan" data-id="' + h(c.id) + '" data-name="' + h(c.name) + '" data-send="' + (c.can_send ? 1 : 0) + '"><span class="h">#</span><span class="t">' + h(c.name) + '</span></button>'; });
                });
                chans.innerHTML = o || '<div class="dc-note">Bot không xem được kênh chữ nào ở server này.</div>';
                markChan();
                if (thenChan) { var el = chans.querySelector('.chan[data-id="' + thenChan + '"]'); if (el) openChannel(thenChan, el.getAttribute('data-name'), el.getAttribute('data-send') === '1', false); }
            };
            if (C.chCache[id] && Date.now() - C.chCache[id].t < 60000) { show(C.chCache[id].d); return; }
            rpc('channels', { guild_id: id }).then(function (d) { C.chCache[id] = { t: Date.now(), d: d }; show(d); })
                .catch(function (e) { chans.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        var STL = { online: 'Trực tuyến', idle: 'Chờ', dnd: 'Đừng làm phiền', offline: 'Ngoại tuyến' };
        function dmRow(x) {
            var st = x.status || 'offline';
            var sub = x.custom || (st !== 'offline' ? STL[st] : '');
            return '<button type="button" class="chan dmi" data-dm="' + h(x.user.id) + '" data-name="' + h(x.user.name) + '"><span class="dav"><img src="' + h(x.user.avatar) + '" alt=""><i class="st s-' + h(st) + '"></i></span>' +
                '<span class="dtx"><span class="t">' + h(x.user.name) + '</span>' + (sub ? '<small>' + h(sub) + '</small>' : '') + '</span></button>';
        }
        function loadDmList() {
            if (!$('dmList')) return;
            rpc('dm_list').then(function (list) {
                var box = $('dmList'); if (!box || C.guild !== '@dm') return;
                var sig = JSON.stringify(list.map(function (x) { return [x.user.id, x.status, x.custom, x.last]; }));
                if (sig === C.dmSig && box.children.length) { paintChans(); return; }
                C.dmSig = sig;
                var keep = chans.scrollTop;
                box.innerHTML = list.length ? list.map(dmRow).join('') : '<div class="dc-note">Chưa có cuộc DM nào. Nhập User ID ở trên, hoặc bấm vào tên/avatar ai đó trong chat server rồi chọn Nhắn DM.</div>';
                chans.scrollTop = keep;
                markChan();
            }).catch(function (e) { var box = $('dmList'); if (box && !box.children.length) box.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        function selectDM() {
            C.guild = '@dm'; C.railSig = ''; renderRail(C.guilds);
            dc.classList.remove('in-chat');
            sideHead.textContent = 'Tin nhắn riêng'; C.dmSig = '';
            chans.innerHTML = '<div class="dm-open"><input type="text" id="dmId" placeholder="Nhập User ID để mở DM" inputmode="numeric"><button type="button" id="dmGo">Mở</button></div><div id="dmList"><div class="dc-note">Đang tải...</div></div>';
            $('dmGo').addEventListener('click', function () { var v = $('dmId').value.trim(); if (v) openDM(v); });
            loadDmList();
            if (C.showMem) drawPanel();
        }
        function openDM(uid) {
            rpc('dm_open', { user_id: uid }).then(function (d) {
                if (C.guild !== '@dm') selectDM();
                C.dmUser = d.user.id;
                openChannel(d.channel_id, d.user.name, true, true);
                if (!C.showMem && window.innerWidth > 800) { C.autoPanel = true; setPanel(true); } else drawPanel();
            }).catch(function (e) { notify(e.message, true); });
        }
        chans.addEventListener('click', function (e) {
            var b = e.target.closest('.chan'); if (!b) return;
            if (b.hasAttribute('data-dm')) { openDM(b.getAttribute('data-dm')); return; }
            openChannel(b.getAttribute('data-id'), b.getAttribute('data-name'), b.getAttribute('data-send') === '1', false);
        });
        $('dcBack').addEventListener('click', function () { dc.classList.remove('in-chat'); });

        /* ---- thao tác trên tin nhắn ---- */
        function msgOf(el) { var row = el.closest('.m'); if (!row) return null; var id = row.getAttribute('data-id'); return C.list.filter(function (m) { return m.id === id; })[0] || null; }
        function toggleReact(mid, emoji, remove) {
            rpc('react', { channel_id: C.chan, message_id: mid, emoji: emoji, remove: remove })
                .then(function () { C.busy = false; return loadHist(false); })
                .catch(function (e) { notify(e.message, true); });
        }
        msgs.addEventListener('click', function (e) {
            var t = e.target.closest('[data-act]'); if (!t) return;
            var act = t.getAttribute('data-act');
            if (act === 'sel') { if (e.target.closest('a,img.ce,video')) return; document.querySelectorAll('.m.sel').forEach(function (x) { if (x !== t) x.classList.remove('sel'); }); t.classList.toggle('sel'); return; }
            e.stopPropagation();
            var m = msgOf(t);
            if (act === 'older') {
                if (!C.list.length) return;
                rpc('history', { channel_id: C.chan, before: C.list[0].id, limit: 50 }).then(function (d) {
                    if (!d.messages.length) { notify('Đã tới tin nhắn đầu tiên'); return; }
                    C.list = mergeById(d.messages, C.list); C.prepended = true; renderMsgs(false);
                }).catch(function (er) { notify(er.message, true); });
            } else if (act === 'prof') { openProfile(t.getAttribute('data-uid'), C.isDM ? null : C.guild); }
            else if (!m) { return; }
            else if (act === 'rx') toggleReact(m.id, t.getAttribute('data-emoji'), t.getAttribute('data-me') === '1');
            else if (act === 'rxadd') openPop('react', m.id);
            else if (act === 'reply') { C.reply = { id: m.id, name: m.author.name, ping: true }; drawReply(); ta.focus(); }
            else if (act === 'del') {
                if (!confirm('Xóa tin nhắn này?')) return;
                rpc('delete', { channel_id: C.chan, message_id: m.id }).then(function () { C.list = C.list.filter(function (x) { return x.id !== m.id; }); renderMsgs(false); notify('Đã xóa tin nhắn'); }).catch(function (er) { notify(er.message, true); });
            }
        });
        function drawReply() {
            if (!C.reply) { replyBar.hidden = true; replyBar.innerHTML = ''; return; }
            replyBar.hidden = false;
            replyBar.innerHTML = '<span>Đang trả lời <b>' + h(C.reply.name) + '</b></span>' +
                '<span class="rbtns"><button type="button" class="pingbtn' + (C.reply.ping ? ' on' : '') + '" id="dcPing" title="Bật: tag người được trả lời. Tắt: trả lời im lặng">@ ' + (C.reply.ping ? 'BẬT' : 'TẮT') + '</button>' +
                '<button type="button" id="dcReplyX" title="Hủy trả lời">✕</button></span>';
        }
        function clearReply() { C.reply = null; drawReply(); }
        replyBar.addEventListener('click', function (e) {
            if (e.target.id === 'dcReplyX') clearReply();
            else if (e.target.id === 'dcPing' && C.reply) { C.reply.ping = !C.reply.ping; drawReply(); }
        });

        /* ---- picker emoji ---- */
        function drawSrv(q) {
            var grid = $('emoGrid'); if (!grid) return;
            q = (q || '').toLowerCase();
            var list = (C.srvEmoji || []).filter(function (x) { return !q || x.name.toLowerCase().indexOf(q) >= 0; }).slice(0, 400);
            grid.innerHTML = list.length ? list.map(function (x) {
                return '<button type="button" data-e="&lt;' + (x.animated ? 'a' : '') + ':' + h(x.name) + ':' + h(x.id) + '&gt;" title=":' + h(x.name) + ': (' + h(x.guild) + ')"><img loading="lazy" src="' + h(x.url) + '" alt=""></button>';
            }).join('') : '<div class="dc-note" style="grid-column:1/-1">Không có emoji nào.</div>';
        }
        function popBuild() {
            var o = '<div class="pop-tabs">';
            CATS.forEach(function (c, i) { o += '<button type="button" data-t="u' + i + '" class="' + (C.popTab === 'u' + i ? 'on' : '') + '" title="' + c[1] + '">' + c[0] + '</button>'; });
            o += '<button type="button" data-t="srv" class="' + (C.popTab === 'srv' ? 'on' : '') + '">Server</button></div>' +
                 '<input type="text" class="gif-search" id="emoQ" placeholder="Tìm emoji server..." autocomplete="off"' + (C.popTab === 'srv' ? '' : ' hidden') + '><div class="emo-grid" id="emoGrid"></div>';
            pop.innerHTML = o;
            var grid = $('emoGrid');
            if (C.popTab === 'srv') {
                if (C.srvEmoji) { drawSrv(''); return; }
                grid.innerHTML = '<div class="dc-note" style="grid-column:1/-1">Đang tải...</div>';
                rpc('emojis', {}).then(function (list) { C.srvEmoji = list; drawSrv(''); })
                    .catch(function (e) { grid.innerHTML = '<div class="dc-note" style="grid-column:1/-1">' + h(e.message) + '</div>'; });
                return;
            }
            var i = parseInt(C.popTab.slice(1), 10) || 0;
            var arr = CAT_CACHE[i] || (CAT_CACHE[i] = rng(CATS[i][2]));
            grid.innerHTML = arr.map(function (e) { return '<button type="button" data-e="' + h(e) + '">' + h(e) + '</button>'; }).join('');
        }

        /* ---- picker GIF: tìm kiếm + yêu thích (lưu trên web) ---- */
        var G = { q: '', next: '', seq: 0, timer: null, tab: 'search' };
        function loadFavs() {
            return authFetch('/api/giffav', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
                C.favs = d.items || []; C.favSet = {}; C.favs.forEach(function (f) { C.favSet[f.url] = 1; });
            }).catch(function () { C.favs = C.favs || []; });
        }
        function gifItem(g) {
            var on = !!C.favSet[g.url];
            return '<div class="gi"><button type="button" class="gpick" data-gif="' + h(g.url) + '"><img loading="lazy" src="' + h(g.preview) + '" alt=""></button>' +
                '<button type="button" class="gstar' + (on ? ' on' : '') + '" data-fav="' + h(g.url) + '" data-prev="' + h(g.preview) + '" title="' + (on ? 'Bỏ yêu thích' : 'Thêm vào yêu thích') + '">' + ic(on ? 'star' : 'starO') + '</button></div>';
        }
        function popGif() {
            pop.innerHTML = '<div class="pop-tabs"><button type="button" data-gt="search" id="gtS">GIF</button><button type="button" data-gt="fav" id="gtF">★ Yêu thích</button></div>' +
                '<input type="text" class="gif-search" id="gifQ" placeholder="Tìm GIF..." autocomplete="off"><div class="gif-grid" id="gifGrid"></div><button type="button" class="gif-more" id="gifMore" hidden>Xem thêm</button>';
            G.q = ''; G.next = '';
            loadFavs().then(function () { gifTab(G.tab); });
        }
        function gifTab(t) {
            G.tab = t;
            var s = $('gtS'), f = $('gtF'); if (!s) return;
            s.classList.toggle('on', t === 'search'); f.classList.toggle('on', t === 'fav');
            $('gifQ').hidden = (t === 'fav'); $('gifMore').hidden = true;
            if (t === 'fav') drawFavs(); else { loadGifs(false); $('gifQ').focus(); }
        }
        function drawFavs() {
            var grid = $('gifGrid'); if (!grid) return;
            grid.innerHTML = (C.favs && C.favs.length) ? C.favs.map(gifItem).join('') : '<div class="dc-note">Chưa có GIF yêu thích. Mở tab GIF, rê chuột vào ảnh rồi bấm ngôi sao để lưu.</div>';
        }
        function loadGifs(more) {
            var grid = $('gifGrid'), moreBtn = $('gifMore');
            if (!grid) return;
            var seq = ++G.seq;
            if (!more) grid.innerHTML = '<div class="dc-note">Đang tải GIF...</div>';
            authFetch('/api/gif?q=' + encodeURIComponent(G.q) + (more && G.next ? '&pos=' + encodeURIComponent(G.next) : ''), { cache: 'no-store' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (seq !== G.seq || G.tab !== 'search') return;
                    if (d.no_key) {
                        grid.innerHTML = '<div class="dc-note">Chưa bật kho GIF. Thêm biến <b>TENOR_API_KEY</b> hoặc <b>GIPHY_API_KEY</b> vào Môi trường rồi khởi động lại. Hoặc dán link GIF thẳng vào ô chat, hoặc bấm dấu cộng để tải file .gif lên.</div>';
                        moreBtn.hidden = true; return;
                    }
                    if (!d.ok) throw new Error(d.error || 'Lỗi tải GIF');
                    var o = d.items.map(gifItem).join('');
                    if (more) grid.insertAdjacentHTML('beforeend', o);
                    else grid.innerHTML = o || '<div class="dc-note">Không tìm thấy GIF nào.</div>';
                    G.next = d.next || '';
                    moreBtn.hidden = !G.next;
                })
                .catch(function (e) { if (seq === G.seq) grid.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        function toggleFav(url, prev) {
            var action = C.favSet[url] ? 'remove' : 'add';
            authFetch('/api/giffav', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: action, url: url, preview: prev }) })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!d.ok) throw new Error(d.error || 'Lỗi lưu GIF');
                    C.favs = d.items; C.favSet = {}; C.favs.forEach(function (f) { C.favSet[f.url] = 1; });
                    if (G.tab === 'fav') drawFavs();
                    else document.querySelectorAll('#gifGrid .gstar').forEach(function (b) {
                        var on = !!C.favSet[b.getAttribute('data-fav')];
                        b.classList.toggle('on', on); b.innerHTML = ic(on ? 'star' : 'starO'); b.title = on ? 'Bỏ yêu thích' : 'Thêm vào yêu thích';
                    });
                    notify(action === 'add' ? 'Đã lưu GIF vào yêu thích' : 'Đã bỏ khỏi yêu thích');
                }).catch(function (e) { notify(e.message, true); });
        }
        function applyMentions(text) {
            Object.keys(C.mentionMap).sort(function (a, b) { return b.length - a.length; }).forEach(function (n) {
                text = text.split('@' + n).join('<@' + C.mentionMap[n] + '>');
            });
            return text;
        }
        function sendGif(url) {
            if (!C.chan || !C.canSend) { notify('Chọn kênh có quyền gửi trước đã', true); return; }
            var chan = C.chan, rp = C.reply; clearReply(); closePop(); C.sending++;
            rpc('send', { channel_id: chan, content: url, reply_to: rp ? rp.id : null, ping: rp ? rp.ping : true, uploads: [] })
                .then(function () { C.busy = false; return loadHist(true, true); })
                .catch(function (e) { notify(e.message, true); })
                .then(function () { C.sending--; });
        }
        pop.addEventListener('input', function (e) {
            if (e.target.id === 'emoQ') { drawSrv(e.target.value); return; }
            if (e.target.id !== 'gifQ') return;
            clearTimeout(G.timer);
            var v = e.target.value.trim();
            G.timer = setTimeout(function () { G.q = v; G.next = ''; loadGifs(false); }, 400);
        });
        function openPop(mode, msgId) {
            C.popMode = mode; C.popMsg = msgId || null;
            pop.classList.add('show'); pop.classList.toggle('wide', mode === 'gif');
            if (mode === 'gif') popGif(); else popBuild();
        }
        function closePop() { pop.classList.remove('show'); }
        pop.addEventListener('click', function (e) {
            e.stopPropagation();
            var t = e.target.closest('button'); if (!t) return;
            if (t.id === 'gifMore') { loadGifs(true); return; }
            if (t.hasAttribute('data-gt')) { gifTab(t.getAttribute('data-gt')); return; }
            if (t.hasAttribute('data-fav')) { toggleFav(t.getAttribute('data-fav'), t.getAttribute('data-prev')); return; }
            if (t.hasAttribute('data-gif')) { sendGif(t.getAttribute('data-gif')); return; }
            if (t.hasAttribute('data-t')) { C.popTab = t.getAttribute('data-t'); popBuild(); return; }
            var raw = t.getAttribute('data-e'); if (!raw) return;
            var emoji = raw.replace(/&lt;/g, '<').replace(/&gt;/g, '>');
            if (C.popMode === 'react') { toggleReact(C.popMsg, emoji, false); closePop(); }
            else { var s = ta.selectionStart || ta.value.length; ta.value = ta.value.slice(0, s) + emoji + ta.value.slice(ta.selectionEnd || s); ta.focus(); ta.setSelectionRange(s + emoji.length, s + emoji.length); }
        });
        $('dcGif').addEventListener('click', function (e) { e.stopPropagation(); if (pop.classList.contains('show') && C.popMode === 'gif') closePop(); else openPop('gif'); });
        $('dcEmoji').addEventListener('click', function (e) { e.stopPropagation(); if (pop.classList.contains('show') && C.popMode === 'compose') closePop(); else openPop('compose'); });
        document.addEventListener('click', function (e) { if (!pop.contains(e.target)) closePop(); });

        /* ---- file đính kèm + gửi ---- */
        function renderFiles() {
            filesBar.innerHTML = C.pending.map(function (f, i) { return '<div class="fchip"><span>📎 ' + h(f.name) + ' (' + fsize(f.size) + ')</span><button type="button" data-i="' + i + '">✕</button></div>'; }).join('');
        }
        filesBar.addEventListener('click', function (e) { var b = e.target.closest('button'); if (!b) return; C.pending.splice(+b.getAttribute('data-i'), 1); renderFiles(); });
        function addFiles(list) {
            Array.prototype.forEach.call(list || [], function (f) {
                if (f.size > 10 * 1024 * 1024) { notify(f.name + ' quá 10MB, Discord không nhận', true); return; }
                if (C.pending.length >= 10) { notify('Tối đa 10 file mỗi tin', true); return; }
                C.pending.push(f);
            });
            renderFiles();
        }
        $('dcAttach').addEventListener('click', function () { if (C.chan && C.canSend) fileIn.click(); });
        fileIn.addEventListener('change', function () { addFiles(fileIn.files); fileIn.value = ''; });
        ta.addEventListener('paste', function (e) { if (e.clipboardData && e.clipboardData.files.length) { addFiles(e.clipboardData.files); e.preventDefault(); } });
        var main = dc.querySelector('.dc-main');
        main.addEventListener('dragover', function (e) { e.preventDefault(); dc.classList.add('drag'); });
        main.addEventListener('dragleave', function (e) { if (e.target === main) dc.classList.remove('drag'); });
        main.addEventListener('drop', function (e) { e.preventDefault(); dc.classList.remove('drag'); if (C.chan && C.canSend) addFiles(e.dataTransfer.files); });

        function uploadAll(files) {
            return files.reduce(function (p, f) {
                return p.then(function (arr) {
                    return authFetch('/api/upload', { method: 'POST', headers: { 'X-Filename': encodeURIComponent(f.name), 'Content-Type': 'application/octet-stream' }, body: f })
                        .then(function (r) { return r.json(); })
                        .then(function (d) { if (!d.ok) throw new Error(d.error || 'Upload lỗi'); arr.push(d.id); return arr; });
                });
            }, Promise.resolve([]));
        }
        function autosize() { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 10 * 16 * C.cs) + 'px'; }
        function send() {
            if (!C.chan || !C.canSend) return;
            var text = ta.value.trim();
            if (!text && !C.pending.length) return;
            var files = C.pending.slice(), rp = C.reply, chan = C.chan, out = applyMentions(text);
            ta.value = ''; autosize(); C.pending = []; renderFiles(); clearReply(); acHide(); ta.focus();
            C.sending++;
            uploadAll(files).then(function (ids) {
                return rpc('send', { channel_id: chan, content: out, reply_to: rp ? rp.id : null, ping: rp ? rp.ping : true, uploads: ids });
            }).then(function () { C.busy = false; return loadHist(true, true); })
              .catch(function (e) { notify(e.message, true); if (!ta.value) { ta.value = text; autosize(); } })
              .then(function () { C.sending--; });
        }
        sendBtn.addEventListener('click', send);
        ta.addEventListener('input', autosize);
        ta.addEventListener('keydown', function (e) {
            if (acKey(e)) return;
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
        });

        /* ---- nút KHÓA cuộn trang ---- */
        var lockBtn = $('dcLock'), locked = true;
        try { locked = localStorage.getItem('ks_chat_lock') !== '0'; } catch (e) {}
        function applyLock() {
            var on = locked && document.body.getAttribute('data-tab') === 'chat';
            document.documentElement.classList.toggle('chat-lock', on);
            lockBtn.innerHTML = ic(locked ? 'lock' : 'unlock');
            lockBtn.classList.toggle('on', locked);
            lockBtn.title = locked ? 'Đang khóa trang, chỉ cuộn trong chat. Bấm để mở' : 'Bấm để khóa trang, không cuộn cả web';
        }
        lockBtn.addEventListener('click', function () {
            locked = !locked;
            try { localStorage.setItem('ks_chat_lock', locked ? '1' : '0'); } catch (e) {}
            applyLock();
        });
        window.__chatLockApply = applyLock;
        applyLock();

        /* ---- TOÀN MÀN HÌNH kiểu Discord ---- */
        var fsBtn = $('dcFull'), fullOn = false;
        function setFull(on) {
            fullOn = on;
            document.documentElement.classList.toggle('chat-full', on);
            fsBtn.innerHTML = ic(on ? 'fsx' : 'fs'); fsBtn.classList.toggle('on', on);
            fsBtn.title = on ? 'Thoát toàn màn hình (Esc)' : 'Toàn màn hình';
            var de = document.documentElement;
            if (on && de.requestFullscreen) de.requestFullscreen().catch(function () {});
            else if (!on && document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
        }
        fsBtn.addEventListener('click', function () { setFull(!fullOn); });
        document.addEventListener('fullscreenchange', function () { if (fullOn && !document.fullscreenElement) setFull(false); });

        /* ---- cập nhật tin mới ---- */
        setInterval(function () {
            if (document.body.getAttribute('data-tab') !== 'chat' || !C.chan || document.hidden || C.busy || C.sending) return;
            C.tick++;
            loadHist(!(C.tick % 6 === 0 && C.list.length <= 100));
        }, 2500);

        /* ---- cột phải: thành viên (server) hoặc thông tin người dùng (DM) ---- */
        function loadMembers(gid) {
            if (!gid || gid === '@dm') return Promise.resolve({ list: [], presence: true });
            var c = C.members[gid];
            if (c && Date.now() - c.t < 120000) return Promise.resolve(c.d);
            return rpc('members', { guild_id: gid }).then(function (d) { C.members[gid] = { t: Date.now(), d: d }; return d; });
        }
        function setPanel(on) {
            C.showMem = on; dc.classList.toggle('show-mem', on);
            if (on) drawPanel();
        }
        function drawPanel() {
            if (!C.showMem) return;
            if (C.guild === '@dm') {
                if (!C.dmUser) { members.innerHTML = '<div class="dc-note">Chọn một cuộc DM để xem thông tin người dùng.</div>'; return; }
                var u = C.dmUser;
                members.innerHTML = '<div class="dc-note">Đang tải...</div>';
                rpc('profile', { user_id: u, guild_id: null }).then(function (p) {
                    if (C.dmUser !== u || C.guild !== '@dm') return;
                    C.prof = p; members.innerHTML = profHtml(p, false);
                }).catch(function (e) { members.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
                return;
            }
            drawMembers();
        }
        function drawMembers() {
            if (!C.guild || C.guild === '@dm') return;
            var g = C.guild;
            members.innerHTML = '<div class="dc-note">Đang tải...</div>';
            loadMembers(g).then(function (d) {
                if (C.guild !== g || !C.showMem) return;
                var list = d.list || [];
                var on = list.filter(function (u) { return u.status && u.status !== 'offline'; });
                var off = list.filter(function (u) { return !(u.status && u.status !== 'offline'); });
                function row(u, cls) {
                    return '<button type="button" class="mem' + cls + '" data-uid="' + h(u.id) + '"><span class="mav"><img loading="lazy" src="' + h(u.avatar) + '" alt=""><i class="st s-' + h(u.status) + '"></i></span>' +
                        '<span class="mtx"><span class="mn"' + (u.color ? ' style="color:#' + hex6(u.color) + '"' : '') + '>' + h(u.name) + '</span>' + (u.act ? '<small>' + h(u.act) + '</small>' : '') + '</span>' + (u.bot ? '<span class="tag">BOT</span>' : '') + '</button>';
                }
                var o = '';
                if (d.presence === false) o += '<div class="dc-warn"><b>Chưa thấy được trạng thái online</b>Bot chưa bật Presence Intent nên ai cũng hiện offline. Bật 2 công tắc <b>Presence Intent</b> + <b>Server Members Intent</b> trong Discord Developer Portal &gt; Bot, rồi trong bot.py thêm <code>intents.presences = True</code> và <code>intents.members = True</code>, khởi động lại bot.</div>';
                else if (d.members_intent === false) o += '<div class="dc-warn"><b>Thiếu Members Intent</b>Bot chỉ thấy một phần thành viên. Bật <code>intents.members = True</code>.</div>';
                if (on.length) o += '<div class="grp">Online — ' + on.length + '</div>' + on.map(function (u) { return row(u, ''); }).join('');
                if (off.length) o += '<div class="grp">' + (on.length ? 'Offline' : 'Thành viên') + ' — ' + off.length + '</div>' + off.slice(0, 250).map(function (u) { return row(u, on.length ? ' off' : ''); }).join('');
                members.innerHTML = o;
            }).catch(function (e) { members.innerHTML = '<div class="dc-note">' + h(e.message) + '</div>'; });
        }
        $('dcMemBtn').addEventListener('click', function () { C.autoPanel = false; setPanel(!C.showMem); });
        members.addEventListener('click', function (e) {
            var b = e.target.closest('.mem'); if (b) { openProfile(b.getAttribute('data-uid'), C.guild); return; }
            var p = e.target.closest('[data-p]'); if (p && C.prof && p.getAttribute('data-p') === 'copy') copyText(C.prof.id);
        });

        /* ---- hồ sơ người dùng ---- */
        function dfmt(ts) { return ts ? new Date(ts * 1000).toLocaleDateString('vi-VN') : '—'; }
        function copyText(s) {
            if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(s).then(function () { notify('Đã copy ' + s); }); return; }
            var t = document.createElement('textarea'); t.value = s; document.body.appendChild(t); t.select();
            try { document.execCommand('copy'); notify('Đã copy ' + s); } catch (e) {}
            t.remove();
        }
        function insertAtCursor(txt) {
            var s = ta.selectionStart == null ? ta.value.length : ta.selectionStart, e2 = ta.selectionEnd == null ? s : ta.selectionEnd;
            ta.value = ta.value.slice(0, s) + txt + ta.value.slice(e2); ta.focus(); ta.setSelectionRange(s + txt.length, s + txt.length); autosize();
        }
        function profHtml(p, modal) {
            var ban = httpsOnly(p.banner) ? 'background-image:url(&quot;' + h(p.banner) + '&quot;)' : (p.accent != null ? 'background:#' + hex6(p.accent) : '');
            var roles = (p.roles || []).map(function (r) {
                return '<span class="role"><i' + (r.color ? ' style="background:#' + hex6(r.color) + '"' : '') + '></i>' + h(r.name) + '</span>';
            }).join('');
            var st = p.status || 'offline';
            var stText = p.presence === false ? 'Không rõ (bot chưa bật Presence Intent)' : (STL[st] || st);
            var acts = (p.activities || []).map(h).join(', ');
            var btns = modal
                ? '<button type="button" class="pbtn pri" data-p="dm">Nhắn DM</button><button type="button" class="pbtn" data-p="copy">Copy ID</button><button type="button" class="pbtn" data-p="mention">@ Mention</button><button type="button" class="pbtn" data-p="close">Đóng</button>'
                : '<button type="button" class="pbtn" data-p="copy">Copy ID</button>';
            return '<div class="prof"><div class="pb" style="' + ban + '"></div><div class="pbody">' +
                '<div class="pavw"><img class="pav" src="' + h(p.avatar) + '" alt=""><i class="st s-' + h(st) + '"></i></div>' +
                '<h3>' + h(p.name) + (p.bot ? '<span class="tag">BOT</span>' : '') + '</h3><div class="un">' + h(p.username) + '</div>' +
                (p.custom ? '<div class="cust">' + h(p.custom) + '</div>' : '') +
                '<div class="box"><b>Trạng thái</b>' + stText + (acts ? '<b>Đang hoạt động</b>' + acts : '') +
                '<b>ID người dùng</b><code>' + h(p.id) + '</code>' +
                '<b>Tạo tài khoản</b>' + dfmt(p.created) + (p.joined ? '<b>Vào server</b>' + dfmt(p.joined) : '') +
                ((p.mutual && p.mutual.length) ? '<b>Server chung với bot</b>' + p.mutual.map(h).join(', ') : '') +
                (roles ? '<b>Vai trò</b>' + roles : '') +
                '<i class="note">Phần "Giới thiệu bản thân" (bio): Discord không cho bot đọc nên không hiện được.</i></div>' +
                '<div class="pbtns">' + btns + '</div></div></div>';
        }
        function openProfile(uid, gid) {
            var box = $('dcProf'); box.hidden = false;
            box.innerHTML = '<div class="prof"><div class="dc-note">Đang tải...</div></div>';
            rpc('profile', { user_id: uid, guild_id: gid && gid !== '@dm' ? gid : null }).then(function (p) {
                C.prof = p; box.innerHTML = profHtml(p, true);
            }).catch(function (e) {
                box.innerHTML = '<div class="prof"><div class="dc-note">' + h(e.message) + '</div><div class="pbtns" style="padding:0 1em 1em"><button type="button" class="pbtn" data-p="close">Đóng</button></div></div>';
            });
        }
        $('dcProf').addEventListener('click', function (e) {
            var box = this, b = e.target.closest('[data-p]');
            if (e.target === box || (b && b.getAttribute('data-p') === 'close')) { box.hidden = true; return; }
            if (!b || !C.prof) return;
            var a = b.getAttribute('data-p');
            if (a === 'copy') copyText(C.prof.id);
            else if (a === 'dm') { box.hidden = true; if (C.prof.bot) notify('Bot không DM được bot khác', true); else openDM(C.prof.id); }
            else if (a === 'mention') { box.hidden = true; C.mentionMap[C.prof.name] = C.prof.id; insertAtCursor('@' + C.prof.name + ' '); }
        });

        /* ---- gõ @ để chọn người ping ---- */
        var AC = { items: [], sel: 0, start: -1 }, acBox = $('dcAc');
        function acHide() { acBox.hidden = true; AC.items = []; }
        function acDraw() {
            acBox.innerHTML = AC.items.map(function (u, i) {
                return '<button type="button" data-i="' + i + '" class="' + (i === AC.sel ? 'on' : '') + '">' + (u.avatar ? '<img src="' + h(u.avatar) + '" alt="">' : '<span class="ai">@</span>') + '<b>' + h(u.name) + '</b>' + (u.desc ? '<small>' + h(u.desc) + '</small>' : '') + '</button>';
            }).join('');
            acBox.hidden = !AC.items.length;
        }
        function acCheck() {
            var pos = ta.selectionStart, m = /(^|\s)@([^\s@]{0,32})$/.exec(ta.value.slice(0, pos));
            if (!m || !C.chan || C.isDM) { acHide(); return; }
            var q = m[2].toLowerCase(); AC.start = pos - m[2].length - 1;
            var c = C.members[C.guild];
            if (!c) loadMembers(C.guild).then(acCheck).catch(function () {});
            var items = [];
            [['everyone', 'Ping tất cả'], ['here', 'Ping người online']].forEach(function (s) { if (s[0].indexOf(q) === 0) items.push({ name: s[0], desc: s[1], special: true }); });
            var hits = ((c && c.d && c.d.list) || []).filter(function (u) { return u.name.toLowerCase().indexOf(q) >= 0; });
            hits.sort(function (a, b) { return (b.name.toLowerCase().indexOf(q) === 0) - (a.name.toLowerCase().indexOf(q) === 0); });
            AC.items = items.concat(hits).slice(0, 10); AC.sel = 0; acDraw();
        }
        function acPick(i) {
            var u = AC.items[i]; if (!u) return;
            var pos = ta.selectionStart, ins = '@' + u.name + ' ';
            if (!u.special) C.mentionMap[u.name] = u.id;
            ta.value = ta.value.slice(0, AC.start) + ins + ta.value.slice(pos);
            var np = AC.start + ins.length; ta.focus(); ta.setSelectionRange(np, np); acHide(); autosize();
        }
        function acKey(e) {
            if (acBox.hidden || e.isComposing) return false;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { AC.sel = (AC.sel + (e.key === 'ArrowDown' ? 1 : AC.items.length - 1)) % AC.items.length; acDraw(); e.preventDefault(); return true; }
            if (e.key === 'Enter' || e.key === 'Tab') { acPick(AC.sel); e.preventDefault(); return true; }
            if (e.key === 'Escape') { acHide(); return true; }
            return false;
        }
        acBox.addEventListener('mousedown', function (e) { var b = e.target.closest('button'); if (b) { e.preventDefault(); acPick(+b.getAttribute('data-i')); } });
        ta.addEventListener('input', acCheck); ta.addEventListener('click', acCheck);

        /* ---- nút "Có tin nhắn mới" ---- */
        msgs.addEventListener('scroll', function () { if (nearBottom()) newBar.hidden = true; });
        newBar.addEventListener('click', function () { msgs.scrollTop = msgs.scrollHeight; newBar.hidden = true; });

        onGuildList = function (l) { if (!C.fullGuilds) renderRail(l); };
        syncComposer();
        window.__chatShow = function () {
            if (!C.fullGuilds) { loadGuilds(); renderRail(liveStats.guild_list || C.guilds); }
            pollUnread();
            if (C.guild) return;
            var last = loadLast();
            if (last.d && last.u) { selectDM(); openDM(last.u); }
            else if (last.g && last.g !== '@dm') selectGuild(last.g, last.c);
            else if (C.guilds.length) selectGuild(C.guilds[0].id);
        };
    }
    if (CHAT_ON) initChat2();
"""
