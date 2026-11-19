import base64
import json
import shutil
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.visual_reader import verify
from scripts.disney_hulu_case import CaseError
from scripts.research_team import release_state, run
from scripts.retrieval_gateway import Corpus
from scripts.sol_baseline import fingerprint
from scripts.team_research import case_for, research
from scripts.visual_reader import Cell, ambiguity, open_pages, render_cell

CELL = {
    "value": "435.6",
    "row": "Value of Shares Repurchased [shareholder return]",
    "column_group": "FY2022",
    "column": "12 months",
    "unit": "billions of yen",
    "note_ids": [4, 5],
}
APPROVE = {
    **dict.fromkeys(("value", "row", "column", "unit", "footnotes"), "supported"),
    "reason": "Compared the printed headings, cell and applicable notes.",
}


class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = case_for("toyota-buybacks")
        cls.corpus = cls.case.corpus()
        cls.rules = verify.load(cls.case.archive.parent / "evaluator/protocol.json")
        cls.pages = verify.verify_source(cls.case, cls.corpus, cls.rules)

    def workflow(self, route, *, case=None, replies=None):
        calls = {}

        def ask(stage, prompt, _):
            self.assertNotIn(stage, calls)
            calls[stage] = prompt
            default = APPROVE if stage.startswith("review.") else CELL
            return json.dumps((replies or {}).get(stage, default))

        memo, trace = research(case or self.case, self.corpus, ask, route=route)
        release_state(memo, trace, review_required=True)
        return memo, trace, calls

    def test_control_and_conditional_have_identical_text_and_isolated_review(self):
        _, control, text_calls = self.workflow("table-text")
        _, visual, visual_calls = self.workflow("visual")
        self.assertEqual(control["evidence"], visual["evidence"])
        self.assertEqual(set(text_calls), {"lead.memo", "review.initial"})
        self.assertTrue(all(isinstance(p["input"], str) for p in text_calls.values()))
        self.assertEqual(
            set(visual_calls), {"lead.memo", "review.initial", "visual.read"}
        )
        for stage in ("visual.read", "review.initial"):
            parts = visual_calls[stage]["input"][0]["content"]
            self.assertEqual(parts[1]["detail"], "high")
            prefix, data = parts[1]["image_url"].split(",")
            self.assertEqual(prefix, "data:image/png;base64")
            self.assertEqual(
                fingerprint(base64.b64decode(data, validate=True)),
                self.rules["page29_png_sha256"],
            )
            context = json.loads(parts[0]["text"])
            self.assertNotIn("page_reader", context)
            self.assertEqual(context["table"]["layout"], self.pages.table.text)
        control_input = json.loads(text_calls["lead.memo"]["input"])
        visual_input = json.loads(visual_calls["lead.memo"]["input"])
        self.assertEqual(visual_input.pop("page_reader"), CELL)
        self.assertEqual(control_input, visual_input)
        self.assertEqual(
            set(control_input), {"question", "cutoff", "table", "footnotes"}
        )
        self.assertIn("435.6149.9", control_input["table"]["layout"])
        self.assertEqual(len(control_input["footnotes"]["definitions"]), 8)

    def test_clear_row_skips_vision_and_unresolved_lead_can_escalate_once(self):
        case = replace(
            self.case,
            table=replace(self.case.table, row_anchor="[actual purchase]"),
            question="What was Toyota's FY2022 full-year actual purchase value?",
        )
        clear = {
            **CELL,
            "value": "404.6",
            "row": "Value of Shares Repurchased [actual purchase]",
            "note_ids": [4],
        }
        _, trace, calls = self.workflow(
            "visual", case=case, replies={"lead.memo": clear}
        )
        self.assertIsNone(trace["visual_reader"]["reason"])
        self.assertEqual(set(calls), {"lead.memo", "review.initial"})
        self.assertIsInstance(calls["review.initial"]["input"], str)
        _, trace, calls = self.workflow(
            "visual",
            case=case,
            replies={
                "lead.memo": {**clear, "value": None},
                "visual.read": clear,
                "lead.after_image": clear,
            },
        )
        self.assertEqual(
            trace["visual_reader"]["reason"],
            "lead could not resolve the target from the text",
        )
        self.assertEqual(
            set(calls),
            {"lead.memo", "visual.read", "lead.after_image", "review.initial"},
        )
        self.assertEqual(ambiguity("Label 1.0  7.2\nAnother 1.01.1", "Label"), None)
        self.assertIsNotNone(ambiguity("label\nlabel", "label"))
        self.assertIsNotNone(ambiguity("Price –", "Price"))

    def test_cutoff_denies_pixels_source_identity_and_all_model_calls(self):
        case = replace(self.case, cutoff="2023-05-09")
        memo, trace, calls = self.workflow("visual", case=case)
        self.assertEqual(calls, {})
        self.assertEqual(trace["evidence"], {})
        self.assertNotIn(b"435.6", memo)
        with self.corpus.session(case.cutoff) as session:
            self.assertIsNone(
                session.page_image(self.pages.table, max_date=case.cutoff)
            )
            self.assertTrue(all(not e["results"] for e in session.trace))
            self.assertNotIn("global.toyota", json.dumps(session.trace))
            with self.assertRaisesRegex(ValueError, "must match"):
                session.page_image(self.pages.table, max_date=self.case.cutoff)

    def test_page_binding_and_object_tampering_even_after_cached_read(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "archive"
            shutil.copytree(self.case.archive, root)
            pdf = self.rules["pdf_sha256"] + ".pdf"
            shutil.copy(self.case.archive_objects / pdf, root / "objects" / pdf)
            catalog = verify.load(root / "catalog.json")
            with Corpus(root).session(self.case.cutoff) as session:
                hit = open_pages(session, self.case.table).table
                self.assertIsNone(
                    session.page_image(
                        replace(hit, text="altered"), max_date=session.cutoff
                    )
                )
                self.assertIsNone(
                    session.page_image(self.pages.notes, max_date=session.cutoff)
                )
                self.assertIsNotNone(session.page_image(hit, max_date=session.cutoff))
                image = root / "objects" / (self.rules["page29_png_sha256"] + ".png")
                png = image.read_bytes()
                image.write_bytes(png + b"changed image")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    session.page_image(hit, max_date=session.cutoff)
                image.write_bytes(png)
                original = root / "objects" / pdf
                data = original.read_bytes()
                original.write_bytes(data + b"changed original")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    session.page_image(hit, max_date=session.cutoff)
                original.write_bytes(data)
            catalog["sources"][0]["page_images"]["29"]["pdf_sha256"] = "0" * 64
            (root / "catalog.json").write_text(json.dumps(catalog))
            with (
                Corpus(root).session(self.case.cutoff) as session,
                self.assertRaisesRegex(ValueError, "different PDF"),
            ):
                session.page_image(hit, max_date=session.cutoff)

    def test_field_repair_and_independent_recheck_or_withhold(self):
        invalid = {**CELL, "value": "435.6149.9"}
        wrong = {**CELL, "value": "404.6"}
        reject = {
            **APPROVE,
            "value": "revise",
            "reason": "The proposed cell is on the adjacent row.",
        }
        replies = {
            "lead.memo": invalid,
            "lead.memo.format_repair": wrong,
            "review.initial": reject,
        }
        memo, trace, calls = self.workflow("visual", replies=replies)
        self.assertIn(
            "format_feedback", json.loads(calls["lead.memo.format_repair"]["input"])
        )
        self.assertEqual(
            [r["approved"] for r in trace["review"]["rounds"]], [False, True]
        )
        self.assertIn(b"435.6", memo)
        self.assertIn("review.recheck", calls)
        memo, trace, _ = self.workflow(
            "visual", replies={**replies, "review.recheck": reject}
        )
        self.assertIsNone(memo)
        self.assertEqual(trace["review"]["status"], "withheld")
        self.assertEqual(verify.grade(trace, memo, self.pages, self.rules)["points"], 0)

    def test_frozen_grader_rejects_wrong_coordinates_scale_and_footnotes(self):
        def score(changes, edit=None):
            cell = Cell.model_validate({**CELL, **changes})
            record = {"review": {"rounds": [{"candidate": cell.model_dump()}]}}
            memo = render_cell(self.case, self.pages, cell)
            return verify.grade(
                record, edit(memo) if edit else memo, self.pages, self.rules
            )

        self.assertTrue(score({})["pass"])
        for changes in (
            {"row": "Value of Shares Repurchased [actual purchase]"},
            {"value": "404.6"},
            {"value": "299.9"},
            {"value": None},
            {"unit": "millions of yen"},
            {"column": "4Q"},
            {"column_group": "FY2023"},
            {"note_ids": [4]},
            {"note_ids": [5]},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(score(changes)["points"], 5)
        self.assertFalse(score({"note_ids": [3, 4, 5]})["pass"])
        self.assertFalse(
            score(
                {},
                lambda m: m.replace(self.pages.table.citation_url.encode(), b"missing"),
            )["pass"]
        )
        self.assertEqual(
            score({}, lambda m: m.replace(b"commission fees", b"fees"))["points"], 5
        )
        with self.assertRaisesRegex(CaseError, "no definition"):
            render_cell(
                self.case, self.pages, Cell.model_validate({**CELL, "note_ids": [45]})
            )

    def test_image_is_counted_before_dispatch_and_budget_rejection_costs_zero(self):
        test = self

        class Provider:
            def count(self, prompt):
                test.assertEqual(
                    prompt["input"][0]["content"][1]["type"], "input_image"
                )
                return 2000

            def generate(self, _):
                test.fail("No dispatch when the image and output do not fit")

        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "trial"
            with self.assertRaisesRegex(CaseError, "exceeds budget"):
                run(
                    self.case,
                    "visual",
                    directory,
                    Decimal("0.001"),
                    Provider(),
                    corpus=self.corpus,
                )
            receipt = verify.load(directory / "run.json")
            self.assertEqual(receipt["calls"], {})
            self.assertEqual(Decimal(receipt["estimated_model_cost_usd"]), 0)
