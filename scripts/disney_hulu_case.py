"""Import and verify ThesisTape's saved Disney/Hulu case without network access."""

import argparse
from datetime import date
from hashlib import sha256
from itertools import takewhile
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from xml.etree import ElementTree


CASE_ID = "disney-hulu-2023-11-02"
SAVED_CASE = Path(__file__).resolve().parents[1] / "cases" / CASE_ID
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1744489"
XHTML = "{http://www.w3.org/1999/xhtml}"
CASE_FILES = {
    "agent": ("manifest.json", "dis-20231101.htm"),
    "evaluator": (
        "manifest.json",
        "scoring.json",
        "dis-20250609.htm",
        "0001744489-23-000206-index.htm",
        "0001744489-25-000126-index.htm",
    ),
}
METADATA_FILES = ("agent/manifest.json", "evaluator/manifest.json", "evaluator/scoring.json")


class CaseError(ValueError):
    """The saved case or a candidate copy failed verification."""


def read_saved_file(path):
    if path.is_symlink() or not path.is_file():
        raise CaseError(f"Expected a regular file at {path}")
    return path.read_bytes()


def require_entries(directory, names):
    if directory.is_symlink() or not directory.is_dir():
        raise CaseError(f"Expected a directory at {directory}")
    observed = {path.name for path in directory.iterdir()}
    if observed != set(names):
        raise CaseError(f"{directory} must contain exactly these entries: {', '.join(names)}")


def read_case(directory):
    require_entries(directory, CASE_FILES)
    files = {}
    for role, allowed_names in CASE_FILES.items():
        require_entries(directory / role, allowed_names)
        for name in allowed_names:
            files[f"{role}/{name}"] = read_saved_file(directory / role / name)
    return files


def checked_bytes(files, path, expected_hash):
    data = files[path]
    if sha256(data).hexdigest() != expected_hash:
        raise CaseError(f"SHA-256 mismatch for {path}")
    return data


def filing_item(data, source):
    document = ElementTree.fromstring(data)
    if document.findtext(f"{XHTML}head/{XHTML}title") != source["title"]:
        raise CaseError(f"Unexpected title for {source['file']}")
    blocks = [
        " ".join("".join(block.itertext()).split())
        for block in document.findall(f"{XHTML}body/{XHTML}div")
    ]
    try:
        start = blocks.index(source["section"]) + 1
    except ValueError as error:
        raise CaseError(f"{source['section']} is missing in {source['file']}") from error
    return " ".join(takewhile(lambda block: not re.match(r"Item \d+\.\d{2}\b", block), blocks[start:]))


def verify_index(data, source, file_size):
    # Both pinned EDGAR index snapshots use this filing-date and document-table markup.
    page = data.decode("utf-8")
    filing_date = re.search(
        r'<div class="infoHead">Filing Date</div>\s*<div class="info">([^<]+)</div>', page
    )
    accession = re.search(r'<div id="secNum">\s*<strong>.*?</strong>\s*([\d-]+)\s*</div>', page, re.DOTALL)
    if not filing_date or not accession or (filing_date[1], accession[1]) != (
        source["filing_date"], source["accession"]
    ):
        raise CaseError(f"EDGAR index date or accession differs for {source['file']}")
    link = f'href="/ix?doc={urlsplit(source["source_url"]).path}"'
    for row in re.findall(r"<tr>.*?</tr>", page, re.DOTALL):
        if link in row:
            numbers = re.findall(r"<td\b[^>]*>(\d+)</td>", row)
            if numbers == ["1", str(file_size)] and '>8-K</td>' in row:
                return
    raise CaseError(f"EDGAR index does not list {source['file']} as the primary 8-K at {file_size} bytes")


def verify_citation(citation, source, item):
    if citation["source_id"] != source["id"] or citation["section"] != source["section"]:
        raise CaseError(f"Citation must point to {source['id']}, {source['section']}")
    anchors = citation["anchors"]
    if not anchors:
        raise CaseError(f"Citation has no passage anchors in {source['id']}")
    for anchor in anchors:
        if not anchor or anchor not in item:
            raise CaseError(f"Citation anchor is missing in {source['id']}, {source['section']}: {anchor!r}")


def verify_contents(files):
    agent, evaluator, scoring = (json.loads(files[name]) for name in METADATA_FILES)
    for metadata in (agent, evaluator, scoring):
        if metadata["schema_version"] != 1 or metadata["case_id"] != CASE_ID:
            raise CaseError("Unexpected case ID or schema version")
    cutoff = date.fromisoformat(agent["cutoff"])
    sources, items, sizes = {}, {}, {}
    for role, manifest in (("agent", agent), ("evaluator", evaluator)):
        source = manifest["source"]
        data = checked_bytes(files, f"{role}/{source['file']}", source["sha256"])
        eligible = date.fromisoformat(source["filing_date"]) <= cutoff
        if eligible != (role == "agent"):
            raise CaseError(f"{source['file']} is on the wrong side of the cutoff for {role}")
        sources[source["id"]] = source
        items[source["id"]] = filing_item(data, source)
        sizes[source["id"]] = len(data)

    indexes = evaluator["edgar_indexes"]
    if sorted(listing["source_id"] for listing in indexes) != sorted(sources):
        raise CaseError("Each filing needs its own EDGAR index")
    for listing in indexes:
        source_id = listing["source_id"]
        source = sources[source_id]
        accession = source["accession"]
        archive = f"{EDGAR_ARCHIVE}/{accession.replace('-', '')}"
        if (
            source["source_url"] != f"{archive}/{source['file']}"
            or listing["source_url"] != f"{archive}/{accession}-index.htm"
            or listing["complete_submission_url"] != f"{archive}/{accession}.txt"
            or listing["file"] != f"{accession}-index.htm"
        ):
            raise CaseError(f"Unexpected EDGAR provenance for {source['file']}")
        index_data = checked_bytes(files, f"evaluator/{listing['file']}", listing["sha256"])
        verify_index(index_data, source, sizes[source_id])

    for criterion in scoring["criteria"]:
        verify_citation(criterion["citation"], agent["source"], items[agent["source"]["id"]])
    verify_citation(scoring["later_outcome"]["citation"], evaluator["source"], items[evaluator["source"]["id"]])
    return agent, evaluator, len(scoring["criteria"])


def verify_case(directory):
    files = read_case(directory)
    for path in METADATA_FILES:
        if files[path] != read_saved_file(SAVED_CASE / path):
            raise CaseError(f"{path} differs from the saved case metadata")
    return verify_contents(files)


def import_case(destination, filings_dir=None):
    # Use the case's allowlist even when every filing is in the same input directory.
    files = {}
    for role, allowed_names in CASE_FILES.items():
        for name in allowed_names:
            source_directory = SAVED_CASE / role
            if filings_dir is not None and name.endswith(".htm"):
                source_directory = filings_dir
            files[f"{role}/{name}"] = read_saved_file(source_directory / name)
    summary = verify_contents(files)
    if destination.exists() or destination.is_symlink():
        if read_case(destination) != files:
            raise CaseError(f"{destination} contains a different case. Use a new destination.")
        return summary

    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".disney-hulu-", dir=destination.parent) as staging:
        staged_case = Path(staging) / "case"
        for relative, contents in files.items():
            path = staged_case / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        staged_case.rename(destination)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Rebuild the case from saved bytes")
    importer.add_argument("--output-dir", type=Path, required=True)
    importer.add_argument("--filings-dir", type=Path, help="Flat directory with both original filings and both EDGAR indexes")
    verifier = commands.add_parser("verify", help="Verify hashes, EDGAR indexes and rubric citations")
    verifier.add_argument("--case-dir", type=Path, default=SAVED_CASE)
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            agent, evaluator, criteria = import_case(args.output_dir, args.filings_dir)
            print(f"Imported {agent['case_id']} to {args.output_dir}")
        else:
            agent, evaluator, criteria = verify_case(args.case_dir)
            print(f"Verified {agent['case_id']} under {args.case_dir}")
    except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
        print(f"Case error: {error}", file=sys.stderr)
        return 1
    for role, manifest in (("agent", agent), ("evaluator", evaluator)):
        source = manifest["source"]
        print(f"{role}/{source['file']}: EDGAR filing date {source['filing_date']}, SHA-256 {source['sha256']}")
    print(f"Both EDGAR indexes verified. Resolved {criteria} rubric citations and 1 later-outcome citation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
