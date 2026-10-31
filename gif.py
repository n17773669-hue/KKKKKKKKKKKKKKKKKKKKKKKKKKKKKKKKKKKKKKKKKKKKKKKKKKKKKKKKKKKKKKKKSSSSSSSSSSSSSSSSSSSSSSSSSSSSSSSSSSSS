"""
gifcog.py - Extension cho ksbot: video / link Pinterest / video trong thư mục -> GIF -> gửi về .zip

Lệnh:
  /gif        file:<video> hoặc link:<link pinterest/video>   size:<5|10|25 MB>  giay:<1-20>
  /gif_folder ten:<chọn tên video trong thư mục>              size:<5|10|25 MB>  giay:<1-20>

Không lưu gì lâu dài: file tạm nằm trong thư mục tạm và bị xóa ngay khi xong (kể cả khi lỗi).
Video gốc trong thư mục KHÔNG bị xóa hay sửa (chỉ đọc).

Cần: ffmpeg (cài trong Dockerfile), pip: yt-dlp, aiohttp
"""
import asyncio
import io
import os
import re
import html
import sys
import tempfile
import zipfile

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# Thư mục chứa video để đổi (đổi bằng biến môi trường GIF_VIDEO_DIR nếu muốn)
VIDEO_DIR = os.environ.get("GIF_VIDEO_DIR", "videos")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif")

MAX_INPUT_MB = 100          # giới hạn file nguồn (bảo vệ RAM/đĩa của Render)
MAX_SECONDS_CAP = 20        # tối đa số giây video được đổi
CONVERT_TIMEOUT = 150       # giây cho mỗi lần ffmpeg
DOWNLOAD_TIMEOUT = 90
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Thang chất lượng: (fps, chiều rộng) - thử từ đẹp nhất, nặng quá thì hạ dần
LADDER = [(15, 480), (12, 420), (10, 360), (8, 320), (6, 260), (5, 200), (4, 160)]

SIZE_CHOICES = [
    app_commands.Choice(name="Nhỏ ~5 MB (gửi nhanh, chất lượng thấp)", value=5),
    app_commands.Choice(name="Vừa ~10 MB (mặc định)", value=10),
    app_commands.Choice(name="Lớn ~25 MB (cần server boost)", value=25),
]

# Mỗi lần chỉ xử lý 1 yêu cầu để không tràn RAM trên host free
_sem = asyncio.Semaphore(1)


async def _run(*args, timeout):
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode


async def _download_direct(session, url, dest):
    async with session.get(url, headers={"User-Agent": UA}) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        size = 0
        with open(dest, "wb") as f:
            async for chunk in r.content.iter_chunked(1 << 16):
                size += len(chunk)
                if size > MAX_INPUT_MB * 1024 * 1024:
                    raise RuntimeError("File nguồn quá lớn")
                f.write(chunk)


async def _fetch_from_link(url, tmp):
    """Trả về đường dẫn file video tải được từ link."""
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # giải link rút gọn pin.it
        if "pin.it" in url:
            async with session.get(url, headers={"User-Agent": UA}, allow_redirects=True) as r:
                url = str(r.url)

        # link trỏ thẳng tới file media
        if re.search(r"\.(mp4|webm|mov|gif)(\?|$)", url, re.I):
            dest = os.path.join(tmp, "in" + os.path.splitext(url.split("?")[0])[1])
            await _download_direct(session, url, dest)
            return dest

        # thử yt-dlp trước (Pinterest video pin, TikTok, YouTube...)
        out_tpl = os.path.join(tmp, "in.%(ext)s")
        try:
            await _run(
                sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "-q",
                "--max-filesize", f"{MAX_INPUT_MB}M", "-o", out_tpl, url,
                timeout=DOWNLOAD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            pass
        for name in os.listdir(tmp):
            if name.startswith("in.") and not name.endswith((".part", ".ytdl")):
                return os.path.join(tmp, name)

        # dự phòng: đọc og:video / og:image trong trang
        async with session.get(url, headers={"User-Agent": UA}) as r:
            page = await r.text()
        for prop in ("og:video", "og:video:url", "og:image"):
            m = re.search(
                rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)', page, re.I
            )
            if m:
                media = html.unescape(m.group(1))
                ext = os.path.splitext(media.split("?")[0])[1] or ".mp4"
                if ext.lower() in (".jpg", ".jpeg", ".png", ".webp") and prop == "og:image":
                    raise RuntimeError("Pin này là ảnh tĩnh, không có video/gif để đổi.")
                dest = os.path.join(tmp, "in" + ext)
                await _download_direct(session, media, dest)
                return dest
    raise RuntimeError("Không lấy được video từ link này.")


async def _to_gif(src, out, fps, width, seconds):
    vf = (
        f"fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    rc = await _run(
        "ffmpeg", "-y", "-v", "error", "-t", str(seconds), "-i", src,
        "-an", "-vf", vf, "-loop", "0", out,
        timeout=CONVERT_TIMEOUT,
    )
    return rc == 0 and os.path.exists(out)


def _resolve_folder_video(name):
    """Chỉ cho phép file nằm TRONG VIDEO_DIR (chặn kiểu ../../)."""
    base = os.path.realpath(VIDEO_DIR)
    path = os.path.realpath(os.path.join(base, os.path.basename(name)))
    if os.path.dirname(path) != base or not os.path.isfile(path):
        return None
    if not path.lower().endswith(VIDEO_EXTS):
        return None
    return path


class GifCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        os.makedirs(VIDEO_DIR, exist_ok=True)

    # ---------- phần dùng chung: đổi -> zip -> gửi -> dọn ----------
    async def _convert_and_send(self, interaction, get_src, size, giay):
        """get_src(tmp) -> đường dẫn video nguồn (có thể nằm ngoài tmp, sẽ không bị xóa)."""
        limit_mb = (interaction.guild.filesize_limit / 1024 / 1024) if interaction.guild else 10
        target_mb = min(size.value if size else 10, limit_mb)
        target_bytes = int(target_mb * 1024 * 1024 * 0.95)  # chừa chỗ cho zip

        async with _sem:
            # thư mục tạm: tự xóa hết khi thoát khối with, kể cả lỗi
            with tempfile.TemporaryDirectory(prefix="gifbot_") as tmp:
                try:
                    src = await get_src(tmp)
                    out = os.path.join(tmp, "out.gif")
                    ok = False
                    for fps, width in LADDER:
                        if not await _to_gif(src, out, fps, width, giay):
                            continue
                        if os.path.getsize(out) <= target_bytes:
                            ok = True
                            break
                    if not ok:
                        return await interaction.followup.send(
                            f"Không nén nổi dưới {target_mb:g} MB. Thử giảm số giây hoặc chọn mức lớn hơn."
                        )

                    gif_size = os.path.getsize(out) / 1024 / 1024
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        z.write(out, "animation.gif")
                    buf.seek(0)
                    if buf.getbuffer().nbytes > limit_mb * 1024 * 1024:
                        return await interaction.followup.send("File zip vượt giới hạn upload của server.")

                    await interaction.followup.send(
                        f"Xong! GIF ~{gif_size:.1f} MB ({fps} fps, rộng {width}px)",
                        file=discord.File(buf, filename="gif.zip"),
                    )
                except asyncio.TimeoutError:
                    await interaction.followup.send("Xử lý quá lâu, thử video ngắn hơn nhé.")
                except Exception as e:
                    await interaction.followup.send(f"Lỗi: {e}")
            # tới đây thư mục tạm đã bị xóa sạch

    # ---------- /gif: file đính kèm hoặc link ----------
    @app_commands.command(name="gif", description="Đổi video hoặc link Pinterest thành GIF (gửi về file zip)")
    @app_commands.describe(
        file="Video tải lên",
        link="Link Pinterest / link video",
        size="Dung lượng GIF tối đa",
        giay="Số giây tối đa lấy từ video (mặc định 10)",
    )
    @app_commands.choices(size=SIZE_CHOICES)
    async def gif(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment = None,
        link: str = None,
        size: app_commands.Choice[int] = None,
        giay: app_commands.Range[int, 1, MAX_SECONDS_CAP] = 10,
    ):
        if not file and not link:
            return await interaction.response.send_message(
                "Gửi kèm **file** video hoặc **link** nhé. (Video có sẵn trong thư mục: dùng `/gif_folder`)",
                ephemeral=True,
            )
        if file and file.size > MAX_INPUT_MB * 1024 * 1024:
            return await interaction.response.send_message(
                f"File lớn quá (tối đa {MAX_INPUT_MB} MB).", ephemeral=True
            )

        await interaction.response.defer(thinking=True)

        async def get_src(tmp):
            if file:
                src = os.path.join(tmp, "in" + (os.path.splitext(file.filename)[1] or ".mp4"))
                await file.save(src)
                return src
            return await _fetch_from_link(link.strip(), tmp)

        await self._convert_and_send(interaction, get_src, size, giay)

    # ---------- /gif_folder: video có sẵn trong thư mục ----------
    async def _folder_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            names = sorted(os.listdir(VIDEO_DIR))
        except OSError:
            return []
        cur = current.lower()
        return [
            app_commands.Choice(name=n, value=n)
            for n in names
            if n.lower().endswith(VIDEO_EXTS) and cur in n.lower() and len(n) <= 100
        ][:25]

    @app_commands.command(name="gif_folder", description="Đổi video có sẵn trong thư mục của bot thành GIF (gửi về file zip)")
    @app_commands.describe(
        ten="Chọn video trong thư mục",
        size="Dung lượng GIF tối đa",
        giay="Số giây tối đa lấy từ video (mặc định 10)",
    )
    @app_commands.choices(size=SIZE_CHOICES)
    @app_commands.autocomplete(ten=_folder_autocomplete)
    async def gif_folder(
        self,
        interaction: discord.Interaction,
        ten: str,
        size: app_commands.Choice[int] = None,
        giay: app_commands.Range[int, 1, MAX_SECONDS_CAP] = 10,
    ):
        path = _resolve_folder_video(ten)
        if not path:
            return await interaction.response.send_message(
                f"Không thấy video **{ten}** trong thư mục `{VIDEO_DIR}`.", ephemeral=True
            )
        if os.path.getsize(path) > MAX_INPUT_MB * 1024 * 1024:
            return await interaction.response.send_message(
                f"File lớn quá (tối đa {MAX_INPUT_MB} MB).", ephemeral=True
            )

        await interaction.response.defer(thinking=True)

        async def get_src(tmp):
            return path  # đọc trực tiếp, không copy, không xóa file gốc

        await self._convert_and_send(interaction, get_src, size, giay)


async def setup(bot):
    await bot.add_cog(GifCog(bot))
