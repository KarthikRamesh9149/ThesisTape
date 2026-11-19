# FinanceGym evaluation

The official runner uses the analyst desk's public-archive research engine. It matches every output to the original question and cutoff, meters each model generation, and records the raw HTTP evidence needed to rebuild dated retrieval independently. The 400 tasks come from the pinned public release. The maintainers hold the scoring rubric. [Public dataset](https://huggingface.co/datasets/Rujun/FinanceGym/tree/980a80810fdb41c90d8533baa228ddbe6557e9b1) · [Submission rules](https://financegym.github.io/#submit)

## Run or resume the complete workflow

Place the pinned question file at `runs/benchmark_400_public.jsonl`. The status command reads the research records and project balance without calling a model. The paced runner completes outstanding primary investigations. The finish command reviews eligible drafts, conducts independent research on unanswered questions and applies the source checks before exporting the reports. Both research commands can use API credits and enforce the shared project ceiling.

```sh
.venv/bin/python -B -m evaluations.financegym.workflow status runs/benchmark_400_public.jsonl
.venv/bin/python -B -m evaluations.financegym.pacing runs/benchmark_400_public.jsonl --workers 3
.venv/bin/python -B -m evaluations.financegym.workflow finish runs/benchmark_400_public.jsonl runs/financegym-submission
```

The final directory contains the submission JSONL, a per-question audit and a methodology paper. The workflow also seals and independently replays the private evidence in `evaluations/financegym/results/`. For a new run in a checkout that already contains a sealed benchmark, choose a fresh destination and pass `--evidence-directory` with a fresh directory. To reproduce the already sealed reports, use `evaluations.financegym.evidence verify` with the public question file. That operation runs offline without an API key.

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
.venv/bin/python -B -m evaluations.financegym.pacing runs/benchmark_400_public.jsonl --workers 3
```

Each task has a durable research record. An interrupted run reuses confirmed generations. Any uncertain charge stays reserved for reconciliation. Completed, withheld and failed attempts keep their original records.

The first six questions used up to three concurrent investigations. After concurrency increased to eight, the archive began returning HTTP 429. At the time we paused scheduling, 218 primary investigations had finished. The last 30 of those recorded 196 rate-limit responses among 1,077 HTTP requests. The first six had recorded zero among 281. The remaining questions use smaller groups and a 20- or 60-second pause when the archive reports throttling. This changes when questions start. Each question still uses its registered researcher, sources, cutoff and spending limit. Resuming skips every terminal investigation.

```sh
.venv/bin/python -B -m evaluations.financegym.pacing runs/benchmark_400_public.jsonl --workers 3
```

The owner set a USD 140 ceiling for the entire project during the run. The paced runner admits new research only up to USD 139, leaving a dollar of headroom. It counts development, testing, previous attempts and outstanding provider charges. Simultaneous investigations reserve their entire individual allowances before starting. An interrupted assignment keeps its reservation and can resume under the same allowance.

The portable cost record preserves previously incurred charges when a clean checkout has no operator files. Restoring an original run counts its charge once. Completing the benchmark refreshes the record. Use one active checkout for paid work so concurrent calls share its reservation database. The spending estimates use priced provider receipts. [Portable project cost record](cost-records.json)

```sh
.venv/bin/python -B -m evaluations.financegym.budget status
.venv/bin/python -B -m evaluations.financegym.budget recovery runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
.venv/bin/python -B -m evaluations.financegym.budget editorial runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
```

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
.venv/bin/python -B -m evaluations.financegym.budget recovery runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
.venv/bin/python -B -m evaluations.financegym.recovery export runs/benchmark_400_public.jsonl runs/financegym-submission
```

If both investigators leave a question unanswered after its optional rewrite fails, the independently reviewed first draft can sometimes support a qualified report. The [publication rule](reviewed-release.md) was recorded during the official run. Its export reuses the accepted paragraphs, reports these cases separately and includes both original failures and their costs. It makes zero new provider calls.

```sh
.venv/bin/python -B -m evaluations.financegym.reviewed_release export runs/benchmark_400_public.jsonl runs/financegym-submission
```

If neither investigator produced a releasable memo or an independently approved first draft, the [funded editing procedure](editorial-policy.md) can use their original dated passages. A Sol analyst checks calculations, Astra writes, and a fresh Sol review determines which paragraphs can appear in the qualified report. The additional ceiling is USD 0.55. The final export counts these reports and all three stages' expenses. It uses the originally released answer whenever one exists.

```sh
.venv/bin/python -B -m evaluations.financegym.budget editorial runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
.venv/bin/python -B -m evaluations.financegym.editorial export runs/benchmark_400_public.jsonl runs/financegym-submission
```

One issuer search exposed case-sensitive title matching after both original investigations returned zero sources. A separately recorded procedure can retry the original queries using different capitalization of entity titles in the question. It keeps the cutoff fixed and verifies the raw retrieval offline. The existing editor can then use any dated evidence it recovers. These qualified reports are counted separately and use the same shared spending check. [Historical title procedure](title-policy.md)

```sh
.venv/bin/python -B -m evaluations.financegym.titled_publication run runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
.venv/bin/python -B -m evaluations.financegym.titled_publication export runs/benchmark_400_public.jsonl runs/financegym-submission
```

Under the USD 140 total budget, the system can first give a fresh Sol reviewer an existing primary draft when its optional revision exceeded the individual allowance. Only an original answer plus another section that both pass independent review can be released. One review costs at most USD 0.075. A withheld review and its charges stay in the record if the independent research team then takes the question. The 33 independent investigations already dispatched when this policy was recorded retain their original routes. Use the independent reviewer for an eligible existing draft. [Prospective eligibility and review](budget-review-policy.md)

```sh
.venv/bin/python -B -m evaluations.financegym.budget_publication run runs/benchmark_400_public.jsonl --task-id "$UNANSWERED_TASK_ID"
```

When a damaged PDF interrupted a dated search, the original attempt kept its failure, timestamp and charge. The separate verifier repeats the paid plan from recorded output and reconstructs every search from the original HTTP bytes with the network disabled. The private replay package includes the malformed PDF so another machine can reproduce the parser error. The final export uses this verifier throughout the existing publication procedure.

```sh
.venv/bin/python -B -m evaluations.financegym.archive_failure verify runs/benchmark_400_public.jsonl 6a04cfe2a3882eb36cfc9305
.venv/bin/python -B -m evaluations.financegym.archive_failure export runs/benchmark_400_public.jsonl runs/financegym-submission
```

## Prepare the methodology paper and local email

Generate the public-facing paper from the verified 400-row export. The note reports the actual configuration and the independently measured results. The code repository can stay private. Once the paper has a public URL, use the submitter's email address, organization and submission date to generate a local email draft with the exact JSONL attached.

```sh
.venv/bin/python -B -m evaluations.financegym.submission paper runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.submission draft runs/benchmark_400_public.jsonl runs/financegym-submission \
  --contact "$SUBMITTER_EMAIL" --org "$SUBMITTER_ORGANIZATION" \
  --published-paper "$PUBLIC_PAPER_URL" --submission-date "$SUBMISSION_DATE"
```

The private evidence archive stores the original and published research, dated passages, model usage, and hashes of the operator's HTTP receipts. The verifier rebuilds all 400 submitted reports with the network disabled. It reconciles each question's document count, elapsed time, model charges and outstanding reservations across its investigations. It also counts reports by the source types their published paragraphs cite, including reports supported solely by dated encyclopedia revisions.

```sh
.venv/bin/python -B -m evaluations.financegym.evidence seal runs/benchmark_400_public.jsonl runs/financegym-submission
.venv/bin/python -B -m evaluations.financegym.evidence verify runs/benchmark_400_public.jsonl
```
