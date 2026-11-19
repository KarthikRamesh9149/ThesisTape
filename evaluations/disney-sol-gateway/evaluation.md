# Disney/Hulu single-agent evaluation

On 19 November 2026, one live GPT-6.2 Sol analyst earned **3/3** on the Disney/Hulu development case. The only case data in its first request were the original question and the cutoff of 2 November 2023. The two runs below used the same question, cutoff, model, Standard service tier, USD 0.05 ceiling and evaluator rubric. [Gateway memo](memo.md) · [Supplied-filing memo](supplied-control/memo.md) · [Evaluator rubric](../../cases/disney-hulu-2023-11-02/evaluator/scoring.json)

| Evidence workflow | Local score | Model calls | Input tokens | Output tokens | Elapsed | Estimated model cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Filing supplied upfront | 3/3 | 1 | 1,116 | 813 | 11.149 s | USD 0.010362 |
| Sol searches and selects passages | 3/3 | 3 | 2,589 | 1,104 | 18.188 s | USD 0.016218 |

Finding the evidence added USD 0.005856 and 7.039 seconds in this case. Each discovery call had a 512-token output limit. The gateway draft had a 3,072-token limit and the supplied-filing draft had a 4,096-token limit. The cost difference includes the two drafts’ different output lengths. The comparison covers one known development question. An official FinanceGym score requires a separate evaluation. [Gateway receipt](run.json) · [Supplied-filing receipt](supplied-control/run.json)

## What the analyst found

Sol issued the query `Disney NBCUniversal Hulu stake expected payment appraisal Disney press release SEC 8-K November 2023`. The archive returned five previews. Sol opened three paragraphs of Item 8.01 in Disney’s 1 November filing, used paragraphs 1 and 2 in the memo and left paragraph 3 uncited. Every search and read used the 2 November cutoff. [Discovery calls and opened passages](run.json) · [Disney Form 8-K, accession 0001744489-23-000206](https://www.sec.gov/Archives/edgar/data/1744489/000174448923000206/dis-20231101.htm)

I graded the saved memo against the existing evaluator sheet and the original Item 8.01. Each row earns one point only when the full condition is present.

| Criterion | Points | Finding in the saved memo | Filing passage |
| --- | ---: | --- | --- |
| Expected initial payment | 1/1 | Approximately USD 8.61 billion on or before 1 December 2023, assuming all outstanding NBCUniversal capital contributions remained unpaid on that date. | Item 8.01 ¶1 |
| Scope of the valuation floor | 1/1 | USD 27.5 billion applied to the equity of all Hulu. NBCUniversal’s ownership percentage applied to the greater of that floor or fair value. | Item 8.01 ¶1 |
| Appraisal and uncertainty | 1/1 | An above-floor appraisal could require a further payment to NBCUniversal. The memo explicitly says the final purchase price was unknown on 2 November. | Item 8.01 ¶2 |

The six passage links reopened the cited paragraphs and the seventh link reopened the full archived item. The subtraction of USD 567 million from USD 9.17 billion gives USD 8.603 billion. The USD 7 million difference from the reported USD 8.61 billion fits the precision of the disclosures. The memo also identifies the separate tax-sharing provision in paragraph 2. Its discussion uses only the terms in the 2023 filing. [Citations and arithmetic](memo.md)

## Usage and verification

| Gateway call | Input tokens | Output tokens | Provider time | Estimated cost |
| --- | ---: | ---: | ---: | ---: |
| Choose search terms | 303 | 36 | 1.743 s | USD 0.000966 |
| Select passages | 961 | 29 | 1.754 s | USD 0.002212 |
| Write memo | 1,325 | 1,039 | 13.056 s | USD 0.013040 |

OpenAI reported zero cache reads or writes and 309 reasoning tokens included in the output total. Archive tools ran locally with zero metered fees. Run elapsed time includes token-count requests and local work. The amounts use provider-reported usage and published Standard rates. Invoice reconciliation is pending. [Gateway receipt](run.json) · [OpenAI pricing](https://developers.openai.com/api/docs/models/gpt-6.2-sol)

An earlier gateway trial cost USD 0.013556. Sol guessed both dollar figures in its opening query before seeing a source. The verified version rejects new numeric search terms unless they occur in the input or an earlier preview, with an allowance for common SEC form names. The verified query above contains neither dollar figure. Across the four recorded project runs, the two supplied-filing trials used USD 0.021564 and the two gateway trials used USD 0.029774, for a total estimate of **USD 0.051338**.

Both saved provider outputs reproduced their memo bytes with outbound network access denied. The gateway memo’s SHA-256 is `7329f01cc7eab4c2a32dced90dd7700d48cfd32b135a17ded2a9397d0174cefb`. A separate offline test added the real June 2025 Disney filing to the archive, opened it under its later cutoff to warm the cache, then reran the recorded 2023 decisions. All three request fingerprints, selected passages, retrieval events and memo bytes matched the live run. Raw provider IDs stay in the local operator receipts. The portable receipts carry their hashes. [Replay commands](../../README.md#run-the-single-sol-baseline)
