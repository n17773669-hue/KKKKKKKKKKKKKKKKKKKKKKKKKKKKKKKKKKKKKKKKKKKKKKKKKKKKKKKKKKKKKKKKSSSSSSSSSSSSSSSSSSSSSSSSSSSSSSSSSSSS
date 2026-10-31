import re
import datetime
import discord
from discord.ext import commands
 
# ================================================================
# LỆNH MUTE/UNMUTE RIÊNG — CHỈ CHỦ BOT (ID DƯỚI ĐÂY) DÙNG ĐƯỢC
#
# File này ĐỘC LẬP hoàn toàn, KHÔNG cần sửa gì bên bot.py.
# Dùng tiền tố RIÊNG "k!" (khác tiền tố "." của các lệnh khác), nên mình
# tự bắt tin nhắn qua on_message thay vì dùng hệ thống command_prefix
# chung của bot — không đụng/ảnh hưởng tới bất kỳ lệnh nào khác.
#
# CÁCH DÙNG:
#   k!mute @người                -> mute TỐI ĐA có thể (28 ngày — giới hạn
#                                    cứng của Discord, không thể vượt qua)
#   k!mute @người 10m            -> mute 10 phút
#   k!mute @người 2h             -> mute 2 giờ
#   k!mute @người 5d             -> mute 5 ngày
#   k!mute @người 999d           -> tự động giới hạn lại về 28 ngày (mức tối đa)
#   k!unmute @người               -> gỡ mute
#
# TÍNH NĂNG KHÓA MUTE:
#   Mute do lệnh k!mute tạo ra sẽ được "khóa" — nếu có AI/BOT KHÁC gỡ mute
#   (kể cả admin dùng tay trên Discord, hoặc bot kiểm duyệt khác), bot sẽ
#   tự động phát hiện qua on_member_update và MUTE LẠI NGAY LẬP TỨC, kèm
#   cảnh báo gửi vào kênh log. CHỈ `k!unmute` từ đúng chủ bot mới gỡ được
#   thật sự. Lưu ý: khóa này chỉ tồn tại trong bộ nhớ (RAM) của tiến trình
#   bot đang chạy — nếu bot restart, các khóa cũ sẽ mất (mute vẫn còn hiệu
#   lực bên Discord tới hạn đã đặt, chỉ là không còn được tự động khôi phục
#   khi bị gỡ trong lúc bot restart).
#
# LƯU Ý QUAN TRỌNG:
#   Discord (không phải do bot) giới hạn tính năng "timeout" (mute) tối đa
#   28 ngày kể từ lúc mute — đây là giới hạn kỹ thuật từ chính Discord,
#   KHÔNG có cách nào để mute lâu hơn bằng tính năng timeout gốc.
#   Muốn "mute vĩnh viễn" thật sự thì phải dùng role riêng (chặn quyền
#   nhắn tin) thay vì timeout — nếu cần mình có thể làm thêm bản đó.
# ================================================================
 
# --- ID được phép dùng lệnh mute/unmute này ---
ALLOWED_MUTE_IDS = {
    1404399385965498479,   # ID của bạn
}
 
MAX_TIMEOUT_DAYS = 28  # giới hạn cứng của Discord — không thể chỉnh số này lớn hơn được
 
 
def _parse_duration(raw: str):
    """Trả về (timedelta, bị_giới_hạn: bool) hoặc None nếu không hợp lệ.
    Hỗ trợ: 30s, 10m, 2h, 5d, hoặc 'max' để lấy luôn mức tối đa."""
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in ("max", "toida", "toiđa", "max out"):
        return datetime.timedelta(days=MAX_TIMEOUT_DAYS), False
 
    m = re.fullmatch(r"(\d+)\s*(s|m|h|d)?", raw)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2) or "m"  # mặc định phút nếu không ghi đơn vị
    seconds_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    total_seconds = value * seconds_map[unit]
    if total_seconds <= 0:
        return None
 
    max_seconds = MAX_TIMEOUT_DAYS * 86400
    capped = False
    if total_seconds > max_seconds:
        total_seconds = max_seconds
        capped = True
    return datetime.timedelta(seconds=total_seconds), capped
 
 
class OwnerMuteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, member_id) -> datetime hết hạn mute mà CHỦ BOT đã đặt.
        # Dùng để phát hiện + tự khôi phục nếu có ai/bot khác gỡ mute trái phép.
        self.locked_mutes: dict[tuple[int, int], datetime.datetime] = {}
 
    def _is_allowed(self, user_id: int) -> bool:
        return user_id in ALLOWED_MUTE_IDS
 
    async def _find_removal_actor(self, guild: discord.Guild, member_id: int):
        """Best-effort: tra Audit Log xem ai vừa gỡ/chỉnh timeout của member_id."""
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(
                limit=15, action=discord.AuditLogAction.member_update
            ):
                if getattr(entry.target, "id", None) != member_id:
                    continue
                before = getattr(entry, "before", None)
                after = getattr(entry, "after", None)
                if hasattr(before, "timed_out_until") or hasattr(after, "timed_out_until"):
                    return entry.user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None
 
    async def _get_log_channel(self, guild: discord.Guild):
        """Tìm 1 kênh text mà bot có thể gửi cảnh báo (ưu tiên system_channel)."""
        me = guild.me
        if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
            return guild.system_channel
        for ch in guild.text_channels:
            if ch.permissions_for(me).send_messages:
                return ch
        return None
 
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Chỉ quan tâm khi trạng thái timeout (mute) thay đổi.
        if before.timed_out_until == after.timed_out_until:
            return
 
        key = (after.guild.id, after.id)
        expire_at = self.locked_mutes.get(key)
        if expire_at is None:
            return  # người này không nằm trong danh sách mute do chủ bot khóa -> bỏ qua
 
        now = discord.utils.utcnow()
 
        if now >= expire_at:
            # Đã hết hạn tự nhiên -> dọn khỏi danh sách khóa, không cần làm gì thêm.
            self.locked_mutes.pop(key, None)
            return
 
        # Nếu timeout bị GỠ HẲN (None) hoặc bị RÚT NGẮN xuống dưới mốc đã khóa
        # trong khi lẽ ra vẫn còn hạn -> coi là gỡ trái phép -> mute lại ngay.
        still_muted_enough = (
            after.timed_out_until is not None and after.timed_out_until >= expire_at
        )
        if still_muted_enough:
            return  # bị gia hạn thêm hoặc giữ nguyên đủ hạn -> không cần can thiệp
 
        me = after.guild.me
        if me is None or not me.guild_permissions.moderate_members:
            return
 
        try:
            await after.timeout(expire_at, reason="Bot Guard: khôi phục mute bị gỡ trái phép")
        except (discord.Forbidden, discord.HTTPException):
            return
 
        actor = await self._find_removal_actor(after.guild, after.id)
        actor_text = f"**{actor}** (`{actor.id}`)" if actor else "không xác định được (thiếu quyền Audit Log hoặc log chưa cập nhật)"
 
        log_channel = await self._get_log_channel(after.guild)
        if log_channel:
            until_ts = int(expire_at.timestamp())
            try:
                await log_channel.send(
                    f"🔒 **PHÁT HIỆN GỠ MUTE TRÁI PHÉP**\n"
                    f"Thành viên: {after.mention} (`{after.id}`)\n"
                    f"Người/bot vừa gỡ: {actor_text}\n"
                    f"➡️ Đã tự động **mute lại** đến <t:{until_ts}:f> (<t:{until_ts}:R>) "
                    f"vì mute này chỉ chủ bot mới được gỡ bằng `k!unmute`."
                )
            except discord.HTTPException:
                pass
 
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
 
        content = message.content.strip()
        lower = content.lower()
 
        if not (lower.startswith("k!mute") or lower.startswith("k!unmute")):
            return  # không phải lệnh của mình -> bỏ qua ngay, không đụng gì khác
 
        if not self._is_allowed(message.author.id):
            return  # im lặng bỏ qua, không lộ cú pháp lệnh cho người không có quyền
 
        member = message.mentions[0] if message.mentions else None
 
        if lower.startswith("k!unmute"):
            await self._handle_unmute(message, member)
        else:
            # tách phần thời gian: bỏ "k!mute", bỏ phần mention (dạng <@id> hoặc <@!id>)
            rest = re.sub(r"k!mute", "", content, count=1, flags=re.IGNORECASE)
            rest = re.sub(r"<@!?\d+>", "", rest).strip()
            duration_raw = rest if rest else None
            await self._handle_mute(message, member, duration_raw)
 
    async def _handle_mute(self, message: discord.Message, member, duration_raw):
        guild = message.guild
        me = guild.me
 
        if member is None:
            return await message.channel.send(
                "❌ Cú pháp: `k!mute @người [thời gian]`\n"
                "VD: `k!mute @A` (mute tối đa 28 ngày), `k!mute @A 10m`, `k!mute @A 2h`.",
                delete_after=12,
            )
 
        if duration_raw is None:
            delta = datetime.timedelta(days=MAX_TIMEOUT_DAYS)
            capped_note = ""
        else:
            parsed = _parse_duration(duration_raw)
            if parsed is None:
                return await message.channel.send(
                    "❌ Thời gian không hợp lệ. VD: `10m`, `2h`, `5d`, hoặc `max`.",
                    delete_after=8,
                )
            delta, capped = parsed
            capped_note = (
                f"\n⚠️ Discord chỉ cho phép mute (timeout) tối đa **{MAX_TIMEOUT_DAYS} ngày** — "
                f"đã tự động giới hạn lại về mức này."
                if capped else ""
            )
 
        if not me.guild_permissions.moderate_members:
            return await message.channel.send("❌ Bot thiếu quyền **Moderate Members** nên không thể mute.")
 
        if member.id == me.id:
            return await message.channel.send("❌ Không thể tự mute chính bot.")
 
        if member.top_role >= me.top_role and guild.owner_id != me.id:
            return await message.channel.send("❌ Không thể mute người này (role của họ cao hơn hoặc bằng bot).")
 
        try:
            await member.timeout(delta, reason=f"Mute bởi chủ bot ({message.author})")
        except discord.Forbidden:
            return await message.channel.send("❌ Không có quyền mute người này.")
        except discord.HTTPException as e:
            return await message.channel.send(f"❌ Lỗi khi mute: {e}")
 
        expire_at = discord.utils.utcnow() + delta
        until_ts = int(expire_at.timestamp())
        # Khóa mute này lại -> nếu bị ai/bot khác gỡ trái phép, on_member_update
        # phía trên sẽ tự động mute lại ngay.
        self.locked_mutes[(guild.id, member.id)] = expire_at
        await message.channel.send(
            f"🔇 Đã mute {member.mention} đến <t:{until_ts}:f> (<t:{until_ts}:R>).{capped_note}\n"
            f"🔒 Mute này đã được KHÓA — chỉ `k!unmute` từ chủ bot mới gỡ được."
        )
 
    async def _handle_unmute(self, message: discord.Message, member):
        guild = message.guild
        me = guild.me
 
        if member is None:
            return await message.channel.send("❌ Cú pháp: `k!unmute @người`", delete_after=8)
 
        if not me.guild_permissions.moderate_members:
            return await message.channel.send("❌ Bot thiếu quyền **Moderate Members** nên không thể unmute.")
 
        # Gỡ khóa TRƯỚC khi unmute, để on_member_update không hiểu nhầm đây là
        # gỡ trái phép rồi tự mute lại ngay lập tức.
        self.locked_mutes.pop((guild.id, member.id), None)
 
        try:
            await member.timeout(None, reason=f"Unmute bởi chủ bot ({message.author})")
        except discord.Forbidden:
            return await message.channel.send("❌ Không có quyền unmute người này.")
        except discord.HTTPException as e:
            return await message.channel.send(f"❌ Lỗi khi unmute: {e}")
 
        await message.channel.send(f"🔊 Đã gỡ mute cho {member.mention}.")
 
 
async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerMuteCog(bot))
