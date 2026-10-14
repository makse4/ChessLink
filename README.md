# ChessLink

A Discord bot that links a Discord user to their Lichess or Chess.com account and shows a chess profile embed.

## Commands

| Command | Description |
| --- | --- |
| `/link platform:Lichess` | Sign in with Lichess (OAuth). Linking completes when you authorize. |
| `/link platform:Chess.com username:<name>` | Gives you a code. Put it in your Chess.com bio, then run `/verify`. |
| `/verify` | Checks your Chess.com bio for the code and completes the link. |
| `/profile [user]` | Shows linked accounts, ratings, and game records in an embed. |
| `/unlink platform:<...>` | Removes a linked account. |

Each Chess.com or Lichess account can belong to only one Discord user.

## Rating roles

Rating roles give members a role based on their best rating across their linked accounts. Each server gets tier roles. A member holds at most one tier role, the highest one whose `min_rating` they meet. Members below the lowest tier hold none.

1. Create the tier roles in your server. The bot's own role must be **above** them in the role list, and the bot needs the **Manage Roles** permission.
2. Copy `rating_roles.example.json` to `rating_roles.json`. Key each entry by server ID, and list tier role IDs with their minimum ratings.
3. Restart the bot.

```json
{
  "123456789012345678": {
    "modes": ["blitz", "rapid", "classical", "daily"],
    "tiers": [
      {"role_id": 111111111111111111, "min_rating": 1000},
      {"role_id": 222222222222222222, "min_rating": 1500}
    ]
  }
}
```

- `modes` picks which time controls count. The rating used is the highest one across all modes and all linked accounts. Valid modes are `bullet`, `blitz`, `rapid`, `classical`, and `daily`. If omitted, the default is `blitz`, `rapid`, `classical`, `daily`. Bullet is left out by default because it tends to be inflated.
- Roles update when an account is linked or unlinked, and then on every `RATING_REFRESH_MINUTES` cycle.
- Provisional ratings count the same as established ones.
- If a rating lookup fails (for example, the platform is down), that member's roles are left unchanged for that cycle.

## How ownership is proven

- **Lichess:** OAuth 2 with PKCE. The bot receives the account identity from Lichess after you authorize, so no code is needed.
- **Chess.com:** The bot issues a one-time `CHESSLINK-XXXXXXXX` code. You add it to your profile bio and run `/verify`. The check reads your public profile page, because the official Chess.com Published-Data API does not expose the bio.

## Setup

1. Create a bot at https://discord.com/developers/applications, copy its token, and invite it with the `applications.commands` and `bot` scopes.
2. Install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill it in. Set `USER_AGENT` to include a contact address.
4. Run the bot:
   ```bash
   python bot.py
   ```

### Lichess callback URL

Lichess sends users to `PUBLIC_URL/oauth/lichess/callback`. That address must reach this process from the user's browser:

- Local testing: `PUBLIC_URL=http://localhost:8080` works as is.
- Production: put the bot behind a reverse proxy (for example Caddy or nginx) with HTTPS, and set `PUBLIC_URL` to that HTTPS origin.

## Notes and limitations

- Chess.com's Published-Data API caches data for up to 24 hours, so `/profile` ratings for Chess.com may lag behind the site.
- The bio check depends on the public Chess.com profile page. If Chess.com changes how that page renders, `ChessComClient.bio_contains` in `chess_api.py` may need updating.
- Pending Lichess logins and Chess.com codes are kept in memory or in the database respectively. Lichess logins started before a restart must be retried.
