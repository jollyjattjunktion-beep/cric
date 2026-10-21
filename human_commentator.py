"""Source-preserving commentary metadata for the Preet Sports web broadcaster.

The browser voice must speak the exact CREX commentary text, not generated or
rewritten commentary. This module only classifies the delivery and selects a
badge, pace, and pitch. It has no API calls and needs no extra dependency.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _space(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


class HumanCommentator:
    """Compatibility layer used by the web and YouTube broadcast pipelines."""

    @staticmethod
    def classify(text: str, runs: Any = "") -> Dict[str, str]:
        source = _space(text).lower()
        token = _space(runs).upper().replace(" ", "")
        if token in {"W", "WKT", "OUT"} or re.search(
            r"\b(wicket|bowled|caught|lbw|run out|stumped|dismissed)\b", source
        ):
            return {"event": "wicket", "badge": "⚡ WICKET", "rate": "+9%", "pitch": "+4Hz", "emotion": "high_energy"}
        if token == "6" or re.search(r"\b(six|sixer|maximum)\b", source):
            return {"event": "six", "badge": "🔥 SIX", "rate": "+8%", "pitch": "+3Hz", "emotion": "high_energy"}
        if token == "4" or re.search(r"\b(four|boundary|chauka)\b", source):
            return {"event": "four", "badge": "🎯 FOUR", "rate": "+6%", "pitch": "+2Hz", "emotion": "medium_energy"}
        if "WD" in token or "WIDE" in token:
            return {"event": "wide", "badge": "WIDE", "rate": "+3%", "pitch": "+1Hz", "emotion": "normal"}
        if "NB" in token or "NOBALL" in token or token == "NO BALL":
            return {"event": "noball", "badge": "NO BALL", "rate": "+3%", "pitch": "+1Hz", "emotion": "normal"}
        if token in {"0", "DOT", "DOTBALL"} or re.search(r"\b(no run|dot ball)\b", source):
            return {"event": "dot", "badge": "🛡️ DOT BALL", "rate": "+1%", "pitch": "+0Hz", "emotion": "calm"}
        if token in {"1", "2", "3", "1B", "2B", "1LB", "2LB"}:
            return {"event": "single", "badge": "🏏 RUNS", "rate": "+3%", "pitch": "+1Hz", "emotion": "normal"}
        return {"event": "ball", "badge": "🏏 BALL", "rate": "+4%", "pitch": "+1Hz", "emotion": "normal"}

    def humanize(
        self,
        raw_text: str,
        ball_info: Optional[Dict[str, Any]] = None,
        lang: str = "hinglish",
        gemini_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Prefer CREX's commentary field to the parser's optional prefix.
        info = ball_info or {}
        spoken_text = _space(info.get("commentary") or raw_text)
        runs = info.get("runs", "")
        event_info = self.classify(spoken_text, runs)

        over_text = _space(info.get("over"))
        match = re.search(r"(\d+)\.(\d+)", over_text)
        over_number = int(match.group(1)) if match else None
        ball_number = int(match.group(2)) if match else None
        token = _space(runs).upper().replace(" ", "")
        is_legal_ball = not any(k in token for k in ("WD", "WIDE", "NB", "NOBALL", "NO BALL"))
        is_over_end = ball_number == 6 and is_legal_ball
        is_over_start = ball_number == 1

        matchup = _space(info.get("matchup"))
        bowler = _space(info.get("bowler"))
        batter = _space(info.get("striker") or info.get("batter"))
        if " to " in matchup:
            left, right = matchup.split(" to ", 1)
            bowler = bowler or left.strip()
            batter = batter or right.strip()

        # This line is intentionally unchanged: no translation, padding, or AI rewrite.
        return {
            "spoken_text": spoken_text,
            "should_speak": bool(spoken_text),
            **event_info,
            "bowler": bowler,
            "batter": batter,
            "is_over_start": is_over_start,
            "is_over_end": is_over_end,
            "completed_overs": over_number + 1 if is_over_end and over_number is not None else over_number,
            "event": "over_end" if is_over_end and event_info["event"] not in {"wicket", "six", "four"} else event_info["event"],
            "badge": "🏁 OVER COMPLETE" if is_over_end and event_info["event"] not in {"wicket", "six", "four"} else event_info["badge"],
            "rate": "+5%" if is_over_end and event_info["event"] not in {"wicket", "six", "four"} else event_info["rate"],
            "pitch": "+1Hz" if is_over_end and event_info["event"] not in {"wicket", "six", "four"} else event_info["pitch"],
        }

    async def humanize_async(
        self,
        raw_text: str,
        ball_info: Optional[Dict[str, Any]] = None,
        lang: str = "hinglish",
        gemini_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.humanize(raw_text, ball_info, lang, gemini_api_key)


commentator = HumanCommentator()
