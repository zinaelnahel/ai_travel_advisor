# SafarAI

SafarAI uses a stateless FastAPI backend with a responsive browser interface.
Each request creates a fresh advisor. The browser sends its signed trip state
with each request; the server keeps no sessions, conversations, image cache,
or user files in memory between requests.

## Run locally

```powershell
python -m pip install -r web/requirements.txt
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
   build/output settings. Do not add the auto-detected `web` Flask service.
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

`GET /health` is public. `POST /api/chat`, `/api/image`, and `/api/reset`
accept JSON and require `X-Travel-Request: 1`. When a password is configured,
send HTTP Basic authentication. Browser requests must come from the same origin.

Start chat with `{"message":"Three days in Lisbon, interested in food"}`.
Every successful action returns `state`; pass it unchanged as `state` in the
next JSON request. Chat also returns sanitized `reply`, optional `itinerary`,
`itinerary_html`, `destination`, and `usage_html`. Image generation requires a
state containing an itinerary. Reset returns an empty signed trip state.

Tokens expire after one hour of inactivity. State is signed, not encrypted:
it contains the conversation and usage but never the API key. The page keeps it
in memory; refreshing starts a new trip. Images remain in the page and are not
cached on the server. API clients should keep requests sequential and retain
an image locally to avoid paying for duplicate generation.

Stateless tokens can be replayed or discarded. Conversation budgets are an
estimate for the supplied state, not account-wide billing limits or replay
protection. Configure provider spending limits and deployment access controls
as appropriate. Long model/tool chains can exceed the function duration, and
large generated images can exceed Vercel's response-size limit; failed requests
may still incur provider costs. Rotating the signing secret invalidates old trips.

## Other interfaces

The Streamlit interface, PDFs, and notebooks remain available. Install their
separate dependencies before using them:

```powershell
python -m pip install -r requirements-streamlit.txt
python -m streamlit run streamlit_app.py
```

The Dockerfile runs Streamlit on port 8501. Root API requirements intentionally
do not install Streamlit or Jupyter dependencies. For Streamlit Cloud, configure
a separate app source/dependency setup using `requirements-streamlit.txt`.
Notebooks use `advisor.py`; model comparisons and live checks incur API charges.

## Validation

```powershell
python -m pip install -r web/requirements.txt -r requirements-streamlit.txt
python -m unittest discover -s tests -v
```

Tests cover stateless continuity across new app instances, isolation, state
signatures/expiry, itinerary/image/reset flows, authentication, request limits,
origin checks, provider failures, and the legacy Streamlit/PDF interface.
