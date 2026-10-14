import asyncio
import logging

import aiohttp
import discord
from aiohttp import web
from discord.ext import commands

from chess_api import ChessComClient, LichessClient
from config import Config, load_config
from db import Database
from oauth_server import PendingOAuth, create_app
from ratings import RatingRoles, load_rating_config

EXTENSIONS = ("cogs.linking", "cogs.profile")


class ChessLinkBot(commands.Bot):
    def __init__(self, config: Config):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.config = config
        self.db = Database(config.database_path)
        self.pending_oauth: dict[str, PendingOAuth] = {}
        self.session: aiohttp.ClientSession | None = None
        self.lichess: LichessClient | None = None
        self.chesscom: ChessComClient | None = None
        self.web_runner: web.AppRunner | None = None
        self.ratings = RatingRoles(self, load_rating_config(config.rating_roles_path))
        self.rating_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": self.config.user_agent},
            timeout=aiohttp.ClientTimeout(total=15),
        )
        self.lichess = LichessClient(self.session, self.config.lichess_client_id)
        self.chesscom = ChessComClient(self.session)

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        if self.ratings.configs:
            self.rating_task = asyncio.create_task(self.ratings.run_forever(self.config.rating_refresh_minutes))

        self.web_runner = web.AppRunner(create_app(self))
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, self.config.oauth_host, self.config.oauth_port).start()
        logging.info("OAuth callback listening on %s", self.config.lichess_redirect_uri)

        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        logging.info("Logged in as %s (%s)", self.user, self.user.id)

    async def close(self) -> None:
        if self.rating_task is not None:
            self.rating_task.cancel()
        if self.web_runner is not None:
            await self.web_runner.cleanup()
        if self.session is not None:
            await self.session.close()
        self.db.conn.close()
        await super().close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    ChessLinkBot(config).run(config.discord_token)


if __name__ == "__main__":
    main()
