import importlib.util
import json
import pathlib
import tempfile
import unittest
import contextlib
import io

spec = importlib.util.spec_from_file_location("model_eval", pathlib.Path(__file__).with_name("model-eval.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LedgerChecks(unittest.TestCase):
    def row(self, **overrides):
        row = dict(id="one", case="case", date="2026-10-25", model="example", effort="unknown",
                   route="unknown", role="worker", task_family="audit", layer="model", score=3,
                   cost_usd=None, evidence=["artifact.json"], note="synthetic")
        return dict(row, **overrides)

    def parse(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "ledger.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in rows))
            return module.load_rows(p)

    def test_unknown_cost_stays_unknown(self):
        self.assertIsNone(self.parse([self.row()])[0]["cost_usd"])

    def test_duplicate_observation_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.parse([self.row(), self.row()])

    def test_missing_evidence_and_invalid_numbers_rejected(self):
        for override in [dict(evidence=[]), dict(evidence=['']), dict(evidence=[None]), dict(evidence=[123]),
                         dict(score=True), dict(score=5), dict(cost_usd=-1), dict(cost_usd=float('nan'))]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.parse([self.row(**override)])

    def test_route_failure_can_be_unscored(self):
        row = self.parse([self.row(layer="route", score=None, cost_usd=0.12)])[0]
        self.assertIsNone(row["score"])
        self.assertEqual(row["layer"], "route")

    def test_supersession_replaces_score_and_cost_without_deleting_history(self):
        rows = self.parse([self.row(cost_usd=10), self.row(id='two', supersedes='one', score=2, cost_usd=1)])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['score'], rows[0]['cost_usd']), (2, 1))

    def test_bad_supersession_rejected(self):
        for correction in [self.row(id='two', supersedes='missing'), self.row(id='two', case='other', supersedes='one')]:
            with self.subTest(correction=correction), self.assertRaises(ValueError):
                self.parse([self.row(), correction])

    def test_list_estimate_does_not_become_billed_cost(self):
        rows = self.parse([self.row(reported_list_cost_usd=1.2)])
        self.assertIsNone(rows[0]['cost_usd'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.report(rows)
        self.assertIn('unknown (0/1)', output.getvalue())
        self.assertIn('CLI list-price estimates: $1.2000000', output.getvalue())
        for bad in [-1, True, float('nan')]:
            with self.assertRaises(ValueError):
                self.parse([self.row(reported_list_cost_usd=bad)])

    def test_report_shows_grouped_list_price_estimate(self):
        rows = self.parse([
            self.row(id='one', model='model-a', reported_list_cost_usd=0.25),
            self.row(id='two', model='model-a', score=None),
            self.row(id='three', model='model-b', cost_usd=0.5, reported_list_cost_usd=1),
        ])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.report(rows)
        text = output.getvalue()
        self.assertIn('| API-reported USD (coverage) | List-price estimate USD (coverage) |', text)
        self.assertIn('| unknown (0/2) | $0.250000 (1/2) |', text)
        self.assertIn('| $0.500000 (1/1) | $1.000000 (1/1) |', text)

    def test_report_shows_none_without_list_price_estimates(self):
        rows = self.parse([self.row(cost_usd=0.5)])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.report(rows)
        self.assertIn('| $0.500000 (1/1) | none (0/1) |', output.getvalue())


if __name__ == "__main__":
    unittest.main()
