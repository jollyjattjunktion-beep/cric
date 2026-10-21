"""Fast, event-aware radio commentary for the Preet Sports broadcaster.

The web player speaks concise cricket-native calls rather than the long CREX
source paragraph. Source commentary remains available to the visual feed.
"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, Optional


def _space(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s+]", "", _space(value).lower())


def number_to_hindi(n: int) -> str:
    words = {
        0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच",
        6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस",
    }
    return words.get(int(n), str(n))


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

def _active_batter_score(info: Dict[str, Any], lang: str) -> str:
    """Compact live scorecard add-on; never uses CREX commentary prose."""
    players = []
    for prefix in ("striker", "non_striker"):
        name = _space(info.get(prefix))
        runs = info.get(f"{prefix}_runs")
        balls = info.get(f"{prefix}_balls")
        if not name or name.lower().startswith("batter ") or runs is None or balls is None:
            continue
        try:
            runs_i, balls_i = int(runs), int(balls)
        except (TypeError, ValueError):
            continue
        identity = _compact(name)
        if any(item[0] == identity for item in players):
            continue
        players.append((identity, name, runs_i, balls_i))

    if not players:
        return ""
    # Keep the score suffix short so Madhur can finish well before the next delivery.
    batting_line = ", ".join(
        f"{name} {runs} off {balls}" for _, name, runs, balls in players
    )
    team_runs, team_wickets = info.get("total_runs"), info.get("total_wickets")
    try:
        # Say the score naturally instead of reading the slash aloud.
        score = f"{int(team_runs)} for {int(team_wickets)}"
    except (TypeError, ValueError):
        score = ""
    # Keep score and batter figures in Hinglish even when the voice dropdown says Hindi.
    return f"Score {score}. {batting_line}." if score else f"Batters: {batting_line}."

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
        """Classify strictly from CREX's delivery-circle value, never from prose."""
        token = _compact(runs)
        n = _number(token, 0)

        # The circle/badge is the authority for the event. Do not let ordinary
        # commentary containing words like "wicket" or "six" override a numeric circle.
        if token in {"w", "wkt", "out", "wicket"}:
            return {"event": "wicket", "badge": "⚡ WICKET", "rate": "+30%", "pitch": "+20Hz", "emotion": "high_energy"}
        if "wd" in token or "wide" in token:
            return {"event": "wide", "badge": "WIDE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if "nb" in token or "noball" in token or "no-ball" in token:
            return {"event": "noball", "badge": "NO BALL", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if "lb" in token or "legbye" in token:
            return {"event": "legbye", "badge": "LEG BYE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}
        if re.fullmatch(r"\d*b|b\d*", token) and token:
            return {"event": "bye", "badge": "BYE", "rate": "+20%", "pitch": "+12Hz", "emotion": "normal"}

        if token.isdigit():
            if n == 0:
                return {"event": "dot", "badge": "🛡️ DOT BALL", "rate": "+15%", "pitch": "+10Hz", "emotion": "calm"}
            if n == 4:
                return {"event": "four", "badge": "🎯 FOUR", "rate": "+25%", "pitch": "+15Hz", "emotion": "medium_energy"}
            if n == 6:
                return {"event": "six", "badge": "🔥 SIX", "rate": "+30%", "pitch": "+20Hz", "emotion": "high_energy"}
            if n in {1, 2, 3, 5}:
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
        """Concise manual calls keyed only by the CREX circle token."""
        hinglish = lang == "hinglish"
        hindi = lang == "hi"
        n = _number(token, 1)
        bowler = bowler or ("bowler" if hinglish else "गेंदबाज़")
        batter = batter or ("batter" if hinglish else "बल्लेबाज़")

        if event == "wicket":
            # The circle determines that a wicket happened; source wording is used
            # only to identify the dismissal type when CREX supplies it.
            kind = _wicket_type(source)
            if hindi:
                return {
                    "caught": f"कैच आउट! {batter} की पारी खत्म, {bowler} को विकेट मिला!",
                    "bowled": f"बोल्ड! {bowler} ने स्टंप्स बिखेर दिए, {batter} आउट!",
                    "lbw": f"एलबीडब्ल्यू! बड़ी अपील, {batter} आउट!",
                    "runout": f"रन आउट! शानदार फील्डिंग, {batter} को लौटना होगा!",
                    "stumped": f"स्टंप्ड! विकेटकीपर ने मौका नहीं छोड़ा, {batter} आउट!",
                    "wicket": f"विकेट! {batter} आउट, {bowler} को बड़ी सफलता!",
                }[kind]
            if hinglish:
                return {
                    "caught": f"Caught! {batter} की पारी खत्म—{bowler} को wicket मिला!",
                    "bowled": f"Bowled! {bowler} ने stumps बिखेर दिए—{batter} आउट!",
                    "lbw": f"LBW! Big appeal, finger up—{batter} आउट!",
                    "runout": f"Run out! Sharp fielding—{batter} की पारी खत्म!",
                    "stumped": f"Stumped! Keeper ने मौका नहीं छोड़ा—{batter} आउट!",
                    "wicket": f"WICKET! {batter} आउट—{bowler} को breakthrough!",
                }[kind]
            return {
                "caught": f"Caught! {batter} is gone. {bowler} gets the wicket!",
                "bowled": f"Bowled! {bowler} knocks the stumps over. {batter} is out!",
                "lbw": f"LBW! Big appeal, finger up. {batter} is out!",
                "runout": f"Run out! Sharp fielding. {batter} is gone!",
                "stumped": f"Stumped! Quick work by the keeper. {batter} is out!",
                "wicket": f"Wicket! {batter} is out. A big breakthrough!",
            }[kind]

        if event == "six":
            if hindi:
                return f"छक्का! {batter} ने गेंद सीधे स्टैंड्स के पार भेज दी!"
            if hinglish:
                return f"SIX! {batter} ने ball stands के पार भेज दी—क्या hit है!"
            return f"SIX! {batter} launches it over the rope!"
        if event == "four":
            if hindi:
                return f"चौका! {batter} की शानदार टाइमिंग, गेंद बाउंड्री के पार!"
            if hinglish:
                return f"FOUR! Kya timing—{batter} ने ball boundary के पार भेजी!"
            return f"FOUR! Beautiful timing from {batter}!"
        if event == "wide":
            if hindi:
                return "वाइड! गेंद लाइन से बाहर, बल्लेबाज़ी टीम को अतिरिक्त रन।"
            if hinglish:
                return "Wide ball! Line se बाहर—batting side को extra मिला।"
            return "Wide! An extra for the batting side."
        if event == "noball":
            if hindi:
                return "नो-बॉल! एक्स्ट्रा रन और अगली गेंद पर फ्री हिट।"
            if hinglish:
                return "No-ball! Extra run, next ball free hit."
            return "No-ball! An extra and a free hit."
        if event == "legbye":
            if hindi:
                return f"लेग बाय के {number_to_hindi(n)} रन स्कोर में जुड़े।"
            if hinglish:
                return f"Leg-bye ke {n} runs, score आगे बढ़ा।"
            return f"{n} leg-byes added."
        if event == "bye":
            if hindi:
                return f"बाय के {number_to_hindi(n)} रन, स्कोर आगे बढ़ा।"
            if hinglish:
                return f"Bye ke {n} runs, score आगे बढ़ा।"
            return f"{n} byes added."
        if event == "dot":
            if hindi:
                return "कसी हुई लाइन और लेंथ—कोई रन नहीं, डॉट बॉल!"
            if hinglish:
                return "Tight line aur length—dot ball, koi run nahi!"
            return f"Good line and length from {bowler}. No run."
        if event == "runs":
            if n == 1:
                if hindi:
                    return "एक रन, स्ट्राइक बदल गई।"
                if hinglish:
                    return "Single लिया, strike rotate हो गई!"
                return "Quick single, strike rotates."
            if n == 2:
                if hindi:
                    return "दो रन, बढ़िया रनिंग!"
                if hinglish:
                    return "Two runs—good running between the wickets!"
                return "Two runs, good running."
            if n == 3:
                if hindi:
                    return "तीन रन, शानदार रनिंग बिटवीन द विकेट्स!"
                if hinglish:
                    return "Three runs—excellent running!"
                return "Three runs, excellent running."
            if n == 5:
                if hindi:
                    return "पाँच रन! शानदार दौड़ और फील्डिंग पर दबाव!"
                if hinglish:
                    return "Five runs! Smart running, score तेज़ी से आगे!"
                return "Five runs! Smart running."
        if hindi:
            return f"{batter} ने गेंद खेली।"
        if hinglish:
            return f"{batter} ने ball खेली।"
        return f"{batter} plays the ball."

    def humanize(
        self,
        raw_text: str,
        ball_info: Optional[Dict[str, Any]] = None,
        lang: str = "hinglish",
        gemini_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        info = ball_info or {}
        # Circle value is the single source of truth for the spoken event.
        # "runs" remains a backward-compatible fallback for existing parsers.
        source = _space(info.get("commentary") or raw_text)
        runs = _space(info.get("circle_val") or info.get("runs", ""))
        token = _compact(runs)
        event_info = self.classify("", token)

        over_text = _space(info.get("over"))
        match = re.search(r"(\d+)\.(\d+)", over_text)
        over_number = int(match.group(1)) + 1 if match else None
        ball_number = int(match.group(2)) if match else None
        is_legal_ball = not any(k in token for k in ("wd", "wide", "nb", "noball", "no-ball"))
        is_over_end = ball_number == 6 and is_legal_ball
        is_over_start = ball_number == 1

        bowler, batter = _player_names(info)
        spoken_text = self._radio_line(source, event_info["event"], token, bowler, batter, lang)

        # Prefix the ball number using the same completed-over convention as final.py.
        if over_number is not None and ball_number is not None:
            if lang == "hinglish":
                ball_word = {1: "pehli", 2: "doosri", 3: "teesri", 4: "chauthi", 5: "paanchvi", 6: "aakhri"}.get(ball_number, str(ball_number))
                spoken_text = f"Over {over_number} ki {ball_word} ball. {spoken_text}"
            elif lang == "hi":
                ball_word = {1: "पहली गेंद", 2: "दूसरी गेंद", 3: "तीसरी गेंद", 4: "चौथी गेंद", 5: "पाँचवीं गेंद", 6: "आखिरी गेंद"}.get(ball_number, f"{ball_number}वीं गेंद")
                over_word = {1: "पहले", 2: "दूसरे", 3: "तीसरे", 4: "चौथे", 5: "पाँचवें", 6: "छठे", 7: "सातवें", 8: "आठवें", 9: "नौवें", 10: "दसवें", 20: "बीसवें"}.get(over_number, f"{over_number}वें")
                spoken_text = f"{over_word} ओवर की {ball_word}। {spoken_text}"
            else:
                spoken_text = f"Over {over_number}, ball {ball_number}. {spoken_text}"

        # Append the scorecard only when the circle records a scoring event or wicket;
        # dot balls stay short so speech can keep up with the live feed.
        if event_info["event"] in {"runs", "four", "six", "wicket", "wide", "noball", "legbye", "bye"}:
            score_context = _active_batter_score(info, lang)
            if score_context:
                spoken_text = f"{spoken_text} {score_context}"

        return {
            "spoken_text": spoken_text,
            "source_text": source,
            "circle_val": token,
            "should_speak": bool(token),
            **event_info,
            "bowler": bowler,
            "batter": batter,
            "is_over_start": is_over_start,
            "is_over_end": is_over_end,
            "completed_overs": over_number if is_over_end else None,
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

