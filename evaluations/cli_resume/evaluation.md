# Can an interrupted research run finish without paying twice?

Use the July 2022 Twitter offer-price case, the conditional dispute route and the existing USD 0.08 cap. The expected result is a reviewed memo that cites Twitter’s board for the USD 54.20 per-share price. The paid stages are the Sol author and the independent Sol reviewer. [Original case](../../cases/twitter-merger-2022-07-11/agent/price-only.json) · [Board statement](https://www.sec.gov/Archives/edgar/data/1418091/000119312522191300/d370677dex991.htm)

## Acceptance criteria

1. Interrupt the CLI with SIGINT during the first live generation. Let the active request finish. A competing CLI must fail to take ownership. The paused ledger must contain one settled author call and no exported memo.
2. Resume the same directory with the same spending limit. Kill the process with SIGKILL at the moment the real reviewer receipt is durable and the ledger still shows its reservation. The test harness selects the moment of interruption. The production client sends both requests.
3. Resume with networking disabled. The controller must reconcile the reviewer’s receipt, retain the author’s original output and export the reviewed memo. Repeating resume must preserve the report, ledger and operator identifiers byte for byte.
4. Count HTTP requests independently of the application ledger. Require exactly one generation request and one token-count request per paid assignment, two distinct provider response IDs, and zero HTTP requests on offline recovery or repeat.
5. Copy the first in-flight snapshot into a separate offline directory without a provider receipt. Resume must retain the entire reservation and stop before another request. This copy measures the unknown-outcome branch and incurs no extra cost.

The portable proof includes the ledger at each interruption, the released memo, hashes of the transport requests and the final usage record. Raw provider identifiers and private recovery receipts stay in the operator’s run directory. [Trial and offline verifier](experiment.py)
