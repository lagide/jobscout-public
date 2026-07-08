"""Tests des fonctions de scoring déterministes (enrichment.py).

Ces invariants protègent la formule de pondération : toute modification de
_W_* ou des brackets qui casse un test est un changement de comportement
volontaire → mettre à jour le test en même temps que la formule.
"""
import unittest
from datetime import date, timedelta

from enrichment import (
    compute_final_score,
    compute_freshness_score,
    compute_salary_score,
    detect_work_mode,
)


class TestFinalScore(unittest.TestCase):
    def test_weighted_combination(self):
        # 8*0.60 + 10*0.15 + 5*0.10 + 5*0.10 + 5*0.05 (compétition neutre) = 7.55
        self.assertEqual(compute_final_score(8, 10, 5, 5), 7.55)

    def test_none_content_returns_none(self):
        self.assertIsNone(compute_final_score(None, 10, 10, 10))

    def test_missing_components_default_to_neutral(self):
        # 6*0.60 + 5*0.15 + 5*0.10 + 5*0.10 + 5*0.05 = 5.6
        self.assertEqual(compute_final_score(6, None, None, None), 5.6)

    def test_content_rejection_is_capped(self):
        # Un rejet contenu (≤2) reste un rejet même full-remote/frais/bien payé.
        self.assertLessEqual(compute_final_score(2, 10, 10, 10), 3.0)
        self.assertLessEqual(compute_final_score(0, 10, 10, 10), 3.0)

    def test_bounds(self):
        self.assertLessEqual(compute_final_score(10, 10, 10, 10, competition=10), 10.0)
        self.assertGreaterEqual(compute_final_score(3, 0, 0, 0, competition=0), 0.0)


class TestFreshness(unittest.TestCase):
    def test_unknown_date_is_neutral(self):
        self.assertEqual(compute_freshness_score(None), 5.0)
        self.assertEqual(compute_freshness_score("pas-une-date"), 5.0)

    def test_decay(self):
        today = date.today()
        self.assertEqual(compute_freshness_score(today), 10.0)
        self.assertEqual(compute_freshness_score(today - timedelta(days=10)), 7.5)
        self.assertEqual(compute_freshness_score(today - timedelta(days=40)), 3.0)
        self.assertEqual(compute_freshness_score(today - timedelta(days=120)), 1.0)

    def test_future_date_counts_as_fresh(self):
        self.assertEqual(
            compute_freshness_score(date.today() + timedelta(days=1)), 10.0
        )

    def test_accepts_iso_string(self):
        self.assertEqual(compute_freshness_score(date.today().isoformat()), 10.0)


class TestSalary(unittest.TestCase):
    def test_no_salary_is_slightly_negative(self):
        self.assertEqual(compute_salary_score(None, None), 4.0)

    def test_annual_brackets(self):
        self.assertEqual(compute_salary_score(35_000, 35_000), 1.0)
        self.assertEqual(compute_salary_score(65_000, 65_000), 6.5)
        self.assertEqual(compute_salary_score(120_000, 120_000), 10.0)

    def test_range_uses_median(self):
        # (50k + 70k) / 2 = 60k → bracket <70k = 6.5
        self.assertEqual(compute_salary_score(50_000, 70_000), 6.5)

    def test_interval_annualisation(self):
        # 5000/mois → 60k/an → 6.5 ; TJM 500 → 110k/an → 9.0
        self.assertEqual(compute_salary_score(5_000, 5_000, "monthly"), 6.5)
        self.assertEqual(compute_salary_score(500, 500, "tjm"), 9.0)


class TestWorkMode(unittest.TestCase):
    def test_detection(self):
        self.assertEqual(detect_work_mode("Poste 100% remote"), "full_remote")
        self.assertEqual(detect_work_mode("Télétravail partiel possible"), "hybrid")
        self.assertEqual(detect_work_mode("Présentiel requis, no remote"), "onsite")

    def test_hint_fallback(self):
        self.assertEqual(detect_work_mode("", is_remote_hint=True), "full_remote")
        self.assertEqual(detect_work_mode("", is_remote_hint=False), "onsite")
        self.assertIsNone(detect_work_mode("", is_remote_hint=None))


if __name__ == "__main__":
    unittest.main()
