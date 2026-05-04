# Devin Chat Aggregator

A self-hosted website that puts **all your Devin accounts** behind a single
login and stores every chat in your own database, so switching between Devin
accounts never loses context.

It is a thin proxy around the [Devin v1 REST API](https://docs.devin.ai/api-reference/v1/overview):

- You log into the site with a single password (`SITE_PASSWORD`).
- You add one or more Devin **API keys** (one per Devin account you own).
- Every chat you start picks one of those keys and forwards messages to Devin.
- All messages — yours and Devin's — are persisted in a local SQLite DB.
- One-click **Export to GitHub** writes the chat as Markdown into any of your repos.

The site is small enough to fit comfortably inside the Fly.io free tier
(`shared-cpu-1x` / 256 MB / 3 GB volume).

## Features

- Single-password login, signed cookie session.
- Multi-account support: add many Devin API keys, mark one as default,
  pick which account a new chat uses.
- New chat / continue chat / refresh status (polls `/v1/session/{id}` while
  the agent is working).
- Image and file attachments — uploaded through `/v1/attachments`, then
  referenced inline in the prompt.
- Optional model hint ("Claude Sonnet 4.5", "GPT-5", etc.) injected into the
  prompt header. The Devin v1 API does not yet expose a model parameter; this
  hint is the closest equivalent.
- Markdown export of any chat to a GitHub repo via the Contents API.
- SQLite database on a persistent volume — chats survive restarts and deploys.

## Quick start (local)

Requires Python 3.11+.

```bash
cd Forex-wws27
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set SITE_PASSWORD and SECRET_KEY

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Open <http://localhost:8080>, log in, then go to **Settings → Devin
accounts** and paste an API key from
<https://app.devin.ai/settings/api-keys>.

## Deploy to Fly.io (free)

1. Install [`flyctl`](https://fly.io/docs/hands-on/install-flyctl/) and run `fly auth login`.
2. From the repo root:

```bash
# pick a unique app name; "devin-chat-aggregator" probably won't be free
fly launch --no-deploy --copy-config --name <your-app-name>

# create the persistent volume (3 GB max on free tier)
fly volumes create dca_data --region ams --size 1

# set required secrets
fly secrets set \
  SITE_PASSWORD='<a strong password>' \
  SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"

# optional, for the "Export to GitHub" feature
fly secrets set GITHUB_TOKEN='ghp_...' GITHUB_DEFAULT_REPO='Jony-wws/Forex-wws27'

fly deploy
```

After the first successful deploy, open `https://<your-app-name>.fly.dev`,
log in, and add your Devin API keys.

## API surface

All `/api/*` endpoints require a logged-in cookie session and return JSON.

| method | path | purpose |
|---|---|---|
| `GET` | `/api/me` | session check |
| `GET/POST/DELETE` | `/api/accounts[...]` | manage Devin API keys |
| `POST` | `/api/accounts/{id}/default` | mark default account |
| `GET` | `/api/chats` | list chats |
| `POST` | `/api/chats` | create chat (proxies `POST /v1/sessions`) |
| `GET` | `/api/chats/{id}` | fetch one chat with messages |
| `POST` | `/api/chats/{id}/messages` | send a follow-up message |
| `POST` | `/api/chats/{id}/refresh` | poll Devin and ingest new events |
| `DELETE` | `/api/chats/{id}` | delete chat (local only) |
| `POST` | `/api/chats/{id}/attachments` | upload via `/v1/attachments` |
| `POST` | `/api/standalone/attachments` | upload before a chat exists |
| `POST` | `/api/chats/{id}/export-github` | commit chat as Markdown |
| `GET` | `/api/config` | non-secret config snapshot |

## Environment variables

| name | required | description |
|---|---|---|
| `SITE_PASSWORD` | yes | password for the single site user |
| `SECRET_KEY` | yes | random string used to sign session cookies |
| `DATA_DIR` | no | directory for SQLite DB. Default `./data`, set to `/data` on Fly |
| `DEVIN_API_BASE` | no | override Devin API base URL |
| `GITHUB_TOKEN` | no | token used by the "Export to GitHub" button |
| `GITHUB_DEFAULT_REPO` | no | default `owner/repo` for export |

## Why this exists

Devin is account-scoped: every chat is tied to the account you started it on.
If you have several Devin accounts and switch between them, you lose visibility
into what was done elsewhere. This site is a thin layer that gives you a single
chat history regardless of which Devin account is doing the work — and lets
you push that history into any of your repos for permanent storage.

## Limitations

- **Model selection** in the v1 API is not yet a first-class parameter; the
  site embeds a hint string at the top of the prompt and relies on Devin to
  honour it.
- **Agent attribution.** Each chat is forever bound to the Devin account whose
  API key created it; only the *site* user is unified.
- **Single user.** No user management — one password, one inbox. Run a
  separate instance per person.
