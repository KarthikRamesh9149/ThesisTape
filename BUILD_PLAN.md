# ThesisTape — build plan

19 November 2026 · Proposed implementation

**ThesisTape is a multi-agent system for point-in-time investment research.** The analyst enters a question, an as-of date and a spending limit. The planned output is a one-page investment memo with a source-linked evidence appendix, calculations, the strongest counterargument and a testable outlook. The interactive product and the FinanceGym runner will use the same research engine.

**Success on the benchmark** means a maintainer-scored result on all 400 questions strictly above the leading full-run Overall score when the result posts. On 19 November the mark was **44.9 Overall**, with **63.8 pre-cutoff** and **15.7 post-cutoff**. Our proposed development target is **50.0**. Among configurations with comparable research quality, select the lowest measured cost. The benchmark materials are released for noncommercial use. [FinanceGym leaderboard](https://financegym.github.io/#leaderboard) · [Benchmark license](https://financegym.github.io/#license)

[Architecture diagram](ThesisTape-Architecture.png) · [Model routing and architecture PDF](ThesisTape-Architecture.pdf)

## Who directs the research

**GPT-6.2 Sol is the main orchestrator and the thesis author.** It owns the plan from intake to a reviewable memo. On routine questions the controller can start the standard assignments directly. On a question spanning multiple issuers, conflicting metrics or a consequential forecast, Sol identifies the subquestions, workers and evidence each answer requires. Workers return evidence to the lead through the case record.

| Role | Responsibility |
| --- | --- |
| Code controller | Validate each proposed action, dispatch work, enforce the immutable cutoff, reserve spend, and checkpoint results. It alone decides whether the final report has passed the required checks. |
| Two Luna workers | One extracts figures and terms. The other starts from the original question and independently searches for contrary evidence. Both return short passages with source locations. |
| Terra visual reader | Inspect a page or chart when its structure affects a material figure. Retain the relevant headers, units and footnotes with the extracted value. |
| Sol forecast specialist | Give a horizon, causal driver, base case, alternative and observable evidence that would change the view. Use historical comparisons available by the cutoff where they fit. |
| Sol lead and separate Sol reviewer | The lead synthesises the report. A reviewer in a fresh context tests material claims against original passages, calculations and contrary evidence. Revised claims go back through review. |
| Astra specialist | Take over a forecast when plausible assumptions reverse the conclusion, or adjudicate a material disagreement after a source check. If the underlying fact is missing, the lead records the gap. |

Sol can request evidence, commission a worker, escalate, draft or finish with gaps. Code rejects an unaffordable or unauthorised transition. Routine handoffs incur no model call. Start with one targeted repair and one possible Astra pass per case, then test whether more depth improves difficult cases enough to pay for it. [OpenAI Docs model catalog](https://developers.openai.com/api/docs/models)

## Implementation choices

Start with one Python 3.12 application. Use the OpenAI Responses API with typed tool calls and Pydantic schemas. Store run state and the budget ledger in SQLite, search admitted document passages with SQLite FTS5, and keep original document bytes in a content-addressed local directory. A CLI runs evaluation jobs. A small FastAPI web view gives the analyst the same engine and evidence. Add semantic retrieval only if the first retrieval tests show that full-text search misses relevant passages. [OpenAI Docs structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) · [SQLite FTS5](https://www.sqlite.org/fts5.html)

| Record | Fields the implementation must preserve |
| --- | --- |
| Case and run | Original question, cutoff, budget, case and run IDs, corpus version, model and prompt configuration |
| Assignment and result | Responsible worker, subquestion, dependencies, evidence required, status, attempt, provider request ID and actual usage |
| Evidence | Original URL, publication time, version available by the cutoff, immutable content hash, page or section, exact passage or table cells |
| Claim and review | Claim type, supporting and contrary source IDs, financial units and fiscal period, calculation inputs, reviewer verdict, unresolved gap |
| Report | Reviewed claim versions, cited memo, outlook and appendix, total cost, elapsed time, completeness and export status |

## Build sequence

### 1. Establish one real case and its acceptance test

Use Disney’s Hulu acquisition with a cutoff of **2 November 2023**. Ask what Disney expected to pay for NBCUniversal’s stake and what could change the final price. Disney’s contemporary filing gives an expected initial payment of about **USD 8.61 billion** and describes the appraisal. The June 2025 filing records the outcome. Keep that later filing and the answer key in an evaluator-only directory. [November 2023 Form 8-K](https://www.sec.gov/Archives/edgar/data/1744489/000174448923000206/dis-20231101.htm) · [EDGAR publication record](https://www.sec.gov/Archives/edgar/data/1744489/000174448923000206/0001744489-23-000206-index.htm) · [June 2025 outcome](https://www.sec.gov/Archives/edgar/data/1744489/000174448925000126/dis-20250609.htm)

**Completion evidence.** A versioned case manifest, source hashes, a separately stored scoring sheet and repeatable import. The scoring sheet distinguishes expected from paid, the stake price from the whole-company valuation floor, and the known terms from the unresolved appraisal. This milestone can run locally without model inference.

### 2. Build the point-in-time retrieval gateway

Give every model a single search interface. The controller binds `max_date` to the task cutoff on **every** search. Check publication date and document version before any title, snippet, table or image reaches an agent. Apply the same check on direct reads, redirects, cache hits and follow-up requests. Reject a mutable page if its eligible historical version cannot be established.

Start with accession-specific EDGAR filings. Extend coverage to dated international issuer and regulator releases, original financial reporting and appropriately licensed archived news. For revised macroeconomic data, use the vintage available on the task date. FinanceGym’s original retrieval corpus is not distributed, so record our sources and their coverage. [FinanceGym retrieval protocol](https://financegym.github.io/#submit) · [EDGAR data](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) · [Federal Reserve vintage parameters](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)

**Completion evidence.** The Disney passage is retrievable and cited. Tests cover a later amendment, an old event reported after the cutoff, an ambiguous timestamp, a cached later version and a known future URL. No future or undated content appears in the model-visible trace. Measure retrieval of manually marked passages across US, international, macro and table-heavy development cases.

**Measured on 19 November 2026.** The shared gateway indexes 78 passages across eight publisher editions. It retrieved all 14 marked passages within the first five results on 12 development questions. The first result was marked on eight questions. All 60 returned citation links reopened the intended passage. The evaluator added the real 2025 Disney filing, warmed the later cache, then reran the 2023 case. Its results, ordering and model-visible trace matched the run without the later filing. [Retrieval results](evaluations/retrieval/results.json) · [Sources and reproduction commands](README.md#search-the-point-in-time-archive)

### 3. Deliver the first working report with one Sol agent

Run a question through the gateway, a decimal calculator, structured evidence extraction and a Markdown report renderer. Record the prompt version, sources, tool calls, provider-reported token usage and metered tool charges. Calculate cost from the applicable rates and mark it as estimated until reconciled with billing. The controller reserves the allowed spend before dispatch. Replay saved outputs offline.

**Completion evidence.** A real provider run produces a cited Disney memo with the expected payment, the appraisal condition and a clearly marked unknown. A reviewer can open each material figure at its source. Offline replay reproduces the saved report with zero new provider calls. Record the single-agent score, latency and spend as the first baseline.

**Measured on 19 November 2026.** Starting with the Disney question and cutoff alone, Sol searched the shared archive and opened three paragraphs of Item 8.01. The two paragraphs it cited supported all three rubric points. The run took 18.188 seconds and used an estimated USD 0.016218. The supplied-filing control also earned 3/3, in 11.149 seconds at USD 0.010362. Both memos replayed byte for byte offline. Adding the 2025 filing and warming its cache left all three verified gateway request fingerprints unchanged. [Gateway memo](evaluations/disney-sol-gateway/memo.md) · [Rubric and usage comparison](evaluations/disney-sol-gateway/evaluation.md)

### 4. Add the main orchestrator and specialist routes

Reuse the Step 3 Sol analyst as the lead. Add the two Luna workers, the forecast specialist and an independent review context. Add Terra and Astra as conditional routes. Evidence and review findings return to Sol when a research decision is needed. Code checks dates, numeric operations, scale, currencies and claim-to-source links. The reviewer checks whether the cited passage supports the meaning of the claim.

**Completion evidence.** Real development cases exercise the standard route, a difficult table, a forecast with competing explanations and a material dispute. Demonstrate a targeted repair, a qualified answer when evidence is missing, and re-review after a material edit. Verify live model access and schema handling for every route before counting it as working.

**First specialist route verified on 19 November 2026.** Sol directed two independent Luna searches, compared grounded evidence and wrote a cited report. On Disney the single agent and the team scored 3/3 at USD 0.016218 and USD 0.0216475 respectively. Both initial Tradeweb reports scored 2/3. After a shared report-format fix, both scored 3/3 at USD 0.008032 and USD 0.0117409. Moving the same question to one second before the Tradeweb amendment triggered a live targeted lookup and a qualified report. These are local development results. [Scoring and receipts](evaluations/first-team/evaluation.md)

**Independent citation review verified on 19 November 2026.** On Toyota’s FY2024 outlook, a fresh Sol reviewer accepted the original memo, identified a deliberately incorrect metric attached to a real figure and citation, and approved the lead’s correction after reopening the original table. The original review cost USD 0.003772. Detection, correction and re-review of the changed claim cost USD 0.011550 in total. [Paired test, reports and costs](evaluations/citation_review/evaluation.md)

**Forecast specialist verified on 19 November 2026.** On NVIDIA’s Q2 FY2024 revenue outlook, the specialist added a grounded prior-quarter comparison. The team with the specialist earned 6/6 against a blinded cutoff-only rubric and the control earned 5/6. Both predicted revenue within guidance. The actual result was above guidance, earning 0/1 for both. The specialist route added USD 0.012394 across the calls used for the graded reports. The routing decision remains opt-in. [Frozen comparison, costs and receipts](evaluations/forecast_specialist/evaluation.md)

### 5. Make the workflow usable and resumable

Provide an analyst screen with question, cutoff and spending limit. Show worker status, the lead’s routing decisions, spend and report. Selecting a material claim opens the original evidence and its locator. The CLI and screen read the same saved run.

Use atomic reservations for concurrent calls, stable assignment IDs and bounded retries. Persist provider request IDs and completed results. After a timeout, reconcile a known request where possible and reserve for any uncertain charge before retrying. Leave budget for review and export. On insufficient evidence or funds, return the supported findings and explain the remaining gap.

**Completion evidence.** Complete a case in the interface, inspect its cited passage, interrupt and resume a run, and compare the exported report with the CLI result. A duplicate dispatch cannot double-book the same assignment. Exhausting the cap launches no further paid work.

### 6. Test whether the extra agents earn their cost

Start with **12 development cases and 24 additional blind cases** from different events outside the official 400. Cover multiple issuers, markets, document layouts and forecast horizons. Use real archived material. Write the factual and forward-outcome scoring criteria before comparing outputs. Keep later outcomes accessible only to evaluators. Small artificial inputs are sufficient for timeout and budget tests.

On the development cases compare **single Sol**, **the same workflow with Sol in every model role**, **the mixed OpenAI team** and **single Astra**. Give them identical cases, permitted corpus, tools and maximum dollar budget. Switch adaptive planning and forecast specialisation off in separate trials. Lock the best mixed configuration before comparing it with the strongest single-agent baseline on the blind cases.

**Decision evidence.** Report paired differences and uncertainty, separate factual and forecast scores, citation support, qualified or incomplete reports, and mean and 95th-percentile cost and latency. Have an analyst review a blinded sample and record correction time. Before opening the blind set, define the smallest quality loss the analyst would accept for a lower cost. Advance the mixed team if its traces show zero cutoff violations and it either improves quality at the same spending limit or meets that quality tolerance at lower measured cost. If the result is inconclusive or leads to a redesign, confirm on fresh cases. FinanceGym’s withheld rubric determines the official score.

**Gate for the official run.** Require a passed date and citation audit, paired results from the independent blind cases, and a cost projection based on measured usage. The projection must cover the full run and leave room for failures and retries within the remaining API credits.

### 7. Freeze and run the official 400

Pin the code revision, dependency lock, model snapshots where available, returned model IDs, prompts, retrieval configuration, source manifest and benchmark-file hash. Replace illustrative costs with the pilot’s measured spend and set an authorised run ceiling. Run only the selected candidate over the official set. Preserve the original question and cutoff exactly when resuming or exporting.

**Submission evidence.** Validate exactly one cited report per original task, all 400 present, every search cutoff recorded, all costs reconciled and unresolved cases included. Prepare `answers.jsonl` with `question`, `cutoff` and `report`, plus the system metadata and public repository or paper link required by the maintainers. Publish or send the package with the user’s authorisation. Record the returned Overall score, both component scores, uncertainty and the leading comparable entry at grading. [Official data](https://huggingface.co/datasets/Rujun/FinanceGym) · [Submission instructions](https://financegym.github.io/#submit)

## Spending plan

The project API ceiling is **USD 100** across development, verification and the official benchmark run. Track spend cumulatively. Every USD 0.10 of average cost per case corresponds to USD 40 for the 400 reports.

At the checked Standard rates, the architecture’s example uses 12,000 input and 1,000 billed output tokens across Luna calls, and 24,000 input and 5,000 billed output tokens across all Sol calls, including any planning. That totals **USD 0.0997**. An additional Astra pass at 4,000 input and 1,000 output plus a Sol recheck at 2,000 input and 500 output adds **USD 0.0810**. [OpenAI Docs pricing](https://developers.openai.com/api/docs/pricing) · [Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna)

**Illustration for the final run.** If all 400 cases used the base allowance and 80 needed that additional pass, text-model usage would be **400 × USD 0.0997 + 80 × USD 0.0810 = USD 46.36**. Search, visual inputs, cache charges, storage, development comparisons and retries are separate. The first live cases establish the actual token distribution. Batch independent offline calls when turnaround permits and apply discounts only to eligible usage. [Batch pricing and turnaround](https://developers.openai.com/api/docs/guides/batch)

**Next implementation checkpoint.** Continue Step 4 with the conditional Terra visual reader. Select a real financial table whose layout makes a material figure ambiguous in extracted text. Freeze its source page, cutoff and scoring criteria. Compare text-only research with a targeted page read on the same evidence and budget. Verify the figure against its row, column, unit and footnotes and record the incremental cost. The material-dispute route follows within Step 4.
