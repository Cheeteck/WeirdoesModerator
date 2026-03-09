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
            overwrites=overwrites
        )
        
        mentions = " ".join([r.mention for r in ping_roles]) if ping_roles else ""
        await ticket_channel.send(f"Welcome {interaction.user.mention}! {mentions}\nPlease describe your issue.")
        
        try:
            await interaction.followup.send(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)
        except:
            pass

@Module.enabled()
@Module.help(
    commands={
        "tickets setup": "Set up the ticket system",
        "tickets close": "Close the current ticket",
        "tickets create <user>": "Create a ticket for another user",
        "tickets remove-system": "Removes the ticket system"
    },
    description="Customizable ticket management module."
)
class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(TicketButton())

    @commands.group(name="tickets", invoke_without_command=True)
    async def tickets_cmd(self, ctx):
        await ctx.reply("Usage: `!tickets setup|close|create|remove-system`")

    @tickets_cmd.command(name="setup")
    async def t_setup(self, ctx):
        if not is_moderator(ctx.author):
            return await ctx.reply("❌ Permission denied.")
            
        cat_tickets = await ctx.guild.create_category("Tickets")
        cat_resolved = await ctx.guild.create_category("Resolved tickets")
        
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, read_message_history=True)
        }
        
        # Position at top
        ticket_channel = await ctx.guild.create_text_channel("tickets", category=None, overwrites=overwrites, position=0)
        
        embed = discord.Embed(title="🎫 Support Tickets", description="Click the button below to open a ticket.", color=0x5865F2)
        await ticket_channel.send(embed=embed, view=TicketButton())
        
        data = {
            "category_id": cat_tickets.id,
            "resolved_id": cat_resolved.id,
            "channel_id": ticket_channel.id
        }
        save_server_data(ctx.guild.id, "tickets.json", data)
        await ctx.reply(f"✅ Ticket system established in {ticket_channel.mention}.")

    @tickets_cmd.command(name="close")
    async def t_close(self, ctx):
        if not is_moderator(ctx.author):
            return await ctx.reply("❌ Permission denied.")
            
        if not ctx.channel.name.startswith("ticket-"):
            return await ctx.reply("❌ This is not a ticket channel.")
            
        data = load_server_data(ctx.guild.id, "tickets.json") or {}
        resolved_id = data.get("resolved_id")
        resolved_category = ctx.guild.get_channel(resolved_id) if resolved_id else None
        
        await ctx.send("🔒 Closing ticket...")
        
        kwargs = {}
        if resolved_category:
            kwargs["category"] = resolved_category
            
        new_overwrites = dict(ctx.channel.overwrites)
        for target, overwrite in list(new_overwrites.items()):
            if isinstance(target, discord.Member) and not target.bot:
                del new_overwrites[target]
                
        kwargs["overwrites"] = new_overwrites
        try:
            await ctx.channel.edit(**kwargs)
        except Exception as e:
            return await ctx.send(f"⚠️ Failed to modify channel: {e}")
        await ctx.send("✅ Ticket closed and moved to Resolved category.")

    @tickets_cmd.command(name="create")
    async def t_create(self, ctx, member: discord.Member):
        if not is_moderator(ctx.author):
            return await ctx.reply("❌ Permission denied.")
            
        data = load_server_data(ctx.guild.id, "tickets.json") or {}
        cat_id = data.get("category_id")
        category = ctx.guild.get_channel(cat_id) if cat_id else None
        
        if not category:
            return await ctx.reply("❌ Ticket system not setup properly.")
        
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True),
            ctx.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, embed_links=True, read_message_history=True)
        }
        mod_roles = get_moderator_roles(ctx.guild.id)
        ping_roles = []
        for role_id in mod_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
                ping_roles.append(role)
                
        ticket_channel = await ctx.guild.create_text_channel(
            name=f"ticket-{member.name}",
            category=category,
            overwrites=overwrites
        )
        mentions = " ".join([r.mention for r in ping_roles]) if ping_roles else ""
        await ticket_channel.send(f"Ticket created for {member.mention}! {mentions}")
        await ctx.reply(f"✅ Ticket created manually: {ticket_channel.mention}")

    @tickets_cmd.command(name="remove-system")
    async def t_remove_system(self, ctx):
        if not is_moderator(ctx.author):
            return await ctx.reply("❌ Permission denied.")
            
        data = load_server_data(ctx.guild.id, "tickets.json") or {}
        if not data:
            return await ctx.reply("❌ No ticket system found.")
            
        view = discord.ui.View()
        
        async def confirm(intx):
            if not is_moderator(intx.user):
                return await intx.response.send_message("❌ Permission denied.", ephemeral=True)
            await intx.response.defer()
            cat_tickets = ctx.guild.get_channel(data.get("category_id"))
            cat_resolved = ctx.guild.get_channel(data.get("resolved_id"))
            main_channel = ctx.guild.get_channel(data.get("channel_id"))
            
            for cat in (cat_tickets, cat_resolved):
                if cat:
                    for ch in cat.channels:
                        try: await ch.delete()
                        except: pass
                    try: await cat.delete()
                    except: pass
            if main_channel:
                try: await main_channel.delete()
                except: pass
                
            save_server_data(ctx.guild.id, "tickets.json", {})
            try: await intx.edit_original_response(content="✅ Ticket system completely removed.", view=None)
            except: pass
            
        async def cancel(intx):
            await intx.response.edit_message(content="❌ Cancelled.", view=None)
            
        btn_y = discord.ui.Button(label="Yes, Remove All", style=discord.ButtonStyle.danger)
        btn_n = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        btn_y.callback = confirm
        btn_n.callback = cancel
        view.add_item(btn_y).add_item(btn_n)
        
        await ctx.reply("🚨 Are you sure you want to remove the ticket system? This deletes categories, channels, and **ALL tickets**.", view=view)

async def setup(bot):
    await bot.add_cog(Tickets(bot))
