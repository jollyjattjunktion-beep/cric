import re
import unittest

from final import AUDIO_PROFILES, build_live_circle_announcement, build_live_over_summary


class HinglishCommentaryTests(unittest.TestCase):
    def test_hinglish_wicket_keeps_names_and_hindi_number_words(self):
        result = build_live_circle_announcement({
            "over": "26.6", "circle_val": "W", "runs": "W",
            "bowler": "Jamie Overton", "batsman": "Avishka Fernando",
            "matchup": "Jamie Overton to Avishka Fernando",
            "last_dismissal": {
                "batter": "Avishka Fernando", "runs": "97", "balls": "82",
                "howOut": "c Pavan Rathnayake b Jamie Overton"
            },
            "total_runs": 190, "total_wickets": 3,
            "striker": "Pavan Rathnayake", "striker_runs": 72, "striker_balls": 64,
            "non_striker": "Avishka Fernando", "non_striker_runs": 97, "non_striker_balls": 82,
        }, lang="hinglish")
        self.assertTrue(result["should_speak"])
        self.assertIn("Avishka Fernando", result["spoken_text"])
        self.assertIn("Jamie Overton", result["spoken_text"])
        self.assertIn("सत्ताईसवें over ki आखिरी ball", result["spoken_text"])
        self.assertNotRegex(result["spoken_text"], r"(?<![A-Za-z])\d+(?:\.\d+)?")

    def test_over_recap_uses_hindi_number_words_and_needs_six_legal_balls(self):
        short = [
            {"over": f"26.{i + 1}", "circle_val": token, "runs": token}
            for i, token in enumerate(["1", "4", "0", "W", "6"])
        ]
        self.assertEqual(
            build_live_over_summary(26, short, {"total_runs": 190, "total_wickets": 3}, lang="hinglish"),
            ""
        )
        full = [
            {"over": f"26.{i + 1}", "circle_val": token, "runs": token, "matchup": f"bat-{i}"}
            for i, token in enumerate(["1", "4", "0", "W", "6", "1"])
        ]
        summary = build_live_over_summary(
            26, full,
            {
                "total_runs": 194, "total_wickets": 3,
                "striker": "Dasun Shanaka", "striker_runs": 1, "striker_balls": 2,
                "non_striker": "Pavan Rathnayake", "non_striker_runs": 74, "non_striker_balls": 67,
            },
            lang="hinglish",
        )
        self.assertIn("सत्ताईस complete", summary)
        self.assertIn("Dasun Shanaka", summary)
        self.assertIn("सात दशमलव", summary)
        self.assertNotRegex(summary, r"(?<![A-Za-z])\d+(?:\.\d+)?")

    def test_summary_uses_madhur_normal_rate_pitch(self):
        self.assertEqual(AUDIO_PROFILES[2], ("+5%", "+1Hz", "+5%"))


if __name__ == "__main__":
    unittest.main()
