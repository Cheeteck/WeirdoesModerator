import discord
from discord.ext import commands
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
    def __init__(self, bot):
        self.bot = bot

    async def _lock_channel(self, channel, hide=False):
        if not channel.guild.me.guild_permissions.manage_roles and not channel.guild.me.guild_permissions.administrator:
            raise commands.CheckFailure("I need 'Manage Roles' or 'Administrator' permission to adjust channel locks.")

        data = get_lockdown_data(channel.guild.id)
        ch_id = str(channel.id)
        
        overwrite = channel.overwrites_for(channel.guild.default_role)
        
        # Save old settings if we haven't already
        if ch_id not in data["channels"]:
            data["channels"][ch_id] = {
                "send_messages": overwrite.send_messages,
                "view_channel": overwrite.view_channel
            }
            save_lockdown_data(channel.guild.id, data)
            
        overwrite.send_messages = False
        if hide:
            overwrite.view_channel = False
            
        # Ensure bot can still see and speak here
        bot_overwrite = channel.overwrites_for(channel.guild.me)
        bot_overwrite.view_channel = True
        bot_overwrite.send_messages = True
        bot_overwrite.manage_permissions = True

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
            overwrite.send_messages = orig.get("send_messages")
            overwrite.view_channel = orig.get("view_channel")
            del data["channels"][ch_id]
            save_lockdown_data(channel.guild.id, data)
            
            # Use explicit kwargs when modifying permissions
            kwargs = {}
            if overwrite.send_messages is not None: kwargs['send_messages'] = overwrite.send_messages
            if overwrite.view_channel is not None: kwargs['view_channel'] = overwrite.view_channel
            
            try:
                if not kwargs and overwrite.is_empty():
                    await channel.set_permissions(channel.guild.default_role, overwrite=None)
                else:
                    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
            except:
                pass
        else:
            overwrite.send_messages = None
            overwrite.view_channel = None
            try:
                await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
            except:
                pass

    @commands.group(name="lockdown", invoke_without_command=True)
    async def lockdown_group(self, ctx, *, set_name: str = None):
        if not is_moderator(ctx.author):
            return await ctx.reply("❌ Permission denied.")
        
        if ctx.invoked_subcommand:
            return

        if set_name:
            data = get_lockdown_data(ctx.guild.id)
            if set_name in data["sets"]:
                count = 0
                try:
                    for ch_id in data["sets"][set_name]:
                        ch = ctx.guild.get_channel(int(ch_id))
                        if ch: 
                            await self._lock_channel(ch)
                            count += 1
                    return await ctx.reply(f"✅ Locked custom set `{set_name}` ({count} channels).")
                except commands.CheckFailure as e:
                    return await ctx.reply(f"❌ {e}")
            else:
                return await ctx.reply(f"❌ Custom set `{set_name}` not found or invalid subcommand.")
        else:
            locked_count = 0
            try:
                for channel in ctx.guild.channels:
                    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                        await self._lock_channel(channel)
                        locked_count += 1
                await ctx.reply(f"🔒 Server locked down. Locked {locked_count} channels/forums.")
            except commands.CheckFailure as e:
                await ctx.reply(f"❌ {e}")
            except Exception as e:
                await ctx.reply(f"❌ Lockdown failed: {e}")

    @lockdown_group.command(name="hide")
    async def ld_hide(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        locked_count = 0
        try:
            for channel in ctx.guild.channels:
                if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                    await self._lock_channel(channel, hide=True)
                    locked_count += 1
            await ctx.reply(f"🔒🙈 Server locked down and hidden. Affected {locked_count} channels.")
        except commands.CheckFailure as e:
            await ctx.reply(f"❌ {e}")

    @lockdown_group.command(name="lock")
    async def ld_lock(self, ctx, channel: discord.abc.GuildChannel = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        target = channel or ctx.channel
        try:
            await self._lock_channel(target)
            await ctx.reply(f"🔒 Locked {target.mention}.")
        except commands.CheckFailure as e:
            await ctx.reply(f"❌ {e}")
        except Exception as e:
            await ctx.reply(f"❌ An unexpected error occurred: {e}")

    @lockdown_group.command(name="unlock")
    async def ld_unlock(self, ctx, *, target_str: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        
        if target_str:
            data = get_lockdown_data(ctx.guild.id)
            # Check custom set
            if target_str in data.get("sets", {}):
                count = 0
                for ch_id in data["sets"][target_str]:
                    ch = ctx.guild.get_channel(int(ch_id))
                    if ch: 
                        await self._unlock_channel(ch)
                        count += 1
                return await ctx.reply(f"✅ Unlocked custom set `{target_str}` ({count} channels).")
                
            try:
                converter = commands.GuildChannelConverter()
                target_chan = await converter.convert(ctx, target_str)
                await self._unlock_channel(target_chan)
                return await ctx.reply(f"🔓 Unlocked {target_chan.mention}.")
            except:
                return await ctx.reply(f"❌ Could not find channel or custom set `{target_str}`.")
                
        await self._unlock_channel(ctx.channel)
        await ctx.reply(f"🔓 Unlocked {ctx.channel.mention}.")

    @lockdown_group.command(name="category")
    async def ld_category(self, ctx, category: discord.CategoryChannel = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        target_cat = category or getattr(ctx.channel, "category", None)
        if not target_cat:
            return await ctx.reply("❌ Could not determine target category.")
            
        locked_count = 0
        try:
            for channel in target_cat.channels:
                if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                    await self._lock_channel(channel)
                    locked_count += 1
            await ctx.reply(f"🔒 Locked {locked_count} channels in category `{target_cat.name}`.")
        except commands.CheckFailure as e:
            await ctx.reply(f"❌ {e}")
        
    @lockdown_group.command(name="create")
    async def ld_create(self, ctx, name: str, *, channels_str: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        reserved = ["hide", "lock", "unlock", "category", "create"]
        if name.lower() in reserved:
            return await ctx.reply(f"❌ `{name}` is a reserved command name.")
            
        channels = ctx.message.channel_mentions
        if not channels:
            return await ctx.reply("⚠️ You must mention at least one channel. e.g. `!lockdown create staff #staff-chat #staff-logs`")
            
        data = get_lockdown_data(ctx.guild.id)
        data["sets"][name] = [str(c.id) for c in channels]
        save_lockdown_data(ctx.guild.id, data)
        await ctx.reply(f"✅ Created custom set `{name}` with {len(channels)} channels.")

async def setup(bot):
    await bot.add_cog(Lockdown(bot))
