# ThesisTape

ThesisTape is a multi-agent research project for testing investment theses using only information that was public by a chosen date. An analyst will be able to enter a question, a cutoff date and a budget, then trace each material claim in the resulting memo back to its source.

The first implementation milestone is an offline Disney/Hulu case. It uses Disney’s November 2023 Form 8-K to distinguish the payment Disney expected from the price that was still open to appraisal. The evaluation plan compares the multi-agent workflow with a single analyst agent before submitting reports to FinanceGym.

[Build plan and validation milestones](BUILD_PLAN.md) · [Architecture and model costs (PDF)](ThesisTape-Architecture.pdf)

![ThesisTape research workflow showing the lead orchestrator, evidence workers, independent review and final report.](ThesisTape-Architecture.png)

## Reproduce the Disney/Hulu case

From the repository root, run these commands with Python 3.9 or later.

```sh
python3 scripts/disney_hulu_case.py verify
case_workspace="$(mktemp -d)"
python3 scripts/disney_hulu_case.py import --output-dir "$case_workspace/disney-hulu"
python3 scripts/disney_hulu_case.py verify --case-dir "$case_workspace/disney-hulu"
python3 -B -m unittest discover -s tests -p 'test_disney_hulu_case.py' -v
```

Give the research agent the two files in the case’s `agent/` directory. The evaluator uses the separate `evaluator/` directory to grade the answer and check the dated sources. Import and verification run offline.

The importer reads the saved filings and indexes from the repository by default. Pass `--filings-dir DIR` to read the same four HTML files by their saved names from a single directory. The manifests and scoring sheet come from the versioned case. Reusing a destination with identical files succeeds without rewriting it.

The stored Form 8-K documents are the original HTML payloads inside EDGAR’s complete submissions. The direct HTML responses included an archive script. The saved payloads match EDGAR’s listed sizes and the rest of each direct response byte for byte. The evaluator manifest links to both complete submissions and the index snapshots.

## Run the single-Sol baseline

The baseline uses the verified Disney archive and sends only the dated passages to GPT-6.2 Sol. It validates the structured response, links each memo section to the cited filing text, and recomputes the disclosed payment from the sourced inputs with decimal arithmetic. The agent receives the question, cutoff and admitted passages. The scoring sheet and later filing are used from the evaluator side.

Set up the baseline dependencies with Python 3.12.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-baseline.txt
.venv/bin/python -B -m unittest discover -s tests -p 'test_sol_baseline.py' -v
```

With `OPENAI_API_KEY` in the ignored project `.env.local` or shell environment, run the CLI using a fresh output path.

```sh
.venv/bin/python -B -m scripts.sol_baseline run --budget-usd 0.05 --output-dir runs/disney-sol
.venv/bin/python -B -m scripts.sol_baseline replay --run-dir runs/disney-sol > runs/disney-sol/replayed.md
cmp runs/disney-sol/memo.md runs/disney-sol/replayed.md
```

`memo.md` is the analyst deliverable. `run.json` records the cutoff-bound retrieval, source hash, prompt version, request hash, provider IDs, latency, token counts, arithmetic and cost estimate. The CLI counts input tokens and reserves enough for the output limit before sending one generation request. Replay rechecks those inputs and renders the saved response without contacting OpenAI. Costs use the published Standard rates and remain estimates until reconciled with billing. [OpenAI GPT-6.2 Sol pricing](https://developers.openai.com/api/docs/models/gpt-6.2-sol)

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
