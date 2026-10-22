import unittest

from server import _legal_deliveries_in_over, _over_announcement


class OverCompletionTests(unittest.TestCase):
    def test_two_balls_do_not_complete_over(self):
        balls = [
            {"over": "26.1", "circle_val": "1", "runs": "1"},
            {"over": "26.2", "circle_val": "4", "runs": "4"},
        ]
        self.assertEqual(_legal_deliveries_in_over(26, balls), 2)
        self.assertIsNone(
            _over_announcement(26, balls, "hinglish", {"total_runs": 5, "total_wickets": 0})
        )

    def test_wide_does_not_count_as_legal_delivery(self):
        balls = [
            {"over": f"26.{i + 1}", "circle_val": token, "runs": token}
            for i, token in enumerate(["1", "4", "Wd", "0", "W", "6", "1"])
        ]
        self.assertEqual(_legal_deliveries_in_over(26, balls), 6)
        summary = _over_announcement(
            26, balls, "hinglish", {"total_runs": 194, "total_wickets": 3}
        )
        self.assertIsNotNone(summary)
        self.assertIn("सत्ताईस complete", summary)


if __name__ == "__main__":
    unittest.main()
