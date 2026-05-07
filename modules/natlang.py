import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from module_utils import Module, load_server_data, is_module_enabled
from groq import Groq
from modules.core import is_moderator

groq_client = Groq(api_key=os.getenv("GROQ")) if os.getenv("GROQ") else None


@Module.dependency.soft("WarnsExtras")
@Module.dependency.soft("Lockdown")
@Module.version("1.7")
@Module.enabled()
@Module.help(
    commands={
        "WM <query>": "Natural language command execution with confirmation"
    },
    description="NatLang AI router module with confirmation dialogs."
)
class NatLang(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _send_or_reply(
        self,
        target,
        content,
        ephemeral=False,
        embed=None,
        view=None
    ):
        kwargs = {
            "content": content
        }

        if embed is not None:
            kwargs["embed"] = embed

        if view is not None:
            kwargs["view"] = view

        if isinstance(target, discord.Interaction):
            kwargs["ephemeral"] = ephemeral

            if target.response.is_done():
                await target.followup.send(**kwargs)
            else:
                await target.response.send_message(**kwargs)

        else:
            await target.reply(**kwargs)

    @app_commands.command(
        name="wm",
        description="Execute moderation actions using natural language"
    )
    async def wm_slash(self, interaction: discord.Interaction, query: str):
        if not is_moderator(interaction.user):
            return await interaction.response.send_message(
                "❌ Denied.",
                ephemeral=True
            )

        await self._do_natlang(interaction, query)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        config = load_server_data(message.guild.id, "config.json") or {}
        wakeword = config.get("natlang_wakeword", "WM").lower()

        if message.content.strip().lower().split(" ")[0] == wakeword:
            if not is_module_enabled(message.guild.id, "NatLang"):
                return

            if not is_moderator(message.author):
                return await message.reply("❌ Denied.")

            query = message.content.strip()[len(wakeword):].strip()

            if not query:
                return await message.reply("Yes?")

            await self._do_natlang(message, query)

    async def _do_natlang(self, target, query):
        if not groq_client:
            return await self._send_or_reply(
                target,
                "❌ Groq API key not configured.",
                ephemeral=True
            )

        guild = target.guild
        author = target.user if isinstance(target, discord.Interaction) else target.author

        try:
            prompt = """
You are an AI moderator assistant.

Respond ONLY with a JSON object.

RESPONSE FORMATS:

1. ACTION
{
  "action": "action_name",
  "args": {"arg1": "val1"},
  "confirm": false
}

2. NEEDS_CONFIRMATION
{
  "confirm": true,
  "message": "Are you sure you want to [action]?",
  "action": "action_name",
  "args": {"arg1": "val1"},
  "buttons": {
    "confirm": "Confirm",
    "cancel": "Cancel"
  }
}

3. CLARIFICATION
{
  "clarify": true,
  "message": "I'm not sure what you mean.",
  "buttons": {
    "option1": "Option 1",
    "option2": "Option 2"
  }
}

IMPORTANT:
- The bot enforces confirmation rules server-side.
- Still mark dangerous actions with "confirm": true.
- If ANY ambiguity exists, use clarification.

Available Actions:
warn(user_id, reason)
mute(user_id, duration, reason)
unmute(user_id)
kick(user_id, reason)
ban(user_id, reason)
unban(user_id)
allwarns
clearwarns(user_id)
resetwarns
lock(channel_id)
unlock(channel_id)
lockdown

If you cannot identify a user, use their username or mention as user_id.
"""

            context_parts = []

            mentions = (
                target.message.mentions
                if hasattr(target, "message")
                else []
            )

            if mentions:
                context_parts.append(
                    "Mentions:\n" +
                    "\n".join([
                        f"Name: {m.name}, ID: {m.id}, Mention: {m.mention}"
                        for m in mentions
                    ])
                )

            if (
                isinstance(target, discord.Message)
                and target.reference
                and target.reference.resolved
            ):
                ref = target.reference.resolved

                if isinstance(ref, discord.Message):
                    context_parts.append(
                        f"Replied-to Message Author: {ref.author.name}, ID: {ref.author.id}"
                    )

                    context_parts.append(
                        f"Replied-to Message Content: {ref.content}"
                    )

            context_parts.append(
                f"Command Author: {author.name}, ID: {author.id}"
            )

            ctx = "\n".join(context_parts) + f"\nQuery: {query}"

            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": ctx
                    }
                ],
                response_format={"type": "json_object"}
            )

            res = json.loads(completion.choices[0].message.content)

            if res.get("clarify"):
                buttons_dict = res.get(
                    "buttons",
                    {
                        "option1": "Option 1",
                        "option2": "Option 2"
                    }
                )

                view = ClarificationView(
                    buttons_dict,
                    self,
                    target,
                    author,
                    guild
                )

                await self._send_or_reply(
                    target,
                    res.get("message", "Please clarify:"),
                    view=view
                )

                return

            action = res.get("action")
            args = res.get("args", {})
            needs_confirm = res.get("confirm", False)

            async def resolve_user(u_input):
                if not u_input:
                    return None

                u_str = (
                    str(u_input)
                    .strip()
                    .replace("<@", "")
                    .replace(">", "")
                    .replace("!", "")
                )

                try:
                    uid = int(u_str)
                    return guild.get_member(uid) or await self.bot.fetch_user(uid)

                except ValueError:
                    u_lower = str(u_input).lower()

                    for m in guild.members:
                        if (
                            m.name.lower() == u_lower
                            or (
                                m.nick
                                and m.nick.lower() == u_lower
                            )
                        ):
                            return m

                    return None

            core_cog = self.bot.get_cog("Core")
            we_cog = self.bot.get_cog("WarnsExtras")
            ld_cog = self.bot.get_cog("Lockdown")

            lvl_map = {
                "warn": 1,
                "mute": 1,
                "unmute": 1,
                "hwarn": 1,
                "allwarns": 1,
                "lock": 2,
                "unlock": 2,
                "kick": 2,
                "ban": 2,
                "unban": 2,
                "clearwarns": 2,
                "resetwarns": 3,
                "lockdown": 2
            }

            req_lvl = lvl_map.get(action, 1)

            if not is_moderator(author, min_level=req_lvl):
                return await self._send_or_reply(
                    target,
                    f"Permission Level {req_lvl} required for '{action}'.",
                    ephemeral=True
                )

            if action == "allwarns" and we_cog:
                return await we_cog._do_allwarns(target)

            if action == "resetwarns" and we_cog:
                return await we_cog._do_resetwarns(target)

            if action == "lockdown" and ld_cog:
                count = 0

                for ch in guild.channels:
                    if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                        try:
                            await ld_cog._lock_channel(ch)
                            count += 1
                        except:
                            pass

                return await self._send_or_reply(
                    target,
                    f"Locked {count} channels."
                )

            if action in ["lock", "unlock"]:
                c_id = args.get("channel_id")
                ch = None

                if c_id:
                    try:
                        ch = guild.get_channel(int(str(c_id).strip()))
                    except:
                        pass

                if not ch:
                    ch = target.channel

                if action == "lock" and ld_cog:
                    await ld_cog._lock_channel(ch)

                    return await self._send_or_reply(
                        target,
                        f"Locked {ch.mention}"
                    )

                elif action == "unlock" and ld_cog:
                    await ld_cog._unlock_channel(ch)

                    return await self._send_or_reply(
                        target,
                        f"Unlocked {ch.mention}"
                    )

            t_user = await resolve_user(args.get("user_id"))

            if not t_user:
                return await self._send_or_reply(
                    target,
                    f"❌ Could not identify user: `{args.get('user_id')}`",
                    ephemeral=True
                )

            always_confirm = {
                "ban"
            }

            self_confirm = {
                "mute",
                "warn"
            }

            if action in always_confirm:
                needs_confirm = True

            if t_user.id == author.id and action in self_confirm:
                needs_confirm = True

            if needs_confirm:
                buttons_dict = res.get(
                    "buttons",
                    {
                        "confirm": "Confirm",
                        "cancel": "Cancel"
                    }
                )

                view = ConfirmationView(
                    buttons_dict,
                    self,
                    target,
                    author,
                    guild,
                    action,
                    args,
                    t_user,
                    core_cog,
                    we_cog,
                    ld_cog
                )

                confirm_message = res.get(
                    "message",
                    f"Are you sure you want to {action} {t_user.mention}?"
                )

                await self._send_or_reply(
                    target,
                    confirm_message,
                    view=view
                )

                return

            if action == "unban":
                await guild.unban(t_user)

                return await self._send_or_reply(
                    target,
                    f"Unbanned {t_user.name}"
                )

            t_member = (
                t_user
                if isinstance(t_user, discord.Member)
                else guild.get_member(t_user.id)
            )

            if not t_member and action not in ["hwarn"]:
                return await self._send_or_reply(
                    target,
                    f"❌ User {t_user.name} is not in this server.",
                    ephemeral=True
                )

            if t_member and t_member == guild.owner:
                return await self._send_or_reply(
                    target,
                    "Cannot moderate owner.",
                    ephemeral=True
                )

            await self._execute_action(
                target,
                action,
                args,
                t_member,
                t_user,
                core_cog,
                we_cog,
                ld_cog,
                guild
            )

        except Exception as e:
            await self._send_or_reply(
                target,
                f"AI Error: {e}",
                ephemeral=True
            )

    async def _execute_action(
        self,
        target,
        action,
        args,
        t_member,
        t_user,
        core_cog,
        we_cog,
        ld_cog,
        guild
    ):
        try:
            if action == "warn" and core_cog:
                await core_cog.execute_warn(
                    target,
                    t_member,
                    args.get("reason", "AI-decision")
                )

            elif action == "mute" and core_cog:
                await core_cog.execute_mute(
                    target,
                    t_member,
                    args.get("duration", "10m"),
                    args.get("reason", "AI-decision")
                )

            elif action == "unmute" and core_cog:
                await t_member.timeout(None)

                await self._send_or_reply(
                    target,
                    f"Unmuted {t_member.mention}"
                )

            elif action == "kick":
                await t_member.kick(reason=args.get("reason"))

                await self._send_or_reply(
                    target,
                    f"Kicked {t_member.name}"
                )

            elif action == "ban":
                await t_member.ban(reason=args.get("reason"))

                await self._send_or_reply(
                    target,
                    f"Banned {t_member.name}"
                )

            elif action == "hwarn" and core_cog:
                await core_cog.execute_hwarn(
                    target,
                    t_member or t_user
                )

            elif action == "clearwarns" and we_cog:
                await we_cog._do_clearwarns(
                    target,
                    t_member or t_user
                )

            else:
                await self._send_or_reply(
                    target,
                    f"Unknown action: {action}",
                    ephemeral=True
                )

        except Exception as e:
            await self._send_or_reply(
                target,
                f"Error executing action: {e}",
                ephemeral=True
            )


class ClarificationView(discord.ui.View):
    def __init__(
        self,
        buttons_dict,
        cog,
        target,
        author,
        guild,
        timeout=30
    ):
        super().__init__(timeout=timeout)

        self.cog = cog
        self.target = target
        self.author = author
        self.guild = guild
        self.buttons_dict = buttons_dict

        for key, label in list(buttons_dict.items())[:2]:
            btn = discord.ui.Button(
                label=label,
                custom_id=key
            )

            btn.callback = self.make_button_callback(key)

            self.add_item(btn)

    def make_button_callback(self, key):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                await interaction.response.send_message(
                    "You're not authorized to use this.",
                    ephemeral=True
                )
                return

            choice = self.buttons_dict.get(key, key)

            await interaction.response.defer()

            await self.cog._do_natlang(
                interaction,
                choice
            )

        return callback


class ConfirmationView(discord.ui.View):
    def __init__(
        self,
        buttons_dict,
        cog,
        target,
        author,
        guild,
        action,
        args,
        t_user,
        core_cog,
        we_cog,
        ld_cog,
        timeout=30
    ):
        super().__init__(timeout=timeout)

        self.cog = cog
        self.target = target
        self.author = author
        self.guild = guild
        self.buttons_dict = buttons_dict
        self.action = action
        self.args = args
        self.t_user = t_user
        self.core_cog = core_cog
        self.we_cog = we_cog
        self.ld_cog = ld_cog

        for key, label in list(buttons_dict.items())[:2]:

            is_confirm_style = (
                key.lower() in ["confirm", "yes", "approve", "ok"]
                or "confirm" in label.lower()
            )

            btn = discord.ui.Button(
                label=label,
                custom_id=key,
                style=(
                    discord.ButtonStyle.success
                    if is_confirm_style
                    else discord.ButtonStyle.danger
                )
            )

            btn.callback = self.make_button_callback(key)

            self.add_item(btn)

    def make_button_callback(self, key):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author.id:
                await interaction.response.send_message(
                    "You're not authorized to use this.",
                    ephemeral=True
                )
                return

            await interaction.response.defer()

            label = self.buttons_dict.get(key, "").lower()

            is_confirm = (
                key.lower() in ["confirm", "yes", "approve", "ok"]
                or "confirm" in label
            )

            if is_confirm:
                t_member = (
                    self.t_user
                    if isinstance(self.t_user, discord.Member)
                    else self.guild.get_member(self.t_user.id)
                )

                await self.cog._execute_action(
                    interaction,
                    self.action,
                    self.args,
                    t_member,
                    self.t_user,
                    self.core_cog,
                    self.we_cog,
                    self.ld_cog,
                    self.guild
                )

            else:
                await self.cog._send_or_reply(
                    interaction,
                    "❌ Action cancelled."
                )

        return callback


async def setup(bot):
    await bot.add_cog(NatLang(bot))