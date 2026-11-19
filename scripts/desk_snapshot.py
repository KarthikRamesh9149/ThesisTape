"""Open a verified historical research example in a read-only analyst session."""

import io
import re
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from .disney_hulu_case import CaseError, read_saved_file
from .run_checkpoint import load
from .sol_baseline import fingerprint


@contextmanager
def saved_browser_run(folder):
    browser = load(folder / "browser.json")
    raw = read_saved_file(folder / "run.zip")
    if fingerprint(raw) != browser["archive_sha256"]:
        raise CaseError("The browser trial archive changed")
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(raw)) as z:
        root = Path(tmp)
        names = set(z.namelist())
        if (
            len(names) != len(z.namelist())
            or not {"run.json", "memo.md", "result.json"} <= names
        ):
            raise CaseError("Incomplete or duplicate historical research artifact")
        for name in names:
            if not re.fullmatch(
                r"run\.json|memo\.md|result\.json|searches/[0-9a-f]{64}\.json", name
            ):
                raise CaseError("Unexpected public proof member")
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(z.read(name))
        yield root, browser
