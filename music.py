import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio

# --- CẤU HÌNH YT-DLP CHUYÊN DÙNG DEEZER ---
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'default_search': 'dzsearch',  # Mặc định tìm kiếm qua Deezer
    'source_address': '0.0.0.0',
    'js_runtimes': {'deno': {}}
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# Lưu trữ dữ liệu từng Server
music_data = {}

def get_guild_data(guild_id):
    if guild_id not in music_data:
        music_data[guild_id] = {
            'queue': [],
            'current': None,
            'loop': False,
            'volume': 1.2, # Mặc định 120%
            'vc': None,
            'audio_source': None,
            'board_msg': None
        }
    return music_data[guild_id]

def create_board_embed(data, author):
    current = data['current']
    loop_status = "Bật" if data['loop'] else "Tắt"
    vol_percent = int(data['volume'] * 100)
    
    embed = discord.Embed(
        title="🎶 Đang phát (Deezer)",
        description=f"**[{current['title']}]({current['url']})**",
        color=discord.Color.blue()
    )
    if current.get('thumbnail'):
        embed.set_thumbnail(url=current['thumbnail'])
        
    embed.add_field(name="⏱️ Thời lượng", value=current['duration_str'], inline=True)
    embed.add_field(name="🔊 Âm lượng", value=f"{vol_percent}%", inline=True)
    embed.add_field(name="🔁 Loop", value=loop_status, inline=True)
    embed.add_field(name="👤 Yêu cầu bởi", value=author.mention, inline=True)
    embed.add_field(name="🌐 Nguồn", value="Deezer", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    if data['queue']:
        next_songs = "\n".join([f"**{i+1}.** {song['title']}" for i, song in enumerate(data['queue'][:3])])
        if len(data['queue']) > 3:
            next_songs += f"\n*...và {len(data['queue']) - 3} bài nữa*"
    else:
        next_songs = "Không có bài hát nào trong hàng chờ."

    embed.add_field(name=f"📋 Tiếp theo ({len(data['queue'])} bài)", value=next_songs, inline=False)
    embed.set_footer(text="Sử dụng các nút bên dưới để điều khiển nhạc nhanh chóng.")
    return embed

class MusicControlView(discord.ui.View):
    def __init__(self, guild_id, bot):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⏮️ Lùi bài hát", ephemeral=True)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.primary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        vc = data['vc']
        if vc and vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
        elif vc and vc.is_paused():
            vc.resume()
            button.emoji = "⏸️"
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("Không có nhạc đang phát!", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        vc = data['vc']
        if vc and (vc.is_playing() or vc.is_paused()):
            data['loop'] = False
            vc.stop()
            await interaction.response.send_message("⏭️ Đã bỏ qua bài hát!", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        import random
        if len(data['queue']) > 1:
            random.shuffle(data['queue'])
            await interaction.response.send_message("🔀 Đã trộn ngẫu nhiên hàng chờ!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Hàng chờ không đủ bài để trộn!", ephemeral=True)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        data['loop'] = not data['loop']
        status = "Bật" if data['loop'] else "Tắt"
        if data['board_msg'] and data['current']:
            try:
                embed = create_board_embed(data, interaction.user)
                await data['board_msg'].edit(embed=embed)
            except:
                pass
        await interaction.response.send_message(f"🔁 Đã **{status}** chế độ Lặp lại!", ephemeral=True)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        data['volume'] = max(0.1, data['volume'] - 0.1)
        if data['audio_source']:
            data['audio_source'].volume = data['volume']
        
        vol_percent = int(data['volume'] * 100)
        if data['board_msg'] and data['current']:
            try:
                embed = create_board_embed(data, interaction.user)
                await data['board_msg'].edit(embed=embed)
            except:
                pass
            
        await interaction.response.send_message(f"🔉 Giảm âm lượng xuống **{vol_percent}%**", ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        data['volume'] = min(2.0, data['volume'] + 0.1)
        if data['audio_source']:
            data['audio_source'].volume = data['volume']
            
        vol_percent = int(data['volume'] * 100)
        if data['board_msg'] and data['current']:
            try:
                embed = create_board_embed(data, interaction.user)
                await data['board_msg'].edit(embed=embed)
            except:
                pass
            
        await interaction.response.send_message(f"🔊 Tăng âm lượng lên **{vol_percent}%**", ephemeral=True)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        if not data['queue']:
            return await interaction.response.send_message("📋 Hàng chờ hiện đang trống!", ephemeral=True)
        q_text = "\n".join([f"**{i+1}.** {song['title']}" for i, song in enumerate(data['queue'][:10])])
        await interaction.response.send_message(f"📋 **Hàng chờ hiện tại:**\n{q_text}", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        data['queue'].clear()
        data['loop'] = False
        if data['vc']:
            data['vc'].stop()
        await interaction.response.send_message("⏹️ Đã dừng phát nhạc và xóa hàng chờ!", ephemeral=True)

    @discord.ui.button(label="Rời phòng", emoji="🚪", style=discord.ButtonStyle.danger, row=2)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = get_guild_data(self.guild_id)
        if data['vc']:
            await data['vc'].disconnect()
            data['vc'] = None
            data['queue'].clear()
            await interaction.response.send_message("🚪 Đã rời voice chat!", ephemeral=False)


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def play_next(self, guild_id, channel, author):
        data = get_guild_data(guild_id)
        vc = data['vc']

        if data['loop'] and data['current']:
            song = data['current']
        elif len(data['queue']) > 0:
            song = data['queue'].pop(0)
            data['current'] = song
        else:
            data['current'] = None
            return

        try:
            info = ytdl.extract_info(song['url'], download=False)
            stream_url = info['url']
        except Exception as e:
            print(f"Lỗi stream Deezer: {e}")
            return

        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32M -analyzeduration 0',
            'options': '-vn'
        }

        raw_source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
        source = discord.PCMVolumeTransformer(raw_source, volume=data['volume'])
        data['audio_source'] = source

        if vc and vc.is_connected():
            vc.play(source, after=lambda e: self.play_next(guild_id, channel, author))

        embed = create_board_embed(data, author)
        view = MusicControlView(guild_id, self.bot)

        async def send_board():
            try:
                msg = await channel.send(embed=embed, view=view)
                data['board_msg'] = msg
            except:
                pass

        asyncio.run_coroutine_threadsafe(send_board(), self.bot.loop)

    async def process_play(self, ctx_or_interaction, query: str):
        if isinstance(ctx_or_interaction, commands.Context):
            author = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            send_func = ctx_or_interaction.send
            text_channel = ctx_or_interaction.channel
        else:
            author = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
            await ctx_or_interaction.response.defer()
            send_func = ctx_or_interaction.followup.send
            text_channel = ctx_or_interaction.channel

        if not author.voice:
            return await send_func("❌ Bro phải vào Voice Chat trước đã!")

        # Chặn tuyệt đối nếu cố tình nhập link
        if "http://" in query or "https://" in query or "www." in query:
            return await send_func("❌ Bot hiện tại **chỉ hỗ trợ nhập tên bài hát**, không nhận link!")

        search_msg = await send_func(f"🔍 Đang tìm trên Deezer: **`{query}`**...")

        voice_channel = author.voice.channel
        data = get_guild_data(guild.id)

        if not data['vc'] or not data['vc'].is_connected():
            try:
                data['vc'] = await voice_channel.connect()
            except Exception as e:
                return await search_msg.edit(content=f"❌ Không thể kết nối Voice: {e}")

        # Thêm tiền tố dzsearch: để yt-dlp ép buộc tìm trên Deezer
        search_query = f"dzsearch:{query}"

        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
        except Exception as e:
            return await search_msg.edit(content=f"❌ Không tìm thấy bài hát trên Deezer: {e}")

        if 'entries' in info and len(info['entries']) > 0:
            info = info['entries'][0]
        else:
            return await search_msg.edit(content="❌ Không tìm thấy kết quả phù hợp trên Deezer!")

        duration = info.get('duration', 0)
        m, s = divmod(duration, 60)

        song_info = {
            'title': info.get('title', query),
            'url': info.get('webpage_url', info.get('url', '')),
            'thumbnail': info.get('thumbnail', ''),
            'duration_str': f"{m}:{s:02d}"
        }

        if data['vc'].is_playing() or data['vc'].is_paused():
            data['queue'].append(song_info)
            await search_msg.edit(content=f"✅ Đã thêm **{song_info['title']}** vào hàng chờ! (Vị trí #{len(data['queue'])})")
        else:
            data['queue'].append(song_info)
            self.play_next(guild.id, text_channel, author)
            try:
                await search_msg.delete()
            except:
                pass

    # --- AUTOCOMPLETE GỢI Ý BÀI HÁT TRÊN DEEZER ---
    async def search_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if not current:
            return []
        
        loop = asyncio.get_running_loop()
        try:
            partial_options = {**YTDL_OPTIONS, 'default_search': 'dzsearch5', 'quiet': True}
            data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(partial_options).extract_info(f"dzsearch:{current}", download=False))
            
            entries = data.get('entries', []) if data else []
            choices = []
            for entry in entries[:5]:
                title = entry.get('title', 'Unknown')
                if len(title) > 99:
                    title = title[:96] + "..."
                choices.append(app_commands.Choice(name=title, value=title))
            return choices
        except Exception:
            return []

    # --- LỆNH PREFIX ---
    @commands.command(name="nhac", aliases=["p", "play"])
    async def cmd_nhac(self, ctx, *, query: str):
        await self.process_play(ctx, query)

    @commands.command(name="loop")
    async def cmd_loop(self, ctx):
        data = get_guild_data(ctx.guild.id)
        data['loop'] = not data['loop']
        status = "Bật" if data['loop'] else "Tắt"
        await ctx.send(f"🔁 Đã **{status}** chế độ Lặp lại!")

    @commands.command(name="queue", aliases=["board", "q"])
    async def cmd_queue(self, ctx):
        data = get_guild_data(ctx.guild.id)
        if not data['current']:
            return await ctx.send("❌ Hiện tại bot không phát bài hát nào!")
        embed = create_board_embed(data, ctx.author)
        view = MusicControlView(ctx.guild.id, self.bot)
        msg = await ctx.send(embed=embed, view=view)
        data['board_msg'] = msg

    # --- LỆNH SLASH (Có Autocomplete gợi ý Deezer) ---
    @app_commands.command(name="nhac", description="Tìm và phát nhạc chất lượng cao từ Deezer")
    @app_commands.autocomplete(tên_bài=search_autocomplete)
    async def slash_nhac(self, interaction: discord.Interaction, tên_bài: str):
        await self.process_play(interaction, tên_bài)

    @app_commands.command(name="loop", description="Bật/Tắt chế độ lặp bài hát")
    async def slash_loop(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        data['loop'] = not data['loop']
        status = "Bật" if data['loop'] else "Tắt"
        await interaction.response.send_message(f"🔁 Đã **{status}** chế độ Lặp lại!")

    @app_commands.command(name="queue", description="Hiển thị Board điều khiển và hàng chờ")
    async def slash_queue(self, interaction: discord.Interaction):
        data = get_guild_data(interaction.guild.id)
        if not data['current']:
            return await interaction.response.send_message("❌ Hiện tại bot không phát bài hát nào!", ephemeral=True)
        embed = create_board_embed(data, interaction.user)
        view = MusicControlView(interaction.guild.id, self.bot)
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(Music(bot))
