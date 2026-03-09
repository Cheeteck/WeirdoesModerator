import os
import inspect
import importlib
import discord
from discord.ext import commands
from discord import app_commands
from module_utils import Module, get_server_dir, load_server_data, save_server_data, is_module_enabled, enable_server_module, disable_server_module
from datetime import datetime, timedelta, timezone
import uuid
import re

def get_moderator_roles(guild_id: int):
    data = load_server_data(guild_id, "info.json") or {}
    return data.get("mod_roles", [])

def is_moderator(member: discord.Member) -> bool:
    if not member or not member.guild:
        return False
    if member == member.guild.owner:
        return True
    mod_roles = get_moderator_roles(member.guild.id)
    return any(role.id in mod_roles for role in member.roles) or member.guild_permissions.administrator

async def send_response(ctx_or_int, content=None, embed=None, view=None, ephemeral=False):
    kwargs = {}
    if content is not None: kwargs['content'] = content
    if embed is not None: kwargs['embed'] = embed
    if view is not None: kwargs['view'] = view
    
    if isinstance(ctx_or_int, discord.Interaction):
        if ephemeral: kwargs['ephemeral'] = ephemeral
        if ctx_or_int.response.is_done():
            await ctx_or_int.followup.send(**kwargs)
        else:
            await ctx_or_int.response.send_message(**kwargs)
    elif isinstance(ctx_or_int, commands.Context):
        await ctx_or_int.send(**kwargs)
    elif isinstance(ctx_or_int, discord.Message):
        await ctx_or_int.reply(**kwargs)
    else:
        await ctx_or_int.send(**kwargs)

def get_author(ctx_or_int):
    if isinstance(ctx_or_int, discord.Interaction): return ctx_or_int.user
    return getattr(ctx_or_int, 'author', None)

def parse_duration(duration_str: str) -> int:
    duration_str = duration_str.lower().strip()
    match = re.match(r'^(\d+)([smhd])$', duration_str)
    if not match: return None
    value = int(match.group(1))
    unit = match.group(2)
    if unit == 's': return value
    elif unit == 'm': return value * 60
    elif unit == 'h': return value * 3600
    elif unit == 'd': return value * 86400
    return None

def add_warning(guild_id: int, user_id: int, mod_id: int, reason: str):
    warns = load_server_data(guild_id, "warnings.json") or []
    new_warn = {
        "id": str(uuid.uuid4()),
        "userId": str(user_id),
        "reason": reason,
        "moderatorId": str(mod_id),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    warns.append(new_warn)
    save_server_data(guild_id, "warnings.json", warns)
    return len([w for w in warns if w["userId"] == str(user_id)])

def add_mute(guild_id: int, user_id: int, mod_id: int, reason: str, durationSec: int):
    mutes = load_server_data(guild_id, "mutes.json") or []
    new_mute = {
        "id": str(uuid.uuid4()),
        "userId": str(user_id),
        "reason": reason,
        "moderatorId": str(mod_id),
        "durationSec": durationSec,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    mutes.append(new_mute)
    save_server_data(guild_id, "mutes.json", mutes)

@Module.help(
    commands={
        "help": "displays help menu",
        "hwarn": "shows user's full moderation history (Discord + Minecraft)",
        "delwarn": "deletes a specific warn from a user",
        "modrole": "adds/removes a mod role",
        "kick": "kicks a user",
        "ban": "bans a user",
        "unban": "unbans a user",
        "mute": "mutes a user",
        "unmute": "unmutes a user",
        "module enable": "Enables a module in this server",
        "module disable": "Disables a module in this server"
    },
    description="Core functionality for the bot (cannot be disabled)."
)
class Core(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Logged in as {self.bot.user.name}")
        try:
            synced = await self.bot.tree.sync()
            print(f"✅ Synced {len(synced)} slash commands")
        except Exception as e:
            print(f"⚠️ Failed to sync commands: {e}")
        print("✅ Bot is ready!")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            if ctx.command and ctx.command.cog:
                cog_name = ctx.command.cog.__class__.__name__
                if not is_module_enabled(ctx.guild.id, cog_name):
                    return await ctx.reply(f"❌ The `{cog_name}` module is disabled for this server. Use `!module enable {cog_name}` to use it.")
            return await ctx.reply("❌ You do not have permission to use this command.")
        
        # Log other errors but maybe don't spam chat
        if not isinstance(error, commands.CommandNotFound):
            print(f"Ignoring error in command {ctx.command}: {error}")
        
    async def load_all_modules(self):
        modules_dir = "modules"
        if not os.path.exists(modules_dir): return
        module_files = [f[:-3] for f in os.listdir(modules_dir) if f.endswith(".py") and f != "core.py" and f != "__init__.py"]
        
        for mod_name in module_files:
            try:
                module_path = f"modules.{mod_name}"
                mod = importlib.import_module(module_path)
                
                for name, obj in inspect.getmembers(mod, inspect.isclass):
                    if issubclass(obj, commands.Cog) and obj is not commands.Cog:
                        if obj.__module__ == module_path:
                            deps = getattr(obj, '_deps', [])
                            all_deps_met = True
                            for dep_info in deps:
                                dep_name = dep_info["name"]
                                is_soft = dep_info["soft"]
                                if dep_name.lower() not in [m.lower() for m in module_files] and dep_name.lower() != "core":
                                    if not is_soft:
                                        print(f"Missing core dependency for {obj.__name__}: {dep_name}")
                                        all_deps_met = False
                                        break
                                    else:
                                        print(f"Missing soft dependency for {obj.__name__}: {dep_name}")
                            
                            if all_deps_met:
                                await self.bot.add_cog(obj(self.bot))
                                print(f"Loaded module cog: {obj.__name__}")
            except Exception as e:
                print(f"Error loading module {mod_name}: {e}")

    # ===== Modules CommandGroup =====
    @app_commands.command(name="module", description="Manage enabled modules for this server")
    async def module_slash(self, interaction: discord.Interaction, action: str, module_name: str):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ This is an admin only command.", ephemeral=True)
        if action.lower() == "enable":
            enable_server_module(interaction.guild_id, module_name)
            await interaction.response.send_message(f"✅ Module `{module_name}` enabled for this server.", ephemeral=False)
        elif action.lower() == "disable":
            disable_server_module(interaction.guild_id, module_name)
            await interaction.response.send_message(f"✅ Module `{module_name}` disabled for this server.", ephemeral=False)
        else:
            await interaction.response.send_message("❌ Action must be either `enable` or `disable`.", ephemeral=True)

    @commands.group(name="module", invoke_without_command=True)
    async def module_cmd(self, ctx):
        await ctx.reply("Usage: `!module enable <name>` or `!module disable <name>`")
        
    @module_cmd.command(name="enable")
    async def module_enable(self, ctx, module_name: str):
        if not ctx.author.guild_permissions.administrator: return await ctx.reply("❌ Admins only.")
        enable_server_module(ctx.guild.id, module_name)
        await ctx.reply(f"✅ Module `{module_name}` enabled for this server.")

    @module_cmd.command(name="disable")
    async def module_disable(self, ctx, module_name: str):
        if not ctx.author.guild_permissions.administrator: return await ctx.reply("❌ Admins only.")
        disable_server_module(ctx.guild.id, module_name)
        await ctx.reply(f"✅ Module `{module_name}` disabled for this server.")

    # ===== Help Command =====
    @app_commands.command(name="help", description="Show all available commands")
    async def help_slash(self, interaction: discord.Interaction):
        await self.perform_help(interaction)

    @commands.command(name="help")
    async def help_command(self, ctx):
        await self.perform_help(ctx)

    async def perform_help(self, ctx_or_int):
        guild_id = getattr(ctx_or_int.guild, "id", None)
        embed = discord.Embed(title="🤖 Bot Commands", description="Available commands (based on enabled server modules):", color=0x7289DA)
        
        for cog_name, cog in self.bot.cogs.items():
            if guild_id and not is_module_enabled(guild_id, cog_name):
                continue
                
            help_info = getattr(cog.__class__, '_help_info', getattr(cog, '_help_info', None))
            if help_info:
                desc = help_info.get('description', 'No description available')
                cmds = help_info.get('commands', {})
                lines = [f"🔹 {cmd}: {cdesc}" for cmd, cdesc in cmds.items()]
                value = "\n".join(lines) if lines else desc
                if value: embed.add_field(name=f"📦 {cog_name}", value=value, inline=False)
                
        if isinstance(ctx_or_int, discord.Interaction):
            await ctx_or_int.response.send_message(embed=embed)
        else:
            await ctx_or_int.send(embed=embed)

    # ===== Core Moderation Commands =====
    async def execute_warn(self, ctx_or_int, member: discord.Member, reason: str):
        guild_id = member.guild.id
        mod_id = get_author(ctx_or_int).id
        count = add_warning(guild_id, member.id, mod_id, reason)
        embed = discord.Embed(
            title=f"⚠️ Warning Issued: {member.name}",
            description=f"**Reason:** {reason}\n**Total Warnings:** {count}",
            color=0xff0000
        )
        await send_response(ctx_or_int, embed=embed)
        self.bot.dispatch("member_warned", member, count, reason)

    @commands.command(name="warn")
    async def warn_command(self, ctx, member: discord.Member = None, *, reason: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ You don't have permission.")
        if not member or not reason: return await ctx.reply("⚠️ Usage: `!warn @user reason`")
        await self.execute_warn(ctx, member, reason)

    @commands.command(name="hwarn")
    async def hwarn_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ You don't have permission.")
        if not member: return await ctx.reply("⚠️ Please mention a user.")
        await self.execute_hwarn(ctx, member)

    async def execute_hwarn(self, ctx_or_int, member: discord.Member):
        guild_id = member.guild.id
        
        # 1. Discord Data
        warns = load_server_data(guild_id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        mutes = load_server_data(guild_id, "mutes.json") or []
        user_mutes = [m for m in mutes if m["userId"] == str(member.id)]

        # 2. Check for Minecraft Data integration
        items = []
        mc_name = None
        
        mc_cog = self.bot.get_cog("Minecraft")
        if mc_cog and hasattr(mc_cog, "get_combined_history"):
            # If Minecraft module is enabled, fetch linked infractions
            mc_items, mc_name = await mc_cog.get_combined_history(member)
            items = mc_items # Minecraft helper already combines Discord + MC
        else:
            # Fallback to standard Discord-only logic if MC module is missing
            for w in user_warns:
                ts = datetime.fromisoformat(w["timestamp"]).timestamp()
                items.append({"origin": "Discord", "type": "Warning", "reason": w["reason"], "ts": ts})
            for m in user_mutes:
                ts = datetime.fromisoformat(m["timestamp"]).timestamp()
                items.append({"origin": "Discord", "type": f"Mute ({m['durationSec']//60}m)", "reason": m["reason"], "ts": ts})
            items.sort(key=lambda x: x["ts"], reverse=True)

        if not items:
            return await send_response(ctx_or_int, f"✅ **{member.name}** has a clean history!")

        embed = discord.Embed(
            title=f"📜 Moderation History — {member.name}",
            color=0xffaa00
        )
        if mc_name:
            embed.set_author(name=f"Linked Minecraft Account: {mc_name}")

        lines = []
        for it in items[:15]: # Show latest 15
            date = f"<t:{int(it['ts'])}:d>" if it['ts'] > 0 else "N/A"
            origin_icon = "🎮" if it["origin"] == "Minecraft" else "💬"
            lines.append(f"{origin_icon} **{it['type']}** — {date}\n└ *{it['reason']}*")
        
        embed.description = "\n".join(lines)
        if len(items) > 15:
            embed.set_footer(text=f"Showing latest 15 of {len(items)} total infractions.")

        await send_response(ctx_or_int, embed=embed)

    @commands.command(name="delwarn")
    async def delwarn_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ You don't have permission.")
        if not member: return await ctx.reply("⚠️ Please mention a user.")
        await self.execute_delwarn(ctx, member)

    async def execute_delwarn(self, ctx_or_int, member: discord.Member):
        warns = load_server_data(member.guild.id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        if not user_warns: return await send_response(ctx_or_int, f"✅ **{member.name}** has no warnings.")

        options = [discord.SelectOption(label=f"Warning {i}: {w['reason'][:50]}", value=w["id"]) for i, w in enumerate(user_warns, 1)]
        select = discord.ui.Select(placeholder="Select warnings to remove...", options=options, min_values=1, max_values=len(options))
        
        async def select_callback(interaction):
            updated_warnings = [w for w in load_server_data(interaction.guild_id, "warnings.json") if w["id"] not in select.values]
            save_server_data(interaction.guild_id, "warnings.json", updated_warnings)
            await interaction.response.edit_message(embed=discord.Embed(title="✅ Selected Warnings Deleted", color=0x00ff00), view=None)
            
        select.callback = select_callback
        view = discord.ui.View().add_item(select)
        await send_response(ctx_or_int, embed=discord.Embed(title=f"🚨 Remove Warnings for {member.name}", color=0xffcc00), view=view)

    @commands.command(name="mute")
    async def mute_command(self, ctx, *, args: str = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ You don't have permission.")
        if not args: return await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`")
        parts = [p.strip() for p in args.split(',', 2)]
        if len(parts) < 3: return await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`")
        user_input, duration_str, reason = parts
        member = ctx.guild.get_member_named(user_input) or (ctx.message.mentions[0] if ctx.message.mentions else None)
        if not member:
            try: member = await ctx.guild.fetch_member(int(user_input))
            except: return await ctx.reply(f"❌ Could not find user.")
        await self.execute_mute(ctx, member, duration_str, reason)

    async def execute_mute(self, ctx_or_int, member, duration_str, reason):
        if member == ctx_or_int.guild.owner:
            return await send_response(ctx_or_int, "❌ You cannot mute the server owner!")
        dur = parse_duration(duration_str)
        if not dur: return await send_response(ctx_or_int, "⚠️ Invalid duration. Use format: `10s`, `5m`, `2h`, `1d`")
        try:
            await member.timeout(timedelta(seconds=dur), reason=reason)
            add_mute(member.guild.id, member.id, get_author(ctx_or_int).id, reason, dur)
            await send_response(ctx_or_int, f"🔇 **{member.mention}** muted for **{duration_str}**. Reason: {reason}")
        except Exception as e:
            await send_response(ctx_or_int, f"❌ Failed to mute: {e}")

    @commands.command(name="unmute")
    async def unmute_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ You don't have permission.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            await member.timeout(None)
            await ctx.reply(f"✅ **{member.mention}** has been unmuted.")
        except Exception as e:
            await ctx.reply(f"❌ Failed to unmute: {e}")

    @commands.command(name="kick")
    async def kick_command(self, ctx, member: discord.Member = None, *, reason: str = "None"):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            if member == ctx.guild.owner:
                return await ctx.reply("❌ You cannot kick the server owner!")
            await member.kick(reason=reason)
            await ctx.reply(f"👢 **{member.name}** kicked. Reason: {reason}")
        except Exception as e: await ctx.reply(f"❌ Failed: {e}")

    @commands.command(name="ban")
    async def ban_command(self, ctx, member: discord.Member = None, *, reason: str = "None"):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            if member == ctx.guild.owner:
                return await ctx.reply("❌ You cannot ban the server owner!")
            await member.ban(reason=reason)
            await ctx.reply(f"🔨 **{member.name}** banned. Reason: {reason}")
        except Exception as e: await ctx.reply(f"❌ Failed: {e}")

    @commands.command(name="unban")
    async def unban_command(self, ctx, user_id: int):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.reply(f"✅ **{user.name}** has been unbanned.")
        except Exception as e: await ctx.reply(f"❌ Failed: {e}")

    @commands.command(name="modrole")
    async def modrole_command(self, ctx, role: discord.Role = None):
        if not ctx.author.guild_permissions.administrator: return await ctx.reply("❌ Admins only.")
        if not role: return await ctx.reply("⚠️ Need role mention.")
        from module_utils import load_server_data, save_server_data
        data = load_server_data(ctx.guild.id, "info.json") or {}
        roles = data.get("mod_roles", [])
        if role.id in roles:
            roles.remove(role.id)
            await ctx.reply(f"🗑️ Removed mod role {role.name}.")
        else:
            roles.append(role.id)
            await ctx.reply(f"✅ Added mod role {role.name}.")
        data["mod_roles"] = roles
        save_server_data(ctx.guild.id, "info.json", data)

async def setup(bot):
    await bot.add_cog(Core(bot))
