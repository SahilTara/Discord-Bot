import discord
from discord.ext import commands

import asyncio
import youtube_dl


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
    async def from_url(cls, url: str, *, loop: asyncio.AbstractEventLoop = None, opts: dict = None, **kwargs):
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
        self.skips = set()

    def toggle_lock(self, error=None):
        if error is not None:
            print(error)
        self.bot.loop.call_soon_threadsafe(self.play_next.set)

    def get_current_video_embed(self, ctx):
        embed = discord.Embed(
            title=self.current.title,
            colour=discord.colour.Color.blue()
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
                                                  loop=self.bot.loop,
                                                  opts=None,
                                                  before_options=args)

            await ctx.send("Enqueued {0} at position {1} of {1} requested by:{2}".format(str(player),
                                                                                         self.songs.qsize(),
                                                                                         ctx.message.author))
            await self.songs.put((player, ctx))

        else:
            await ctx.send("Sorry. You must be in a voice channel for this command")

    @commands.command()
    @commands.guild_only()
    async def stop(self, ctx):
        if self.vc is not None:
            await ctx.send("Clearing song queue, and leaving the voice channel")

            self.songs = asyncio.Queue()
            await self.vc.disconnect()
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
