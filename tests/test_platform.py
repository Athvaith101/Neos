import unittest
from neos.platform import platform_manifest


class TestPlatformManifest(unittest.TestCase):
    def test_three_kits_are_exposed(self):
        d = platform_manifest()
        self.assertEqual([k["id"] for k in d["kits"]], ["urban", "low_income", "rural"])

    def test_evidence_boundary_is_explicit(self):
        d = platform_manifest()
        self.assertIn("OpenDSS", d["evidence_rule"])
        self.assertEqual(d["capabilities"]["hardware_control"], "not_implemented")
        self.assertEqual(d["capabilities"]["islanding"], "concept_simulation_only")


if __name__ == "__main__":
    unittest.main()
