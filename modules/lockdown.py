import discord
from discord.ext import commands
from discord import app_commands
from module_utils import Module, load_server_data, save_server_data
from modules.core import is_moderator

def get_lockdown_data(guild_id: int):
    return load_server_data(guild_id, "lockdown.json") or {"channels": {}, "sets": {}}

def save_lockdown_data(guild_id: int, data: dict):
    save_server_data(guild_id, "lockdown.json", data)

@Module.enabled()
@Module.help(
    commands={
        "lockdown": "Locks every text channel and forum",
        "lockdown hide": "Locks every channel and hides them",
        "lockdown lock [channel]": "Locks target or current channel",
        "lockdown unlock [channel/set]": "Unlocks channel or custom set",
        "lockdown category [category]": "Locks target or current category",
        "lockdown create <name> <channels>": "Creates a custom set of channels",
        "lockdown <set>": "Locks the specified custom set"
    },
    description="Advanced channel lockdown operations."
)
class Lockdown(commands.Cog):
    lockdown_group_slash = app_commands.Group(name="lockdown", description="Lockdown module commands")

    def __init__(self, bot):
        self.bot = bot

    async def _send_or_reply(self, target, content, ephemeral=False):
        if isinstance(target, discord.Interaction):
            if target.response.is_done(): await target.followup.send(content, ephemeral=ephemeral)
            else: await target.response.send_message(content, ephemeral=ephemeral)
        else: await target.reply(content)

    async def _lock_channel(self, channel, hide=False):
        if not channel.guild.me.guild_permissions.manage_roles and not channel.guild.me.guild_permissions.administrator:
            raise commands.CheckFailure("I need 'Manage Roles' or 'Administrator' permission to adjust channel locks.")
        data = get_lockdown_data(channel.guild.id)
        ch_id = str(channel.id)
        overwrite = channel.overwrites_for(channel.guild.default_role)
        if ch_id not in data["channels"]:
            data["channels"][ch_id] = {"send_messages": overwrite.send_messages, "view_channel": overwrite.view_channel}
            save_lockdown_data(channel.guild.id, data)
        overwrite.send_messages = False
        if hide: overwrite.view_channel = False
        bot_overwrite = channel.overwrites_for(channel.guild.me)
        bot_overwrite.view_channel, bot_overwrite.send_messages, bot_overwrite.manage_permissions = True, True, True
        try:
            await channel.set_permissions(channel.guild.me, overwrite=bot_overwrite)
            await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
        except discord.Forbidden:
            raise commands.CheckFailure(f"I lack permissions to edit overwrites in {channel.mention}. Check my role hierarchy position!")

    async def _unlock_channel(self, channel):
        data = get_lockdown_data(channel.guild.id)
        ch_id = str(channel.id)
        overwrite = channel.overwrites_for(channel.guild.default_role)
        if ch_id in data["channels"]:
            orig = data["channels"][ch_id]
            overwrite.send_messages, overwrite.view_channel = orig.get("send_messages"), orig.get("view_channel")
            del data["channels"][ch_id]
            save_lockdown_data(channel.guild.id, data)
            kwargs = {}
            if overwrite.send_messages is not None: kwargs['send_messages'] = overwrite.send_messages
            if overwrite.view_channel is not None: kwargs['view_channel'] = overwrite.view_channel
            try:
                if not kwargs and overwrite.is_empty(): await channel.set_permissions(channel.guild.default_role, overwrite=None)
                else: await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
            except: pass
        else:
            overwrite.send_messages, overwrite.view_channel = None, None
            try: await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
            except: pass

    # ─── Slash Commands ───────────────────────────────────────────────────────
    @lockdown_group_slash.command(name="server", description="Locks every text channel and forum")
    async def ld_server_slash(self, interaction: discord.Interaction, hide: bool = False):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await interaction.response.defer()
        count = 0
        for channel in interaction.guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                try:
                    await self._lock_channel(channel, hide=hide)
                    count += 1
                except: pass
        await interaction.followup.send(f"🔒 Server locked down ({count} channels). Hidden: {hide}")

    @lockdown_group_slash.command(name="channel", description="Locks the current or target channel")
    async def ld_channel_slash(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel = None, hide: bool = False):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        target = channel or interaction.channel
        try:
            await self._lock_channel(target, hide=hide)
            await interaction.response.send_message(f"🔒 Locked {target.mention}. Hidden: {hide}")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

    @lockdown_group_slash.command(name="unlock", description="Unlocks target channel or custom set")
    async def ld_unlock_slash(self, interaction: discord.Interaction, target_name: str = None):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await interaction.response.defer()
        if target_name:
            data = get_lockdown_data(interaction.guild_id)
            if target_name in data["sets"]:
                for c_id in data["sets"][target_name]:
                    ch = interaction.guild.get_channel(int(c_id))
                    if ch: await self._unlock_channel(ch)
                return await interaction.followup.send(f"🔓 Unlocked set `{target_name}`.")
            return await interaction.followup.send(f"❌ Set `{target_name}` not found.")
        await self._unlock_channel(interaction.channel)
        await interaction.followup.send(f"🔓 Unlocked {interaction.channel.mention}.")

    # ─── Prefix Commands ──────────────────────────────────────────────────────
    @commands.group(name="lockdown", invoke_without_command=True)
    async def lockdown_group(self, ctx, *, set_name: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        if set_name:
            data = get_lockdown_data(ctx.guild.id)
            if set_name in data["sets"]:
                for ch_id in data["sets"][set_name]:
                    ch = ctx.guild.get_channel(int(ch_id))
                    if ch: await self._lock_channel(ch)
                return await ctx.reply(f"✅ Locked custom set `{set_name}`.")
        locked_count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                try:
                    await self._lock_channel(ch); locked_count += 1
                except: pass
        await ctx.reply(f"🔒 Server locked down ({locked_count} channels).")

    @lockdown_group.command(name="hide")
    async def ld_hide_prefix(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        count = 0
        for ch in ctx.guild.channels:
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                try:
                    await self._lock_channel(ch, hide=True); count += 1
                except: pass
        await ctx.reply(f"🔒🙈 Server locked and hidden ({count} channels).")

    @lockdown_group.command(name="lock")
    async def ld_lock_prefix(self, ctx, channel: discord.abc.GuildChannel = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        target = channel or ctx.channel
        try:
            await self._lock_channel(target)
            await ctx.reply(f"🔒 Locked {target.mention}.")
        except Exception as e:
            await ctx.reply(f"❌ Failed: {e}")

    @lockdown_group.command(name="unlock")
    async def ld_unlock_prefix(self, ctx, *, target_str: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        if target_str:
            data = get_lockdown_data(ctx.guild.id)
            if target_str in data["sets"]:
                for c_id in data["sets"][target_str]:
                    ch = ctx.guild.get_channel(int(c_id))
                    if ch: await self._unlock_channel(ch)
                return await ctx.reply(f"✅ Unlocked set `{target_str}`.")
            try:
                target_chan = await commands.GuildChannelConverter().convert(ctx, target_str)
                await self._unlock_channel(target_chan)
                return await ctx.reply(f"🔓 Unlocked {target_chan.mention}.")
            except: pass
        await self._unlock_channel(ctx.channel)
        await ctx.reply(f"🔓 Unlocked {ctx.channel.mention}.")

    @lockdown_group.command(name="category")
    async def ld_category_prefix(self, ctx, category: discord.CategoryChannel = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        target_cat = category or getattr(ctx.channel, "category", None)
        if not target_cat: return await ctx.reply("❌ No target category.")
        count = 0
        for ch in target_cat.channels:
            if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                try:
                    await self._lock_channel(ch); count += 1
                except: pass
        await ctx.reply(f"🔒 Locked category `{target_cat.name}` ({count} channels).")

    @lockdown_group.command(name="create")
    async def ld_create_prefix(self, ctx, name: str):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        channels = ctx.message.channel_mentions
        if not channels: return await ctx.reply("⚠️ Mention channels.")
        data = get_lockdown_data(ctx.guild.id)
        data["sets"][name] = [str(c.id) for c in channels]
        save_lockdown_data(ctx.guild.id, data)
        await ctx.reply(f"✅ Created set `{name}` with {len(channels)} channels.")

async def setup(bot):
    await bot.add_cog(Lockdown(bot))
