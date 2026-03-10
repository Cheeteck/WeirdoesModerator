import discord
from discord.ext import commands
from discord import app_commands
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

    async def _send_or_reply(self, target, content=None, embed=None, view=None, ephemeral=False):
        if isinstance(target, discord.Interaction):
            if target.response.is_done(): await target.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
            else: await target.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)
        else: await target.reply(content=content, embed=embed, view=view)

    @commands.Cog.listener()
    async def on_member_warned(self, member: discord.Member, count: int, reason: str):
        from module_utils import is_module_enabled
        if not is_module_enabled(member.guild.id, "WarnsExtras"): return
        if count >= 3:
            hours = (count - 2) * 6
            duration = timedelta(hours=hours)
            try:
                await member.timeout(duration, reason=f"Auto-timeout: Reached {count} warnings")
                await member.send(f"🔇 You have been automatically timed out in **{member.guild.name}** for {hours} hours due to reaching {count} warnings.")
            except Exception as e: print(f"Failed to auto-timeout user {member.name}: {e}")

    # ─── All Warns ───────────────────────────────────────────────────────────
    @app_commands.command(name="allwarns", description="Shows all warnings in this server")
    async def allwarns_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await self._do_allwarns(interaction)

    @commands.command(name="allwarns")
    async def allwarns_prefix(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Denied.")
        await self._do_allwarns(ctx)

    async def _do_allwarns(self, target):
        guild_id = target.guild.id
        warns = load_server_data(guild_id, "warnings.json") or []
        if not warns: return await self._send_or_reply(target, "✅ No warnings found.", ephemeral=True)
        warns.sort(key=lambda x: x["timestamp"], reverse=True)
        lines = [f"• <@{w['userId']}> — {w['reason']} (by <@{w['moderatorId']}> on <t:{int(datetime.fromisoformat(w['timestamp']).timestamp())}:f>)" for w in warns]
        embed = discord.Embed(title=f"📜 Server Warnings ({len(warns)})", description="\n".join(lines[:30]), color=0xff4444)
        if len(lines) > 30: embed.set_footer(text="Showing last 30 warnings.")
        await self._send_or_reply(target, embed=embed)

    # ─── Clear Warns ─────────────────────────────────────────────────────────
    @app_commands.command(name="clearwarns", description="Clears all warnings for a member")
    async def clearwarns_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await self._do_clearwarns(interaction, member)

    @commands.command(name="clearwarns")
    async def clearwarns_prefix(self, ctx, member: discord.Member):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Denied.")
        await self._do_clearwarns(ctx, member)

    async def _do_clearwarns(self, target, member):
        guild_id = target.guild.id
        warns = load_server_data(guild_id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        if not user_warns: return await self._send_or_reply(target, f"✅ {member.name} has no warnings.", ephemeral=True)
        updated = [w for w in warns if w["userId"] != str(member.id)]
        save_server_data(guild_id, "warnings.json", updated)
        await self._send_or_reply(target, f"✅ Cleared {len(user_warns)} warnings for {member.mention}.")

    # ─── Reset Warns ─────────────────────────────────────────────────────────
    @app_commands.command(name="resetwarns", description="RESET ALL WARNINGS IN THE WHOLE SERVER")
    async def resetwarns_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user): return await interaction.response.send_message("❌ Denied.", ephemeral=True)
        await self._do_resetwarns(interaction)

    @commands.command(name="resetwarns")
    async def resetwarns_prefix(self, ctx):
        if not is_moderator(ctx.author): return await ctx.reply("❌ Denied.")
        await self._do_resetwarns(ctx)

    async def _do_resetwarns(self, target):
        guild_id = target.guild.id
        old_warns = load_server_data(guild_id, "warnings.json") or []
        save_server_data(guild_id, "warnings.json", [])
        
        view = discord.ui.View()
        async def undo(intx):
            save_server_data(guild_id, "warnings.json", old_warns)
            await intx.response.edit_message(content="✅ Restored warnings.", view=None)
        
        btn = discord.ui.Button(label="Undo", style=discord.ButtonStyle.primary)
        btn.callback = undo
        view.add_item(btn)
        
        content = "🚨 **ALL warnings have been reset.** You can undo this now."
        if isinstance(target, discord.Interaction):
            await target.response.send_message(content, view=view)
        else:
            msg = await target.reply(content, view=view)
            await asyncio.sleep(600)
            try: await msg.edit(view=None)
            except: pass

async def setup(bot):
    await bot.add_cog(WarnsExtras(bot))
