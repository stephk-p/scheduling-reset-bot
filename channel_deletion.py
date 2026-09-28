"""
Permanently deleting a channel (not just its messages). Used by both the
manual /deletechannel command and the scheduler, so both behave the same way:
log the channel's message history (if the server has a log channel set),
then delete the channel outright, then clean up any settings that referenced
its ID (since that ID is now meaningless).

This is irreversible. There is no "undo" and no recovery once channel.delete()
succeeds — Discord does not support restoring a deleted channel.
"""
from __future__ import annotations

import discord

import deletion_log


async def fetch_full_history(channel) -> list:
    """Every message currently in the channel, oldest first. Best-effort."""
    messages = []
    async for m in channel.history(limit=None, oldest_first=True):
        messages.append(m)
    return messages


async def delete_channel_entirely(bot, channel, source: str) -> None:
    """
    Log the channel's history (if a log channel is set for this server), then
    delete the channel, then remove its ID from every setting that referenced
    it. `source` says what caused it, e.g. "scheduled reset" or
    "/deletechannel by Alex".
    """
    guild = channel.guild
    gs = bot.store.get(guild.id)

    if gs.log_channel and gs.log_channel != channel.id:
        try:
            messages = await fetch_full_history(channel)
        except discord.HTTPException as e:
            print(f"[WARN] couldn't read {channel.name}'s history before deleting it: {e}")
            messages = []
        # A note distinguishes this from an ordinary message clear: the
        # channel itself is about to stop existing, not just get emptied.
        await deletion_log.send_deletion_log(
            bot, channel, messages, f"{source} (channel permanently deleted)"
        )

    await channel.delete(reason=f"Deleted via {source}"[:512])   # Discord's audit-log reason cap

    gs.forget_channel(channel.id)
    bot.store.commit(gs)
    print(f"[OK]   {guild.name}: permanently deleted #{channel.name} ({channel.id}) via {source}")
