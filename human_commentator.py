"""Fast, event-aware radio commentary for the Preet Sports broadcaster.

The web player speaks concise cricket-native calls rather than the long CREX
source paragraph. Source commentary remains available to the visual feed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _space(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s+]", "", _space(value).lower())


def _number(token: str, default: int = 0) -> int:
    match = re.search(r"\d+", token or "")
    return int(match.group(0)) if match else default


def _player_names(info: Dict[str, Any]) -> tuple[str, str]:
    """Prefer the names attached to this delivery, especially on a wicket."""
    matchup = _space(info.get("matchup"))
    bowler = _space(info.get("bowler"))
    batter = _space(info.get("batter") or info.get("striker"))

    # CREX English layout: "Kagiso Rabada to Marnus Labuschagne".
    parts = re.split(r"\s+to\s+", matchup, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        bowler, batter = parts[0].strip(" ,:."), parts[1].strip(" ,:.")
    elif "," in matchup:
        # Hindi/localized CREX layout: "कागिसो रबाडा, मार्नस लाबुशाने को".
        left, right = matchup.split(",", 1)
        bowler = left.strip(" ,:.")
        batter = re.sub(r"\s*(?:को|to)\s*$", "", right.strip(" ,:."), flags=re.IGNORECASE)

    return bowler, batter


def _wicket_type(source: str) -> str:
    text = (source or "").lower()
    if re.search(r"\b(run[\s-]?out|runout)\b|रन आउट", text):
        return "runout"
    if re.search(r"\b(stumped|stumping)\b|स्टंप्ड|स्टंपिंग", text):
        return "stumped"
    if re.search(r"\b(lbw|leg before wicket)\b|एलबीडब्ल्यू", text):
        return "lbw"
    if re.search(r"\b(bowled|clean bowled)\b|बोल्ड|स्टंप्स बिखर", text):
        return "bowled"
    if re.search(r"\b(caught|catch|catches)\b|कैच|पकड़ा|पकड़", text):
        return "caught"
    return "wicket"


class HumanCommentator:
    """Fast local phrase builder; no LLM/API call is made for each delivery."""

    @staticmethod
    def classify(text: str, runs: Any = "") -> Dict[str, str]:
        source = _space(text).lower()
        token = _compact(runs)
        number = _number(token, 0)

        if token in {"w", "wkt", "out", "wicket"} or re.search(
            r"\b(dismissed|caught behind|caught by|run[\s-]?out|runout|stumped|lbw|bowled out|is out|given out)\b|कैच आउट|रन आउट|स्टंप(?:्ड|िंग)?|एलबीडब्ल्यू|आउट हो गए|आउट हो गया|बल्लेबाज़ आउट|बल्लेबाज आउट|विकेट गिरा|विकेट मिल गया|विकेट मिला|बोल्ड",
            source,
        ):
            return {"event": "wicket", "badge": "⚡ WICKET", "rate": "+30%", "pitch": "+20Hz", "emotion": "high_energy"}

        if "wd" in token or "wide" in token or "वाइड" in source:
            return {"event": "wide", "badge": "WIDE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if "nb" in token or "noball" in token or "no-ball" in token or "नो-बॉल" in source:
            return {"event": "noball", "badge": "NO BALL", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if "lb" in token or "legbye" in token:
            return {"event": "legbye", "badge": "LEG BYE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if re.fullmatch(r"\d*b|b\d*", token) and token:
            return {"event": "bye", "badge": "BYE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}

        if token == "6" or number == 6 or re.search(r"\b(six|sixer|maximum)\b|छक्का", source):
            return {"event": "six", "badge": "🔥 SIX", "rate": "+30%", "pitch": "+20Hz", "emotion": "high_energy"}
        if token == "4" or number == 4 or re.search(r"\b(four|boundary|chauka)\b|चौका|बाउंड्री पार", source):
            return {"event": "four", "badge": "🎯 FOUR", "rate": "+25%", "pitch": "+15Hz", "emotion": "medium_energy"}
        if token in {"0", "dot", "dotball"} or re.search(r"\b(no run|dot ball)\b|कोई रन नहीं|डॉट बॉल", source):
            return {"event": "dot", "badge": "🛡️ DOT BALL", "rate": "+15%", "pitch": "+10Hz", "emotion": "calm"}
        if number in {1, 2, 3, 5}:
            return {"event": "runs", "badge": "🏏 RUNS", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        return {"event": "ball", "badge": "🏏 BALL", "rate": "+15%", "pitch": "+10Hz", "emotion": "normal"}

    @staticmethod
    def _radio_line(
        source: str,
        event: str,
        token: str,
        bowler: str,
        batter: str,
        lang: str,
    ) -> str:
        """Create a short, spoken-first call to reduce browser TTS start latency."""
        hinglish = lang == "hinglish"
        n = _number(token, 1)
        bowler = bowler or ("bowler" if hinglish else "गेंदबाज़")
        batter = batter or ("batter" if hinglish else "बल्लेबाज़")

        if event == "wicket":
            kind = _wicket_type(source)
            if lang == "en":
                english_wicket_lines = {
                    "caught": f"Caught! {batter} is gone — {bowler} gets the breakthrough!",
                    "bowled": f"Bowled! {bowler knocks the stumps over — {batter} has to go!",
                    "lbw": f"LBW! The appeal is up and the wicket is given — {batter} is out!",
                    "runout": f"Run out! Sharp fielding and a quick throw — {batter} is gone!",
                    "stumped": f"Stumped! The keeper is quick and {batter} is out!",
                    "wicket": f"Wicket! {batter} is out — a big breakthrough for {bowler}!",
                }
                return english_wicket_lines[kind]
            if hinglish:
                wicket_lines = {
                    "caught": f"Caught! {batter} की पारी खत्म—{bowler} को बड़ा breakthrough मिला!",
                    "bowled": f"Bowled! {bowler} ने stumps बिखेर दिए—{batter} को लौटना होगा!",
                    "lbw": f"LBW! बड़ी अपील पर wicket मिल गया—{batter} आउट!",
                    "runout": f"Run out! तेज़ fielding का कमाल—{batter} की पारी खत्म!",
                    "stumped": f"Stumped! Keeper ने मौका नहीं छोड़ा—{batter} आउट!",
                    "wicket": f"WICKET! {batter} आउट—{bowler} को बड़ा breakthrough मिला!",
                }
                return wicket_lines[kind]
            wicket_lines = {
                "caught": f"कैच आउट! {batter} की पारी खत्म, {bowler} को बड़ी सफलता!",
                "bowled": f"बोल्ड! {bowler} ने स्टंप्स बिखेर दिए, {batter} को लौटना होगा!",
                "lbw": f"एलबीडब्ल्यू! विकेट मिल गया, {batter} आउट!",
                "runout": f"रन आउट! तेज़ फील्डिंग का कमाल, {batter} की पारी खत्म!",
                "stumped": f"स्टंप आउट! विकेटकीपर ने मौका नहीं छोड़ा, {batter} आउट!",
                "wicket": f"विकेट! {batter} आउट, {bowler} को बड़ा विकेट मिला!",
            }
            return wicket_lines[kind]

        if event == "six":
            if lang == "en":
                return f"What a hit! {batter} launches it over the rope for SIX!"
            return (f"क्या hit है! {batter} ने गेंद को stands के पार भेजा—SIX!"
                    if hinglish else f"क्या शानदार छक्का! {batter} ने गेंद को सीधे बाउंड्री के पार भेज दिया!")
        if event == "four":
            if lang == "en":
                return f"Timed beautifully! {batter} finds the gap for FOUR!"
            return (f"बेहतरीन timing! {batter} ने gap निकाला—चार रन, शानदार boundary!"
                    if hinglish else f"गज़ब की टाइमिंग! {batter} ने गैप निकाला और चार रन बटोर लिए!")
        if event == "wide":
            extra = f" {n} extra runs." if n > 1 else ""
            if lang == "en":
                return f"Wide! Too far outside the line; the batting side gets an extra.{extra}"
            return (f"Wide! गेंद line से बाहर, batting team को extra मिल गया.{extra}"
                    if hinglish else f"वाइड गेंद! लाइन से बाहर, बल्लेबाज़ी टीम को अतिरिक्त रन मिला.{extra}")
        if event == "noball":
            if lang == "en":
                return "No-ball! A costly mistake from the bowler; an extra goes to the batting side."
            return ("No-ball! गेंदबाज़ से गलती, बल्लेबाज़ी टीम को extra मिला."
                    if hinglish else "नो-बॉल! गेंदबाज़ से गलती, बल्लेबाज़ी टीम को अतिरिक्त रन मिला।")
        if event == "legbye":
            if lang == "en":
                return f"{n} leg-byes added to the score."
            return (f"Leg-bye से {n} रन आए—score में जुड़ गए।"
                    if hinglish else f"लेग बाय से {n} रन आए और स्कोर में जुड़ गए।")
        if event == "bye":
            if lang == "en":
                return f"{n} byes! The score moves on without bat on ball."
            return (f"Bye के {n} रन—batter के बिना बल्ला लगाए score आगे बढ़ा।"
                    if hinglish else f"बाय के {n} रन, स्कोर आगे बढ़ा।")
        if event == "dot":
            if lang == "en":
                return f"Good line and length from {bowler}; no run, dot ball."
            return ("अच्छी line और length! कोई run नहीं—dot ball."
                    if hinglish else "अच्छी लाइन और लेंथ, कोई रन नहीं—डॉट बॉल!")
        if event == "runs":
            if n == 1:
                if lang == "en":
                    return "Quick single; the strike rotates."
                return ("एक run लिया, strike rotate हो गई।" if hinglish else "एक रन लिया गया, स्ट्राइक बदल गई।")
            if n == 2:
                if lang == "en":
                    return "Two runs! Good running between the wickets."
                return ("दो runs! अच्छी running से score में दो जुड़े।" if hinglish else "दो रन! अच्छी दौड़ से स्कोर में दो जुड़ गए।")
            if n == 3:
                if lang == "en":
                    return "Three runs! Excellent running between the wickets."
                return ("तीन runs! तेज़ दौड़ और अच्छी running between the wickets।"
                        if hinglish else "तीन रन! तेज़ दौड़ से स्कोर में तीन जुड़े।")
            if n == 5:
                if lang == "en":
                    return "Five runs! Smart running and pressure on the fielding side."
                return ("पाँच runs! फील्डिंग पर दबाव और score तेज़ी से आगे।"
                        if hinglish else "पाँच रन! स्कोर तेज़ी से आगे बढ़ा।")

        # Fallback is deliberately short. We never replay the long source paragraph.
        if lang == "en":
            return f"{bowler} keeps it tight; {batter} cannot score."
        return (f"{bowler} की गेंद को {batter} ने खेला—अभी कोई बड़ा बदलाव नहीं।"
                if not hinglish else f"{bowler} की गेंद {batter} ने खेली—अभी score में बड़ा बदलाव नहीं।")

    def humanize(
        self,
        raw_text: str,
        ball_info: Optional[Dict[str, Any]] = None,
        lang: str = "hinglish",
        gemini_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        info = ball_info or {}
        source = _space(info.get("commentary") or raw_text)
        runs = _space(info.get("runs", ""))
        token = _compact(runs)
        event_info = self.classify(source, runs)

        over_text = _space(info.get("over"))
        match = re.search(r"(\d+)\.(\d+)", over_text)
        over_number = int(match.group(1)) if match else None
        ball_number = int(match.group(2)) if match else None
        is_legal_ball = not any(k in token for k in ("wd", "wide", "nb", "noball", "no-ball"))
        is_over_end = ball_number == 6 and is_legal_ball
        is_over_start = ball_number == 1

        bowler, batter = _player_names(info)
        spoken_text = self._radio_line(source, event_info["event"], token, bowler, batter, lang)

        return {
            "spoken_text": spoken_text,
            "source_text": source,
            "should_speak": bool(source or runs),
            **event_info,
            "bowler": bowler,
            "batter": batter,
            "is_over_start": is_over_start,
            "is_over_end": is_over_end,
            "completed_overs": over_number + 1 if is_over_end and over_number is not None else over_number,
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
