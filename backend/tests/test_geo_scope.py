import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geo_scope import GeoScope

# In the container, config/ is mounted at /app/config (see geo_scope.DEFAULT_SCOPE_PATH).
# In a local checkout, it lives at <repo_root>/config/geo_scope.json instead.
_CONTAINER_PATH = Path("/app/config/geo_scope.json")
_LOCAL_PATH = Path(__file__).resolve().parents[2] / "config" / "geo_scope.json"
SCOPE_PATH = _CONTAINER_PATH if _CONTAINER_PATH.exists() else _LOCAL_PATH


class TestGeoScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = GeoScope(SCOPE_PATH)

    def assert_allowed(self, location, mode="onsite", remote=False):
        self.assertTrue(self.scope.allows(location, mode, remote)[0])

    def assert_rejected(self, location, mode="onsite", remote=False):
        self.assertFalse(self.scope.allows(location, mode, remote)[0])

    def test_idf(self):
        self.assert_allowed("Paris - 75")
        self.assert_allowed("Versailles - 78", "hybrid")
        self.assert_allowed("La Défense")

    def test_radius(self):
        self.assert_allowed("Fontainebleau - 77")
        self.assert_allowed("Compiègne - 60")
        self.assert_allowed("Chartres - 28")

    def test_distant_hybrid_or_onsite(self):
        self.assert_rejected("Reims - 51", "hybrid")
        self.assert_rejected("Lille, HDF, FR", "hybrid")
        self.assert_rejected("Lyon, ARA, FR", "onsite")
        self.assert_rejected("France", None)

    def test_full_remote_is_national(self):
        self.assert_allowed("Toulouse, France", "full_remote")
        self.assert_allowed("Bordeaux", None, True)


if __name__ == "__main__":
    unittest.main()
