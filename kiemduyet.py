import discord
from discord.ext import commands
from discord import app_commands, ui
import time
import os
from datetime import datetime, timezone
import asyncio

WAIT_SECONDS = 30  
DATA_CHANNEL_ID = 1566780713058046094  # Kênh log cố định

KIEM_DUYET_ROLES = {}
VERIFY_ROLE_NAME = "Chờ Kiểm Duyệt" # Role tự động cách ly

class AdminDecisionView(ui.View):
    def __init__(self, member: discord.Member, reviewer_role_id: int = None):
        super().__init__(timeout=None)
        self.member = member
        self.reviewer_role_id = reviewer_role_id

    def check_permission(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        if user.guild_permissions.administrator:
            return True
        if self.reviewer_role_id:
            role = interaction.guild.get_role(self.reviewer_role_id)
            if role and role in user.roles:
                return True
        elif user.guild_permissions.manage_roles:
            return True
        return False

    @ui.button(label="Chấp nhận", style=discord.ButtonStyle.green, emoji="✅", custom_id="decision_accept")
    async def accept_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not self.check_permission(interaction):
            await interaction.response.send_message("❌ Bạn không có Role Kiểm Duyệt để thực hiện thao tác này!", ephemeral=True)
            return

        # Gỡ Role Chờ Kiểm Duyệt cách ly ra khỏi user
        guild = interaction.guild
        restricted_role = discord.utils.get(guild.roles, name=VERIFY_ROLE_NAME)
        if restricted_role and restricted_role in self.member.roles:
            try:
                await self.member.remove_roles(restricted_role)
            except Exception as e:
                print(f"Lỗi gỡ role cách ly: {e}")

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
        if not self.check_permission(interaction):
            await interaction.response.send_message("❌ Bạn không có Role Kiểm Duyệt!", ephemeral=True)
            return

        try:
            await self.member.ban(reason=f"Bị ban qua hệ thống kiểm duyệt bởi {interaction.user}")
            embed = discord.Embed(
                title="🔨 ĐÃ BAN THÀNH VIÊN",
                description=f"Thành viên {self.member.mention} đã bị BAN bởi {interaction.user.mention}.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể ban: {e}", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.edit(archived=True, locked=True)

    @ui.button(label="Kick", style=discord.ButtonStyle.blurple, emoji="👢", custom_id="decision_kick")
    async def kick_btn(self, interaction: discord.Interaction, button: ui.Button):
        if not self.check_permission(interaction):
            await interaction.response.send_message("❌ Bạn không có Role Kiểm Duyệt!", ephemeral=True)
            return

        try:
            await self.member.kick(reason=f"Bị kick qua hệ thống kiểm duyệt bởi {interaction.user}")
            embed = discord.Embed(
                title="👢 ĐÃ KICK THÀNH VIÊN",
                description=f"Thành viên {self.member.mention} đã bị KICK bởi {interaction.user.mention}.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"❌ Không thể kick: {e}", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

        if isinstance(interaction.channel, discord.Thread):
            await interaction.channel.edit(archived=True, locked=True)


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
        await process_member_verification(interaction.user, interaction.channel, interaction)


# Hàm tự động tạo và cấu hình Role cách ly nếu chưa có
async def get_or_create_restricted_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name=VERIFY_ROLE_NAME)
    if not role:
        try:
            role = await guild.create_role(
                name=VERIFY_ROLE_NAME,
                color=discord.Color.dark_gray(),
                reason="Role tự động cách ly kiểm duyệt"
            )
            print(f"⚙️ [AUTO ROLE] Đã tự động khởi tạo Role: '{VERIFY_ROLE_NAME}' cho {guild.name}")
        except Exception as e:
            print(f"Lỗi tạo role tự động: {e}")
    return role


# Hàm xử lý chung tự động Duyệt đơn + Nhét Role + Phân luồng kiểm duyệt
async def process_member_verification(user: discord.Member, channel: discord.abc.GuildChannel, interaction: discord.Interaction = None):
    guild = user.guild
    
    if guild.id not in KIEM_DUYET_ROLES:
        return

    # 1. TỰ ĐỘNG CHẤP THUẬN ĐƠN DÀNH CHO CÁC MEMBER ĐANG PENDING
    if user.pending:
        try:
            await user.edit(pending=False, reason="Bot tự động duyệt đơn để đưa vào kiểm duyệt")
            print(f"✅ [AUTO APPROVE] Đã tự động chấp thuận đơn đăng ký của {user.name}")
        except Exception as e:
            print(f"Lỗi tự động duyệt đơn: {e}")

    # 2. TỰ ĐỘNG GÁN ROLE CÁCH LY
    restricted_role = await get_or_create_restricted_role(guild)
    if restricted_role and restricted_role not in user.roles:
        try:
            await user.add_roles(restricted_role, reason="Gán role cách ly kiểm duyệt")
        except Exception as e:
            print(f"Lỗi gán role cách ly: {e}")

    now = datetime.now(timezone.utc)
    account_age_days = (now - user.created_at).days
    account_age_months = account_age_days / 30.0

    data_channel = guild.get_channel(DATA_CHANNEL_ID)

    if interaction and not interaction.response.is_done():
        embed_completed = discord.Embed(
            title="✅ HOÀN THÀNH XÁC NHẬN",
            description=f"Cảm ơn {user.mention} đã hoàn tất! Bạn đã được đưa vào hệ thống kiểm duyệt.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed_completed, ephemeral=True)

    if data_channel:
        log_detail = (
            f"📋 **[LOG ĐÃ CHẤP THUẬN ĐƠN & GÁN ROLE CÁCH LY]**\n"
            f"• **Thành viên:** {user} (`{user.id}`)\n"
            f"• **Server:** {guild.name} (`{guild.id}`)\n"
            f"• **Tuổi tài khoản:** {int(account_age_months)} tháng\n"
            f"• **Thời gian:** <t:{int(time.time())}:F>"
        )
        await data_channel.send(log_detail)

    # Phân luồng <= 5 tháng (Check Voice)
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
            await data_channel.send(content=f"🔔 **[CẦN VOICE CHECK]** Thành viên {user.mention} tại `{guild.name}`.")
        return

    # Tạo Thread riêng (TÀI KHOẢN >= 7 THÁNG)
    thread_name = f"kiem-duyet-{user.name}"
    
    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=f"Kiểm duyệt tự động tài khoản {user.name}"
        )
        await thread.add_user(user)

        embed_thread_msg = discord.Embed(
            title="🛡️ PHÒNG KIỂM DUYỆT TÀI KHOẢN",
            description=(
                f"Chào {user.mention}!\n"
                f"Tài khoản của bạn có tuổi đời trên **7 tháng** ({int(account_age_months)} tháng).\n\n"
                "📸 **Yêu cầu:** Vui lòng **gửi ảnh chụp màn hình danh sách server** bạn đang tham gia vào đây.\n\n"
                "👉 **HƯỚNG DẪN:**\n"
                "• Nếu đã chụp đủ, hãy gõ lệnh **`.check`** hoặc **`/check`** để gọi Mod vào kiểm tra.\n"
                "• Hoặc chờ **10 phút**, hệ thống sẽ tự động gọi Ban kiểm duyệt."
            ),
            color=discord.Color.blue()
        )
        await thread.send(content=f"{user.mention} Vui lòng gửi ảnh chụp màn hình vào đây!", embed=embed_thread_msg)

        async def auto_ping_mod():
            await asyncio.sleep(600)
            try:
                role_id = KIEM_DUYET_ROLES.get(guild.id)
                ping_content = f"<@&{role_id}>" if role_id else "@here"
                
                decision_view = AdminDecisionView(user, reviewer_role_id=role_id)
                await thread.send(
                    content=f"⚠️ **[TỰ ĐỘNG BÁO CÁO SAU 10 PHÚT]** {ping_content} - Thành viên {user.mention} đang chờ kiểm duyệt danh sách server!", 
                    view=decision_view
                )
            except Exception:
                pass

        asyncio.create_task(auto_ping_mod())

    except Exception as e:
        print(f"Lỗi tạo thread: {e}")


class KiemDuyetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rule_image_path = "ruleks.png"

    async def cog_load(self):
        self.bot.add_view(RuleVerifyView())

    @commands.Cog.listener()
    async def on_ready(self):
        print("🔍 [BOT STARTUP] Khôi phục cài đặt và duyệt đơn tự động...")
        
        data_channel = self.bot.get_channel(DATA_CHANNEL_ID)
        if data_channel:
            try:
                async for msg in data_channel.history(limit=200):
                    if "⚙️ **[SETUP ROLE KIỂM DUYỆT]**" in msg.content:
                        lines = msg.content.split("\n")
                        guild_id = None
                        role_id = None
                        for line in lines:
                            if "• **Server:**" in line and "`" in line:
                                guild_id = int(line.split("`")[1])
                            if "• **Role được chọn:**" in line and "`" in line:
                                role_id = int(line.split("`")[1])
                        if guild_id and role_id:
                            KIEM_DUYET_ROLES[guild_id] = role_id
            except Exception as e:
                print(f"Lỗi cào dữ liệu: {e}")

        for guild_id, role_id in KIEM_DUYET_ROLES.items():
            guild = self.bot.get_guild(guild_id)
            if guild:
                try:
                    target_channel = guild.system_channel or guild.text_channels[0]
                    async for member in guild.fetch_members(limit=None):
                        # Quét nếu còn đơn chưa duyệt -> Tự động duyệt và gắn Role cách ly ngay
                        if member.pending:
                            print(f"📌 [Tự động duyệt đơn] {member.name} ({member.id}) tại {guild.name}")
                            await process_member_verification(member, target_channel)
                except Exception as e:
                    print(f"Lỗi duyệt đơn tại {guild.name}: {e}")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.pending and not after.pending:
            if after.guild.id in KIEM_DUYET_ROLES:
                system_channel = after.guild.system_channel or after.guild.text_channels[0]
                if system_channel:
                    await process_member_verification(after, system_channel)

    @app_commands.command(name="setup_role_kiemduyet", description="Chọn Role được phép bấm nút duyệt/ban/kick thành viên (Admin)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="Role của Ban kiểm duyệt / Moderator")
    async def setup_role_kiemduyet(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        KIEM_DUYET_ROLES[guild.id] = role.id
        data_channel = self.bot.get_channel(DATA_CHANNEL_ID)

        # Tự động tạo trước Role cách ly
        await get_or_create_restricted_role(guild)

        if data_channel:
            log_msg = (
                f"⚙️ **[SETUP ROLE KIỂM DUYỆT]**\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Role được chọn:** {role.mention} (`{role.id}`)\n"
                f"• **Người cài đặt:** {interaction.user.mention}\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(log_msg)

        await interaction.followup.send(f"✅ Đã bật hệ thống kiểm duyệt tự động & khởi tạo Role cách ly `{VERIFY_ROLE_NAME}` cho server!", ephemeral=True)

    @app_commands.command(name="setup_rule", description="Thiết lập hệ thống kiểm duyệt thủ công (Admin)")
    @app_commands.default_permissions(administrator=True)
    async def setup_rule(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not os.path.exists(self.rule_image_path):
            await interaction.followup.send(f"❌ Không tìm thấy file ảnh `{self.rule_image_path}`!", ephemeral=True)
            return

        guild = interaction.guild
        channel = interaction.channel
        data_channel = self.bot.get_channel(DATA_CHANNEL_ID)

        file = discord.File(self.rule_image_path, filename="ruleks.png")
        await channel.send(file=file, view=RuleVerifyView())

        if data_channel:
            setup_log = (
                f"⚙️ **[SETUP HỆ THỐNG KIỂM DUYỆT]**\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Kênh thực hiện:** {channel.mention} (`{channel.id}`)\n"
                f"• **Người thực hiện:** {interaction.user} (`{interaction.user.id}`)\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(setup_log)

        await interaction.followup.send(f"✅ Đã thiết lập xong!", ephemeral=True)

    @app_commands.command(name="check", description="Gọi ban kiểm duyệt vào thread hiện tại để duyệt tài khoản")
    async def check_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("❌ Lệnh này chỉ dùng được bên trong phòng Thread kiểm duyệt!", ephemeral=True)
            return

        guild = interaction.guild
        data_channel = self.bot.get_channel(DATA_CHANNEL_ID)
        role_id = KIEM_DUYET_ROLES.get(guild.id)
        ping_content = f"<@&{role_id}>" if role_id else "@here"

        embed = discord.Embed(
            title="🔔 CÓ YÊU CẦU KIỂM DUYỆT MỚI",
            description=f"Thành viên {interaction.user.mention} đã gửi ảnh và yêu cầu kiểm tra. Mời Ban kiểm duyệt vào xem xét!",
            color=discord.Color.gold()
        )
        
        decision_view = AdminDecisionView(interaction.user, reviewer_role_id=role_id)
        await interaction.channel.send(content=f"🛡️ **[YÊU CẦU DUYỆT]** {ping_content}", embed=embed, view=decision_view)

        if data_channel:
            check_log = (
                f"🔍 **[LOG GỌI CHECK]**\n"
                f"• **Thành viên gọi:** {interaction.user} (`{interaction.user.id}`)\n"
                f"• **Server:** {guild.name} (`{guild.id}`)\n"
                f"• **Thời gian:** <t:{int(time.time())}:F>"
            )
            await data_channel.send(check_log)

        await interaction.followup.send("✅ Đã gửi tín hiệu gọi ban kiểm duyệt thành công!", ephemeral=True)

    @commands.command(name="check")
    async def check_text_command(self, ctx):
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send("❌ Lệnh này chỉ dùng được trong phòng Thread kiểm duyệt!")
            return
        
        guild = ctx.guild
        data_channel = self.bot.get_channel(DATA_CHANNEL_ID)
        role_id = KIEM_DUYET_ROLES.get(guild.id)
        ping_content = f"<@&{role_id}>" if role_id else "@here"

        embed = discord.Embed(
            title="🔔 CÓ YÊU CẦU KIỂM DUYỆT MỚI",
            description=f"Thành viên {ctx.author.mention} đã yêu cầu kiểm tra. Mời Ban kiểm duyệt vào xử lý!",
            color=discord.Color.gold()
        )
        decision_view = AdminDecisionView(ctx.author, reviewer_role_id=role_id)
        await ctx.send(content=f"🛡️ **[YÊU CẦU DUYỆT]** {ping_content}", embed=embed, view=decision_view)

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
