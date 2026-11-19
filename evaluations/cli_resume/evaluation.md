# Can an interrupted research run finish without paying twice?

Use the July 2022 Twitter offer-price case, the conditional dispute route and the existing USD 0.08 cap. The expected result is a reviewed memo that cites Twitter’s board for the USD 54.20 per-share price. The paid stages are the Sol author and the independent Sol reviewer. [Original case](../../cases/twitter-merger-2022-07-11/agent/price-only.json) · [Board statement](https://www.sec.gov/Archives/edgar/data/1418091/000119312522191300/d370677dex991.htm)

## Acceptance criteria

1. Interrupt the CLI with SIGINT during the first live generation. Let the active request finish. A competing CLI must fail to take ownership. The paused ledger must contain one settled author call and no exported memo.
2. Resume the same directory with the same spending limit. Kill the process with SIGKILL at the moment the real reviewer receipt is durable and the ledger still shows its reservation. The test harness selects the moment of interruption. The production client sends both requests.
3. Resume with networking disabled. The controller must reconcile the reviewer’s receipt, retain the author’s original output and export the reviewed memo. Repeating resume must preserve the report, ledger and operator identifiers byte for byte.
4. Count HTTP requests independently of the application ledger. Require exactly one generation request and one token-count request per paid assignment, two distinct provider response IDs, and zero HTTP requests on offline recovery or repeat.
5. Copy the first in-flight snapshot into a separate offline directory without a provider receipt. Resume must retain the entire reservation and stop before another request. This copy measures the unknown-outcome branch and incurs no extra cost.

The portable proof includes the ledger at each interruption, the released memo, hashes of the transport requests and the final usage record. Raw provider identifiers and private recovery receipts stay in the operator’s run directory. [Trial and offline verifier](experiment.py)

## Observed on 19 November 2026

The live trial ran against commit `56f1a05`. The CLI produced the reviewed USD 54.20-per-share memo after two interruptions. An independent HTTP trace recorded two generation requests, and their fingerprints matched the author and reviewer reservations. The two responses had distinct provider IDs. [Cited memo](proof/memo.md) · [Transport and process observations](proof/observations.json)

| Point in the same run | Estimated usage | Outstanding reservation | Observed outcome |
| --- | ---: | ---: | --- |
| SIGINT during author generation | USD 0 | USD 0.0296275 | The running process retained ownership while the author finished. |
| Author saved, CLI paused | USD 0.004992 | USD 0 | The first resume reused the original author response. |
| SIGKILL before reviewer settlement | USD 0.004992 | USD 0.0262575 | The reviewer’s private receipt had reached disk. |
| Offline recovery and repeat | USD 0.011814 | USD 0 | Recovery settled USD 0.006822 for the reviewer. Repeat preserved the report, run record and provider IDs byte for byte. |

The separate missing-receipt check used a copy of the first in-flight snapshot. It retained USD 0.0296275 and made zero HTTP requests. This is the recovery limit when a process dies before its provider response reaches durable storage. Estimated project usage reached USD 0.5462619 of the USD 100 budget. [Pending snapshot](proof/before-recovery.json) · [Unknown outcome](proof/unconfirmed.json) · [Costs and report hash](proof/results.json)

## Reproduce

From the repository root with the [research dependencies](../../README.md#run-the-research-team) installed, these commands verify the snapshots, transport hashes, reviewed release and costs offline.

```sh
.venv/bin/python -B -m evaluations.cli_resume.experiment check
.venv/bin/python -B -m scripts.research_team replay --run-dir evaluations/cli_resume/proof
.venv/bin/python -B -m unittest discover -s tests -p 'test_cli_resume.py' -v
```

The paid trial command below requires a fresh destination, the project API key and a platform with SIGINT and SIGKILL.

```sh
.venv/bin/python -B -m evaluations.cli_resume.experiment live --output-dir runs/cli-resume-live
```
