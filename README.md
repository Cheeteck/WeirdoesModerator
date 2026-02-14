# Weirdoes Moderator

## What is Weirdoes Moderator?

Weirdoes Moderator is a moderation bot originally built for the Weirdoes server, it has a variety of funny and useful features to help moderate a Discord server.

## Commands

### Slash Commands

- `/warn <user> <reason>` - Warn a user
- `/mute <user> <duration> <reason>` - Mute a user
- `/unmute <user>` - Unmute a user
- `/clear <amount>` - Clear messages
- `/kick <user> <reason>` - Kick a user
- `/ban <user> <reason>` - Ban a user
- `/unban <user>` - Unban a user
- `/timeout <user> <duration> <reason>` - Timeout a user
- `/untimeout <user>` - Untimeout a user
- `/modrole <role>` - Set moderator role
- `/modroles` - List moderator roles
- `/reset` - Reset bot
- `/help` - Show help

### Prefix Commands

- `!warn <user> <reason>` - Warn a user
- `!mute <user> <duration> <reason>` - Mute a user
- `!unmute <user>` - Unmute a user
- `!clear <amount>` - Clear messages
- `!kick <user> <reason>` - Kick a user
- `!ban <user> <reason>` - Ban a user
- `!unban <user>` - Unban a user
- `!timeout <user> <duration> <reason>` - Timeout a user
- `!untimeout <user>` - Untimeout a user
- `!modrole <role>` - Set moderator role
- `!modroles` - List moderator roles
- `!reset` - Reset bot
- `!help` - Show help

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Create a `.env` file with the following variables:
   ```env
   TOKEN=your_bot_token
   CLIENT_ID=your_client_id
   GROQ=your_groq_api_key
   OWNER_ID=your_owner_id
   ```
3. Run the bot: `python main.py`

## Jarivs

The bot has a Jarvis system, of course based on Jarvis from the Iron Man movies. Jarvis can execute commands just like if you did it yourself

Examples:
```
Jarvis, ban @user for spamming
```
```
*In reply to @cheeteck*
Jarvis warn him for spam