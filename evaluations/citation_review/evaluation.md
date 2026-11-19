# Independent citation review on Toyota’s FY2024 outlook

A fresh GPT-6.2 Sol reviewer accepted the team’s original answer, caught a one-word change that assigned the forecast to the wrong financial metric, and approved the lead’s correction on a second read. The final memo matched the accepted control byte for byte. All three prespecified development checks passed. [Scoring sheet](../../cases/toyota-outlook-2023-05-11/evaluator/scoring.json) · [Corrected memo](challenge/memo.md)

## What the filing supports

Toyota published its FY2023 consolidated financial results on 10 May 2023. Page 3 gives its forecast for the following fiscal year. The full-year operating income column shows **3,000,000 million yen**. The separate column for net income attributable to Toyota Motor Corporation shows **2,580,000 million yen**. The case asks for operating income as of 11 May 2023. The archived English PDF and a fresh download from Toyota had the same SHA-256 digest. [Toyota financial summary, page 3](https://global.toyota/pages/global_toyota/ir/financial-results/2023_4q_summary_en.pdf#page=3) · [Issuer publication index](https://global.toyota/en/ir/financial-results/archives/03.html)

| Candidate | What changed | Independent decision |
| --- | --- | --- |
| Original team memo | The author gave the operating income forecast and cited the full-year table. | Supported. The reviewer identified the operating income column, fiscal period and yen scale. |
| Evaluator’s copy | The evaluator changed “operating income” to “net income” in one assertion. The figure, fiscal year, source excerpts and page link were identical. The existing deterministic checks still passed. | Contradicted. The reviewer named both metrics and gave the different value in the net income column. |
| Lead’s correction | The lead received the finding and restored “operating income.” | Supported. A fresh reviewer reopened the same dated table and checked the exact revised memo before release. |

The original came from the live Sol-led research team. Its first writing attempt also mentioned an announcement date absent from the cited table. The numeric validator requested one repair, which is included in the drafting cost. The paired test used the first draft that cleared these deterministic checks. Each independent reviewer received only the question, cutoff, rendered memo, claim text with its citations, and original passages. The evaluator introduced the wrong label deliberately. This single pair demonstrates detection and correction of that error. Estimating accuracy or false alarms across issuers requires more independent cases. [Control receipt](control/run.json) · [Challenge receipt](challenge/run.json)

## Measured cost

| Work | Live generations | Estimated USD | Time |
| --- | ---: | ---: | ---: |
| Research and write the shared draft, including the numeric repair | 10 | 0.0130365 | 20.441 s through the writer’s last response |
| Independent review of the original | 1 | 0.003772 | 3.850 s provider call |
| Complete original route | 11 | 0.0168085 | 24.800 s end to end |
| Independent review of the changed claim | 1 | 0.003692 | 3.228 s provider call |
| Lead correction | 1 | 0.003992 | 3.360 s provider call |
| Fresh review of the correction | 1 | 0.003866 | 4.039 s provider call |
| Complete challenge branch using the shared draft | 3 | 0.011550 | 11.575 s from branch start |

Both runs together used an estimated **USD 0.0283585**. The lead correction and second review added USD 0.007858 after detection. All 14 generations have settled provider usage and their request and response ID digests matched the operator receipts. Cost uses the provider’s reported tokens and the recorded Standard rates. The project’s cumulative recorded estimate is **USD 0.195495** against its USD 100 ceiling. [Usage ledger](usage.json) · [Sol pricing](https://developers.openai.com/api/docs/models/gpt-6.2-sol) · [Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna)

## Reproduce the pair

With the repository’s Python dependencies installed, run the following from its root. The first command reconstructs both memos and every model request, verifies the dated source, checks the spend ledgers, and grades the saved pair offline.

```sh
.venv/bin/python -B -m evaluations.citation_review.experiment check
.venv/bin/python -B -m unittest discover -s tests -p 'test_claim_review.py' -v
```

For a new paid trial, choose two unused output directories. The second command reuses the first run’s draft, so both reviewers get the same evidence and the research is paid for once. Keep every attempt when reporting results.

```sh
.venv/bin/python -B -m scripts.research_team run --case toyota --route reviewed --budget-usd 0.09 --output-dir runs/toyota-control-new
.venv/bin/python -B -m evaluations.citation_review.experiment run --control-dir runs/toyota-control-new --challenge-dir runs/toyota-challenge-new --budget-usd 0.06
```
