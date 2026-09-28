"""
The scheduler. Every few seconds it checks each server's clock and, when a
server's reset is due, empties that server's channels (only if they contain
messages). By default it deletes everything; /resetamount can limit it to the
newest N messages. It has no slash commands; those live in the other files.
"""
from __future__ import annotations

import discord
from discord.ext import commands, tasks

import channel_deletion
import clearing
import config
import deletion_log
from settings import migrate_legacy


class Scheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._test_done = False

    async def cog_load(self):
        self.check_schedules.start()

    async def cog_unload(self):
        self.check_schedules.cancel()

    # ---- the clock check ---------------------------------------------------

    @tasks.loop(seconds=config.CHECK_INTERVAL)
    async def check_schedules(self):
        # Reads each server's current settings every time, so any change made
        # with a slash command takes effect immediately.
        for guild in list(self.bot.guilds):
            try:
                gs = self.bot.store.get(guild.id)
                now = gs.now()
                if not gs.is_due(now):
                    continue
                gs.last_run = now.date()   # mark first so a slow purge can't double-trigger
                if not gs.channels and not gs.queued and not gs.delete_queue:
                    continue
                print(f"[{now:%Y-%m-%d %H:%M:%S %Z}] running reset for {guild.name} ({guild.id})")
                await self.delete_queued_channels(guild, gs)   # entire deletions happen first
                await self.reset_guild(guild, gs)
                if gs.queued:
                    gs.queued = []
                    self.bot.store.commit(gs)
            except Exception as e:
                print(f"[ERR]  {guild.id}: {type(e).__name__}: {e}")

    @check_schedules.before_loop
    async def before_check_schedules(self):
        await self.bot.wait_until_ready()   # channels aren't cached until the bot is ready
        migrate_legacy(self.bot, self.bot.store)

    @commands.Cog.listener()
    async def on_ready(self):
        # `python bot.py --test` runs one reset for every server straight away.
        if self.bot.test_mode and not self._test_done:
            self._test_done = True
            print("--test flag set: running one reset now")
            for guild in list(self.bot.guilds):
                await self.reset_guild(guild, self.bot.store.get(guild.id))

    # ---- the actual clearing -----------------------------------------------

    async def delete_queued_channels(self, guild, gs):
        """Permanently delete every channel queued for full deletion, in this server."""
        for channel_id in list(gs.delete_queue):
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                owner = getattr(channel, "guild", None)
                if owner is None or owner.id != guild.id:
                    print(f"[WARN] {guild.name}: queued-for-deletion channel {channel_id} isn't in this server, skipped")
                    gs.delete_queue = [c for c in gs.delete_queue if c != channel_id]
                    self.bot.store.commit(gs)
                    continue
                await channel_deletion.delete_channel_entirely(self.bot, channel, "scheduled reset")
            except discord.NotFound:
                # Already gone (deleted by hand, etc.) — just drop it from the queue.
                gs.delete_queue = [c for c in gs.delete_queue if c != channel_id]
                self.bot.store.commit(gs)
            except Exception as e:
                print(f"[ERR]  {guild.name} channel {channel_id} (scheduled deletion): {type(e).__name__}: {e}")

    async def reset_guild(self, guild, gs):
        # The regular list uses gs.amount; queued channels are always cleared
        # completely, since queuing one is a deliberate one-off action. Anything
        # already handled by delete_queued_channels() is gone, so it's naturally
        # skipped here (forget_channel() removed it from these lists too).
        targets = [(cid, gs.amount) for cid in gs.channels] + [(cid, None) for cid in gs.queued]
        if not targets:
            print(f"[WARN] {guild.name}: no channels configured, nothing to reset")
            return
        seen = set()
        for channel_id, amount in targets:
            if channel_id in seen:
                continue   # e.g. queued while already on the permanent list; clear it only once
            seen.add(channel_id)
            if channel_id == gs.log_channel:
                print(f"[WARN] {guild.name}: channel {channel_id} is both a reset channel and the log channel, skipped")
                continue
            # One bad channel (deleted, no permissions, etc.) shouldn't stop the rest.
            try:
                await self.reset_channel(guild, channel_id, amount)
            except Exception as e:
                print(f"[ERR]  {guild.name} channel {channel_id}: {type(e).__name__}: {e}")

    async def reset_channel(self, guild, channel_id: int, amount: int | None = None):
        """Clear one channel. amount=None deletes everything; a number deletes that many of the newest."""
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)

        # Safety: a server's list may only ever clear that server's own channels.
        owner = getattr(channel, "guild", None)
        if owner is None or owner.id != guild.id:
            print(f"[WARN] {guild.name}: channel {channel_id} isn't in this server, skipped")
            return

        label = f"{guild.name} #{channel.name} ({channel.id})"
        messages = await clearing.clear_channel(channel, amount)
        deleted = len(messages)
        await deletion_log.send_deletion_log(self.bot, channel, messages, "scheduled reset")

        if amount is not None:
            print(f"[OK]   {label}: deleted {deleted} of the newest {amount} messages")
        elif await clearing.has_messages(channel):
            print(f"[WARN] {label}: still has messages after {config.MAX_PURGE_PASSES} passes")
        elif deleted:
            print(f"[OK]   {label}: deleted {deleted} messages")
        else:
            print(f"[OK]   {label}: already empty, nothing to delete")


async def setup(bot):
    await bot.add_cog(Scheduler(bot))
