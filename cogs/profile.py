import asyncio
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from chess_api import APIError
from db import Link

PLATFORMS = {"lichess": "♞ Lichess", "chesscom": "♟ Chess.com"}
LICHESS_MODES = [("bullet", "Bullet", "⚡"), ("blitz", "Blitz", "🔥"), ("rapid", "Rapid", "⏱️"), ("classical", "Classical", "🕰️")]
CHESSCOM_MODES = [
    ("chess_bullet", "Bullet", "⚡"),
    ("chess_blitz", "Blitz", "🔥"),
    ("chess_rapid", "Rapid", "⏱️"),
    ("chess_daily", "Daily", "📅"),
]
ACCENT = discord.Colour.from_rgb(212, 175, 55)


@dataclass(frozen=True)
class ProfileSection:
    name: str
    value: str
    best: int | None = None


@dataclass(frozen=True)
class Rating:
    icon: str
    label: str
    rating: int
    provisional: bool = False
    peak: int | None = None


def _rating_lines(ratings: list[Rating]) -> str:
    if not ratings:
        return "*No rated games yet*"

    top = max(r.rating for r in ratings)
    lines = []
    for r in ratings:
        value = f"{r.rating}{'?' if r.provisional else ''}"
        if r.rating == top:
            value = f"**{value}**"
        line = f"{r.icon} {r.label} · {value}"
        if r.peak:
            line += f" *(peak {r.peak})*"
        lines.append(line)
    return "\n".join(lines)


def _stats_lines(games: int, wins: int, losses: int, draws: int) -> str:
    return (
        f"⚔️ **{games:,}** games\n"
        f"✅ **{wins:,}** wins · ❌ **{losses:,}** losses · ➖ **{draws:,}** draws"
    )


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
            embed.description = f"📊 Top rating **{top.best}** on {top.name}"

        for section in sections:
            embed.add_field(name=section.name, value=section.value, inline=True)

        embed.set_footer(text="ChessLink · ✅ Verified ownership", icon_url=self.bot.user.display_avatar.url)
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
        ratings = []
        for key, label, icon in LICHESS_MODES:
            perf = data.get("perfs", {}).get(key)
            if perf and perf.get("games", 0) > 0:
                ratings.append(Rating(icon, label, perf["rating"], bool(perf.get("prov"))))

        count = data.get("count", {})
        header = [f"**[{title}{data['username']}]({data['url']})**"]
        location = data.get("profile", {}).get("location")
        if location:
            header.append(f"📍 {location}")

        dates = [f"📅 Joined <t:{data['createdAt'] // 1000}:D>"]
        if "seenAt" in data:
            dates.append(f"👁️ Seen <t:{data['seenAt'] // 1000}:D>")

        blocks = [
            "\n".join(header),
            _rating_lines(ratings),
            _stats_lines(count.get("all", 0), count.get("win", 0), count.get("loss", 0), count.get("draw", 0)),
            "\n".join(dates),
        ]
        best = max((r.rating for r in ratings), default=None)
        return ProfileSection(PLATFORMS["lichess"], "\n\n".join(blocks), best)

    async def _chesscom_section(self, link: Link) -> ProfileSection:
        player = await self.bot.chesscom.player(link.account)
        if player is None:
            return self._missing(link)

        stats = await self.bot.chesscom.stats(link.account)

        title = f"{player['title']} " if player.get("title") else ""
        ratings = []
        wins = losses = draws = 0
        for key, label, icon in CHESSCOM_MODES:
            mode = stats.get(key)
            if not mode or "last" not in mode:
                continue
            ratings.append(
                Rating(icon, label, mode["last"]["rating"], peak=mode.get("best", {}).get("rating"))
            )
            record = mode.get("record", {})
            wins += record.get("win", 0)
            losses += record.get("loss", 0)
            draws += record.get("draw", 0)

        header = [f"**[{title}{player['username']}]({player['url']})**"]
        if player.get("location"):
            header.append(f"📍 {player['location']}")

        dates = [f"📅 Joined <t:{player['joined']}:D>"]
        if "last_online" in player:
            dates.append(f"👁️ Seen <t:{player['last_online']}:D>")

        blocks = ["\n".join(header), _rating_lines(ratings)]
        total = wins + losses + draws
        if total:
            blocks.append(_stats_lines(total, wins, losses, draws))
        blocks.append("\n".join(dates))

        best = max((r.rating for r in ratings), default=None)
        return ProfileSection(PLATFORMS["chesscom"], "\n\n".join(blocks), best)


async def setup(bot):
    await bot.add_cog(Profile(bot))
