import gzip
import tempfile
import unittest
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf.errors import PdfReadError, PdfStreamError

from evaluations.financegym import archive_failure, evidence, pacing, recovery
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.temporal_web import article


class ArchiveFailureTests(unittest.TestCase):
    def test_a_truncated_stream_has_its_own_exception_and_receipt(self):
        raw = b"%PDF-1.7\n1 0 obj\n"
        with self.assertRaises(PdfStreamError) as failure:
            article(raw)
        self.assertEqual(
            archive_failure.parser_error(raw, "PdfStreamError"), str(failure.exception)
        )
        self.assertIsNone(archive_failure.parser_error(raw, "PdfReadError"))

    def test_portable_proof_requires_original_bytes_error_and_receipt(self):
        raw = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
        with self.assertRaises(PdfReadError) as failure:
            article(raw)
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp)
            run = loc / "research"
            http = loc / "operator-http"
            run.mkdir()
            http.mkdir()
            record = {"state": "failed", "failure": "PdfReadError"}
            (run / "run.json").write_bytes(json_bytes(record))
            sha = fingerprint(raw)
            key = fingerprint(b"request")
            body = {
                "status": 200,
                "request": key,
                "byte_length": len(raw),
                "sha256": sha,
            }
            env = {"sha256": fingerprint(json_bytes(body)), "receipt": body}
            (http / (key + ".json")).write_bytes(json_bytes(env))
            (http / (sha + ".gz")).write_bytes(gzip.compress(raw))
            message = str(failure.exception)
            receipts = archive_failure.damaged_receipts(http, {key}, message)
            detail = {"error": message, "pdf_receipts": receipts}
            parts = evidence.portable(run, detail)
            portable = loc / "portable"
            for name, blob in parts.items():
                target = portable / name
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(blob)
            proof = {
                **record,
                "transport_requests": 1,
                "report_sha256": None,
                "archive_failure": detail,
            }
            archive_failure.verify_certificate(portable, proof)
            for field, value in [("error", "different"), ("pdf_receipts", [])]:
                changed = {**proof, "archive_failure": {**detail, field: value}}
                with self.assertRaises(ValueError):
                    archive_failure.verify_certificate(portable, changed)
            payload = portable / "failed-pdf" / (sha + ".bin")
            payload.write_bytes(raw + b"X")
            with self.assertRaisesRegex(ValueError, "bytes changed"):
                archive_failure.verify_certificate(portable, proof)
            payload.unlink()
            with self.assertRaisesRegex(ValueError, "bytes are missing"):
                archive_failure.verify_certificate(portable, proof)

    def test_research_verifier_is_restored_on_failure(self):
        original = recovery.attempt
        with (
            self.assertRaisesRegex(RuntimeError, "deliberate"),
            archive_failure.use_archive_failure_verifier(),
        ):
            self.assertIs(recovery.attempt, archive_failure.attempt)
            raise RuntimeError("deliberate")
        self.assertIs(recovery.attempt, original)

    def test_pacing_accepts_only_a_matching_terminal_assignment_with_settled_spend(
        self,
    ):
        row = {"task_id": "example", "cutoff": "2023-11-02", "question": "How much?"}
        ticket = {"task_id": "example"}
        fields = {
            **row,
            "assignment": ticket,
            "state": "failed",
            "failure": "PdfReadError",
            "budget_usd": "0.30",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "example/research/run.json"
            record.parent.mkdir(parents=True)
            budget = Mock()
            budget.reserve.return_value = nullcontext()
            with (
                patch.object(
                    pacing.runner, "route", return_value=(None, "single_astra", "0.30")
                ),
                patch.object(pacing.runner, "assignment", return_value=ticket),
                patch.object(
                    pacing.runner, "capture_one", side_effect=PdfReadError("bad PDF")
                ) as capture,
                patch.object(
                    pacing, "verify_ledger", return_value=(Decimal("0.01"), {})
                ) as ledger,
            ):
                record.write_bytes(json_bytes(fields))
                self.assertEqual(
                    pacing.capture_primary(root, row, {}, budget)[
                        "estimated_model_cost_usd"
                    ],
                    "0.01",
                )
                for field, value in [
                    ("cutoff", "2023-11-03"),
                    ("state", "running"),
                    ("failure", "unrelated"),
                    ("failure", "PdfStreamError"),
                ]:
                    record.write_bytes(json_bytes({**fields, field: value}))
                    with self.assertRaises(PdfReadError):
                        pacing.capture_primary(root, row, {}, budget)
                capture.side_effect = PdfStreamError("truncated")
                record.write_bytes(json_bytes({**fields, "failure": "PdfStreamError"}))
                self.assertEqual(
                    pacing.capture_primary(root, row, {}, budget)["state"], "failed"
                )
                capture.side_effect = PdfReadError("bad PDF")
                record.write_bytes(json_bytes(fields))
                ledger.return_value = Decimal("0.01"), {"call": Decimal("0.02")}
                with self.assertRaisesRegex(ValueError, "unsettled"):
                    pacing.capture_primary(root, row, {}, budget)
