# SafarAI

SafarAI uses a stateless FastAPI backend with a responsive browser interface.
Each request creates a fresh advisor. The browser sends its signed trip state
with each request; the server keeps no sessions, conversations, image cache,
or user files in memory between requests.

## Project layout

| Path | Purpose |
|---|---|
| `app.py` | Vercel and Docker entry point |
| `advisor.py` | Conversation, tool calls, summaries, images, and usage tracking |
| `itinerary_prompt.txt` | Itinerary instructions and Markdown format |
| `web/app.py` | FastAPI routes, authentication, and signed trip state |
| `web/templates/`, `web/static/` | Browser interface and assets |
| `tests/` | Offline tests |
| `*.ipynb` | Coursework interface, experiments, and test runner |
| `model_comparison.py`, `model_comparison/` | Benchmark code and saved results |
| `integration_smoke/` | Saved live-check results |
| `SECURITY.md` | Security review and known limitations |

Install `requirements.txt` to run the app, or `requirements-dev.txt` for tests
and notebooks. Deployment settings live in `vercel.json`, `pyproject.toml`,
and `Dockerfile`.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and put the generated value
in `STATE_SIGNING_SECRET`. Then run:

```powershell
python -m web.app
```

Open http://127.0.0.1:8000. Set `APP_PASSWORD` to protect a private deployment.

## Deploy to Vercel

The root `app.py` exports the FastAPI application using Vercel's supported
entrypoint. `vercel.json` explicitly declares one FastAPI service named `app`,
routes all public paths to it, and allows up to 300 seconds per function invocation.
The root `app.py` imports `web.app`; these are two entry points for the same
application, not independent services. No internal service calls or bindings
are needed. Browser requests keep their existing `/api/` paths.
Root `requirements.txt` contains only API dependencies, and `pyproject.toml`
selects Python 3.12.

1. Push these changes to `zinaelnahel/ai_travel_advisor`.
2. Import that repository in Vercel, with the repository root as Root Directory.
   Import the Services configuration from `vercel.json` and keep default
   build/output settings. The application uses one service named `app`.
3. Add server environment variables: `OPENAI_API_KEY`, `STATE_SIGNING_SECRET`
   (a random value of at least 32 characters), `APP_PASSWORD` for private access,
   and optionally `CONVERSATION_BUDGET_USD` (default `1.0`). Use the same signing
   secret across instances of a deployment. Never put secrets in Git or browser code.
4. Deploy. Verify `https://<your-project>.vercel.app/health` returns
   `{"status":"ok"}`, then open `/` and test chat.

With a logged-in Vercel CLI, deploy from this directory with `vercel --prod`.
See [Vercel's FastAPI documentation](https://vercel.com/docs/frameworks/backend/fastapi).
Deployment requires Vercel account/project access; adding configuration alone
does not publish the app.

## API contract and state

Open `/docs` on the deployed domain (or locally) for Swagger UI and `/openapi.json`
for the schema. Swagger sends the required request header automatically. If
`APP_PASSWORD` is configured, sign in using the browser's Basic authentication
prompt. Choose an action and matching JSON example, then use **Try it out**.
Pass the returned `state` into subsequent requests. Swagger assets load from
jsDelivr; the relaxed asset policy applies only to `/docs`.

`GET /health` is public. `POST /api/chat`, `/api/image`, and `/api/reset`
accept JSON and require `X-Travel-Request: 1`. When a password is configured,
send HTTP Basic authentication. Browser requests must come from the same origin.

Start chat with `{"message":"Three days in Lisbon, interested in food"}`.
Every successful action returns `state`; pass it unchanged as `state` in the
next JSON request. Chat also returns sanitized `reply`, optional `itinerary`,
`itinerary_html`, `destination`, and `usage_html`. Image generation requires a
state containing an itinerary. Reset returns an empty signed trip state.

Tokens expire after one hour of inactivity. State is signed, not encrypted:
it contains the conversation and usage but never the API key. The page stores the signed token in browser session storage so a refresh in the
same tab restores the trip. The server verifies its signature and one-hour expiry
before rendering saved content. New trip removes the saved token. Closing the
tab ends the browser session; generated images are not restored. Images remain in the page and are not
cached on the server. API clients should keep requests sequential and retain
an image locally to avoid paying for duplicate generation.

Stateless tokens can be replayed or discarded. Conversation budgets are an
estimate for the supplied state, not account-wide billing limits or replay
protection. Configure provider spending limits and deployment access controls
as appropriate. Long model/tool chains can exceed the function duration, and
large generated images can exceed Vercel's response-size limit; failed requests
may still incur provider costs. Rotating the signing secret invalidates old trips.

## Notebooks and coursework

The notebooks, model comparison reports, and saved integration results remain
as coursework evidence. Install the development dependencies to run notebooks:

```powershell
python -m pip install -r requirements-dev.txt
```

Open `ai_travel_advisor.ipynb` for the notebook interface. Notebooks import
`advisor.py` directly; `compare_models.ipynb` uses `model_comparison.py`.
Model comparisons and live checks incur API charges when explicitly enabled.
The browser interface exports PDFs through the browser's print dialog.
The Dockerfile runs the same FastAPI application on port 8000.

## Validation

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests cover stateless continuity across new app instances, isolation, state
signatures/expiry, itinerary/image/reset flows, authentication, request limits,
origin checks, provider failures, advisor orchestration, and notebook controls.

## Public data APIs

The interface focuses on travel chat and itineraries. The separate destination
lookup and currency panel has been removed. The AI advisor can still use its
weather and currency tools during conversation.

The keyless GET endpoints /api/places?name=Lisbon,
/api/forecast?latitude=38.7&longitude=-9.1 and /api/rate?currency=USD remain
available to API clients. They validate inputs, use fixed HTTPS providers,
disable redirects, and apply a 10-second timeout. App password protection applies.
Open-Meteo's free API is for noncommercial use and requires attribution:
https://open-meteo.com/en/terms . Frankfurter reference rates are free and keyless:
https://frankfurter.dev/ . Forecasts cover the upcoming 16 days, and exchange
rates do not include bank or card fees.

## Output checks and trip restoration

Estimated-budget tables are parsed with Decimal arithmetic. Category sums and
range bounds are calculated in code, and model totals and extra budget prose
are replaced. Unsupported or ambiguous formats hide the budget total. All
accepted rows are defined as per person for the whole trip; the estimates
themselves remain model-generated, not verified prices.

A conservative keyword filter removes model-written weather lines. Exact dated
provider observations are rendered separately; there is no long-range forecast.
This filter is not a general semantic fact checker and may remove useful text.
Routing has no verification API: prompts avoid uncertain restaurant locations,
common precise walking-time/nearby claims are qualified, and plans disclose
that routes and accessibility require confirmation. No route is certified.

POST /api/restore verifies the signed state and renders saved messages and the
itinerary without a model call. Session storage survives refresh in the same
tab, not guaranteed across closed tabs or cleared browser storage. It contains
conversation data, so New trip clears it. PDF export opens the browser print
dialog; select Save as PDF there.
