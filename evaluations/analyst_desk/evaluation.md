# From a research question to a verified memo

On 19 November 2026 an analyst-desk browser session submitted a free-form Tradeweb question with a cutoff of **27 October 2023 at 00:15 UTC** and a **USD 0.10** limit. The session paused and resumed the run, inspected the corrected expense figure in Tradeweb’s SEC filing, and downloaded the released memo. [Browser observations](proof/browser.json) · [Released memo](proof/memo.md) · [Run ledger](proof/run.json)

| Check | Observed result |
| --- | --- |
| Intake and routing | The runner retained the exact question, UTC timestamp, collection and cap. Sol planned two independent searches. Both Luna workers opened the dated evidence. Sol chose to write. |
| Interruption | The browser displayed an outstanding reservation and requested a pause. Sol’s planning call settled. Resume reused that assignment. The operator matched all 12 settled assignments to 12 private receipts and distinct provider response IDs. |
| Review | The reviewer flagged the draft’s attribution of the forecast to a particular period. The amended filing excerpts did not establish that period. Sol revised the memo and the reviewer approved it. The activity tab shows the routed action, while financial conclusions appear in the released memo. |
| Material claim | The memo identifies USD 659 million as the previously printed upper end, USD 695 million as the correction, and the replacement of the earnings release. The rest of the disclosure was unchanged. Clicking the citation opened the original Item 2.02 passage with both figures. Both cited sections reopened at the saved cutoff. |
| Export | The downloaded Markdown, saved memo and CLI replay each had **1,941 bytes** and SHA-256 `daddfbdff9a69b8cb0a8cdc99f75ad48607fc05a49f6a459fb4d1993af0bc922`. The saved ledger stayed byte-identical across inspection and export. |
| Spending | Twelve generations across Sol and Luna cost an estimated **USD 0.0323058** from returned usage. Outstanding reservations ended at zero. Added to the previous USD 0.5462619, recorded project usage reached **USD 0.5785677**. |

The source check used the original Tradeweb amendment and the existing development rubric. This exercise measures the product workflow. The planned independent comparison and FinanceGym scoring are later milestones. [Tradeweb Form 8-K/A](https://www.sec.gov/Archives/edgar/data/1758730/000175873023000171/tw-20231026.htm) · [Existing scoring sheet](../../cases/tradeweb-expenses-2023-10-26/evaluator/scoring.json) · [Previous cumulative usage](../cli_resume/proof/results.json)

## Reproduce the saved result

Run from the repository root with the [research dependencies](../../README.md#run-the-research-team). These checks use the archived evidence and saved provider outputs. The optional `--operator-run DIRECTORY` argument to the verifier also checks the private receipts for this particular run.

```sh
.venv/bin/python -B -m evaluations.analyst_desk.verify
.venv/bin/python -B -m scripts.research_team replay --run-dir evaluations/analyst_desk/proof > /tmp/thesistape-desk.md
cmp /tmp/thesistape-desk.md evaluations/analyst_desk/proof/memo.md
.venv/bin/python -B -m unittest discover -s tests -p 'test_analyst_desk.py' -v
```

## Reproduce the browser checks

Start the local server and open the [saved Tradeweb browser trial](http://127.0.0.1:8766/#run=example-tradeweb-desk). The trial is also available under **Saved examples**. Selecting a numbered citation shows the archived text, publication date, locator and publisher link. The server reads the same run record as the CLI.

```sh
.venv/bin/python -B -m scripts.analyst_desk --port 8766
```

In a second terminal, the following command checks the screen, keyboard navigation, source selection and actual download in Chromium. It opens saved reports and issues zero writes while reopening the proof. This optional browser test requires Node, Playwright and a matching Chromium installation. Set `PLAYWRIGHT_MODULE` or `CHROMIUM_PATH` if they are installed outside the default locations. The Python app itself needs neither.

```sh
DESK_URL=http://127.0.0.1:8766 node evaluations/analyst_desk/browser.cjs --proof
```

The browser test ran at 1536 × 1024, 390 × 844 and 320 × 844. It exercised both report tabs, keyboard focus, saved-run selection, the research form, an arithmetic section, passage links and the Markdown download. The live run also exercised the pause and resume buttons. Chromium reported no page exceptions or console errors. On desktop, the serif masthead, status line, three spending metrics, memo tabs and adjacent evidence panel follow the layout reference. The report uses the complete research question, precise cutoff and exact archived evidence. On small screens the evidence follows the report, and selecting a citation moves focus to its passage.

To repeat the **paid** trial, start the default desk with its normal writable runs directory and run `node evaluations/analyst_desk/browser.cjs --live` against that address. Each explicit live invocation creates a fresh run capped at USD 0.10. The default browser check and `--proof` only reopen existing reports.
