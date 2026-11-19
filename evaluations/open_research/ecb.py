"""Reproduce the dated ECB report presented and downloaded through the public-source analyst desk."""

import json
import tempfile
import zipfile
from pathlib import Path

from scripts.open_desk_report import inspect
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint

FOLDER = Path(__file__).with_suffix("")


def verify():
    browser = load(FOLDER / "browser.json")
    archive = FOLDER / "run.zip"
    if fingerprint(archive.read_bytes()) != browser["archive_sha256"]:
        raise ValueError("The browser trial archive changed")
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(archive) as z:
        root = Path(tmp)
        for name in z.namelist():
            if name not in {"run.json", "memo.md", "result.json"} and not (
                name.startswith("searches/")
                and name.endswith(".json")
                and len(Path(name).parts) == 2
                and len(Path(name).stem) == 64
                and all(c in "0123456789abcdef" for c in Path(name).stem)
            ):
                raise ValueError("Unexpected public proof member")
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(z.read(name))
        info, report = inspect(root, load(root / "run.json"))
        result = load(root / "result.json")
        if (
            len(result["searches"]) != 3
            or any(s["max_date"] != info["cutoff"] for s in result["searches"])
            or len(report.memo) != browser["download_bytes"]
            or fingerprint(report.memo) != browser["download_sha256"]
            or info["state"] != browser["state"]
            or info["spent"] != browser["spent"]
            or info["call_count"] != browser["calls"]
            or len(report.passages) != browser["passages"]
            or report.passages[0].citation_url != browser["first_source"]
        ):
            raise ValueError("The reproduced report differs from the browser's output")
    return browser


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
