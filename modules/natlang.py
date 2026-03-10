import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from module_utils import Module, load_server_data, is_module_enabled
from datetime import datetime, timezone
from groq import Groq
from modules.core import is_moderator

groq_client = Groq(api_key=os.getenv("GROQ")) if os.getenv("GROQ") else None

@Module.dependency.soft("WarnsExtras")
@Module.enabled()
@Module.help(
    commands={
        "WM <query>": "Natural language command execution"
    },
    description="NatLang AI router module."
)
class NatLang(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _send_or_reply(self, target, content, ephemeral=False):
        if isinstance(target, discord.Interaction):
            if target.response.is_done(): await target.followup.send(content, ephemeral=ephemeral)
            else: await target.response.send_message(content, ephemeral=ephemeral)
        else: await target.reply(content)

    @app_commands.command(name="wm", description="Execute moderation actions using natural language")
    async def wm_slash(self, interaction: discord.Interaction, query: str):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await self._do_natlang(interaction, query)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        config = load_server_data(message.guild.id, "config.json") or {}
        wakeword = config.get("natlang_wakeword", "WM").lower()
        if message.content.strip().lower().split(" ")[0] == wakeword:
            if not is_module_enabled(message.guild.id, "NatLang"): return
            if not is_moderator(message.author): return await message.reply("❌ Denied.")
            query = message.content.strip()[len(wakeword):].strip()
            if not query: return await message.reply("Yes?")
            await self._do_natlang(message, query)

    async def _do_natlang(self, target, query):
        if not groq_client: return await self._send_or_reply(target, "❌ Groq API key not configured.", ephemeral=True)
        
        # Determine guild/author/etc from target
        guild = target.guild
        author = target.user if isinstance(target, discord.Interaction) else target.author
        
        try:
            prompt = """You are an AI moderator assistant. Respond ONLY with a JSON object: {"action": "action_name", "args": {"arg1": "val1"}}
Actions: warn(user_id, reason), mute(user_id, duration, reason), unmute(user_id), kick(user_id, reason), ban(user_id, reason), unban(user_id), allwarns, clearwarns(user_id), resetwarns, lock(channel_id), unlock(channel_id), lockdown.
"""
            mentions = "\n".join([f"Name: {m.name}, ID: {m.id}" for m in (target.message.mentions if hasattr(target, 'message') else [])])
            ctx = f"Mentions:\n{mentions}\nQuery: {query}"
            
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": ctx}],
                response_format={"type": "json_object"}
            )
            res = json.loads(completion.choices[0].message.content)
            action, args = res.get("action"), res.get("args", {})
            
            core_cog = self.bot.get_cog("Core")
            we_cog = self.bot.get_cog("WarnsExtras")
            ld_cog = self.bot.get_cog("Lockdown")
            
            # Helper to generate message-like object for legacy method calls if needed
            pseudo_msg = target if not isinstance(target, discord.Interaction) else None
            
            if action == "unban":
                user = await self.bot.fetch_user(int(args["user_id"]))
                await guild.unban(user)
                return await self._send_or_reply(target, f"✅ Unbanned {user.name}")

            t_member = await guild.fetch_member(int(args["user_id"]))
            if t_member == guild.owner: return await self._send_or_reply(target, "❌ Cannot moderate owner.", ephemeral=True)

            if action == "warn" and core_cog: await core_cog._do_warn(target, t_member, args.get("reason", "AI-decision"))
            elif action == "mute" and core_cog: await core_cog._do_mute(target, t_member, args.get("duration", "10m"), args.get("reason", "AI-decision"))
            elif action == "unmute" and core_cog: 
                await t_member.timeout(None)
                await self._send_or_reply(target, f"✅ Unmuted {t_member.mention}")
            elif action == "kick":
                await t_member.kick(reason=args.get("reason"))
                await self._send_or_reply(target, f"👢 Kicked {t_member.name}")
            elif action == "ban":
                await t_member.ban(reason=args.get("reason"))
                await self._send_or_reply(target, f"🔨 Banned {t_member.name}")
            elif action == "hwarn" and core_cog: await core_cog._do_hwarn(target, t_member)
            elif action == "allwarns" and we_cog: await we_cog._do_allwarns(target)
            elif action == "clearwarns" and we_cog: await we_cog._do_clearwarns(target, t_member)
            elif action == "resetwarns" and we_cog: await we_cog._do_resetwarns(target)
            elif action == "lock" and ld_cog:
                c_id = args.get("channel_id")
                ch = guild.get_channel(int(c_id)) if c_id else (target.channel if hasattr(target, 'channel') else target.channel)
                await ld_cog._lock_channel(ch)
                await self._send_or_reply(target, f"🔒 Locked {ch.mention}")
            elif action == "unlock" and ld_cog:
                c_id = args.get("channel_id")
                ch = guild.get_channel(int(c_id)) if c_id else (target.channel if hasattr(target, 'channel') else target.channel)
                await ld_cog._unlock_channel(ch)
                await self._send_or_reply(target, f"🔓 Unlocked {ch.mention}")
            elif action == "lockdown" and ld_cog:
                count = 0
                for ch in guild.channels:
                    if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                        try: await ld_cog._lock_channel(ch); count += 1
                        except: pass
                await self._send_or_reply(target, f"🔒 Locked {count} channels.")
            else: await self._send_or_reply(target, f"🤔 Unknown action: {action}", ephemeral=True)

        except Exception as e: await self._send_or_reply(target, f"❌ AI Error: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(NatLang(bot))
