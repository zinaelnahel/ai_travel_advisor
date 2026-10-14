# SafarAI

## Streamlit app (recommended)

SafarAI runs on desktop and phone with a colorful exploration theme, chat,
a separate itinerary, actual PDF downloads, and optional destination images.
Panels stack on small screens. Images are generated only when requested.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

Open the local URL printed in your terminal. Your existing `.env` works locally.

### Deploy on Streamlit Community Cloud

1. Push the project to `zinaelnahel/ai_travel_advisor` on GitHub.
2. Sign in at https://share.streamlit.io and select **Create app**.
3. Choose repository **zinaelnahel/ai_travel_advisor**, branch **main**,
   and main file **streamlit_app.py**. Select Python **3.12** in Advanced settings.
4. In **Advanced settings > Secrets**, paste your private values:

```toml
OPENAI_API_KEY = "your-actual-key"
APP_PASSWORD = "your-private-app-password"
CONVERSATION_BUDGET_USD = "1.0"
```

5. Click **Deploy**, then open the provided `streamlit.app` HTTPS URL on your
   phone. Use your app password when prompted. Keep API keys in Secrets,
   never in GitHub. `.streamlit/secrets.toml.example` is a placeholder template.

[Official deployment instructions](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
and [Secrets management](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).

Community Cloud hosting does not pay for OpenAI requests. The app still uses
paid API calls. Conversations last for the current browser session and can be
lost on reload/disconnection or server restart. The optional shared password
is for a private demo; conversation budgets reset when a new trip starts.

### Alternative Flask interface

The existing Flask frontend is still available locally:

```powershell
python -m pip install -r web/requirements.txt
python -m web.app
```

Open http://127.0.0.1:8000. The Dockerfile now runs the recommended Streamlit
app on port 8501. Inject OPENAI_API_KEY and APP_PASSWORD as private environment
variables when running a container. Streamlit Cloud builds from requirements.txt
and does not use the Dockerfile.

## Code layout

- `streamlit_app.py`: main Streamlit interface and per-browser state.
- `streamlit_style.html`: playful, responsive theme.
- `pdf_export.py`: downloadable PDF itineraries.
- `advisor.py`: shared AI and travel logic.
- `web/app.py`: browser sessions and the three API actions.
- `web/templates/index.html`: page structure.
- `web/static/style.css`: responsive styling.
- `web/static/app.js`: chat, reset, image, and PDF controls.
- `web/static/exploration.svg`: the travel illustration.

## Notebooks

Open `ai_travel_advisor.ipynb` in Jupyter or VS Code and run its cells from top
to bottom. Start Jupyter in this project folder so the notebooks can find each
other. Put your API key in `.env` as `OPENAI_API_KEY=your-key`.

The main notebook installs the packages, loads the travel functions, sets up
the advisor, and displays the chat. You can change the models and conversation
budget in its setup cell.

Supporting notebooks:

- `advisor.py`: shared travel tools, memory, planning, and images.
- `advisor.ipynb`: a one-cell loader for the shared Python file.
- `notebook_ui.ipynb`: chat text box and buttons; loaded by the main notebook.
- `compare_models.ipynb`: optional model comparison. Set `run_comparison = True` to run it.
- `smoke_test_live.ipynb`: optional live checks. Set `run_live_checks = True` to run them.
- `test_advisor.ipynb`: 16 offline checks using fake responses; no API charges.

Comparison and live checks make paid API calls when enabled. Notebook outputs
are cleared in the saved files. Keep your key private and clear personal chat
outputs before sharing a notebook.
