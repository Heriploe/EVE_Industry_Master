import unittest

from Utilities.blueprint_utils import build_jita_prices
from Utilities.industry_cost import invention_T2_runs


class TestIndustryCostUtilities(unittest.TestCase):
    def test_invention_t2_runs_without_decryptor(self):
        required_runs, me, te = invention_T2_runs(invention_skill_modifier=1.0)
        print(f"without_decryptor => required_runs={required_runs}, me={me}, te={te}")
        self.assertAlmostEqual(required_runs, 1 / 0.34 / 1)
        self.assertEqual(me, 0)
        self.assertEqual(te, 0)

    def test_invention_t2_runs_with_valid_decryptor(self):
        # 34202: Probability_Multiplier=1.8, Max_Run_Modifier=+4, ME=-1, TE=+4
        required_runs, me, te = invention_T2_runs(decryptor_id=34202, invention_skill_modifier=1.0)
        print(f"valid_decryptor(34202) => required_runs={required_runs}, me={me}, te={te}")
        self.assertAlmostEqual(required_runs, 1 / (0.34 * 1.8) / (1 + 4))
        self.assertEqual(me, -1)
        self.assertEqual(te, 4)

    def test_invention_t2_runs_with_unknown_decryptor(self):
        required_runs, me, te = invention_T2_runs(decryptor_id=999999, invention_skill_modifier=1.0)
        print(f"unknown_decryptor(999999) => required_runs={required_runs}, me={me}, te={te}")
        self.assertAlmostEqual(required_runs, 1 / 0.34 / 1)
        self.assertEqual(me, 0)
        self.assertEqual(te, 0)


    def test_invention_t2_runs_with_skill_modifier(self):
        required_runs, me, te = invention_T2_runs(decryptor_id=34202, invention_skill_modifier=1.2)
        print(f"valid_decryptor_with_skill_modifier => required_runs={required_runs}, me={me}, te={te}")
        self.assertAlmostEqual(required_runs, 1 / (0.34 * 1.8 * 1.2) / (1 + 4))
        self.assertEqual(me, -1)
        self.assertEqual(te, 4)


class TestPriceRegionFallback(unittest.TestCase):
    def test_vale_price_fallback_to_jita_when_missing(self):
        raw = [
            {
                "id": 34,
                "jita": {"lowest": 100.0, "volume": 1000},
                "vale_of_the_silent": {"lowest": 0.0, "volume": 0.0},
            }
        ]
        prices = build_jita_prices(raw, region_key="vale of slience")
        self.assertEqual(prices[34]["buy"], 100.0)
        self.assertEqual(prices[34]["volume"], 1000)

    def test_vale_price_used_when_available(self):
        raw = [
            {
                "id": 35,
                "jita": {"lowest": 100.0, "volume": 1000},
                "vale_of_the_silent": {"lowest": 120.0, "volume": 10.0},
            }
        ]
        prices = build_jita_prices(raw, region_key="vale_of_the_silent")
        self.assertEqual(prices[35]["buy"], 120.0)
        self.assertEqual(prices[35]["volume"], 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
