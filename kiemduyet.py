import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os

WAIT_SECONDS = 30  # Thời gian tối thiểu phải đợi mới bấm được (không công khai cho thành viên biết)


class RuleVerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Timeout=None giúp nút hoạt động vĩnh viễn kể cả khi restart bot

    @ui.button(
        label="Đã đọc & Đồng ý Nội quy", 
        style=discord.ButtonStyle.green, 
        emoji="✅", 
        custom_id="rule_verify_accept_button_persistent"
    )
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        # Tính thời gian từ lúc tin nhắn luật được gửi đến lúc bấm
        msg_created_at = interaction.message.created_at.timestamp()
        elapsed_time = time.time() - msg_created_at

        # Bấm quá nhanh: chỉ nhắc đọc kỹ, KHÔNG nói con số giây để người ta khỏi canh giờ
        if elapsed_time < WAIT_SECONDS:
            embed_warning = discord.Embed(
                title="⚠️ BẠN CHƯA ĐỌC KỸ NỘI QUY!",
                description="Vui lòng đọc kỹ toàn bộ nội quy trên ảnh rồi bấm lại nhé.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed_warning, ephemeral=True)
            return

        # Đã đủ thời gian -> Xác nhận thành công
        embed_success = discord.Embed(
            title="🎉 XÁC NHẬN THÀNH CÔNG",
            description="Cảm ơn bạn đã đọc và đồng ý với nội quy của **Server 💤KS**.\nHệ thống đang chuẩn bị bước kiểm duyệt tiếp theo cho bạn!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed_success, ephemeral=True)


# Đổi tên từ NoiTuCog -> KiemDuyetCog để không trùng tên với cog trong noitu.py
class KiemDuyetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rule_image_path = "ruleks.png"  # Tên file ảnh nội quy trong thư mục dự án

    # Đăng ký persistent view khi Cog được load để nút không bị lỗi hỏng khi khởi động lại bot
    async def cog_load(self):
        self.bot.add_view(RuleVerifyView())

    async def _send_rule(self, interaction: discord.Interaction):
        """Chỉ gửi ảnh nội quy + nút, không embed, không chữ."""
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


async def setup(bot):
    await bot.add_cog(KiemDuyetCog(bot))
