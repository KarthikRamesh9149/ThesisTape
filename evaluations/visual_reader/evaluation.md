# Reading Toyota’s share repurchase table

Both live routes recovered **¥435.6 billion** for Toyota’s FY2022 full-year share repurchases on the shareholder-return basis. Each earned **6/6**. Opening the page image added **USD 0.012342**. The text-only route reconstructed the right value even though the extractor joined adjacent cells into `435.6149.9`. [Toyota’s table, page 29](https://global.toyota/pages/global_toyota/ir/financial-results/2023_4q_summary_en.pdf#page=29) · [Paired scores and costs](pair/results.json)

| Live route | Score | Generations | Elapsed | Estimated USD |
| --- | ---: | ---: | ---: | ---: |
| Sol with extracted text | 6/6 | 2 | 10.31 s | 0.018746 |
| Terra page read, Sol author and independent Sol review | 6/6 | 3 | 13.48 s | 0.031088 |

The criteria cover the number, row, fiscal group and column, unit, and two applicable footnotes. Note 4 excludes sub-unit shares purchased on request and repurchase commissions. Note 5 ties shareholder return to net income for the period and distinguishes the maximum purchase price during a repurchase period from the actual amount once it ends. Terra selected the correct cell and both markers. Both released reports reproduce the full definitions with links. [Publisher notes, page 30](https://global.toyota/pages/global_toyota/ir/financial-results/2023_4q_summary_en.pdf#page=30) · [Text-only report](pair/table-text/memo.md) · [Visual report](pair/visual/memo.md)

## What the comparison measures

Toyota published the 30-page financial summary on 10 May 2023. The trial cutoff was 11 May. A fresh download matched the archived PDF byte for byte. Poppler 26.05.0 rendered the complete page at 1,768 × 2,500 pixels. The question, original PDF and page image digests, six criteria and USD 0.12 cap per arm were frozen in commit `3960bd0`. The implementation and grader were committed in `ba51584` before the first paid call. [Toyota release index](https://global.toyota/en/ir/financial-results/archives/03.html) · [Case](../../cases/toyota-buybacks-2023-05-11/agent/question.json) · [Source manifest](../../cases/toyota-buybacks-2023-05-11/archive/catalog.json) · [Frozen protocol](../../cases/toyota-buybacks-2023-05-11/evaluator/protocol.json)

Both arms received the located table in its native extracted layout and all eight numbered note definitions. On the visual route, Terra also received the full original table page. Sol received Terra’s proposed reading, wrote the report and sent it to a fresh Sol reviewer. That reviewer independently reopened the page and footnotes. The control used the same author, reviewer and release rules with text evidence. Both reports passed their first review. The comparison measures the combined page reader and visual review on one known table. Source discovery and FinanceGym scoring have separate checkpoints. [Text-only receipt](pair/table-text/run.json) · [Visual receipt](pair/visual/run.json)

Terra’s generation cost USD 0.007262. The USD 0.012342 difference between the complete routes also includes the reviewer’s image input and variation in Sol usage. Costs use provider-reported tokens at the published Standard rates. All reservations settled. [Terra pricing](https://developers.openai.com/api/docs/models/gpt-6.1-terra) · [Sol pricing](https://developers.openai.com/api/docs/models/gpt-6.2-sol) · [Image inputs](https://developers.openai.com/api/docs/guides/images-vision)

## Separate live routing check

After the paired trial, we froze a question about the intact actual-purchase row directly above it. The default conditional route reported **¥404.6 billion**, selected only Note 4 and earned **5/5**. It used two Sol calls, sent zero images and cost **USD 0.016962**. This checks whether the router can skip Terra on a clear row of the same damaged table. The paired trial and this check used seven live generations at a combined **USD 0.066796**. Recorded project spend reached **USD 0.3865639** against the USD 100 ceiling. [Routing criterion](../../cases/toyota-buybacks-2023-05-11/evaluator/clear-row.json) · [Clear-row report](clear-row/memo.md) · [Clear-row receipt](clear-row/run.json) · [Spend ledger](usage.json)

## Reproduce the verification

With the project requirements installed, this command replays the three reports offline, checks their source links, model requests and token ledgers, and recomputes the scores and spend. The regression suite also checks every rendered citation and the actual image content sent to each model.

```sh
.venv/bin/python -B -m evaluations.visual_reader.verify
.venv/bin/python -B -m unittest discover -s tests -p test_visual_reader.py -v
```

To regenerate the frozen PNG directly from the publisher PDF, install Poppler 26.05.0 and run the following command. Routine offline replay uses the saved PNG.

```sh
.venv/bin/python -B -m evaluations.visual_reader.verify --render --source-only
```

The original live runs used these commands. A new paid run requires a fresh output directory.

```sh
.venv/bin/python -B -m scripts.research_team run --case toyota-buybacks --route table-text --budget-usd 0.12 --output-dir runs/toyota-table-pair/table-text
.venv/bin/python -B -m scripts.research_team run --case toyota-buybacks --route visual --budget-usd 0.12 --output-dir runs/toyota-table-pair/visual
.venv/bin/python -B -m scripts.research_team run --case toyota-buybacks-actual --budget-usd 0.06 --output-dir runs/toyota-clear-row
```
