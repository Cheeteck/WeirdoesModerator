import os
import json
import discord
from functools import wraps

def get_server_dir(guild_id: int) -> str:
    path = os.path.join(".", "servers", str(guild_id))
    os.makedirs(path, exist_ok=True)
    return path

def load_server_data(guild_id: int, filename: str):
    path = os.path.join(get_server_dir(guild_id), filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return None

def save_server_data(guild_id: int, filename: str, data):
    path = os.path.join(get_server_dir(guild_id), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def is_module_enabled(guild_id: int, module_name: str) -> bool:
    if module_name.lower() == "core":
        return True
    data = load_server_data(guild_id, "modules.json") or {}
    enabled = data.get("enabled", [])
    return module_name.lower() in [m.lower() for m in enabled]

def enable_server_module(guild_id: int, module_name: str):
    data = load_server_data(guild_id, "modules.json") or {"enabled": []}
    if "enabled" not in data:
        data["enabled"] = []
    if module_name.lower() not in [m.lower() for m in data["enabled"]]:
        data["enabled"].append(module_name.lower())
        save_server_data(guild_id, "modules.json", data)

def disable_server_module(guild_id: int, module_name: str):
    data = load_server_data(guild_id, "modules.json") or {"enabled": []}
    if "enabled" not in data:
        data["enabled"] = []
    data["enabled"] = [m for m in data["enabled"] if m.lower() != module_name.lower()]
    save_server_data(guild_id, "modules.json", data)

class Module:
    @staticmethod
    def _dependency(name, soft=False):
        def decorator(cls):
            if not hasattr(cls, '_deps'):
                cls._deps = []
            cls._deps.append({"name": name, "soft": soft})
            return cls
        return decorator

    @staticmethod
    def dependency(name):
        return Module._dependency(name, soft=False)

    @staticmethod
    def help(commands=None, description="No description provided."):
        if commands is None:
            commands = {}
        def decorator(cls):
            cls._help_info = {'description': description, 'commands': commands}
            return cls
        return decorator

    @staticmethod
    def enabled():
        def decorator(cls):
            async def cog_check(self, ctx):
                if not getattr(ctx, "guild", None):
                    return True
                return is_module_enabled(ctx.guild.id, self.__class__.__name__)
            cls.cog_check = cog_check
            
            async def interaction_check(self, interaction: discord.Interaction):
                if not getattr(interaction, "guild_id", None):
                    return True
                return is_module_enabled(interaction.guild_id, self.__class__.__name__)
            cls.interaction_check = interaction_check
            
            return cls
        return decorator

# Add soft dependency static method
def _dependency_soft(name):
    return Module._dependency(name, soft=True)

Module.dependency.soft = _dependency_soft
