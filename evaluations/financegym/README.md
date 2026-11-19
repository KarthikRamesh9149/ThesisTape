# FinanceGym evaluation

The official runner uses the analyst desk's public-archive research engine. It matches every output to the original question and cutoff, meters each model generation, and records the raw HTTP evidence needed to rebuild dated retrieval independently. The 400 tasks come from the pinned public release. The maintainers hold the scoring rubric. [Public dataset](https://huggingface.co/datasets/Rujun/FinanceGym/tree/980a80810fdb41c90d8533baa228ddbe6557e9b1) · [Submission rules](https://financegym.github.io/#submit)

## Verify the inputs and retrieval

Save the JSONL from the pinned dataset as `runs/benchmark_400_public.jsonl`. Run from the repository root.

```sh
.venv/bin/python -B -m evaluations.financegym.runner preflight runs/benchmark_400_public.jsonl
.venv/bin/python -B -m evaluations.financegym.archive_check runs/financegym-focused-source-proof --focused --live
.venv/bin/python -B -m evaluations.financegym.archive_check runs/financegym-focused-source-proof --focused
```

The live archive check on the ECB decision on 6 June 2024 admitted four historical source editions to a 7 June search. Its 14 HTTP requests reproduced five passage links offline. The gateway can reopen an admitted original and retrieve the surrounding paragraphs for each part of the question. It checks the original bytes against the edition hash. This check uses public sources and makes zero model calls. Discovery receipts stay in an operator-only directory.

## Run the registered candidate

Registration verifies the committed paired blind decision and reproduces the independent FinanceBench grades. Each official task receives a frozen research configuration and spending limit. The general route has a USD 0.30 limit. The quantitative route, when it qualifies, has a USD 0.45 limit. The registration records the expected cost separately from the sum of those limits. [Candidate selection](selection.md)

```sh
.venv/bin/python -B -m evaluations.financegym.runner register runs/benchmark_400_public.jsonl --cap-usd 0.30 --source-proof runs/financegym-focused-source-proof
.venv/bin/python -B -m evaluations.financegym.runner run runs/benchmark_400_public.jsonl --workers 4
```

Each task has a durable research record. An interrupted run reuses confirmed generations. Any uncertain charge stays reserved for reconciliation. Completed, withheld and failed attempts keep their original records.

## Export for grading

```sh
.venv/bin/python -B -m evaluations.financegym.runner export runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.runner validate runs/benchmark_400_public.jsonl runs/financegym-submission/answers.jsonl
```

Export verifies the original HTTP bytes, all model request fingerprints, the ledger, and the exact text of every released memo offline. It writes the submission only when all 400 questions have a cited report. The separate audit lists qualified reports, the total research cost and a report fingerprint for each task. FinanceGym asks for the JSONL plus contributor metadata and a public system or paper link. A leaderboard score comes from the maintainers after submission.

If any task ends without a publishable answer, register the independent recovery implementation and commit its generated manifest before dispatch. This route preserves both attempts and their charges. It skips every accepted primary answer. [Recovery eligibility and spending rules](recovery-policy.md)

```sh
.venv/bin/python -B -m evaluations.financegym.recovery register
git add evaluations/financegym/recovery-registration.json
git commit -m "Register independent recovery of unanswered FinanceGym questions" -- evaluations/financegym/recovery-registration.json
.venv/bin/python -B -m evaluations.financegym.recovery run runs/benchmark_400_public.jsonl --workers 4
.venv/bin/python -B -m evaluations.financegym.recovery export runs/benchmark_400_public.jsonl runs/financegym-submission
```

## Prepare the methodology paper and local email

Generate the public-facing paper from the verified 400-row export. The note reports the actual configuration and the independently measured results. The code repository can stay private. Once the paper has a public URL, use the submitter's email address, organization and submission date to generate a local email draft with the exact JSONL attached.

```sh
.venv/bin/python -B -m evaluations.financegym.submission paper runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.submission draft runs/benchmark_400_public.jsonl runs/financegym-submission \
  --contact "$SUBMITTER_EMAIL" --org "$SUBMITTER_ORGANIZATION" \
  --published-paper "$PUBLIC_PAPER_URL" --submission-date "$SUBMISSION_DATE"
```

The private evidence archive stores the original and published research, dated passages, model usage, and hashes of the operator's HTTP receipts. The verifier rebuilds all 400 submitted reports from those recorded inputs with the network disabled.

```sh
.venv/bin/python -B -m evaluations.financegym.evidence seal runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.evidence verify runs/benchmark_400_public.jsonl
```
