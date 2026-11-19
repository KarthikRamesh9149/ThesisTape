# ThesisTape

## Point-in-time financial research

ThesisTape produces a research memorandum for a question, an information cutoff and a spending limit. We evaluated it on the full 400-question public FinanceGym release. The maintainer-held rubric determines the official pre-cutoff, post-cutoff and Overall scores. [FinanceGym](https://financegym.github.io/)

## Research configuration

The API model identifiers used on the benchmark were `gpt-6-luna`, `gpt-6.2-astra`, `gpt-6.2-sol`.

- Single Astra analyst received 400 original tasks under a USD 0.30 per-task ceiling.

The primary researcher released 340 reports using an estimated USD 74.81. Independent investigations on unanswered questions released 39 reports. All independent investigations together used an additional USD 15.03. Unresolved provider reservations across both attempts total USD 0.

After the first case in which an optional rewrite failed following a completed independent review, we added an offline publication rule. It produced 12 further qualified reports by retaining only previously approved paragraphs. The originally registered system left these questions unanswered. Both original attempts and expenses are included. The additional publications used zero new searches or generations.

A second addition addressed questions with neither a released memo nor independently approved paragraphs. Sol checked proposed calculations, an Astra author wrote a short answer and a fresh Sol context reviewed it. Editing generated 3 qualified reports for an additional USD 0.33, under a USD 0.55 ceiling per eligible case. The editor worked from verified dated passages.

For 1 of those edited reports, both investigators had retrieved zero passages. A later retrieval rule retried their original queries with capitalization variants of entity titles present in the question. It kept the cutoff, original query terms and result limits. The editor received the resulting historical passages. All earlier failures and the time spent on the retry were retained.

After the owner set a USD 140 total project budget, we prospectively allowed one independent Sol review of a primary draft whose optional revision exceeded its allowance. It required a source-checked answer and a separate supporting section. We retained the original wording only when both survived independent review. The reviewer made no searches and had a USD 0.075 ceiling. Across 14 such reviews, 6 qualified reports were released for a total additional USD 0.20. Earlier independent investigations kept their original outcomes.

After every primary question and existing-draft review had settled, the available project balance determined the remaining independent research assignments. The previously tested mixed team took 18 source-rich questions under its USD 0.30 case ceiling. It conducted fresh dated searches, used Luna researchers and Sol analysis, and gave the Astra draft to a fresh Sol reviewer. The team released 18 reports directly and used an estimated USD 4.05, included in the independent totals above. All earlier investigations retain their original routes. The allocation used source counts and measured costs before spending.

We reproduced 3 PDF parsing failures from the original dated searches and HTTP bytes. Each affected question retains its original research cost and follows the same independent recovery procedure as other unanswered questions.

The researcher plans dated searches and receives passages only after the archive verifies their availability. Web evidence comes from dated captures, immutable encyclopedia revisions or accession-specific SEC records. For an admitted archived page or filing, the gateway can reopen the original bytes, match their hash and retrieve a section relevant to a specific part of the question. Every search uses the exact cutoff of its assigned question. Discovery titles and live snippets stay outside the model context. Numeric and citation checks run before the text is released. The local analyst application exposes cited passages, checked calculations and the cost of each run.

We ran the first six questions with up to three concurrent investigations, then used up to eight. When the public archive began rate-limiting requests, we let the active investigations finish and reduced concurrency after 218 questions. Subsequent primary investigations ran in groups of up to three, with pauses when the archive returned HTTP 429. The original results and expenses of all completed investigations were retained.

## Independent comparisons

Before publication, ThesisTape checks whether a statement attributed to the analyst's question appears in the original wording. When the check removes a passage, it retains the original and published hashes and qualifies the report. The same check applies to all configurations and incurs no model charge.

Before the official run we locked reports on 24 historical financial events across six categories. Cases used matched documents, dates and spending limits. Graders saw anonymized reports and eligible evidence before the evaluator opened the later outcomes. Local quality weighted facts at 50%, reasoning at 25% and forecast direction at 25%. Reports with no usable answer counted in the denominator. These weights define the local experiment.

| Configuration | Cases | Facts | Reasoning | Forecast direction | Local quality | Reports released |
|---|---:|---:|---:|---:|---:|---:|
| Sol-led team with Luna researchers and an Astra author | 24 | 73.6% | 89.6% | 43.8% | 70.1% | 22 |
| Single Astra analyst | 24 | 87.5% | 100.0% | 43.8% | 79.7% | 24 |

The event-paired 90% bootstrap interval for mixed minus single local quality was -17.4 to -1.9 percentage points. A separate financial statement test sampled one published FinanceBench question per issuer for 24 issuers. The quantitative team answered 15/24 correctly with support from cited pages. Single Astra with the same tools answered 13/24. The 90% paired interval for that difference was -8.3 to +25.0 percentage points. The grader was an independent model context checking responses against the authors' human references. The open questions may have appeared in model training. [FinanceBench](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)

## Reproducibility and cost

The JSONL contains exactly 400 original question and cutoff pairs. Each report's dated searches, provider request fingerprints, recorded usage and final Markdown were checked offline. The submission contains 273 qualified reports. Those reports restrict their conclusions where the research did not establish part of the answer. Model generation for these runs cost an estimated USD 90.38, based on the recorded usage for 1729 confirmed generations. The estimate excludes local computing, external judging and development.

Per question, model costs averaged USD 0.2259 and the 95th percentile was USD 0.5249. Recorded research time averaged 147.4 seconds, with a 95th percentile of 292.1 seconds. Each duration sums the elapsed time of that question's investigations and any additional editing. Time waiting between those stages is excluded.

| Artifact | SHA-256 |
|---|---|
| FinanceGym public question file | `1be781afd34abff47c3ebeb452ed9e46a23b94c087c69b9cfbd3a58d21193fac` |
| Registered candidate | `2a51039b25186a8e30262013c82be24898ab4ffbf9adb6c692312b30d694fd4d` |
| 400 reports | `5daeb98a17509bda30fa5f394ab3fc5ed440a22ceb1a407dbe0ff70165ab1057` |

The source archive is independent of the retrieval corpus used by the benchmark authors, so coverage can differ. A later-trained model can also retain information in its parameters. We require citations for factual assertions and preserve the original retrieval records. FinanceGym is used here for noncommercial benchmark evaluation under CC BY-NC 4.0. [Public questions and license](https://huggingface.co/datasets/Rujun/FinanceGym)
