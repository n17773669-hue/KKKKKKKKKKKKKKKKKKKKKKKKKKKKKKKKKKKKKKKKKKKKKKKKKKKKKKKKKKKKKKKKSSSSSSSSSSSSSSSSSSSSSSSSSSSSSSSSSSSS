"""Chạy bot.py nhưng ép trạng thái thành Streaming (có nút "Xem").

Không cần sửa bot.py: file này vá discord.Client.change_presence (và tham số
activity lúc khởi tạo) rồi chạy bot.py như bình thường. Nếu việc vá lỗi thì
vẫn chạy bot.py bình thường, không làm bot chết.
Dashboard: công tắc "Luôn dùng Streaming" (force_stream) và ô Link Stream
(stream_url) trong config.json.
"""
import json
import re
import runpy

DEFAULT_URL = "https://www.youtube.com/watch?v=9svI65nxVhc"


def normalize_stream_url(url):
    """Discord chỉ hiện nút Xem + chấm tím với link dạng youtube.com/watch?v=... hoặc twitch.tv/...
    Đổi youtu.be/ID (kể cả có ?si=...) thành dạng youtube.com/watch?v=ID."""
    url = str(url or "").strip()
    m = re.match(r"^https://(?:www\.|m\.)?youtu\.be/([A-Za-z0-9_-]{6,20})", url)
    if not m:
        m = re.match(r"^https://(?:www\.|m\.)?youtube\.com/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]{6,20})", url)
    return "https://www.youtube.com/watch?v=" + m.group(1) if m else url


def _cfg():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply():
    import discord

    logged = []

    def to_streaming(activity):
        cfg = _cfg()
        want = cfg.get("force_stream", True) or cfg.get("activity_type") == "streaming"
        if activity is None or not want or isinstance(activity, discord.Streaming):
            return activity
        if isinstance(activity, discord.CustomActivity):
            name = activity.state or activity.name or "KSBot"
        else:
            name = getattr(activity, "name", None) or "KSBot"
        if not logged:
            logged.append(1)
            print(f"[run_bot] Đã đổi status thành Streaming: {name!r}", flush=True)
        return discord.Streaming(name=str(name), url=normalize_stream_url(cfg.get("stream_url") or DEFAULT_URL))

    orig_presence = discord.Client.change_presence
    orig_init = discord.Client.__init__

    def streaming_on():
        cfg = _cfg()
        return cfg.get("force_stream", True) or cfg.get("activity_type") == "streaming"

    async def change_presence(self, *args, **kwargs):
        if "activity" in kwargs:
            kwargs["activity"] = to_streaming(kwargs["activity"])
            if kwargs["activity"] is not None and streaming_on() and kwargs.get("status") is not None \
                    and kwargs["status"] != discord.Status.invisible:
                kwargs["status"] = discord.Status.online
        elif args:
            args = (to_streaming(args[0]),) + tuple(args[1:])
        return await orig_presence(self, *args, **kwargs)

    def __init__(self, *args, **kwargs):
        if kwargs.get("activity") is not None:
            kwargs["activity"] = to_streaming(kwargs["activity"])
            if streaming_on() and kwargs.get("status") not in (None, discord.Status.invisible):
                kwargs["status"] = discord.Status.online
        orig_init(self, *args, **kwargs)

    discord.Client.change_presence = change_presence
    discord.Client.__init__ = __init__
    print("[run_bot] Đã bật vá status Streaming", flush=True)


if __name__ == "__main__":
    try:
        apply()
    except Exception as e:
        print(f"[run_bot] Không vá được status Streaming ({e!r}), chạy bot.py bình thường.", flush=True)
    runpy.run_path("bot.py", run_name="__main__")
