"""The research-side view of one frozen event. It needs no split registry or grading files."""

import json
import re
import subprocess
from dataclasses import dataclass
from io import BytesIO
from threading import Lock
from urllib.parse import quote

from pypdf import PdfReader

from .disney_hulu_case import CaseError, read_saved_file, require_entries
from .retrieval_gateway import Corpus
from .retrieval_sources import Block, instant, object_bytes
from .sol_baseline import fingerprint, json_bytes

CHUNK_CHARS = 1750
IMAGE_SIZE = 1400
CHOICES = ("lower", "within", "higher")


@dataclass(frozen=True)
class PacketCase:
    id: str
    question: str
    cutoff: str
    forecast_rule: dict | None

    @property
    def title(self):
        return "Financial research memo"

    @property
    def forecast_choices(self):
        return CHOICES if self.forecast_rule else ()

    def intake(self):
        return {"question": self.question, "cutoff": self.cutoff, "forecast_rule": self.forecast_rule}


def packet_digest(question, sources):
    # The source manifest itself contains the SHA-256 of every allowed original.
    return fingerprint(
        json_bytes({"question.json": fingerprint(question), "sources.json": fingerprint(sources)})
    )


def page_blocks(page, number):
    """Keep row order and mark large spaces between cells. The original page is always available."""
    text = page.extract_text(extraction_mode="layout")
    lines = [re.sub(r" {3,}", " | ", line.strip()) for line in text.splitlines() if line.strip()]
    chunks, current = [], []
    for line in lines:
        if current and len("\n".join((*current, line))) > CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = current[-1:] if len(current[-1]) < CHUNK_CHARS // 3 else []
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    for section, value in enumerate(chunks, 1):
        fragment = f"page={number}&search=" + quote(" ".join(value.split()[:6]), safe="")
        yield Block(f"PDF p{number} §{section}", value, fragment)


class PacketCorpus(Corpus):
    def __init__(self, directory, expected_sha256):
        require_entries(directory, ("question.json", "sources.json", "objects"))
        question = read_saved_file(directory / "question.json")
        sources = read_saved_file(directory / "sources.json")
        self.digest = packet_digest(question, sources)
        if self.digest != expected_sha256:
            raise CaseError("Case packet differs from registration")
        q = json.loads(question)
        self.case = PacketCase(q["case_id"], q["question"], q["cutoff"], q["forecast_rule"])
        self._objects = directory / "objects"
        self._redirects = {}
        self._sources, self._cache = {}, {}
        self._cache_lock = Lock()
        self.sources = json.loads(sources)
        require_entries(self._objects, [s["sha256"] + ".pdf" for s in self.sources])
        for s in self.sources:
            if instant(s["published"], s["timezone"]) > instant(self.case.cutoff) or not self._https(
                s["url"]
            ):
                raise CaseError("Ineligible case source")
            object_bytes(self._objects, s["sha256"], ".pdf")
            self._sources[s["url"]] = {
                **s,
                "kind": "pdf",
                "version_at": s["published"],
                "edition": s["published"],
                "family": s["id"],
            }

    def session(self, cutoff):
        if cutoff != self.case.cutoff:
            raise CaseError("This packet is bound to its registered cutoff")
        return super().session(cutoff)

    def _blocks(self, source):
        raw = object_bytes(self._objects, source["sha256"], ".pdf")
        with self._cache_lock:
            key = source["sha256"]
            if key not in self._cache:
                reader = PdfReader(BytesIO(raw))
                self._cache[key] = tuple(
                    block for i, page in enumerate(reader.pages, 1) for block in page_blocks(page, i)
                )
            return self._cache[key]

    def page_bytes(self, source, page):
        # DatedSession has reopened this exact original and validated the one-based page locator.
        raw = object_bytes(self._objects, source["sha256"], ".pdf")
        proc = subprocess.run(
            ["pdftoppm", "-f", page, "-l", page, "-singlefile", "-scale-to", str(IMAGE_SIZE), "-png", "-"],
            input=raw,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if proc.returncode or not proc.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise CaseError("Cannot render the admitted original PDF page")
        return proc.stdout
