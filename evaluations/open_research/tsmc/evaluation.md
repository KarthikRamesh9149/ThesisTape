# TSMC Arizona financing

A real Chromium session submitted a question about the Arizona project as of 10 April 2024. The quantitative team made eight generation calls costing an estimated USD 0.308256. The browser opened a source and downloaded the 3,612-byte report. Five dated searches admitted 15 source editions. Reconstructing them from 56 saved HTTP responses reproduced the model-visible searches. [Browser observations](browser.json) · [Original browser export](browser-export.md)

The original release omitted the main funding amounts. Its second draft contained the figures and an independent reviewer approved them, but the release code picked the first draft because its reviewer had listed fewer missing items. Both reviewers had also rejected some paragraphs. The revised code compares the supported portions of each draft. It recovered the grant, loan, planned investment and its checked increase from the same eight saved generation responses, at zero additional model cost. [Corrected memo](revised.md) · [Offline comparison](comparison.json)

The memo remains qualified. The excerpts supplied to the author did not establish all the financing conditions. The original government announcement describes due diligence, final negotiations and milestones. The archived sources and reviewers' original decisions remain available for inspection. [Contemporaneous government announcement](https://web.archive.org/web/20240410192324/https://www.nist.gov/news-events/news/2024/04/biden-harris-administration-announces-preliminary-terms-tsmc-expanded)

Reproduce the browser report and both release decisions with the saved requests.

```sh
.venv/bin/python -B -m evaluations.open_research.tsmc
```
