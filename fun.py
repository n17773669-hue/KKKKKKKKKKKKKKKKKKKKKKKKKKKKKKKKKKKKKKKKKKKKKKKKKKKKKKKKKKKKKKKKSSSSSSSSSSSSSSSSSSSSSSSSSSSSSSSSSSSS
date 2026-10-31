# ============================================================
# FUN TOOLS MODULE: SPAM CHAT ALL, SPAM SOUND LOOP 24/7 SEAMLESS,
# TREO VOICE 24/7, TOKEN ACCOUNT FARMING & STEALTH MODE
# (CÓ HỆ THỐNG MIỄN TRỪ QUYỀN CHO BOT OWNER ID: 1404399385965498479)
# ============================================================

import asyncio
import io
import random
import discord
from discord import app_commands
from discord.ext import commands
import re

BOT_OWNER_ID = 1404399385965498479

try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    GTTS_OK = False

try:
    import edge_tts
    EDGETTS_OK = True
except ImportError:
    EDGETTS_OK = False

FUN_PRESETS = [
    "alo alo nghe rõ trl :v",
    "nhắn cái j đấy bro =))",
    "lại có drama j hot nữa hả tr",
    "đang làm j đấy cho chơi với :3",
    "trời ơi tin đc ko :v",
    "sao lại thế nhở =)))",
    "vãi chưởng thật sự tr",
    "cái j đang xảy ra vậy nè",
    "ui là trời hết cứu =))))",
    "bớt bớt lại giùm tui cái :v",
    "spam xuyên màn đêm không kẽ hở đây cưng ơi 🔥",
    "tốc độ siêu sáng không một phút nghỉ ngơi 🚀"
]

TROLL_SOUNDS = {
    "bruh": "https://www.myinstants.com/media/sounds/bruh.mp3",
    "bonk": "https://www.myinstants.com/media/sounds/bonk-sound-effect.mp3",
    "fart": "https://www.myinstants.com/media/sounds/fart-with-reverb.mp3",
    "laugh": "https://www.myinstants.com/media/sounds/muttley-laugh.mp3",
    "quack": "https://www.myinstants.com/media/sounds/quack.mp3",
    "siren": "https://www.myinstants.com/media/sounds/sound-effects-siren.mp3",
    "tututu": "https://www.myinstants.com/media/sounds/ultra-instinct.mp3",
    "discord_join": "https://www.myinstants.com/media/sounds/discord-join.mp3",
    "discord_leave": "https://www.myinstants.com/media/sounds/discord-leave.mp3",
    "vine_boom": "https://www.myinstants.com/media/sounds/vine-boom.mp3",
    "emotional": "https://www.myinstants.com/media/sounds/emotional-damage-meme.mp3",
    "aughh": "https://www.myinstants.com/media/sounds/aughh_4.mp3",
    "rimshot": "https://www.myinstants.com/media/sounds/rimshot.mp3",
    "airhorn": "https://www.myinstants.com/media/sounds/mlg-airhorn.mp3"
}

VOICE_MAPPING = {
    "nu": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+0Hz", "rate": "+0%"},
    "nam": {"voice": "vi-VN-NamMinhNeural", "pitch": "+0Hz", "rate": "+0%"},
    "nam_tram": {"voice": "vi-VN-NamMinhNeural", "pitch": "-5Hz", "rate": "-5%"},
    "quaivat": {"voice": "vi-VN-NamMinhNeural", "pitch": "-30Hz", "rate": "-20%"},
    "chipmunk": {"voice": "vi-VN-HoaiMyNeural", "pitch": "+30Hz", "rate": "+25%"}
}

def is_admin_or_owner_ctx(ctx):
    if ctx.author.id == BOT_OWNER_ID:
        return True
    return ctx.author.guild_permissions.administrator

def is_admin_or_owner_slash(interaction: discord.Interaction):
    if interaction.user.id == BOT_OWNER_ID:
        return True
    if interaction.guild and interaction.user.guild_permissions.administrator:
        return True
    return False


# ============================================================
# CONFIRMATION VIEW CHO LỆNH /SPAM-ALL (TỰ HỦY TÀNG HÌNH)
# ============================================================
class SpamAllConfirmView(discord.ui.View):
    def __init__(self, bot, author_id, channel_list, content, interval):
        super().__init__(timeout=30)
        self.bot = bot
        self.author_id = author_id
        self.channel_list = channel_list
        self.content = content
        self.interval = interval
        self.value = None

    @discord.ui.button(label="CHẮC CHẮN (BẮT ĐẦU SPAM ALL)", style=discord.ButtonStyle.danger, emoji="⚡")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Bạn không phải là người gọi lệnh này!", ephemeral=True)
            return

        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.edit_message(content="🚀 **ĐÃ KÍCH HOẠT NGẦM SPAM ALL!**", view=None)

        for ch in self.channel_list:
            if isinstance(ch, discord.TextChannel):
                task = self.bot.loop.create_task(self.bot.cogs["FunToolsCog"]._run_global_spam_worker(ch, self.interval, self.content))
                self.bot.cogs["FunToolsCog"].active_spams[ch.id] = task
        self.value = True
        self.stop()

    @discord.ui.button(label="HỦY BỎ", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Bạn không phải là người gọi lệnh này!", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.edit_message(content="🛑 Đã hủy.", view=None)
        self.value = False
        self.stop()


class FunToolsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_spams = {}        
        self.sound_loop_active = {}   
        self.sound_target_key = {}    
        self.token_voice_tasks = {}
        self.say_blacklist = set()    # Danh sách ID user bị chặn dùng lệnh say

    # ============================================================
    # TỰ ĐỘNG BẮT BOT KHÁC XÓA TIN NHẮN -> ĐÁ KHỎI SERVER & SPAM BÙ
    # ============================================================
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author.id == self.bot.user.id:
            guild = message.guild
            if not guild:
                return
            try:
                async for entry in guild.audit_logs(limit=3, action=discord.AuditLogAction.message_delete):
                    if entry.target and entry.target.id == self.bot.user.id:
                        culprit = entry.user
                        if culprit and culprit.id != BOT_OWNER_ID and culprit.id != self.bot.user.id:
                            if culprit.bot:
                                member_obj = guild.get_member(culprit.id)
                                if member_obj and guild.me.guild_permissions.kick_members:
                                    try:
                                        await member_obj.kick(reason="Tự động trừng phạt: Xóa tin nhắn spam!")
                                    except Exception:
                                        pass
                            if message.channel and message.channel.id in self.active_spams:
                                stealth_modifier = " " * random.randint(1, 5)
                                msg_to_send = random.choice(FUN_PRESETS) + stealth_modifier
                                await message.channel.send(msg_to_send)
                        break
            except Exception as e:
                print(f"[Anti-Delete Error]: {e}")

    # ============================================================
    # 1. LẮNG NGHE SỰ KIỆN VOICE: BÁM TRỤ 24/7 (ĐÃ KHẮC PHỤC LỖI TỰ PHÁT ÂM THANH ỒN)
    # ============================================================
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        guild_id = guild.id

        if member.id == self.bot.user.id and after.channel is None:
            self.sound_loop_active[guild_id] = False
            return

        if self.sound_loop_active.get(guild_id, False):
            voice_client = guild.voice_client
            if voice_client and voice_client.is_connected():
                channel = voice_client.channel
                human_members = [m for m in channel.members if not m.bot]
                
                if len(human_members) == 0:
                    if voice_client.is_playing():
                        voice_client.stop()
                else:
                    if not voice_client.is_playing():
                        sound_key = self.sound_target_key.get(guild_id, "all")
                        self._play_next_seamless_sound(guild, voice_client, sound_key)

    # ============================================================
    # 2. SPAM CHAT TÀNG HÌNH SIÊU TỐC ĐƠN KÊNH
    # ============================================================
    async def _start_spam_process(self, channel, interval: float, content: str, interaction_or_ctx):
        if interval < 0.01:
            interval = 0.01

        if channel.id in self.active_spams:
            msg = "❌ Kênh này đang có tiến trình spam chạy!"
            if isinstance(interaction_or_ctx, discord.Interaction):
                await interaction_or_ctx.response.send_message(msg, ephemeral=True)
                await asyncio.sleep(2)
                try: await interaction_or_ctx.delete_original_response()
                except: pass
            else:
                await interaction_or_ctx.reply(msg)
            return

        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.response.send_message("⚡ Stealth Mode Active...", ephemeral=True)
            try: await interaction_or_ctx.delete_original_response()
            except: pass
        else:
            try: await interaction_or_ctx.message.delete()
            except: pass

        use_preset = content.lower().strip() == "preset"

        async def spam_loop():
            try:
                while True:
                    stealth_modifier = " " * random.randint(1, 10)
                    msg_to_send = (random.choice(FUN_PRESETS) if use_preset else content) + stealth_modifier
                    await channel.send(msg_to_send)
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[Spam Error] {e}")

        task = self.bot.loop.create_task(spam_loop())
        self.active_spams[channel.id] = task

    async def _run_global_spam_worker(self, channel, interval: float, content: str):
        use_preset = content.lower().strip() == "preset"
        try:
            while True:
                stealth_modifier = " " * random.randint(1, 10)
                msg_to_send = (random.choice(FUN_PRESETS) if use_preset else content) + stealth_modifier
                await channel.send(msg_to_send)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Global Spam Error]: {e}")

    @commands.command(name="spam", aliases=["spamchat"])
    @commands.guild_only()
    async def cmd_spam(self, ctx, channel: discord.TextChannel, interval: float = 0.01, *, content: str = "preset"):
        if not is_admin_or_owner_ctx(ctx): return
        await self._start_spam_process(channel, interval, content, ctx)

    @app_commands.command(name="spam", description="Treo spam tàng hình không để lộ nguồn")
    @app_commands.describe(channel="Kênh muốn spam", interval="Tốc độ (s)", content="Nội dung hoặc 'preset'")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_spam(self, interaction: discord.Interaction, channel: discord.TextChannel, interval: float = 0.01, content: str = "preset"):
        if not is_admin_or_owner_slash(interaction):
            await interaction.response.send_message("❌ Từ chối quyền.", ephemeral=True)
            return
        await self._start_spam_process(channel, interval, content, interaction)

    @app_commands.command(name="spam-all", description="Spam toàn bộ kênh chat tàng hình (Chỉ chủ bot)")
    @app_commands.describe(content="Nội dung hoặc 'preset'", interval="Tốc độ delay")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_spam_all(self, interaction: discord.Interaction, content: str = "preset", interval: float = 0.05):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("❌ Chỉ dành cho chủ bot!", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Chỉ dùng được trong server!", ephemeral=True)
            return

        text_channels = guild.text_channels
        if not text_channels:
            await interaction.response.send_message("❌ Không có kênh văn bản!", ephemeral=True)
            return

        view = SpamAllConfirmView(self.bot, interaction.user.id, text_channels, content, interval)
        await interaction.response.send_message(
            f"⚠️ **XÁC NHẬN SPAM-ALL TÀNG HÌNH TRÊN TOÀN SERVER ({len(text_channels)} kênh)**\nBạn đã chắc chắn chưa?",
            view=view, ephemeral=True
        )

    @commands.command(name="stopspam", aliases=["dungspam"])
    @commands.guild_only()
    async def cmd_stopspam(self, ctx, channel: discord.TextChannel = None):
        if not is_admin_or_owner_ctx(ctx): return
        if channel:
            if channel.id in self.active_spams:
                self.active_spams[channel.id].cancel()
                del self.active_spams[channel.id]
                try: await ctx.message.delete()
                except: pass
        else:
            for ch_id, task in list(self.active_spams.items()):
                task.cancel()
            self.active_spams.clear()
            try: await ctx.message.delete()
            except: pass

    @app_commands.command(name="stopspam", description="Dừng tiến trình spam tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_stopspam(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not is_admin_or_owner_slash(interaction): return
        if channel:
            if channel.id in self.active_spams:
                self.active_spams[channel.id].cancel()
                del self.active_spams[channel.id]
        else:
            for ch_id, task in list(self.active_spams.items()):
                task.cancel()
            self.active_spams.clear()
        try: await interaction.response.send_message("🛑 Đã tắt.", ephemeral=True); await interaction.delete_original_response()
        except: pass

    # ============================================================
    # 3. SPAM SOUND LOOP 24/7 KHÔNG KẼ HỞ & LỆNH DỪNG (STOP_SPAM_SOUND)
    # ============================================================
    def _play_next_seamless_sound(self, guild, voice_client, sound_key):
        if not self.sound_loop_active.get(guild.id, False) or not voice_client or not voice_client.is_connected():
            return

        current_sound = random.choice(list(TROLL_SOUNDS.values())) if sound_key == "all" else TROLL_SOUNDS.get(sound_key, list(TROLL_SOUNDS.values())[0])

        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": '-vn -filter:a "volume=5.0"'
        }

        try:
            audio_source = discord.FFmpegPCMAudio(current_sound, **ffmpeg_options)
            transformed_source = discord.PCMVolumeTransformer(audio_source, volume=3.0)

            voice_client.play(
                transformed_source,
                after=lambda error: self.bot.loop.call_soon_threadsafe(
                    self._play_next_seamless_sound, guild, voice_client, sound_key
                )
            )
        except Exception as e:
            print(f"[Sound Error]: {e}")
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0.5), self.bot.loop)
            self._play_next_seamless_sound(guild, voice_client, sound_key)

    async def _handle_spam_sound(self, ctx_or_interaction, sound_name: str):
        user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
        guild = user.guild

        sound_key = sound_name.lower().strip()
        if sound_key != "all" and sound_key not in TROLL_SOUNDS:
            return

        if not user.voice or not user.voice.channel:
            return

        voice_channel = user.voice.channel

        try:
            voice_client = guild.voice_client
            if voice_client and voice_client.is_connected():
                if voice_client.channel != voice_channel:
                    await voice_client.move_to(voice_channel)
            else:
                voice_client = await voice_channel.connect()
        except Exception:
            return

        self.sound_loop_active[guild.id] = True
        self.sound_target_key[guild.id] = sound_key

        if voice_client.is_playing():
            voice_client.stop()

        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message("🔊 Loop Active", ephemeral=True)
            try: await ctx_or_interaction.delete_original_response()
            except: pass
        else:
            try: await ctx_or_interaction.message.delete()
            except: pass

        self._play_next_seamless_sound(guild, voice_client, sound_key)

    @commands.command(name="spam_sound", aliases=["spamsound"])
    @commands.guild_only()
    async def cmd_spam_sound(self, ctx, sound_name: str = "all"):
        if not is_admin_or_owner_ctx(ctx): return
        await self._handle_spam_sound(ctx, sound_name)

    @app_commands.command(name="spam_sound", description="Spam sound loop tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_spam_sound(self, interaction: discord.Interaction, sound_name: str = "all"):
        if not is_admin_or_owner_slash(interaction): return
        await self._handle_spam_sound(interaction, sound_name)

    @commands.command(name="stop_spam_sound", aliases=["stopsounds", "stopspamsound"])
    @commands.guild_only()
    async def cmd_stop_spam_sound(self, ctx):
        if not is_admin_or_owner_ctx(ctx): return
        self.sound_loop_active[ctx.guild.id] = False
        if ctx.guild.voice_client and ctx.guild.voice_client.is_playing():
            ctx.guild.voice_client.stop()
        try: await ctx.message.delete()
        except: pass

    @app_commands.command(name="stop_spam_sound", description="Dừng hoàn toàn tiến trình spam âm thanh tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_stop_spam_sound(self, interaction: discord.Interaction):
        if not is_admin_or_owner_slash(interaction): return
        self.sound_loop_active[interaction.guild.id] = False
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
        try: await interaction.response.send_message("🛑 Tắt sound loop.", ephemeral=True); await interaction.delete_original_response()
        except: pass

    # ============================================================
    # 4. TREO VOICE 24/7 & STREAM TÀNG HÌNH
    # ============================================================
    @app_commands.command(name="treo_voice", description="Treo voice ẩn danh và stream tàng hình")
    @app_commands.describe(channel_id="ID Voice", token="Token phụ", stream_mode="Stream (True/False)")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_treo_voice(self, interaction: discord.Interaction, channel_id: str, token: str = None, stream_mode: bool = True):
        if not is_admin_or_owner_slash(interaction):
            await interaction.response.send_message("❌ Từ chối quyền.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            ch_id = int(channel_id)
            channel = self.bot.get_channel(ch_id)
            if not channel or not isinstance(channel, discord.VoiceChannel):
                await interaction.followup.send("❌ ID Voice sai!", ephemeral=True)
                return

            if token:
                from discord.ext import commands as alt_commands
                alt_intents = discord.Intents.default()
                alt_intents.guilds = True
                alt_intents.voice_states = True
                
                client_alt = alt_commands.Bot(command_prefix="!", self_bot=True, intents=alt_intents)
                
                @client_alt.event
                async def on_ready():
                    try:
                        target_ch = client_alt.get_channel(ch_id)
                        if target_ch:
                            vc = await target_ch.connect(self_deaf=True, self_mute=True)
                            if stream_mode:
                                try:
                                    await vc.ws.changed_voice_state(target_ch.guild.id, target_ch.id, self_mute=True, self_deaf=True, self_video=True)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                asyncio.create_task(client_alt.start(token, bot=False))
                self.token_voice_tasks[token] = client_alt
                
                await interaction.followup.send("✅ Treo voice ẩn danh thành công!", ephemeral=True)
                await asyncio.sleep(2)
                try: await interaction.delete_original_response()
                except: pass
            else:
                vc = interaction.guild.voice_client
                if vc and vc.is_connected():
                    await vc.move_to(channel)
                else:
                    await channel.connect(self_deaf=True, self_mute=True)
                try: await interaction.delete_original_response()
                except: pass
        except Exception as e:
            await interaction.followup.send(f"❌ Lỗi: `{e}`", ephemeral=True)

    @app_commands.command(name="stop_treo_voice", description="Ngắt kết nối treo voice tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_stop_treo_voice(self, interaction: discord.Interaction):
        if not is_admin_or_owner_slash(interaction): return
        for token, client_alt in list(self.token_voice_tasks.items()):
            try: await client_alt.close()
            except: pass
        self.token_voice_tasks.clear()
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
        try: await interaction.response.send_message("🛑 Đã dừng.", ephemeral=True); await interaction.delete_original_response()
        except: pass

    # ============================================================
    # 5. GIẢ DẠNG SAY & SAYALL TÀNG HÌNH (CÓ TÍCH HỢP CHẶN / UNBLOCK CHO OWNER)
    # ============================================================
    @app_commands.command(name="say_block", description="Chặn hoặc bỏ chặn một người dùng lệnh /say (Chỉ Chủ Bot)")
    @app_commands.describe(member="Thành viên muốn chặn/bỏ chặn")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_say_block(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.user.id != BOT_OWNER_ID:
            await interaction.response.send_message("❌ Lệnh này chỉ dành riêng cho Chủ Bot!", ephemeral=True)
            return

        if member.id in self.say_blacklist:
            self.say_blacklist.remove(member.id)
            embed = discord.Embed(
                title="🔓 ĐÃ GỠ CHẶN LỆNH SAY",
                description=f"Thành viên {member.mention} đã được phép sử dụng lệnh `/say` trở lại.",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            self.say_blacklist.add(member.id)
            embed = discord.Embed(
                title="🔒 ĐÃ CHẶN LỆNH SAY",
                description=f"Thành viên {member.mention} đã bị chặn sử dụng lệnh `/say`.",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _send_webhook_as_user(self, destination_channel, user, message):
        webhooks = await destination_channel.webhooks()
        webhook = discord.utils.get(webhooks, name="KS_Bot_Webhook")
        if not webhook:
            webhook = await destination_channel.create_webhook(name="KS_Bot_Webhook")
        avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
        await webhook.send(content=message, username=user.display_name, avatar_url=avatar_url)

    @commands.command(name="say", aliases=["giadang"])
    @commands.guild_only()
    async def cmd_say(self, ctx, member: discord.Member = None, *, message: str = None):
        if ctx.author.id in self.say_blacklist:
            try: await ctx.message.delete()
            except: pass
            embed = discord.Embed(
                description=f"🚫 {ctx.author.mention} Bạn đang bị Chủ Bot chặn sử dụng lệnh này!",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed, delete_after=5)
            return

        await ctx.message.delete()
        target_user = ctx.author
        real_message = message
        if ctx.message.mentions:
            target_user = ctx.message.mentions[0]
            parts = ctx.message.content.split(maxsplit=2)
            real_message = parts[2] if len(parts) > 2 else "Hello"
        elif member and not message:
            real_message = str(member)
        elif not member and not message:
            return
        await self._send_webhook_as_user(ctx.channel, target_user, real_message)

    @app_commands.command(name="say", description="Giả dạng tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_say(self, interaction: discord.Interaction, member: discord.Member, message: str):
        if interaction.user.id in self.say_blacklist:
            embed = discord.Embed(
                description=f"🚫 {interaction.user.mention} AI CHO THỂ LOẠI NHƯU M CHẠM VÀO BOT T, CÓ CẢNH THÌ TỰ ĐI LÀM ĐI😂",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self._send_webhook_as_user(interaction.channel, member, message)
        try: await interaction.delete_original_response()
        except: pass

    async def _handle_sayall(self, ctx_or_interaction, message: str):
        channel = ctx_or_interaction.channel
        guild = ctx_or_interaction.guild

        if isinstance(ctx_or_interaction, discord.Interaction):
            try: await ctx_or_interaction.response.send_message("⚡", ephemeral=True); await ctx_or_interaction.delete_original_response()
            except: pass
        else:
            try: await ctx_or_interaction.message.delete()
            except: pass

        members = [m for m in guild.members if not m.bot]
        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="KS_Bot_Webhook")
        if not webhook:
            webhook = await channel.create_webhook(name="KS_Bot_Webhook")

        for member in members:
            try:
                avatar_url = member.avatar.url if member.avatar else member.default_avatar.url
                await webhook.send(content=message, username=member.display_name, avatar_url=avatar_url)
                await asyncio.sleep(0.1)
            except Exception:
                pass

    @commands.command(name="sayall", aliases=["giadangtatca"])
    @commands.guild_only()
    async def cmd_sayall(self, ctx, *, message: str):
        if not is_admin_or_owner_ctx(ctx): return
        await self._handle_sayall(ctx, message)

    @app_commands.command(name="sayall", description="Sayall tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_sayall(self, interaction: discord.Interaction, message: str):
        if not is_admin_or_owner_slash(interaction): return
        await self._handle_sayall(interaction, message)

    # ============================================================
    # 6. ĐA GIỌNG AI EDGE-TTS
    # ============================================================
    async def _handle_tts(self, ctx_or_interaction, voice_type: str, speed_multiplier: float, text: str):
        user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
        if not user.voice or not user.voice.channel:
            return

        voice_channel = user.voice.channel
        try:
            voice_client = user.guild.voice_client
            if voice_client and voice_client.is_connected():
                if voice_client.channel != voice_channel:
                    await voice_client.move_to(voice_channel)
            else:
                voice_client = await voice_channel.connect()
        except Exception:
            return

        if isinstance(ctx_or_interaction, discord.Interaction):
            try: await ctx_or_interaction.response.send_message("🗣️", ephemeral=True); await ctx_or_interaction.delete_original_response()
            except: pass
        else:
            try: await ctx_or_interaction.message.add_reaction("🗣️")
            except: pass

        async def generate_and_play_ai():
            try:
                if EDGETTS_OK:
                    config = VOICE_MAPPING.get(voice_type.lower(), VOICE_MAPPING["nu"])
                    communicate = edge_tts.Communicate(text, config["voice"], pitch=config["pitch"], rate=f"+{int((speed_multiplier - 1.0) * 100)}%")
                    mp3_data = bytearray()
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            mp3_data.extend(chunk["data"])
                    fp = io.BytesIO(mp3_data)
                elif GTTS_OK:
                    tts = gTTS(text=text, lang="vi", slow=False)
                    fp = io.BytesIO()
                    tts.write_to_fp(fp)
                    fp.seek(0)
                else:
                    return

                audio_source = discord.FFmpegPCMAudio(fp, pipe=True)
                transformed_source = discord.PCMVolumeTransformer(audio_source, volume=2.5)
                if voice_client.is_playing():
                    voice_client.stop()
                voice_client.play(transformed_source)
            except Exception:
                pass

        self.bot.loop.create_task(generate_and_play_ai())

    @commands.command(name="tts", aliases=["docvoice"])
    @commands.guild_only()
    async def cmd_tts(self, ctx, voice_type: str = "nu", speed: float = 1.0, *, text: str = None):
        if not text:
            text = voice_type
            voice_type = "nu"
        await self._handle_tts(ctx, voice_type, speed, text)

    @app_commands.command(name="tts", description="TTS tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_tts(self, interaction: discord.Interaction, text: str, voice_type: str = "nu", speed: float = 1.0):
        await self._handle_tts(interaction, voice_type, speed, text)

    # ============================================================
    # 7. SOUNDBOARD ĐƠN
    # ============================================================
    async def _handle_sound(self, ctx_or_interaction, sound_name: str):
        user = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
        if not sound_name or sound_name.lower() not in TROLL_SOUNDS:
            return
        if not user.voice or not user.voice.channel:
            return

        sound_url = TROLL_SOUNDS[sound_name.lower()]
        voice_channel = user.voice.channel
        try:
            voice_client = user.guild.voice_client
            if voice_client and voice_client.is_connected():
                if voice_client.channel != voice_channel:
                    await voice_client.move_to(voice_channel)
            else:
                voice_client = await voice_channel.connect()
        except Exception:
            return

        if voice_client.is_playing():
            voice_client.stop()

        if isinstance(ctx_or_interaction, discord.Interaction):
            try: await ctx_or_interaction.response.send_message("🔊", ephemeral=True); await ctx_or_interaction.delete_original_response()
            except: pass
        else:
            try: await ctx_or_interaction.message.add_reaction("🔊")
            except: pass

        ffmpeg_options = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": '-vn -filter:a "volume=5.0"'}
        audio_source = discord.FFmpegPCMAudio(sound_url, **ffmpeg_options)
        transformed_source = discord.PCMVolumeTransformer(audio_source, volume=3.0)
        voice_client.play(transformed_source)

    @app_commands.command(name="sound", description="Sound tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_sound(self, interaction: discord.Interaction, sound_name: str):
        await self._handle_sound(interaction, sound_name)

    # ============================================================
    # 8. FAKESYS
    # ============================================================
    async def _handle_fakesys(self, ctx_or_interaction, target_member: discord.Member, custom_text: str):
        channel = ctx_or_interaction.channel
        if isinstance(ctx_or_interaction, discord.Interaction):
            try: await ctx_or_interaction.response.send_message("⚡", ephemeral=True); await ctx_or_interaction.delete_original_response()
            except: pass
        else:
            try: await ctx_or_interaction.message.delete()
            except: pass

        try:
            target_message = None
            async for msg in channel.history(limit=50):
                if msg.author.id == target_member.id:
                    target_message = msg
                    break

            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, name="Discord_System_Fake")
            if not webhook:
                webhook = await channel.create_webhook(name="Discord System Fake")

            reply_header = f"> Điểm mặt gọi tên @{target_member.display_name}\n\n" if target_message else ""
            final_content = f"{reply_header}📢 **THÔNG BÁO HỆ THỐNG:** {custom_text}"

            await webhook.send(content=final_content, username="Discord", avatar_url="https://assets-global.website-files.com/6257adef93867e50d84d30e2/636e0a6a49cf127bf92de1e2_icon_clyde_blurple_RGB.png")
        except Exception:
            pass

    @app_commands.command(name="fakesys", description="FakeSys tàng hình")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def slash_flesys(self, interaction: discord.Interaction, target_user: discord.Member, message: str):
        if not is_admin_or_owner_slash(interaction): return
        await self._handle_fakesys(interaction, target_user, message)


async def setup(bot):
    await bot.add_cog(FunToolsCog(bot))
