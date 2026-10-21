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
    """Short scorecard suffix from the live scoreboard, never from c2 commentary."""
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
    batting_line = ", ".join(f"{name} {runs} off {balls}" for _, name, runs, balls in players)
    team_runs, team_wickets = info.get("total_runs"), info.get("total_wickets")
    try:
        score = f"{int(team_runs)}/{int(team_wickets)}"
    except (TypeError, ValueError):
        score = ""
    if lang == "hinglish":
        return f"Live score {score}. Batters: {batting_line}." if score else f"Batters: {batting_line}."
    if lang == "hi":
        return f"स्कोर {score}। बल्लेबाज़: {batting_line}।" if score else f"बल्लेबाज़: {batting_line}।"
    return f"Score {score}. Batters: {batting_line}." if score else f"Batters: {batting_line}."



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
        """Manual ball calls adapted from final.py; CREX circle token chooses the call."""
        hinglish = lang == "hinglish"
        hindi = lang == "hi"
        n = _number(token, 1)
        bowler = bowler or ("bowler" if hinglish else "गेंदबाज़")
        batter = batter or ("batter" if hinglish else "बल्लेबाज़")

        # The text is intentionally handcrafted and concise; never read c2/source prose.
        if event == "wicket":
            kind = _wicket_type(source)  # Used only after the circle explicitly says W.
            if hindi:
                wicket_lines = {
                    "caught": f"कैच आउट! {batter} की पारी खत्म, {bowler} को बड़ी कामयाबी!",
                    "bowled": f"बोल्ड! {bowler} ने स्टंप्स बिखेर दिए, {batter} आउट!",
                    "lbw": f"ज़ोरदार अपील और उंगली उठी—एलबीडब्ल्यू! {batter} आउट!",
                    "runout": f"रन चुराना भारी पड़ा—रन आउट! {batter} पवेलियन लौटेंगे!",
                    "stumped": f"विकेटकीपर ने बेल्स उड़ा दीं—स्टंप्ड! {batter} आउट!",
                    "wicket": f"विकेट! {batter} आउट—{bowler} को बड़ा झटका मिला!",
                }
                return wicket_lines[kind]
            if hinglish:
                wicket_lines = {
                    "caught": f"Caught! {batter} की पारी खत्म—{bowler} को बड़ा breakthrough मिला!",
                    "bowled": f"Bowled! {bowler} ने stumps बिखेर दिए—{batter} आउट!",
                    "lbw": f"Big appeal, finger goes up—LBW! {batter} आउट!",
                    "runout": f"Run out! तेज़ fielding, sharp throw—{batter} की पारी खत्म!",
                    "stumped": f"Stumped! Keeper ने chance नहीं छोड़ा—{batter} आउट!",
                    "wicket": f"WICKET! {batter} out—{bowler} gets a big breakthrough!",
                }
                return wicket_lines[kind]
            wicket_lines = {
                "caught": f"Caught! {batter} is gone. {bowler} gets the breakthrough!",
                "bowled": f"Bowled! {bowler} knocks the stumps over. {batter} is out!",
                "lbw": f"LBW! The appeal is up and {batter} is given out!",
                "runout": f"Run out! Sharp fielding and a quick throw. {batter} is gone!",
                "stumped": f"Stumped! The keeper is quick and {batter} is out!",
                "wicket": f"Wicket! {batter} is out. A big breakthrough for {bowler}!",
            }
            return wicket_lines[kind]

        if event == "six":
            if hindi:
                return random.choice((
                    f"उठाकर मारा! गेंद आसमान में और सीधा स्टैंड्स के पार—छह रन, {batter} का ज़बरदस्त छक्का!",
                    f"क्या पावर हिटिंग! {bowler} की गेंद पर {batter} ने गेंद बाउंड्री के बाहर भेज दी—छक्का!",
                    f"बल्ले के बीचों-बीच लगी, फील्डर देखते रह गए—छह रन!",
                ))
            if hinglish:
                return random.choice((
                    f"उठाकर मारा! Ball हवा में और सीधे stands के पार—SIX!",
                    f"क्या power hitting! {batter} ने {bowler} की ball stands के पार भेज दी—SIX!",
                    f"Sweet spot से निकला shot, fielder देखते रह गए—maximum!",
                ))
            return f"What a hit! {batter} launches {bowler}'s delivery over the rope for SIX!"

        if event == "four":
            if hindi:
                return random.choice((
                    f"गज़ब की टाइमिंग! {batter} ने गैप निकाला और गेंद सीमा रेखा के पार—चौका!",
                    f"कलाइयों का जादू! खूबसूरत प्लेसमेंट, चार रन!",
                    f"ज़मीन के साथ दौड़ी गेंद और रस्सी के पार—शानदार चौका!",
                ))
            if hinglish:
                return random.choice((
                    f"क्या timing! {batter} ने gap निकाला और ball boundary के पार—FOUR!",
                    f"Sweet placement! Ball ground के साथ boundary तक—FOUR runs!",
                    f"Gap मिल गया, fielder late पड़े—{batter} के चार runs!",
                ))
            return f"Timed beautifully! {batter} finds the gap for FOUR!"

        if event == "wide":
            if hindi:
                return "लाइन से भटकी गेंद, अंपायर का इशारा—वाइड! बल्लेबाज़ी टीम को एक्स्ट्रा मिला।"
            if hinglish:
                return "Line से भटकी ball—wide! Batting side को extra मिला."
            return "Wide! Too far outside the line; the batting side gets an extra."

        if event == "noball":
            if hindi:
                return "नो-बॉल! गेंदबाज़ से गलती, बल्लेबाज़ी टीम को अतिरिक्त रन और अगली गेंद पर फ्री हिट।"
            if hinglish:
                return "No-ball! Bowler से mistake, batting side को extra—next ball free hit."
            return "No-ball! A costly mistake from the bowler; an extra goes to the batting side."

        if event == "legbye":
            if hindi:
                return f"गेंद पैड से लगकर निकली, लेग बाय के {number_to_hindi(n)} रन।"
            if hinglish:
                return f"Ball pads से लगी—leg-bye के {n} runs, score में जुड़ गए."
            return f"{n} leg-byes added to the score."

        if event == "bye":
            if hindi:
                return f"विकेटकीपर से गेंद निकली—बाय के {number_to_hindi(n)} रन!"
            if hinglish:
                return f"Keeper से ball निकली—bye के {n} runs!"
            return f"{n} byes! The score moves on without bat on ball."

        if event == "dot":
            if hindi:
                return random.choice((
                    "कसी हुई लाइन और लेंथ, बल्लेबाज़ ने संभलकर खेला—कोई रन नहीं, डॉट बॉल।",
                    f"शानदार गेंद! {batter} को छकाया, कोई रन नहीं।",
                    "सीधा फील्डर के हाथों में—रन का कोई मौका नहीं।",
                ))
            if hinglish:
                return random.choice((
                    f"Tight line और length! {batter} ने defend किया—dot ball, कोई run नहीं.",
                    f"{bowler} की अच्छी delivery—{batter} को run का chance नहीं मिला.",
                    "सीधे fielder के पास—no run, dot ball.",
                ))
            return f"Good line and length from {bowler}; no run, dot ball."

        if event == "runs":
            if n == 1:
                if hindi:
                    return random.choice((
                        "गेंद को गैप में धकेला और एक रन लेकर स्ट्राइक बदल ली।",
                        "हल्के हाथों से खेला और तेज़ी से एक सिंगल पूरा किया।",
                        "कलाइयों से मोड़ा, एक रन लिया—स्ट्राइक रोटेट हुई।",
                    ))
                if hinglish:
                    return random.choice((
                        "Gap में push किया—quick single, strike rotate हो गई.",
                        "Soft hands, fast running—एक run complete!",
                        f"{batter} ने gap निकाला और single ले लिया.",
                    ))
                return "Quick single; the strike rotates."
            if n == 2:
                if hindi:
                    return random.choice((
                        "गैप में खेलकर दोनों बल्लेबाज़ों ने दौड़कर दो रन पूरे किए!",
                        "सॉफ्ट हैंड्स से धकेला, बढ़िया तालमेल और दो रन निकाले!",
                    ))
                if hinglish:
                    return random.choice((
                        "Gap में shot, दोनों batters की तेज़ running—two runs complete!",
                        "Soft hands, sharp running—दो runs मिल गए!",
                    ))
                return "Two runs! Good running between the wickets."
            if n == 3:
                if hindi:
                    return "शानदार रनिंग बिटवीन द विकेट्स, दोनों बल्लेबाज़ों ने तीन रन पूरे किए!"
                if hinglish:
                    return "Excellent running between the wickets—तीन runs complete!"
                return "Three runs! Excellent running between the wickets."
            if n == 5:
                if hindi:
                    return "पाँच रन! ओवरथ्रो या बेहतरीन दौड़, बल्लेबाज़ों ने पाँच रन बटोर लिए!"
                if hinglish:
                    return "Five runs! Smart running या overthrow—score तेज़ी से आगे!"
                return "Five runs! Smart running and pressure on the fielding side."

        # Safe fallback when the delivery circle is neither numeric nor a known extra.
        if hindi:
            return f"{bowler} की गेंद को {batter} ने खेला।"
        if hinglish:
            return f"{bowler} ki ball {batter} ne kheli."
        return f"{batter} plays {bowler}'s delivery."

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
        if event_info["event"] in {"runs", "four", "six", "wicket"}:
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

