import asyncio
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands

from module_utils import Module, load_server_data, save_server_data, is_module_enabled
from modules.core import is_moderator, send_response, get_author, add_warning

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
HANDSHAKE_PORT = 7913   # Temporary port for setup handshake
API_PORT       = 7912   # Permanent port for ongoing WMMC ↔ WMD communication
HANDSHAKE_TIMEOUT = 600  # 10 minutes


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def load_mc_rules(guild_id: int) -> dict:
    """Load mcrules.json for a guild (source-of-truth pushed from WMMC at startup)."""
    return load_server_data(guild_id, "mcrules.json") or {}

def save_mc_rules(guild_id: int, rules: dict):
    save_server_data(guild_id, "mcrules.json", rules)

def load_mc_links(guild_id: int) -> dict:
    """Load account links {discord_id: minecraft_name}."""
    return load_server_data(guild_id, "mclinks.json") or {}

def save_mc_links(guild_id: int, links: dict):
    save_server_data(guild_id, "mclinks.json", links)

def load_mc_infractions(guild_id: int) -> list:
    """Load MC punishment history."""
    return load_server_data(guild_id, "mc_infractions.json") or []

def save_mc_infractions(guild_id: int, records: list):
    save_server_data(guild_id, "mc_infractions.json", records)

def add_mc_infraction(guild_id: int, player_name: str, mod_discord_id: int,
                      rule_id: str, degree: int | None, punishment: str, reason: str):
    records = load_mc_infractions(guild_id)
    records.append({
        "id": str(uuid.uuid4()),
        "playerName": player_name,
        "moderatorDiscordId": str(mod_discord_id),
        "ruleId": rule_id,
        "degree": degree,
        "punishmentType": punishment,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).timestamp()
    })
    save_mc_infractions(guild_id, records)



def get_punishment_for_degree(rule: dict, degree: int) -> str | None:
    """Return the punishment string for a given 1-indexed degree, or None."""
    punishments = rule.get("punishments", [])
    if not punishments:
        return None
    idx = degree - 1
    if idx < 0 or idx >= len(punishments):
        return None
    return punishments[idx]

def parse_punishment(punishment_str: str) -> dict:
    """
    Parse a punishment string into an action dict.
    Examples:
        "warn"           → {"action": "warn"}
        "perm_ban"       → {"action": "perm_ban"}
        "temp_ban_7d"    → {"action": "temp_ban", "duration": "7d"}
        "mute_30m"       → {"action": "mute",    "duration": "30m"}
    """
    if punishment_str == "warn":
        return {"action": "warn"}
    if punishment_str == "perm_ban":
        return {"action": "perm_ban"}
    if punishment_str.startswith("temp_ban_"):
        return {"action": "temp_ban", "duration": punishment_str[len("temp_ban_"):]}
    if punishment_str.startswith("mute_"):
        return {"action": "mute", "duration": punishment_str[len("mute_"):]}
    return {"action": punishment_str}


# ──────────────────────────────────────────────────────────────────────────────
# Temporary handshake server (port 7913)
# ──────────────────────────────────────────────────────────────────────────────

class HandshakeHandler(BaseHTTPRequestHandler):
    """Serves GET /handshake once, returning the guild ID, then signals shutdown."""

    def log_message(self, format, *args):  # suppress default access log noise
        pass

    def do_GET(self):
        if self.path == "/handshake":
            guild_id = self.server.guild_id
            payload = json.dumps({"discord_server_id": str(guild_id)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            # Signal that the handshake was consumed
            self.server.handshake_done.set()
        else:
            self.send_response(404)
            self.end_headers()

def _run_handshake_server(guild_id: int, done_event: threading.Event):
    """Runs the temporary handshake HTTP server in a background thread."""
    server = HTTPServer(("localhost", HANDSHAKE_PORT), HandshakeHandler)
    server.guild_id = guild_id
    server.handshake_done = done_event
    server.timeout = 1  # poll every second to check for timeout / done_event

    deadline = asyncio.get_event_loop if False else None  # just a placeholder
    import time
    end_time = time.monotonic() + HANDSHAKE_TIMEOUT

    while not done_event.is_set() and time.monotonic() < end_time:
        server.handle_request()

    server.server_close()


# ──────────────────────────────────────────────────────────────────────────────
# Permanent API server (port 7912)
# ──────────────────────────────────────────────────────────────────────────────

_api_server: HTTPServer | None = None
_api_bot_ref = None   # set on cog init so handlers can call back into the bot


class PermanentAPIHandler(BaseHTTPRequestHandler):
    """
    Permanent REST API that WMMC talks to after setup.

    Endpoints (Aligned with WMMC Implementation)
    ─────────
    POST /identify                 → body: {"discord_server_id": "...", "wmmc_version": "..."}
    POST /rules/sync               → body: {"discord_server_id": "...", "rules": "{...}"} (rules is a JSON string)
    POST /punishment/log           → body: {"discord_server_id": "...", "player_uuid": "...", "player_name": "...",
                                           "rule_id": "...", "degree": ..., "punishment_type": "...",
                                           "reason": "...", "timestamp": ...}
    GET  /history?server_id=...&player_uuid=...
                                   → returns combined history
    GET  /ping                     → health check
    """

    def log_message(self, format, *args):
        pass

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _send_json(self, code: int, data: dict):
        payload = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ── GET ──────────────────────────────────────────────────────────────────

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/ping":
            self._send_json(200, {"status": "ok"})

        elif parsed.path == "/history":
            server_id_list = qs.get("server_id") or qs.get("guild_id")
            uuid_list = qs.get("player_uuid")

            if not server_id_list or not uuid_list:
                return self._send_json(400, {"error": "server_id and player_uuid required"})

            guild_id = int(server_id_list[0])
            player_uuid = uuid_list[0]

            # 1. Load MC infractions for this UUID
            mc_infractions = load_mc_infractions(guild_id)
            player_mc = [r for r in mc_infractions if r["playerUuid"] == player_uuid]

            # 2. Try to find linked Discord account for this UUID to pull Discord history
            links = load_mc_links(guild_id)
            player_name = player_mc[0]["playerName"] if player_mc else "Unknown"

            discord_infractions = []
            linked_discord_id = None
            for d_id, mc_name in links.items():
                if mc_name.lower() == player_name.lower():
                    linked_discord_id = d_id
                    break

            if linked_discord_id:
                from modules.core import load_server_data
                from datetime import timedelta
                d_warns = load_server_data(guild_id, "warnings.json") or []
                for w in d_warns:
                    if w["userId"] == str(linked_discord_id):
                        ts = datetime.fromisoformat(w["timestamp"]).timestamp()
                        discord_infractions.append({
                            "type": "Warning (Discord)",
                            "origin": "Discord",
                            "reason": w["reason"],
                            "timestamp": ts,
                            "date_label": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                        })
                d_mutes = load_server_data(guild_id, "mutes.json") or []
                for m in d_mutes:
                    if m["userId"] == str(linked_discord_id):
                        ts = datetime.fromisoformat(m["timestamp"]).timestamp()
                        discord_infractions.append({
                            "type": f"Mute ({m['durationSec']//60}m) (Discord)",
                            "origin": "Discord",
                            "reason": m["reason"],
                            "timestamp": ts,
                            "date_label": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                        })

            # 3. Format MC infractions
            formatted_mc = []
            for r in player_mc:
                ts = r.get("timestamp") or 0
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        ts = 0

                ptype = r.get("punishmentType", r.get("punishment", "Unknown"))
                formatted_mc.append({
                    "type": ptype.replace("_", " ").title(),
                    "origin": "Minecraft",
                    "reason": r.get("reason", ""),
                    "timestamp": ts,
                    "date_label": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "N/A"
                })

            # 4. Combine and Sort
            combined = sorted(discord_infractions + formatted_mc, key=lambda x: x["ts"] if "ts" in x else x["timestamp"], reverse=True)
            self._send_json(200, {"infractions": combined})

        elif parsed.path == "/sync/mutes":
            server_id_list = qs.get("server_id") or qs.get("guild_id")
            if not server_id_list:
                return self._send_json(400, {"error": "server_id required"})

            guild_id = int(server_id_list[0])
            links = load_mc_links(guild_id) # {discord_id: mc_name}
            if not links:
                return self._send_json(200, {"mutes": []})

            from modules.core import load_server_data
            from datetime import timedelta
            d_mutes = load_server_data(guild_id, "mutes.json") or []
            active_mutes = []
            now = datetime.now(timezone.utc)

            for m in d_mutes:
                d_id = str(m["userId"])
                if d_id in links:
                    ts = datetime.fromisoformat(m["timestamp"])
                    expiry = ts + timedelta(seconds=m.get("durationSec", 0))
                    if expiry > now:
                        active_mutes.append({
                            "playerName": links[d_id],
                            "expiry": int(expiry.timestamp())
                        })
            self._send_json(200, {"mutes": active_mutes})

        else:
            self._send_json(404, {"error": "not found"})

    # ── POST ─────────────────────────────────────────────────────────────────

    def do_POST(self):
        body = self._read_json_body()
        if body is None:
            return self._send_json(400, {"error": "invalid json"})

        # Aligned key: discord_server_id
        target_guild_id = body.get("discord_server_id") or body.get("guild_id")

        if self.path == "/identify":
            if not target_guild_id:
                return self._send_json(400, {"error": "discord_server_id required"})
                
            listen_port = body.get("listen_port")
            if listen_port:
                save_server_data(int(target_guild_id), "mc_port.json", {"port": listen_port})
                
            print(f"[WMMC API] Identified: Guild {target_guild_id} (Version: {body.get('wmmc_version', 'unknown')}, Port: {listen_port})")
            self._send_json(200, {"status": "identified"})

        elif self.path == "/rules/sync":
            if not target_guild_id or "rules" not in body:
                return self._send_json(400, {"error": "discord_server_id and rules required"})

            # WMMC sends rules as a JSON STRING
            rules_raw = body["rules"]
            try:
                rules_dict = json.loads(rules_raw)
                save_mc_rules(int(target_guild_id), rules_dict)
                print(f"[WMMC API] Rules synced for guild {target_guild_id} ({len(rules_dict)} entries)")
                self._send_json(200, {"status": "synced", "count": len(rules_dict)})
            except Exception as e:
                self._send_json(400, {"error": f"Invalid rules JSON: {e}"})

        elif self.path == "/punishment/log":
            required = ["discord_server_id", "player_uuid", "player_name", "rule_id", "punishment_type", "reason"]
            if not all(k in body for k in required):
                return self._send_json(400, {"error": f"Missing fields: {required}"})

            guild_id = int(body["discord_server_id"])
            records = load_mc_infractions(guild_id)
            records.append({
                "id": str(uuid.uuid4()),
                "playerUuid": body["player_uuid"],
                "playerName": body["player_name"],
                "ruleId": body["rule_id"],
                "degree": body.get("degree", 0),
                "punishmentType": body["punishment_type"],
                "reason": body["reason"],
                "timestamp": body.get("timestamp", datetime.now().timestamp())
            })
            save_mc_infractions(guild_id, records)
            print(f"[WMMC API] Punishment logged for '{body['player_name']}' in guild {guild_id}: {body['punishment_type']}")
            self._send_json(200, {"status": "logged"})

        else:
            self._send_json(404, {"error": "not found"})


def _start_permanent_api_server():
    global _api_server
    if _api_server is not None:
        return  # already running
    try:
        _api_server = HTTPServer(("localhost", API_PORT), PermanentAPIHandler)
        _api_server.timeout = 1
        print(f"[WMMC] Permanent API server started on localhost:{API_PORT}")

        def _serve():
            while True:
                _api_server.handle_request()

        t = threading.Thread(target=_serve, name="wmmc-api-server", daemon=True)
        t.start()
    except OSError as e:
        print(f"[WMMC] Could not start API server on port {API_PORT}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Discord Cog
# ──────────────────────────────────────────────────────────────────────────────

@Module.version("1.2")
@Module.enabled()
@Module.help(
    commands={
        "minecraft setup": "Starts the 10-minute handshake window for /wmmc setup in-game",
        "minecraft status": "Shows the Minecraft module status and tethered server info",
        "minecraft rules": "Lists the synced Minecraft rules for this server",
        "minecraft link": "Links a Discord account to a Minecraft username",
        "minecraft unlink": "Removes a Discord↔Minecraft account link",
        "minecraft links": "Lists all Discord↔Minecraft account links",
        "punish": "Issues a standardized or manual Minecraft punishment",
    },
    description="Minecraft integration — rule syncing, cross-platform punishments, and account linking."
)
class Minecraft(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        global _api_bot_ref
        _api_bot_ref = bot
        _start_permanent_api_server()

    # ── /minecraft slash command group ───────────────────────────────────────

    minecraft_group = app_commands.Group(name="minecraft", description="Minecraft module commands")

    # ── /minecraft setup ─────────────────────────────────────────────────────
    @minecraft_group.command(name="setup", description="Start the 10-minute handshake window so WMMC can tether to this Discord server")
    async def minecraft_setup_slash(self, interaction: discord.Interaction):
        await self._execute_minecraft_setup(interaction)

    async def _execute_minecraft_setup(self, ctx_or_int):
        guild_id = ctx_or_int.guild_id if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild.id
        guild_name = (ctx_or_int.guild.name if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild.name)
        is_int = isinstance(ctx_or_int, discord.Interaction)

        if not is_moderator(ctx_or_int.user if is_int else ctx_or_int.author, min_level=3):
            return await send_response(ctx_or_int, "Administrator permission (Level 3) required.", ephemeral=True)

        embed = discord.Embed(
            title="🔗 Minecraft Setup — Handshake Window Open",
            description=(
                "A temporary connection window is now open on **localhost:7913** for **10 minutes**.\n\n"
                "**Next step:** Go to your Minecraft server and run:\n"
                "```\n/wmmc setup\n```\n"
                "Once the handshake succeeds, this window will close automatically and your Minecraft "
                "server will be tethered to this Discord server.\n\n"
                f"🔒 **Discord server ID:** `{guild_id}`"
            ),
            color=0x00cc66
        )
        embed.set_footer(text="Handshake window closes in 10 minutes if not used.")
        await send_response(ctx_or_int, embed=embed)

        # Run the temporary handshake server in a background thread
        done_event = threading.Event()

        def run_server():
            _run_handshake_server(guild_id, done_event)

        server_thread = threading.Thread(target=run_server, name="wmmc-handshake", daemon=True)
        server_thread.start()

        # Wait for the handshake to complete or timeout, then follow up
        async def await_result():
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: done_event.wait(HANDSHAKE_TIMEOUT))
            if result:
                success_embed = discord.Embed(
                    title="Handshake Complete!",
                    description=(
                        f"Your Minecraft server has successfully tethered to **{guild_name}**.\n"
                        f"Discord server ID `{guild_id}` is now saved on the Minecraft side.\n\n"
                        f"The Minecraft server will communicate with this bot at **localhost:{API_PORT}** from now on."
                    ),
                    color=0x00ff88
                )
                if is_int:
                    await ctx_or_int.followup.send(embed=success_embed)
                else:
                    await ctx_or_int.channel.send(embed=success_embed)
            else:
                timeout_embed = discord.Embed(
                    title="Handshake Timed Out",
                    description=(
                        "The 10-minute window expired without a Minecraft server connecting.\n"
                        "Run `/minecraft setup` again when you're ready to try."
                    ),
                    color=0xff4444
                )
                if is_int:
                    await ctx_or_int.followup.send(embed=timeout_embed)
                else:
                    await ctx_or_int.channel.send(embed=timeout_embed)

        asyncio.create_task(await_result())

    @minecraft_group.command(name="status", description="Show the Minecraft module status")
    async def minecraft_status_slash(self, interaction: discord.Interaction):
        await self._execute_minecraft_status(interaction)

    @minecraft_group.command(name="rules", description="List the synced Minecraft rules for this server")
    async def minecraft_rules_slash(self, interaction: discord.Interaction):
        await self._execute_minecraft_rules(interaction)

    @minecraft_group.command(name="link", description="Link a Discord account to a Minecraft username")
    async def minecraft_link_slash(self, interaction: discord.Interaction,
                             member: discord.Member, minecraft_name: str):
        if not is_moderator(interaction.user):
            return await interaction.response.send_message("Moderators only.", ephemeral=True)

        links = load_mc_links(interaction.guild_id)
        links[str(member.id)] = minecraft_name
        save_mc_links(interaction.guild_id, links)
        await interaction.response.send_message(f"Linked **{member.name}** ↔ `{minecraft_name}`.")

    @minecraft_group.command(name="unlink", description="Remove a Discord↔Minecraft account link")
    async def minecraft_unlink_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not is_moderator(interaction.user):
            return await interaction.response.send_message("Moderators only.", ephemeral=True)

        links = load_mc_links(interaction.guild_id)
        if str(member.id) not in links:
            return await interaction.response.send_message(f"{member.name} is not linked.", ephemeral=True)

        mc_name = links.pop(str(member.id))
        save_mc_links(interaction.guild_id, links)
        await interaction.response.send_message(f"Unlinked **{member.name}** (was `{mc_name}`).")

    @minecraft_group.command(name="links", description="List all Discord↔Minecraft account links")
    async def minecraft_links_slash(self, interaction: discord.Interaction):
        if not is_moderator(interaction.user):
            return await interaction.response.send_message("Moderators only.", ephemeral=True)

        links = load_mc_links(interaction.guild_id)
        if not links:
            return await interaction.response.send_message("No accounts linked yet.", ephemeral=True)

        lines = []
        for discord_id, mc_name in links.items():
            lines.append(f"<@{discord_id}> ↔ `{mc_name}`")

        embed = discord.Embed(title=f"🔗 Linked Accounts ({len(links)})", description="\n".join(lines), color=0x5865F2)
        await interaction.response.send_message(embed=embed)

    # ── /punish ───────────────────────────────────────────────────────────────

    @app_commands.command(name="punish", description="Issue a Minecraft punishment to a player")
    @app_commands.describe(
        player="The Minecraft username of the player to punish",
        rule_id="The rule ID (e.g. '1', '1a', '2')",
        degree="The violation degree (1, 2, 3...). Defaults to 1st degree if left blank."
    )
    async def punish_slash(self, interaction: discord.Interaction,
                           player: str, rule_id: str, degree: int | None = None):
        if not is_moderator(interaction.user):
            return await interaction.response.send_message("Moderators only.", ephemeral=True)

        await self._execute_punish(interaction, player, rule_id, degree)

    async def _execute_punish(self, ctx_or_int, player: str, rule_id: str, degree: int | None):
        guild_id = ctx_or_int.guild_id if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild.id
        mod = ctx_or_int.user if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.author
        guild = self.bot.get_guild(guild_id) or (ctx_or_int.guild if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild)

        rules = load_mc_rules(guild_id)
        if rule_id not in rules:
            return await send_response(ctx_or_int,
                f"❌ Rule `{rule_id}` not found. Use `/minecraft rules` to see available rules.", ephemeral=True)

        rule = rules[rule_id]
        rule_name = rule.get("name", f"Rule {rule_id}")
        punishments = rule.get("punishments", [])

        # 1. Resolve 'player' if it's a mention or ID
        links = load_mc_links(guild_id)
        resolved_player = player
        discord_linked_id = None

        id_match = re.search(r"(\d+)", player)
        if id_match:
            uid = id_match.group(1)
            if uid in links:
                resolved_player = links[uid]
                discord_linked_id = int(uid)

        # 2. Manual vs Standardized
        is_manual = len(punishments) == 0
        if is_manual:
            reason = f"Rule {rule_id} ({rule_name}) — manual infraction (via Discord)"
            add_mc_infraction(guild_id, resolved_player, mod.id, rule_id, 0, "manual", reason)
            embed = discord.Embed(
                title="Manual Infraction Logged",
                description=(f"**Player:** `{resolved_player}`\n**Rule:** `{rule_id}` — {rule_name}\n**Issued by:** {mod.mention}"),
                color=0xffaa00
            )
            return await send_response(ctx_or_int, embed=embed)

        if degree is None: degree = 1
        if degree < 1 or degree > len(punishments):
            return await send_response(ctx_or_int, f"Invalid degree `{degree}`. Valid: 1–{len(punishments)}.")

        punishment_str = punishments[degree - 1]
        reason = f"Minecraft Rule {rule_id} ({rule_name}) — {degree}° violation"

        # 3. Apply Discord Action
        if not discord_linked_id: # lookup if player was passed as MC name
            for d_id, mc_name in links.items():
                if mc_name.lower() == resolved_player.lower():
                    discord_linked_id = int(d_id)
                    break

        discord_action = ""
        if discord_linked_id:
            linked_member = guild.get_member(discord_linked_id) or await guild.fetch_member(discord_linked_id)
            if linked_member:
                try:
                    from modules.core import parse_duration, add_warning
                    from datetime import timedelta
                    if punishment_str == "perm_ban":
                        await linked_member.ban(reason=reason)
                        discord_action = f"Ban executed on Discord for {linked_member.mention}."
                    elif punishment_str == "warn":
                        add_warning(guild_id, linked_member.id, mod.id, reason)
                        discord_action = f"Warning logged on Discord for {linked_member.mention}."
                    else:
                        dur_str = punishment_str.split("_")[-1]
                        dur_sec = parse_duration(dur_str)
                        if dur_sec:
                            if dur_sec <= 2419200:
                                await linked_member.timeout(timedelta(seconds=dur_sec), reason=reason)
                                discord_action = f"Muted (Timeout) for {dur_str} on Discord."
                            else:
                                await linked_member.ban(reason=reason)
                                discord_action = f"Banned (exceeds timeout limit) on Discord."
                        elif "ban" in punishment_str:
                            await linked_member.ban(reason=reason)
                            discord_action = f"Ban executed on Discord."
                except Exception as e:
                    if "Missing Permissions" in str(e) or "403" in str(e):
                        discord_action = f"*Linked to {linked_member.mention}, but WMD lacks permissions to punish them (check role hierarchy).* "
                    else:
                        discord_action = f"*Linked to {linked_member.mention}, but Discord punishment failed: {e}*"
        else:
            discord_action = "*Player not linked to Discord. Recorded for Minecraft only.*"

        # 4. Log MC infraction and Queue Sync for WMMC
        add_mc_infraction(guild_id, resolved_player, mod.id, rule_id, degree, punishment_str, reason)
        
        port_data = load_server_data(guild_id, "mc_port.json")
        port = port_data.get("port") if port_data else None
        
        cmd_string = f"punish {resolved_player} {rule_id} {degree}"
        if port:
            async def send_to_wmmc():
                import aiohttp
                try:
                    async with aiohttp.ClientSession() as session:
                        url = f"http://localhost:{port}/command"
                        async with session.post(url, json={"command": cmd_string}, timeout=3) as resp:
                            if resp.status != 200:
                                print(f"[WMMC API] Failed to push command to WMMC on port {port}: HTTP {resp.status}")
                except Exception as e:
                    print(f"[WMMC API] Error pushing command to WMMC on port {port}: {e}")
            self.bot.loop.create_task(send_to_wmmc())
        else:
            print(f"[WMMC API] No port found for guild {guild_id}. Command `{cmd_string}` not sent to Minecraft.")

        embed = discord.Embed(
            title="Minecraft Punishment Record",
            description=(
                f"**Player:** `{resolved_player}`\n"
                f"**Rule:** `{rule_id}` — {rule_name}\n"
                f"**Degree:** {degree}°\n"
                f"**Action:** `{punishment_str}`\n\n"
                f"{discord_action}"
            ),
            color=0xff4444
        )
        embed.set_footer(text=f"Issued by {mod.name} • Syncing to Minecraft...")
        await send_response(ctx_or_int, embed=embed)

    # ── Prefix fallback for /punish ───────────────────────────────────────────

    @commands.command(name="punish")
    async def punish_command(self, ctx, player: str = None, rule_id: str = None, degree: int = None):
        if not is_moderator(ctx.author):
            return await ctx.reply("Moderators only.")
        if not player or not rule_id:
            return await ctx.reply("Usage: `!punish <player> <rule_id> [degree]`")
        await self._execute_punish(ctx, player, rule_id, degree)

    # ── Prefix group for !minecraft ──────────────────────────────────────────

    @commands.group(name="minecraft", invoke_without_command=True)
    async def minecraft_prefix(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.reply("Usage: `!minecraft <setup|status|rules|link|unlink|links>`")

    @minecraft_prefix.command(name="setup")
    async def minecraft_setup_prefix(self, ctx):
        await self._execute_minecraft_setup(ctx)

    @minecraft_prefix.command(name="status")
    async def minecraft_status_prefix(self, ctx):
        await self._execute_minecraft_status(ctx)

    @minecraft_prefix.command(name="rules")
    async def minecraft_rules_prefix(self, ctx):
        await self._execute_minecraft_rules(ctx)

    @minecraft_prefix.command(name="link")
    async def minecraft_link_prefix(self, ctx, member: discord.Member, mc_name: str):
        if not is_moderator(ctx.author):
            return await ctx.reply("Moderators only.")
        links = load_mc_links(ctx.guild.id)
        links[str(member.id)] = mc_name
        save_mc_links(ctx.guild.id, links)
        await ctx.reply(f"Linked **{member.name}** ↔ `{mc_name}`.")

    @minecraft_prefix.command(name="unlink")
    async def minecraft_unlink_prefix(self, ctx, member: discord.Member):
        if not is_moderator(ctx.author):
            return await ctx.reply("Moderators only.")
        links = load_mc_links(ctx.guild.id)
        if str(member.id) not in links:
            return await ctx.reply(f"{member.name} is not linked.")
        mc_name = links.pop(str(member.id))
        save_mc_links(ctx.guild.id, links)
        await ctx.reply(f"Unlinked **{member.name}** (was `{mc_name}`).")

    @minecraft_prefix.command(name="links")
    async def minecraft_links_prefix(self, ctx):
        if not is_moderator(ctx.author):
            return await ctx.reply("Moderators only.")
        links = load_mc_links(ctx.guild.id)
        if not links:
            return await ctx.reply("No linked accounts.")
        text = "\n".join(f"<@{d_id}> ↔ `{n}`" for d_id, n in links.items())
        await ctx.reply(embed=discord.Embed(title="Linked Accounts", description=text, color=0x5865F2))

    # ── Refactored Logic for Parity ──────────────────────────────────────────

    async def _execute_minecraft_status(self, ctx_or_int):
        guild_id = ctx_or_int.guild_id if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild.id
        rules = load_mc_rules(guild_id)
        links = load_mc_links(guild_id)
        infractions = load_mc_infractions(guild_id)
        api_status = "🟢 Running" if _api_server is not None else "🔴 Not running"

        embed = discord.Embed(title="Minecraft Module Status", color=0x5865F2)
        embed.add_field(name="Permanent API (port 7912)", value=api_status, inline=False)
        embed.add_field(name="Synced Rules", value=str(len(rules)), inline=True)
        embed.add_field(name="Linked Accounts", value=str(len(links)), inline=True)
        embed.add_field(name="MC Infractions", value=str(len(infractions)), inline=True)
        embed.set_footer(text=f"Guild ID: {guild_id}")
        await send_response(ctx_or_int, embed=embed)

    async def _execute_minecraft_rules(self, ctx_or_int):
        guild_id = ctx_or_int.guild_id if isinstance(ctx_or_int, discord.Interaction) else ctx_or_int.guild.id
        rules = load_mc_rules(guild_id)
        if not rules:
            return await send_response(ctx_or_int, "No rules synced yet.", ephemeral=True)

        embed = discord.Embed(title="Minecraft Server Rules", color=0xffaa00)
        for rule_id, rule in rules.items():
            name = rule.get("name", f"Rule {rule_id}")
            desc = rule.get("description", "")
            punishments = rule.get("punishments", [])
            pun_lines = "\n".join(f"  **{i+1}°** `{p}`" for i, p in enumerate(punishments)) if punishments else "*(Manual)*"
            embed.add_field(name=f"`{rule_id}` — {name}", value=f"{desc}\n{pun_lines}".strip(), inline=False)
        await send_response(ctx_or_int, embed=embed)

    # ── Internal Helpers ──────────────────────────────────────────────────────

    async def get_combined_history(self, member: discord.Member):
        from modules.core import load_server_data
        guild_id = member.guild.id

        # 1. Discord Data
        warns = load_server_data(guild_id, "warnings.json") or []
        user_warns = [w for w in warns if w["userId"] == str(member.id)]
        mutes = load_server_data(guild_id, "mutes.json") or []
        user_mutes = [m for m in mutes if m["userId"] == str(member.id)]

        # 2. MC Data
        links = load_mc_links(guild_id)
        mc_name = links.get(str(member.id))
        mc_infractions = []
        if mc_name:
            all_mc = load_mc_infractions(guild_id)
            mc_infractions = [r for r in all_mc if r["playerName"].lower() == mc_name.lower()]

        # 3. Combine
        items = []
        for w in user_warns:
            ts = datetime.fromisoformat(w["timestamp"]).timestamp()
            items.append({"origin": "Discord", "type": "Warning", "reason": w["reason"], "ts": ts})
        for m in user_mutes:
            ts = datetime.fromisoformat(m["timestamp"]).timestamp()
            items.append({"origin": "Discord", "type": f"Mute ({m['durationSec']//60}m)", "reason": m["reason"], "ts": ts})
        for r in mc_infractions:
            ts = r.get("timestamp") or 0
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    ts = 0
            ptype = r.get("punishmentType", r.get("punishment", "Unknown"))
            items.append({"origin": "Minecraft", "type": ptype.replace("_", " ").title(), "reason": r.get("reason", ""), "ts": ts})

        items.sort(key=lambda x: x["ts"], reverse=True)
        return items, mc_name


async def setup(bot: commands.Bot):
    await bot.add_cog(Minecraft(bot))
