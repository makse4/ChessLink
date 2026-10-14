import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int | None
    database_path: str
    public_url: str
    oauth_host: str
    oauth_port: int
    lichess_client_id: str
    user_agent: str
    rating_roles_path: str
    rating_refresh_minutes: int

    @property
    def lichess_redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/oauth/lichess/callback"


def load_config() -> Config:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")

    guild_id = os.getenv("GUILD_ID")

    return Config(
        discord_token=token,
        guild_id=int(guild_id) if guild_id else None,
        database_path=os.getenv("DATABASE_PATH", "chesslink.db"),
        public_url=os.getenv("PUBLIC_URL", "http://localhost:8080"),
        oauth_host=os.getenv("OAUTH_HOST", "0.0.0.0"),
        oauth_port=int(os.getenv("OAUTH_PORT", "8080")),
        lichess_client_id=os.getenv("LICHESS_CLIENT_ID", "chesslink-bot"),
        user_agent=os.getenv("USER_AGENT", "ChessLinkBot/1.0 (set USER_AGENT to your contact info)"),
        rating_roles_path=os.getenv("RATING_ROLES_PATH", "rating_roles.json"),
        rating_refresh_minutes=int(os.getenv("RATING_REFRESH_MINUTES", "120")),
    )