"""Operator-only import of the exact historical editions in the retrieval catalog."""

import argparse
import json
import re
import subprocess
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from .retrieval_gateway import ARCHIVE, Corpus
from .retrieval_sources import edgar_blocks, wiki_blocks


def fetch(url):
    # Native curl uses the platform's certificate trust. No .curlrc, redirects or
    # protocol changes. The status is appended after the binary body, not parsed from it.
    result = subprocess.run(
        [
            "curl",
            "--disable",
            "--proto",
            "=https",
            "--silent",
            "--show-error",
            "--compressed",
            "--max-time",
            "30",
            "--user-agent",
            "OpenAI Codex research (https://openai.com/codex/; https://help.openai.com/)",
            "--write-out",
            "\n%{response_code}",
            "--url",
            url,
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    data, _, status = result.stdout.rpartition(b"\n")
    if status != b"200":
        raise ValueError(f"Publisher returned HTTP {status.decode(errors='replace')} for the exact address")
    return data


def save(objects, digest, suffix, data):
    if sha256(data).hexdigest() != digest:
        raise ValueError("Publisher bytes differ from the pinned SHA-256")
    (objects / (digest + suffix)).write_bytes(data)


class VisibleText(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.parts = []
        self.hidden_tag = None
        self.feed(document.decode("utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.hidden_tag = tag

    def handle_endtag(self, tag):
        if self.hidden_tag == tag:
            self.hidden_tag = None

    def handle_data(self, data):
        if self.hidden_tag is None:
            self.parts.append(data)


def words(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def check_html_passages(direct, blocks):
    published = words(" ".join(VisibleText(direct).parts))
    for block in blocks:
        if words(block.text) not in published:
            raise ValueError(f"Cited publisher page differs at {block.locator}")


def import_edition(source, objects):
    kind = source["kind"]
    url = source["url"]
    direct = fetch(url)
    if kind == "edgar":
        index = fetch(source["proof_url"])
        save(objects, source["selection"]["index_sha256"], ".htm", index)
        submission = fetch(url.rsplit("/", 1)[0] + "/" + source["edition"] + ".txt")
        primary = submission.split(b"</DOCUMENT>", 1)[0]
        name = urlsplit(url).path.rsplit("/", 1)[1].encode()
        if b"<SEQUENCE>1\n" not in primary or b"<FILENAME>" + name + b"\n" not in primary:
            raise ValueError("The SEC submission has a different primary document")
        match = re.search(rb"<XBRL>\r?\n(.*?)</XBRL>", primary, re.DOTALL)
        if not match or b"<html" not in direct:
            raise ValueError("Could not resolve the EDGAR document")
        # The direct SEC response can include a presentation script. The complete
        # submission preserves the issuer's bytes and the index records their size.
        save(objects, source["sha256"], ".htm", match[1])
        check_html_passages(direct, edgar_blocks(source, match[1], objects))
    elif kind == "pdf":
        save(objects, source["sha256"], ".pdf", direct)
        proof = fetch(source["proof_url"])
        day = date.fromisoformat(source["published"][:10])
        rendered = words(" ".join(VisibleText(proof).parts))
        date_forms = (f"{day:%B} {day.day}, {day.year}", f"{day.day} {day:%B %Y}")
        if not any(words(d) in rendered for d in date_forms):
            raise ValueError("Publisher release listing does not confirm the edition date")
        if urlsplit(url).hostname != "www.bde.es" and urlsplit(url).path.encode() not in proof:
            raise ValueError("Publisher release listing does not link this PDF")
    elif kind == "wikinews":
        if source["title"].encode() not in direct:
            raise ValueError("The permanent news article did not open")
        revision = fetch(source["proof_url"])
        save(objects, source["sha256"], ".json", revision)
        check_html_passages(direct, wiki_blocks(source, revision))
    else:
        raise ValueError("Unsupported historical edition")


def restore(destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Supply a new directory for a publisher import")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "archive"
        objects = staged / "objects"
        objects.mkdir(parents=True)
        catalog = (ARCHIVE / "catalog.json").read_bytes()
        (staged / "catalog.json").write_bytes(catalog)
        for source in json.loads(catalog)["sources"]:
            try:
                import_edition(source, objects)
            except (OSError, ValueError) as error:
                raise ValueError(f"Could not restore {source['id']}: {error}") from error
        counts = Corpus(staged).verify()
        staged.rename(destination)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        counts = restore(args.output_dir)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Archive error: {error}\n")
    print(f"Verified {len(counts)} publisher editions and {sum(counts.values())} passages.")


if __name__ == "__main__":
    main()
