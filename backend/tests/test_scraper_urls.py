import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_urls import select_job_urls


class TestJobUrlSelection(unittest.TestCase):
    def test_indeed_prefers_direct_employer_url(self):
        row = {
                "site": "indeed",
                "job_url": "https://fr.indeed.com/viewjob?jk=abc",
                "job_url_direct": "https://careers.example.com/jobs/42",
                "title": "RSSI",
            }
        canonical, sources = select_job_urls(row)
        self.assertEqual(canonical, "https://careers.example.com/jobs/42")
        self.assertEqual(
            sources,
            [
                "https://careers.example.com/jobs/42",
                "https://fr.indeed.com/viewjob?jk=abc",
            ],
        )

    def test_indeed_falls_back_to_listing(self):
        row = {
                "site": "indeed",
                "job_url": "https://fr.indeed.com/viewjob?jk=abc",
                "job_url_direct": None,
                "title": "RSSI",
            }
        self.assertEqual(
            select_job_urls(row)[0],
            "https://fr.indeed.com/viewjob?jk=abc",
        )

    def test_other_platform_keeps_listing_as_canonical(self):
        row = {
                "site": "linkedin",
                "job_url": "https://linkedin.example/jobs/1",
                "job_url_direct": "https://careers.example/jobs/1",
                "title": "TAM",
            }
        self.assertEqual(select_job_urls(row)[0], "https://linkedin.example/jobs/1")


if __name__ == "__main__":
    unittest.main()
