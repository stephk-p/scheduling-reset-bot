"""
/clearchannel: pick a channel, then choose whether to CLEAR ITS MESSAGES right now
or queue it to be cleared once at the next scheduled run. The channel itself is
never touched — only its messages. Works on any text channel in the server,
whether or not it's on the permanent /resetchannels list. Needs the Manage Server
permission.

For deleting an entire channel (not just its messages), see /deletechannel in
channel_delete_commands.py.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import clearing
import deletion_log
import timeutils
from cogs._common import reply, save, settings_for


class ClearChannelView(discord.ui.View):
    """The Clear Messages Now / Queue Messages for Next Run / Cancel buttons."""

    def __init__(self, bot, origin: discord.Interaction, channel: discord.TextChannel):
        super().__init__(timeout=60)
        self.bot = bot
        self.origin = origin           # the original /deletechannel interaction
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.origin.user.id   # only whoever ran the command

    @discord.ui.button(label="Clear Messages Now", style=discord.ButtonStyle.danger)
    async def delete_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content=f"Clearing messages in {self.channel.mention}...", view=None)
        try:
            messages = await clearing.clear_channel(self.channel)
        except discord.Forbidden:
            return await interaction.edit_original_response(
                content=f"I'm missing permissions in {self.channel.mention} (Manage Messages, Read Message History)."
            )
        except discord.HTTPException as e:
            return await interaction.edit_original_response(content=f"Failed (Discord error {e.status}).")
        await deletion_log.send_deletion_log(self.bot, self.channel, messages, f"/clearchannel by {interaction.user}")
        n = len(messages)
        print(f"[OK]   /clearchannel (now) by {interaction.user} ({interaction.user.id}) in server "
              f"{interaction.guild_id}: {self.channel.name}, {n} message(s)")
        await interaction.edit_original_response(
            content=f"**Done.** Deleted {n} message(s) in {self.channel.mention}." if n
            else f"**Done.** {self.channel.mention} was already empty."
        )

    @discord.ui.button(label="Queue Messages for Next Run", style=discord.ButtonStyle.primary)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        gs = settings_for(interaction)
        if self.channel.id == gs.log_channel:
            return await interaction.response.edit_message(
                content=f"{self.channel.mention} is this server's deletion log channel and can't be cleared.",
                view=None,
            )
        already = self.channel.id in gs.queued or self.channel.id in gs.channels
        if self.channel.id not in gs.queued:
            gs.queued.append(self.channel.id)
            save(interaction, gs)
        runs = gs.upcoming_runs(1)
        when = timeutils.format_run(gs, runs[0]) if runs else "the next scheduled run (none is currently set)"
        note = " (it's already covered by the regular schedule)" if already and self.channel.id in gs.channels else ""
        await interaction.response.edit_message(
            content=f"Queued. {self.channel.mention} will be cleared once at {when}{note}.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)

    async def on_timeout(self):
        try:
            await self.origin.edit_original_response(content="Timed out. Nothing was changed.", view=None)
        except discord.HTTPException:
            pass


class ClearChannelCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="clearchannel", description="Clear a channel's messages now, or queue it for the next scheduled run (channel itself is kept)")
    @app_commands.describe(channel="The channel to clear")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clearchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if channel.guild.id != interaction.guild_id:
            return await reply(interaction, "That channel isn't in this server.")
        view = ClearChannelView(self.bot, interaction, channel)
        await interaction.response.send_message(
            f"What do you want to do with {channel.mention}?\n"
            "**Clear Messages Now** removes its messages immediately, but keeps the channel. "
            "**Queue Messages for Next Run** clears it once, the next time the schedule fires, without "
            "adding it to the permanent list. Either way, the channel itself is never deleted.",
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ClearChannelCommands(bot))
