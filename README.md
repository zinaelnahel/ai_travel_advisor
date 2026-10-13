# AI Travel Advisor

Open `ai_travel_advisor.ipynb` in Jupyter or VS Code and run its cells from top
to bottom. Start Jupyter in this project folder so the notebooks can find each
other. Put your API key in `.env` as `OPENAI_API_KEY=your-key`.

The main notebook installs the packages, loads the travel functions, sets up
the advisor, and displays the chat. You can change the models and conversation
budget in its setup cell.

Supporting notebooks:

- `advisor.ipynb`: travel tools, conversation memory, itinerary planning, and images.
- `notebook_ui.ipynb`: chat text box and buttons; loaded by the main notebook.
- `compare_models.ipynb`: optional model comparison. Set `run_comparison = True` to run it.
- `smoke_test_live.ipynb`: optional live checks. Set `run_live_checks = True` to run them.
- `test_advisor.ipynb`: 15 offline checks using fake responses; no API charges.

Comparison and live checks make paid API calls when enabled. Notebook outputs
are cleared in the saved files. Keep your key private and clear personal chat
outputs before sharing a notebook.
