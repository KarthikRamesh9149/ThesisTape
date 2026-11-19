# FinanceGym evaluation

The official runner uses the analyst desk's public-archive research engine. It matches every output to the original question and cutoff, meters each model generation, and records the raw HTTP evidence needed to rebuild dated retrieval independently. The 400 tasks come from the pinned public release. The maintainers hold the scoring rubric. [Public dataset](https://huggingface.co/datasets/Rujun/FinanceGym/tree/980a80810fdb41c90d8533baa228ddbe6557e9b1) · [Submission rules](https://financegym.github.io/#submit)

## Verify the inputs and retrieval

Save the JSONL from the pinned dataset as `runs/benchmark_400_public.jsonl`. Run from the repository root.

```sh
.venv/bin/python -B -m evaluations.financegym.runner preflight runs/benchmark_400_public.jsonl
.venv/bin/python -B -m evaluations.financegym.archive_check runs/financegym-archive-proof --live
.venv/bin/python -B -m evaluations.financegym.archive_check runs/financegym-archive-proof
```

The live archive check on the ECB decision on 6 June 2024 admitted four historical sources to a 7 June search. Its 14 HTTP requests reproduced the same result offline. This check uses public sources and makes zero model calls. Discovery receipts stay in an operator-only directory. The agent receives the dated passages that pass the gateway.

## Run the registered candidate

Registration reads the committed paired blind decision, checks the selected configuration against its frozen quality and cost rule, and binds the question-file hash, runtime, dependencies, model prices and per-case allowance. Set the case limit from reconciled measurements and commit the generated registration before generation. For example, the following registration command accepts a case limit supplied through the shell environment.

```sh
.venv/bin/python -B -m evaluations.financegym.runner register runs/benchmark_400_public.jsonl --cap-usd "$FINANCEGYM_CASE_CAP_USD" --source-proof runs/financegym-archive-proof
.venv/bin/python -B -m evaluations.financegym.runner run runs/benchmark_400_public.jsonl --workers 4
```

Each task has a durable research record. An interrupted run reuses confirmed generations. Any uncertain charge stays reserved for reconciliation. Completed, withheld and failed attempts keep their original records.

## Export for grading

```sh
.venv/bin/python -B -m evaluations.financegym.runner export runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.runner validate runs/benchmark_400_public.jsonl runs/financegym-submission/answers.jsonl
```

Export verifies the original HTTP bytes, all model request fingerprints, the ledger, and the exact text of every released memo offline. It writes the submission only when all 400 questions have a cited report. The separate audit lists qualified reports, the total research cost and a report fingerprint for each task. FinanceGym asks for the JSONL plus contributor metadata and a public system or paper link. A leaderboard score comes from the maintainers after submission.
