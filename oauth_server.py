import base64
import hashlib
import html
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from aiohttp import web

from chess_api import APIError

if TYPE_CHECKING:
    from bot import ChessLinkBot

log = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600


@dataclass
class PendingOAuth:
    discord_id: int
    code_verifier: str
    created_at: float


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def start_lichess_flow(bot: "ChessLinkBot", discord_id: int) -> str:
    now = time.time()
    for state in [s for s, p in bot.pending_oauth.items() if now - p.created_at > STATE_TTL_SECONDS]:
        del bot.pending_oauth[state]

    verifier, challenge = create_pkce_pair()
    state = secrets.token_urlsafe(32)
    bot.pending_oauth[state] = PendingOAuth(discord_id, verifier, now)
    return bot.lichess.authorize_url(bot.config.lichess_redirect_uri, state, challenge)


def create_app(bot: "ChessLinkBot") -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/oauth/lichess/callback", lichess_callback)
    return app


def _page(title: str, message: str, status: int = 200) -> web.Response:
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
        f"{html.escape(title)}</title></head>"
        "<body style=\"font-family:sans-serif;max-width:480px;margin:3rem auto\">"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"
    )
    return web.Response(text=body, content_type="text/html", status=status)


async def _notify(bot: "ChessLinkBot", discord_id: int, message: str) -> None:
    try:
        user = await bot.fetch_user(discord_id)
        await user.send(message)
    except discord.HTTPException:
        log.info("Could not DM user %s about their link", discord_id)


async def lichess_callback(request: web.Request) -> web.Response:
    bot: "ChessLinkBot" = request.app["bot"]
    state = request.query.get("state", "")
    pending = bot.pending_oauth.pop(state, None)

    if pending is None or time.time() - pending.created_at > STATE_TTL_SECONDS:
        return _page(
            "Link expired",
            "This authorization link is invalid or has expired. Run /link in Discord to try again.",
            status=400,
        )

    if "error" in request.query:
        return _page("Authorization cancelled", "Lichess did not grant access. Run /link to try again.", status=400)

    code = request.query.get("code")
    if not code:
        return _page("Missing code", "Lichess did not return an authorization code. Run /link to try again.", status=400)

    try:
        token = await bot.lichess.exchange_code(code, pending.code_verifier, bot.config.lichess_redirect_uri)
        account = await bot.lichess.account(token)
    except APIError:
        log.exception("Lichess OAuth exchange failed")
        return _page("Lichess error", "Could not verify your Lichess account. Please try again.", status=502)

    username = account["username"]
    if not bot.db.set_link(pending.discord_id, "lichess", account["id"], username):
        return _page(
            "Already linked",
            f"The Lichess account {username} is already linked to a different Discord user.",
            status=409,
        )

    await bot.ratings.sync_user(pending.discord_id)
    await _notify(bot, pending.discord_id, f"Your Lichess account **{username}** is now linked.")
    return _page("Account linked", f"Your Lichess account {username} is linked. You can close this tab.")
