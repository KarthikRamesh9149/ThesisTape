"""Select development finalists and apply the frozen paired quality and cost decision rule."""

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction

from scripts.sol_baseline import fingerprint, json_bytes

from .confirmation import PROTOCOL, REVISION, committed, write_once
from .corpus import require
from .study import locked


def nearest(values, p):
    return sorted(values)[max(0, math.ceil(p * len(values)) - 1)]


def metrics(rows):
    n = len(rows)
    forward = [r for r in rows if r["O"] is not None]
    if not n:
        return None
    factual = Fraction(sum(r["F"] for r in rows), 3 * n)
    reason = Fraction(sum(r["R"] for r in rows), 2 * n)
    outcome = Fraction(sum(r["O"] for r in forward), len(forward)) if forward else None
    cost = [Decimal(r["cost_usd"]) for r in rows]
    times = [r["elapsed_ms"] / 1000 for r in rows]
    claims = sum(r["material_claims"] for r in rows)
    supported = sum(r["supported_claims"] for r in rows)
    result = {
        "cases": n,
        "forward_cases": len(forward),
        "F": float(factual),
        "R": float(reason),
        "O": float(outcome) if outcome is not None else None,
        "Q": float(factual / 2 + reason / 4 + outcome / 4)
        if outcome is not None
        else None,
        "mean_cost_usd": float(sum(cost) / n),
        "p95_cost_usd": float(nearest(cost, 0.95)),
        "mean_seconds": statistics.mean(times),
        "p95_seconds": nearest(times, 0.95),
        "released": sum(bool(r["memo_sha256"]) for r in rows),
        "qualified": sum(r["qualified"] for r in rows),
        "empty": sum(not r["material_claims"] for r in rows),
        "cutoff_violations": sum(r["cutoff_violations"] for r in rows),
        "supported_claims": supported,
        "material_claims": claims,
        "citation_support": supported / claims if claims else None,
    }

    if any(Decimal(r.get("unreconciled_usd", "0")) for r in rows):
        settled = [Decimal(r.get("confirmed_cost_usd", r["cost_usd"])) for r in rows]
        result.update(
            mean_confirmed_cost_usd=float(sum(settled) / n),
            mean_cost_upper_bound_usd=result["mean_cost_usd"],
            unreconciled_usd=str(sum(cost) - sum(settled)),
        )
    return result


def select(rows, mixed_arms=("mixed", "mixed_fixed_plan", "mixed_no_forecast")):
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    expected = {r["case_id"] for r in rows}
    require(
        len(expected) == 12
        and all(
            len(v) == len(expected) and {r["case_id"] for r in v} == expected
            for v in by_arm.values()
        ),
        "Missing development assignments",
    )
    stats = {arm: metrics(records) for arm, records in by_arm.items()}

    def winner(arms):
        eligible = [
            a
            for a in arms
            if stats[a]["cutoff_violations"] == 0 and stats[a]["Q"] is not None
        ]
        require(eligible, "No eligible finalist in this class")
        chosen = min(
            eligible,
            key=lambda a: (
                -stats[a]["Q"],
                stats[a]["mean_cost_usd"],
                stats[a]["mean_seconds"],
                a,
            ),
        )

        # Any dollar-based tie-break must select the same arm under every still-possible bill.
        upper = stats[chosen]["mean_cost_usd"]
        for other in eligible:
            if other == chosen or stats[chosen]["Q"] != stats[other]["Q"]:
                continue
            if (
                "unreconciled_usd" in stats[chosen]
                or "unreconciled_usd" in stats[other]
            ):
                lower = stats[other].get(
                    "mean_confirmed_cost_usd", stats[other]["mean_cost_usd"]
                )
                require(
                    upper < lower,
                    "A finalist cost tie-break requires a confirmed charge",
                )
        return chosen

    return (
        stats,
        winner(mixed_arms),
        winner(("single_sol", "single_astra")),
    )


def lock_finalists():
    saved = committed(REVISION / "development/scores.json")
    stats, mixed, single = select(json.loads(saved)["rows"])
    result = {
        "study_registration_sha256": fingerprint(json_bytes(locked())),
        "development_scores_sha256": fingerprint(saved),
        "development": stats,
        "mixed": mixed,
        "single": single,
    }
    write_once(REVISION / "finalists.json", json_bytes(result))
    return result


def differences(pairs):
    if not pairs:
        return {"quality": None, "cost": None}
    total = len(pairs)
    forward = [(a, b) for a, b in pairs if a["O"] is not None]
    d_f = sum(a["F"] - b["F"] for a, b in pairs) / (3 * total)
    d_r = sum(a["R"] - b["R"] for a, b in pairs) / (2 * total)
    d_o = sum(a["O"] - b["O"] for a, b in forward) / len(forward) if forward else None
    upper = (
        sum(
            Decimal(a["cost_usd"]) - Decimal(b.get("confirmed_cost_usd", b["cost_usd"]))
            for a, b in pairs
        )
        / total
    )
    lower = (
        sum(
            Decimal(a.get("confirmed_cost_usd", a["cost_usd"])) - Decimal(b["cost_usd"])
            for a, b in pairs
        )
        / total
    )
    return {
        **({"cost_range": [float(lower), float(upper)]} if lower != upper else {}),
        "quality": d_f / 2 + d_r / 4 + d_o / 4 if d_o is not None else None,
        "cost": float(upper),
    }


def blind_stats(rows, mixed, single, proto):
    groups = defaultdict(dict)
    for row in rows:
        event = groups[row["case_id"]]
        require(row["arm"] not in event, "Duplicate blind arm")
        event[row["arm"]] = row
    require(
        len(groups) == 24 and all(set(e) == {mixed, single} for e in groups.values()),
        "Expected 24 matched blind pairs",
    )
    pairs = [(groups[k][mixed], groups[k][single]) for k in sorted(groups)]
    strata = defaultdict(list)
    for a, b in pairs:
        require(
            a["stratum"] == b["stratum"]
            and a["forward"] == b["forward"]
            and (a["O"] is None) == (b["O"] is None),
            "Unmatched strata or forecast denominators",
        )
        strata[a["stratum"]].append((a, b))
    require(
        len(strata) == 6 and all(len(v) == 4 for v in strata.values()),
        "Unexpected blind strata",
    )
    rng = random.Random(proto["bootstrap_seed"])
    q, c, cost_lower = [], [], []
    buckets = [strata[k] for k in sorted(strata)]
    for _ in range(proto["bootstrap_draws"]):
        sample = [pair for bucket in buckets for pair in rng.choices(bucket, k=4)]
        delta = differences(sample)
        q.append(delta["quality"])
        c.append(delta["cost"])
        cost_lower.append(delta.get("cost_range", [delta["cost"]])[0])
    q_low, q_hi, c_low, c_hi = [
        nearest(l, p) for l, p in ((q, 0.05), (q, 0.95), (cost_lower, 0.05), (c, 0.95))
    ]
    leak = sum(r["cutoff_violations"] for r in rows)
    repeated = {
        p
        for a, _ in pairs
        for p in a["publishers"]
        if sum(p in b["publishers"] for b, _ in pairs) > 1
    }
    return {
        "mixed": mixed,
        "single": single,
        "metrics": {
            a: metrics([r for r in rows if r["arm"] == a]) for a in (mixed, single)
        },
        "paired_mixed_minus_single": differences(pairs),
        "quality_90pct_interval": [q_low, q_hi],
        "cost_usd_90pct_interval": [c_low, c_hi],
        "per_event": {a["case_id"]: differences([(a, b)]) for a, b in pairs},
        "leave_one_stratum_out": {
            s: differences([(a, b) for a, b in pairs if a["stratum"] != s])
            for s in sorted(strata)
        },
        "leave_one_recurring_publisher_out": {
            p: differences([(a, b) for a, b in pairs if p not in a["publishers"]])
            for p in sorted(repeated)
        },
        "cutoff_violations": leak,
        "bootstrap_draws": proto["bootstrap_draws"],
        "advance": leak == 0 and (q_low > 0 or (q_low >= 0 and c_hi < 0)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["select", "blind"])
    args = parser.parse_args()
    if args.operation == "select":
        result = lock_finalists()
    else:
        locked_arms = json.loads(committed(REVISION / "finalists.json"))
        scores = committed(REVISION / "blind/scores.json")
        result = blind_stats(
            json.loads(scores)["rows"],
            locked_arms["mixed"],
            locked_arms["single"],
            json.loads(committed(PROTOCOL)),
        )
        result["scores_sha256"] = fingerprint(scores)
        write_once(REVISION / "blind/decision.json", json_bytes(result))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
