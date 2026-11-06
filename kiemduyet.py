import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os
from datetime import datetime, timezone

WAIT_SECONDS = 30  # Thời gian tối thiểu phải đợi mới bấm được
DATA_CHANNEL_ID = 1566780713058046094  # ID kênh lưu trữ và quản lý dữ liệu kiểm duyệt

class RuleVerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Giữ nút vĩnh viễn không mất khi restart bot

    @ui.button(
        label="Đã đọc & Đồng ý Nội quy", 
        style=discord.ButtonStyle.green, 
        emoji="✅", 
        custom_id="rule_verify_accept_button_persistent"
    )
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        msg_created_at = interaction.message.created_at.timestamp()
        elapsed_time = time.time() - msg_created_at

        # Nếu bấm quá nhanh (chưa đủ thời gian đọc)
        if elapsed_time < WAIT_SECONDS:
            embed_warning = discord.Embed(
                title="⚠️ BẠN CHƯA ĐỌC KỸ NỘI QUY!",
                description="Vui lòng đọc kỹ toàn bộ nội quy trên ảnh rồi bấm lại nhé.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed_warning, ephemeral=True)
            return

        # 1. Xóa tin nhắn chứa ảnh nội quy ngay khi user đồng ý để kênh sạch sẽ
        try:
            await interaction.message.delete()
        except Exception:
            pass  # Phòng hờ bot thiếu quyền xóa tin nhắn

        user = interaction.user
        now = datetime.now(timezone.utc)
        account_age_days = (now - user.created_at).days
        account_age_months = account_age_days / 30.0

        guild = interaction.guild
        data_channel = guild.get_channel(DATA_CHANNEL_ID)

        # Thông báo trạng thái đã hoàn thành bước nội quy
        embed_completed = discord.Embed(
            title="✅ HOÀN THÀNH XÁC NHẬN NỘI QUY",
            description=f"Cảm ơn {user.mention} đã đọc và đồng ý với nội quy **Server 💤KS**!\nHệ thống đang tiến hành chuyển sang bước kiểm duyệt tiếp theo...",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed_completed, ephemeral=True)

        # --- PHÂN NHÁNH KIỂM DUYỆT THEO TUỔI TÀI KHOẢN ---
        
        # Trường hợp 1: Tài khoản <= 5 tháng (Yêu cầu Voice check)
        if account_age_months <= 5:
            embed_voice = discord.Embed(
                title="🎙️ YÊU CẦU KIỂM DUYỆT QUA VOICE",
                description=(
                    f"Tài khoản của bạn dưới **5 tháng** tuổi ({int(account_age_months)} tháng).\n"
                    "Theo quy định chống gián điệp của server, bạn bắt buộc phải vào **Voice Channel kiểm duyệt** để check trực tiếp (Mở Mic & Cam)."
                ),
                color=discord.Color.gold()
            )
            # Gửi thông báo chi tiết vào kênh dữ liệu quản lý (DATA_CHANNEL_ID) nếu có
            if data_channel:
                await data_channel.send(content=f"🔔 **[VOICE CHECK]** Cần kiểm duyệt thành viên {user.mention} (Acc mới {int(account_age_months)} tháng).", embed=embed_voice)
            return

        # Trường hợp 2: Tài khoản >= 7 tháng (Tạo Thread gửi ảnh chụp danh sách server)
        channel = interaction.channel
        thread_name = f"kiem-duyet-{user.name}"
        
        try:
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread,
                reason=f"Kiểm duyệt tài khoản {user.name}"
            )
            await thread.add_user(user)

            embed_thread_msg = discord.Embed(
                title="🛡️ PHÒNG KIỂM DUYỆT TÀI KHOẢN",
                description=(
                    f"Chào {user.mention}!\n"
                    f"Tài khoản của bạn có tuổi đời trên **7 tháng** ({int(account_age_months)} tháng).\n\n"
                    "📸 **Yêu cầu:** Vui lòng **gửi ảnh chụp màn hình (Screenshot) toàn bộ danh sách server** bạn đang tham gia vào đây để ban kiểm duyệt tiến hành check.\n\n"
                    "*(Chỉ bạn và Người kiểm duyệt mới trao đổi được trong chủ đề này)*"
                ),
                color=discord.Color.blue()
            )
            await thread.send(content=f"{user.mention}", embed=embed_thread_msg)

            # Ghi nhận log về kênh dữ liệu chỉ định
            if data_channel:
                log_embed = discord.Embed(
                    title="📂 [LOG KIỂM DUYỆT MỚI]",
                    description=f"Thành viên: {user.mention} đã hoàn tất nội quy và được tạo phòng check tại: {thread.mention}",
                    color=discord.Color.blue()
                )
                await data_channel.send(embed=log_embed)

        except Exception as e:
            print(f"Lỗi tạo phòng kiểm duyệt: {e}")


class KiemDuyetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rule_image_path = "ruleks.png"

    async def cog_load(self):
        self.bot.add_view(RuleVerifyView())
        
        # Khi bot khởi động/host lại, tự động quét dữ liệu từ kênh cấu hình
        @self.bot.event
        async def on_ready_scan():
            for guild in self.bot.guilds:
                data_ch = guild.get_channel(DATA_CHANNEL_ID)
                if data_ch:
                    print(f"✅ [DATA SYNC] Đã kết nối và quét dữ liệu thành công tại kênh quản lý của server: {guild.name}")

    @app_commands.command(name="setup_rule", description="Chọn kênh này làm điểm gửi bảng nội quy và quản lý kiểm duyệt (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def setup_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục bot!", ephemeral=True)
            return

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        
        # Gửi ảnh luật kèm nút bấm vào kênh được setup
        await interaction.channel.send(file=file, view=RuleVerifyView())
        await interaction.followup.send(f"✅ Đã thiết lập bảng nội quy thành công tại kênh này!\n📂 Dữ liệu liên kết kênh quản lý: `{DATA_CHANNEL_ID}`", ephemeral=True)

    @app_commands.command(name="test_rule", description="Test thử bảng nội quy và nút xác nhận")
    async def test_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục bot!", ephemeral=True)
            return

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        await interaction.channel.send(file=file, view=RuleVerifyView())
        await interaction.followup.send("✅ Đã tạo bảng luật test thành công!", ephemeral=True)

    # Lệnh /check hoặc .check để gọi phòng kiểm duyệt thủ công
    @app_commands.command(name="check", description="Tạo phòng chủ đề kiểm duyệt cho một thành viên (Staff)")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(member="Thành viên cần kiểm duyệt")
    async def check_member(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        thread = await channel.create_thread(
            name=f"kiem-duyet-{member.name}",
            type=discord.ChannelType.public_thread,
            reason=f"Kiểm duyệt thủ công bởi {interaction.user}"
        )
        await thread.add_user(member)

        embed = discord.Embed(
            title="🔍 YÊU CẦU KIỂM DUYỆT THỦ CÔNG",
            description=f"Xin chào {member.mention}, bạn đã được đưa vào danh sách kiểm duyệt thủ công bởi {interaction.user.mention}.\nVui lòng gửi thông tin/hình ảnh theo yêu cầu của Mod vào đây!",
            color=discord.Color.orange()
        )
        await thread.send(content=f"{member.mention} {interaction.user.mention}", embed=embed)
        await interaction.followup.send(f"✅ Đã tạo thành công phòng kiểm duyệt cho {member.mention} tại {thread.mention}!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(KiemDuyetCog(bot))
