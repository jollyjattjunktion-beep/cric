"""Fast, event-aware radio commentary for the Preet Sports broadcaster.

The web player speaks concise cricket-native calls rather than the long CREX
source paragraph. Source commentary remains available to the visual feed.
"""
from __future__ import annotations


import asyncio
import ctypes
import logging
import os
import random
import re
import sys
import tempfile
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

import edge_tts
from playwright.async_api import Page, Route, async_playwright

from typing import Any, Dict, Optional

POLL_SECONDS = 0.35
OVER_SETTLE_SECONDS = 0.6
WICKET_CARD_WAIT_SECONDS = 0.45
PAGE_LOAD_TIMEOUT_MS = 45_000
RELOAD_AFTER_FAILURES = 5
TTS_TIMEOUT_SECONDS = 8
VOICE_NAME = "hi-IN-MadhurNeural"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# False = stay silent for the ball already on screen when the script starts,
# and begin commentary from the next delivery.
ANNOUNCE_LAST_BALL_ON_START = False
# If this many lines are already waiting, skip dots/singles so the radio keeps up.
MAX_BACKLOG_FOR_MINOR_BALLS = 2

BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
BLOCKED_URL_PATTERNS = (
    "doubleclick", "googlesyndication", "taboola",
    "adsystem", "adnxs", "amazon-adsystem",
)

# Voice delivery per excitement level: (rate, pitch, volume)
AUDIO_PROFILES = {
    1: ("+15%", "+10Hz", "+0%"),    # Level 1 (Dot balls): Normal speaking volume
    2: ("+20%", "+12Hz", "+15%"),   # Level 2 (Singles/Extras): Slightly louder
    3: ("+25%", "+15Hz", "+35%"),   # Level 3 (Fours): Loud and punchy
    4: ("+30%", "+20Hz", "+60%"),   # Level 4 (Sixes/Wickets): Shouting over the crowd
}

log = logging.getLogger("crex_radio")

def _space(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[\s+]", "", _space(value).lower())



# ============================================================
# HINDI NUMBERS & OVER/BALL CONVERTER
# ============================================================
HINDI_OVER_WORDS = {
    1: "पहले", 2: "दूसरे", 3: "तीसरे", 4: "चौथे", 5: "पांचवें",
    6: "छठे", 7: "सातवें", 8: "आठवें", 9: "नौवें", 10: "दसवें",
    11: "ग्यारहवें", 12: "बारहवें", 13: "तेरहवें", 14: "चौदहवें", 15: "पंद्रहवें",
    16: "सोलहवें", 17: "सत्रहवें", 18: "अठारहवें", 19: "उन्नीसवें", 20: "बीसवें",
}

HINDI_BALL_WORDS = {
    1: "पहली गेंद", 2: "दूसरी गेंद", 3: "तीसरी गेंद",
    4: "चौथी गेंद", 5: "पांचवीं गेंद", 6: "आखिरी गेंद",
}

NUM_TO_HINDI = {
    0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पांच",
    6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस",
    11: "ग्यारह", 12: "बारह", 13: "तेरह", 14: "चौदह", 15: "पंद्रह",
    16: "सोलह", 17: "सत्रह", 18: "अठारह", 19: "उन्नीस", 20: "बीस",
    21: "इक्कीस", 22: "बाईस", 23: "तेईस", 24: "चौबीस", 25: "पच्चीस",
    26: "छब्बीस", 27: "सत्ताईस", 28: "अट्ठाईस", 29: "उनतीस", 30: "तीस",
    31: "इकत्तीस", 32: "बत्तीस", 33: "तैंतीस", 34: "चौंतीस", 35: "पैंतीस",
    36: "छत्तीस", 37: "सैंतीस", 38: "अड़तीस", 39: "उनतालीस", 40: "चालीस",
    41: "इकतालीस", 42: "बयालीस", 43: "तैंतालीस", 44: "चौवालीस", 45: "पैंतालीस",
    46: "छियालीस", 47: "सैंतालीस", 48: "अड़तालीस", 49: "उनचास", 50: "पचास",
    51: "इक्यावन", 52: "बावन", 53: "तिरेपन", 54: "चौवन", 55: "पचपन",
    56: "छप्पन", 57: "सत्तावन", 58: "अट्ठावन", 59: "उनसठ", 60: "साठ",
    61: "इकसठ", 62: "बासठ", 63: "तिरसठ", 64: "चौंसठ", 65: "पैंसठ",
    66: "छियासठ", 67: "सरसठ", 68: "अड़सठ", 69: "उनहत्तर", 70: "सत्तर",
    71: "इकहत्तर", 72: "बहत्तर", 73: "तिहत्तर", 74: "चौहत्तर", 75: "पचहत्तर",
    76: "छिहत्तर", 77: "सतहत्तर", 78: "अठहत्तर", 79: "उनासी", 80: "अस्सी",
    81: "इक्यासी", 82: "बयासी", 83: "तिरासी", 84: "चौरासी", 85: "पचासी",
    86: "छियासी", 87: "सत्तासी", 88: "अठासी", 89: "नवासी", 90: "नब्बे",
    91: "इक्यानवे", 92: "बानवे", 93: "तिरानवे", 94: "चौरानवे", 95: "पंचानवे",
    96: "छियानवे", 97: "सत्तानवे", 98: "अट्ठानवे", 99: "निन्यानवे", 100: "एक सौ",
}

HINDI_HUNDREDS = {
    1: "एक सौ", 2: "दो सौ", 3: "तीन सौ", 4: "चार सौ", 5: "पांच सौ",
    6: "छह सौ", 7: "सात सौ", 8: "आठ सौ", 9: "नौ सौ",
}


def number_to_hindi_words(num_str) -> str:
    try:
        n = int(str(num_str).strip())
    except (TypeError, ValueError):
        return str(num_str)
    if n in NUM_TO_HINDI:
        return NUM_TO_HINDI[n]
    if 101 <= n <= 999:
        hundreds, rem = divmod(n, 100)
        head = HINDI_HUNDREDS.get(hundreds, f"{hundreds} सौ")
        return head if rem == 0 else f"{head} {NUM_TO_HINDI.get(rem, str(rem))}"
    return str(n)


def over_ordinal_hindi(over_num: int) -> str:
    return HINDI_OVER_WORDS.get(over_num, f"{number_to_hindi_words(over_num)}वें")


def parse_ball(ball_str: str) -> tuple[int, int]:
    """'3.4' -> (over_number=4, ball_index=4). The page counts completed overs."""
    parts = (ball_str or "").strip().split(".")
    over_num = int(parts[0]) + 1
    idx = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    return over_num, min(max(idx, 1), 6)


def format_ball_phrase_hindi(ball_str: str) -> str:
    try:
        over_num, idx = parse_ball(ball_str)
        return f"{over_ordinal_hindi(over_num)} ओवर की {HINDI_BALL_WORDS.get(idx, f'{idx}वीं गेंद')}"
    except Exception:
        return "अगली गेंद"


def format_over_phrase_hindi(ball_str: str) -> str:
    try:
        over_num, _ = parse_ball(ball_str)
        return f"{over_ordinal_hindi(over_num)} ओवर में"
    except Exception:
        return "इस ओवर में"



# ============================================================
# MASTER PHONETIC DICTIONARY
# ============================================================
# ============================================================
# MASTER PHONETIC DICTIONARY & NAME PRONUNCIATION
# ============================================================
COMMON_NAMES = {
    "muditha": "मुदिथा", "disanayaka": "डिसानायका", "aftab": "आफताब",
    "alam": "आलम", "shah": "शाह", "aryansh": "आर्यांश", "sharma": "शर्मा",
    "y": "वाई", "rai": "राय", "ethan": "ईथन", "carl": "कार्ल", "dsouza": "डिसूज़ा",
    "vicky": "विक्की", "ranjan": "रंजन", "wahab": "वहाब", "hassan": "हसन",
    "zadran": "जादरान", "panoly": "पनोली", "sabnani": "सबनानी", "parashar": "पाराशर",
    "asif": "आसिफ", "taniwal": "तानीवाल", "noorullah": "नूरुल्लाह", "ayobi": "अय्यूबी",
    "singh": "सिंह", "khan": "खान", "kumar": "कुमार", "ali": "अली",
    "ahmed": "अहमद", "ahmad": "अहमद", "mohammad": "मोहम्मद", "mohammed": "मोहम्मद",
    "muhammad": "मुहम्मद", "patel": "पटेल", "malik": "मलिक", "iqbal": "इकबाल",
    "rahman": "रहमान", "raza": "रज़ा", "dasun": "दासुन", "shanaka": "शनाका",
    "pavan": "पवन", "rathnayake": "रथनायके", "liam": "लियाम", "dawson": "डॉसन",
    "avishka": "अविश्का", "fernando": "फर्नांडो", "jamie": "जेमी", "overton": "ओवरटन",
    "wiaan": "वियान", "mulder": "मुल्डर", "beau": "बो", "webster": "वेब्स्टर",
    "pathum": "पथुम", "nissanka": "निसांका", "charith": "चरिथ", "asalanka": "असलंका",
    "kusal": "कुसल", "mendis": "मेंडिस", "kamindu": "कमिंदु", "dhananjaya": "धनंजय",
    "silva": "सिल्वा", "angelo": "एंजेलो", "mathews": "मैथ्यूज़", "wanindu": "वानिंदु",
    "hasaranga": "हसरंगा", "maheesh": "महेश", "theekshana": "थीक्षना", "dilshan": "दिलशान",
    "madushanka": "मदुशंका", "perera": "परेरा", "chandimal": "चांदीमल", "root": "रूट",
    "stokes": "स्टोक्स", "buttler": "बटलर", "brook": "ब्रूक", "jofra": "जोफ्रा",
    "archer": "आर्चर", "adil": "आदिल", "rashid": "रशीद", "mark": "मार्क", "wood": "वुड",
}
PLAYER_NAME_PRONUNCIATIONS = {
    "dasun shanaka": "दासुन शनाका", "pavan rathnayake": "पवन रथनायके",
    "liam dawson": "लियाम डॉसन", "avishka fernando": "अविश्का फर्नांडो",
    "jamie overton": "जेमी ओवरटन", "wiaan mulder": "वियान मुल्डर",
    "beau webster": "बो वेब्स्टर", "pathum nissanka": "पथुम निसांका",
    "charith asalanka": "चरिथ असलंका", "kusal mendis": "कुसल मेंडिस",
    "kamindu mendis": "कमिंदु मेंडिस", "dhananjaya de silva": "धनंजय डी सिल्वा",
    "angelo mathews": "एंजेलो मैथ्यूज़", "wanindu hasaranga": "वानिंदु हसरंगा",
    "maheesh theekshana": "महेश थीक्षना", "dilshan madushanka": "दिलशान मदुशंका",
    "kusal perera": "कुसल परेरा", "dinesh chandimal": "दिनेश चांदीमल",
    "joe root": "जो रूट", "ben stokes": "बेन स्टोक्स", "jos buttler": "जोस बटलर",
    "harry brook": "हैरी ब्रूक", "jofra archer": "जोफ्रा आर्चर",
    "adil rashid": "आदिल रशीद", "mark wood": "मार्क वुड",
}
PHONIC_RULES = (
    ("sh", "श"), ("ch", "च"), ("th", "थ"), ("ph", "फ"), ("kh", "ख"),
    ("gh", "घ"), ("bh", "भ"), ("dh", "ध"), ("jh", "झ"), ("ee", "ी"),
    ("oo", "ू"), ("aa", "ा"), ("ai", "ै"), ("au", "ौ"),
    ("a", "ा"), ("b", "ब"), ("c", "क"), ("d", "ड"), ("e", "े"),
    ("f", "फ"), ("g", "ग"), ("h", "ह"), ("i", "ि"), ("j", "ज"),
    ("k", "क"), ("l", "ल"), ("m", "म"), ("n", "न"), ("o", "ो"),
    ("p", "प"), ("q", "क"), ("r", "र"), ("s", "स"), ("t", "ट"),
    ("u", "ु"), ("v", "व"), ("w", "व"), ("x", "क्स"), ("y", "य"), ("z", "ज़"),
)
INITIAL_VOWELS = {"aa": "आ", "ee": "ई", "oo": "ऊ", "ai": "ऐ", "au": "औ",
                  "a": "अ", "e": "ए", "i": "इ", "o": "ओ", "u": "उ"}
_NAME_NOISE = re.compile(r"\b(to|w|wd|nb|wide|no\s*runs?|runs?|dots?|out|c|b|wkt|wk|lb|st|\d+)\b", re.I)


def _phonetic_word_to_hindi(key: str) -> str:
    out, index = "", 0
    while index < len(key):
        for pattern, replacement in PHONIC_RULES:
            if key.startswith(pattern, index):
                if index == 0 and pattern in INITIAL_VOWELS:
                    replacement = INITIAL_VOWELS[pattern]
                out += replacement
                index += len(pattern)
                break
        else:
            index += 1
    return out or key


def clean_player_name_to_hindi(raw_name: Optional[str]) -> str:
    if not raw_name:
        return ""
    cleaned = _NAME_NOISE.sub(" ", str(raw_name))
    cleaned = re.sub(r"[^A-Za-z\s']", " ", cleaned).strip()
    out = []
    for word in cleaned.split():
        key = word.lower().replace("'", "")
        if key:
            out.append(COMMON_NAMES.get(key) or _phonetic_word_to_hindi(key))
    return " ".join(out)


def _pronunciation_context_names(context=None):
    if not isinstance(context, dict):
        return []
    names = []
    def add(value):
        if isinstance(value, str):
            value = re.sub(r"\s+", " ", value).strip(" \t\r\n,.:;")
            if value and len(value) >= 3 and re.search(r"[A-Za-z]", value):
                if value.lower() not in {"batter", "batsman", "bowler", "unknown", "innings", "team"} and value not in names:
                    names.append(value)
    for key in ("bowler", "batsman", "batter", "striker", "non_striker", "fielder",
                "player", "dismissed_batter", "dismissed_player"):
        add(context.get(key))
    parts = re.split(r"\s+to\s+", str(context.get("matchup") or ""), maxsplit=1, flags=re.I)
    if len(parts) == 2:
        add(parts[0]); add(parts[1])
    dismissal = context.get("last_dismissal") or context.get("lastDismissal") or context.get("dismissal_card") or {}
    if isinstance(dismissal, dict):
        for key in ("batter", "batsman", "bowler", "fielder", "player", "name", "dismissed_player"):
            add(dismissal.get(key))
        how_out = str(dismissal.get("howOut") or dismissal.get("how_out") or "")
    else:
        how_out = str(dismissal or "")
    patterns = (
        r"\bc\s+([A-Za-z][A-Za-z .'-]+?)\s+b\s+([A-Za-z][A-Za-z .'-]+)$",
        r"\bst\s+([A-Za-z][A-Za-z .'-]+?)\s+b\s+([A-Za-z][A-Za-z .'-]+)$",
    )
    match = next((m for p in patterns if (m := re.search(p, how_out, re.I))), None)
    if match:
        add(match.group(1)); add(match.group(2))
    else:
        match = re.search(r"\bb\s+([A-Za-z][A-Za-z .'-]+)$", how_out, re.I)
        if match:
            add(match.group(1))
    return sorted(names, key=len, reverse=True)


def prepare_tts_pronunciation_text(text, context=None):
    result = str(text or "")
    for name, phonetic in sorted(PLAYER_NAME_PRONUNCIATIONS.items(), key=lambda x: len(x[0]), reverse=True):
        result = re.sub(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", phonetic, result, flags=re.I)
    for name in _pronunciation_context_names(context):
        pattern = r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])"
        if re.search(pattern, result, flags=re.I):
            phonetic = PLAYER_NAME_PRONUNCIATIONS.get(name.lower()) or clean_player_name_to_hindi(name)
            result = re.sub(pattern, lambda _: phonetic, result, flags=re.I)
    for name, phonetic in sorted(COMMON_NAMES.items(), key=lambda x: len(x[0]), reverse=True):
        if len(name) >= 3:
            result = re.sub(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", phonetic, result, flags=re.I)
    return result

# ============================================================
# DELIVERY BADGE PARSER
# ============================================================
def classify_delivery(token: Optional[str]) -> dict:
    t = re.sub(r"[\s+]", "", (token or "").strip().lower())
    num = re.search(r"\d+", t)
    n = int(num.group()) if num else None

    if t in ("w", "wkt", "out"):
        return {"kind": "wicket", "runs": 0, "legal": True}
    if "wd" in t or "wide" in t:
        return {"kind": "wide", "runs": n if n else 1, "legal": False}
    if "nb" in t or "noball" in t:
        return {"kind": "noball", "runs": n if n else 1, "legal": False}
    if "lb" in t:
        return {"kind": "legbye", "runs": n if n else 1, "legal": True}
    if re.fullmatch(r"\d*b|b\d*", t):
        return {"kind": "bye", "runs": n if n else 1, "legal": True}
    if t.isdigit():
        return {"kind": "runs" if n else "dot", "runs": n or 0, "legal": True}
    return {"kind": "dot", "runs": 0, "legal": True}


def over_stats_from_tokens(tokens) -> dict:
    stats = {"runs": 0, "fours": 0, "sixes": 0, "wickets": 0, "legal": 0}
    for tok in tokens or []:
        d = classify_delivery(tok)
        stats["runs"] += d["runs"]
        if d["legal"]:
            stats["legal"] += 1
        if d["kind"] == "wicket":
            stats["wickets"] += 1
        elif d["kind"] == "runs" and d["runs"] == 4:
            stats["fours"] += 1
        elif d["kind"] == "runs" and d["runs"] == 6:
            stats["sixes"] += 1
    return stats


def guess_match_overs(url: str) -> Optional[int]:
    u = (url or "").lower()
    if re.search(r"(?<![a-z0-9])[dt]10(?![a-z0-9])", u):
        return 10
    if re.search(r"(?<![a-z0-9])t20i?(?![a-z0-9])", u) or "twenty20" in u:
        return 20
    if re.search(r"(?<![a-z0-9])(?:odi|50-over)(?![a-z0-9])", u):
        return 50
    return None


# ============================================================
# NATURAL RADIO COMMENTARY SCRIPTS
# ============================================================
SIX_LINES = (
    "उठाकर मारा! गेंद आसमान में... बहुत ऊंची... और ये गई स्टैंड्स में! छह रन!",
    "क्रीज़ से बाहर निकलकर सीधा प्रहार! गेंद मैदान के बाहर, गगनचुंबी छक्का!",
    "बल्ले के बिल्कुल बीचों-बीच लगी! गेंद सीमा रेखा के बहुत पार, दर्शकों के बीच! छह रन!",
    "ये तो सीधा आसमान में! फील्डर बस देखते रह गए, ये छक्का है!",
    "क्या हिट है! स्वीट स्पॉट से निकली और गेंद स्टेडियम के बाहर की तरफ़! मैक्सिमम!",
    "इसे कहते हैं पावर हिटिंग! गेंद हवा में और सीधा सीमा रेखा के उस पार! छह रन!",
)
SIX_TAGS = (
    "{batsman} का ज़बरदस्त छक्का!",
    "{bowler} की गेंद पर पड़ी ज़बरदस्त मार!",
    "{batsman} पूरे रंग में नज़र आ रहे हैं!",
    "{bowler} के लिए बहुत महंगी साबित हुई ये गेंद!",
)

FOUR_LINES = (
    "गजब की टाइमिंग! फील्डर को हिलने का भी मौका नहीं मिला, गेंद गोली की रफ्तार से सीमा रेखा के पार! चौका!",
    "कलाइयों का जादू! खूबसूरत प्लेसमेंट, गेंद रस्सी के पार! चार रन!",
    "गैप मिला और गेंद सीधी बाउंड्री पर! फील्डर ने पीछा किया पर बहुत देर हो चुकी थी, चौका!",
    "ज़मीन के साथ-साथ दौड़ी गेंद और बाउंड्री लाइन के पार! शानदार चौका!",
    "क्लासिक शॉट! बेहतरीन टाइमिंग, गेंद चार रन के लिए सीमा रेखा के बाहर!",
)
FOUR_TAGS = (
    "{batsman} का खूबसूरत शॉट!",
    "{bowler} की लेंथ थोड़ी चूकी और बल्लेबाज़ ने उसे सज़ा दे दी!",
    "{batsman} को मिल गई लय!",
)

DOT_LINES = (
    "कसी हुई लाइन और लेंथ, बल्लेबाज़ ने संभलकर खेला, कोई रन नहीं, डॉट बॉल।",
    "शानदार गेंद! बल्लेबाज़ को पूरी तरह छकाया, गेंद सीधे विकेटकीपर के दस्तानों में।",
    "सीधा फील्डर के हाथों में खेला, रन का कोई मौका नहीं।",
    "बल्ले के बाहरी किनारे को छूते-छूते बची गेंद, कोई रन नहीं।",
)
SINGLE_LINES = (
    "हल्के हाथों से कवर्स की ओर खेला और तेज़ी से एक सिंगल पूरा किया।",
    "कलाइयों को मोड़कर स्ट्राइक रोटेट की, आसानी से एक रन चुरा लिया।",
    "गेंद को गैप में धकेला और एक रन लेकर स्ट्राइक बदल ली।",
)
DOUBLE_LINES = (
    "गैप में खेलकर दो फील्डर्स के बीच से बढ़िया रनिंग, आसानी से दो रन पूरे किए!",
    "सॉफ्ट हैंड्स से धकेला, बढ़िया तालमेल और दौड़कर दो रन निकाले!",
)
THREE_LINE = "शानदार रनिंग बिटवीन द विकेट्स, दोनों बल्लेबाज़ों ने दौड़कर तीन रन पूरे किए!"
FIVE_LINE = "पांच रन! ओवरथ्रो का फायदा या बेहतरीन दौड़, बल्लेबाज़ों नेत पांच रन बटोर लिए!"

WIDE_LINES = (
    "दिशाहीन गेंदबाज़ी, लेग स्टंप से काफ़ी बाहर, अंपायर ने दोनों हाथ फैलाए, वाइड!",
    "लाइन से भटकी गेंद, वाइड का इशारा! गेंदबाज़ को एक गेंद और डालनी होगी!",
)
NOBALL_LINES = (
    "नो बॉल! गेंदबाज़ ने क्रीज़ को ओवरस्टेप किया, अब अगली गेंद फ्री हिट!",
    "अंपायर का हाथ ऊपर, नो बॉल! और अगली गेंद पर मिलेगी फ्री हिट!",
)
LEGBYE_LINE = "गेंद पैड से लगकर निकल गई, बल्लेबाज़ों ने लेग बाई के {runs} रन दौड़कर चुरा लिए!"
BYE_LINE = "विकेटकीपर के हाथों से निकल गई गेंद, बाई के {runs} रन!"

INTROS_BOTH = (
    "{bowler} की गेंद, सामने {batsman},",
    "{bowler} दौड़ते हुए आए, गेंद {batsman} की ओर,",
    "{bowler} ने डाली गेंद, {batsman} तैयार,",
    "{bowler} से {batsman} को,",
)


def build_intro(bowler: str, batsman: str) -> str:
    if bowler and batsman:
        return random.choice(INTROS_BOTH).format(bowler=bowler, batsman=batsman)
    if bowler:
        return f"{bowler} गेंदबाज़ी छोर से तैयार,"
    return ""


def pick_tag(tags, batsman: str, bowler: str) -> str:
    usable = [
        t for t in tags
        if not (("{batsman}" in t and not batsman) or ("{bowler}" in t and not bowler))
    ]
    return random.choice(usable).format(batsman=batsman, bowler=bowler) if usable else ""


# ------------------------------------------------------------
# Wickets
# ------------------------------------------------------------
_NAME = r"[a-z .'\-]"

def build_live_wicket_speech(
    dismissal_text: str,
    batter_name: str,
    batter_runs: str,
    batter_balls: str,
    bowler_name: str = "",
) -> str:
    batter_hi = clean_player_name_to_hindi(batter_name) or "बल्लेबाज़"
    runs_known = str(batter_runs).strip().isdigit()
    balls_known = str(batter_balls).strip().isdigit()
    runs_val = int(batter_runs) if runs_known else 0
    runs_hi = number_to_hindi_words(runs_val)
    balls_hi = number_to_hindi_words(batter_balls) if balls_known else ""

    if runs_known and runs_val > 0:
        status = f"{balls_hi} गेंदों में {runs_hi} रन बनाकर" if balls_hi else f"{runs_hi} रन बनाकर"
    elif runs_known:
        status = "बिना खाता खोले"
    else:
        status = ""
    who = f"{batter_hi} {status}".strip()

    d = re.sub(r"\s+", " ", (dismissal_text or "").lower()).strip()

    m = re.search(rf"\bc\s*&\s*b\s+({_NAME}+)", d)
    if m:
        bowler = clean_player_name_to_hindi(m.group(1))
        return random.choice((
            f"और ये लिया कैच, खुद गेंदबाज़ ने! {bowler} ने अपनी ही गेंद पर लपका कैच! {who} पवेलियन लौट रहे हैं!",
            f"कैच एंड बोल्ड! {bowler} ने ज़बरदस्त रिफ्लेक्स दिखाया, {who} वापस लौट रहे हैं!",
        ))

    m = re.search(rf"\bc\s+({_NAME}+?)\s+b\s+({_NAME}+)", d)
    if m:
        fielder = clean_player_name_to_hindi(m.group(1))
        bowler = clean_player_name_to_hindi(m.group(2))
        return random.choice((
            f"गेंद हवा में उठी... और सुरक्षित हाथों में! {fielder} ने कैच लपक लिया! विकेट! "
            f"{who} पवेलियन लौट रहे हैं, {bowler} को मिली बड़ी कामयाबी!",
            f"बड़ा विकेट! शॉट गलत लगा और {fielder} ने कोई गलती नहीं की! "
            f"{who} कैच आउट, {bowler} की गेंद पर!",
        ))

    m = re.search(rf"\bst\s+({_NAME}+?)\s+b\s+({_NAME}+)", d)
    if m:
        keeper = clean_player_name_to_hindi(m.group(1))
        bowler = clean_player_name_to_hindi(m.group(2))
        return (
            f"बल्लेबाज़ क्रीज़ से बाहर... और विकेटकीपर {keeper} ने बेल्स उड़ा दीं! स्टंप्ड! "
            f"{who} पवेलियन लौट रहे हैं, {bowler} की गेंद पर!"
        )

    if "lbw" in d:
        m = re.search(rf"lbw\s+b\s+({_NAME}+)", d)
        bowler = clean_player_name_to_hindi(m.group(1)) if m else clean_player_name_to_hindi(bowler_name)
        tail = f", {bowler} को मिला विकेट" if bowler else ""
        return (
            f"ज़ोरदार अपील... और अंपायर ने उंगली उठा दी! एलबीडब्ल्यू! "
            f"{who} पवेलियन लौट रहे हैं{tail}!"
        )

    if "run out" in d or "runout" in d:
        return random.choice((
            f"आपसी तालमेल बिगड़ा... थ्रो सीधा स्टंप्स पर... और रन आउट! {who} पवेलियन लौट रहे हैं!",
            f"रन चुराने की कोशिश भारी पड़ी! रन आउट! {who} वापस लौट रहे हैं!",
        ))

    if "hit wicket" in d or "hitwicket" in d:
        return f"अपने ही स्टंप्स पर जा लगा बल्ला! हिट विकेट! {who} पवेलियन लौट रहे हैं!"

    m = re.search(rf"\bb\s+({_NAME}+)", d)
    if m:
        bowler = clean_player_name_to_hindi(m.group(1))
        return random.choice((
            f"गिल्लियां बिखर गईं! क्लीन बोल्ड! {bowler} की शानदार गेंद ने स्टंप उखाड़ दिए! "
            f"{who} पवेलियन लौट रहे हैं!",
            f"बोल्ड! बोल्ड! {bowler} ने बल्लेबाज़ को पूरी तरह छका दिया, {who} पवेलियन लौट रहे हैं!",
        ))

    bowler = clean_player_name_to_hindi(bowler_name)
    if bowler:
        return f"विकेट! विकेट! {bowler} की गेंद पर बड़ा झटका! {who} पवेलियन लौट रहे हैं!"
    return f"विकेट! बड़ा झटका! {who} पवेलियन लौट रहे हैं!"


def score_after_wicket_phrase(runs: int, wkts: int) -> str:
    runs_hi = number_to_hindi_words(runs)
    wkts_hi = number_to_hindi_words(wkts)
    phrase = f"स्कोर अब {runs_hi} रन, {wkts_hi} विकेट के नुकसान पर।"
    if wkts >= 10:
        phrase += " और ये आखिरी विकेट! पूरी टीम ऑल आउट!"
    return phrase


# ------------------------------------------------------------
# Ball-by-ball
# ------------------------------------------------------------
def generate_circle_ball_commentary(
    ball_num: str,
    circle_val: Optional[str],
    bowler_raw: Optional[str],
    batsman_raw: Optional[str],
    last_dismissal: Optional[dict] = None,
) -> tuple[str, int]:
    ball_phrase = format_ball_phrase_hindi(ball_num)
    over_phrase = format_over_phrase_hindi(ball_num)
    bowler = clean_player_name_to_hindi(bowler_raw)
    batsman = clean_player_name_to_hindi(batsman_raw)

    d = classify_delivery(circle_val)
    kind, n = d["kind"], d["runs"]

    if kind == "wicket":
        batter_to_announce = batsman_raw or ""
        runs_s, balls_s, how_out = "", "", ""
        card = last_dismissal or {}
        card_b = card.get("batter", "") or ""
        if card_b:
            same_player = (not batsman_raw) or any(
                part.lower() in card_b.lower() for part in batsman_raw.split() if len(part) > 2
            )
            if same_player:
                batter_to_announce = card_b
                runs_s = card.get("runs", "")
                balls_s = card.get("balls", "")
                how_out = card.get("howOut", "")
        speech = build_live_wicket_speech(how_out, batter_to_announce, runs_s, balls_s, bowler_raw or "")
        return f"{ball_phrase}, {speech}", 4

    if kind == "wide":
        extra = f" अतिरिक्त {number_to_hindi_words(n)} रन।" if n > 1 else ""
        return f"{ball_phrase}, {random.choice(WIDE_LINES)}{extra}", 2
    if kind == "noball":
        return f"{ball_phrase}, {random.choice(NOBALL_LINES)}", 2

    intro = build_intro(bowler, batsman)
    tag = ""
    if kind == "legbye":
        action, level = LEGBYE_LINE.format(runs=number_to_hindi_words(n)), 2
    elif kind == "bye":
        action, level = BYE_LINE.format(runs=number_to_hindi_words(n)), 2
    elif n >= 6:
        action, level = random.choice(SIX_LINES), 4
        tag = pick_tag(SIX_TAGS, batsman, bowler)
    elif n == 5:
        action, level = FIVE_LINE, 2
    elif n == 4:
        action, level = random.choice(FOUR_LINES), 3
        tag = pick_tag(FOUR_TAGS, batsman, bowler)
    elif n == 3:
        action, level = THREE_LINE, 2
    elif n == 2:
        action, level = random.choice(DOUBLE_LINES), 2
    elif n == 1:
        action, level = random.choice(SINGLE_LINES), 2
    else:
        action, level = random.choice(DOT_LINES), 1

    text = " ".join(p for p in (f"{ball_phrase},", intro, action, tag) if p)
    return text, level


def _count_phrase(n: int, one: str, many: str) -> str:
    return one if n == 1 else f"{number_to_hindi_words(n)} {many}"


def _join_hindi(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " और " + items[-1]


def _rr_thresholds(match_overs: Optional[int]) -> tuple[float, float]:
    if match_overs and match_overs <= 10:
        return 11.0, 8.0
    if match_overs and match_overs >= 40:
        return 6.5, 4.5
    return 8.5, 6.5


def build_over_summary(
    over_int: int,
    runs,
    wkts,
    stats: Optional[dict] = None,
    match_overs: Optional[int] = None,
) -> str:
    """Short Hinglish over recap with a naturally spoken current score."""
    parts: list[str] = []

    if stats and stats.get("legal", 0) >= 6:
        over_runs = int(stats.get("runs", 0))
        over_wickets = int(stats.get("wickets", 0))
        fours = int(stats.get("fours", 0))
        sixes = int(stats.get("sixes", 0))
        if over_runs == 0 and over_wickets == 0:
            parts.append(f"Over {over_int} complete—maiden over! Is over mein koi run nahi. Great bowling!")
        elif over_runs == 0:
            parts.append(f"Over {over_int} complete—wicket maiden! Koi run nahi, {over_wickets} wicket. Superb bowling!")
        else:
            details = []
            if fours:
                details.append(f"{fours} fours")
            if sixes:
                details.append(f"{sixes} sixes")
            if over_wickets:
                details.append(f"{over_wickets} wickets")
            line = f"Over {over_int} complete! Is over mein {over_runs} runs aaye"
            if details:
                line += ", " + ", ".join(details)
            parts.append(line + ".")
    else:
        parts.append(f"Over {over_int} complete.")

    try:
        runs_num = int(str(runs).strip())
        wkts_num = int(str(wkts).strip())
    except (TypeError, ValueError):
        return " ".join(parts)

    parts.append(f"Score {runs_num} for {wkts_num}.")
    crr = round(runs_num / max(over_int, 1), 2)
    high, mid = _rr_thresholds(match_overs)
    if crr >= high:
        parts.append(f"Run rate {crr:.2f} per over hai—batting side achchi position mein hai.")
    elif crr >= mid:
        parts.append(f"Run rate {crr:.2f} per over hai, scoring steady chal rahi hai.")
    else:
        parts.append(f"Run rate {crr:.2f} per over hai—ab runs accelerate karne honge.")

    if match_overs and over_int < match_overs:
        parts.append(f"{match_overs - over_int} overs abhi baaki hain.")

    return " ".join(parts)

def build_innings_end(runs, wkts) -> str:
    try:
        runs_hi = number_to_hindi_words(int(runs))
        wkts_hi = number_to_hindi_words(int(wkts))
        return f"पारी समाप्त! स्कोर {runs_hi} रन, {wkts_hi} विकेट के नुकसान पर।"
    except (TypeError, ValueError):
        return "पारी समाप्त!"



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
                return random.choice((
                    "उठाकर मारा! गेंद आसमान में, और ये गई स्टैंड्स में—छह रन!",
                    f"क्या पावर हिटिंग! {batter} ने गेंद बाउंड्री के पार भेज दी—छक्का!",
                    "बल्ले के बिल्कुल बीचों-बीच लगी, फील्डर देखते रह गए—छह रन!",
                ))
            if hinglish:
                return random.choice((
                    f"SIX! {batter} ने ball stands के पार भेज दी—क्या hit है!",
                    f"Power hitting! {batter} ने गेंद सीधे rope के पार भेजी—six runs!",
                ))
            return f"SIX! {batter} launches it over the rope!"

        if event == "four":
            if hindi:
                return random.choice((
                    "कलाइयों का जादू! खूबसूरत प्लेसमेंट, गेंद रस्सी के पार—चार रन!",
                    "गज़ब की टाइमिंग! फील्डर को हिलने का मौका नहीं, चौका!",
                    "गैप मिला और गेंद सीधी बाउंड्री के पार—शानदार चौका!",
                ))
            if hinglish:
                return random.choice((
                    f"FOUR! Kya timing—{batter} ने ball boundary के पार भेजी!",
                    "कलाइयों का जादू! Perfect placement—चार रन!",
                ))
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
                    return random.choice((
                        "हल्के हाथों से कवर्स की ओर खेला और तेज़ी से एक सिंगल पूरा किया।",
                        "कलाइयों को मोड़कर स्ट्राइक रोटेट की, आसानी से एक रन चुरा लिया।",
                        "गेंद को गैप में धकेला और एक रन लेकर स्ट्राइक बदल ली।",
                    ))
                if hinglish:
                    return random.choice((
                        "Soft hands, quick running—एक single पूरा किया!",
                        "Gap में push किया और strike rotate कर ली—one run!",
                    ))
                return "Quick single, strike rotates."
            if n == 2:
                if hindi:
                    return random.choice((
                        "गैप में खेलकर दो फील्डर्स के बीच से बढ़िया रनिंग, दो रन पूरे!",
                        "सॉफ्ट हैंड्स से धकेला, बढ़िया तालमेल और दौड़कर दो रन निकाले!",
                    ))
                if hinglish:
                    return "Gap में shot, sharp running—दो runs पूरे!"
                return "Two runs, good running."
            if n == 3:
                if hindi:
                    return "शानदार रनिंग बिटवीन द विकेट्स—तीन रन पूरे!"
                if hinglish:
                    return "Excellent running between the wickets—three runs!"
                return "Three runs, excellent running."
            if n == 5:
                if hindi:
                    return "पाँच रन! ओवरथ्रो या शानदार दौड़, बल्लेबाज़ों ने पाँच रन बटोर लिए!"
                if hinglish:
                    return "Five runs! Smart running या overthrow—score तेज़ी से आगे!"
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

        # Only append the scorecard on a wicket or the sixth legal delivery.
        is_over_end = bool(
            ball_number == 6
            and not any(k in token for k in ("wd", "wide", "nb", "noball", "no-ball"))
        )
        if event_info["event"] == "wicket" or is_over_end:
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


def build_live_circle_announcement(
    ball_info: Dict[str, Any],
    lang: str = "hi",
) -> Dict[str, Any]:
    """Use final.py's handcrafted radio scripts; circle token alone selects the delivery."""
    info = dict(ball_info or {})
    ball_num = _space(info.get("over"))
    token = _space(info.get("circle_val") or info.get("runs")).upper()
    d = classify_delivery(token)

    bowler = _space(info.get("bowler"))
    batter = _space(info.get("batsman") or info.get("batter"))
    matchup = _space(info.get("matchup"))
    parts = re.split(r"\s+to\s+", matchup, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        bowler = bowler or parts[0].strip(" ,:.")
        batter = batter or parts[1].strip(" ,:.")
    bowler = bowler or _space(info.get("current_bowler"))
    batter = batter or _space(info.get("striker"))

    dismissal = info.get("last_dismissal") or info.get("lastDismissal") or {}
    speech, level = generate_circle_ball_commentary(
        ball_num,
        token,
        bowler or None,
        batter or None,
        dismissal if isinstance(dismissal, dict) else None,
    )

    # Score and batter figures are announced ONLY after a wicket or the final legal
    # ball of an over. Routine singles, dots, boundaries and extras get only the
    # short manual circle call so the voice can keep up.
    over_match = re.search(r"(\d+)\.(\d+)", ball_num)
    is_over_end = bool(
        over_match
        and int(over_match.group(2)) == 6
        and d.get("legal", True)
    )
    announce_score = d["kind"] == "wicket" or is_over_end

    # Do not copy any scraped CREX commentary sentence into the voice text.
    if announce_score:
        score_parts = []
        try:
            team_runs = int(info.get("total_runs"))
            team_wickets = int(info.get("total_wickets"))
            score_parts.append(f"Score {team_runs} for {team_wickets}.")
        except (TypeError, ValueError):
            pass

        batter_parts = []
        for prefix in ("striker", "non_striker"):
            name = _space(info.get(prefix))
            runs_value = info.get(f"{prefix}_runs")
            balls_value = info.get(f"{prefix}_balls")
            if not name or runs_value is None or balls_value is None or name.lower().startswith("batter "):
                continue
            try:
                batter_parts.append(f"{name} {int(runs_value)} off {int(balls_value)}")
            except (TypeError, ValueError):
                continue
        if batter_parts:
            score_parts.append(", ".join(batter_parts) + ".")
        if score_parts:
            speech = f"{speech} {' '.join(score_parts)}"

    rate, pitch, volume = AUDIO_PROFILES.get(level, AUDIO_PROFILES[1])
    event_kind = d["kind"]
    if event_kind == "runs" and d["runs"] == 4:
        badge = "FOUR"
    elif event_kind == "runs" and d["runs"] >= 6:
        badge = "SIX"
    elif event_kind == "wicket":
        badge = "WICKET"
    elif event_kind == "wide":
        badge = "WIDE"
    elif event_kind == "noball":
        badge = "NO BALL"
    elif event_kind == "legbye":
        badge = "LEG BYE"
    elif event_kind == "bye":
        badge = "BYE"
    elif event_kind == "dot":
        badge = "DOT BALL"
    else:
        badge = "RUNS"

    return {
        "spoken_text": " ".join(speech.split()),
        "tts_text": prepare_tts_pronunciation_text(" ".join(speech.split()), info),
        "circle_value": token,
        "event": event_kind,
        "badge": badge,
        "level": level,
        "rate": rate,
        "pitch": pitch,
        "volume": volume,
        "bowler": bowler,
        "batter": batter,
        "over": ball_num,
        "should_speak": bool(token),
    }



def _over_nominative_hindi(over_number: int) -> str:
    """Grammatical subject form: 'सत्ताईसवाँ ओवर पूरा हुआ'."""
    direct = {
        1: "पहला", 2: "दूसरा", 3: "तीसरा", 4: "चौथा", 5: "पाँचवाँ",
        6: "छठा", 7: "सातवाँ", 8: "आठवाँ", 9: "नौवाँ", 10: "दसवाँ",
        11: "ग्यारहवाँ", 12: "बारहवाँ", 13: "तेरहवाँ", 14: "चौदहवाँ",
        15: "पंद्रहवाँ", 16: "सोलहवाँ", 17: "सत्रहवाँ", 18: "अठारहवाँ",
        19: "उन्नीसवाँ", 20: "बीसवाँ",
    }
    n = int(over_number)
    return direct.get(n, f"{number_to_hindi_words(n)}वाँ")


def _decimal_hindi_words(value: float) -> str:
    rendered = f"{value:.2f}"
    whole, fractional = rendered.split(".")
    return (
        f"{number_to_hindi_words(int(whole))} दशमलव "
        + " ".join(number_to_hindi_words(int(digit)) for digit in fractional)
    )


def build_live_over_summary(over_index, balls, score_state=None, lang="hinglish"):
    """Hindi-script Hinglish over recap; return nothing before six legal deliveries."""
    score_state = score_state or {}
    unique_tokens = []
    seen = set()
    for ball in balls or []:
        if not isinstance(ball, dict):
            continue
        raw = ball.get("circle_val")
        if raw is None or str(raw).strip() == "":
            raw = ball.get("runs", "")
        token = _compact(str(raw or ""))
        over = _space(ball.get("over"))
        seq = _space(ball.get("browser_seq") or ball.get("seq"))
        matchup = _space(ball.get("matchup")).lower()
        key = (over, token, matchup, seq) if (seq or matchup) else (over, token)
        if key in seen:
            continue
        seen.add(key)
        unique_tokens.append(token)

    stats = over_stats_from_tokens(unique_tokens)
    if int(stats.get("legal", 0)) < 6:
        return ""

    try:
        spoken_over = int(over_index) + 1
    except (TypeError, ValueError):
        spoken_over = 1

    over_runs = int(stats.get("runs", 0) or 0)
    over_wickets = int(stats.get("wickets", 0) or 0)
    fours = int(stats.get("fours", 0) or 0)
    sixes = int(stats.get("sixes", 0) or 0)
    ordinal = _over_nominative_hindi(spoken_over)

    if over_runs == 0 and over_wickets == 0:
        parts = [f"{ordinal} ओवर पूरा हुआ! इस ओवर में कोई रन नहीं आया। शानदार गेंदबाज़ी!"]
    elif over_runs == 0:
        parts = [
            f"{ordinal} ओवर पूरा हुआ! विकेट मेडन ओवर—कोई रन नहीं आया और "
            f"{number_to_hindi_words(over_wickets)} विकेट गिरा। शानदार गेंदबाज़ी!"
        ]
    else:
        line = f"{ordinal} ओवर पूरा हुआ! इस ओवर में {number_to_hindi_words(over_runs)} रन आए"
        details = []
        if fours:
            details.append(f"{number_to_hindi_words(fours)} " + ("चौका" if fours == 1 else "चौके"))
        if sixes:
            details.append(f"{number_to_hindi_words(sixes)} " + ("छक्का" if sixes == 1 else "छक्के"))
        if over_wickets:
            details.append(f"{number_to_hindi_words(over_wickets)} विकेट")
        if details:
            line += ", " + ", ".join(details)
        parts = [line + "।"]

    try:
        score_runs = int(score_state.get("total_runs"))
        score_wickets = int(score_state.get("total_wickets"))
    except (TypeError, ValueError):
        score_runs = score_wickets = None

    if score_runs is not None and score_wickets is not None:
        parts.append(
            f"स्कोर अब {number_to_hindi_words(score_runs)} रन, "
            f"{number_to_hindi_words(score_wickets)} विकेट के नुकसान पर।"
        )
        crr = score_runs / max(spoken_over, 1)
        high, mid = _rr_thresholds(_safe_int(score_state.get("match_overs"), 20))
        rate_words = _decimal_hindi_words(crr)
        if crr >= high:
            parts.append(f"रन रेट {rate_words} प्रति ओवर है, रन बनाने की रफ्तार शानदार है।")
        elif crr >= mid:
            parts.append(f"रन रेट {rate_words} प्रति ओवर है, रन बनने की रफ्तार ठीक चल रही है।")
        else:
            parts.append(f"रन रेट {rate_words} प्रति ओवर है, अब रन बनाने की रफ्तार बढ़ानी होगी।")

    batters = []
    for prefix in ("striker", "non_striker"):
        name = _space(score_state.get(prefix))
        try:
            runs = int(score_state.get(f"{prefix}_runs"))
            faced = int(score_state.get(f"{prefix}_balls"))
        except (TypeError, ValueError):
            continue
        if name and not name.lower().startswith("batter "):
            batters.append(
                f"{name} {number_to_hindi_words(runs)} रन, "
                f"{number_to_hindi_words(faced)} गेंदें"
            )
    if batters:
        parts.append("बल्लेबाज़: " + "; ".join(batters) + "।")

    return " ".join(parts)

