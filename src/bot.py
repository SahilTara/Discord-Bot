import discord
from discord.ext import commands
from src.media import Music

if not discord.opus.is_loaded():
    discord.opus.load_opus('opus')


bot = commands.Bot(command_prefix='$', description="Hello, I am the UO CS Discord Bot!")


@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')
    await bot.change_presence(activity=discord.Game(name="Intense Coding"))

bot.add_cog(Music(bot))
bot.run("")