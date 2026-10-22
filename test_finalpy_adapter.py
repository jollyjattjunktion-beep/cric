import unittest
from final import AUDIO_PROFILES, build_live_circle_announcement, build_live_over_summary

class FinalPyAdapterTests(unittest.TestCase):
    def test_four_event_is_exposed(self):
        result = build_live_circle_announcement({
            "over": "0.4", "circle_val": "4", "runs": "4",
            "bowler": "Wiaan Mulder", "batsman": "Beau Webster",
            "matchup": "Wiaan Mulder to Beau Webster",
        }, lang="hinglish")
        self.assertTrue(result["should_speak"])
        self.assertEqual(result["event"], "four")
        self.assertEqual(result["rate"], "+8%")
        self.assertEqual(result["pitch"], "+2Hz")

    def test_over_summary_export(self):
        balls = [{"over": "0." + str(i + 1), "circle_val": token, "runs": token, "matchup": str(i)}
                 for i, token in enumerate(["1", "4", "0", "W", "6", "1"])]
        summary = build_live_over_summary(0, balls, {"total_runs": 61, "total_wickets": 3}, lang="hinglish")
        self.assertIn("Over 1 complete", summary)

    def test_madhur_profiles_are_moderate(self):
        self.assertEqual(AUDIO_PROFILES[1], ("+3%", "+0Hz", "+0%"))
        self.assertEqual(AUDIO_PROFILES[4], ("+10%", "+3Hz", "+18%"))

if __name__ == "__main__":
    unittest.main()
