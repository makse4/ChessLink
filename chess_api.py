from html import unescape
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp


class APIError(Exception):
    pass


async def _get_json(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> dict | None:
    """GET a JSON document. Returns None on 404 and raises APIError on other failures."""
    try:
        async with session.get(url, **kwargs) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise APIError(f"{url} returned HTTP {resp.status}")
            return await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise APIError(f"Request to {url} failed: {exc}") from exc


class LichessClient:
    BASE = "https://lichess.org"

    def __init__(self, session: aiohttp.ClientSession, client_id: str):
        self.session = session
        self.client_id = client_id

    def authorize_url(self, redirect_uri: str, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
        }
        return f"{self.BASE}/oauth?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }
        try:
            async with self.session.post(f"{self.BASE}/api/token", data=data) as resp:
                if resp.status != 200:
                    raise APIError(f"Lichess token exchange failed with HTTP {resp.status}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise APIError(f"Lichess token exchange failed: {exc}") from exc
        return body["access_token"]

    async def account(self, token: str) -> dict:
        data = await _get_json(
            self.session,
            f"{self.BASE}/api/account",
            headers={"Authorization": f"Bearer {token}"},
        )
        if data is None:
            raise APIError("Lichess account lookup returned 404")
        return data

    async def user(self, username: str) -> dict | None:
        return await _get_json(self.session, f"{self.BASE}/api/user/{quote(username)}")


class ChessComClient:
    API = "https://api.chess.com/pub"
    WEB = "https://www.chess.com"

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def player(self, username: str) -> dict | None:
        return await _get_json(self.session, f"{self.API}/player/{quote(username)}")

    async def stats(self, username: str) -> dict:
        data = await _get_json(self.session, f"{self.API}/player/{quote(username)}/stats")
        return data or {}

    async def bio_contains(self, username: str, code: str) -> bool:
        """The public API does not expose the bio, so this reads the public profile page and looks for the code."""
        try:
            async with self.session.get(f"{self.WEB}/member/{quote(username)}") as resp:
                if resp.status == 404:
                    return False
                if resp.status != 200:
                    raise APIError(f"chess.com profile page returned HTTP {resp.status}")
                page = await resp.text()
        except aiohttp.ClientError as exc:
            raise APIError(f"chess.com profile page request failed: {exc}") from exc
        return code in unescape(page)
