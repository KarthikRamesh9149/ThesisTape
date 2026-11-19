# Development runner and Nintendo readiness check

All six configurations reached their registered starting stage on Nintendo’s FY2021 guidance case. Each ran in a fresh Python process with its own copy of the evidence and a USD 0.20 allowance. The question asks for the profit guidance, a comparison with the prior year, component-supply risk and a forecast. The cutoff is 8 May 2020 at noon UTC. [Nintendo’s 7 May financial statements](https://www.nintendo.co.jp/ir/pdf/2020/200507e.pdf) · [Frozen comparison protocol](protocol.md)

## What was verified on 20 November 2026

Every configuration indexed the same 26 passages across the same 15 pages, reopened the same five ranked passages, rendered identical pixels for the first hit and calculated the same result using a printed operand. All six rejected an unknown source and a changed cutoff. The model-facing tools can access only the exported question and dated publisher evidence. The controller keeps the evaluator archives outside the packet. [Hashes and observed results](runner-readiness.json)

| Configuration, in registered order | First action | Model available to read an original page |
| --- | --- | --- |
| Single Sol | Sol searches | Sol |
| All-Sol team | Sol delegates | Sol |
| Mixed team | Sol delegates | Terra |
| Single Astra | Astra searches | Astra |
| Mixed with fixed plan | Two Luna workers search independently | Terra |
| Mixed without forecast specialist | Sol delegates | Terra |

OpenAI counted the input tokens for the seven opening requests and six page-reading requests. The runner temporarily protected each configuration’s entire allowance and verified that generation stopped before dispatch. All six ledgers closed with zero generation requests, USD 0 settled and USD 0 outstanding. This measures access and spending controls. Report quality, actual generation cost and report latency will be measured in the paid comparison. [Per-request counts and maximum charges](runner-readiness.json)

Separate regression tests supply recorded-shape responses through a fixture transport. They exercise all six workflows through report creation and byte-identical replay, including source citations, conditional visual reading, forecast routing, independent review, interrupted requests and separate spending limits. The single-agent controls use identical prompts apart from the model ID, as do the all-Sol and mixed teams on the same sequence of decisions. [Runner tests](../../tests/test_paired_runner.py)

## Reproduce the checks

From the repository root, use the Python packages, SQLite and `pdftoppm` versions in the manifest. The first command checks the saved provider counts offline. The second runs the workflow regression tests. [Registered runtime and model settings](run-manifest.json)

```sh
.venv/bin/python -B -m evaluations.paired.runner preflight
.venv/bin/python -B -m unittest discover -s tests -p test_paired_runner.py -v
```

To obtain fresh input-token counts, use the project’s OpenAI key. This command calls the counting endpoint and prints a new result to standard output.

```sh
.venv/bin/python -B -m evaluations.paired.runner preflight --live-counts
```

## First paid event

The first paid event completed with six terminal decisions, zero released memos and USD 0.48113 in receipt-based cost. The controller checked the committed manifest, followed the seeded order and saved each configuration under `runs/paired-development-v1/nintendo-fy2021-guidance/`. Repeating the run command on the original workspace reopens those assignments. Elsewhere the archived event audit can replay the results offline. [Results, failure analysis and offline commands](results/nintendo-fy2021-guidance/evaluation.md)

```sh
.venv/bin/python -B -m evaluations.paired.runner run-event nintendo-fy2021-guidance
.venv/bin/python -B -m evaluations.paired.runner replay-event nintendo-fy2021-guidance
```

The manifest binds 68 development assignments to their case packets, models, prompts, retrieval and tool limits, rates, retries and source revision. The project’s model catalogue exposed the four named aliases. Since it supplied no dated snapshots for them, each generation records the model ID the provider returns. [Schedule and manifest](run-manifest.json)
