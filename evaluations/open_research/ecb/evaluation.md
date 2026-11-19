# ECB rate decision in the analyst desk

On 20 November 2026 (Sydney) the **Public historical sources** collection answered a new question about the ECB's 6 June 2024 decision with a cutoff of 7 June 2024. Chromium submitted the question through the local analyst desk. The team completed nine paid generations, with an estimated USD 0.2052854 in settled usage against a USD 0.40 allowance.

The report identifies the 25-basis-point cut and gives the three new rates, distinguishes the announcement from its 12 June effective date, and explains why inflation risks left subsequent decisions dependent on incoming data. Its sources include historical captures of the Governing Council's policy statement and decision. [Research memo](memo.md)

The browser opened a cited passage, visited the memo and activity interface, and downloaded the 3,802-byte Markdown file. The export matched the saved report byte for byte. The browser displayed the completed report at both 1440 and 390 pixels wide and recorded zero page exceptions. [Browser observations](browser.json)

From the repository root, reproduce all three searches from their saved model-visible responses, every model request, the passage links, generation costs and downloaded bytes without network access.

```sh
.venv/bin/python -B -m evaluations.open_research.ecb
```

This is a live product test on a non-benchmark question. FinanceGym grades its official reports independently.
