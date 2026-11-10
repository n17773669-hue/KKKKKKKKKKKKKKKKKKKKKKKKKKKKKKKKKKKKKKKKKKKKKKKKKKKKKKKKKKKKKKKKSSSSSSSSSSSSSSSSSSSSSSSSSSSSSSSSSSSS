"""
casino.py — Tài Xỉu, Bầu Cua, Tù Xì (rps), Mines.
Logic giữ nguyên như bot.py cũ, chỉ chuyển thành Cog.
Nạp bằng: await bot.load_extension("casino")
"""
import math
import random
import asyncio

import discord
from discord.ext import commands

from database import (
    get_user, update_user_full, add_exp_to_user, parse_bet_amount,
    COIN_EMOJI, MAIN_COLOR,
)


# ==========================================
# TÀI XỈU
# ==========================================
class TaiXiuModal(discord.ui.Modal, title="NHẬP SỐ TIỀN CƯỢC"):
    money_input = discord.ui.TextInput(
        label="SỐ TIỀN CƯỢC (Hoặc gõ 'all')",
        placeholder="Nhập số tiền hoặc all...",
        min_length=1,
        max_length=15
    )

    def __init__(self, outer_view, choice_type):
        super().__init__()
        self.outer_view = outer_view
        self.choice_type = choice_type

    async def on_submit(self, interaction: discord.Interaction):
        u_data = get_user(str(interaction.user.id))
        bet = parse_bet_amount(u_data[0], self.money_input.value)

        if bet is None or bet <= 0:
            await interaction.response.send_message("❌ Số tiền cược không hợp lệ!", ephemeral=True)
            return

        if u_data[0] < bet:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", ephemeral=True)
            return

        if interaction.user.id not in self.outer_view.bets:
            self.outer_view.bets[interaction.user.id] = {}

        current_door_bet = self.outer_view.bets[interaction.user.id].get(self.choice_type, 0)
        self.outer_view.bets[interaction.user.id][self.choice_type] = current_door_bet + bet

        await interaction.response.send_message(f"✅ Đã đặt cược **{bet:,}** {COIN_EMOJI} vào cửa **{str(self.choice_type).upper()}**!", ephemeral=True)


class TaiXiuChoiceButton(discord.ui.Button):
    def __init__(self, label, choice_value, style, row_val):
        super().__init__(label=label, style=style, row=row_val)
        self.choice_value = choice_value

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TaiXiuModal(self.view, self.choice_value))


class TaiXiuButtonsView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=35)
        self.ctx = ctx
        self.bets = {}

        self.add_item(TaiXiuChoiceButton("Xỉu (3-10)", "Xiu (3-10)", discord.ButtonStyle.success, 0))
        self.add_item(TaiXiuChoiceButton("Tài (11-18)", "Tai (11-18)", discord.ButtonStyle.danger, 0))
        self.add_item(TaiXiuChoiceButton("Chẵn", "Chẵn", discord.ButtonStyle.secondary, 0))
        self.add_item(TaiXiuChoiceButton("Lẻ", "Lẻ", discord.ButtonStyle.secondary, 0))

        for i in range(3, 19):
            row_idx = 1 + (i - 3) // 5
            self.add_item(TaiXiuChoiceButton(f"Số {i}", f"Số {i}", discord.ButtonStyle.primary, row_idx))


# ==========================================
# BẦU CUA
# ==========================================
BC_ANIMALS = ["nai", "bầu", "gà", "cá", "cua", "tôm"]
BC_EMOJIS = {"nai": "🦌", "bầu": "🎃", "gà": "🐓", "cá": "🐠", "cua": "🦀", "tôm": "🦐"}


class BauCuaModal(discord.ui.Modal, title="NHẬP SỐ TIỀN CƯỢC BẦU CUA"):
    money_input = discord.ui.TextInput(
        label="SỐ TIỀN CƯỢC (Hoặc gõ 'all')",
        placeholder="Nhập số tiền hoặc all...",
        min_length=1,
        max_length=15
    )

    def __init__(self, outer_view, choice_type):
        super().__init__()
        self.outer_view = outer_view
        self.choice_type = choice_type

    async def on_submit(self, interaction: discord.Interaction):
        u_data = get_user(str(interaction.user.id))
        bet = parse_bet_amount(u_data[0], self.money_input.value)

        if bet is None or bet <= 0:
            await interaction.response.send_message("❌ Số tiền cược không hợp lệ!", ephemeral=True)
            return
        if bet > 250000:
            await interaction.response.send_message("❌ Số tiền cược tối đa mỗi lần là 250,000!", ephemeral=True)
            return

        if u_data[0] < bet:
            await interaction.response.send_message(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", ephemeral=True)
            return

        if interaction.user.id not in self.outer_view.bets:
            self.outer_view.bets[interaction.user.id] = {}

        current_door_bet = self.outer_view.bets[interaction.user.id].get(self.choice_type, 0)
        self.outer_view.bets[interaction.user.id][self.choice_type] = current_door_bet + bet

        await interaction.response.send_message(f"✅ Đã đặt cược **{bet:,}** {COIN_EMOJI} vào cửa **{self.choice_type.capitalize()}**!", ephemeral=True)


class BauCuaChoiceButton(discord.ui.Button):
    def __init__(self, label, choice_value, emoji, style, row_val):
        super().__init__(label=label, emoji=emoji, style=style, row=row_val)
        self.choice_value = choice_value

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BauCuaModal(self.view, self.choice_value))


class BauCuaButtonsView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bets = {}

        self.add_item(BauCuaChoiceButton("Nai", "nai", "🦌", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Bầu", "bầu", "🎃", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Gà", "gà", "🐓", discord.ButtonStyle.primary, 0))
        self.add_item(BauCuaChoiceButton("Cá", "cá", "🐠", discord.ButtonStyle.primary, 1))
        self.add_item(BauCuaChoiceButton("Cua", "cua", "🦀", discord.ButtonStyle.primary, 1))
        self.add_item(BauCuaChoiceButton("Tôm", "tôm", "🦐", discord.ButtonStyle.primary, 1))


# ==========================================
# TÙ XÌ
# ==========================================
class TuXiView(discord.ui.View):
    def __init__(self, author, bet_amount):
        super().__init__(timeout=60)
        self.author = author
        self.bet_amount = bet_amount
        self.choice = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message("❌ Đây không phải ván Tù Xì của bạn!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Búa", style=discord.ButtonStyle.primary, emoji="✊")
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "búa"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Kéo", style=discord.ButtonStyle.success, emoji="✌️")
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "kéo"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Bao", style=discord.ButtonStyle.danger, emoji="🖐️")
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "bao"
        self.stop()
        await interaction.response.defer()


# ==========================================
# MINES
# ==========================================
class MinesButton(discord.ui.Button):
    def __init__(self, index, view_obj):
        super().__init__(style=discord.ButtonStyle.secondary, label="?", row=index // 3)
        self.index = index
        self.mine_view = view_obj

    async def callback(self, interaction: discord.Interaction):
        await self.mine_view.handle_click(interaction, self)


class MinesView(discord.ui.View):
    def __init__(self, ctx, bet, mines_count):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.bet = bet
        self.mines_count = mines_count
        self.grid_size = 9
        self.mines = random.sample(range(self.grid_size), self.mines_count)
        self.revealed = [False] * self.grid_size
        self.game_over = False
        self.diamonds_found = 0

        for i in range(self.grid_size):
            self.add_item(MinesButton(i, self))

    def calculate_multiplier(self):
        n = self.grid_size
        k = self.mines_count
        r = self.diamonds_found
        if r == 0:
            return 1.0
        mult = math.prod([(n - i) / (n - k - i) for i in range(r)]) * 0.95
        return round(mult, 2)

    async def handle_click(self, interaction: discord.Interaction, button: MinesButton):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Đây không phải ván chơi của bạn!", ephemeral=True)
            return
        if self.game_over:
            await interaction.response.send_message("❌ Ván chơi đã kết thúc!", ephemeral=True)
            return

        index = button.index

        if index in self.mines:
            self.game_over = True
            for child in self.children:
                if isinstance(child, MinesButton):
                    child.disabled = True
                    if child.index in self.mines:
                        child.emoji = "💣"
                        child.label = ""
                        child.style = discord.ButtonStyle.danger
                    else:
                        child.emoji = "💎"
                        child.label = ""
                        child.style = discord.ButtonStyle.success
                elif isinstance(child, discord.ui.Button) and child.label == "Cash Out":
                    child.disabled = True

            u_data = get_user(str(self.ctx.author.id))
            new_coin = max(0, u_data[0] - self.bet)
            update_user_full(str(self.ctx.author.id), new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])

            embed = discord.Embed(
                title=f"💥 **{self.ctx.author.display_name}** đã dính mìn và mất **-{self.bet:,}** {COIN_EMOJI}!",
                description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Winnings:** `0 (0.00x)`",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        else:
            self.revealed[index] = True
            self.diamonds_found += 1
            current_mult = self.calculate_multiplier()

            button.emoji = "💎"
            button.label = ""
            button.style = discord.ButtonStyle.success
            button.disabled = True

            max_safe = self.grid_size - self.mines_count
            if self.diamonds_found >= max_safe:
                await self.do_cash_out(interaction, win_multiplier=current_mult)
                return

            next_mult = round(math.prod([(self.grid_size - i) / (self.grid_size - self.mines_count - i) for i in range(self.diamonds_found + 1)]) * 0.95, 2)

            embed = discord.Embed(
                title=f"💎 **{self.ctx.author.display_name}** đang dò mìn...",
                description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Cash Out:** `{int(self.bet * current_mult):,}` (`{current_mult}x`)\n**Next:** `+{int(self.bet * next_mult):,}` (`{next_mult}x`)",
                color=MAIN_COLOR
            )
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success, row=3)
    async def cashout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("❌ Đây không phải ván chơi của bạn!", ephemeral=True)
            return
        if self.game_over:
            await interaction.response.send_message("❌ Ván chơi đã kết thúc!", ephemeral=True)
            return
        if self.diamonds_found == 0:
            await interaction.response.send_message("❌ Bạn phải lật ít nhất 1 ô an toàn trước khi Cash Out!", ephemeral=True)
            return

        await self.do_cash_out(interaction, win_multiplier=self.calculate_multiplier())

    async def do_cash_out(self, interaction, win_multiplier):
        self.game_over = True
        winnings = int(self.bet * win_multiplier)
        net_profit = winnings - self.bet

        u_data = get_user(str(self.ctx.author.id))
        new_coin = u_data[0] + net_profit
        update_user_full(str(self.ctx.author.id), new_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])
        add_exp_to_user(str(self.ctx.author.id), 50)

        for child in self.children:
            if isinstance(child, MinesButton):
                child.disabled = True
                child.emoji = "💣" if child.index in self.mines else "💎"
                child.label = ""
            elif isinstance(child, discord.ui.Button) and child.label == "Cash Out":
                child.style = discord.ButtonStyle.secondary
                child.disabled = True

        embed = discord.Embed(
            title=f"💎 **{self.ctx.author.display_name}** cashed out thành công!",
            description=f"**Bet:** `{self.bet}` | **Mines:** `{self.mines_count}`\n**Lãi:** `+{net_profit:,}` {COIN_EMOJI} (`{win_multiplier}x`)",
            color=discord.Color.green()
        )
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            await interaction.message.edit(embed=embed, view=self)
        self.stop()


# ==========================================
# COG — các lệnh
# ==========================================
class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- TÀI XỈU ----------
    @commands.hybrid_command(name='sicbo', aliases=['taixiu'])
    async def cmd_tx(self, ctx):
        """Chơi Tài Xỉu."""
        view = TaiXiuButtonsView(ctx)

        def get_tx_desc(sec):
            return (
                f"🎲 **Tài Xỉu 💤KS-BOT💤 - Nhà cái đến từ Châu Á!** 🔥🔥🔥\n\n"
                f"Chọn Tài (11–18), Xỉu (3–10), Chẵn/Lẻ hoặc số cụ thể (3–18) để đặt cược.\n"
                f"Sau khi chọn, nhập số gold hoặc `all` muốn cược.\n"
                f"⏳ Thời gian đặt cược còn lại: **{sec} giây**."
            )

        embed = discord.Embed(title=f"TÀI XỈU #{random.randint(1000, 9999)}", description=get_tx_desc(35), color=0x4a2e80)
        msg = await ctx.send(embed=embed, view=view)

        for remaining in range(34, -1, -1):
            await asyncio.sleep(1)
            if view.is_finished():
                break
            embed.description = get_tx_desc(remaining)
            try:
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass

        view.stop()

        shaking_embed = discord.Embed(
            title="🎲 ĐANG LẮC XÚC XẮC...",
            description="🎲 🎲 🎲\nNhà cái đang xóc đĩa, xin vui lòng chờ kết quả...",
            color=0x4a2e80
        )
        try:
            await msg.edit(embed=shaking_embed, view=None)
        except Exception:
            pass

        await asyncio.sleep(6)

        d1, d2, d3 = random.randint(1, 6), random.randint(1, 6), random.randint(1, 6)
        total = d1 + d2 + d3
        is_tai = total >= 11
        is_chan = total % 2 == 0

        dice_emojis = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        dice_str = f"{dice_emojis[d1]} {dice_emojis[d2]} {dice_emojis[d3]}"
        ket_qua_tx = "Tài" if is_tai else "Xỉu"
        ket_qua_cl = "Chẵn" if is_chan else "Lẻ"

        result_desc = (
            f"Kết quả:\n"
            f"{dice_str} = {total}\n"
            f"Tài/Xỉu: **{ket_qua_tx}**\n"
            f"Chẵn/Lẻ: **{ket_qua_cl}**\n\n"
            f"**Tổng kết:**\n"
        )

        if not view.bets:
            result_desc += "Không có ai tham gia đặt cược ván này."
        else:
            for uid, user_bets in view.bets.items():
                u_data = get_user(uid)
                coin, level, exp, streak, last_time, history = u_data

                total_bet_user = sum(user_bets.values())
                coin = max(0, coin - total_bet_user)

                total_win_payout = 0
                bet_descriptions = []

                for choice_name, bet_amt in user_bets.items():
                    bet_descriptions.append(f"**{choice_name}** ({bet_amt:,})")

                    won = False
                    multiplier = 1.0

                    c_lower = choice_name.lower()
                    if "tài" in c_lower and is_tai:
                        won = True
                    elif "xỉu" in c_lower and not is_tai:
                        won = True
                    elif "chẵn" in c_lower and is_chan:
                        won = True
                    elif "lẻ" in c_lower and not is_chan:
                        won = True
                    elif "số" in c_lower:
                        try:
                            num_val = int("".join([c for c in choice_name if c.isdigit()]))
                            if num_val == total:
                                won = True
                                multiplier = 10.0
                        except Exception:
                            pass

                    if won:
                        total_win_payout += int(bet_amt * multiplier) + bet_amt

                net_profit = total_win_payout - total_bet_user
                coin += total_win_payout
                update_user_full(uid, coin, level, exp, streak, last_time, history)
                add_exp_to_user(uid, 60)

                bets_str_joined = " và ".join(bet_descriptions)
                if net_profit > 0:
                    result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Lụm **+{net_profit:,}** {COIN_EMOJI} (Tổng nhận: {total_win_payout:,})\n"
                elif net_profit < 0:
                    result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Toạch **-{total_bet_user:,}** {COIN_EMOJI}\n"
                else:
                    result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Hòa vốn.\n"

        try:
            await msg.edit(embed=discord.Embed(description=result_desc, color=0x2b5345), view=None)
        except Exception:
            pass

    # ---------- BẦU CUA ----------
    @commands.hybrid_command(name='baucua')
    async def cmd_baucua(self, ctx):
        """Chơi Bầu Cua."""
        view = BauCuaButtonsView(ctx)

        def get_bc_desc(sec):
            return (
                f"🎲 **Bầu Cua 💤KS-BOT💤 - Nhà cái đến từ Việt Nam!** 🔥🔥🔥\n\n"
                f"Chọn bầu, cua, tôm, cá, gà, hoặc nai để cược (nhập số hoặc `all`, tối đa 250,000 {COIN_EMOJI}).\n"
                f"⏳ Thời gian đếm ngược đặt cược: **{sec} giây**."
            )

        embed = discord.Embed(description=get_bc_desc(30), color=0x2b5345)
        msg = await ctx.send(embed=embed, view=view)

        for remaining in range(29, -1, -1):
            await asyncio.sleep(1)
            if view.is_finished():
                break
            embed.description = get_bc_desc(remaining)
            try:
                await msg.edit(embed=embed, view=view)
            except Exception:
                pass

        view.stop()

        shaking_embed = discord.Embed(
            title="🎲 ĐANG LẮC BẦU CUA...",
            description="🎲 Xóc xóc xóc... Nhà cái đang mở lồng bàn, chờ 10 giây...",
            color=0x2b5345
        )
        try:
            await msg.edit(embed=shaking_embed, view=None)
        except Exception:
            pass

        await asyncio.sleep(10)

        result_dice = [random.choice(BC_ANIMALS) for _ in range(3)]
        dice_display = f"{BC_EMOJIS[result_dice[0]]} {BC_EMOJIS[result_dice[1]]} {BC_EMOJIS[result_dice[2]]}"

        result_desc = f"{dice_display}\n**Tổng kết:**\n"

        if not view.bets:
            result_desc += "Không có ai tham gia đặt cược ván này."
        else:
            for uid, user_bets in view.bets.items():
                u_data = get_user(uid)
                coin, level, exp, streak, last_time, history = u_data

                total_bet_user = sum(user_bets.values())
                coin = max(0, coin - total_bet_user)

                total_win_payout = 0
                bet_descriptions = []

                for choice_name, bet_amt in user_bets.items():
                    bet_descriptions.append(f"{choice_name} ({bet_amt:,})")
                    matches = result_dice.count(choice_name)
                    if matches > 0:
                        total_win_payout += bet_amt + (bet_amt * matches)

                net_profit = total_win_payout - total_bet_user
                coin += total_win_payout
                update_user_full(uid, coin, level, exp, streak, last_time, history)
                add_exp_to_user(uid, 50)

                bets_str_joined = " và ".join(bet_descriptions)
                if total_win_payout > 0:
                    result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Lụm **+{net_profit:,}** {COIN_EMOJI} (Tổng nhận: {total_win_payout:,})\n"
                else:
                    result_desc += f"<@!{uid}> cược {bets_str_joined} ➔ Toạch **-{total_bet_user:,}** {COIN_EMOJI}\n"

        try:
            await msg.edit(embed=discord.Embed(description=result_desc, color=0x2b5345), view=None)
        except Exception:
            pass

    # ---------- TÙ XÌ ----------
    @commands.hybrid_command(name='rps', aliases=['tuxi'])
    async def cmd_tuxi(self, ctx, bet_str: str = "100"):
        """Chơi Kéo Búa Bao, cược coin."""
        user_id = str(ctx.author.id)
        u_data = get_user(user_id)

        bet = parse_bet_amount(u_data[0], bet_str)
        if bet is None or bet <= 0:
            await ctx.send("❌ Số tiền cược không hợp lệ!", delete_after=5)
            return

        if u_data[0] < bet:
            await ctx.send(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", delete_after=5)
            return

        view = TuXiView(ctx.author, bet)

        desc = (
            f"Trò chơi Kéo Búa Bao. Chọn chiến thuật của bạn!\n\n"
            f"Mức cược: **{bet:,}** {COIN_EMOJI}. Hãy bấm vào nút bên dưới để chọn."
        )

        msg = await ctx.send(embed=discord.Embed(description=desc, color=discord.Color.blue()), view=view)

        await view.wait()

        current_coin = get_user(user_id)[0]
        current_coin = max(0, current_coin - bet)
        update_user_full(user_id, current_coin, u_data[1], u_data[2], u_data[3], u_data[4], u_data[5])

        if view.choice is None:
            await msg.edit(
                content=f"{ctx.author.mention} đã hết thời gian chờ và bị xử thua mất **-{bet:,}** {COIN_EMOJI}!",
                embed=None,
                view=None
            )
            return

        bot_choice = random.choice(["búa", "kéo", "bao"])
        user_choice = view.choice
        emojis = {"búa": "✊", "kéo": "✌️", "bao": "🖐️"}

        u_data_updated = get_user(user_id)
        c_coin = u_data_updated[0]

        if user_choice == bot_choice:
            c_coin += bet
            result_msg = f"Hòa! Được hoàn lại {bet:,} {COIN_EMOJI}."
        elif (
            (user_choice == "búa" and bot_choice == "kéo") or
            (user_choice == "kéo" and bot_choice == "bao") or
            (user_choice == "bao" and bot_choice == "búa")
        ):
            c_coin += bet * 2
            result_msg = f"Thắng! Bạn nhận được **+{bet:,}** {COIN_EMOJI}."
        else:
            result_msg = f"Thua! Bạn mất **{-bet:,}** {COIN_EMOJI}."

        update_user_full(user_id, c_coin, u_data_updated[1], u_data_updated[2], u_data_updated[3], u_data_updated[4], u_data_updated[5])
        add_exp_to_user(user_id, 30)

        final_content = (
            f"{ctx.author.mention} đã tham gia game Kéo Búa Bao!\n"
            f"**Kết quả:** Bot chọn {emojis[bot_choice]} (`{bot_choice}`), bạn chọn {emojis[user_choice]} (`{user_choice}`). {result_msg}"
        )
        await msg.edit(content=final_content, embed=None, view=None)

    # ---------- MINES ----------
    @commands.hybrid_command(name='mine', aliases=['mines'])
    async def cmd_mine(self, ctx, bet_str: str = None, boom_str: str = None):
        """Chơi Mines, cược coin."""
        u_data = get_user(str(ctx.author.id))
        bet = parse_bet_amount(u_data[0], bet_str)

        if bet is None or bet <= 0:
            await ctx.send("❌ Cú pháp: `.mine <tiền_cược/all> [số_boom]`", delete_after=6)
            return

        if u_data[0] < bet:
            await ctx.send(f"❌ Bạn không đủ tiền! Ví hiện có: `{u_data[0]:,}` {COIN_EMOJI}", delete_after=5)
            return

        mines_count = 3
        if boom_str and boom_str.isdigit():
            mines_count = int(boom_str)
            if mines_count < 1 or mines_count >= 9:
                await ctx.send("❌ Số boom phải từ 1 đến 8!", delete_after=5)
                return

        view = MinesView(ctx, bet, mines_count)
        embed = discord.Embed(
            title=f"💣 **{ctx.author.display_name}** đã bắt đầu một ván Dò Mìn.",
            description=f"**Bet:** `{bet}` | **Mines:** `{mines_count}`\n**Cash Out:** `0 (0.00x)`\n**Next:** `14 (1.40x)`",
            color=MAIN_COLOR
        )
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Casino(bot))
