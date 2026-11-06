import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os

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
        # Lấy thời gian tạo tin nhắn luật và thời gian hiện tại để tính toán
        msg_created_at = interaction.message.created_at.timestamp()
        current_time = time.time()
        elapsed_time = current_time - msg_created_at
        wait_time = 30  # Yêu cầu đọc ít nhất 30 giây

        # Nếu người dùng bấm quá nhanh (chưa đủ 30 giây)
        if elapsed_time < wait_time:
            remaining = int(wait_time - elapsed_time)
            embed_warning = discord.Embed(
                title="⚠️ CHƯA ĐỦ THỜI GIAN ĐỌC!",
                description=f"Vui lòng đọc kỹ nội quy server trên ảnh trước khi bấm!\nBạn cần đọc thêm ít nhất **{remaining} giây** nữa mới được xác nhận.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed_warning, ephemeral=True)
            return

        # Nếu đã đọc đủ thời gian -> Xác nhận thành công
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

    @app_commands.command(name="setup_rule", description="Gửi bảng nội quy kèm nút xác thực 30 giây (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def setup_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục bot!", ephemeral=True)
            return

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        
        embed = discord.Embed(
            title="📜 NỘI QUY MÁY CHỦ 💤KS",
            description="Vui lòng dành ít nhất **30 giây** để đọc kỹ toàn bộ nội quy trên hình ảnh trước khi nhấn nút **Đã đọc & Đồng ý** ở phía dưới.",
            color=discord.Color.from_rgb(15, 17, 21)
        )
        embed.set_image(url="attachment://ruleks.png")
        embed.set_footer(text="Hệ thống kiểm duyệt tự động 💤KS")

        view = RuleVerifyView()
        
        # Gửi ảnh kèm embed và view vào kênh hiện tại
        await interaction.channel.send(file=file, embed=embed, view=view)
        await interaction.followup.send("✅ Đã gửi bảng nội quy thành công!", ephemeral=True)

    @app_commands.command(name="test_rule", description="Test thử giao diện và bộ đếm 30 giây xác nhận luật")
    async def test_rule(self, interaction: discord.Interaction):  # <-- đã thêm self
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục bot!", ephemeral=True)
            return

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        
        embed = discord.Embed(
            title="🧪 [TESTMODE] BẢNG NỘI QUY THỬ NHIỆM",
            description="Bản test này yêu cầu phải đợi đủ **30 giây** mới được phép bấm nút xác nhận!",
            color=discord.Color.gold()
        )
        embed.set_image(url="attachment://ruleks.png")

        view = RuleVerifyView()
        
        await interaction.channel.send(file=file, embed=embed, view=view)
        await interaction.followup.send("✅ Đã tạo bảng luật test thành công!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(KiemDuyetCog(bot))
