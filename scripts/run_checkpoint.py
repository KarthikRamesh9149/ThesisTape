"""Durable local checkpoints and single-writer ownership for a research run."""

import json
import os
import signal
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from .disney_hulu_case import CaseError, read_saved_file
from .sol_baseline import fingerprint, json_bytes


def load(path):
    return json.loads(read_saved_file(path))


def sync_directory(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def publish(path, data):
    """Publish either the complete previous version or the complete new version."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".checkpoint-")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def own_run(directory):
    # SQLite releases the OS lock on process death, including SIGKILL. The
    # transaction holds ownership only; receipts are committed independently.
    path = directory / ".writer.sqlite3"
    if directory.is_symlink() or not directory.is_dir() or path.is_symlink():
        raise CaseError("Expected an existing local run directory")
    db = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise CaseError("Another process owns this run") from None
            raise
        yield
    finally:
        db.close()


class PauseRequested(Exception):
    """The operator interrupted a run. Existing dispatches are allowed to settle."""


@contextmanager
def pause_on_signal():
    pause = Event()

    def request(signum, frame):
        if not pause.is_set():
            pause.set()
            os.write(2, b"Pause requested. Saving any in-flight responses.\n")

    previous = {
        sig: signal.signal(sig, request) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield pause
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def specification(config, record):
    return fingerprint(
        json_bytes(
            {
                "configuration": config,
                **(
                    {"legacy_settled": record["legacy_settled"]}
                    if "legacy_settled" in record
                    else {}
                ),
                **{
                    key: record[key]
                    for key in ("run_id", "checkpoint_id", "created_at", "budget_usd")
                },
            }
        )
    )


def receipt_path(directory, stage):
    # Stage names come from several workflows; use the same fixed form for every path.
    return directory / "receipts" / (fingerprint(stage.encode()) + ".json")


def save_receipt(directory, receipt):
    path = receipt_path(directory, receipt["stage"])
    if path.parent.is_symlink():
        raise CaseError("Invalid private receipts directory")
    if not path.parent.exists():
        path.parent.mkdir(mode=0o700, exist_ok=True)
        sync_directory(directory)
    if path.exists() or path.is_symlink():
        raise CaseError("A provider receipt already exists for this assignment")
    publish(
        path,
        json_bytes({"receipt": receipt, "sha256": fingerprint(json_bytes(receipt))}),
    )


def load_receipt(path):
    envelope = load(path)
    if envelope["sha256"] != fingerprint(json_bytes(envelope["receipt"])):
        raise CaseError("Private provider receipt checksum changed")
    return envelope["receipt"]
