"""Compare travel plans across models using the same prompts."""
import csv
import json
import random
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from advisor import MODEL_RATES, SECURITY_PROMPT, TravelAdvisor
from travel_validation import structured_budget


CASES = [
    {"id": "lisbon", "days": 3, "prompt": "Plan 3 days in Lisbon for one person interested in food and architecture. Moderate budget, no departure city known."},
    {"id": "accessibility", "days": 2, "prompt": "Plan 2 days in Berlin for a wheelchair user interested in museums and vegetarian food. Budget EUR 400 excluding flights. Avoid stairs; flag accessibility details to verify."},
    {"id": "family", "days": 4, "prompt": "Plan 4 days in Kyoto for a family with two children aged 5 and 8. Interests: gardens and food. Slow pace, midday rest, no nightlife. Estimate per-person costs in JPY; flights excluded."},
]

def format_checks(text, days):
    headings = ["## Trip summary", "## Assumptions", "## Day-by-day itinerary", "## Practical travel tips", "## Estimated budget"]
    positions = [text.find(h) for h in headings]
    numbered_days = [int(n) for n in re.findall(r"^### Day (\d+):", text, re.M)]
    return {
        "sections_in_order": all(p >= 0 for p in positions) and positions == sorted(positions),
        "correct_day_count": numbered_days == list(range(1, days + 1)),
        "budget_table": bool(re.search(r"\|\s*Category\s*\|\s*Estimated cost\s*\|", text)),
    }

def compare(api_key, repeats=1, output_dir="model_comparison", transport=None):
    if not 1 <= repeats <= 5:
        raise ValueError("Use 1-5 repetitions.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    kwargs = {} if transport is None else {"transport": transport}
    advisor = TravelAdvisor(api_key, budget_usd=1.0, **kwargs)
    (output / 'run_metadata.json').write_text(json.dumps({
        'started_at': datetime.now(timezone.utc).isoformat(), 'repeats': repeats,
        'temperature': 0.3, 'max_output_tokens': 3500, 'model_rates': MODEL_RATES,
        'system_prompt': SECURITY_PROMPT + advisor.itinerary_prompt, 'cases': CASES,
        'tools_enabled': False, 'images_enabled': False,
    }, indent=2), encoding='utf-8')
    tasks = [(model, case, repeat) for repeat in range(repeats) for case in CASES for model in MODEL_RATES]
    random.Random(42).shuffle(tasks)
    rows = []
    for model, case, repeat in tasks:
        print(f"Comparing {model}: {case['id']} (trial {repeat + 1})", flush=True)
        started = time.perf_counter()
        count_before = len(advisor.metrics)
        row = {"model": model, "case": case["id"], "trial": repeat + 1, "success": False}
        try:
            message = advisor.chat(model, [{"role": "system", "content": SECURITY_PROMPT + advisor.itinerary_prompt},
                                         {"role": "user", "content": case["prompt"]}], "benchmark", max_tokens=3500)
            text = advisor.reply_text(message)
            if '```budget-json' in text:
                text = structured_budget(text, {})
            file = f"{case['id']}_{model}_{repeat + 1}.md"
            (output / file).write_text(text, encoding="utf-8")
            row.update(success=True, response_file=file, **format_checks(text, case["days"]))
        except (RuntimeError, ValueError, KeyError, TypeError, ArithmeticError) as error:
            row["error"] = str(error)
            row['error_code'] = getattr(error, 'code', None)
        row["latency_s"] = time.perf_counter() - started
        row["cost_usd"] = sum(m["cost_usd"] for m in advisor.metrics[count_before:])
        row["input_tokens"] = sum(m["input"] for m in advisor.metrics[count_before:])
        row["output_tokens"] = sum(m["output"] for m in advisor.metrics[count_before:])
        rows.append(row)
        (output / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        # Stop global auth/quota failures rather than make nine identical requests.
        if row.get('error_code') in ('provider_authentication', 'provider_access', 'provider_quota', 'provider_rate_limit'):
            break
    report = ["# Model comparison", "", "Same prompts, temperature 0.3, 3,500 output-token cap. No live tools or images, so each model sees identical data.",
              "Latency is wall-clock request time, not time to first token. USD cost uses API-reported usage and configured rates.", "",
              "| Model | Successful trials | Median latency (s) | Mean cost per successful trial (USD) |", "|---|---:|---:|---:|"]
    for model in MODEL_RATES:
        valid = [r for r in rows if r["model"] == model and r["success"]]
        if valid:
            report.append(f"| {model} | {len(valid)} | {statistics.median(r['latency_s'] for r in valid):.2f} | {statistics.mean(r['cost_usd'] for r in valid):.6f} |")
        else:
            report.append(f"| {model} | 0 | unavailable | unavailable |")
    report += ["", "Format checks are structural diagnostics, not quality scores. Review saved responses using review.csv; scores are intentionally blank until human review.",
               "Score 1-5 for constraint adherence, geographic coherence, practical pacing, budget credibility and uncertainty transparency. Blind the model names when reviewing; use multiple repetitions before relying on latency differences.",
               "", "Default: gpt-4o-mini for conversation and summaries, gpt-4.1-mini for itineraries. This is a provisional cost-conscious configuration, not an empirical quality winner.",
               "Use gpt-4.1 only if blind review shows a meaningful gain on difficult constraints and that gain warrants measured extra cost/latency. Otherwise retain the mini planner; if gpt-4o-mini meets the same quality bar, use it for both tasks."]
    (output / "report.md").write_text('\n'.join(report) + '\n', encoding="utf-8")
    with (output / "review.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["response_file", "constraint_adherence", "geographic_coherence", "practical_pacing", "budget_credibility", "uncertainty_transparency", "notes"])
        writer.writeheader()
        writer.writerows({"response_file": r["response_file"]} for r in rows if r["success"])
    return rows
