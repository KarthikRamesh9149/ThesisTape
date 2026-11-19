import unittest

from evaluations.paired.grading_recovery import exhausted


class MaskedGraderContinuation(unittest.TestCase):
    def test_only_a_confirmed_token_limit_without_an_assessment_qualifies(self):
        provider = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 4096},
        }
        call = {"max_output_tokens": 4096, "provider": provider}
        self.assertEqual(
            exhausted({"calls": {"grade.asof": call}}), [("grade.asof", call)]
        )
        for other in (
            {"max_output_tokens": 4096},
            {**call, "output": "usable"},
            {**call, "provider": {**provider, "status": "completed"}},
            {
                **call,
                "provider": {**provider, "incomplete_details": {"reason": "other"}},
            },
            {**call, "provider": {**provider, "usage": {"output_tokens": 1024}}},
        ):
            self.assertFalse(exhausted({"calls": {"grade.asof": other}}))
