"""Tests de la blacklist titres/entreprises + rechargement config chaude."""
import json
import tempfile
import unittest
from pathlib import Path

import constants
from constants import is_company_blacklisted, is_title_blacklisted


class TestTitleBlacklist(unittest.TestCase):
    def test_target_roles_pass(self):
        # Les 4 familles cibles ne doivent JAMAIS être blacklistées.
        for title in (
            "Senior Technical Account Manager",
            "Technical Account Manager Cybersécurité",
            "Team Leader Sécurité Réseaux",
            "Responsable des Systèmes d'Information",
            "Manager Support Informatique",
            "Responsable Support Informatique",
            "Responsable Support IT",
            "IT Support Manager",
            "Service Desk Manager",
            "Responsable Service Desk",
            "Head of IT Support",
            "Responsable Helpdesk",
            "RSSI",
        ):
            self.assertFalse(is_title_blacklisted(title), title)

    def test_disqualified_roles_blocked(self):
        for title in (
            "Account Executive",
            "Account Manager",            # sans préfixe Technical = commercial
            "Stagiaire cybersécurité",
            "Technicien support informatique",
            "Support informatique N2",
            "Helpdesk N1",
            "Ingénieur FPGA",
            "Coordinateur SSI",           # sécurité incendie, pas SI
            "Consultant HSE",
        ):
            self.assertTrue(is_title_blacklisted(title), title)

    def test_empty_title(self):
        self.assertFalse(is_title_blacklisted(None))
        self.assertFalse(is_title_blacklisted(""))


class TestCompanyBlacklist(unittest.TestCase):
    def test_case_insensitive_exact_match(self):
        self.assertTrue(is_company_blacklisted("Symrise"))
        self.assertTrue(is_company_blacklisted("  SYMRISE  "))
        self.assertFalse(is_company_blacklisted("Acme Corp"))
        self.assertFalse(is_company_blacklisted(None))


class TestConfigReload(unittest.TestCase):
    """La blacklist doit être surchageable par config/blacklist.json (volume)."""

    def setUp(self):
        self._orig_dir = constants.CONFIG_DIR

    def tearDown(self):
        constants.CONFIG_DIR = self._orig_dir
        constants.reload_blacklist()  # retour à la config réelle

    def test_reload_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / constants.BLACKLIST_FILE
            cfg.write_text(json.dumps({
                "title_patterns": [r"foobar\s+specialist"],
                "title_abbr": [],
                "companies": ["evil corp"],
            }), encoding="utf-8")
            constants.CONFIG_DIR = Path(tmp)
            summary = constants.reload_blacklist()
            self.assertEqual(summary["title_patterns"], 1)
            self.assertIsNone(summary["error"])
            self.assertTrue(is_title_blacklisted("Foobar Specialist"))
            self.assertTrue(is_company_blacklisted("Evil Corp"))
            # Un pattern des défauts, absent du fichier, ne matche plus (remplacement).
            self.assertFalse(is_title_blacklisted("Account Executive"))

    def test_invalid_json_keeps_current_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / constants.BLACKLIST_FILE).write_text("{pas du json", encoding="utf-8")
            constants.CONFIG_DIR = Path(tmp)
            constants.reload_blacklist()
            # Les défauts restent actifs : jamais de blacklist vide par accident.
            self.assertTrue(is_title_blacklisted("Account Executive"))

    def test_invalid_regex_keeps_current_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / constants.BLACKLIST_FILE).write_text(json.dumps({
                "title_patterns": ["(regex non fermée"],
            }), encoding="utf-8")
            constants.CONFIG_DIR = Path(tmp)
            summary = constants.reload_blacklist()
            self.assertIsNotNone(summary["error"])
            self.assertTrue(is_title_blacklisted("Account Executive"))


if __name__ == "__main__":
    unittest.main()
