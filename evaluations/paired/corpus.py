"""Offline validation and case-scoped export of the preregistered paired corpus."""

import argparse
import io
import json
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from functools import cache
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from pypdf import PdfReader

from scripts.retrieval_sources import instant

ROOT = Path(__file__).resolve().parent
FILES = {
    "protocol.json",
    "protocol.md",
    "development.json",
    "development-sources.zip",
    "evaluator/blind-inputs.zip",
    "evaluator/development-grading.zip",
    "evaluator/blind-grading.zip",
    "exclusion-audit.json",
}
SOURCE_FIELDS = ("id", "title", "url", "published", "timezone", "sha256")
RULE_FIELDS = ("metric", "unit", "mode", "tolerance")
HEX = re.compile(r"[0-9a-f]{64}")
PUBLISHERS = {
    "www.apple.com",
    "www.berkshirehathaway.com",
    "assets.publishing.service.gov.uk",
    "www.nintendo.co.jp",
    "global.canon",
    "www.federalreserve.gov",
    "www.bankofcanada.ca",
    "www.bea.gov",
    "www.treasurydirect.gov",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "–": "-", "‐": "-", "−": "-", "⁄": "/"}))
    return "".join(text.split())


def read_json(path):
    return json.loads(path.read_bytes())


def checked_file(root, lock, name):
    data = (root / name).read_bytes()
    require(digest(data) == lock["files"][name], f"Changed frozen artifact: {name}")
    return data


def split_ids(cases, seed):
    groups = defaultdict(list)
    for case in cases:
        groups[case["stratum"]].append(case["case_id"])
    require(len(groups) == 6 and all(len(ids) == 6 for ids in groups.values()), "Unbalanced event registry")
    return {
        cid
        for ids in groups.values()
        for cid in sorted(ids, key=lambda i: digest(f"{seed}/{i}".encode()))[:2]
    }


def direction(reference, actual, tolerance, mode):
    base, result, width = map(Decimal, (reference, actual, tolerance))
    require(all(v.is_finite() for v in (base, result, width)) and width >= 0, "Invalid forecast comparator")
    require(mode in {"absolute", "relative"}, "Unknown comparator")
    if mode == "relative":
        width *= abs(base)
    if result < base - width:
        return "lower"
    if result > base + width:
        return "higher"
    return "within"


class Listing(HTMLParser):
    """Bind a publisher's link to its surrounding list item's publication date."""

    def __init__(self, url, html):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.links = {}
        self.item = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.item = ([], [])
        if tag == "a" and (href := dict(attrs).get("href")):
            url = unquote(urljoin(self.url, href))
            self.links.setdefault(url, [])
            if self.item:
                self.item[0].append(url)

    def handle_data(self, data):
        if self.item:
            self.item[1].append(data)

    def handle_endtag(self, tag):
        if tag == "li" and self.item:
            for url in self.item[0]:
                self.links[url].append(" ".join(self.item[1]))
            self.item = None


def pdf_instant(raw):
    # Some publishers append a redundant zero offset after Z.
    match = re.fullmatch(r"D:(\d{14})(Z(?:00'00')?|([+-])(\d\d)'?(\d\d)'?)", raw)
    require(match, "Missing or ambiguous PDF edition timestamp")
    zone = UTC
    if match[3]:
        offset = timedelta(hours=int(match[4]), minutes=int(match[5]))
        zone = timezone(offset if match[3] == "+" else -offset)
    return datetime.strptime(match[1], "%Y%m%d%H%M%S").replace(tzinfo=zone)


def printed_date(day, page):
    for month in (day.strftime("%B"), day.strftime("%b"), day.strftime("%b") + "."):
        for number in (str(day.day), f"{day.day:02}"):
            if any(
                normalized(form) in page
                for form in (f"{month} {number}, {day.year}", f"{number} {month} {day.year}")
            ):
                return True
    return False


def check_listing(source, proof, raw):
    html = raw.decode()
    url = unquote(source["url"])
    links = Listing(proof["url"], html).links
    day = date.fromisoformat(source["published"])
    require(url in links, "Historical PDF is missing from its publisher listing")
    host = urlsplit(proof["url"]).hostname
    if host == "www.apple.com":
        require(
            urlsplit(url).hostname == host
            and re.search(r'"datePublished"\s*:\s*"' + day.isoformat() + r'Z?"', html),
            "Apple publication date mismatch",
        )
    elif host == "www.gov.uk":
        marker = f"({day.day}.{day.month}.{day.year % 100:02})"
        require(
            urlsplit(url).hostname == "assets.publishing.service.gov.uk"
            and any(marker in item for item in links[url]),
            "CMA listing date mismatch",
        )
    else:
        raise ValueError("Unsupported publisher listing")


def pdf_name(source):
    value = source["sha256"]
    require(HEX.fullmatch(value), "Invalid document hash")
    return f"objects/{value}.pdf"


def validate_source(source, files, proofs, grading, cutoff=None):
    require(set(source) == set(SOURCE_FIELDS), "Unexpected source metadata")
    data = files.read(pdf_name(source))
    url = urlsplit(source["url"])
    require(
        digest(data) == source["sha256"]
        and url.scheme == "https"
        and url.hostname in PUBLISHERS
        and url.path.endswith(".pdf"),
        "Invalid primary source",
    )
    reader = PdfReader(io.BytesIO(data))
    day = date.fromisoformat(source["published"])
    available = instant(source["published"], source["timezone"])
    stamps = [reader.metadata[key] for key in ("/CreationDate", "/ModDate") if key in reader.metadata]
    require(stamps and max(map(pdf_instant, stamps)) <= available, "PDF edition postdates publication day")
    if cutoff is not None:
        require(available <= instant(cutoff), "Future edition in case input")

    @cache
    def page(number):
        require(type(number) is int and 1 <= number <= len(reader.pages), "Invalid citation page")
        return normalized(reader.pages[number - 1].extract_text(extraction_mode="layout"))

    if source["id"] in proofs:
        proof = proofs[source["id"]]
        raw = grading.read("proofs/" + proof["sha256"] + ".html")
        require(digest(raw) == proof["sha256"], "Publisher listing hash mismatch")
        check_listing(source, proof, raw)
    else:
        require(printed_date(day, page(1)), "Publication dateline missing from original PDF: " + source["id"])
    return page


def unique_members(archive, expected):
    names = archive.namelist()
    require(len(names) == len(set(names)) and set(names) == expected, "Unexpected archive contents")


def check_protocol(protocol):
    arms = protocol["development_runs"]
    require(
        len(arms) == 6 and sorted(arms.values()) == [8, 12, 12, 12, 12, 12],
        "Wrong development trial counts",
    )
    budget = protocol["budget_usd"]
    total = Decimal(budget["per_case_arm"]) * (sum(arms.values()) + protocol["blind_runs"])
    require(
        protocol["blind_runs"] == 48 and total == Decimal(budget["comparison_ceiling"]),
        "Inconsistent comparison ceiling",
    )
    require(
        total + Decimal(budget["prior_project_estimate"]) + Decimal(budget["official_planning_reserve"])
        <= Decimal(budget["project_cap"]),
        "Project ceiling exceeded",
    )
    require(sum(map(Decimal, protocol["weights"].values())) == 1, "Inconsistent quality weights")


def check_refs(refs, allowed, pages, case_id):
    require(refs, "Missing grading references")
    for ref in refs:
        require(ref["source"] in allowed and ref["anchors"], "Citation uses wrong evidence partition")
        page = pages[ref["source"]](ref["page"])
        require(
            all(a.strip() and normalized(a) in page for a in ref["anchors"]),
            f"Unresolved citation for {case_id}",
        )
    return len(refs)


def check_case(case, rubric, observation, packet, grading, proofs, counts, seen):
    cid = case["case_id"]
    allowed = {s["id"] for s in case["sources"]}
    pages = {}
    later = observation["sources"] if observation else []
    for documents, archive, as_of in ((case["sources"], packet, case["cutoff"]), (later, grading, None)):
        for source in documents:
            require(
                all(source[k] not in seen[k] for k in ("id", "sha256", "url")),
                "Source reused across events or partitions",
            )
            for k in ("id", "sha256", "url"):
                seen[k].add(source[k])
            pages[source["id"]] = validate_source(source, archive, proofs, grading, as_of)
    require(allowed and len(rubric["facts"]) == 3 and case["question"].strip(), "Incomplete event or rubric")
    counts["input_pdfs"] += len(allowed)
    counts["outcome_pdfs"] += len(later)
    for fact in rubric["facts"]:
        require(fact["award_if"].strip(), "Missing grading text")
        counts["page_citations"] += check_refs(fact["citations"], allowed, pages, cid)
        counts["factual_criteria"] += 1
    rule = case["forecast_rule"]
    require(bool(observation) == bool(rule), "Forecast partition mismatch")
    if not rule:
        return
    require(set(rule) == set(RULE_FIELDS), "Unexpected forecast metadata")
    require(len(later) == 1 and later[0]["published"] > case["cutoff"][:10], "Outcome was knowable by cutoff")
    for side, ids in (("reference", allowed), ("actual", {later[0]["id"]})):
        ref = observation[side + "_citation"]
        counts["page_citations"] += check_refs([ref], ids, pages, cid)
        literal = observation[side + "_printed"]
        require(
            literal in ref["anchors"] and numeric_anchor(literal) == Decimal(observation[side]),
            "Outcome value not tied to cited text",
        )
    require(
        direction(observation["reference"], observation["actual"], rule["tolerance"], rule["mode"])
        == observation["label"],
        "Forecast label mismatch",
    )


def verify(root=ROOT, official=None):
    lock = read_json(root / "freeze.json")
    require(lock["schema_version"] == 1 and set(lock["files"]) == FILES, "Incomplete freeze manifest")
    for name in sorted(FILES):
        checked_file(root, lock, name)
    protocol = read_json(root / "protocol.json")
    check_protocol(protocol)
    cases, keys = [], {}
    seen = defaultdict(set)
    counts = Counter()
    publications = set()
    for split, archive_name in (
        ("development", "development-sources.zip"),
        ("blind", "evaluator/blind-inputs.zip"),
    ):
        with (
            zipfile.ZipFile(root / archive_name) as packet,
            zipfile.ZipFile(root / f"evaluator/{split}-grading.zip") as grading,
        ):
            records = (
                read_json(root / "development.json")
                if split == "development"
                else json.loads(packet.read("inputs.json"))
            )
            asof = json.loads(grading.read("asof.json"))
            observations = json.loads(grading.read("outcomes.json"))
            rubrics, proofs = asof["cases"], asof["publication_proofs"]
            inputs = {"inputs.json"} if split == "blind" else set()
            expected_grading = {"asof.json", "outcomes.json"}
            ids = {c["case_id"] for c in records}
            require(
                len(ids) == len(records) and ids == set(rubrics) and ids.isdisjoint(keys),
                "Case identity mismatch",
            )
            require(
                set(observations) == {c["case_id"] for c in records if c["forecast_rule"]},
                "Wrong outcome key inventory",
            )
            require(set(proofs) <= {s["id"] for c in records for s in c["sources"]}, "Unbound listing")
            for p in proofs.values():
                publications.add(p["sha256"])
                expected_grading.add("proofs/" + p["sha256"] + ".html")
            for case in records:
                cid = case["case_id"]
                require(case["event"] not in seen["event"], "Duplicate event")
                seen["event"].add(case["event"])
                obs = observations.get(cid)
                inputs.update(pdf_name(s) for s in case["sources"])
                if obs:
                    expected_grading.update(pdf_name(s) for s in obs["sources"])
                check_case(case, rubrics[cid], obs, packet, grading, proofs, counts, seen)
            unique_members(packet, inputs)
            unique_members(grading, expected_grading)
            keys.update(rubrics)
            cases.extend(records)
            counts[split] = len(records)
            counts[split + "_forward"] = len(observations)
            if split == "development":
                dev_ids = ids
    require(
        counts["development"] == 12
        and counts["blind"] == 24
        and dev_ids == split_ids(cases, protocol["split_seed"]),
        "Frozen split mismatch",
    )
    require(counts["development_forward"] == 8 and counts["blind_forward"] == 16, "Unbalanced forecast split")
    counts.update(markets=len({c["market"] for c in cases}), publisher_listings=len(publications))
    require(dict(counts) == lock["counts"], "Corpus counts differ from freeze")
    audit = read_json(root / "exclusion-audit.json")
    require(
        audit["official_sha256"] == protocol["official"]["sha256"]
        and audit["pairs_checked"] == 14400
        and audit["exact_duplicates"] == audit["unresolved_candidates"] == 0,
        "Incomplete exclusion audit",
    )
    if official is not None:
        from .exclusion import audit_official

        require(
            audit_official(official.read_bytes(), protocol, cases, keys) == audit,
            "Official exclusion audit failed to reproduce",
        )
    return dict(sorted(counts.items()))


def numeric_anchor(value):
    """Decode publisher typography used in exact outcome cells and policy ranges."""
    text = unicodedata.normalize("NFKC", value).replace("⁄", "/").strip().replace(",", "")
    text = text.removesuffix("%").strip()
    if " to " in text:
        text = text.split(" to ")[-1]
    if "/" in text:
        match = re.fullmatch(r"(?:(\d+)[ -])?(\d+)/(\d+)", text)
        require(match, f"Invalid printed fraction {value}")
        return Decimal(match[1] or 0) + Decimal(match[2]) / Decimal(match[3])
    for word, sign in (
        ("decreased ", -1),
        ("increased ", 1),
        ("decreased at an annual rate of ", -1),
        ("increased at an annual rate of ", 1),
    ):
        if text.startswith(word) and re.fullmatch(r"\d+(\.\d+)?", text[len(word) :]):
            return sign * Decimal(text[len(word) :])
    return Decimal(text)


def agent_fields(case):
    prompt = {k: case[k] for k in ("case_id", "question", "cutoff")}
    rule = case["forecast_rule"]
    prompt["forecast_rule"] = {k: rule[k] for k in RULE_FIELDS} if rule else None
    sources = [{k: source[k] for k in SOURCE_FIELDS} for source in case["sources"]]
    return prompt, sources


def export_development(case_id, output, root=ROOT):
    lock = read_json(root / "freeze.json")
    cases = json.loads(checked_file(root, lock, "development.json"))
    selected = [case for case in cases if case["case_id"] == case_id]
    require(len(selected) == 1, "Only a frozen development case can be exported")
    require(not output.exists(), "Choose a fresh output path")
    case = selected[0]
    prompt, sources = agent_fields(case)
    with zipfile.ZipFile(io.BytesIO(checked_file(root, lock, "development-sources.zip"))) as archive:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
            packet = Path(tmp) / "packet"
            (packet / "objects").mkdir(parents=True)
            (packet / "question.json").write_bytes(json_bytes(prompt))
            (packet / "sources.json").write_bytes(json_bytes(sources))
            for source in sources:
                require(
                    instant(source["published"], source["timezone"]) <= instant(case["cutoff"]),
                    "Source postdates cutoff",
                )
                name = pdf_name(source)
                data = archive.read(name)
                require(digest(data) == source["sha256"], "Source digest mismatch")
                (packet / name).write_bytes(data)
            packet.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    verify_cmd = subs.add_parser("verify")
    verify_cmd.add_argument("--official", type=Path, help="Recheck the pinned FinanceGym JSONL locally")
    subs.add_parser("list-dev")
    export = subs.add_parser("export-dev")
    export.add_argument("case_id")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify":
        print(json.dumps(verify(official=args.official), sort_keys=True))
    elif args.command == "list-dev":
        lock = read_json(ROOT / "freeze.json")
        for case in json.loads(checked_file(ROOT, lock, "development.json")):
            print(case["case_id"])
    else:
        export_development(args.case_id, args.output)
        print(args.output)


if __name__ == "__main__":
    main()
