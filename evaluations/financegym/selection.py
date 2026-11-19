"""Select the tested research configuration from the independent results."""

import re

from evaluations.financebench.study import ARMS

# The optional statement-analysis route is limited to explicit accounting questions.
# Any request for an outlook or prospective action stays with the broadly tested finalist.
FORWARD = re.compile(
    r"\b(?:forecast\w*|predict\w*|outlook|future|expect\w*|will|would|could|might|scenario\w*|guidance|projected|prospect\w*|likely|implication\w*)\b",
    re.I,
)
STATEMENT = re.compile(
    r"\b(?:cash flows?|balance sheets?|income statements?|liabilit(?:y|ies)|receivables?|inventor(?:y|ies)|retained earnings|gross margin|operating margin|net income|net profit|operating (?:income|profit)|revenues?|shareholders?'? equity|earnings per share|free cash flow|capital expenditures?|depreciation|amorti[sz]ation|cost of (?:sales|revenue)|10-[KQ])\b",
    re.I,
)
QUANTIFIED = re.compile(
    r"\b(?:calculate|comput\w*|determine|compar\w*|how (?:much|many)|amount|ratio|percent\w*|rate|value|total|chang\w*|difference|increase\w*|decrease\w*|growth|amounted to|what (?:was|were|is|are))\b",
    re.I,
)


def choose(decision, financial):
    if decision["cutoff_violations"]:
        raise ValueError("Dated-source violations prevent candidate registration")
    if decision["mixed"] != "mixed" or decision["single"] not in {
        "single_astra",
        "single_sol",
    }:
        raise ValueError("Unrecognised finalist for the public analyst")
    general = decision["mixed"] if decision["advance"] else decision["single"]
    team, single = (financial["arms"][p] for p in ARMS)
    if team["cases"] != 24 or single["cases"] != 24:
        raise ValueError("The independent quantitative comparison is incomplete")
    q = financial["paired_90pct_interval"]
    numerical = ARMS[0] if q[0] > 0 and team["both"] > single["both"] else None
    return {"general": general, "statement_analysis": numerical}


def profile_for(question, policy):
    if (
        policy["statement_analysis"]
        and STATEMENT.search(question)
        and QUANTIFIED.search(question)
        and not FORWARD.search(question)
    ):
        return policy["statement_analysis"]
    return policy["general"]
