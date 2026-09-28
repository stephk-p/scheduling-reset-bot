"""
/deletechannel: PERMANENTLY delete an entire channel — not just its messages.
This is irreversible. Gives two options, both requiring you to type the
channel's name to confirm:
    Delete Channel Now       — deletes it immediately
    Queue Deletion for Next Run — deletes it automatically at the next
                                  scheduled reset, with no further confirmation
                                  at that moment
Needs the Manage Server AND Manage Channels permissions.

For clearing a channel's messages while keeping the channel itself, see
/clearchannel in clearchannel_commands.py.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import channel_deletion
import timeutils
from cogs._common import reply, save, settings_for

WARNING = (
    "This will **permanently delete** {mention} — not just its messages. The channel, its "
    "permission overrides, and its position cannot be recovered once this runs.\n\n"
    "**Delete Channel Now** deletes it immediately.\n"
    "**Queue Deletion for Next Run** deletes it automatically at the next scheduled reset, "
    "with **no further confirmation** at that moment.\n\n"
    "Either option asks you to type the channel's name first, to confirm."
)


class ConfirmDeleteModal(discord.ui.Modal):
    """Requires typing the channel's exact name before anything destructive happens."""

    def __init__(self, bot, channel: discord.TextChannel, action: str, origin: discord.Interaction):
        super().__init__(title="Type the channel name to confirm")
        self.bot = bot
        self.channel = channel
        self.action = action   # "now" or "queue"
        self.origin = origin
        self.typed_name = discord.ui.TextInput(
            label=f"Channel name (#{channel.name})",
            placeholder=channel.name,
            min_length=1,
            max_length=100,
        )
        self.add_item(self.typed_name)

    async def on_submit(self, interaction: discord.Interaction):
        typed = self.typed_name.value.strip().lstrip("#").lower()
        if typed != self.channel.name.lower():
            return await interaction.response.send_message(
                f"That didn't match `#{self.channel.name}`, so nothing was changed.", ephemeral=True
            )

        if self.action == "now":
            await interaction.response.send_message(f"Deleting {self.channel.mention}...", ephemeral=True)
            try:
                await channel_deletion.delete_channel_entirely(
                    self.bot, self.channel, f"/deletechannel by {interaction.user}"
                )
                await interaction.followup.send(f"**Done.** #{self.channel.name} has been permanently deleted.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(
                    "I'm missing the **Manage Channels** permission, so I couldn't delete it.", ephemeral=True
                )
            except discord.HTTPException as e:
                await interaction.followup.send(f"Failed to delete it (Discord error {e.status}).", ephemeral=True)
            return

        # action == "queue"
        gs = settings_for(interaction)
        if self.channel.id not in gs.delete_queue:
            gs.delete_queue.append(self.channel.id)
        # Deletion supersedes clearing: no point clearing a channel about to be destroyed.
        gs.channels = [c for c in gs.channels if c != self.channel.id]
        gs.queued = [c for c in gs.queued if c != self.channel.id]
        save(interaction, gs)
        runs = gs.upcoming_runs(1)
        when = timeutils.format_run(gs, runs[0]) if runs else "the next scheduled run (none is currently set — set one with /resetdays)"
        await reply(
            interaction,
            f"Queued. #{self.channel.name} will be **permanently deleted** at {when}. "
            "This will happen automatically, with no further confirmation.\n"
            "To back out before then, run `/deletechannel` on it again and cancel, "
            "or ask an admin to check `/resetdays view`.",
        )


class DeleteChannelView(discord.ui.View):
    def __init__(self, bot, origin: discord.Interaction, channel: discord.TextChannel):
        super().__init__(timeout=60)
        self.bot = bot
        self.origin = origin
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.origin.user.id

    async def _collapse(self, note: str):
        try:
            await self.origin.edit_original_response(content=note, view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Delete Channel Now", style=discord.ButtonStyle.danger)
    async def delete_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_modal(ConfirmDeleteModal(self.bot, self.channel, "now", self.origin))
        await self._collapse(f"Confirming deletion of #{self.channel.name} in the popup...")

    @discord.ui.button(label="Queue Deletion for Next Run", style=discord.ButtonStyle.danger)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_modal(ConfirmDeleteModal(self.bot, self.channel, "queue", self.origin))
        await self._collapse(f"Confirming queued deletion of #{self.channel.name} in the popup...")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)

    async def on_timeout(self):
        await self._collapse("Timed out. Nothing was changed.")


class ChannelDeleteCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="deletechannel", description="PERMANENTLY delete an entire channel, now or at the next scheduled run")
    @app_commands.describe(channel="The channel to delete ENTIRELY. This cannot be undone.")
    @app_commands.default_permissions(manage_guild=True, manage_channels=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True, manage_channels=True)
    async def deletechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if channel.guild.id != interaction.guild_id:
            return await reply(interaction, "That channel isn't in this server.")
        gs = settings_for(interaction)
        if channel.id == gs.log_channel:
            return await reply(
                interaction,
                f"{channel.mention} is this server's deletion log channel, so it can't be deleted this way. "
                "Change the log channel first with `/resetlog set` or `/resetlog off`.",
            )
        view = DeleteChannelView(self.bot, interaction, channel)
        await interaction.response.send_message(WARNING.format(mention=channel.mention), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ChannelDeleteCommands(bot))
