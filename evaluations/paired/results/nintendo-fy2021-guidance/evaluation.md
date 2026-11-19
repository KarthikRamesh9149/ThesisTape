# Nintendo FY2021 guidance · first paid development event

**0 of 6 configurations released a memo.** The six registered attempts ran on 20 November 2026 (Sydney), using Nintendo’s 7 May 2020 financial statements and a cutoff of 8 May at noon UTC. Five stopped at a numerical evidence check. Single Astra stopped when its remaining allowance could not cover a repair. All 74 generation receipts reconciled to **USD 0.4811300**, with USD 0 outstanding against the combined USD 1.20 cap. [Publisher filing](https://www.nintendo.co.jp/ir/pdf/2020/200507e.pdf) · [Machine-readable results](results.json)

| Configuration in registered order | Generations | Estimated USD from receipts | Wall time | Final decision |
| --- | ---: | ---: | ---: | --- |
| [Single Sol](single_sol/run.json) | 7 | 0.0633260 | 55.280 s | Draft check rejected the day in the cutoff date |
| [All-Sol team](sol_team/run.json) | 18 | 0.1060280 | 66.397 s | Forecast check rejected the question’s 2% band |
| [Mixed team](mixed/run.json) | 17 | 0.0578850 | 63.525 s | Forecast check rejected the question’s 2% band |
| [Single Astra](single_astra/run.json) | 5 | 0.1416160 | 37.050 s | Remaining funds could not cover the writer’s repair |
| [Mixed with fixed plan](mixed_fixed_plan/run.json) | 10 | 0.0357632 | 42.558 s | Forecast check rejected the question’s 2% band |
| [Mixed without forecast specialist](mixed_no_forecast/run.json) | 17 | 0.0765118 | 78.645 s | Draft check rejected the page number of a verified citation |

Under the frozen missing-report rule, each configuration earned **0/3 factual points, 0/2 reasoning points and 0/1 forward points**, for a local quality score of **0.00** on this event. Every attempt stays in the denominator. The citation-support rate is undefined because the six attempts released zero claims. Grading the empty submissions required no later observation. [Scoring protocol](../../protocol.md)

## What stopped release

Three teams included the ±2% comparison band supplied in the question. The forecast validator required a matching percentage in a cited issuer passage. In Single Sol’s repaired draft, “as of May 8, 2020” supplied the date of the run, and the writer’s check rejected the number `8`. In the mixed team without a forecast specialist, the two cited excerpts both reopen on PDF page 3, section 4. The writer’s check rejected the `3` in “(PDF p. 3)”. These were the first failing checks on the final drafts or forecasts. The subsequent quality of those drafts remains untested. [Registered question and cutoff](../../development.json) · [Single Sol record](single_sol/run.json) · [Mixed forecast record](mixed/run.json) · [Page citation record](mixed_no_forecast/run.json)

Single Astra had USD 0.058384 left when it requested a memo repair. A separate post-run input-token count returned **4,961** for that exact request. Replaying the registered pricing and output policy reproduced the **USD 0.152010** reservation refusal. The repair produced zero generation charges. [Count and request fingerprint](single_astra/budget-stop.json) · [Astra usage and recorded refusal](single_astra/run.json)

## Replay and accounting

The offline audit exported the registered packet, rebuilt all 74 dispatched request fingerprints and reproduced all six terminal decisions. The operator check matched each generation to a distinct private request ID and response ID, the returned usage, output and receipt hash. The provider reported 40 Sol, 26 Luna, 3 Terra and 5 Astra generations, with zero cached or cache-write input tokens. The cost uses returned token usage and registered rates. Adding it to the previously recorded USD 0.5785677 gives **USD 1.0596977** of the USD 100 project allowance. [Run manifest and rates](../../run-manifest.json) · [Previous project usage](../../../../BUILD_PLAN.md)

Verify the committed evidence from the repository root with the registered runtime. The second command also checks the private receipts on the machine that ran the event. Both commands work offline.

```sh
.venv/bin/python -B -m evaluations.paired.event_audit nintendo-fy2021-guidance
.venv/bin/python -B -m evaluations.paired.event_audit nintendo-fy2021-guidance --operator-root runs/paired-development-v1
```

The paid and offline `evaluations.paired.runner` commands both returned exit status `2` because the event contained failed assignments. Their six terminal summaries were byte-identical. The audit above returns `0` when the recorded failures, costs and evidence reproduce. [Runner](../../runner.py) · [Offline event audit](../../event_audit.py)
