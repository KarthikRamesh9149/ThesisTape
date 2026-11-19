# ThesisTape

ThesisTape researches a financial question using the sources in its archive that were public by a chosen cutoff. An analyst can set a spending limit, watch the work, and open the original passage behind a claim in the released memo.

GPT-6.2 Sol can research a filing alone or lead two GPT-6 Luna workers. The team and the single analyst each earned 3/3 on the Disney/Hulu and Tradeweb development cases. The single analyst was cheaper on both. Moving the Tradeweb cutoff to one second before its amendment led the team to request another search and qualify its answer. [Results and costs](evaluations/first-team/evaluation.md) · [Disney memo](evaluations/first-team/disney-team/memo.md) · [Tradeweb memo](evaluations/first-team/tradeweb-team/memo.md)

[Build plan and validation milestones](BUILD_PLAN.md) · [Architecture and model costs (PDF)](ThesisTape-Architecture.pdf)

The analyst team released nine of 12 reports on the registered development set. Its local quality score was 55.2%, compared with 81.6% for single Astra and 80.6% for single Sol. All three had a USD 0.30 limit per case. Three interrupted team attempts earned zero. The 24 held-out cases compare the selected team with Astra. [Development results](evaluations/paired/revisions/analyst_product/finalists.json) · [Evaluation protocol](evaluations/paired/protocol.md)

The desk also offers additional checks on financial figures. On Nintendo, the team recovered the prior-year profit from the original PDF, calculated the decline and the comparison band, and correctly forecast the direction. It spent USD 0.351676 against a USD 0.45 limit. On Canon it calculated the remaining profit needed to meet guidance and missed the forecast direction. [Nintendo memo](evaluations/paired/revisions/quantitative_trials_v3/nintendo.md) · [Canon memo](evaluations/paired/revisions/quantitative_trials_v3/canon.md) · [Scoring and costs](evaluations/paired/revisions/quantitative_trials_v3/evaluation.md)

On a public-source question about TSMC Arizona, the analyst desk retrieved the grant, proposed loans and payment conditions from the archived government announcement. The browser-exported report cost USD 0.349358. The evaluation includes an earlier draft that omitted the financing amounts. [TSMC financing memo and evidence](evaluations/open_research/tsmc/evaluation.md)

Replaying the same responses with revised evidence checks produced the single-Astra memo at its historical cost of USD 0.141616. The five other configurations need new responses to finish. New assignments use a common USD 0.30 ceiling. [Replay and cap decision](evaluations/paired/revisions/grounding_v2/evaluation.md)

![ThesisTape research workflow showing the lead orchestrator, evidence workers, independent review and final report.](ThesisTape-Architecture.png)

## Ask a historical finance question

Install the [Python 3.12 research dependencies](#run-the-research-team), start the local analyst desk and choose **New research → Public historical sources**. Enter the question, the last eligible UTC date and a USD spending limit. The report links each cited claim to the dated passage. Run activity shows the participating models, settled cost and remaining reservations.

```sh
.venv/bin/python -B -m scripts.analyst_desk --port 8765
```

The live ECB trial cost USD 0.2052854 and its download reproduced offline. [ECB report and browser checks](evaluations/open_research/ecb/evaluation.md) · [Boeing acquisition report](evaluations/open_research/boeing/reviewed.md)

Open [the local desk](http://127.0.0.1:8765/). **Saved runs** opens real reports, including their call history, with no API key or model usage. Select a numbered citation to read its dated passage and locator. **Open original** links to the publisher. **Export .md** downloads the same bytes as the CLI.

**New research** takes a question, source collection, UTC cutoff and per-run USD limit. A date without a time includes that entire UTC day. Live runs use the project’s `OPENAI_API_KEY`. **Run activity** shows dispatched assignments, routing decisions, estimated charges from returned token usage and outstanding reservations. **Pause run** lets in-flight requests settle. **Resume run** continues under the original cutoff and cap. The public archive accepts open research questions. Three curated collections cover the dated research archive, Twitter merger filings and NVIDIA earnings filings. Questions outside their coverage can produce a qualified memo.

The desk binds to loopback and saves each intake to its own folder under `runs/desk/`. Use `--runs-dir DIRECTORY` to open a different parent folder of CLI runs. The CLI accepts the same free-form intake with `run --question "…" --cutoff 2023-10-27 --collection archive --budget-usd 0.10 --output-dir runs/desk/my-run`. Replay a completed run offline with `python -m scripts.research_team replay --run-dir DIRECTORY`. [Browser verification and measured costs](evaluations/analyst_desk/evaluation.md)

## Reproduce the Disney/Hulu case

From the repository root, run these commands with Python 3.9 or later.

```sh
python3 scripts/disney_hulu_case.py verify
case_workspace="$(mktemp -d)"
python3 scripts/disney_hulu_case.py import --output-dir "$case_workspace/disney-hulu"
python3 scripts/disney_hulu_case.py verify --case-dir "$case_workspace/disney-hulu"
python3 -B -m unittest discover -s tests -p 'test_disney_hulu_case.py' -v
```

The two files in `agent/` form the supplied-filing control. The gateway run takes its question and cutoff from the case and discovers its evidence through search. The evaluator uses the separate `evaluator/` directory to grade the answer and check the dated sources. Import and verification run offline.

The importer reads the saved filings and indexes from the repository by default. Pass `--filings-dir DIR` to read the same four HTML files by their saved names from a single directory. The manifests and scoring sheet come from the versioned case. Reusing a destination with identical files succeeds without rewriting it.

The stored Form 8-K documents are the original HTML payloads inside EDGAR’s complete submissions. The direct HTML responses included an archive script. The saved payloads match EDGAR’s listed sizes and the rest of each direct response byte for byte. The evaluator manifest links to both complete submissions and the index snapshots.

## Run the research team

Sol assigns a filings search and an independent search for contrary evidence. The Luna workers run in parallel. Each uses its own cutoff-bound archive session, opens passages and selects exact source spans. Sol compares their findings and can request one targeted follow-up before writing. The controller verifies citations, runs the Disney calculation and reserves each call against the same run budget, including any calls still in flight.

Install the dependencies from the repository root with Python 3.12. The saved team reports replay offline without an API key.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-baseline.txt -r requirements-retrieval.txt
.venv/bin/python -B -m scripts.research_team replay --run-dir evaluations/first-team/disney-team > /tmp/thesistape-team.md
cmp /tmp/thesistape-team.md evaluations/first-team/disney-team/memo.md
.venv/bin/python -B -m unittest discover -s tests -v
```

For a live run, set `OPENAI_API_KEY` in the ignored project `.env.local` or the shell. Use a fresh output directory for each run. The Tradeweb question is identical at the two cutoffs.

```sh
.venv/bin/python -B -m scripts.research_team run --case disney --route team --budget-usd 0.05 --output-dir runs/disney-team-new
.venv/bin/python -B -m scripts.research_team run --case tradeweb --route team --budget-usd 0.05 --output-dir runs/tradeweb-team-new
.venv/bin/python -B -m scripts.research_team run --case tradeweb --route single --budget-usd 0.05 --output-dir runs/tradeweb-single-new
.venv/bin/python -B -m scripts.research_team run --case tradeweb-before --route team --budget-usd 0.05 --output-dir runs/tradeweb-earlier-new
```

Each directory contains the memo and a portable `run.json` with assignments, actual search queries, selected excerpts, lead decisions, token usage, timings and costs. The separate `provider-ids.json` is an operator receipt kept under the ignored `runs/` directory. The earlier Tradeweb report lists the passages inspected when the archive could not support a figure. [Earlier report](evaluations/first-team/tradeweb-before/memo.md) · [First team runs and spend](evaluations/first-team/development-usage.json)

### Resume an interrupted research run

Press Ctrl+C once to pause the CLI. It finishes saving any provider responses already in flight and exits with status 130. Continue in the same directory. The saved case, cutoff, route and spending limit carry over.

```sh
.venv/bin/python -B -m scripts.research_team resume --run-dir runs/disney-team-new
```

Keep the entire operator run directory when moving unfinished work. It contains the private receipts used to reconcile a generation that returned just before a crash. A completed run can reproduce its memo offline from the portable `run.json`. If a dispatched request has no recoverable receipt, the CLI records `needs_reconciliation`, displays the amount still reserved and stops before sending another generation.

The Twitter price case survived both Ctrl+C and a hard process kill. The HTTP trace recorded one author generation and one reviewer generation. Offline recovery exported the cited memo, and repeating resume left the files byte-identical. [Live interruption proof and offline check](evaluations/cli_resume/evaluation.md)

### Run independent citation review

Use `--route reviewed` to send the team’s memo to a fresh Sol reviewer. It reopens the original passages at the case cutoff and assesses every material paragraph, any filing history and the Disney arithmetic. A material rejection goes to the lead for one correction. The controller exports the exact memo that passes review. If the revision still fails review, the verdict and drafts remain in the run record and the CLI exits with status 2. A search that yields no usable evidence returns a qualified notice listing the passages inspected.

On Toyota’s real FY2024 forecast table, the reviewer accepted the original and caught a deliberately changed metric with identical numbers and citations. The complete correction branch added USD 0.011550 to the shared draft. [Paired evaluation and replay instructions](evaluations/citation_review/evaluation.md) · [Reviewed memo](evaluations/citation_review/challenge/memo.md) · [Spend through citation review](evaluations/citation_review/usage.json)

```sh
.venv/bin/python -B -m evaluations.citation_review.experiment check
.venv/bin/python -B -m scripts.research_team run --case toyota --route reviewed --budget-usd 0.09 --output-dir runs/toyota-reviewed-new
```

### Run a forecast

Use `--route forecast` for a Sol specialist that gives the lead a base case, causal driver, alternative and observable revision signal. Each point refers to a dated source. The independent reviewer checks the forecast’s factual premises and whether the conclusion follows from them. The same question also runs through `--route reviewed` for comparison.

On NVIDIA’s Q2 FY2024 revenue, the specialist memo earned 6/6 and the reviewed control 5/6 for their cutoff-based analyses. Both predicted revenue within guidance, and NVIDIA reported revenue above the range. [Reports, missed forecast and costs](evaluations/forecast_specialist/evaluation.md) · [Cumulative spend](evaluations/forecast_specialist/usage.json)

```sh
.venv/bin/python -B -m evaluations.forecast_specialist.experiment check
.venv/bin/python -B -m scripts.research_team run --case nvidia --route forecast --budget-usd 0.12 --output-dir runs/nvidia-forecast-new
```

### Read a financial table

The conditional table route checks the requested row in a located, dated PDF. If extraction joins numeric cells, the row is ambiguous or Sol cannot resolve a field, Terra reads the registered page. A fresh Sol reviewer checks the report against that same page and the original notes. A clear row can complete with text alone.

On Toyota’s full-year buybacks, both routes scored 6/6. Visual confirmation added USD 0.012342. A separate live check of the adjacent row returned the correct value and skipped the image. [Reports, scores and replay](evaluations/visual_reader/evaluation.md) · [Cumulative spend](evaluations/visual_reader/usage.json)

```sh
.venv/bin/python -B -m evaluations.visual_reader.verify
.venv/bin/python -B -m scripts.research_team run --case toyota-buybacks --route visual --budget-usd 0.12 --output-dir runs/toyota-table-new
```

### Review a material dispute

Use `--route dispute` when opposing primary sources could change the answer. The independent Sol reviewer identifies the disputed claim and cites both sides. The controller reopens the originals at the cutoff and calls Astra once if enough budget remains for revision and review. A material change must pass fresh Sol review. The workflow can release a supported, qualified answer or withhold a conclusion it cannot support.

On the July 2022 Twitter merger dispute, both the reviewed Sol report and the conditional Astra report earned 6/6. Both explained why closing was still unresolved. Escalation added USD 0.025464. A separate question about the board’s USD 54.20-per-share price completed without Astra. [Comparison, source dates and receipts](evaluations/material_dispute/evaluation.md)

The first command reproduces the saved comparison offline. The second starts a new paid run.

```sh
.venv/bin/python -B -m evaluations.material_dispute.experiment check
.venv/bin/python -B -m scripts.research_team run --case twitter-deal --route dispute --budget-usd 0.20 --output-dir runs/twitter-dispute-new
```

## Run the single-Sol baseline

Sol chooses its search terms, inspects dated result previews and selects passages to open. It has up to two searches. Only the selected full passages go into its writing request. The controller checks the quoted evidence, reconciles the amounts with decimal arithmetic and builds citations from the opened sources.

Install both dependencies with Python 3.12. To reproduce the verified result offline, replay the saved provider responses through the archive.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-baseline.txt -r requirements-retrieval.txt
.venv/bin/python -B -m scripts.sol_baseline replay --run-dir evaluations/disney-sol-gateway > /tmp/thesistape-disney-replay.md
cmp evaluations/disney-sol-gateway/memo.md /tmp/thesistape-disney-replay.md
.venv/bin/python -B -m unittest discover -s tests -v
```

For a new live run, set `OPENAI_API_KEY` in the ignored project `.env.local` or in the shell and choose a fresh output directory.

```sh
.venv/bin/python -B -m scripts.sol_baseline run --retrieval gateway --budget-usd 0.05 --output-dir runs/disney-sol
.venv/bin/python -B -m scripts.sol_baseline replay --run-dir runs/disney-sol > runs/disney-sol/replayed.md
cmp runs/disney-sol/memo.md runs/disney-sol/replayed.md
```

`memo.md` is the analyst deliverable. The portable `run.json` records the model’s search and passage choices, source hashes, quoted evidence, request fingerprints, token usage, arithmetic and estimated spend. The exact provider identifiers are saved locally in `provider-ids.json`. Before each generation, the controller counts the input tokens and checks that the remaining run budget covers the maximum charge. An unconfirmed request retains its reservation. The cost estimate uses Standard rates. [OpenAI GPT-6.2 Sol pricing](https://developers.openai.com/api/docs/models/gpt-6.2-sol)

Use `--retrieval supplied` to run the original single-call control with the filing passages provided upfront. Both saved runs replay without a key. [Supplied-filing control](evaluations/disney-sol-gateway/supplied-control/memo.md)

## Search the point-in-time archive

The shared gateway searches eight historical editions. Each result carries its original URL, edition, publication date, availability in UTC, SHA-256, passage, locator and citation link. Install the PDF reader in the Python 3.12 environment above. Search, direct reads and evaluation run offline and require no API key.

```sh
.venv/bin/python -m pip install -r requirements-retrieval.txt
.venv/bin/python -m scripts.retrieval_gateway verify
.venv/bin/python -m scripts.retrieval_gateway search --as-of 2023-11-02 --limit 5 'What did Disney expect to pay NBCU for Hulu and what could an appraisal change?'
.venv/bin/python -m scripts.retrieval_gateway read --as-of 2023-11-02 --locator 'Item 8.01 Other Events. ¶1' 'https://www.sec.gov/Archives/edgar/data/1744489/000174448923000206/dis-20231101.htm'
.venv/bin/python -m scripts.retrieval_eval --check
.venv/bin/python -B -m unittest discover -s tests -v
```

The controller creates one session per cutoff and supplies its original `max_date` on every search and read. Calendar cutoffs include the full UTC day. An offset timestamp permits intraday cases. A publisher that gives only a local date is admitted at the end of that local day. An unverified publication time or version stays outside the index. Each case gets its own SQLite FTS5 index so later documents cannot affect ranking. A search returns the latest covered vintage for each release series. An explicit citation can still open an older edition that was public by the cutoff.

| Archive coverage | Passages available to the gateway |
| --- | --- |
| Disney and Tradeweb, US | Disney’s Hulu agreement in Item 8.01, plus Tradeweb’s original and amended earnings filings. EDGAR acceptance times distinguish the two Tradeweb versions issued on the same day. |
| Toyota, Japan | Pages 2–3 of the English FY2023 financial summary. Consolidated results, forecasts and cash flow tables retain their rows, headings, units and notes. |
| European Central Bank | The three-page September 2023 policy decision in the Banco de España archive. |
| US Bureau of Economic Analysis | Pages 1–2 of the advance and 1–3 of the second Q3 2023 GDP releases. Current-vintage search ends when the third release became available on 21 December. |
| Financial news | The published 16 September 2008 Wikinews revision about Lehman Brothers. Article text by Wikinews contributors under [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/). |

Publisher dates and version evidence are recorded in the [source catalog](retrieval/catalog.json). The gateway reads an explicit list of content-addressed archive objects. Direct URLs, citation fragments and dated redirects resolve within the same session. A missing or ineligible target returns an empty result. The model-visible trace includes the bound cutoff and admitted passage IDs. The evaluator supplies the 2025 Disney filing only in the mixed-archive isolation test.

The retrieval evaluation uses 12 development questions and 14 manually marked passages. It reports top-five passage recall and whether the first result is marked. Each returned citation must reopen the exact passage through both its citation URL and its source locator. For PDFs the external link targets the original page and includes a search phrase, whose highlighting depends on the viewer. The development questions and ranks are available in the [scoring sheet](evaluations/retrieval/cases.json) and [measured results](evaluations/retrieval/results.json).

To rebuild the archive directly from the publishers, run the optional import with network access and native `curl`. It checks the pinned hashes and edition dates before creating the destination. The bundled archive supports repeatable offline verification when a publisher changes its site.

```sh
archive_workspace="$(mktemp -d)"
.venv/bin/python -m scripts.retrieval_archive --output-dir "$archive_workspace/publishers"
diff -qr retrieval "$archive_workspace/publishers"
```
