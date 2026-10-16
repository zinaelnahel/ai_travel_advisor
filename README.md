# SafarAI

An AI travel advisor that turns a conversation into a personalized itinerary.
Tell it where you want to go, how long you are staying, and what you enjoy.

## Run locally

You need Python 3.12 and an OpenAI API key with available credit.

1. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Edit `.env`. Set `OPENAI_API_KEY` and `STATE_SIGNING_SECRET`.
   Generate a signing secret with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Start the app:

   ```powershell
   python -m web.app
   ```

5. Open **http://127.0.0.1:8000**.

## What it does

- Asks for missing trip details and remembers the conversation.
- Creates and revises daily plans with tips and group/per-person budgets.
- Searches the web for venue prices and locations, with source links.
- Looks up weather and exchange rates when needed.
- Restores trips and unfinished messages after refreshing the same tab.
- Offers optional destination images and **Print / Save PDF**.
- Shows estimated token usage and API costs.

Trips expire after one hour of inactivity. Closing the tab may clear saved data.
Images are not restored. For PDF export, choose **Save as PDF** in the print dialog.

## Settings

Set these in `.env` locally or in Vercel's environment variables:

| Setting | Purpose |
|---|---|
| `OPENAI_API_KEY` | Required OpenAI API key |
| `STATE_SIGNING_SECRET` | Required random secret, at least 32 characters |
| `APP_PASSWORD` | Optional password; use it before sharing the app |
| `CONVERSATION_BUDGET_USD` | Estimated AI allowance per trip; default `1.0` |
| `WEB_SEARCH_ENABLED` | `1` to research venues; `0` to skip web research |

OpenAI chat, web search and images cost money. The trip allowance is an estimate,
not an account-wide spending limit. Keep `.env` and API keys out of Git.
Prices, routes and accessibility still need confirmation before booking.
Meal quantities are calculated from travelers and days. Shared costs and
per-person prices are kept separate; the summary uses the calculated totals.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

These tests use mocked responses and do not make paid API calls.

## Deploy

Import this GitHub repository into Vercel, add the settings above, and deploy.
The repository already contains the FastAPI deployment configuration.
Once GitHub is connected, pushing to `main` triggers a deployment.

Use `/health` to check the server and `/docs` to try the API.
If a request fails, its reference ID can be found in Vercel Runtime Logs.

## Code tour

```text
safarai/       AI conversation, research, budgets and planning prompt
web/           API and browser interface
tests/         Offline regression tests
experiments/   Optional notebooks, model comparisons and saved results
docs/          Security analysis
```

See [the security review](docs/SECURITY.md) for limitations.

The conversation uses `gpt-4o-mini`; planning and research use `gpt-4.1-mini`.
Long conversations are summarized automatically. Images are generated on request.
Model comparisons and live-check notebooks incur API charges when run.
