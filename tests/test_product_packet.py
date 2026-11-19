import json
import tempfile
import unittest
from pathlib import Path

from evaluations.paired import corpus
from evaluations.paired.registration import registered
from scripts.disney_hulu_case import CaseError
from scripts.packet_archive import PacketArchive, SavedSearches
from scripts.paired_packet import PacketCorpus
from scripts.research_desk_agent import Paragraph, compile_paragraph, research
from scripts.sol_provider import ASTRA, MODEL


class ProductionPacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cid = "nintendo-fy2021-guidance"
        corpus.export_development(cid, root / "input")
        row = next(r for r in registered()["schedule"] if r["case_id"] == cid)
        self.corpus = PacketCorpus(root / "input", row["packet_sha256"])
        self.backend = PacketArchive(self.corpus)
        self.cache = root / "captured"
        self.saved = SavedSearches(self.backend, self.cache)

    def test_registered_original_cutoff_cache_and_literal_provenance(self):
        args = {
            "max_date": self.corpus.case.cutoff,
            "titles": [],
            "filing_terms": "",
            "limit": 3,
        }
        result = self.saved.search("forecast Nintendo results profit owners", **args)
        self.assertGreater(len(result["sources"]), 0)
        offline = SavedSearches(PacketArchive(self.corpus), self.cache, offline=True)
        self.assertEqual(
            offline.search("forecast Nintendo results profit owners", **args), result
        )
        with self.assertRaises(CaseError):
            self.saved.search(
                "forecast Nintendo results profit owners",
                **{**args, "max_date": "2021-05-11"},
            )
        first = result["sources"][0]
        with self.assertRaises(CaseError):
            PacketArchive(self.corpus).check_source(
                {**first, "title": "different title"}, args["max_date"]
            )
        p = Paragraph(
            section="Outlook",
            text="The question specifies {{BAND}} as of {{DATE}}.",
            citations=[{"passage": "P1", "quote": "Nintendo Co., Ltd."}],
        )
        sources = {"P1": {"text": "Nintendo Co., Ltd. released the original results."}}
        view = compile_paragraph(
            p,
            self.corpus.case.question,
            args["max_date"],
            sources,
            {},
            self.corpus.case.forecast_rule,
        )
        self.assertIn("±2%", view["text"])
        with self.assertRaises(CaseError):
            compile_paragraph(
                p.model_copy(update={"text": "Nintendo earned {{P1|234 billion}}."}),
                "",
                args["max_date"],
                sources,
                {},
                self.corpus.case.forecast_rule,
            )
        path = next(self.cache.glob("*.json"))
        changed = json.loads(path.read_bytes())
        changed["body"]["sources"][0]["blocks"] = ["wrong"]
        path.write_text(json.dumps(changed))
        with self.assertRaises(CaseError):
            offline.search("forecast Nintendo results profit owners", **args)

    def test_matched_single_controls_and_forecast_in_released_text(self):
        for profile, model in (("single_sol", MODEL), ("single_astra", ASTRA)):
            with self.subTest(profile=profile):
                stages = []

                def ask(
                    stage, prompt, limit, expected=model, observed=stages, **kwargs
                ):
                    self.assertEqual(prompt["model"], expected)
                    observed.append(stage)
                    if stage == "orchestrator.plan":
                        return json.dumps(
                            {
                                "decision": "Assess the issuer forecast",
                                "searches": [
                                    {"query": q, "titles": [], "filing_terms": ""}
                                    for q in (
                                        "Nintendo income forecast",
                                        "Nintendo components outlook",
                                        "Nintendo consolidated income",
                                    )
                                ],
                            }
                        )
                    if stage == "research.arithmetic":
                        return '{"calculations":[]}'
                    self.assertEqual(stage, "lead.memo")
                    context = json.loads(prompt["input"])
                    pid, original = next(iter(context["originals"].items()))
                    quote = original["text"].strip()[:65].strip()
                    ref = [{"passage": pid, "quote": quote}]
                    return json.dumps(
                        {
                            "direction": "within",
                            "paragraphs": [
                                {
                                    "section": "Answer",
                                    "text": "The original financial statement provides the comparison.",
                                    "citations": ref,
                                },
                                {
                                    "section": "Outlook",
                                    "text": "The test case assumes a result within {{BAND}}.",
                                    "citations": ref,
                                },
                            ],
                        }
                    )

                memo, trace = research(
                    self.corpus.case.question,
                    self.corpus.case.cutoff,
                    self.saved,
                    ask,
                    profile=profile,
                )
                self.assertEqual(
                    stages, ["orchestrator.plan", "research.arithmetic", "lead.memo"]
                )
                self.assertEqual(trace["direction"], "within")
                self.assertIn("±2%".encode(), memo)
                self.assertIn(b"#page=", memo)
