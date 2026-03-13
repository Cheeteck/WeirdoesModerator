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
    roles = data.get("mod_roles", {})
    # Migration if it was a list
    if isinstance(roles, list):
        roles = {str(rid): 1 for rid in roles}
    return roles

def is_moderator(member: discord.Member, min_level: int = 1) -> bool:
    if not member or not member.guild:
        return False
    if member == member.guild.owner:
        return True
    if member.guild_permissions.administrator:
        return True
    mod_roles = get_moderator_roles(member.guild.id)
    for role in member.roles:
        level = mod_roles.get(str(role.id), 0)
        if level >= min_level:
            return True
    return False

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

from typing import Optional

def parse_duration(duration_str: str) -> Optional[int]:
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

@Module.version("1.1")
@Module.help(
    commands={
        "help": "displays help menu",
        "hwarn": "shows user's full moderation history",
        "delwarn": "delete warns from a user",
        "modrole": "adds/removes a mod role with a level (1-3)",
        "kick": "kicks a user",
        "ban": "bans a user",
        "unban": "unbans a user",
        "mute": "mutes a user",
        "unmute": "unmutes a user",
        "module enable": "Enables a module in this server",
        "module disable": "Disables a module in this server",
        "refresh_modules": "Refreshes modules from GitHub (Owner only)"
    },
    description="Core functionality for the bot (cannot be disabled)."
)
class Core(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        print(f" Logged in as {self.bot.user.name}")
        try:
            synced = await self.bot.tree.sync()
            print(f" Synced {len(synced)} slash commands")
        except Exception as e:
            print(f"⚠️ Failed to sync commands: {e}")
        print(" Bot is ready!")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            if ctx.command and ctx.command.cog:
                cog_name = ctx.command.cog.__class__.__name__
                if not is_module_enabled(ctx.guild.id, cog_name):
                    return await ctx.reply(f"Command disabled, enable with `!module enable {cog_name}`")
            return await ctx.reply("You do not have permission to use this command.")
        
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



    # ===== Core Slash Commands =====
    @app_commands.command(name="warn", description="Warn a user")
    async def warn_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not is_moderator(interaction.user, min_level=1): return await interaction.response.send_message("Permission denied (Level 1 required).", ephemeral=True)
        await self.execute_warn(interaction, member, reason)

    @app_commands.command(name="hwarn", description="Show user history")
    async def hwarn_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user, min_level=1): return await interaction.response.send_message("Permission denied (Level 1 required).", ephemeral=True)
        await self.execute_hwarn(interaction, member)

    @app_commands.command(name="mute", description="Mute a user")
    async def mute_slash(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
        if not is_moderator(interaction.user, min_level=1): return await interaction.response.send_message("Permission denied (Level 1 required).", ephemeral=True)
        await self.execute_mute(interaction, member, duration, reason)

    @app_commands.command(name="unmute", description="Unmute a user")
    async def unmute_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user, min_level=1): return await interaction.response.send_message("Permission denied (Level 1 required).", ephemeral=True)
        try:
            await member.timeout(None)
            await interaction.response.send_message(f" **{member.mention}** has been unmuted.")
        except Exception as e: await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    @app_commands.command(name="kick", description="Kick a user")
    async def kick_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "None"):
        if not is_moderator(interaction.user, min_level=2): return await interaction.response.send_message("Permission denied (Level 2 required).", ephemeral=True)
        if member == interaction.guild.owner: return await interaction.response.send_message("Cannot kick owner.", ephemeral=True)
        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(f"👢 **{member.name}** kicked. Reason: {reason}")
        except Exception as e: await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    @app_commands.command(name="ban", description="Ban a user")
    async def ban_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "None"):
        if not is_moderator(interaction.user, min_level=2): return await interaction.response.send_message("Permission denied (Level 2 required).", ephemeral=True)
        if member == interaction.guild.owner: return await interaction.response.send_message("Cannot ban owner.", ephemeral=True)
        try:
            await member.ban(reason=reason)
            await interaction.response.send_message(f"🔨 **{member.name}** banned. Reason: {reason}")
        except Exception as e: await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    @app_commands.command(name="unban", description="Unban a user by ID")
    async def unban_slash(self, interaction: discord.Interaction, user_id: str):
        if not is_moderator(interaction.user, min_level=2): return await interaction.response.send_message("Permission denied (Level 2 required).", ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user)
            await interaction.response.send_message(f" **{user.name}** has been unbanned.")
        except Exception as e: await interaction.response.send_message(f"Failed: {e}", ephemeral=True)

    @app_commands.command(name="delwarn", description="Delete specific warnings from a user")
    async def delwarn_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user, min_level=1): return await interaction.response.send_message("Permission denied (Level 1 required).", ephemeral=True)
        await self.execute_delwarn(interaction, member)

    @app_commands.command(name="modrole", description="Add/Remove a moderator role with a level")
    async def modrole_slash_cmd(self, interaction: discord.Interaction, role: discord.Role, level: int = 1):
        if not interaction.user.guild_permissions.administrator: return await interaction.response.send_message("Admins only.", ephemeral=True)
        if level < 1 or level > 3: return await interaction.response.send_message("Level must be 1, 2, or 3.", ephemeral=True)
        
        data = load_server_data(interaction.guild_id, "info.json") or {}
        roles = data.get("mod_roles", {})
        if isinstance(roles, list): roles = {str(rid): 1 for rid in roles}
        
        rid = str(role.id)
        if rid in roles and level == roles[rid]:
            del roles[rid]
            await interaction.response.send_message(f"🗑️ Removed mod role {role.name}.")
        else:
            roles[rid] = level
            level_names = {1: "Base", 2: "Higher", 3: "Administrator"}
            await interaction.response.send_message(f" Set mod role {role.name} to level {level} ({level_names[level]}).")
            
        data["mod_roles"] = roles
        save_server_data(interaction.guild_id, "info.json", data)

    # ===== Modules CommandGroup =====
    @app_commands.command(name="module", description="Manage enabled modules for this server")
    async def module_slash(self, interaction: discord.Interaction, action: str, module_name: str = None):
        if not is_moderator(interaction.user, min_level=3):
            return await interaction.response.send_message("Administrator permission (Level 3) required.", ephemeral=True)
        
        action = action.lower()
        if action == "enable" and module_name:
            enable_server_module(interaction.guild_id, module_name)
            await interaction.response.send_message(f" Module `{module_name}` enabled for this server.", ephemeral=False)
        elif action == "disable" and module_name:
            disable_server_module(interaction.guild_id, module_name)
            await interaction.response.send_message(f" Module `{module_name}` disabled for this server.", ephemeral=False)
        elif action == "list":
            await interaction.response.defer()
            embed = await self._get_module_list_embed(interaction.guild_id)
            await interaction.followup.send(embed=embed)
        elif action == "info" and module_name:
            await interaction.response.defer()
            embed = await self._get_module_info_embed(interaction.guild_id, module_name)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message("Usage: `/module action:list` or `/module action:enable module_name:NatLang`", ephemeral=True)

    @commands.group(name="module", invoke_without_command=True)
    async def module_cmd(self, ctx, module_name: str = None):
        if module_name and not ctx.invoked_subcommand:
            await self.module_info(ctx, module_name)
        elif not ctx.invoked_subcommand:
            await ctx.reply("Usage: `!module list`, `!module enable <name>`, or `!module <name>`")
        
    @module_cmd.command(name="list")
    async def module_list(self, ctx):
        if not is_moderator(ctx.author, min_level=3): return await ctx.reply("Administrator permission (Level 3) required.")
        async with ctx.typing():
            embed = await self._get_module_list_embed(ctx.guild.id)
            await ctx.reply(embed=embed)

    @module_cmd.command(name="info")
    async def module_info(self, ctx, module_name: str):
        if not is_moderator(ctx.author, min_level=3): return await ctx.reply("Administrator permission (Level 3) required.")
        async with ctx.typing():
            embed = await self._get_module_info_embed(ctx.guild.id, module_name)
            await ctx.reply(embed=embed)
        
    @module_cmd.command(name="enable")
    async def module_enable(self, ctx, module_name: str):
        if not is_moderator(ctx.author, min_level=3): return await ctx.reply("Administrator permission (Level 3) required.")
        enable_server_module(ctx.guild.id, module_name)
        await ctx.reply(f" Module `{module_name}` enabled for this server.")

    @module_cmd.command(name="disable")
    async def module_disable(self, ctx, module_name: str):
        if not is_moderator(ctx.author, min_level=3): return await ctx.reply("Administrator permission (Level 3) required.")
        disable_server_module(ctx.guild.id, module_name)
        await ctx.reply(f" Module `{module_name}` disabled for this server.")

    # ===== Help Command =====
    @app_commands.command(name="help", description="Show all available commands")
    async def help_slash(self, interaction: discord.Interaction):
        await self.perform_help(interaction)

    @commands.command(name="help")
    async def help_command(self, ctx):
        await self.perform_help(ctx)

    async def perform_help(self, ctx_or_int, specific_cog=None):
        guild_id = getattr(ctx_or_int.guild, "id", None)
        title = f"🤖 {specific_cog} Commands" if specific_cog else "🤖 Bot Commands"
        embed = discord.Embed(title=title, description="Available commands (based on enabled server modules):", color=0x7289DA)
        
        for cog_name, cog in self.bot.cogs.items():
            if specific_cog and cog_name.lower() != specific_cog.lower():
                continue
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
            if ctx_or_int.response.is_done():
                await ctx_or_int.followup.send(embed=embed)
            else:
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
        if not is_moderator(ctx.author, min_level=1): return await ctx.reply("You don't have permission.")
        if not member or not reason: return await ctx.reply("⚠️ Usage: `!warn @user reason`")
        await self.execute_warn(ctx, member, reason)

    @commands.command(name="hwarn")
    async def hwarn_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author, min_level=1): return await ctx.reply("You don't have permission.")
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
            return await send_response(ctx_or_int, f" **{member.name}** has a clean history!")

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
        if not is_moderator(ctx.author, min_level=1): return await ctx.reply("You don't have permission.")
        if not member: return await ctx.reply("⚠️ Please mention a user.")
        await self.execute_delwarn(ctx, member)

    async def execute_delwarn(self, ctx_or_int, member: discord.Member):
        warns = load_server_data(member.guild.id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        if not user_warns: return await send_response(ctx_or_int, f" **{member.name}** has no warnings.")

        options = [discord.SelectOption(label=f"Warning {i}: {w['reason'][:50]}", value=w["id"]) for i, w in enumerate(user_warns, 1)]
        select = discord.ui.Select(placeholder="Select warnings to remove...", options=options, min_values=1, max_values=len(options))
        
        async def select_callback(interaction):
            updated_warnings = [w for w in load_server_data(interaction.guild_id, "warnings.json") if w["id"] not in select.values]
            save_server_data(interaction.guild_id, "warnings.json", updated_warnings)
            await interaction.response.edit_message(embed=discord.Embed(title=" Selected Warnings Deleted", color=0x00ff00), view=None)
            
        select.callback = select_callback
        view = discord.ui.View().add_item(select)
        await send_response(ctx_or_int, embed=discord.Embed(title=f"🚨 Remove Warnings for {member.name}", color=0xffcc00), view=view)

    @commands.command(name="mute")
    async def mute_command(self, ctx, *, args: str = None):
        if not is_moderator(ctx.author, min_level=1): return await ctx.reply("You don't have permission.")
        if not args: return await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`")
        parts = [p.strip() for p in args.split(',', 2)]
        if len(parts) < 3: return await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`")
        user_input, duration_str, reason = parts
        member = ctx.guild.get_member_named(user_input) or (ctx.message.mentions[0] if ctx.message.mentions else None)
        if not member:
            try: member = await ctx.guild.fetch_member(int(user_input))
            except: return await ctx.reply(f"Could not find user.")
        await self.execute_mute(ctx, member, duration_str, reason)

    async def execute_mute(self, ctx_or_int, member, duration_str, reason):
        if member == ctx_or_int.guild.owner:
            return await send_response(ctx_or_int, "You cannot mute the server owner!")
        dur = parse_duration(duration_str)
        if not dur: return await send_response(ctx_or_int, "⚠️ Invalid duration. Use format: `10s`, `5m`, `2h`, `1d`")
        try:
            await member.timeout(timedelta(seconds=dur), reason=reason)
            add_mute(member.guild.id, member.id, get_author(ctx_or_int).id, reason, dur)
            await send_response(ctx_or_int, f"🔇 **{member.mention}** muted for **{duration_str}**. Reason: {reason}")
        except Exception as e:
            await send_response(ctx_or_int, f"Failed to mute: {e}")

    @commands.command(name="unmute")
    async def unmute_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author, min_level=1): return await ctx.reply("You don't have permission.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            await member.timeout(None)
            await ctx.reply(f" **{member.mention}** has been unmuted.")
        except Exception as e:
            await ctx.reply(f"Failed to unmute: {e}")

    @commands.command(name="kick")
    async def kick_command(self, ctx, member: discord.Member = None, *, reason: str = "None"):
        if not is_moderator(ctx.author, min_level=2): return await ctx.reply("Higher permission (Level 2) required.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            if member == ctx.guild.owner:
                return await ctx.reply("You cannot kick the server owner!")
            await member.kick(reason=reason)
            await ctx.reply(f"👢 **{member.name}** kicked. Reason: {reason}")
        except Exception as e: await ctx.reply(f"Failed: {e}")

    @commands.command(name="ban")
    async def ban_command(self, ctx, member: discord.Member = None, *, reason: str = "None"):
        if not is_moderator(ctx.author, min_level=2): return await ctx.reply("Higher permission (Level 2) required.")
        if not member: return await ctx.reply("⚠️ Content missing.")
        try:
            if member == ctx.guild.owner:
                return await ctx.reply("You cannot ban the server owner!")
            await member.ban(reason=reason)
            await ctx.reply(f"🔨 **{member.name}** banned. Reason: {reason}")
        except Exception as e: await ctx.reply(f"Failed: {e}")

    @commands.command(name="unban")
    async def unban_command(self, ctx, user_id: int):
        if not is_moderator(ctx.author, min_level=2): return await ctx.reply("Higher permission (Level 2) required.")
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user)
            await ctx.reply(f" **{user.name}** has been unbanned.")
        except Exception as e: await ctx.reply(f"Failed: {e}")

    @commands.group(name="modrole", invoke_without_command=True)
    async def modrole_command(self, ctx, role: discord.Role = None, level: int = 1):
        if not ctx.author.guild_permissions.administrator: return await ctx.reply("Admins only.")
        if not role: return await ctx.reply("⚠️ Usage: `!modrole <role> [level (1-3)]`")
        if level < 1 or level > 3: return await ctx.reply("Level must be 1 (Base), 2 (Higher), or 3 (Administrator).")
        
        data = load_server_data(ctx.guild.id, "info.json") or {}
        roles = data.get("mod_roles", {})
        if isinstance(roles, list): roles = {str(rid): 1 for rid in roles}
        
        rid = str(role.id)
        if rid in roles and level == roles[rid]:
            del roles[rid]
            await ctx.reply(f"🗑️ Removed mod role {role.name}.")
        else:
            roles[rid] = level
            level_names = {1: "Base", 2: "Higher", 3: "Administrator"}
            await ctx.reply(f" Set mod role {role.name} to level {level} ({level_names[level]}).")
        
        data["mod_roles"] = roles
        save_server_data(ctx.guild.id, "info.json", data)

    @app_commands.command(name="refresh_modules", description="Refresh modules from GitHub (Owner only)")
    async def refresh_modules_slash(self, interaction: discord.Interaction):
        if interaction.user != interaction.guild.owner:
            return await interaction.response.send_message("Only the server owner can use this command.", ephemeral=True)
        await interaction.response.defer()
        
        updates, github_data = await self.check_for_updates()
        if isinstance(updates, str): # Error message
            return await interaction.followup.send(updates)
        
        if not updates:
            return await interaction.followup.send("✅ All modules are already up to date.")
        
        view = ConfirmRefreshView(self, updates, github_data)
        update_list = "\n".join([f"- `{u}`" for u in updates])
        await interaction.followup.send(f"**Do you want to refresh these modules?**\n{update_list}", view=view)

    @commands.command(name="refresh_modules")
    async def refresh_modules_command(self, ctx):
        if ctx.author != ctx.guild.owner:
            return await ctx.reply("Only the server owner can use this command.")
        
        msg = await ctx.reply("⏳ Checking for updates...")
        updates, github_data = await self.check_for_updates()
        
        if isinstance(updates, str):
            return await msg.edit(content=updates)
            
        if not updates:
            return await msg.edit(content="✅ All modules are already up to date.")
            
        view = ConfirmRefreshView(self, updates, github_data)
        update_list = "\n".join([f"- `{u}`" for u in updates])
        await msg.edit(content=f"**Do you want to refresh these modules?**\n{update_list}", view=view)

    async def _get_module_list_embed(self, guild_id):
        import os
        local_files = [f for f in os.listdir("modules") if f.endswith(".py")]
        github_data = await self._fetch_github_modules_data()
        
        embed = discord.Embed(title="🧩 Bot Modules", color=0x3498db)
        
        for file in local_files:
            mod_name = file[:-3]
            # Try to get local version from loaded cog or from file
            local_ver_str = "Unknown"
            cog = self._find_cog_by_module(f"modules.{mod_name}")
            if cog and hasattr(cog, "_version"):
                local_ver_str = cog._version
            else:
                # Read from file if not loaded
                try:
                    with open(os.path.join("modules", file), "r", encoding="utf-8") as f:
                        v = self.parse_version(f.read())
                        if v: local_ver_str = ".".join(map(str, [x for x in v if x != 0] or [0]))
                except: pass

            gh_ver_str = "Unknown"
            if mod_name != "core" and github_data and file in github_data:
                v = github_data[file].get("version")
                if v: gh_ver_str = ".".join(map(str, [x for x in v if x != 0] or [0]))

            enabled = is_module_enabled(guild_id, mod_name)
            status_env = "✅" if enabled else "❌"
            
            ver_status = ""
            if local_ver_str != "Unknown" and gh_ver_str != "Unknown":
                lv = self.parse_version(f"@Module.version('{local_ver_str}')")
                gv = github_data[file].get("version")
                if gv and lv and gv > lv:
                    ver_status = " ⚠️ (Update available)"
                elif gv and lv and gv == lv:
                    ver_status = " ✨"
                elif lv and not gv:
                    ver_status = " (Local only)"

            embed.add_field(
                name=f"{status_env} {mod_name.capitalize()}",
                value=f"Version: `{local_ver_str}` (GH: `{gh_ver_str}`){ver_status}",
                inline=False
            )
        
        return embed

    async def _get_module_info_embed(self, guild_id, mod_name):
        import os
        filename = f"{mod_name.lower()}.py"
        filepath = os.path.join("modules", filename)
        
        if not os.path.exists(filepath) and mod_name.lower() != "core":
             return discord.Embed(description=f"❌ Module `{mod_name}` not found.", color=0xff0000)
        
        if mod_name.lower() == "core": filename = "core.py"; filepath = os.path.join("modules", filename)

        github_data = await self._fetch_github_modules_data()
        
        local_ver_str = "Unknown"
        cog = self._find_cog_by_module(f"modules.{mod_name.lower()}")
        if cog and hasattr(cog, "_version"):
            local_ver_str = cog._version
        
        gh_ver_str = "Unknown"
        if mod_name.lower() != "core" and github_data and filename in github_data:
            v = github_data[filename].get("version")
            if v: gh_ver_str = ".".join(map(str, [x for x in v if x != 0] or [0]))

        enabled = is_module_enabled(guild_id, mod_name)
        
        embed = discord.Embed(title=f"📦 Module: {mod_name.capitalize()}", color=0x3498db)
        embed.add_field(name="Status", value="Enabled" if enabled else "Disabled", inline=True)
        embed.add_field(name="Local Version", value=f"`{local_ver_str}`", inline=True)
        embed.add_field(name="GitHub Version", value=f"`{gh_ver_str}`", inline=True)
        
        if cog and hasattr(cog, "_help_info"):
            help_info = cog._help_info
            embed.description = help_info.get("description", "No description.")
            cmds = help_info.get("commands", {})
            if cmds:
                cmd_list = "\n".join([f"`{k}`: {v}" for k, v in cmds.items()])
                embed.add_field(name="Commands", value=cmd_list[:1024], inline=False)
                
        return embed

    def _find_cog_by_module(self, module_path):
        for cog in self.bot.cogs.values():
            if cog.__module__ == module_path:
                return cog
        return None

    async def _fetch_github_modules_data(self):
        import aiohttp
        github_url = "https://api.github.com/repos/Cheeteck/WeirdoesModerator/contents/modules"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(github_url) as resp:
                    if resp.status != 200: return None
                    files = await resp.json()
                    
                data = {}
                for f in files:
                    if f["name"].endswith(".py") and f["name"] != "core.py":
                        async with session.get(f["download_url"]) as vresp:
                            if vresp.status == 200:
                                content = (await vresp.read()).decode("utf-8")
                                ver = self.parse_version(content)
                                data[f["name"]] = {"version": ver, "url": f["download_url"]}
                return data
        except:
            return None

    def parse_version(self, content):
        import re
        # Look for @Module.version("1.1") or @Module.version('1.1') or @Module.version(1.1)
        match = re.search(r"@Module\.version\((?:['\"](.*?)['\"]|(.*?))\)", content)
        if match:
            v = match.group(1) or match.group(2)
            if not v: return None
            v = v.strip().lower()
            if v.startswith('v'): v = v[1:]
            try:
                parts = [int(x) for x in v.split('.')]
                while len(parts) < 4: parts.append(0)
                return tuple(parts)
            except:
                return (0, 0, 0, 0)
        return None

    async def check_for_updates(self):
        modules_dir = "modules"
        try:
            github_data = await self._fetch_github_modules_data()
            if not github_data: return "❌ Failed to fetch from GitHub.", None
            
            updates = []
            for name, info in github_data.items():
                gh_ver = info["version"]
                if gh_ver is None: continue
                
                local_path = os.path.join(modules_dir, name)
                local_ver = (0, 0, 0, 0)
                if os.path.exists(local_path):
                    with open(local_path, "r", encoding="utf-8") as f:
                        local_ver = self.parse_version(f.read()) or (0, 0, 0, 0)
                
                if gh_ver > local_ver:
                    updates.append(name)
            return updates, github_data
        except Exception as e:
            return f"❌ Error checking updates: {e}", None

    async def sync_modules_from_github(self, updates=None, github_data=None):
        import aiohttp
        import importlib
        modules_dir = "modules"
        
        try:
            if github_data is None:
                github_data = await self._fetch_github_modules_data()
            if not github_data: return "❌ Failed to fetch from GitHub."
            
            if updates is None:
                updates, _ = await self.check_for_updates()
                if isinstance(updates, str): return updates

            applied = []
            async with aiohttp.ClientSession() as session:
                for name in updates:
                    info = github_data.get(name)
                    if not info: continue
                    
                    local_path = os.path.join(modules_dir, name)
                    async with session.get(info["url"]) as resp:
                        if resp.status == 200:
                            github_content = (await resp.read()).decode("utf-8")
                            with open(local_path, "w", encoding="utf-8") as f:
                                f.write(github_content)
                            applied.append(name)
            
            reloaded = []
            for name in applied:
                mod_name = name[:-3]
                module_path = f"modules.{mod_name}"
                try:
                    for CogName, cog in list(self.bot.cogs.items()):
                        if cog.__module__ == module_path:
                            await self.bot.remove_cog(CogName)
                    
                    if module_path in importlib.sys.modules:
                        importlib.reload(importlib.sys.modules[module_path])
                    else:
                        importlib.import_module(module_path)
                        
                    mod = importlib.sys.modules[module_path]
                    for _, obj in inspect.getmembers(mod, inspect.isclass):
                        if issubclass(obj, commands.Cog) and obj is not commands.Cog:
                            if obj.__module__ == module_path:
                                await self.bot.add_cog(obj(self.bot))
                                reloaded.append(obj.__name__)
                except Exception as e:
                    print(f"Failed to reload {mod_name}: {e}")
            
            try:
                await self.bot.tree.sync()
            except: pass

            if not applied:
                return "✅ All modules are already up to date."
            return f"✅ Updated and reloaded {len(applied)} modules ({len(reloaded)} cogs): {', '.join(applied)}"
        except Exception as e:
            return f"❌ Failed to sync: {e}"

class ConfirmRefreshView(discord.ui.View):
    def __init__(self, core_cog, updates, github_data):
        super().__init__(timeout=60)
        self.core_cog = core_cog
        self.updates = updates
        self.github_data = github_data

    @discord.ui.button(label="Yes, Update", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        result = await self.core_cog.sync_modules_from_github(self.updates, self.github_data)
        await interaction.edit_original_response(content=result, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Refresh cancelled.", view=None)
        self.stop()

async def setup(bot):
    await bot.add_cog(Core(bot))
