# Financial calculations on Nintendo and Canon

Nintendo’s consolidated net profit attributable to owners of parent was JPY 258,641 million in FY2020. Its FY2021 guide was JPY 200,000 million. The team retrieved both rows, computed a JPY 58,641 million decline, and applied the question’s inclusive 2% tolerance to get JPY 196,000–204,000 million. It forecast profit above that range and specified what would change its view. The later reported result was above the range. [Nintendo memo](nintendo.md) · [Nintendo statements available at the cutoff](https://www.nintendo.co.jp/ir/pdf/2020/200507e.pdf)

Canon needed JPY 206,025 million after the first quarter to reach its JPY 252,000 million annual projection. Its actual full-year profit fell below the comparison band. The team had predicted a result inside it. [Canon memo](canon.md) · [Canon first-quarter filing](https://global.canon/en/ir/results/2022/rslt2022q1e.pdf)

| Case | Facts | Reasoning | Forecast | Supported claims | Model usage | Peak amount needed | Separate grader |
|---|---:|---:|---:|---:|---:|---:|---:|
| Nintendo | 3/3 | 2/2 | 1/1 | 30/30 | $0.351676 | $0.404994 | $0.185032 |
| Canon | 3/3 | 2/2 | 0/1 | 22/22 | $0.296268 | $0.386464 | $0.161280 |

Both trials used eight real generation calls and released a complete memo. The peak includes amounts paid and outstanding reservations at each dispatch. Each case had a USD 0.45 ceiling. The grader saw one masked memo, the source passages and the pre-existing factual rubric. It supplied verbatim witnesses for its decisions. Both assessments were committed before the evaluation read the later outcomes. These two familiar development events establish the repair. FinanceGym grades its own 400 questions. [Graded criteria](asof.json) · [Forecast and cost records](results.json)

The original Nintendo pilot released zero of six memos. The first arithmetic revision scored 2/3 on Nintendo facts. The second also scored 2/3, despite finding both inputs, because the calculator rejected a fiscal-period label. The final version keeps valid calculations independently, replaces unsuitable display labels, distinguishes task periods from financial claims, and presents checked formulas to the reviewer. Every earlier result is retained. [Original pilot](../../results/nintendo-fy2021-guidance/evaluation.md) · [First revision](../quantitative_trials/results.json) · [Second revision](../quantitative_trials_v2/results.json)

From the repository root, these commands reopen the source packets, reproduce the saved generation requests and check the final Markdown hashes with zero API calls.

```sh
.venv/bin/python -B -m evaluations.paired.quantitative_proof verify --revision 3
.venv/bin/python -B -m unittest discover -s tests -p test_verified_arithmetic.py -v
```
