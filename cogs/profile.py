import asyncio
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from chess_api import APIError
from db import Link

PLATFORMS = {"lichess": "♞ Lichess", "chesscom": "♟ Chess.com"}
LICHESS_MODES = [("bullet", "Bullet"), ("blitz", "Blitz"), ("rapid", "Rapid"), ("classical", "Classical")]
CHESSCOM_MODES = [("chess_bullet", "Bullet"), ("chess_blitz", "Blitz"), ("chess_rapid", "Rapid"), ("chess_daily", "Daily")]
ACCENT = discord.Colour.from_rgb(212, 175, 55)


@dataclass(frozen=True)
class ProfileSection:
    name: str
    value: str
    best: int | None = None


def _rating_block(rows: list[tuple[str, str]]) -> str:
    width = max(len(label) for label, _ in rows)
    body = "\n".join(f"{label:<{width}}  {value}" for label, value in rows)
    return f"```\n{body}\n```"


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="Show your linked chess profiles")
    @app_commands.describe(user="Member whose profile to show (defaults to you)")
    async def profile(self, interaction: discord.Interaction, user: discord.User | None = None):
        target = user or interaction.user
        links = self.bot.db.get_links(target.id)

        if not links:
            if target.id == interaction.user.id:
                message = "You haven't linked any chess accounts yet. Use `/link` to get started."
            else:
                message = f"{target.display_name} hasn't linked any chess accounts."
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.defer()
        sections = await asyncio.gather(*(self._section(link) for link in links))
        await interaction.followup.send(embed=self._embed(target, sections))

    def _embed(self, target: discord.User, sections: list[ProfileSection]) -> discord.Embed:
        embed = discord.Embed(title="Chess Profile", colour=ACCENT, timestamp=discord.utils.utcnow())
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.set_thumbnail(url=target.display_avatar.url)

        rated = [s for s in sections if s.best is not None]
        if rated:
            top = max(rated, key=lambda s: s.best)
            embed.description = f"Top rating **{top.best}** on {top.name}"

        for section in sections:
            embed.add_field(name=section.name, value=section.value, inline=True)

        embed.set_footer(text="ChessLink · verified ownership", icon_url=self.bot.user.display_avatar.url)
        return embed

    async def _section(self, link: Link) -> ProfileSection:
        try:
            if link.platform == "lichess":
                return await self._lichess_section(link)
            return await self._chesscom_section(link)
        except APIError:
            return ProfileSection(
                PLATFORMS[link.platform],
                "Couldn't reach this platform right now. Try again later.",
            )

    def _missing(self, link: Link) -> ProfileSection:
        return ProfileSection(
            PLATFORMS[link.platform],
            f"**{link.display_name}** was not found. It may have been closed.",
        )

    async def _lichess_section(self, link: Link) -> ProfileSection:
        data = await self.bot.lichess.user(link.account)
        if data is None:
            return self._missing(link)

        title = f"{data['title']} " if data.get("title") else ""
        rows = []
        best = None
        for key, label in LICHESS_MODES:
            perf = data.get("perfs", {}).get(key)
            if perf and perf.get("games", 0) > 0:
                provisional = "?" if perf.get("prov") else ""
                rows.append((label, f"{perf['rating']}{provisional}"))
                best = max(best or 0, perf["rating"])

        count = data.get("count", {})
        lines = [
            f"**[{title}{data['username']}]({data['url']})**",
            _rating_block(rows) if rows else "*No rated games yet*",
            f"{count.get('all', 0):,} games · {count.get('win', 0):,} W · "
            f"{count.get('loss', 0):,} L · {count.get('draw', 0):,} D",
        ]

        dates = [f"Joined <t:{data['createdAt'] // 1000}:D>"]
        if "seenAt" in data:
            dates.append(f"Seen <t:{data['seenAt'] // 1000}:D>")
        lines.append(" · ".join(dates))

        return ProfileSection(PLATFORMS["lichess"], "\n".join(lines), best)

    async def _chesscom_section(self, link: Link) -> ProfileSection:
        player = await self.bot.chesscom.player(link.account)
        if player is None:
            return self._missing(link)

        stats = await self.bot.chesscom.stats(link.account)

        title = f"{player['title']} " if player.get("title") else ""
        rows = []
        best = None
        wins = losses = draws = 0
        for key, label in CHESSCOM_MODES:
            mode = stats.get(key)
            if not mode or "last" not in mode:
                continue
            rating = mode["last"]["rating"]
            peak = mode.get("best", {}).get("rating")
            value = f"{rating}" + (f"  (peak {peak})" if peak else "")
            rows.append((label, value))
            best = max(best or 0, rating)

            record = mode.get("record", {})
            wins += record.get("win", 0)
            losses += record.get("loss", 0)
            draws += record.get("draw", 0)

        lines = [
            f"**[{title}{player['username']}]({player['url']})**",
            _rating_block(rows) if rows else "*No rated games yet*",
        ]

        total = wins + losses + draws
        if total:
            lines.append(f"{total:,} games · {wins:,} W · {losses:,} L · {draws:,} D")

        dates = [f"Joined <t:{player['joined']}:D>"]
        if "last_online" in player:
            dates.append(f"Seen <t:{player['last_online']}:D>")
        lines.append(" · ".join(dates))

        return ProfileSection(PLATFORMS["chesscom"], "\n".join(lines), best)


async def setup(bot):
    await bot.add_cog(Profile(bot))
