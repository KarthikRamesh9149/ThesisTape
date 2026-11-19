# Forecasting NVIDIA’s Q2 FY2024 revenue

As of 25 May 2023, both teams expected NVIDIA’s quarterly revenue to finish **within** management’s guidance. The company subsequently reported **USD 13.51 billion**, above the original **USD 10.78–11.22 billion** range. The specialist improved the use of historical evidence by one point on the blinded assessment. Both teams missed the outcome. The specialist stays optional pending evidence from more cases. [Control memo](pair/control/memo.md) · [Specialist memo](pair/specialist/memo.md) · [NVIDIA’s May guidance](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000087/q1fy24pr.htm) · [NVIDIA’s August result](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000171/q2fy24pr.htm)

| Measure | Reviewed team | Reviewed team with Sol forecast specialist |
| --- | ---: | ---: |
| Factual evidence | 1/2 | 2/2 |
| Horizon, causal mechanism, alternative and revision signal | 4/4 | 4/4 |
| Total as-of assessment | **5/6** | **6/6** |
| Realized revenue category | 0/1 | 0/1 |
| Cost of calls used to produce the graded report | USD 0.0270769 | USD 0.0394709 |
| Cost including development retries and prior reviews attributed to the arm | USD 0.0481649 | USD 0.0579609 |
| Sum of measured run intervals, including recovery and shared research | 50.757 s | 65.375 s |

The control argued that management had already accounted for surging AI demand and the production ramp. Both memos named faster cloud deployment as a reason to move toward an upside case. The specialist memo also used consolidated Q1 revenue of USD 7.19 billion, up 19% sequentially, to show how much acceleration the guidance implied. This prior-quarter comparison earned the extra factual point. The grader awarded both memos all four reasoning points. Their upside alternatives received reasoning credit. Outcome scoring used each memo’s published base case. [Control grades and excerpts](pair/grade-control/grade.json) · [Specialist grades and excerpts](pair/grade-specialist/grade.json) · [Contemporaneous CFO commentary](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000087/q1fy24cfocommentary.htm)

## Design and validation

The question, USD 0.12 ceiling per arm, dated source selection, six criteria and outcome rule were committed to private GitHub before the first paid call. The Sol lead and two Luna researchers ran their investigation once. The paired routes received the same four original passages, worker findings, lead decision, writing instructions and model settings. The specialist route added one source-grounded Sol note to the author’s input. Each final draft went to its own fresh Sol reviewer. A separate stateless Sol grader assessed each released report against the frozen criteria with the arm, specialist note, cost and later result hidden. The grades are model judgments on one retrospective development case. Model training may have included this event. [Frozen protocol and implementation](https://github.com/KarthikRamesh9149/ThesisTape/tree/009877e5ca8c43cd3f80bec2c0f3bb6392c86ccb) · [Scoring criteria](../../cases/nvidia-revenue-2023-05-25/evaluator/protocol.json)

The input archive contains the May earnings release and CFO commentary. The August earnings release and its EDGAR index live in an evaluator-only directory. Each exhibit is the original SGML document component from the complete submission. The EDGAR index verifies its sequence, type, filename, byte size and acceptance time. Reimporting from the SEC reproduced the stored bytes. The outcome evaluator reads the two source passages, calculates the inclusive interval from the May midpoint and tolerance using decimal arithmetic, and compares the August headline figure with those bounds. An offline test also adds the August exhibit to the search archive, warms its cache under the later date and verifies the original model requests at the May cutoff. [May EDGAR index](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000087/0001045810-23-000087-index.htm) · [August EDGAR index](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000171/0001045810-23-000171-index.htm) · [Computed results](pair/results.json)

## Validation defect and spending

The first numeric validator looked for fiscal labels and publication dates in paragraph bodies. Valid information in the cited exhibit title and dateline triggered a control writing retry and stopped the treatment twice, first on the specialist note and then on the draft. The shared validator now accepts a fiscal label from the cited title and a publication date attached to a reporting verb. Amounts and percentages still need support in the passage. After fixing the validator, the experiment recovered the first specialist note and the first draft from **each** arm, verified their original request fingerprints, and sent those drafts to fresh reviewers. Both passed on the first independent review. The earlier control memo and all three historical receipts are included for audit. [Earlier control](pair/history/control/run.json) · [Specialist note attempt](pair/history/note/run.json) · [Writing attempt](pair/history/writer/run.json)

The specialist call itself cost USD 0.006682. Across the calls that produced the graded memos, the specialist route cost **USD 0.012394 more**. Including the retries and reviews attributed to each arm, the difference was **USD 0.009796**. Research worth USD 0.0065709 was allocated to both arms for comparison and billed once. The two blind grades cost USD 0.024718 together. All 20 paid generations, including the attempts that stopped during validation, totaled **USD 0.1242729** and reconciled to the operator receipts. The project’s estimated cumulative spend is **USD 0.3197679 of USD 100** at the recorded Standard rates. [Usage ledger](usage.json)

## Reproduce the comparison

From the repository root with the Python dependencies installed, replay both reports, both blind grades, the original requests and the outcome calculation offline.

```sh
.venv/bin/python -B -m evaluations.forecast_specialist.experiment check
.venv/bin/python -B -m unittest discover -s tests -p 'test_forecast*.py' -v
```

To run a new paid pair, supply a fresh directory. The experiment shares the investigation, reserves each arm’s spend, and writes both reports before grading. For an individual analyst report, use the standard team CLI with the forecast route.

```sh
.venv/bin/python -B -m evaluations.forecast_specialist.experiment run --directory runs/nvidia-pair-new
.venv/bin/python -B -m scripts.research_team run --case nvidia --route forecast --budget-usd 0.12 --output-dir runs/nvidia-outlook-new
```
