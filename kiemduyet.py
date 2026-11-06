import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os
from datetime import datetime, timezone

WAIT_SECONDS = 30  # Thời gian tối thiểu phải đợi mới bấm được

class RuleVerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Timeout=None giữ nút vĩnh viễn không mất khi restart bot

    @ui.button(
        label="Đã đọc & Đồng ý Nội quy", 
        style=discord.ButtonStyle.green, 
        emoji="✅", 
        custom_id="rule_verify_accept_button_persistent"
    )
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        msg_created_at = interaction.message.created_at.timestamp()
        elapsed_time = time.time() - msg_created_at

        # Nếu bấm quá nhanh (chưa đủ 30 giây)
        if elapsed_time < WAIT_SECONDS:
            embed_warning = discord.Embed(
                title="⚠️ BẠN CHƯA ĐỌC KỸ NỘI QUY!",
                description="Vui lòng đọc kỹ toàn bộ nội quy trên ảnh rồi bấm lại nhé.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed_warning, ephemeral=True)
            return

        # Đã đủ thời gian -> Kiểm tra tuổi tài khoản
        user = interaction.user
        # Tính tuổi tài khoản tính theo tháng
        now = datetime.now(timezone.utc)
        account_age_days = (now - user.created_at).days
        account_age_months = account_age_days / 30.0

        guild = interaction.guild

        # --- TRƯỜNG HỢP 1: Tài khoản <= 5 tháng (Yêu cầu Voice check) ---
        if account_age_months <= 5:
            embed_voice = discord.Embed(
                title="🎙️ YÊU CẦU KIỂM DUYỆT QUA VOICE",
                description=(
                    f"Tài khoản của bạn dưới **5 tháng** tuổi ({int(account_age_months)} tháng).\n"
                    "Theo quy định chống gián điệp của server **💤KS**, bạn bắt buộc phải vào **Voice Channel kiểm duyệt** để check trực tiếp (Mở Mic & Cam).\n\n"
                    "Hệ thống đã gửi yêu cầu tới ban kiểm duyệt!"
                ),
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed_voice, ephemeral=True)
            
            # (Tùy chọn) Bạn có thể tạo kênh Voice tự động hoặc gán role ở đây nếu muốn
            return

        # --- TRƯỜNG HỢP 2: Tài khoản >= 7 tháng (Yêu cầu gửi ảnh chụp màn hình server) ---
        # Tạo một Thread (Chủ đề) riêng tư cho user này gửi ảnh cho Mod kiểm duyệt
        channel = interaction.channel
        thread_name = f"kiem-duyet-{user.name}"
        
        try:
            # Tạo thread riêng tại kênh chứa bảng luật
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.public_thread, # hoặc private_thread nếu server boost cấp độ 2
                reason=f"Kiểm duyệt tài khoản {user.name}"
            )
            
            # Thêm quyền hạn chỉ cho User đó và Mod nhắn tin (nếu dùng public thread thì dùng cơ chế ping/gửi tin nhắn hướng dẫn)
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

            embed_success = discord.Embed(
                title="🎉 XÁC NHẬN THÀNH CÔNG",
                description=f"Hệ thống đã tạo riêng cho bạn một chủ đề kiểm duyệt: {thread.mention}\nVui lòng truy cập vào đó để hoàn tất thủ tục!",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed_success, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f"❌ Có lỗi xảy ra khi tạo phòng kiểm duyệt riêng: `{e}`", ephemeral=True)


class KiemDuyetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rule_image_path = "ruleks.png"

    async def cog_load(self):
        self.bot.add_view(RuleVerifyView())

    async def _send_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục bot!", ephemeral=True)
            return

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        await interaction.channel.send(file=file, view=RuleVerifyView())
        await interaction.followup.send("✅ Đã gửi bảng nội quy thành công!", ephemeral=True)

    @app_commands.command(name="setup_rule", description="Gửi bảng nội quy kèm nút xác nhận (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def setup_rule(self, interaction: discord.Interaction):
        await self._send_rule(interaction)

    @app_commands.command(name="test_rule", description="Test thử bảng nội quy và nút xác nhận")
    async def test_rule(self, interaction: discord.Interaction):
        await self._send_rule(interaction)

    # Lệnh /check dùng cho Admin/Mod gọi thủ công khi cần kiểm duyệt ai đó
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
