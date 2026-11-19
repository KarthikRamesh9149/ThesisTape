# Nintendo replay and revised comparison

The first Astra draft now passes verification. Replaying its five provider responses produces a cited report with two checked calculations. Estimated cost from the historical receipts is USD 0.141616. This is a counterfactual release on saved responses. The six historical submissions each still score zero. [Historical results](../../results/nintendo-fy2021-guidance/results.json) · [Revised replay with fingerprints](replay.json)

The revised checks accepted nine of eleven saved author and forecast responses. Single Sol’s first draft includes an untraced 0.227. The first draft from the mixed team without a forecast specialist includes two untraced endpoints, 196,000 and 204,000. Both still fail. A separate check changed a genuine profit decline of 58.6 billion yen to 59.9 billion yen. The cited original and verified calculation rejected it.

Only single Astra has a complete response chain under the revised policy. Three teams need a newly generated memo after their forecast passes. The other two configurations need a new paragraph repair. The replay stops at the first missing or changed request in each arm. No forecasts, reviews or scores are imputed.

## Repair allowance

The original full repair requested 4,961 input tokens and a reservation of USD 0.152010 after USD 0.141616 was already spent. On a deliberately altered paragraph from the original draft, the provider counted 918 input tokens for the new repair. Reserving those tokens and 768 output tokens at the conservative registered rates costs USD 0.039900. Even at maximum output, that would bring the assignment to at most USD 0.181516. This diagnostic made one input count and zero generations. It measures affordability. [Provider input count and prompt digest](repair-count.json)

## New assignments

The revised study gives every arm the same USD 0.30 ceiling. USD 0.293626 would have covered both historical usage and the full repair reservation when a paragraph patch was insufficient. The 68 development assignments can reserve at most USD 20.40 in total. The two finalists on 24 blind cases can reserve at most USD 14.40. Each assignment starts cold. The Nintendo development event informed the revision. Blind events remain reserved for confirmation.

To reproduce the observations offline, run the following from the repository root.

```sh
.venv/bin/python -B -m evaluations.paired.grounding_replay --verify
.venv/bin/python -B -m unittest discover -s tests -p test_paired_grounding.py
```
