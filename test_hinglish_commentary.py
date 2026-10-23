import unittest

from final import (
    AUDIO_PROFILES,
    build_live_circle_announcement,
    build_live_over_summary,
    clean_player_name_to_hindi,
    prepare_tts_pronunciation_text,
)


class HinglishCommentaryTests(unittest.TestCase):
    def test_master_dictionary_pronunciation_is_used_for_tts_only(self):
        display = "Wicket! Dasun Shanaka pavilion laut rahe hain. Liam Dawson ko wicket mila. Pavan Rathnayake."
        spoken = prepare_tts_pronunciation_text(display, {
            "bowler": "Liam Dawson",
            "batsman": "Dasun Shanaka",
            "striker": "Pavan Rathnayake",
            "matchup": "Liam Dawson to Dasun Shanaka",
        })
        self.assertIn("दासुन शनाका", spoken)
        self.assertIn("लियाम डॉसन", spoken)
        self.assertIn("पवन रथनायके", spoken)
        self.assertIn("Dasun Shanaka", display)

    def test_master_dictionary_falls_back_to_name_rules(self):
        self.assertEqual(clean_player_name_to_hindi("Pavan Rathnayake"), "पवन रथनायके")
        self.assertEqual(clean_player_name_to_hindi("Liam Dawson"), "लियाम डॉसन")

    def test_final_builder_returns_pronunciation_ready_text(self):
        result = build_live_circle_announcement({
            "over": "35.4", "circle_val": "W", "runs": "W",
            "bowler": "Liam Dawson", "batsman": "Dasun Shanaka",
            "matchup": "Liam Dawson to Dasun Shanaka",
            "last_dismissal": {
                "batter": "Dasun Shanaka", "runs": "40", "balls": "23",
                "howOut": "c Pavan Rathnayake b Liam Dawson"
            },
            "total_runs": 262, "total_wickets": 4,
            "striker": "Pavan Rathnayake", "striker_runs": 101, "striker_balls": 88,
        }, lang="hinglish")
        self.assertIn("tts_text", result)
        self.assertIn("दासुन शनाका", result["tts_text"])
        self.assertIn("लियाम डॉसन", result["tts_text"])
        self.assertIn("पवन रथनायके", result["tts_text"])

    def test_live_over_summary_requires_six_legal_deliveries(self):
        five = [
            {"over": f"26.{i+1}", "circle_val": token, "runs": token}
            for i, token in enumerate(["1", "4", "0", "W", "6"])
        ]
        score = {"total_runs": 194, "total_wickets": 3}
        self.assertEqual(build_live_over_summary(26, five, score, lang="hinglish"), "")

        six = [
            {"over": f"26.{i+1}", "circle_val": token, "runs": token, "matchup": f"batter{i}"}
            for i, token in enumerate(["1", "4", "0", "W", "6", "1"])
        ]
        score.update({
            "striker": "Dasun Shanaka", "striker_runs": 1, "striker_balls": 2,
            "non_striker": "Pavan Rathnayake", "non_striker_runs": 74, "non_striker_balls": 67,
        })
        summary = build_live_over_summary(26, six, score, lang="hinglish")
        self.assertIn("सत्ताईसवाँ ओवर पूरा हुआ", summary)
        self.assertIn("रन रेट", summary)
        self.assertIn("पवन रथनायके", prepare_tts_pronunciation_text(summary))

    def test_voice_profiles_remain_unchanged(self):
        self.assertEqual(AUDIO_PROFILES[2], ("+20%", "+12Hz", "+15%"))


if __name__ == "__main__":
    unittest.main()
