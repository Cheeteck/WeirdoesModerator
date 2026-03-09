import discord
from discord.ext import commands
from module_utils import Module, load_server_data, save_server_data
from datetime import datetime, timedelta
import asyncio
from modules.core import is_moderator

@Module.enabled()
@Module.help(
    commands={
        "allwarns": "shows all warns in a server",
        "clearwarns": "clears all warns from a user",
        "resetwarns": "clears all warns in the whole server"
    },
    description="WarnsExtras handles advanced warning features and auto punishments."
)
class WarnsExtras(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_warned(self, member: discord.Member, count: int, reason: str):
        from module_utils import is_module_enabled
        if not is_module_enabled(member.guild.id, "WarnsExtras"):
            return
            
        if count >= 3:
            hours = (count - 2) * 6
            duration = timedelta(hours=hours)
            try:
                await member.timeout(duration, reason=f"Auto-timeout: Reached {count} warnings")
                await member.send(f"🔇 You have been automatically timed out in **{member.guild.name}** for {hours} hours due to reaching {count} warnings.")
            except Exception as e:
                print(f"Failed to auto-timeout user {member.name}: {e}")

    @commands.command(name="allwarns")
    async def allwarns_command(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Admins only.")
        warns = load_server_data(ctx.guild.id, "warnings.json") or []
        if not warns: return await ctx.reply("✅ No warnings found in this server.")
        warns.sort(key=lambda x: x["timestamp"], reverse=True)
        lines = [f"• <@{w['userId']}> — {w['reason']} (by <@{w['moderatorId']}> on <t:{int(datetime.fromisoformat(w['timestamp']).timestamp())}:f>)" for w in warns]
        embed = discord.Embed(title=f"📜 All Warnings ({len(warns)} total)", description="\n".join(lines[:50]), color=0xff4444)
        if len(lines) > 50: embed.set_footer(text="Showing last 50 warnings.")
        await ctx.reply(embed=embed)

    @commands.command(name="clearwarns")
    async def clearwarns_command(self, ctx, member: discord.Member = None):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Admins only.")
        if not member: return await ctx.reply("⚠️ Specify a member.")
        warns = load_server_data(ctx.guild.id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        if not user_warns: return await ctx.reply(f"✅ {member.name} has no warnings.")
        updated_warnings = [w for w in warns if w["userId"] != str(member.id)]
        save_server_data(ctx.guild.id, "warnings.json", updated_warnings)
        await ctx.reply(f"✅ Cleared all {len(user_warns)} warnings for {member.name}.")

    @commands.command(name="resetwarns")
    async def resetwarns_command(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Admins only.")
        
        warns = load_server_data(ctx.guild.id, "warnings.json") or []
        save_server_data(ctx.guild.id, "warnings.json", [])
        
        undo_view = discord.ui.View()
        undo_button = discord.ui.Button(label="Undo", style=discord.ButtonStyle.primary)
        
        async def undo_callback(interaction):
            save_server_data(interaction.guild_id, "warnings.json", warns)
            await interaction.response.edit_message(content="✅ Warnings restored successfully!", view=None)
            
        undo_button.callback = undo_callback
        undo_view.add_item(undo_button)
        msg = await ctx.reply("✅ All warnings for this server reset! You can undo within 10 minutes.", view=undo_view)
        
        await asyncio.sleep(600)
        try:
            done_view = discord.ui.View()
            done_view.add_item(discord.ui.Button(label="Warnings cleared", style=discord.ButtonStyle.secondary, disabled=True))
            await msg.edit(content="✅ Server warnings permanently cleared.", view=done_view)
        except:
            pass

async def setup(bot):
    await bot.add_cog(WarnsExtras(bot))
