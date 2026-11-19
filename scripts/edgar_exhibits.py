"""Read indexed EDGAR exhibit components and explicitly selected prose sections."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


def submission_exhibit(submission, name):
    documents = re.findall(
        rb"(?ms)^<DOCUMENT>\r?\n.*?^</DOCUMENT>(?:\r?\n|$)", submission
    )
    matches = [
        d
        for d in documents
        if re.search(
            rb"(?m)^<FILENAME>" + re.escape(name.encode()) + rb"\r?$",
            d.split(b"<TEXT>", 1)[0],
        )
    ]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous exhibit in EDGAR submission")
    return matches[0]


class ExhibitProse(HTMLParser):
    """Read body paragraphs without incorporating scripts, styles or table cells."""

    def __init__(self, body):
        super().__init__()
        self.in_body = False
        self.skip = 0
        self.buffer = []
        self.paragraphs = []
        self.feed(body)
        self.close()

    def flush(self):
        text = " ".join("".join(self.buffer).split())
        if text:
            self.paragraphs.append(text)
        self.buffer.clear()

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if tag in ("table", "script", "style"):
            self.flush()
            self.skip += 1
        if tag in ("div", "p", "li", "h1", "h2", "h3"):
            self.flush()
        if tag == "br":
            self.buffer.append(" ")

    def handle_endtag(self, tag):
        if tag in ("table", "script", "style"):
            self.skip -= 1
        if tag in ("div", "p", "li", "h1", "h2", "h3", "body"):
            self.flush()
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        if self.in_body and not self.skip:
            self.buffer.append(data)


def sections(source, original, index):
    spec = source["selection"]
    path = urlsplit(source["url"]).path
    name = path.rsplit("/", 1)[1]
    page = index.decode()
    date = re.search(r'Filing Date</div>\s*<div class="info">([^<]+)</div>', page)
    accession = re.search(
        r'<div id="secNum">\s*<strong>.*?</strong>\s*([\d-]+)\s*</div>', page, re.DOTALL
    )
    header, sep, body = original.partition(b"<TEXT>")
    if (
        not sep
        or not re.search(rb"</TEXT>\r?\n</DOCUMENT>\s*\Z", body)
        or not date
        or not accession
        or date[1] != source["published"][:10]
        or accession[1] != source["edition"]
    ):
        raise ValueError("EDGAR exhibit date, accession or source envelope disagrees")
    fields = {
        "TYPE": spec["exhibit"],
        "SEQUENCE": str(spec["sequence"]),
        "FILENAME": name,
    }
    for key, value in fields.items():
        if f"<{key}>{value}" not in header.decode().splitlines():
            raise ValueError("EDGAR exhibit header disagrees with the index")
    rows = [
        r
        for r in re.findall(r"<tr\b[^>]*>.*?</tr>", page, re.DOTALL)
        if f'href="{path}"' in r
    ]
    if (
        len(rows) != 1
        or re.findall(r"<td\b[^>]*>(\d+)</td>", rows[0])
        != [fields["SEQUENCE"], str(len(original))]
        or f">{fields['TYPE']}</td>" not in rows[0]
    ):
        raise ValueError(
            "EDGAR index does not confirm this exhibit, sequence and byte size"
        )
    paragraphs = ExhibitProse(body.decode()).paragraphs
    quarter = re.search(r"\bQ([1-4])\b", source["title"])
    year = re.search(r"\bFY(\d{4})\b", source["title"])
    if quarter and year:
        ordinal = ("first", "second", "third", "fourth")[int(quarter[1]) - 1]
        printed = rf"\b(?:{ordinal} quarter (?:of )?fiscal (?:year )?{year[1]}|Q{quarter[1]}\s*(?:FY|Fiscal)\s*{year[1]})\b"
        if not re.search(printed, " ".join(paragraphs[:3]), re.IGNORECASE):
            raise ValueError(
                "The exhibit's printed fiscal period differs from its catalog title"
            )

    def find(prefix):
        matches = [i for i, p in enumerate(paragraphs) if p.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous exhibit paragraph: {prefix}")
        return matches[0]

    for section in spec["sections"]:
        begin = find(section["start"])
        end = find(section["end"]) if "end" in section else begin
        if end < begin:
            raise ValueError("Invalid exhibit paragraph range")
        yield section["name"], " ".join(paragraphs[begin : end + 1])
