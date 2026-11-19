# Financial statement accuracy on 24 independent issuers

At the same USD 0.45 spending limit, the quantitative team returned a correct, cited answer for 15 of 24 companies. Single Astra did so for 13. The paired difference was +8.3 percentage points. Its 90% bootstrap interval ran from −8.3 to +25.0 points. The result does not establish a reliable advantage for routing official questions to the quantitative team. [Masked judgments and paired statistics](results.json) · [Preregistered selection rule](../financegym/selection.md)

| Measure | Quantitative team | Single Astra with the same tools |
|---|---:|---:|
| Released reports | 23/24 | 23/24 |
| Correct answer | 15/24 | 16/24 |
| Answer supported by cited passages | 18/24 | 16/24 |
| Correct and supported | 15/24 | 13/24 |
| Mean model cost per assigned case | $0.2330 | $0.2627 |

The team alone succeeded on four companies and the single analyst alone succeeded on two. The two-sided exact McNemar p-value was 0.6875. Both configurations scored 5/8 on domain questions and 4/8 on novel questions. On metrics questions, the team scored 6/8 and the single analyst scored 4/8. These small groups describe this sample. [All individual outcomes](results.json)

The research workers saw the original question and a complete issuer PDF, with the same source search and calculator. The 48 reports were committed before the evaluator opened the authors' answers and annotated evidence. A fresh Astra grading context examined each released report independently with the configuration and costs hidden. Quoted witnesses were checked against the report and its cited passages. Grading cost USD 1.847056. The public dataset could have appeared in model training. FinanceGym evaluates forward-looking research separately. [Study protocol](protocol.md) · [48 locked reports](reports.json) · [FinanceBench source](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)

The private receipt audit reconciled 272 research generations totaling USD 11.895946. All 185 searches used the registered cutoff and issuer document. Replaying the two failed assignments reproduced an incomplete provider response and a repair that exceeded its remaining funds. [Audit](audit.json)

Recreate the packet inputs from the pinned dataset and reproduce the publicly shareable scores from the saved grades. The first command downloads the original financial statements. The second uses the local corpus and saved responses.

```sh
.venv/bin/python -B -m evaluations.financebench.corpus
.venv/bin/python -B -m evaluations.financebench.grading
```
