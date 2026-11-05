# Postiz on this Mac (local server)

A second Postiz, running in Docker on this Mac, for the platforms that are easy
to connect. It runs next to Postiz Cloud and does not replace it.

| | |
| - | - |
| Address | <http://localhost:4007> (this Mac only; nothing listens on the network or Tailscale) |
| Folder | `~/postiz-local/` (outside this repo on purpose: it holds secrets) |
| Version | Postiz `v2.25.3` (image `ghcr.io/gitroomhq/postiz-app:v2.25.3`), installed 2026-11-05 |
| Settings and secrets | `~/postiz-local/.env` (hidden file, private; never copy it into this repo or a chat) |
| Uploaded pictures | `~/postiz-local/uploads/` |
| Public API | `http://localhost:4007/api/public/v1`, header `Authorization: <API key>` |

Always open it as `http://localhost:4007`, not `127.0.0.1`, and use Chrome or
Firefox. Safari will not keep you logged in on a plain `http://localhost` site.

It is six containers: `postiz` (the app), `postiz-postgres`, `postiz-redis`, and
Temporal (`temporal`, `temporal-postgresql`, `temporal-elasticsearch`), which
Postiz uses to send posts at their scheduled time. Together about 2.2 GB of
memory.

## Everyday commands

Run these in Terminal. All of them start with `cd ~/postiz-local`.

```bash
cd ~/postiz-local

docker compose ps            # is it running? every line should say "healthy"
docker compose up -d         # start (also: apply a change made in .env)
docker compose down          # stop (data is kept)
docker compose logs -f postiz   # watch the app's log, Ctrl-C to leave
```

It starts by itself when Docker Desktop starts, so it comes back after a
reboot. After `docker compose down` it stays off until `docker compose up -d`.

To open the settings file: `open -e ~/postiz-local/.env`

## First time: account, lock sign-up, API key

1. Open <http://localhost:4007>, fill in the sign-up form. No email is sent;
   the account works straight away.
2. Close sign-up so nobody else can register: in `.env` change
   `DISABLE_REGISTRATION=false` to `DISABLE_REGISTRATION=true`, then
   `cd ~/postiz-local && docker compose up -d`.
3. API key (for Social Autopilot): **Settings → Developers → API Key**. It can
   be rotated there if it ever leaks.

## Adding a channel

In Postiz: **Calendar → Add Channel** (left side), pick the platform.

### Platforms where you type the details into Postiz

Nothing to set up on the server. Use an app password where one exists, never
the main account password.

| Platform | What Postiz asks for | Where to get it |
| - | - | - |
| Bluesky | Service (leave `https://bsky.social`), handle, app password | Bluesky → Settings → Privacy and security → App passwords |
| Lemmy | Instance address (e.g. `https://lemmy.world`), username, password | Your Lemmy account |
| Nostr | Private key, in hex form (not the `nsec…` form) | Your Nostr app's key backup |
| Medium | API key ("integration token") | Medium → Settings → Security and apps. Medium has stopped giving out new tokens, so this only works with a token the account already has |
| Dev.to | API key | dev.to → Settings → Extensions → DEV Community API Keys |
| Hashnode | API key ("personal access token") | Hashnode → Account settings → Developer |
| WordPress | Site address with `https://`, username, application password | WordPress admin → Users → Profile → Application Passwords |
| Listmonk | Address, API username, API password/token | Listmonk admin → Users (create an API user) |
| Moltbook | Nothing; follow the steps in the dialog | |

### Platforms that need an app registration first

For each one: create the app on the platform, put the ID and secret into
`~/postiz-local/.env` (the empty lines are already there), run
`cd ~/postiz-local && docker compose up -d`, then use **Add Channel**.

Postiz shows these platforms in the list even with no keys; clicking one
without keys simply fails.

**Discord** — accepts the plain `http://localhost` address.
1. <https://discord.com/developers/applications> → New Application. You need
   "Manage Server" on the Discord server you want to post to.
2. Give the app an icon (Postiz fails without one).
3. OAuth2 page: copy Client ID and Client Secret. Add the redirect
   `http://localhost:4007/integrations/social/discord`
4. Bot page: Reset Token and copy it.
5. `.env`: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN_ID`.
   Postiz asks Discord for: `bot`, `identify`, `guilds`.

**Slack** — Slack only accepts `https` addresses, so Postiz sends the return
trip through a public forwarding site, redirectmeto.com. That site sees the
one-time sign-in code as it passes.
1. <https://api.slack.com/apps> → Create New App → From scratch.
2. OAuth & Permissions → Redirect URLs, add exactly
   `https://redirectmeto.com/http://localhost:4007/integrations/social/slack`
3. Same page, Bot Token Scopes: `channels:read`, `chat:write`, `users:read`,
   `groups:read`, `channels:join`, `chat:write.customize` (all six).
4. Basic Information → Display Information: add a square icon, 512 px or more.
5. `.env`: `SLACK_ID` (Client ID), `SLACK_SECRET` (Client Secret).

**Reddit** — accepts the plain `http://localhost` address.
1. <https://www.reddit.com/prefs/apps> → create app, type **web app**, redirect
   `http://localhost:4007/integrations/social/reddit`
2. `.env`: `REDDIT_CLIENT_ID` (the string under the app name),
   `REDDIT_CLIENT_SECRET`. Postiz asks for: `read`, `identity`, `submit`,
   `flair`.
3. Reddit now wants new apps approved before they may use its API. If creating
   the app is refused or posting is blocked, that approval is the reason.

**LinkedIn** — the slow one. Needs a LinkedIn company Page and LinkedIn's
approval.
1. <https://www.linkedin.com/developers/apps> → Create app (it must be linked
   to a LinkedIn Page), then verify the app.
2. Products: add "Share on LinkedIn", "Sign In with LinkedIn using OpenID
   Connect" and "Advertising API". The last one is a request form that LinkedIn
   reviews; without it Postiz refuses the connection.
3. Auth tab: add both redirects
   `http://localhost:4007/integrations/social/linkedin` and
   `http://localhost:4007/integrations/social/linkedin-page`.
   LinkedIn's rules say `https`; its form normally accepts `http://localhost`.
   If it does not, LinkedIn cannot be used on this local server.
4. The Auth tab must list all of: `openid`, `profile`, `w_member_social`,
   `r_basicprofile`, `rw_organization_admin`, `w_organization_social`,
   `r_organization_social`.
5. `.env`: `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` (one app serves both
   "LinkedIn" and "LinkedIn Page").

**Mastodon** — accepts the plain `http://localhost` address. No website form:
the app is registered with one command. Change `mastodon.social` if the account
lives on another server.

```bash
curl -sS -X POST https://mastodon.social/api/v1/apps \
  -F "client_name=Bio Luxe Lab Postiz" \
  -F "redirect_uris=http://localhost:4007/integrations/social/mastodon" \
  -F "scopes=write:statuses write:media profile"
```

Copy `client_id` and `client_secret` from the answer into `.env` as
`MASTODON_CLIENT_ID` and `MASTODON_CLIENT_SECRET`, and set `MASTODON_URL` to the
server (no slash at the end).

**Dribbble** — its form takes an `http://localhost` address (Dribbble's rules
do not ask for `https`).
1. <https://dribbble.com/account/applications/new>, callback URL
   `http://localhost:4007/integrations/social/dribbble`
2. `.env`: `DRIBBBLE_CLIENT_ID`, `DRIBBBLE_CLIENT_SECRET`. Postiz asks for:
   `public`, `upload`.
3. Spelling: three b's everywhere. The Postiz help page for Dribbble writes it
   with two; that is a typo there.

## Posts with pictures

Text posts work as set up. Pictures need care on a local-only server:

- A picture uploaded into this Postiz gets an address starting with
  `http://localhost:4007/uploads/…`. Only this Mac can open that, and Postiz
  itself refuses to fetch `localhost` addresses when it sends the post. So
  posts with an uploaded picture are expected to fail.
- Slack, Nostr, Dev.to, Hashnode and Listmonk pass the picture's address on to
  the platform, so for them the picture must be on the public internet in any
  case.
- What should work instead (read from Postiz's code, not yet tried with a real
  post): give Postiz a picture that is already public, such as the shop's own
  image address (`https://cdn.shopify.com/….jpg`). Through the API that is the
  `path` of the image; it must end in `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
  or `.mp4`.
- The other way is to keep uploads in a public store (Cloudflare R2, which
  Postiz supports) instead of on this Mac. That needs a Cloudflare account.

## Update to a newer version

1. Back up first (next section).
2. Look at the release notes: <https://github.com/gitroomhq/postiz-app/releases>
   and at the official compose file for changes:
   <https://github.com/gitroomhq/postiz-docker-compose>
3. In `~/postiz-local/docker-compose.yaml` change the version in the line
   `image: ghcr.io/gitroomhq/postiz-app:v2.25.3`.
4. Check the new image still starts the same way (it should print
   `["sh","-c","nginx && pnpm run pm2"]`); if not, change the `command:` line
   in the compose file to match:
   ```bash
   docker pull ghcr.io/gitroomhq/postiz-app:vNEW
   docker inspect ghcr.io/gitroomhq/postiz-app:vNEW --format '{{json .Config.Cmd}}'
   ```
5. `cd ~/postiz-local && docker compose up -d`, then `docker compose ps`.

## Backup and restore

```bash
cd ~/postiz-local
docker exec postiz-postgres pg_dump --clean --if-exists -U postiz-user -d postiz-db-local | gzip > backups/postiz-db-$(date +%F).sql.gz
docker exec temporal-postgresql pg_dumpall -U temporal | gzip > backups/temporal-db-$(date +%F).sql.gz
tar -czf backups/uploads-$(date +%F).tar.gz uploads
```

A backup is only usable together with `~/postiz-local/.env`: the saved channel
logins are locked with the secret in that file. Keep a private copy of `.env`
with the backups (not in this repo).

Restore the Postiz database (this replaces what is in it now):

```bash
cd ~/postiz-local
docker compose stop postiz
gunzip -c backups/postiz-db-DATE.sql.gz | docker exec -i postiz-postgres psql -q -v ON_ERROR_STOP=1 -U postiz-user -d postiz-db-local
docker compose up -d
```

Pictures: `tar -xzf backups/uploads-DATE.tar.gz` in `~/postiz-local`.

## Extras

- Temporal's own screen (to see scheduled jobs), off by default:
  `cd ~/postiz-local && docker compose --profile tools up -d`, then
  <http://localhost:8080> (this Mac only). Turn off again with
  `docker compose --profile tools rm -sf temporal-ui temporal-admin-tools`.
- The API allows 30 "create post" calls an hour (`API_LIMIT` in
  `docker-compose.yaml`).
- Remove everything, data included: `cd ~/postiz-local && docker compose down -v`
  (cannot be undone).
