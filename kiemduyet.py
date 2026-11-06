import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os
from datetime import datetime, timezone
import asyncio

WAIT_SECONDS = 30  # Thời gian tối thiểu phải đợi mới bấm được
DATA_CHANNEL_ID = 1566780713058046094  # ID kênh lưu trữ và quản lý dữ liệu chi tiết

# Bộ nhớ đệm lưu trữ cấu hình hệ thống (có thể khôi phục từ lịch sử kênh data)
BOT_CONFIGS = {}

# 1. Bảng nút bấm quyết định cho Admin/Mod (Chấp nhận / Ban / Kick)
class AdminDecisionView(ui.View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=None)
        self.member = member

    @ui.button(label="Chấp nhận", style=discord.ButtonStyle.green, emoji="✅", custom_id="decision_accept")
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ Bạn không có quyền sử dụng nút này!", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎉 KIỂM DUYỆT THÀNH CÔNG",
            description=f"Thành viên {self.member.mention} đã vượt qua vòng kiểm duyệt bởi {interaction.user.mention}!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.edit(archived=True, locked=True)

    @ui.button(label="Ban", style=discord.ButtonStyle.red, emoji="🔨", custom_id="decision_ban")
    async def ban_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.ban_members:
            await interaction.response.send_message("❌ Bạn không có quyền Ban thành viên!", ephemeral=True)
            return

        try:
            await self.member.ban(reason=f"Bị ban qua hệ thống kiểm duyệt bởi {interaction.user}")
            embed = discord.Embed(
                title="🔨 ĐÃ BAN THÀNH VIÊN",
                description=f"Thành viên {self.member.mention} đã bị BAN khỏi server bởi {interaction.user.mention}.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể ban thành viên này: {e}", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.edit(archived=True, locked=True)

    @ui.button(label="Kick", style=discord.ButtonStyle.blurple, emoji="👢", custom_id="decision_kick")
    async def kick_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message("❌ Bạn không có quyền Kick thành viên!", ephemeral=True)
            return

        try:
            await self.member.kick(reason=f"Bị kick qua hệ thống kiểm duyệt bởi {interaction.user}")
            embed = discord.Embed(
                title="👢 ĐÃ KICK THÀNH VIÊN",
                description=f"Thành viên {self.member.mention} đã bị KICK khỏi server bởi {interaction.user.mention}.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể kick thành viên này: {e}", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.edit(archived=True, locked=True)


# 2. View nút xác thực nội quy
class RuleVerifyView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="Đã đọc & Đồng ý Nội quy", 
        style=discord.ButtonStyle.green, 
        emoji="✅", 
        custom_id="rule_verify_accept_button_persistent"
    )
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        msg_created_at = interaction.message.created_at.timestamp()
        elapsed_time = time.time() - msg_created_at

        if elapsed_time < WAIT_SECONDS:
            embed_warning = discord.Embed(
                title="⚠️ BẠN CHƯA ĐỌC KỸ NỘI QUY!",
                description="Vui lòng đọc kỹ toàn bộ nội quy trên ảnh rồi bấm lại nhé.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed_warning, ephemeral=True)
            return

        try:
            await interaction.message.delete()
        except Exception:
            pass

        user = interaction.user
        guild = interaction.guild
        now = datetime.now(timezone.utc)
        account_age_days = (now - user.created_at).days
        account_age_months = account_age_days / 30.0

        data_channel = guild.get_channel(DATA_CHANNEL_ID)

        embed_completed = discord.Embed(
            title="✅ HOÀN THÀNH XÁC NHẬN NỘI QUY",
            description=f"Cảm ơn {user.mention} đã đồng ý với nội quy **Server 💤KS**!\nHệ thống đang tạo phòng kiểm duyệt riêng cho bạn...",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed_completed, ephemeral=True)

        # Lưu log chi tiết hành động đọc luật lên kênh dữ liệu
        if data_channel:
            log_detail = (
                f"📋 **[LOG ĐÃ ĐỌC LUẬT]**\n"
                f"• **Thành viên:** {user} (`{user.id}`)\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Tuổi tài khoản:** {int(account_age_months)} tháng\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(log_detail)

        # Phân nhánh theo tuổi tài khoản
        if account_age_months <= 5:
            embed_voice = discord.Embed(
                title="🎙️ YÊU CẦU KIỂM DUYỆT QUA VOICE",
                description=(
                    f"Tài khoản của bạn dưới **5 tháng** tuổi ({int(account_age_months)} tháng).\n"
                    "Vui lòng vào **Voice Channel kiểm duyệt** để check trực tiếp (Mở Mic & Cam)."
                ),
                color=discord.Color.gold()
            )
            if data_channel:
                await data_channel.send(content=f"🔔 **[CẦN VOICE CHECK]** Thành viên {user.mention} tại Server `{guild.name}`.")
            return

        # Tạo Thread riêng cho tài khoản >= 7 tháng
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
                    "📸 **Yêu cầu:** Vui lòng **gửi ảnh chụp màn hình danh sách server** bạn đang tham gia vào đây.\n\n"
                    "👉 **HƯỚNG DẪN:**\n"
                    "• Nếu đã chụp đủ server, hãy gõ lệnh **`.check`** hoặc **`/check`** để gọi Mod vào kiểm tra.\n"
                    "• Hoặc chờ **10 phút**, hệ thống sẽ tự động gọi Ban kiểm duyệt và hiển thị bảng lựa chọn xử lý."
                ),
                color=discord.Color.blue()
            )
            
            decision_view = AdminDecisionView(user)
            await thread.send(content=f"{user.mention} Vui lòng gửi ảnh chụp màn hình vào đây!", embed=embed_thread_msg, view=decision_view)

            # Task nền tự động sau 10 phút nếu chưa xử lý
            async def auto_ping_mod():
                await asyncio.sleep(600)
                try:
                    await thread.send(content=f"⚠️ **[TỰ ĐỘNG BÁO CÁO SAU 10 PHÚT]** Thành viên {user.mention} đang chờ kiểm duyệt danh sách server!", view=decision_view)
                except Exception:
                    pass

            asyncio.create_task(auto_ping_mod())

        except Exception as e:
            print(f"Lỗi tạo phòng kiểm duyệt: {e}")


class KiemDuyetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rule_image_path = "ruleks.png"

    async def cog_load(self):
        self.bot.add_view(RuleVerifyView())
        
        # Tự động quét lịch sử kênh dữ liệu để khôi phục cấu hình khi host lại bot
        @self.bot.event
        async def on_ready_recovery():
            for guild in self.bot.guilds:
                data_ch = guild.get_channel(DATA_CHANNEL_ID)
                if data_ch:
                    try:
                        async for message in data_ch.history(limit=50):
                            if "⚙️ **[SETUP KÊNH LUẬT]**" in message.content:
                                # Trích xuất thông tin cấu hình cũ từ lịch sử tin nhắn
                                pass
                    except Exception:
                        print("Không thể quét lịch sử kênh dữ liệu.")

    @app_commands.command(name="setup_rule", description="Chọn kênh này làm điểm gửi bảng nội quy và lưu log chi tiết (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def setup_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}` trong thư mục!", ephemeral=True)
            return

        guild = interaction.guild
        channel = interaction.channel
        data_channel = guild.get_channel(DATA_CHANNEL_ID)

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        await channel.send(file=file, view=RuleVerifyView())

        # Ghi log chi tiết việc dùng lệnh setup vào kênh dữ liệu cố định
        if data_channel:
            setup_log = (
                f"⚙️ **[SETUP KÊNH LUẬT]**\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Kênh thực hiện lệnh:** {channel.mention} (`{channel.id}`)\n"
                f"• **Người thực hiện:** {interaction.user} (`{interaction.user.id}`)\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(setup_log)

        await interaction.followup.send(f"✅ Đã thiết lập bảng nội quy thành công tại kênh này!\n📂 Mọi thông tin chi tiết đã được lưu về kênh quản lý ID: `{DATA_CHANNEL_ID}`", ephemeral=True)

    @app_commands.command(name="test_rule", description="Test thử bảng nội quy")
    async def test_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send("❌ Không tìm thấy file ảnh `ruleks.png`!", ephemeral=True)
            return
        file = discord.File(self.rule_image_path, filename="ruleks.png")
        await interaction.channel.send(file=file, view=RuleVerifyView())
        await interaction.followup.send("✅ Đã tạo bảng luật test!", ephemeral=True)

    # Lệnh /check (Slash Command) gọi ban kiểm duyệt
    @app_commands.command(name="check", description="Gọi ban kiểm duyệt vào thread hiện tại để duyệt tài khoản")
    async def check_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Lệnh này chỉ dùng được bên trong phòng chủ đề (Thread) kiểm duyệt!", ephemeral=True)
            return

        guild = interaction.guild
        data_channel = guild.get_channel(DATA_CHANNEL_ID)

        embed = discord.Embed(
            title="🔔 CÓ YÊU CẦU KIỂM DUYỆT MỚI",
            description=f"Thành viên {interaction.user.mention} đã gửi ảnh và yêu cầu kiểm tra danh sách server. Mời các Kiểm duyệt viên vào xem xét!",
            color=discord.Color.gold()
        )
        
        decision_view = AdminDecisionView(interaction.user)
        await interaction.channel.send(content="🛡️ **[YÊU CẦU DUYỆT]** @here", embed=embed, view=decision_view)

        # Lưu log chi tiết lệnh check
        if data_channel:
            check_log = (
                f"🔍 **[LOG GỌI CHECK]**\n"
                f"• **Thành viên gọi:** {interaction.user} (`{interaction.user.id}`)\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Thread:** {interaction.channel.name}\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(check_log)

        await interaction.followup.send("✅ Đã gửi tín hiệu gọi ban kiểm duyệt thành công!", ephemeral=True)

    # Lệnh text `.check` theo thói quen
    @commands.command(name="check")
    async def check_text_command(self, ctx):
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("❌ Lệnh này chỉ dùng được trong phòng Thread kiểm duyệt!")
            return
        
        guild = ctx.guild
        data_channel = guild.get_channel(DATA_CHANNEL_ID)

        embed = discord.Embed(
            title="🔔 CÓ YÊU CẦU KIỂM DUYỆT MỚI",
            description=f"Thành viên {ctx.author.mention} đã yêu cầu kiểm tra. Mời ban kiểm duyệt vào xử lý!",
            color=discord.Color.gold()
        )
        decision_view = AdminDecisionView(ctx.author)
        await ctx.send(content="🛡️ **[YÊU CẦU DUYỆT]** @here", embed=embed, view=decision_view)

        if data_channel:
            check_log = (
                f"🔍 **[LOG GỌI CHECK (.check)]**\n"
                f"• **Thành viên gọi:** {ctx.author} (`{ctx.author.id}`)\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(check_log)


async def setup(bot):
    await bot.add_cog(KiemDuyetCog(bot))
