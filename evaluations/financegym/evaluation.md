# FinanceGym results and submission package

ThesisTape produced a cited research report for each of the 400 original FinanceGym questions. Offline replay reproduced all 400 with their original wording, order and cutoff dates. The full project used an estimated USD 138.93 of its USD 140 API budget. The external score and ranking are pending maintainer grading. [400 reports](results/answers.jsonl) · [Methodology paper](methodology.pdf) · [Offline reader instructions](README.md) · [FinanceGym submission rules](https://financegym.github.io/#submit)

## Where the reports came from

| Publication stage | Reports | Additional model cost across the stage |
|---|---:|---:|
| Registered single Astra primary | 340 | USD 74.8131 |
| Registered independent quantitative team | 21 | USD 10.9799 |
| Independent mixed team assigned from the remaining budget | 18 | USD 4.0523 |
| Previously approved paragraphs from an unsuccessful optional rewrite | 12 | USD 0 |
| Additional author and independent review using existing sources | 2 | USD 0.2798 |
| Historical title lookup followed by additional author and independent review | 1 | USD 0.0536 |
| Independent review of an existing primary draft under the project ceiling | 6 | USD 0.2003 |

The originally registered researchers released 361 reports. Procedures added during the run supplied the remaining 39. We assigned 18 questions to the mixed team based on its measured cost and the available balance. The audit includes every unsuccessful attempt and its cost. [Case assignments and balance](results/recovery-allocation.json)

The historical title lookup recovered evidence for 1 question by varying the capitalization of company names already in the question. An independent reviewer also accepted 6 existing drafts for publication. Of the 400 reports, 127 met the internal criteria for a complete answer and 273 identify a gap in the dated evidence. [Publication rules](reviewed-release.md) · [Funded editing](editorial-policy.md) · [Historical title lookup](title-policy.md) · [Primary draft review](budget-review-policy.md)

## Sources and usage

Across the 400 reports, 351 cited dated web captures and 298 cited immutable encyclopedia revisions. A report may cite both. Of the 400, 49 relied solely on encyclopedia revisions and 37 included a calculation checked against its inputs. FinanceGym's withheld rubric determines the official accuracy and forecast scores.

The full run used 1,729 confirmed model generations and an estimated USD 90.3790. Cost per original question averaged USD 0.2259, with a median of USD 0.1967, a 95th percentile of USD 0.5249 and a maximum of USD 0.6567. The maximum unresolved reservation on this run was USD 0 at final reconciliation. Estimated usage for the entire project, including development and grading, was USD 138.9301. The project-wide unresolved maximum was USD 0.008229375. Amounts are based on provider usage receipts and the model rates recorded in the repository.

The reproducibility check verified 471 investigations and editing runs. Each report's elapsed time, original document editions, spending ceilings, question and final text were checked. The portable archive excludes private provider identities. [Detailed audit](results/audit.json) · [Source coverage and cost verification](verification.json) · [Artifact fingerprints](results/manifest.json)

Research time per original question averaged 147.4 seconds. The 95th percentile was 292.1 seconds. These durations sum the investigations and editing on each question and exclude waiting between stages.

## Reproduce the reports

Download the pinned public FinanceGym question file to `runs/benchmark_400_public.jsonl`. From the repository root, this command checks the sealed reports without an API key or a network connection. [Pinned public dataset](https://huggingface.co/datasets/Rujun/FinanceGym/tree/980a80810fdb41c90d8533baa228ddbe6557e9b1)

```sh
.venv/bin/python -B -m evaluations.financegym.evidence verify runs/benchmark_400_public.jsonl
```

The submission file SHA-256 is `5daeb98a17509bda30fa5f394ab3fc5ed440a22ceb1a407dbe0ff70165ab1057`.
