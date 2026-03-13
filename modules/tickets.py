import discord
from discord.ext import commands
from discord import app_commands
from module_utils import Module, load_server_data, save_server_data
from modules.core import is_moderator, get_moderator_roles
import asyncio

class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_create_btn", emoji="🎫")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        from module_utils import is_module_enabled
        if not getattr(interaction, "guild", None) or not is_module_enabled(interaction.guild_id, "Tickets"):
            return await interaction.response.send_message("❌ Tickets module is disabled.", ephemeral=True)
            
        data = load_server_data(interaction.guild_id, "tickets.json") or {}
        cat_id = data.get("category_id")
        if not cat_id:
            return await interaction.response.send_message("❌ Ticket system not set up properly.", ephemeral=True)
            
        category = interaction.guild.get_channel(cat_id)
        if not category:
            return await interaction.response.send_message("❌ Tickets category missing.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True)
        }
        mod_roles = get_moderator_roles(interaction.guild_id)
        ping_roles = []
        for role_id in mod_roles:
            role = interaction.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
                ping_roles.append(role)
                
        ticket_channel = await interaction.guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket Owner: {interaction.user.name} | User ID: {interaction.user.id}"
        )
        
        mentions = " ".join([r.mention for r in ping_roles]) if ping_roles else ""
        await ticket_channel.send(f"Welcome {interaction.user.mention}! {mentions}\nPlease describe your issue.")
        
        try:
            await interaction.followup.send(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)
        except:
            pass

@Module.version("1.1")
@Module.enabled()
@Module.help(
    commands={
        "tickets setup": "Set up the ticket system",
        "tickets close": "Close the current ticket",
        "tickets reopen": "Reopen a closed ticket",
        "tickets create <user>": "Create a ticket for another user",
        "tickets remove-system": "Removes the ticket system"
    },
    description="Customizable ticket management module."
)
class Tickets(commands.Cog):
    tickets_group = app_commands.Group(name="tickets", description="Tickets module commands")

    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(TicketButton())

    async def _send_or_reply(self, ctx_or_int, content, **kwargs):
        if isinstance(ctx_or_int, discord.Interaction):
            if ctx_or_int.response.is_done():
                await ctx_or_int.followup.send(content, **kwargs)
            else:
                await ctx_or_int.response.send_message(content, **kwargs)
        else:
            await ctx_or_int.reply(content, **kwargs)

    # ─── Setup ────────────────────────────────────────────────────────────────
    @tickets_group.command(name="setup", description="Set up the ticket system")
    async def setup_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user, min_level=3):
            return await interaction.response.send_message("❌ Administrator permission (Level 3) required.", ephemeral=True)
        await self._do_setup(interaction)

    @commands.group(name="tickets", aliases=["ticket"], invoke_without_command=True)
    async def tickets_cmd(self, ctx):
        await ctx.reply("Usage: `!tickets setup|close|reopen|create|remove-system`")

    @tickets_cmd.command(name="setup")
    async def setup_prefix(self, ctx):
        if not is_moderator(ctx.author, min_level=3):
            return await ctx.reply("❌ Administrator permission (Level 3) required.")
        await self._do_setup(ctx)

    async def _do_setup(self, target):
        guild = target.guild
        cat_tickets = await guild.create_category("Tickets")
        cat_resolved = await guild.create_category("Resolved tickets")
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, read_message_history=True)
        }
        
        ticket_channel = await guild.create_text_channel("tickets", category=None, overwrites=overwrites, position=0)
        embed = discord.Embed(title="🎫 Support Tickets", description="Click the button below to open a ticket.", color=0x5865F2)
        await ticket_channel.send(embed=embed, view=TicketButton())
        
        data = {
            "category_id": cat_tickets.id,
            "resolved_id": cat_resolved.id,
            "channel_id": ticket_channel.id
        }
        save_server_data(guild.id, "tickets.json", data)
        await self._send_or_reply(target, f"✅ Ticket system established in {ticket_channel.mention}.")

    # ─── Close ────────────────────────────────────────────────────────────────
    @tickets_group.command(name="close", description="Close the current ticket")
    async def close_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user, min_level=1):
            return await interaction.response.send_message("❌ Permission denied (Level 1 required).", ephemeral=True)
        await self._do_close(interaction)

    @tickets_cmd.command(name="close")
    async def close_prefix(self, ctx):
        if not is_moderator(ctx.author, min_level=1):
            return await ctx.reply("❌ Permission denied (Level 1 required).")
        await self._do_close(ctx)

    async def _do_close(self, target):
        channel = target.channel if hasattr(target, "channel") else target.channel # Interaction has channel
        if not channel.name.startswith("ticket-"):
            return await self._send_or_reply(target, "❌ This is not a ticket channel.", ephemeral=True)
            
        guild = target.guild
        data = load_server_data(guild.id, "tickets.json") or {}
        resolved_id = data.get("resolved_id")
        resolved_category = guild.get_channel(resolved_id) if resolved_id else None
        
        await self._send_or_reply(target, "🔒 Closing ticket...")
        
        kwargs = {}
        if resolved_category:
            kwargs["category"] = resolved_category
            
        new_overwrites = dict(channel.overwrites)
        for t, ov in list(new_overwrites.items()):
            if isinstance(t, discord.Member) and not t.bot:
                del new_overwrites[t]
                
        kwargs["overwrites"] = new_overwrites
        try:
            await channel.edit(**kwargs)
            await channel.send("✅ Ticket closed and moved to Resolved category.")
        except Exception as e:
            await channel.send(f"⚠️ Failed to modify channel: {e}")

    # ─── Reopen ────────────────────────────────────────────────────────────────
    @tickets_group.command(name="reopen", description="Reopen a closed ticket")
    async def reopen_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user, min_level=1):
            return await interaction.response.send_message("❌ Permission denied (Level 1 required).", ephemeral=True)
        await self._do_reopen(interaction)

    @tickets_cmd.command(name="reopen")
    async def reopen_prefix(self, ctx):
        if not is_moderator(ctx.author, min_level=1):
            return await ctx.reply("❌ Permission denied (Level 1 required).")
        await self._do_reopen(ctx)

    async def _do_reopen(self, target):
        channel = target.channel
        if not channel.name.startswith("ticket-"):
            return await self._send_or_reply(target, "❌ This is not a ticket channel.", ephemeral=True)
            
        user_id = None
        if channel.topic and "User ID: " in channel.topic:
            try: user_id = int(channel.topic.split("User ID: ")[1].strip())
            except: pass
        
        if not user_id:
            return await self._send_or_reply(target, "❌ Could not determine owner from topic. Ticket must be new.", ephemeral=True)
            
        guild = target.guild
        member = guild.get_member(user_id)
        if not member:
            try: member = await guild.fetch_member(user_id)
            except: member = None
        if not member:
            return await self._send_or_reply(target, "❌ Owner no longer in server.", ephemeral=True)

        data = load_server_data(guild.id, "tickets.json") or {}
        cat_id = data.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        
        await self._send_or_reply(target, "🔓 Reopening ticket...")
        
        overwrites = dict(channel.overwrites)
        overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
        
        kwargs = {"overwrites": overwrites}
        if category: kwargs["category"] = category
            
        try:
            await channel.edit(**kwargs)
            author = target.user if isinstance(target, discord.Interaction) else target.author
            await channel.send(f"✅ {member.mention} your ticket has been reopened by {author.mention}")
        except Exception as e:
            await self._send_or_reply(target, f"❌ Failed to reopen: {e}", ephemeral=True)

    # ─── Create ───────────────────────────────────────────────────────────────
    @tickets_group.command(name="create", description="Create a ticket for another user")
    async def create_slash_cmd(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user, min_level=1):
            return await interaction.response.send_message("❌ Permission denied (Level 1 required).", ephemeral=True)
        await self._do_create(interaction, member)

    @tickets_cmd.command(name="create")
    async def create_prefix_cmd(self, ctx, member: discord.Member):
        if not is_moderator(ctx.author, min_level=1):
            return await ctx.reply("❌ Permission denied (Level 1 required).")
        await self._do_create(ctx, member)

    async def _do_create(self, target, member):
        guild = target.guild
        data = load_server_data(guild.id, "tickets.json") or {}
        cat_id = data.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        
        if not category:
            return await self._send_or_reply(target, "❌ Ticket system not setup properly.", ephemeral=True)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, read_message_history=True)
        }
        mod_roles = get_moderator_roles(guild.id)
        ping_roles = []
        for r_id in mod_roles:
            r = guild.get_role(r_id)
            if r:
                overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
                ping_roles.append(r)
                
        ticket_ch = await guild.create_text_channel(
            name=f"ticket-{member.name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket Owner: {member.name} | User ID: {member.id}"
        )
        mentions = " ".join([r.mention for r in ping_roles]) if ping_roles else ""
        await ticket_ch.send(f"Ticket created for {member.mention}! {mentions}")
        await self._send_or_reply(target, f"✅ Ticket created: {ticket_ch.mention}")

    # ─── Remove ───────────────────────────────────────────────────────────────
    @tickets_group.command(name="remove-system", description="Remove the ticket system")
    async def remove_system_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user, min_level=3):
            return await interaction.response.send_message("❌ Administrator permission (Level 3) required.", ephemeral=True)
        await self._do_remove_prompt(interaction)

    @tickets_cmd.command(name="remove-system")
    async def remove_system_prefix(self, ctx):
        if not is_moderator(ctx.author, min_level=3):
            return await ctx.reply("❌ Administrator permission (Level 3) required.")
        await self._do_remove_prompt(ctx)

    async def _do_remove_prompt(self, target):
        guild = target.guild
        data = load_server_data(guild.id, "tickets.json") or {}
        if not data:
            return await self._send_or_reply(target, "❌ No ticket system found.", ephemeral=True)
            
        view = discord.ui.View()
        async def confirm(intx):
            if not is_moderator(intx.user, min_level=3): return await intx.response.send_message("❌ Denied.", ephemeral=True)
            await intx.response.defer()
            for k in ["category_id", "resolved_id", "channel_id"]:
                c = guild.get_channel(data.get(k))
                if c:
                    if isinstance(c, discord.CategoryChannel):
                        for ch in c.channels:
                            try: await ch.delete()
                            except: pass
                    try: await c.delete()
                    except: pass
            save_server_data(guild.id, "tickets.json", {})
            await intx.edit_original_response(content="✅ System removed.", view=None)

        async def cancel(intx): await intx.response.edit_message(content="❌ Cancelled.", view=None)
            
        btn_y = discord.ui.Button(label="Yes, Remove All", style=discord.ButtonStyle.danger)
        btn_n = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        btn_y.callback = confirm
        btn_n.callback = cancel
        view.add_item(btn_y).add_item(btn_n)
        
        await self._send_or_reply(target, "🚨 Remove ticket system? (Deletes ALL tickets)", view=view)

async def setup(bot):
    await bot.add_cog(Tickets(bot))
