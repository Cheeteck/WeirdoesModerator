import discord
from discord.ext import commands
from discord import app_commands
import os
from datetime import datetime, timedelta, timezone
import json
import asyncio
from dotenv import load_dotenv
import uuid
from groq import Groq

load_dotenv()

# Groq Client Initialization
groq_client = Groq(api_key=os.getenv("GROQ")) if os.getenv("GROQ") else None

# Local Storage Files
WARNINGS_FILE = "warnings.json"
MUTES_FILE = "mutes.json"
MODERATOR_ROLES_FILE = "info.json"

# ==================== DATA HELPERS ====================

def load_data(file_path):
    """Load data from a JSON file"""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading {file_path}: {e}")
            return []
    return []

def save_data(file_path, data):
    """Save data to a JSON file"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"⚠️ Error saving {file_path}: {e}")

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Configuration
MODERATOR_ROLES_FILE = "info.json"

# Global state
moderator_roles = []
reset_backup = None


# ==================== UTILITY FUNCTIONS ====================

def load_moderator_roles():
    """Load moderator roles from file"""
    global moderator_roles
    try:
        if os.path.exists(MODERATOR_ROLES_FILE):
            with open(MODERATOR_ROLES_FILE, "r") as f:
                moderator_roles = json.load(f)
                # Convert strings to integers
                moderator_roles = [int(role_id) for role_id in moderator_roles]
        else:
            moderator_roles = []
    except Exception as e:
        print(f"⚠️ Error loading moderator roles: {e}")
        moderator_roles = []


def save_moderator_roles():
    """Save moderator roles to file"""
    try:
        # Convert integers to strings for JSON
        with open(MODERATOR_ROLES_FILE, "w") as f:
            json.dump([str(role_id) for role_id in moderator_roles], f)
    except Exception as e:
        print(f"⚠️ Error saving moderator roles: {e}")


def is_moderator(member: discord.Member) -> bool:
    """Check if member has moderator role"""
    return any(role.id in moderator_roles for role in member.roles)


def parse_duration(duration_str: str) -> int:
    """Parse duration string (e.g., '10m', '2h', '30s') to seconds"""
    duration_str = duration_str.lower().strip()

    # Extract number and unit
    import re
    match = re.match(r'^(\d+)([smhd])$', duration_str)
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    # Convert to seconds
    if unit == 's':
        return value
    elif unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400

    return None


async def get_member_flexible(ctx, user_input: str) -> discord.Member:
    """Get member by mention, username, or ID"""
    if ctx.message.mentions:
        return ctx.message.mentions[0]
    try:
        user_id = int(user_input)
        return await ctx.guild.fetch_member(user_id)
    except:
        pass
    user_input_lower = user_input.lower()
    for member in ctx.guild.members:
        if member.name.lower() == user_input_lower or member.display_name.lower() == user_input_lower:
            return member
    return None


async def send_response(ctx_or_int, content=None, embed=None, view=None, ephemeral=False):
    """Helper to respond to either Context, Interaction, or raw Message"""
    # Build kwargs, only including non-None values
    kwargs = {}
    if content is not None:
        kwargs['content'] = content
    if embed is not None:
        kwargs['embed'] = embed
    if view is not None:
        kwargs['view'] = view
    
    if isinstance(ctx_or_int, discord.Interaction):
        if ephemeral:
            kwargs['ephemeral'] = ephemeral
        if ctx_or_int.response.is_done():
            await ctx_or_int.followup.send(**kwargs)
        else:
            await ctx_or_int.response.send_message(**kwargs)
    elif isinstance(ctx_or_int, commands.Context):
        await ctx_or_int.send(**kwargs)
    elif isinstance(ctx_or_int, discord.Message):
        await ctx_or_int.reply(**kwargs)
    else:
        # Fallback for anything else with a send method
        await ctx_or_int.send(**kwargs)


def get_author(ctx_or_int):
    """Get author from either Context, Interaction, or Message"""
    if isinstance(ctx_or_int, discord.Interaction):
        return ctx_or_int.user
    # Context and Message both have .author
    return ctx_or_int.author


def get_channel(ctx_or_int):
    """Get channel from Context, Interaction, or Message"""
    return ctx_or_int.channel


async def get_user_id(ctx, user_input: str) -> int:
    """Get user ID by mention or ID string"""
    if ctx.message.mentions:
        return ctx.message.mentions[0].id
    try:
        return int(user_input)
    except:
        return None


# ==================== JARVIS ROUTER ====================

async def handle_jarvis_command(message: discord.Message, query: str):
    """Route natural language query to bot functions using Groq"""
    if not groq_client:
        await message.reply("❌ Groq API key is not configured.")
        return
    try:
      
        system_prompt = f"""
You are Jarvis, a highly sophisticated, polite, and professional AI moderator assistant.
Your tone is sleek, premium, and efficient (like the MCU Jarvis).

Your task is to parse the user's natural language request and determine the appropriate moderation action.

REASON BEAUTIFICATION:
If the user's reason is informal or simple (e.g., "being annoying", "spamming"), rephrase it to be more formal and professional while preserving the original intent.
- "bad words" -> "Usage of prohibited language"
Provide the REPHRASED reason in the "reason" argument.

Available actions:
1. warn (args: user_id, reason)
2. mute (args: user_id, duration, reason)
3. unmute (args: user_id)
4. kick (args: user_id, reason)
5. ban (args: user_id, reason)
6. unban (args: user_id)
7. clear (args: amount)
8. hwarn (args: user_id) - views history
9. allwarn (no args)
10. lwarn (no args)
11. clearwarns (args: user_id) - clear all warnings for a specific user
12. resetwarns (no args) - reset ALL warnings for everyone
13. shutdown (no args)
14. help (no args)

Respond ONLY with a JSON object:
{{"action": "action_name", "args": {{"arg1": "val1", ...}}}}

Duration format for mute: '10s', '5m', '2h', '1d'.
Current Time: {datetime.now(timezone.utc).isoformat()}
"""
        
        # Identify mentioned users
        mentions_info = "\n".join([f"Name: {m.name}, ID: {m.id}, Display Name: {m.display_name}" for m in message.mentions])
        user_context = f"Mentioned Users:\n{mentions_info}\n\nUser Message: {query}"
        
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_context}
            ],
            response_format={"type": "json_object"}
        )
        
        response_data = json.loads(completion.choices[0].message.content)
        action = response_data.get("action")
        args = response_data.get("args", {})
        
        print(f"🤖 Jarvis Routing: {action} with {args}")
        
        # Execute the routed action
        if action == "warn":
            target = await message.guild.fetch_member(int(args["user_id"]))
            reason = args.get("reason", "No reason provided")
            await handle_warn(message, target, reason)
            
        elif action == "mute":
            target = await message.guild.fetch_member(int(args["user_id"]))
            duration = args.get("duration", "10m")
            reason = args.get("reason", "No reason provided")
            await handle_mute(message, target, duration, reason)
            
        elif action == "unmute":
            target = await message.guild.fetch_member(int(args["user_id"]))
            await handle_unmute(message, target)

        elif action == "kick":
            target = await message.guild.fetch_member(int(args["user_id"]))
            reason = args.get("reason", "No reason provided")
            await handle_kick(message, target, reason)

        elif action == "ban":
            target = await message.guild.fetch_member(int(args["user_id"]))
            reason = args.get("reason", "No reason provided")
            await handle_ban(message, target, reason)

        elif action == "unban":
            user_id = int(args["user_id"])
            await handle_unban(message, user_id)

        elif action == "clear":
            amount = int(args.get("amount", 10))
            await handle_clear(message, amount)
            
        elif action == "hwarn":
            target = await message.guild.fetch_member(int(args["user_id"]))
            await send_history(message, target)
            
        elif action == "allwarn":
            await perform_allwarn(message)
            
        elif action == "lwarn":
            await perform_lwarn(message)
            
        elif action == "clearwarns":
            target = await message.guild.fetch_member(int(args["user_id"]))
            await perform_clearwarns(message, target)
            
        elif action == "resetwarns":
            await perform_resetwarns(message)
            
        elif action == "shutdown":
            await perform_shutdown(message)
            
        elif action == "help":
            await perform_help(message)
            
        else:
            await message.reply(f"🤔 I understood the request, but '{action}' is not in my current protocols.")

    except Exception as e:
        print(f"⚠️ Jarvis Error: {e}")
        await message.reply(f"❌ I encountered an error processing that: {e}")


# ==================== BOT EVENTS ====================

@bot.event
async def on_ready():
    """Bot startup"""
    print(f"✅ Logged in as {bot.user.name}")

    # Load moderator roles
    load_moderator_roles()
    print(f"✅ Loaded {len(moderator_roles)} moderator roles")

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"⚠️ Failed to sync commands: {e}")

    print("✅ Bot is ready!")


@bot.event
async def on_message(message):
    """Handle Jarvis natural language commands"""
    if message.author.bot:
        return

    # Check for Jarvis commands
    content = message.content.strip()
    if content.lower().startswith("jarvis"):
        # PERMISSION CHECK FIRST
        if not is_moderator(message.author):
            await message.reply("❌ You don't have permission to command me, you pathetic peasant.")
            return
            
        query = content[6:].strip()
        if not query:
            await message.reply("Yes, sir?")
            return
            
        # Use simple router for immediate keywords (optional fallback)
        # or just pass everything to Groq
        await handle_jarvis_command(message, query)
        return

    # Process commands
    await bot.process_commands(message)


# ==================== WARN HANDLER ====================

async def handle_warn(ctx_or_int, member: discord.Member, reason: str):
    """Handle warning logic (used by !warn, /warn, and Jarvis)"""
    warnings = load_data(WARNINGS_FILE)
    
    # Create warning
    new_warn = {
        "id": str(uuid.uuid4()),
        "userId": str(member.id),
        "reason": reason,
        "moderatorId": str(get_author(ctx_or_int).id),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    warnings.append(new_warn)
    save_data(WARNINGS_FILE, warnings)

    warning_count = len([w for w in warnings if w["userId"] == str(member.id)])

    embed = discord.Embed(
        title=f"⚠️ Warning Issued: {member.name}",
        description=f"**Reason:** {reason}\n**Total Warnings:** {warning_count}",
        color=0xff0000
    )

    await send_response(ctx_or_int, embed=embed)

    try:
        guild_name = ctx_or_int.guild.name if ctx_or_int.guild else "the server"
        await member.send(f"⚠️ You have been warned in **{guild_name}** for: {reason}\nYou now have **{warning_count}** warning(s).")
    except:
        pass

    timeout_durations = {3: timedelta(hours=6), 4: timedelta(days=7), 5: timedelta(days=7), 6: timedelta(days=7)}
    if warning_count >= 3:
        timeout_duration = timeout_durations.get(warning_count, timeout_durations[6])
        try:
            await member.timeout(timeout_duration, reason=f"Auto-timeout for {warning_count} warnings")
            hours = timeout_duration.total_seconds() / 3600
            await get_channel(ctx_or_int).send(f"🔇 {member.mention} has been timed out for {hours:.1f} hours.")
        except Exception as e:
            print(f"⚠️ Failed to timeout user: {e}")
            await get_channel(ctx_or_int).send(f"❌ Couldn't timeout {member.mention}. Missing permissions?")


# ==================== COMMANDS ====================

@bot.command(name="help")
async def help_command(ctx):
    """Show help"""
    await perform_help(ctx)

@bot.tree.command(name="help", description="Show all available commands")
async def help_slash(interaction: discord.Interaction):
    """Slash help"""
    await perform_help(interaction)

async def perform_help(ctx_or_int):
    embed = discord.Embed(
        title="🤖 Bot Commands",
        description="Here are all available commands:",
        color=0x7289DA
    )
    embed.add_field(name="🔹 !warn <@user> <reason>", value="Warns a user", inline=False)
    embed.add_field(name="🔹 !kick <@user> <reason>", value="Kicks a user", inline=False)
    embed.add_field(name="🔹 !ban <@user> <reason>", value="Bans a user", inline=False)
    embed.add_field(name="🔹 !unban <user_id>", value="Unbans a user", inline=False)
    embed.add_field(name="🔹 !mute <user>, <duration>, <reason>", value="Mutes a user (e.g. 10m, 2h)", inline=False)
    embed.add_field(name="🔹 !unmute <user>", value="Unmutes a user", inline=False)
    embed.add_field(name="🔹 !timeout <user>, <duration>, <reason>", value="Alias for mute", inline=False)
    embed.add_field(name="🔹 !clear <amount>", value="Delete messages", inline=False)
    embed.add_field(name="🔹 !hwarn <@user>", value="Shows history", inline=False)
    embed.add_field(name="🔹 !lwarn", value="Shows leaderboard", inline=False)
    embed.add_field(name="🔹 !allwarn", value="Shows all warnings", inline=False)
    embed.add_field(name="🔹 !modrole <@role>", value="Add/remove moderator role", inline=False)
    embed.add_field(name="🔹 !modroles", value="List moderator roles", inline=False)
    embed.add_field(name="🔹 !reset", value="Reset bot data", inline=False)
    embed.add_field(name="🔹 !shutdown", value="Shutdown the bot", inline=False)
    embed.add_field(name="🔹 Jarvis warn @user for reason", value="Natural language commands", inline=False)
    embed.add_field(name="✨ Note", value="All commands are also available as slash commands (e.g. `/warn`)", inline=False)

    await send_response(ctx_or_int, embed=embed)


@bot.command(name="warn")
async def warn_command(ctx, member: discord.Member = None, *, reason: str = None):
    """Warn a user"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    if not member or not reason:
        await ctx.reply("⚠️ Usage: `!warn @user reason`")
        return
    await handle_warn(ctx, member, reason)

@bot.tree.command(name="warn", description="Warn a user")
@app_commands.describe(member="The user to warn", reason="The reason for the warning")
async def warn_slash(interaction: discord.Interaction, member: discord.Member, reason: str):
    """Slash command for warning"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await handle_warn(interaction, member, reason)


@bot.command(name="hwarn")
async def history_warn(ctx, member: discord.Member = None):
    """Show warning and mute history"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return

    if not member:
        await ctx.reply("⚠️ Please mention a user.")
        return

    await send_history(ctx, member)

@bot.tree.command(name="warnh", description="Show a user's warning & mute history")
@app_commands.describe(member="The user to check")
async def warnh_slash(interaction: discord.Interaction, member: discord.Member):
    """Slash command version of hwarn"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return

    await send_history(interaction, member)

async def send_history(ctx_or_interaction, member: discord.Member):
    """shared logic for showing history"""
    all_warnings = load_data(WARNINGS_FILE)
    warnings = [w for w in all_warnings if w["userId"] == str(member.id)]
    
    all_mutes = load_data(MUTES_FILE)
    mutes = [m for m in all_mutes if m["userId"] == str(member.id)]

    if not warnings and not mutes:
        msg = f"✅ **{member.name}** has a clean history!"
        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(msg)
        else:
            await ctx_or_interaction.reply(msg)
        return

    # Build warning text
    warning_text = "No warnings found."
    if warnings:
        warning_lines = []
        for i, w in enumerate(warnings, 1):
            dt = datetime.fromisoformat(w["timestamp"])
            timestamp = int(dt.timestamp())
            warning_lines.append(f"**{i}.** {w['reason']} (by <@{w['moderatorId']}> on <t:{timestamp}:d>)")
        warning_text = "\n".join(warning_lines)

    # Build mute text
    mute_text = "No mutes found."
    if mutes:
        mute_lines = []
        for i, m in enumerate(mutes, 1):
            dt = datetime.fromisoformat(m["timestamp"])
            timestamp = int(dt.timestamp())
            duration_min = m["durationSec"] // 60
            mute_lines.append(f"**{i}.** {m['reason']} — {duration_min}m (by <@{m['moderatorId']}> on <t:{timestamp}:d>)")
        mute_text = "\n".join(mute_lines)

    embed = discord.Embed(
        title=f"📜 History for {member.name}",
        color=0xffaa00
    )
    embed.add_field(name="⚠️ Warnings", value=warning_text, inline=False)
    embed.add_field(name="🔇 Mutes", value=mute_text, inline=False)

    if isinstance(ctx_or_interaction, discord.Interaction):
        await ctx_or_interaction.response.send_message(embed=embed)
    else:
        await ctx_or_interaction.send(embed=embed)


@bot.command(name="allwarn")
async def all_warnings_command(ctx):
    """Show all warnings"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    await perform_allwarn(ctx)

@bot.tree.command(name="allwarn", description="Show all warnings in the database")
async def allwarn_slash(interaction: discord.Interaction):
    """Slash allwarn"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_allwarn(interaction)

async def perform_allwarn(ctx_or_int):
    all_warns = load_data(WARNINGS_FILE)
    all_warns.sort(key=lambda x: x["timestamp"], reverse=True)

    if not all_warns:
        await send_response(ctx_or_int, "✅ No warnings found in the entire database.")
        return

    pages = []
    current_page = []
    current_length = 0

    for w in all_warns:
        dt = datetime.fromisoformat(w["timestamp"])
        timestamp = int(dt.timestamp())
        line = f"• <@{w['userId']}> — {w['reason']} (by <@{w['moderatorId']}> on <t:{timestamp}:f>)\n"
        if current_length + len(line) > 4000:
            pages.append("".join(current_page))
            current_page = []
            current_length = 0
        current_page.append(line)
        current_length += len(line)

    if current_page:
        pages.append("".join(current_page))

    for i, page in enumerate(pages, 1):
        embed = discord.Embed(
            title=f"📜 All Warnings ({len(all_warns)} total) — Page {i}/{len(pages)}",
            description=page,
            color=0xff4444
        )
        if i == 1 and isinstance(ctx_or_int, discord.Interaction):
            await ctx_or_int.response.send_message(embed=embed)
        else:
            await get_channel(ctx_or_int).send(embed=embed)

@bot.command(name="lwarn")
async def leaderboard_warn_command(ctx):
    """Show leaderboard"""
    await perform_lwarn(ctx)

@bot.tree.command(name="lwarn", description="Show warning leaderboard")
async def lwarn_slash(interaction: discord.Interaction):
    """Slash lwarn"""
    await perform_lwarn(interaction)

async def perform_lwarn(ctx_or_int):
    all_warns = load_data(WARNINGS_FILE)
    if not all_warns:
        await send_response(ctx_or_int, "✅ No warnings found.")
        return

    counts = {}
    for warn in all_warns:
        user_id = warn["userId"]
        counts[user_id] = counts.get(user_id, 0) + 1

    sorted_users = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    description_lines = []
    for i, (user_id, count) in enumerate(sorted_users, 1):
        try:
            user = await bot.fetch_user(int(user_id))
            username = user.name
        except:
            username = f"Unknown User ({user_id})"
        plural = "s" if count > 1 else ""
        description_lines.append(f"**{i}.** {username} — {count} warning{plural}")

    embed = discord.Embed(
        title="🏆 Warning Leaderboard",
        description="\n".join(description_lines),
        color=0xff9900
    )
    if isinstance(ctx_or_int, discord.Interaction):
        await ctx_or_int.response.send_message(embed=embed)
    else:
        await ctx_or_int.send(embed=embed)


@bot.command(name="mute")
async def mute_command(ctx, *, args: str = None):
    """Mute a user - Usage: !mute <user>, <duration>, <reason>"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    if not args:
        await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`\nExample: `!mute @User, 10m, spamming`")
        return
    parts = [p.strip() for p in args.split(',', 2)]
    if len(parts) < 3:
        await ctx.reply("⚠️ Usage: `!mute <user>, <duration>, <reason>`\nExample: `!mute @User, 10m, spamming`")
        return
    user_input, duration_str, reason = parts
    member = await get_member_flexible(ctx, user_input)
    if not member:
        await ctx.reply(f"❌ Could not find user: {user_input}")
        return
    await handle_mute(ctx, member, duration_str, reason)

@bot.tree.command(name="mute", description="Mute a user")
@app_commands.describe(member="The user to mute", duration="Duration (e.g. 10m, 2h)", reason="The reason for the mute")
async def mute_slash(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    """Slash mute"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await handle_mute(interaction, member, duration, reason)

async def handle_mute(ctx_or_int, member: discord.Member, duration_str: str, reason: str):
    """Shared mute logic"""
    duration_seconds = parse_duration(duration_str)
    if not duration_seconds:
        await send_response(ctx_or_int, "⚠️ Invalid duration. Use format: `10s`, `5m`, `2h`, `1d`")
        return

    try:
        await member.timeout(timedelta(seconds=duration_seconds), reason=reason)
        mutes = load_data(MUTES_FILE)
        mutes.append({
            "id": str(uuid.uuid4()),
            "userId": str(member.id),
            "moderatorId": str(get_author(ctx_or_int).id),
            "reason": reason,
            "durationSec": duration_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        save_data(MUTES_FILE, mutes)

        if duration_seconds < 60: display_duration = f"{duration_seconds}s"
        elif duration_seconds < 3600: display_duration = f"{duration_seconds // 60}m"
        elif duration_seconds < 86400: display_duration = f"{duration_seconds // 3600}h"
        else: display_duration = f"{duration_seconds // 86400}d"

        await send_response(ctx_or_int, f"🔇 **{member.mention}** muted for **{display_duration}**. Reason: {reason}")
    except Exception as e:
        print(f"Mute error: {e}")
        await send_response(ctx_or_int, "❌ Failed to mute the user. Check bot permissions.")


@bot.command(name="unmute")
async def unmute_command(ctx, member: discord.Member = None):
    """Unmute a user"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    if not member:
        await ctx.reply("⚠️ Please mention a user.")
        return
    await handle_unmute(ctx, member)

@bot.tree.command(name="unmute", description="Unmute a user")
@app_commands.describe(member="The user to unmute")
async def unmute_slash(interaction: discord.Interaction, member: discord.Member):
    """Slash unmute"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await handle_unmute(interaction, member)

async def handle_unmute(ctx_or_int, member: discord.Member):
    """Shared unmute logic"""
    try:
        await member.timeout(None)
        await send_response(ctx_or_int, f"✅ **{member.mention}** has been unmuted.")
    except Exception as e:
        print(f"Unmute error: {e}")
        await send_response(ctx_or_int, "❌ Failed to unmute the user.")


async def handle_kick(ctx_or_int, member: discord.Member, reason: str):
    """Shared kick logic"""
    try:
        await member.kick(reason=reason)
        await send_response(ctx_or_int, f"👢 **{member.name}** has been kicked. Reason: {reason}")
    except Exception as e:
        await send_response(ctx_or_int, f"❌ Failed to kick: {e}")


async def handle_ban(ctx_or_int, member: discord.Member, reason: str):
    """Shared ban logic"""
    try:
        await member.ban(reason=reason)
        await send_response(ctx_or_int, f"🔨 **{member.name}** has been banned. Reason: {reason}")
    except Exception as e:
        await send_response(ctx_or_int, f"❌ Failed to ban: {e}")


async def handle_unban(ctx_or_int, user_id: int):
    """Shared unban logic"""
    try:
        guild = get_channel(ctx_or_int).guild
        user = await bot.fetch_user(user_id)
        await guild.unban(user)
        await send_response(ctx_or_int, f"✅ **{user.name}** has been unbanned.")
    except Exception as e:
        await send_response(ctx_or_int, f"❌ Failed to unban: {e}")


async def handle_clear(ctx_or_int, amount: int):
    """Shared clear logic"""
    try:
        channel = get_channel(ctx_or_int)
        # If it's a context, it's a prefix command, so we should delete one more to include the command itself
        purge_limit = amount + 1 if isinstance(ctx_or_int, commands.Context) else amount
        deleted = await channel.purge(limit=purge_limit)
        await send_response(ctx_or_int, f"🧹 Cleared **{len(deleted)}** messages.", ephemeral=True)
    except Exception as e:
        await send_response(ctx_or_int, f"❌ Failed to clear messages: {e}", ephemeral=True)


# Kick command
@bot.command(name="kick")
async def kick_command(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    """Kick a user"""
    if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
    if not member: return await ctx.reply("⚠️ Usage: `!kick @user <reason>`")
    await handle_kick(ctx, member, reason)

@bot.tree.command(name="kick", description="Kick a user")
@app_commands.describe(member="The user to kick", reason="The reason for the kick")
async def kick_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    await handle_kick(interaction, member, reason)

# Ban command
@bot.command(name="ban")
async def ban_command(ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
    """Ban a user"""
    if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
    if not member: return await ctx.reply("⚠️ Usage: `!ban @user <reason>`")
    await handle_ban(ctx, member, reason)

@bot.tree.command(name="ban", description="Ban a user")
@app_commands.describe(member="The user to ban", reason="The reason for the ban")
async def ban_slash(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    await handle_ban(interaction, member, reason)

# Unban command
@bot.command(name="unban")
async def unban_command(ctx, user_input: str = None):
    """Unban a user"""
    if not is_moderator(ctx.author): return await ctx.reply("❌ Permission denied.")
    if not user_input: return await ctx.reply("⚠️ Usage: `!unban <user_id>`")
    user_id = await get_user_id(ctx, user_input)
    if not user_id: return await ctx.reply("❌ Invalid user ID.")
    await handle_unban(ctx, user_id)

@bot.tree.command(name="unban", description="Unban a user")
@app_commands.describe(user_id="The ID of the user to unban")
async def unban_slash(interaction: discord.Interaction, user_id: str):
    if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    try: await handle_unban(interaction, int(user_id))
    except: await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)

# Clear command
@bot.command(name="clear")
async def clear_command(ctx, amount: int = None):
    """Clear messages"""
    if not is_moderator(ctx.author): return
    if amount is None: return await ctx.reply("⚠️ Usage: `!clear <amount>`")
    await handle_clear(ctx, amount)

@bot.tree.command(name="clear", description="Clear messages")
@app_commands.describe(amount="Number of messages to clear")
async def clear_slash(interaction: discord.Interaction, amount: int):
    if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
    await handle_clear(interaction, amount)

# Timeout aliases
@bot.command(name="timeout")
async def timeout_command(ctx, *, args: str = None):
    """Alias for mute"""
    await mute_command(ctx, args=args)

@bot.tree.command(name="timeout", description="Timeout a user")
@app_commands.describe(member="The user to timeout", duration="Duration (e.g. 10m, 2h)", reason="The reason for the timeout")
async def timeout_slash(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    await mute_slash(interaction, member, duration, reason)

@bot.command(name="untimeout")
async def untimeout_command(ctx, member: discord.Member = None):
    """Alias for unmute"""
    await unmute_command(ctx, member=member)

@bot.tree.command(name="untimeout", description="Untimeout a user")
@app_commands.describe(member="The user to untimeout")
async def untimeout_slash(interaction: discord.Interaction, member: discord.Member):
    await unmute_slash(interaction, member)


@bot.command(name="delwarn")
async def delete_warn_command(ctx, member: discord.Member = None):
    """Delete warnings"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    if not member:
        await ctx.reply("⚠️ Please mention a user.")
        return
    await perform_delwarn(ctx, member)

@bot.tree.command(name="delwarn", description="Remove specific warnings from a user")
@app_commands.describe(member="The user to remove warnings from")
async def delwarn_slash(interaction: discord.Interaction, member: discord.Member):
    """Slash delwarn"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_delwarn(interaction, member)

async def perform_delwarn(ctx_or_int, member: discord.Member):
    all_warnings = load_data(WARNINGS_FILE)
    user_warnings = [w for w in all_warnings if w["userId"] == str(member.id)]

    if not user_warnings:
        await send_response(ctx_or_int, f"✅ **{member.name}** has no warnings.")
        return

    options = []
    for i, w in enumerate(user_warnings, 1):
        options.append(
            discord.SelectOption(
                label=f"Warning {i}: {w['reason'][:50]}",
                description=f"Issued by moderator",
                value=w["id"]
            )
        )

    select = discord.ui.Select(
        placeholder="Select warnings to remove...",
        options=options,
        min_values=1,
        max_values=len(options)
    )

    async def select_callback(interaction):
        selected_ids = select.values
        updated_warnings = [w for w in load_data(WARNINGS_FILE) if w["id"] not in selected_ids]
        save_data(WARNINGS_FILE, updated_warnings)
        embed = discord.Embed(title="✅ Selected Warnings Deleted", color=0x00ff00)
        await interaction.response.edit_message(embed=embed, view=None)

    select.callback = select_callback
    view = discord.ui.View()
    view.add_item(select)

    embed = discord.Embed(
        title=f"🚨 Remove Warnings for {member.name}",
        description="Select which warnings to delete.",
        color=0xffcc00
    )

    if isinstance(ctx_or_int, discord.Interaction):
        await ctx_or_int.response.send_message(embed=embed, view=view)
    else:
        await ctx_or_int.send(embed=embed, view=view)


@bot.command(name="clearwarns")
async def clearwarns_command(ctx, member: discord.Member = None):
    """Clear all warnings for a user"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    if not member:
        await ctx.reply("⚠️ Please mention a user.")
        return
    await perform_clearwarns(ctx, member)

@bot.tree.command(name="clearwarns", description="Clear all warnings for a specific user")
@app_commands.describe(member="The user whose warnings to clear")
async def clearwarns_slash(interaction: discord.Interaction, member: discord.Member):
    """Slash clearwarns"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_clearwarns(interaction, member)


@bot.command(name="modrole")
async def permissions_command(ctx, role: discord.Role = None):
    """Add or remove mod role"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("❌ Admins only.")
        return
    if not role:
        await ctx.reply("⚠️ Mention a role.")
        return
    await handle_perm(ctx, role)


@bot.command(name="modroles")
async def modroles_list_command(ctx):
    """List moderator roles"""
    if not is_moderator(ctx.author):
        return
    if not moderator_roles:
        await ctx.reply("📜 No moderator roles configured.")
        return
    roles_text = "\n".join([f"• <@&{rid}>" for rid in moderator_roles])
    await ctx.reply(f"🛡️ **Moderator Roles:**\n{roles_text}")


@bot.tree.command(name="modrole", description="Set moderator role")
@app_commands.describe(role="The role to toggle")
@app_commands.default_permissions(administrator=True)
async def perm_slash(interaction: discord.Interaction, role: discord.Role):
    """Slash modrole"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    await handle_perm(interaction, role)


@bot.tree.command(name="modroles", description="List moderator roles")
async def modroles_slash(interaction: discord.Interaction):
    """Slash modroles"""
    if not is_moderator(interaction.user):
        return
    if not moderator_roles:
        await interaction.response.send_message("📜 No moderator roles configured.", ephemeral=True)
        return
    roles_text = "\n".join([f"• <@&{rid}>" for rid in moderator_roles])
    await interaction.response.send_message(f"🛡️ **Moderator Roles:**\n{roles_text}")

async def handle_perm(ctx_or_int, role: discord.Role):
    """Shared perm logic"""
    if role.id in moderator_roles:
        moderator_roles.remove(role.id)
        save_moderator_roles()
        await send_response(ctx_or_int, f"🗑️ Removed moderator permissions from role {role.name}")
    else:
        moderator_roles.append(role.id)
        save_moderator_roles()
        await send_response(ctx_or_int, f"✅ Added moderator permissions to role {role.name}")

@bot.command(name="reset")
async def reset_bot_command(ctx):
    """Reset bot data (warnings and mutes)"""
    if not is_moderator(ctx.author):
        return
    await perform_reset_all(ctx)


@bot.tree.command(name="reset", description="Reset bot data (warnings and mutes)")
async def reset_slash(interaction: discord.Interaction):
    """Slash reset"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_reset_all(interaction)


async def perform_reset_all(ctx_or_int):
    """Shared reset all logic"""
    view = discord.ui.View()

    async def yes_callback(interaction):
        save_data(WARNINGS_FILE, [])
        save_data(MUTES_FILE, [])
        await interaction.response.edit_message(content="✅ All bot data (warnings and mutes) has been reset.", view=None)

    async def cancel_callback(interaction):
        await interaction.response.edit_message(content="❌ Reset cancelled.", view=None)

    yes_btn = discord.ui.Button(label="Reset Everything", style=discord.ButtonStyle.danger)
    yes_btn.callback = yes_callback
    no_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
    no_btn.callback = cancel_callback
    view.add_item(yes_btn)
    view.add_item(no_btn)
    await send_response(ctx_or_int, "⚠️ **WARNING:** This will clear ALL warnings and mutes from the database. Are you sure?", view=view)



@bot.command(name="resetwarns")
async def reset_warnings_command(ctx):
    """Reset all warnings"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    await perform_resetwarns(ctx)

@bot.tree.command(name="resetwarns", description="Reset all warnings with an undo option")
async def resetwarns_slash(interaction: discord.Interaction):
    """Slash resetwarns"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_resetwarns(interaction)

async def perform_resetwarns(ctx_or_int):
    view = discord.ui.View()
    async def yes_callback(interaction):
        global reset_backup
        reset_backup = load_data(WARNINGS_FILE)
        save_data(WARNINGS_FILE, [])
        undo_view = discord.ui.View()
        undo_button = discord.ui.Button(label="Undo", style=discord.ButtonStyle.primary)
        async def undo_callback(undo_interaction):
            global reset_backup
            if not reset_backup:
                await undo_interaction.response.edit_message(content="❌ Undo period expired.", view=None)
                return
            save_data(WARNINGS_FILE, reset_backup)
            reset_backup = None
            await undo_interaction.response.edit_message(content="✅ Warnings restored successfully!", view=None)
        undo_button.callback = undo_callback
        undo_view.add_item(undo_button)
        await interaction.response.edit_message(content="✅ All warnings reset! You can undo within 10 minutes.", view=undo_view)
        await asyncio.sleep(600)
        reset_backup = None
        done_view = discord.ui.View()
        done_button = discord.ui.Button(label="All warnings cleared", style=discord.ButtonStyle.secondary, disabled=True)
        done_view.add_item(done_button)
        try: await interaction.message.edit(content="✅ All warnings permanently cleared.", view=done_view)
        except: pass

    async def cancel_callback(interaction):
        await interaction.response.edit_message(content="❌ Reset cancelled.", view=None)

    yes_button = discord.ui.Button(label="Yes", style=discord.ButtonStyle.danger)
    yes_button.callback = yes_callback
    cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
    cancel_button.callback = cancel_callback
    view.add_item(yes_button)
    view.add_item(cancel_button)

    msg = "⚠️ Are you sure you want to reset **EVERYONE's warnings**? You can undo within 10 minutes."
    await send_response(ctx_or_int, content=msg, view=view)


async def perform_clearwarns(ctx_or_int, member: discord.Member):
    """Clear all warnings for a specific user (used by Jarvis and commands)"""
    all_warnings = load_data(WARNINGS_FILE)
    user_warnings = [w for w in all_warnings if w["userId"] == str(member.id)]
    
    if not user_warnings:
        await send_response(ctx_or_int, content=f"✅ **{member.name}** has no warnings to clear.")
        return
    
    # Remove all warnings for this user
    updated_warnings = [w for w in all_warnings if w["userId"] != str(member.id)]
    save_data(WARNINGS_FILE, updated_warnings)
    
    embed = discord.Embed(
        title=f"✅ Warnings Cleared",
        description=f"All **{len(user_warnings)}** warning(s) for **{member.name}** have been removed.",
        color=0x00ff00
    )
    await send_response(ctx_or_int, embed=embed)


@bot.command(name="shutdown")
async def shutdown_command(ctx):
    """Shutdown the bot"""
    if not is_moderator(ctx.author):
        await ctx.reply("❌ You don't have permission.")
        return
    await perform_shutdown(ctx)

@bot.tree.command(name="shutdown", description="Shutdown the bot")
async def shutdown_slash(interaction: discord.Interaction):
    """Slash shutdown"""
    if not is_moderator(interaction.user):
        await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
        return
    await perform_shutdown(interaction)

async def perform_shutdown(ctx_or_int):
    await send_response(ctx_or_int, "✅ Shutting down, sir. Until next time.")
    print(f"🔴 Bot shutdown by {get_author(ctx_or_int).name}")
    await bot.close()


@bot.command(name="sync")
async def sync_commands(ctx):
    """Sync slash commands (Admins only)"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.reply("❌ You need Administrator permission to use this.")
        return

    try:
        synced = await bot.tree.sync()
        await ctx.reply(f"✅ Synced {len(synced)} slash commands.")
    except Exception as e:
        await ctx.reply(f"❌ Failed to sync: {e}")


# ==================== RUN BOT ====================

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("❌ No TOKEN found in .env file!")
    else:
        print(f"✅ Token loaded (starts with: {token[:5]}...)")
        bot.run(token)