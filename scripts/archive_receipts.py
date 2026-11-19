"""Record operator-only HTTP receipts so an evaluator can reconstruct every dated public search."""

import gzip
import json
import re
import tempfile
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError

from .disney_hulu_case import CaseError, read_saved_file
from .run_checkpoint import publish
from .sol_baseline import fingerprint, json_bytes
from .temporal_web import MAX_BODY, ArchiveGateway, PublicHttp


class RecordedHttp:
    def __init__(self, directory, *, offline=False, transport=None):
        self.directory = Path(directory)
        self.offline = offline
        self.transport = transport or PublicHttp()
        self.lock = Lock()
        self.requests = {}
        self.seen = set()
        self.missing = set()

    def __call__(self, url, *, max_bytes=MAX_BODY):
        # Discovery and metadata stay private to the gateway. Workers only see admitted historical passages.
        key = fingerprint(json_bytes([url, max_bytes]))
        with self.lock:
            lock = self.requests.setdefault(key, Lock())
            self.seen.add(key)
        with lock:
            path = self.directory / (key + ".json")
            if path.is_file():
                envelope = json.loads(read_saved_file(path))
                receipt = envelope["receipt"]
                if (
                    receipt["request"] != key
                    or fingerprint(json_bytes(receipt)) != envelope["sha256"]
                ):
                    raise CaseError("Archive request receipt changed")
            elif self.offline:
                with self.lock:
                    self.missing.add(key)
                raise CaseError("An original archive request receipt is missing")
            else:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                receipt = {"request": key}
                try:
                    data = self.transport(url, max_bytes=max_bytes)
                except HTTPError as error:
                    receipt["status"] = error.code
                except (ValueError, OSError, RuntimeError):
                    receipt["status"] = 0
                else:
                    receipt.update(
                        status=200, sha256=fingerprint(data), byte_length=len(data)
                    )
                    blob = self.directory / (receipt["sha256"] + ".gz")
                    if not blob.exists():
                        publish(blob, gzip.compress(data, compresslevel=5, mtime=0))
                publish(
                    path,
                    json_bytes(
                        {"receipt": receipt, "sha256": fingerprint(json_bytes(receipt))}
                    ),
                )
            if receipt["status"] == 0:
                raise OSError("Archive transport did not return a response")
            if receipt["status"] != 200:
                raise HTTPError(
                    url, receipt["status"], "Archive returned an error", {}, None
                )
            if not isinstance(receipt.get("sha256"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", receipt["sha256"]
            ):
                raise CaseError("Invalid original content address")
            length = receipt["byte_length"]
            if type(length) is not int or not 0 <= length <= max_bytes:
                raise CaseError("Invalid archive payload size")
            blob = self.directory / (receipt["sha256"] + ".gz")
            with gzip.open(blob, "rb") as reader:
                data = reader.read(length + 1)
            if len(data) != length or fingerprint(data) != receipt["sha256"]:
                raise CaseError("The original archive bytes differ from their receipt")
            return data


def check_retrieval(output, directory, *, gateway_type=ArchiveGateway):
    """Independently rerun raw discovery and dated extraction off-network and compare with the model-visible records."""
    paths = sorted((output / "searches").glob("*.json"))
    if not paths:
        raise CaseError("No recorded search to audit")
    receipts = RecordedHttp(directory, offline=True)
    with tempfile.TemporaryDirectory() as temp:
        cutoff = json.loads(read_saved_file(output / "run.json"))["cutoff"]
        gateway = gateway_type(cutoff, Path(temp), http=receipts)
        sources = set()
        for path in paths:
            stored = json.loads(read_saved_file(path))
            expected = stored["body"]
            request = expected["request"]
            if (
                stored["sha256"] != fingerprint(json_bytes(expected))
                or request["max_date"] != cutoff
                or request["version"] != gateway.VERSION
                or path.stem != fingerprint(json_bytes(request))
            ):
                raise CaseError(
                    "Dated search does not match its assigned case or saved request"
                )
            actual = gateway.search(
                **{k: v for k, v in request.items() if k != "version"}
            )
            if actual != expected:
                raise CaseError(
                    "Original HTTP receipts do not reproduce the dated search"
                )
            sources.update(s["url"] for s in actual["sources"])
    # A missing transport result can look like a normal upstream error at the boundary. Verify the full inventory too.
    if receipts.missing or receipts.seen != {
        p.stem for p in Path(directory).glob("*.json")
    }:
        raise CaseError(
            "Archive request inventory differs from the independently replayed searches"
        )
    return {
        "searches": len(paths),
        "transport_requests": len(receipts.seen),
        "eligible_sources": len(sources),
    }
