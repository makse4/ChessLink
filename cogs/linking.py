import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands

from chess_api import APIError
from oauth_server import start_lichess_flow

PENDING_TTL_SECONDS = 30 * 60

PLATFORM_CHOICES = [
    app_commands.Choice(name="Lichess", value="lichess"),
    app_commands.Choice(name="Chess.com", value="chesscom"),
]
PLATFORM_NAMES = {"lichess": "Lichess", "chesscom": "Chess.com"}


class Linking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your Lichess or Chess.com account")
    @app_commands.describe(
        platform="Which account to link",
        username="Your Chess.com username (not needed for Lichess)",
    )
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def link(
        self,
        interaction: discord.Interaction,
        platform: app_commands.Choice[str],
        username: str | None = None,
    ):
        if platform.value == "lichess":
            await self._link_lichess(interaction)
        else:
            await self._link_chesscom(interaction, username)

    async def _link_lichess(self, interaction: discord.Interaction):
        url = start_lichess_flow(self.bot, interaction.user.id)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Authorize on Lichess", url=url))
        await interaction.response.send_message(
            "Click the button to sign in with Lichess. This proves you own the account. "
            "The link expires in 10 minutes.",
            view=view,
            ephemeral=True,
        )

    async def _link_chesscom(self, interaction: discord.Interaction, username: str | None):
        if not username or not username.strip():
            await interaction.response.send_message(
                "Please provide your Chess.com username, e.g. `/link platform:Chess.com username:yourname`.",
                ephemeral=True,
            )
            return

        username = username.strip()
        await interaction.response.defer(ephemeral=True)

        player = await self.bot.chesscom.player(username)
        if player is None:
            await interaction.followup.send(f"No Chess.com account named `{username}` was found.", ephemeral=True)
            return

        account = player["username"].lower()
        owner = self.bot.db.link_owner("chesscom", account)
        if owner is not None and owner != interaction.user.id:
            await interaction.followup.send(
                "That Chess.com account is already linked to a different Discord user.",
                ephemeral=True,
            )
            return

        code = f"CHESSLINK-{secrets.token_hex(4).upper()}"
        self.bot.db.set_pending(interaction.user.id, "chesscom", account, player["username"], code)
        await interaction.followup.send(
            f"To prove you own **{player['username']}**, add this code to your Chess.com profile bio:\n"
            f"```{code}```\n"
            "Save the bio, then run `/verify`. The code expires in 30 minutes. "
            "You can remove it from your bio after linking.",
            ephemeral=True,
        )

    @app_commands.command(name="verify", description="Confirm a pending Chess.com link by checking your bio")
    async def verify(self, interaction: discord.Interaction):
        pending = self.bot.db.get_pending(interaction.user.id, "chesscom")
        if pending is None:
            await interaction.response.send_message(
                "You have no pending Chess.com link. Start one with `/link platform:Chess.com username:yourname`.",
                ephemeral=True,
            )
            return

        if time.time() - pending.created_at > PENDING_TTL_SECONDS:
            self.bot.db.delete_pending(interaction.user.id, "chesscom")
            await interaction.response.send_message(
                "That verification code has expired. Run `/link` again to get a new one.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            found = await self.bot.chesscom.bio_contains(pending.account, pending.code)
        except APIError:
            await interaction.followup.send(
                "Could not reach Chess.com right now. Please try `/verify` again in a moment.",
                ephemeral=True,
            )
            return

        if not found:
            await interaction.followup.send(
                f"I couldn't find `{pending.code}` in the bio of **{pending.display_name}** yet. "
                "Make sure it is saved and visible on your profile, then try again.",
                ephemeral=True,
            )
            return

        if not self.bot.db.set_link(interaction.user.id, "chesscom", pending.account, pending.display_name):
            self.bot.db.delete_pending(interaction.user.id, "chesscom")
            await interaction.followup.send(
                "That Chess.com account was linked to a different Discord user while you were verifying.",
                ephemeral=True,
            )
            return

        self.bot.db.delete_pending(interaction.user.id, "chesscom")
        await self.bot.ratings.sync_user(interaction.user.id)
        await interaction.followup.send(
            f"Your Chess.com account **{pending.display_name}** is now linked.",
            ephemeral=True,
        )

    @app_commands.command(name="unlink", description="Remove a linked chess account")
    @app_commands.describe(platform="Which account to unlink")
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def unlink(self, interaction: discord.Interaction, platform: app_commands.Choice[str]):
        if self.bot.db.delete_link(interaction.user.id, platform.value):
            message = f"Your {PLATFORM_NAMES[platform.value]} account has been unlinked."
        else:
            message = f"You don't have a {PLATFORM_NAMES[platform.value]} account linked."
        await interaction.response.send_message(message, ephemeral=True)
        await self.bot.ratings.sync_user(interaction.user.id)


async def setup(bot):
    await bot.add_cog(Linking(bot))
