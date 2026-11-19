# Paired financial research evaluation, version 1

**Question.** Under the same USD 0.20 maximum per report, does the mixed team deliver better supported research, or equivalent quality at lower measured cost, than the stronger single agent? This is a local test of the workflow. FinanceGym uses its own tasks, search setting and grading.

## Population and custody

Use the 36 registered historical events. Six strata cover corporate actions, financial statements, company guidance, monetary policy, economic revisions and Treasury auctions. For each stratum, sort the six event IDs by SHA-256 of the UTF-8 string `split_seed/event_id`. Assign the first two to development and the remaining four to blind evaluation. One event contributes one question. No primary document is reused between events. The four forward-looking strata have 8 development and 16 blind outcomes. Forecast horizons range from the next auction to the next annual earnings release.

Use the original publisher PDFs. The cutoff is noon UTC on the day following the newest input release. This admits the complete publication day in the publisher’s timezone. Check the printed release date or the publisher listing linking that PDF, and check the PDF edition timestamps. The listing for one regulator report resolves an incorrect cover year. Publication-proof HTML is for evaluators only. Later outcomes use the specified vintage, even if newer results or revisions are available today.

Development and blind grading materials have separate archives. Each contains an as-of factual key and a separate outcomes file. Only the controller may read the split manifests. The curator assembled the retrospective event registry and keys. Custodians with repository access can read the evaluator archives. For development, give each research process only its exported event packet in a fresh working directory and a scoped retrieval endpoint. The evaluation host owns the repository, answer keys and later sources. Disable general web, filesystem and cross-run memory for research workers. Disjoint events and dated evidence cannot exclude prior model knowledge. A historical fact recalled without eligible evidence earns no factual credit.

The official public file is pinned by URL, revision and SHA-256 in [protocol.json](protocol.json). Check every local question against all 400 using the substantive question before common answer instructions, normalized question equality, token-trigram Jaccard similarity (review threshold 0.20), and issuer/event aliases. Inspect every flagged question against the local event and period, and retain the decisions with the evaluator. All local cutoffs precede the earliest official cutoff. Existing ThesisTape showcase events are excluded.

## Controlled comparison

| Development arm | Configuration | Reports |
| --- | --- | ---: |
| Single Sol | One Sol agent researches, calculates and writes. | 12 |
| All-Sol team | Same routing, roles, prompts and tool schemas as the mixed team, with Sol in every model role. | 12 |
| Mixed | Adaptive Sol lead, economical evidence workers, conditional visual reading, forecasting and dispute review according to the frozen routing policy. | 12 |
| Single Astra | The single-Sol configuration with Astra in its place. | 12 |
| Mixed, fixed plan | Skip model-based planning and replanning. Send the fixed evidence and contrary-evidence assignments, use the forecast role on forward cases, then draft and review. Keep the common visual, dispute and budget checks. | 12 |
| Mixed, forecast specialist off | Use the mixed policy without a separate forecast analyst on the 8 forward cases. Reuse the original mixed reports on the four cases where that role is inapplicable. | 8 |

Use the repository’s Sol, Astra, Luna and Terra identifiers. Before any development generation, commit a run manifest with exact model IDs or available snapshots, prompt hashes, tool versions, planner limits, retry policy, rate card, token limits, and code and corpus revisions. Use the same publisher editions, search, page rendering, citations, calculator and dollar ceiling in all arms. A single model may inspect the same page images. Each arm gets one normal attempt per event, including its own affordable recovery within that cap. Reuse only the four specified, byte-identical results. Prevent a hidden cross-arm response cache from answering a new case-arm assignment.

Block by event. Order events by SHA-256 of `order_seed/split/event_id` and order arms within each event by SHA-256 of `order_seed/split/event_id/arm_id`. Use the decimal seed from the JSON and literal split strings `development` and `blind`. Start each run cold at the application level and record provider cache usage, returned model IDs, rate-limit waits and terminal status. Reconcile any uncertain billable attempt before an automatic retry. Stop a run if the remaining reservation cannot cover it. Include interrupted and budget-exhausted cases in the denominator.

Before unsealing the blind inputs, select the eligible mixed configuration and the stronger of the two single agents by highest development quality, then lowest mean cost, then lowest mean latency, then ascending arm ID. An arm with a cutoff violation is ineligible. The all-Sol team is a diagnostic control. If either finalist class has no eligible arm, stop the comparison. Commit both finalists, code, prompts, models, settings, development results and this freeze digest. No prompt changes, case replacements or selective retries after that lock. On blind cases run only the two finalists, once each, and commit all 48 report hashes, forecasts, traces, cost receipts and terminal statuses before the factual graders receive the blind as-of keys. Withhold the separate outcome file and later PDFs from those graders until they commit factual and reasoning scores.

## Scoring

Assign study aliases to reports and shuffle their order per event using the order seed before a grader sees them. Hide configuration, spend and latency. Score factual and reasoning criteria with the as-of packet first. Lock those scores before revealing the outcome. For audit, retain the exact claim, source, one-based PDF page, cited passage, point awarded and reason. Equivalent formulations, units and valid rounding earn the same credit. A materially contradictory statement cancels credit for the affected criterion.

- **Factual F** is the mean fraction of the three binary, case-specific criteria satisfied per event. Each point requires the right period, unit, interpretation and a cutoff-eligible supporting passage. Derived numbers need traceable operands and correct arithmetic.
- **Reasoning R** is the mean of two binary points per event. Award one for a decision-relevant inference with a specific mechanism grounded in as-of evidence. Award the second for a material alternative or limitation, with a concrete observable signal that could change the interpretation. For a forecast require the signal to apply to the stated horizon. Repeating the same source claim for both points is insufficient.
- **Forward O** is the fraction of forward cases whose single committed base-case class matches the later publisher observation. Use the numeric target, unit, comparison vintage and inclusive band in the input. The evaluator key binds both numeric operands to their original pages. `within` means equality when tolerance is zero. A qualified or uncertain forecast may earn credit if it still commits to one direction. Missing, ambiguous or multiple base cases earn zero. Grade the direction independently of prose quality.
- **Primary quality Q** is 0.50 F + 0.25 R + 0.25 O. Compute F and R across every assigned case and O only across forward cases. Missing reports score zero on each applicable component. For qualified or incomplete reports grade only supported criteria. Keep all assigned cases. Never use a later result to decide whether the pre-cutoff investment reasoning was sensible.

Count a cutoff violation if an agent obtains post-cutoff evidence or evaluator-only content, even when omitted from the final memo. Audit source/tool traces and date manifests. Record unsupported retrospective statements separately. Report the citation-support fraction (entailed, dated material claims divided by all material claims), ungradable empty reports, citation denominators, claim counts, qualified/incomplete counts, and any unresolved costs. Report words or cumulative calls as diagnostics only.

## Decision and budget

For each finalist publish F, R, O, Q, coverage, and mean and nearest-rank 95th-percentile per-case USD cost and wall time. Include retries, shared infrastructure tool fees attributable to a run, cached-token charges, and elapsed recovery time. Disclose unpriced overhead separately. A missing receipt keeps its reservation and blocks a savings claim until reconciled. Show each paired difference (mixed minus single) and a paired interval. Resample case pairs with replacement **within each of the six strata**, four draws per stratum on blind data, preserving the two reports and all their subscores. Use 20,000 replicates, six strata in sorted order, sorted case IDs per stratum, Python 3.12 `random.Random(bootstrap_seed).choices` and nearest-rank 5th and 95th percentiles. Recompute the complete quality formula and cost means each draw. Report the middle 90% interval and its one-sided 95% bounds. Also report the six leave-one-stratum-out estimates and how conclusions change when each recurring issuer/publisher is omitted, with undefined components marked unavailable.

Apply the blind rule once. Require zero observed cutoff violations in both finalists. Advance if the 5th percentile of paired quality improvement is strictly above zero at the common cap, or if it is at least zero and the 95th percentile of the paired mean cost difference is strictly below zero. The preregistered acceptable quality loss for savings is **zero** on the 0–1 scale. This is a conservative project choice, pending any separate study with analyst-elicited preferences. With only 24 blind pairs and repeated publishers, an inconclusive result is plausible. A redesigned system requires fresh confirmation cases. This study cannot establish a leaderboard rank.

Select the first two blind events in each stratum by the same seeded event order for the usability review. A financial analyst edits both masked reports to a usable standard against the as-of evidence. Alternate which arm is read first within each stratum. Record active correction minutes, substantive corrections, missing evidence, and whether each report was usable as delivered. Report 12 paired times and their median difference. Disclose the repeat-exposure effect and grader identity/qualifications. Machine checks do not substitute for this review.

The 68 development and 48 blind assignments have a combined maximum of **USD 23.20**. Charge every attempt, retry and paid tool to its case-arm ceiling. The work in this freeze requires zero paid calls. Estimated prior project use is USD 0.5785677. Reserving USD 60 for a possible official run leaves USD 16.2214323 for other work if all 116 comparison assignments hit their ceilings. The USD 60 is a planning allocation. Set any official-run decision from reconciled pilot consumption and the remaining USD 100 project cap.

## Reproduce the freeze

Run from the repository root with the pinned retrieval dependencies installed. The first command verifies every original PDF, cutoff, page citation, forecast comparator, split and manifest offline. The export command creates one research packet in a fresh directory. Neither command calls a model. The evaluator archives stay on the grading host.

```sh
.venv/bin/python -B -m evaluations.paired.corpus verify
.venv/bin/python -B -m evaluations.paired.corpus list-dev
.venv/bin/python -B -m evaluations.paired.corpus export-dev alleghany-merger-2022 --output runs/paired/example
```

To repeat the FinanceGym exclusion screen, obtain the public JSONL at the revision in the protocol and pass its local path. The verifier checks the pinned hash before using its contents. The comparison never supplies this file to the agents.

```sh
.venv/bin/python -B -m evaluations.paired.corpus verify --official /path/to/benchmark_400_public.jsonl
```
