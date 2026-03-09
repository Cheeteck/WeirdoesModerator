import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize the Bot instance
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

async def setup():
    # Explicitly load the Core module
    await bot.load_extension("modules.core")
    
    # Call the Core.load_all_modules() function
    core_cog = bot.get_cog("Core")
    if core_cog:
        await core_cog.load_all_modules()

bot.setup_hook = setup

if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("No TOKEN found in .env file!")
    else:
        print(f"Token loaded (starts with: {token[:5]}...)")
        bot.run(token)