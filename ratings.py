import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from chess_api import APIError
from db import Link

if TYPE_CHECKING:
    from bot import ChessLinkBot

log = logging.getLogger(__name__)

REASON = "ChessLink rating role"
LICHESS_KEYS = {"bullet": "bullet", "blitz": "blitz", "rapid": "rapid", "classical": "classical", "daily": "correspondence"}
CHESSCOM_KEYS = {"bullet": "chess_bullet", "blitz": "chess_blitz", "rapid": "chess_rapid", "daily": "chess_daily"}
DEFAULT_MODES = ("blitz", "rapid", "classical", "daily")
# Pause between users during periodic runs to stay polite to Lichess rate limits.
PACING_SECONDS = 1.0


@dataclass(frozen=True)
class Tier:
    role_id: int
    min_rating: int


@dataclass(frozen=True)
class GuildRatingConfig:
    guild_id: int
    modes: tuple[str, ...]
    tiers: tuple[Tier, ...]


@dataclass(frozen=True)
class _GuildContext:
    config: GuildRatingConfig
    guild: discord.Guild
    tiers: tuple[Tier, ...]


def load_rating_config(path: str) -> dict[int, GuildRatingConfig]:
    if not os.path.exists(path):
        log.info("No rating roles config at %s; rating roles are disabled", path)
        return {}

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    configs = {}
    for guild_key, entry in raw.items():
        guild_id = int(guild_key)
        modes = tuple(entry.get("modes", DEFAULT_MODES))
        unknown = [mode for mode in modes if mode not in LICHESS_KEYS]
        if unknown:
            raise ValueError(f"Guild {guild_id}: unknown rating modes {unknown}. Valid: {sorted(LICHESS_KEYS)}")

        tiers = tuple(
            sorted(
                (Tier(int(t["role_id"]), int(t["min_rating"])) for t in entry.get("tiers", [])),
                key=lambda tier: tier.min_rating,
                reverse=True,
            )
        )
        if not tiers:
            raise ValueError(f"Guild {guild_id}: no tiers defined")

        configs[guild_id] = GuildRatingConfig(guild_id, modes, tiers)
    return configs


async def _platform_ratings(bot: "ChessLinkBot", link: Link, modes: tuple[str, ...]) -> list[int]:
    if link.platform == "lichess":
        data = await bot.lichess.user(link.account)
        if data is None:
            return []
        perfs = data.get("perfs", {})
        return [
            perfs[LICHESS_KEYS[mode]]["rating"]
            for mode in modes
            if LICHESS_KEYS[mode] in perfs and perfs[LICHESS_KEYS[mode]].get("games", 0) > 0
        ]

    stats = await bot.chesscom.stats(link.account)
    ratings = []
    for mode in modes:
        key = CHESSCOM_KEYS.get(mode)
        if key and "last" in stats.get(key, {}):
            ratings.append(stats[key]["last"]["rating"])
    return ratings


async def best_rating(bot: "ChessLinkBot", discord_id: int, modes: tuple[str, ...]) -> int | None:
    """Highest rating across all of a user's linked accounts for the given modes. Raises APIError on lookup failure."""
    ratings: list[int] = []
    for link in bot.db.get_links(discord_id):
        ratings.extend(await _platform_ratings(bot, link, modes))
    return max(ratings, default=None)


class RatingRoles:
    def __init__(self, bot: "ChessLinkBot", configs: dict[int, GuildRatingConfig]):
        self.bot = bot
        self.configs = configs

    async def run_forever(self, interval_minutes: int) -> None:
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.sync_all()
            except Exception:
                log.exception("Periodic rating role sync failed")
            await asyncio.sleep(interval_minutes * 60)

    async def sync_all(self) -> None:
        contexts = await self._contexts()
        if not contexts:
            return
        for discord_id in self.bot.db.linked_user_ids():
            try:
                await self._sync_user(discord_id, contexts)
            except APIError as exc:
                log.warning("Rating roles: skipped user %s: %s", discord_id, exc)
            await asyncio.sleep(PACING_SECONDS)

    async def sync_user(self, discord_id: int) -> None:
        if not self.configs:
            return
        contexts = await self._contexts()
        try:
            await self._sync_user(discord_id, contexts)
        except APIError as exc:
            log.warning("Rating roles: skipped user %s: %s", discord_id, exc)

    async def _contexts(self) -> list[_GuildContext]:
        contexts = []
        for config in self.configs.values():
            try:
                guild = await self.bot.fetch_guild(config.guild_id)
                existing = {role.id for role in await guild.fetch_roles()}
            except discord.HTTPException as exc:
                log.warning("Rating roles: cannot access guild %s: %s", config.guild_id, exc)
                continue

            for tier in config.tiers:
                if tier.role_id not in existing:
                    log.warning("Rating roles: role %s not found in guild %s", tier.role_id, config.guild_id)
            tiers = tuple(tier for tier in config.tiers if tier.role_id in existing)
            if tiers:
                contexts.append(_GuildContext(config, guild, tiers))
        return contexts

    async def _sync_user(self, discord_id: int, contexts: list[_GuildContext]) -> None:
        # Look up each distinct mode set once, and finish all lookups before touching any roles.
        ratings: dict[tuple[str, ...], int | None] = {}
        for ctx in contexts:
            if ctx.config.modes not in ratings:
                ratings[ctx.config.modes] = await best_rating(self.bot, discord_id, ctx.config.modes)

        for ctx in contexts:
            await self._apply(ctx, discord_id, ratings[ctx.config.modes])

    async def _apply(self, ctx: _GuildContext, discord_id: int, rating: int | None) -> None:
        member = ctx.guild.get_member(discord_id)
        if member is None:
            try:
                member = await ctx.guild.fetch_member(discord_id)
            except discord.NotFound:
                return
            except discord.HTTPException as exc:
                log.warning("Rating roles: cannot fetch member %s in guild %s: %s", discord_id, ctx.guild.id, exc)
                return

        target = next((tier for tier in ctx.tiers if rating is not None and rating >= tier.min_rating), None)
        tier_ids = {tier.role_id for tier in ctx.tiers}
        current = {role.id for role in member.roles}
        desired = {target.role_id} if target else set()
        to_add = desired - current
        to_remove = (current & tier_ids) - desired

        try:
            if to_remove:
                await member.remove_roles(*(discord.Object(id=i) for i in to_remove), reason=REASON)
            if to_add:
                await member.add_roles(*(discord.Object(id=i) for i in to_add), reason=REASON)
        except discord.HTTPException as exc:
            log.warning(
                "Rating roles: could not update member %s in guild %s (check Manage Roles and role order): %s",
                discord_id,
                ctx.guild.id,
                exc,
            )
