"""Tests du hash de contenu — cœur de la déduplication cross-plateforme."""
import unittest

from scraper import compute_content_hash


class TestContentHash(unittest.TestCase):
    def test_normalisation_accents_casse_espaces(self):
        # La même offre vue sur deux plateformes doit produire le même hash,
        # malgré accents, casse et espaces différents.
        h1 = compute_content_hash("Responsable Sécurité", "Société Générale", "Paris")
        h2 = compute_content_hash("responsable securite", "societe  generale", " PARIS ")
        self.assertEqual(h1, h2)

    def test_offres_differentes_hashes_differents(self):
        h1 = compute_content_hash("TAM Cyber", "Acme Corp", "Paris")
        h2 = compute_content_hash("TAM Cyber", "Fortinet", "Paris")
        h3 = compute_content_hash("TAM Cyber", "Acme Corp", "Lyon")
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_champs_none_stables(self):
        self.assertEqual(
            compute_content_hash("Titre", None, None),
            compute_content_hash("Titre", None, None),
        )


if __name__ == "__main__":
    unittest.main()
