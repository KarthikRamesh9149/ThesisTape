import gzip
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from evaluations.financegym import runner
from evaluations.open_research import ecb
from scripts.archive_receipts import RecordedHttp, check_retrieval
from scripts.packet_archive import SavedSearches
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.temporal_web import ArchiveGateway


class FinanceGymAdapterTests(unittest.TestCase):
    def test_recovery_cannot_send_a_duplicate_original_request(self):
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "counts").mkdir()
            prompt = {"model": "example", "input": "unchanged"}
            payload = {**prompt, "max_output_tokens": 64}
            (root / "counts" / (fingerprint(json_bytes(prompt)) + ".json")).touch()
            (root / "run.json").write_bytes(
                json_bytes(
                    {
                        "calls": {
                            "lead": {"request_sha256": fingerprint(json_bytes(payload))}
                        }
                    }
                )
            )
            service = Mock()
            guarded = runner.DistinctProvider(service, root)
            with self.assertRaisesRegex(ValueError, "duplicate an original prompt"):
                guarded.count(prompt)
            with self.assertRaisesRegex(ValueError, "duplicate an original generation"):
                guarded.generate(payload)
            service.count.assert_not_called()
            service.generate.assert_not_called()
            guarded.count({**prompt, "input": "independent investigation"})
            service.count.assert_called_once()

    def test_an_unconfirmed_provider_call_stops_new_dispatches_and_a_resume_skips_it(
        self,
    ):
        import contextlib
        import io

        rows = [{"task_id": str(i)} for i in range(4)]
        with (
            tempfile.TemporaryDirectory() as t,
            patch.object(runner, "tasks", return_value=rows),
            patch.object(runner, "locked", return_value={}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            root = Path(t)
            first = {"task_id": "0", "state": "needs_reconciliation"}
            with patch.object(runner, "capture_one", return_value=first) as work:
                with self.assertRaisesRegex(ValueError, "Active tasks drained"):
                    runner.run(None, root, 1, 4)
                self.assertEqual(work.call_count, 1)
            retained = {**first, "already_recorded": True}
            values = [
                retained,
                *({"task_id": str(i), "state": "complete"} for i in range(1, 4)),
            ]
            with patch.object(runner, "capture_one", side_effect=values) as work:
                runner.run(None, root, 1, 4)
                self.assertEqual(work.call_count, 4)

    def test_original_ecb_memo_retains_its_exact_request_citations_and_cutoff(self):
        with zipfile.ZipFile(ecb.FOLDER / "run.zip") as z:
            data, memo = json.loads(z.read("result.json")), z.read("memo.md")
        row = {"cutoff": data["cutoff"], "question": data["question"]}
        out = runner.answer(row, data, memo)
        self.assertEqual(out["report"].encode(), memo)
        self.assertGreater(out["num_docs_retrieved"], 0)
        self.assertGreater(out["elapsed_s"], 0)
        with self.assertRaises(ValueError):
            runner.answer({**row, "question": row["question"] + " "}, data, memo)
        with self.assertRaises(ValueError):
            runner.answer(row, data, memo + b"Later fact")
        with self.assertRaises(ValueError):
            runner.answer(
                row,
                {
                    **data,
                    "searches": [
                        {**s, "max_date": "2025-01-01"} for s in data["searches"]
                    ],
                },
                memo,
            )

    def test_raw_date_bounded_discovery_replays_and_rejects_changed_bytes(self):
        day = "2024-02-29"
        original = "https://example.org/company-filing"

        def transport(url, **kwargs):
            host = urlsplit(url).hostname
            if host == "lite.duckduckgo.com":
                return f'<a class="result-link" href="{original}">Later live search title</a>'.encode()
            if host == "archive.org":
                return json_bytes(
                    {
                        "archived_snapshots": {
                            "closest": {
                                "timestamp": "20240229090000",
                                "status": "200",
                                "available": True,
                                "url": "http://web.archive.org/web/20240229090000/"
                                + original,
                            }
                        }
                    }
                )
            if host == "web.archive.org":
                return b"<html><title>Original filing</title><p>The issuer reported quarterly revenues before the cutoff date.</p></html>"
            raise AssertionError("Unknown test endpoint")

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "case/searches").mkdir(parents=True)
            (root / "case/run.json").write_bytes(json_bytes({"cutoff": day}))
            http = RecordedHttp(root / "http", transport=transport)
            g = ArchiveGateway(day, root / "cache", http=http)
            s = SavedSearches(g, root / "case/searches")
            seen = s.search(
                "quarterly issuer filing",
                max_date=day,
                titles=[],
                filing_terms="",
                limit=3,
            )
            self.assertNotIn("Later live search title", str(seen))
            self.assertEqual(
                check_retrieval(root / "case", root / "http"),
                {"searches": 1, "transport_requests": 3, "eligible_sources": 1},
            )
            original_bytes = transport("https://web.archive.org/web/dated")
            object_path = root / "http" / (fingerprint(original_bytes) + ".gz")
            saved = object_path.read_bytes()
            object_path.write_bytes(
                gzip.compress(original_bytes.replace(b"quarterly", b"late-year"))
            )
            with self.assertRaises(ValueError):
                check_retrieval(root / "case", root / "http")
            object_path.write_bytes(saved)
            archive_receipt = next(
                x
                for x in (root / "http").glob("*.json")
                if json.loads(x.read_bytes())["receipt"].get("byte_length")
                and (
                    root
                    / "http"
                    / (json.loads(x.read_bytes())["receipt"]["sha256"] + ".gz")
                ).is_file()
            )
            archive_receipt.unlink()
            with self.assertRaises(ValueError):
                check_retrieval(root / "case", root / "http")

    def test_full_export_refuses_missing_or_reworded_question(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            public = root / "questions.jsonl"
            answers = root / "answers.jsonl"
            rows = [
                {
                    "task_id": f"{i:024x}",
                    "question": f"Example question {i}?",
                    "cutoff": "2024-01-01",
                }
                for i in range(400)
            ]

            def save(path, records):
                path.write_text("".join(json.dumps(x) + "\n" for x in records))

            save(public, rows)
            with patch.dict(
                runner.PUBLIC, {"sha256": fingerprint(public.read_bytes())}
            ):
                submitted = [
                    {
                        **r,
                        "report": "Check the [dated source](https://example.org/filing).",
                    }
                    for r in rows
                ]
                save(answers, submitted)
                self.assertEqual(runner.validate_file(public, answers)["rows"], 400)
                save(answers, submitted[:-1])
                with self.assertRaises(ValueError):
                    runner.validate_file(public, answers)
                submitted[7]["question"] += " amended"
                save(answers, submitted)
                with self.assertRaises(ValueError):
                    runner.validate_file(public, answers)
                save(public, list(reversed(rows)))
                with self.assertRaises(ValueError):
                    runner.tasks(public)
