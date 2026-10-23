import unittest

from final import AUDIO_PROFILES, build_live_circle_announcement, build_live_over_summary, clean_player_name_to_hindi, prepare_tts_pronunciation_text


class HinglishCommentaryTests(unittest.TestCase):
    def test_single_run_has_consistent_hindi_script(self):
        result = build_live_circle_announcement({
            "over": "35.3", "circle_val": "1", "runs": "1",
            "bowler": "Liam Dawson", "batsman": "Dasun Shanaka",
            "matchup": "Liam Dawson to Dasun Shanaka",
        }, lang="hinglish")
        self.assertEqual(result["spoken_text"], "छत्तीसवें ओवर की तीसरी बॉल, एक रन लेकर स्ट्राइक बदल ली।")

    def test_wicket_preserves_player_names_and_hindi_numbers(self):
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
            "non_striker": "Dasun Shanaka", "non_striker_runs": 40, "non_striker_balls": 23,
        }, lang="hinglish")
        text = result["spoken_text"]
        self.assertIn("छत्तीसवें ओवर की चौथी बॉल", text)
        self.assertIn("Dasun Shanaka चालीस रन बनाकर", text)
        self.assertIn("Liam Dawson", text)
        self.assertIn("स्कोर अब दो सौ बासठ रन, चार विकेट के नुकसान पर", text)
        self.assertIn("Pavan Rathnayake एक सौ एक रन, अठासी गेंदें", text)
        self.assertNotRegex(text, r"(?<![A-Za-z])\d+(?:\.\d+)?")
        self.assertNotIn("ki ", text)
        self.assertNotIn(" off ", text)

    def test_over_recap_has_consistent_hindi_script_and_requires_six_legal_balls(self):
        five = [{"over": f"26.{i+1}", "circle_val": token, "runs": token}
                for i, token in enumerate(["1", "4", "0", "W", "6"])]
        score = {"total_runs": 194, "total_wickets": 3}
        self.assertEqual(build_live_over_summary(26, five, score, lang="hinglish"), "")
        six = [{"over": f"26.{i+1}", "circle_val": token, "runs": token, "matchup": f"batter{i}"}
               for i, token in enumerate(["1", "4", "0", "W", "6", "1"])]
        score.update({
            "striker": "Dasun Shanaka", "striker_runs": 1, "striker_balls": 2,
            "non_striker": "Pavan Rathnayake", "non_striker_runs": 74, "non_striker_balls": 67
        })
        recap = build_live_over_summary(26, six, score, lang="hinglish")
        self.assertIn("सत्ताईसवाँ ओवर पूरा हुआ", recap)
        self.assertIn("स्कोर अब एक सौ चौरानवे रन, तीन विकेट", recap)
        self.assertIn("रन रेट", recap)
        self.assertIn("सात दशमलव", recap)
        self.assertIn("Pavan Rathnayake चौहत्तर रन, सरसठ गेंदें", recap)
        self.assertNotRegex(recap, r"(?<![A-Za-z])\d+(?:\.\d+)?")
        self.assertNotIn(" complete", recap)
        self.assertNotIn(" per over", recap)

    def test_hi_and_hinglish_mode_use_same_madhur_style(self):
        ball = {
            "over": "35.3", "circle_val": "1", "runs": "1",
            "bowler": "Liam Dawson", "batsman": "Dasun Shanaka",
            "matchup": "Liam Dawson to Dasun Shanaka"
        }
        self.assertEqual(
            build_live_circle_announcement(ball, lang="hi")["spoken_text"],
            build_live_circle_announcement(ball, lang="hinglish")["spoken_text"]
        )

    def test_master_phonetic_dictionary_is_used_for_tts_not_display(self):
        display = (
            "Wicket! Dasun Shanaka pavilion laut rahe hain. "
            "Liam Dawson ko wicket mila. Batters: Pavan Rathnayake."
        )
        context = {
            "bowler": "Liam Dawson",
            "batsman": "Dasun Shanaka",
            "striker": "Pavan Rathnayake",
            "matchup": "Liam Dawson to Dasun Shanaka",
        }
        spoken = prepare_tts_pronunciation_text(display, context)
        self.assertIn("दासुन शनाका", spoken)
        self.assertIn("लियाम डॉसन", spoken)
        self.assertIn("पवन रथनायके", spoken)
        self.assertIn("Dasun Shanaka", display)  # original screen text is preserved
        self.assertEqual(clean_player_name_to_hindi("Pavan Rathnayake"), "पवन रथनायके")

    def test_circle_builder_returns_separate_pronunciation_text(self):
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
            "non_striker": "Dasun Shanaka", "non_striker_runs": 40, "non_striker_balls": 23,
        }, lang="hinglish")
        self.assertIn("Dasun Shanaka", result["spoken_text"])
        self.assertIn("Liam Dawson", result["spoken_text"])
        self.assertIn("दासुन शनाका", result["tts_text"])
        self.assertIn("लियाम डॉसन", result["tts_text"])
        self.assertIn("पवन रथनायके", result["tts_text"])

    def test_server_pronunciation_helper_uses_master_dictionary(self):
        from server import prepare_tts_pronunciation_text as server_pronunciation
        spoken = server_pronunciation(
            "Dasun Shanaka and Liam Dawson",
            {"batsman": "Dasun Shanaka", "bowler": "Liam Dawson"},
        )
        self.assertIn("दासुन शनाका", spoken)
        self.assertIn("लियाम डॉसन", spoken)

    def test_recap_uses_normal_madhur_profile(self):
        self.assertEqual(AUDIO_PROFILES[2], ("+5%", "+1Hz", "+5%"))


if __name__ == "__main__":
    unittest.main()
