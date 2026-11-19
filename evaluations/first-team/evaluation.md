# Sol lead and two Luna workers

The team and the single-Sol control each earned **3/3** on the final Disney and Tradeweb development runs. Sol alone was faster and cheaper on both. The contrary-evidence worker independently found an important qualification in Tradeweb's amendment. The single analyst also reported it. These two cases establish that the team works and give it a measured cost baseline. A quality advantage remains to be demonstrated on other cases.

| Case and route | Local score | Live generations | End-to-end time | Estimated API cost |
| --- | ---: | ---: | ---: | ---: |
| Disney, single Sol | 3/3 | 3 | 18.188 s | USD 0.016218 |
| Disney, Sol and two Luna workers | 3/3 | 9 | 25.670 s | USD 0.0216475 |
| Tradeweb after amendment, single Sol | 3/3 | 3 | 8.955 s | USD 0.008032 |
| Tradeweb after amendment, Sol and two Luna workers | 3/3 | 9 | 20.397 s | USD 0.0117409 |

Within each pair, the question, cutoff, archived sources and USD 0.05 run ceiling matched. The Disney control is the verified Step 3 run. Both Tradeweb routes used the same report schema and writing instructions. The scores are manual grades against the local rubrics. Each timing is one live observation. [Disney control](../disney-sol-gateway/evaluation.md) · [Disney team](disney-team/memo.md) · [Tradeweb control](tradeweb-single/memo.md) · [Tradeweb team](tradeweb-team/memo.md)

## What the workers contributed

| Case | Filings worker | Contrary-evidence worker | Cost of the three Sol lead calls |
| --- | --- | --- | ---: |
| Disney | Selected the expected payment, deduction, unpaid-balance condition and appraisal terms. USD 0.0003127. | Searched separately and selected the same five excerpts. It added no new source or rubric point. USD 0.0003228. | USD 0.021012 |
| Tradeweb | Found both endpoints and the filing's explanation that the original figure was a typo. USD 0.0002589. | Also selected two passages confirming that other disclosures stayed unchanged. USD 0.0002760. | USD 0.011206 |

The rest of the added cost came from the lead's planning, comparison and different drafting usage. The first worker requests overlapped for 1.222 seconds on Disney and 1.320 seconds on Tradeweb. Each worker received its own assignment and search results. Its handoff contains offsets into the original source text. Sol received both handoffs and the deduplicated passages, decided the cases were ready to write and delivered cited reports. [Disney execution record](disney-team/run.json) · [Tradeweb execution record](tradeweb-team/run.json)

## Grading

**Disney.** Both final reports distinguish the initial estimate from the unknown final price. Each earns one point for the approximately USD 8.61 billion due by 1 December if the outstanding capital contributions remained unpaid, one for applying the USD 27.5 billion floor to all Hulu equity, and one for explaining the possible above-floor appraisal payment. The arithmetic independently reconciles approximately USD 9.17 billion less USD 567 million to USD 8.603 billion. The USD 7 million difference from Disney's reported estimate fits the precision of the figures. All eight team memo links reopened through the dated archive. [Rubric](../../cases/disney-hulu-2023-11-02/evaluator/scoring.json) · [Disney Form 8-K, Item 8.01](https://www.sec.gov/Archives/edgar/data/1744489/000174448923000206/dis-20231101.htm)

**Tradeweb.** The question asked about the high end of the expense forecast and gave no hint of a correction. Both final reports identify the change from USD 659 million to USD 695 million, attribute it to a typographical error, and explain that the amended earnings release replaced the original with its other information unchanged. Each report earns one point for each of those three criteria. All four links in each memo reopened the relevant passages. [Question](../../cases/tradeweb-expenses-2023-10-26/agent/question.json) · [Rubric](../../cases/tradeweb-expenses-2023-10-26/evaluator/scoring.json) · [Tradeweb Form 8-K/A, explanatory note and Item 2.02](https://www.sec.gov/Archives/edgar/data/1758730/000175873023000171/tw-20231026.htm)

The first Tradeweb runs each earned **2/3**. Both correctly reported the figures, cause and unchanged information. Neither explained that the original release had been replaced. The rubric was written before those runs. The shared report format now asks for the history of a disclosure when a cited passage establishes that relationship. The final 3/3 scores were measured on the same development question after that change. [First single-Sol memo](initial/tradeweb-single.md) · [First team memo](initial/tradeweb-team.md)

## One second before the amendment

EDGAR accepted the amendment at **13:16:39 UTC** on 26 October 2023. With the same question at **13:16:38 UTC**, the team could inspect the original 8-K body, which points readers to Exhibit 99.1. That separate exhibit is outside this archive. Sol requested one more filings search, then qualified the answer. The memo links the inspected original passages and gives no expense figure. This live control took 21.522 seconds and cost USD 0.0045051. At **13:17:00 UTC**, both routes could open and cite the amendment. [Early memo](tradeweb-before/memo.md) · [Decision and follow-up trace](tradeweb-before/run.json) · [Archived EDGAR acceptance record](../../retrieval/objects/94d6f1d486c05904693f17f6f3998b2dd5a6f996a9724b17d5a2755e58af565b.htm)

## Verification and spend

Replay rebuilds the model requests, runs the recorded choices against the pinned archive, verifies the concurrent reservations against provider usage and renders the original memo bytes. The tests also place Disney's real 2025 filing in a warmed archive and compare every model input against the 2023 live run. Both worker contexts and the lead retain identical request fingerprints. [Reproduction commands](../../README.md#run-the-research-team)

The twelve development runs used an estimated **USD 0.1157985**. Four stopped during validation or budget checks and account for USD 0.0284231 of that amount. They exposed overlong worker quotations, a Luna token limit, valid cutoff phrasing rejected by the numeric checker, and an unsupported guessed number in a search. The resulting fixes and reruns are included in the total. With USD 0.051338 from earlier milestones, the recorded project estimate is **USD 0.1671365**. Local archive tools had zero metered fees. These estimates use reported token counts and Standard pricing, pending invoice reconciliation. [All run usage and outcomes](development-usage.json) · [Sol rates](https://developers.openai.com/api/docs/models/gpt-6.2-sol) · [Luna rates](https://developers.openai.com/api/docs/models/gpt-6-luna)
