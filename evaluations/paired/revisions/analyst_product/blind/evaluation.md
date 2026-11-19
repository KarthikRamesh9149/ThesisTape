# Held-out financial research results

The production study assigned the same 24 historical financial events, dated source packets and USD 0.30 ceilings to two researchers. The six categories covered acquisitions, earnings, financial statements, central bank policy, economic releases and government bond auctions. Each arm attempted every event. 16 cases required a forward-looking conclusion. The reports and their billing records were sealed before masked graders assessed the as-of evidence. Forecast scoring began after the as-of assessments were committed. [Study protocol](../../../protocol.md) · [Registered finalists](../finalists.json)

| Researcher | Reports released | Facts | Reasoning | Forecast direction | Local quality | Supported material claims | Mean cost per assignment | Mean seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Production research team | 22/24 | 73.6% | 89.6% | 43.8% | 70.1% | 427/431 | $0.1378 | 86.6 |
| Single Astra | 24/24 | 87.5% | 100.0% | 43.8% | 79.7% | 424/431 | $0.1417 | 39.7 |

The paired team-minus-single quality difference was -9.55 percentage points, with a 90% bootstrap interval from -17.36 to -1.91. Its mean cost difference was USD -0.0039, with a 90% interval from -0.0203 to +0.0110. The analysis retains unsuccessful assignments as zero-quality outcomes and counts unresolved provider exposure at its maximum. The production packet retained USD 0.0012465 in unconfirmed reservations. The dated-source audits detected 0 cutoff violations. [Per-event results, costs and sensitivity analyses](decision.json)

The preregistered rule selected **Single Astra** for general FinanceGym research. A separate comparison across 24 companies found 15/24 correct, source-supported financial statement answers from the quantitative team and 13/24 from single Astra. Its paired 90% interval was -8.3 to +25.0 percentage points. [Candidate selection policy](../../../../financegym/selection.md) · [FinanceBench comparison](../../../../financebench/evaluation.md)

The earlier workflow was tested on the same events. It released 4/24 fixed-plan team reports and 3/24 single-Sol reports. Their local quality scores were 12.5% and 8.7%. These are a separate control on the same 24 events. [Earlier study](../../grounding_v2/blind/decision.json)

The portable audit reproduced all 48 production assessments, including 46 paid masked assessments and their 50 verified generations. Confirmed grading cost was USD 6.750768. Any truncated assessment retained its original charge. The same source witnesses and constraints governed the continuation. [Grader receipts](grading-audit.json) · [Continuation procedure](grader-continuation.md)

A separate package contains 24 blinded reports from 12 paired events for a qualified human analyst to edit. Its SHA-256 is `5d9c09cda10be65ad2f28a5420c10a7e7319aa8199bb539c2d713cd4dfcb3508`. The package is ready for review. Human correction times and professional usability ratings are pending. [Review procedure](../../../analyst_review.py)

Local quality weights factual accuracy at 50%, reasoning at 25% and realized forecast direction at 25%. FinanceGym uses a separate set of questions and a maintainer-held rubric. [FinanceGym leaderboard and evaluation](https://financegym.github.io/)
