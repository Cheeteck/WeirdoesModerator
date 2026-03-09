import os
import json
import discord
from discord.ext import commands
from module_utils import Module, load_server_data
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

    async def handle_jarvis_command(self, message: discord.Message, query: str):
        if not groq_client: return await message.reply("❌ Groq API key is not configured.")
        try:
            system_prompt = f"""
You are an AI moderator assistant for a Discord bot. Parse the user's natural language request and determine the appropriate moderation action.

REASON REPHRASING:
You may rephrase the reason if its unclear. Things like "Spam" or "Brainrot" does not count as unclear

Available actions:
1. warn (args: user_id, reason)
2. mute (args: user_id, duration, reason)
3. unmute (args: user_id)
4. kick (args: user_id, reason)
5. ban (args: user_id, reason)
6. unban (args: user_id)
7. help (no args)
8. hwarn (args: user_id)
9. allwarns (no args)
10. clearwarns (args: user_id)
11. resetwarns (no args)
12. lock (args: channel_id)
13. unlock (args: channel_id)
14. lockdown (no args)

Respond ONLY with a JSON object:
{{"action": "action_name", "args": {{"arg1": "val1"}}}}

Duration format for mute: '10s', '5m', '2h', '1d'.
"""
            mentions_info = "\n".join([f"Name: {m.name}, ID: {m.id}" for m in message.mentions])
            user_context = f"Mentioned Users:\n{mentions_info}\n\nUser Message: {query}"
            
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_context}
                ],
                response_format={"type": "json_object"}
            )
            
            res = json.loads(completion.choices[0].message.content)
            action = res.get("action")
            args = res.get("args", {})
            
            from module_utils import is_module_enabled
            
            core_cog = self.bot.get_cog("Core")
            we_cog = self.bot.get_cog("WarnsExtras")
            we_enabled = is_module_enabled(message.guild.id, "WarnsExtras")
            
            if action == "unban":
                user = await self.bot.fetch_user(int(args["user_id"]))
                try: 
                    await message.guild.unban(user)
                    await message.reply(f"✅ Unbanned {user.name}")
                except Exception as e: await message.reply(f"❌ Error: {e}")
                return

            # All subsequent actions involve a member target
            target = await message.guild.fetch_member(int(args["user_id"]))
            
            if target == message.guild.owner:
                return await message.reply("❌ I cannot perform moderation actions on the server owner, sir.")

            if action == "warn" and core_cog:
                await core_cog.execute_warn(message, target, args.get("reason", "None"))
            elif action == "mute" and core_cog:
                await core_cog.execute_mute(message, target, args.get("duration", "10m"), args.get("reason", "None"))
            elif action == "unmute" and core_cog:
                try:
                    await target.timeout(None)
                    await message.reply(f"✅ Unmuted {target.mention}")
                except Exception as e: await message.reply(f"❌ Error: {e}")
            elif action == "kick":
                try: 
                    await target.kick(reason=args.get("reason"))
                    await message.reply(f"👢 Kicked {target.name}")
                except Exception as e: await message.reply(f"❌ Error: {e}")
            elif action == "ban":
                reason = args.get("reason", "None")
                view = discord.ui.View()
                confirm = discord.ui.Button(label="Confirm Ban", style=discord.ButtonStyle.danger)
                cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
                
                async def confirm_callback(interaction):
                    if not is_moderator(interaction.user):
                        return await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
                    
                    # Defer to prevent 3-second timeout
                    await interaction.response.defer()
                    
                    try:
                        await target.ban(reason=reason)
                        await interaction.edit_original_response(content=f"🔨 **{target.name}** has been banned. Reason: {reason}", view=None)
                    except Exception as e:
                        try:
                            await interaction.edit_original_response(content=f"❌ Failed to ban: {e}", view=None)
                        except:
                            pass

                async def cancel_callback(interaction):
                    await interaction.response.edit_message(content="❌ Ban operation cancelled.", view=None)

                confirm.callback = confirm_callback
                cancel.callback = cancel_callback
                view.add_item(confirm).add_item(cancel)
                
                await message.reply(f"🚨 **Sir, are you sure you want to ban {target.mention}?**\n**Reason:** {reason}", view=view)
                
            elif action == "hwarn" and core_cog:
                await core_cog.execute_hwarn(message, target)
            elif action == "allwarns" and we_cog and we_enabled:
                await we_cog.allwarns_command(message)
            elif action == "clearwarns" and we_cog and we_enabled:
                await we_cog.clearwarns_command(message, target)
            elif action == "resetwarns" and we_cog and we_enabled:
                await we_cog.resetwarns_command(message)
            elif action == "help" and core_cog:
                await core_cog.perform_help(message)
            elif action == "lock":
                ld_cog = self.bot.get_cog("Lockdown")
                if ld_cog and is_module_enabled(message.guild.id, "Lockdown"):
                    ch_id = args.get("channel_id")
                    channel = message.guild.get_channel(int(ch_id)) if ch_id else message.channel
                    await ld_cog.ld_lock(message, channel)
                else: await message.reply("❌ Lockdown module is not enabled.")
            elif action == "unlock":
                ld_cog = self.bot.get_cog("Lockdown")
                if ld_cog and is_module_enabled(message.guild.id, "Lockdown"):
                    ch_id = args.get("channel_id")
                    arg = str(ch_id) if ch_id else None
                    await ld_cog.ld_unlock(message, target_str=arg)
                else: await message.reply("❌ Lockdown module is not enabled.")
            elif action == "lockdown":
                ld_cog = self.bot.get_cog("Lockdown")
                if ld_cog and is_module_enabled(message.guild.id, "Lockdown"):
                    await ld_cog.lockdown_group(message)
                else: await message.reply("❌ Lockdown module is not enabled.")
            else:
                await message.reply(f"🤔 Action '{action}' failed (either invalid or module isn't loaded/enabled).")

        except Exception as e:
            await message.reply(f"❌ AI Error: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        
        config = load_server_data(message.guild.id, "config.json") or {}
        wakeword = config.get("natlang_wakeword", "WM").lower()
        
        # Exact start-match only to avoid triggering mid-sentence
        content_lower = message.content.strip().lower()
        if content_lower.split(" ")[0] == wakeword:
            from module_utils import is_module_enabled
            if not is_module_enabled(message.guild.id, "NatLang"): return
            
            if not is_moderator(message.author):
                return await message.reply("❌ Permission denied.")
                
            query = message.content.strip()[len(wakeword):].strip()
            if not query: return await message.reply("Yes?")
            await self.handle_jarvis_command(message, query)

async def setup(bot):
    await bot.add_cog(NatLang(bot))
