"""Make a searchable, self-contained reader for a sealed FinanceGym submission."""

import argparse
import json
from pathlib import Path

from evaluations.paired.corpus import require
from scripts.run_checkpoint import load, publish
from scripts.sol_baseline import fingerprint

from . import submission

TEMPLATE = Path(__file__).with_name("report-index.html")


def render(reports, proofs):
    items = []
    for report, proof in zip(reports, proofs, strict=True):
        require(
            proof["state"] in {"complete", "qualified"}
            and fingerprint(report["report"].encode("utf-8")) == proof["report_sha256"],
            "The report reader must use the verified final text and release decision",
        )
        items.append(
            {
                **{
                    k: report[k]
                    for k in ("question", "cutoff", "report", "num_docs_retrieved")
                },
                "task_id": proof["task_id"],
                "state": proof["state"],
                "cost": proof["estimated_cost_usd"],
            }
        )
    require(bool(items), "The submitted report list is empty")
    # The payload lives in an inert script element. Escape '<' even in a quoted JSON
    # string so source titles and quotations cannot terminate the element.
    data = json.dumps(items, ensure_ascii=True, separators=(",", ":")).replace(
        "<", r"\u003c"
    )
    return TEMPLATE.read_text(encoding="utf-8").replace("<!-- research-data -->", data)


def write(questions, sealed, output):
    manifest = load(sealed / "manifest.json")
    for name in ("answers.jsonl", "audit.json"):
        require(
            manifest["files"][name] == fingerprint((sealed / name).read_bytes()),
            "The reader input differs from the sealed submission",
        )
    _, audit = submission.verify_export(questions, sealed)
    reports = [
        json.loads(line)
        for line in (sealed / "answers.jsonl").read_bytes().splitlines()
    ]
    html = render(reports, audit["tasks_audited"])
    output.parent.mkdir(parents=True, exist_ok=True)
    publish(output, html.encode("utf-8"))
    return {"reports": len(reports), "answers_sha256": audit["answers_sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    parser.add_argument("sealed", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(write(args.questions, args.sealed, args.output), indent=2))
