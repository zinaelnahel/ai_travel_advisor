# Model comparison

Same prompts, temperature 0.3, 3,500 output-token cap. No live tools or images, so each model sees identical data.
Latency is wall-clock request time, not time to first token. USD cost uses API-reported usage and configured rates.

| Model | Successful trials | Median latency (s) | Mean cost per successful trial (USD) |
|---|---:|---:|---:|
| gpt-4o-mini | 3 | 6.49 | 0.000516 |
| gpt-4.1-mini | 3 | 6.83 | 0.001430 |
| gpt-4.1 | 3 | 7.02 | 0.008203 |

Format checks are structural diagnostics, not quality scores. Review saved responses using review.csv; scores are intentionally blank until human review.
Score 1-5 for constraint adherence, geographic coherence, practical pacing, budget credibility and uncertainty transparency. Blind the model names when reviewing; use multiple repetitions before relying on latency differences.

Default: gpt-4o-mini for conversation and summaries, gpt-4.1-mini for itineraries. This is a provisional cost-conscious configuration, not an empirical quality winner.
Use gpt-4.1 only if blind review shows a meaningful gain on difficult constraints and that gain warrants measured extra cost/latency. Otherwise retain the mini planner; if gpt-4o-mini meets the same quality bar, use it for both tasks.

A completed agent review with specific constraint failures and official-source checks is available in [quality_review.md](quality_review.md). This baseline used the prompt before the additional venue-eligibility and hard-constraint instructions were added.
