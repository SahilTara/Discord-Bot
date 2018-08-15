import discord
from discord.ext import commands

import asyncio
import youtube_dl
from math import ceil


class YoutubeSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, info, volume=0.8):
        super().__init__(source, volume)
        self.uploader = info.get('uploader')
        self.views = info.get('view_count')
        self.likes = info.get('like_count')
        self.title = info.get('title')
        self.duration = info.get('duration')
        self.thumbnail = info.get('thumbnail')

    @classmethod
    async def from_url(cls, url: str, volume: float, *, loop: asyncio.AbstractEventLoop = None, opts: dict = None, **kwargs):
        """
        Creates an AudioSource for videos.

        :param url: url you wish to download, should be valid!
        :type url: str
        :param loop: The event loop
        :type loop: asyncio.AbstractEventLoop
        :param opts: User defined options for youtube-dl
        :type opts: dict
        :param kwargs: Forwarded to FFmpegPCMAudio
        """

        youtube_opts = {
            'format': 'webm[abr>0]/bestaudio/best',
            'prefer_ffmpeg': True
        }

        if opts is not None:
            youtube_opts.update(opts)

        ytdl = youtube_dl.YoutubeDL(youtube_opts)
        info = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        download_url = info.get('url')
        return cls(discord.FFmpegPCMAudio(download_url, **kwargs), info=info)

    def __str__(self):
        result = f"{self.title} by {self.uploader}"
        result += (f" [{self.get_duration_string()}]" if self.duration else "")
        return result

    def get_duration_string(self):
        if self.duration:
            return "Length: {0[0]}m {0[1]}s".format(divmod(self.duration, 60))
        return ""


class Music:
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.vc: discord.VoiceClient = None
        self.current: YoutubeSource = None
        self.play_next: asyncio.Event = asyncio.Event()  # Acts kind of like a lock mechanism, but can wake multiple.
        self.songs: asyncio.Queue = asyncio.Queue()  # We have to queue the songs of course
        self.audio_player = self.bot.loop.create_task(self.audio_task())
        self.volume = 0.8
        self.skips = set()
        self.embeds = dict()

    def toggle_lock(self, error=None):
        if error is not None:
            print(error)
        self.bot.loop.call_soon_threadsafe(self.play_next.set)

    def get_current_video_embed(self, ctx):
        embed = discord.Embed(
            title=self.current.title,
            colour=discord.colour.Colour.blue()
        )

        author = ctx.message.author

        embed.set_author(name="Now Playing")
        embed.set_image(url=self.current.thumbnail)
        embed.set_thumbnail(url=author.avatar_url)

        embed.add_field(name="Uploader", value=self.current.uploader)
        embed.add_field(name="Requested by", value=author)
        embed.add_field(name="Views", value=f"{self.current.views:,}")
        embed.add_field(name="Likes", value=f"{self.current.likes:,}")
        embed.set_footer(text=self.current.get_duration_string())

        return embed

    async def audio_task(self):
        while True:
            if self.vc:
                self.play_next.clear()  # Set the event to false (kind of like acquiring a lock

                self.current, ctx = await self.songs.get()

                if ctx is not None:
                    await ctx.send(embed=self.get_current_video_embed(ctx))
                self.skips.clear()
                self.vc.play(self.current, after=self.toggle_lock)
                await self.play_next.wait()
            else:
                await asyncio.sleep(0)  # Needs some type of await

    def get_enqueue_embed(self, player: YoutubeSource, ctx):
        embed = discord.Embed(
            title=player.title,
            color=discord.colour.Colour.green()
        )

        embed.set_author(name="Enqueued")
        embed.add_field(name="Requested by", value=ctx.message.author)
        embed.add_field(name="Position", value=f"{self.songs.qsize()}/{self.songs.qsize()}")
        embed.set_footer(text=player.get_duration_string())

        return embed

    @commands.command()
    @commands.guild_only()
    async def play(self, ctx, link: str):
        if not (link.startswith("https://www.youtube.com/watch?v=")):
            await ctx.send("Sorry, this is not a valid url")
            return

        user = ctx.message.author
        channel = None

        if user.voice:
            channel = user.voice.channel

        if channel is not None:
            if self.vc is None:
                self.vc = await channel.connect()
            args = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            player = await YoutubeSource.from_url(url=link,
                                                  volume=self.volume,
                                                  loop=self.bot.loop,
                                                  opts=None,
                                                  before_options=args)

            await self.songs.put((player, ctx))
            await ctx.send(embed=self.get_enqueue_embed(player, ctx))
        else:
            await ctx.send("Sorry. You must be in a voice channel for this command")

    @commands.command()
    @commands.guild_only()
    async def stop(self, ctx):
        if self.vc is not None:
            await ctx.send("Clearing song queue, and leaving the voice channel")
            self.songs = asyncio.Queue()
            await self.vc.disconnect()
            self.embeds.clear()
            self.vc = None
        else:
            await ctx.send("Sorry. I am not in a channel right now!")

    @commands.command()
    @commands.guild_only()
    async def skip(self, ctx):
        skip_size = 2
        if self.vc and self.vc.is_playing():
            author: discord.Member = ctx.message.author
            if len(self.skips) > skip_size:
                return
            if author.id not in self.skips:
                self.skips.add(author.id)
                await ctx.send("Voted to skip song! ({0}/{1})".format(len(self.skips), skip_size))
            else:
                await ctx.send("You have already voted!")
            if len(self.skips) == skip_size:
                await ctx.send("Now skipping song!")
                self.vc.stop()
        else:
            await ctx.send("I am not playing anything right now!")

    def get_queue_embed_and_page(self, page: int=1):
        embed = discord.Embed(
            title="List of songs",
            color=discord.colour.Colour.dark_gold(),
            description=""
        )

        items_per_page = 5
        pages = ceil(self.songs.qsize() / items_per_page)

        if 0 < pages < page:
            page = 1
        elif pages == 0:
            page = 0
        elif page <= 0:
            page = pages

        start = (page-1) * items_per_page
        end = start + items_per_page

        for index, ele in enumerate(list(self.songs._queue)[start:end], start=start):
            player: YoutubeSource = ele[0]
            embed.description += f"{index+1}. ***{player.title}*** [{player.get_duration_string()}]\n"

        embed.set_footer(text=f"Viewing page {page}/{pages}")
        return embed, page

    @commands.command()
    @commands.guild_only()
    async def queue(self, ctx):
        result = self.get_queue_embed_and_page()  # (embed,page)
        msg: discord.Message = await ctx.send(embed=result[0])
        await msg.add_reaction("⬅")
        await msg.add_reaction("➡")

        self.embeds[msg.id] = 1

    async def on_reaction_add(self, reaction: discord.Reaction, user):
        message: discord.Message = reaction.message

        # If it is the bot reacting, we are in a bugged state and we shouldn't process anything
        # This fixes a bug where the reaction prompt disappears if the user clicks too fast
        # (since the listener somehow gets applied).
        if user.id == self.bot.user.id:
            return

        if message.id in self.embeds.keys():
            page_num = self.embeds[message.id]

            await message.remove_reaction(reaction.emoji, user)

            if reaction.emoji == "⬅":
                page_num -= 1
            elif reaction.emoji == "➡":
                page_num += 1

            result = self.get_queue_embed_and_page(page_num)
            self.embeds[message.id] = result[1]
            await message.edit(embed=result[0])

    @commands.command()
    @commands.guild_only()
    async def vol(self, ctx, volume: int = 80):
        if volume < 0:
            volume = 0
        elif volume > 100:
            volume = 100

        self.volume = volume / 100
        self.current.volume = self.volume
        await ctx.send(f"Set volume to {volume}/100")