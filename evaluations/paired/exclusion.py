"""Compare the event registry with the pinned public FinanceGym questions."""

import json
import re
import unicodedata
from datetime import date
from functools import lru_cache

from .corpus import digest, require


def words(text):
    return tuple(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).casefold()))


@lru_cache(maxsize=512)
def shingles(text):
    tokens = words(text)
    return set(zip(tokens, tokens[1:], tokens[2:]))


def screen(raw, protocol, cases, keys):
    official = protocol["official"]
    require(digest(raw) == official["sha256"], "Expected the exact pinned FinanceGym file")
    tasks = [json.loads(line) for line in raw.splitlines()]
    require(len(tasks) == len({t["task_id"] for t in tasks}) == 400, "Expected 400 distinct official tasks")
    dates = [date.fromisoformat(t["cutoff"]) for t in tasks]
    exact = 0
    candidates = []
    highest = 0.0
    for case in sorted(cases, key=lambda c: c["case_id"]):
        cid = case["case_id"]
        require(date.fromisoformat(case["cutoff"][:10]) < min(dates), "Cutoff overlaps official period")
        aliases = [words(a) for a in keys[cid]["event_aliases"]]
        q = re.split(
            r" State one base-case direction| Explain one implication and one limitation",
            case["question"],
            maxsplit=1,
        )[0]
        left = shingles(q)
        for task in tasks:
            right = shingles(task["question"])
            score = len(left & right) / len(left | right)
            highest = max(highest, score)
            other = words(task["question"])
            exact += words(q) == other
            alias_match = any(
                alias == other[i : i + len(alias)] for alias in aliases for i in range(len(other))
            )
            if score >= 0.20 or alias_match:
                candidates.append((cid, task["task_id"]))
    return (
        tasks,
        {
            "official_sha256": official["sha256"],
            "official_tasks": len(tasks),
            "first_official_cutoff": str(min(dates)),
            "last_official_cutoff": str(max(dates)),
            "latest_local_cutoff": max(c["cutoff"][:10] for c in cases),
            "pairs_checked": len(tasks) * len(cases),
            "exact_duplicates": exact,
            "max_token_trigram_jaccard": round(highest, 6),
            "near_duplicate_threshold": 0.20,
            "flagged_pairs": len(candidates),
            "flagged_official_tasks": len({tid for _, tid in candidates}),
        },
        candidates,
    )


def audit_official(raw, protocol, cases, keys):
    _, result, candidates = screen(raw, protocol, cases, keys)
    actual = set(candidates)
    require(
        all(
            str(reason).strip()
            for key in keys.values()
            for reason in key["reviewed_different_events"].values()
        ),
        "Missing event review explanation",
    )
    signed = {(cid, task) for cid, key in keys.items() for task in key["reviewed_different_events"]}
    require(signed == actual, "Exclusion review missing or stale")
    result["unresolved_candidates"] = 0
    return result
