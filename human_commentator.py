#!/usr/bin/env python3
"""
PREET SPORTS - Professional Cricket Radio Engine (Pro Commentary Edition)
=========================================================================
- Voice : Microsoft Edge-TTS (hi-IN-MadhurNeural)
- Effects : LIVE STADIUM MIXING (Sidechain compression, Reverb, Headset EQ)
- Wicket: pairs the dismissal card with the batter on the delivery row
- Radio : rich lines for 4, 6, wickets (caught / bowled / lbw / run out /
          stumped), wides, no-balls and an over-by-over summary
- Audio : native Windows MCI playback (8.3 short path, Python 3.14 safe)

Usage
-----
    python crex_radio_pro.py                 # asks for a CREX URL
    python crex_radio_pro.py <CREX_URL>      # no prompt
    python crex_radio_pro.py --demo          # speak sample 4/6/wicket/over lines
    python crex_radio_pro.py <URL> --debug   # print raw page snapshots

Setup
-----
    pip install edge-tts playwright
    playwright install chromium
    (Ensure ffmpeg is installed and added to Windows PATH)
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

IS_WINDOWS = sys.platform == "win32"
DEBUG = "--debug" in sys.argv
DEMO = "--demo" in sys.argv

# ============================================================
# CONFIG
# ============================================================
DEFAULT_URL = (
    "https://crex.com/cricket-live-score/"
    "abd-vs-emb-2nd-semi-final-emirates-d10-league-2026-match-updates-14NA"
)
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


# ============================================================
# WINDOWS TUNING
# ============================================================
def disable_quickedit() -> None:
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        h_stdin = kernel32.GetStdHandle(-10)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode))
        kernel32.SetConsoleMode(h_stdin, mode.value & ~0x0040)
    except Exception:
        pass


def prevent_sleep_and_throttle() -> None:
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000)
    except Exception:
        pass


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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
COMMON_NAMES = {
    "muditha": "मुदिथा", "disanayaka": "डिसानायका",
    "aftab": "आफताब", "alam": "आलम", "shah": "शाह",
    "aryansh": "आर्यांश", "sharma": "शर्मा",
    "y": "वाई", "rai": "राय",
    "ethan": "ईथन", "carl": "कार्ल", "dsouza": "डिसूज़ा",
    "vicky": "विक्की", "ranjan": "रंजन", "wahab": "वहाब", "hassan": "हसन",
    "zadran": "जादरान", "panoly": "पनोली", "sabnani": "सबनानी", "parashar": "पाराशर",
    "asif": "आसिफ", "taniwal": "तानीवाल", "noorullah": "नूरुल्लाह", "ayobi": "अय्यूबी",
    # common surnames / first names
    "singh": "सिंह", "khan": "खान", "kumar": "कुमार", "ali": "अली",
    "ahmed": "अहमद", "ahmad": "अहमद", "mohammad": "मोहम्मद", "mohammed": "मोहम्मद",
    "muhammad": "मुहम्मद", "patel": "पटेल", "malik": "मलिक", "iqbal": "इकबाल",
    "rahman": "रहमान", "raza": "रज़ा",
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

INITIAL_VOWELS = {
    "aa": "आ", "ee": "ई", "oo": "ऊ", "ai": "ऐ", "au": "औ",
    "a": "अ", "e": "ए", "i": "इ", "o": "ओ", "u": "उ",
}

_NAME_NOISE = re.compile(
    r"\b(to|w|wd|nb|wide|no\s*runs?|runs?|dots?|out|c|b|wkt|wk|lb|st|\d+)\b",
    flags=re.IGNORECASE,
)


def clean_player_name_to_hindi(raw_name: Optional[str]) -> str:
    if not raw_name:
        return ""
    cleaned = _NAME_NOISE.sub("", raw_name)
    cleaned = re.sub(r"[^A-Za-z\s']", " ", cleaned).strip()
    result = []
    for word in cleaned.split():
        key = word.lower().replace("'", "")
        if not key:
            continue
        if key in COMMON_NAMES:
            result.append(COMMON_NAMES[key])
            continue
        word_hi, idx = "", 0
        while idx < len(key):
            matched = False
            for pattern, repl in PHONIC_RULES:
                if key.startswith(pattern, idx):
                    if idx == 0 and pattern in INITIAL_VOWELS:
                        repl = INITIAL_VOWELS[pattern]
                    word_hi += repl
                    idx += len(pattern)
                    matched = True
                    break
            if not matched:
                idx += 1
        result.append(word_hi if word_hi else word)
    return " ".join(result)


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
        return f"{over_phrase} {random.choice(WIDE_LINES)}{extra}", 2
    if kind == "noball":
        return f"{over_phrase} {random.choice(NOBALL_LINES)}", 2

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


# ============================================================
# WINDOWS MCI AUDIO ENGINE (short-path safe)
# ============================================================
def get_short_path_name(long_name: str) -> str:
    output_buf_size = 260
    output_buf = ctypes.create_unicode_buffer(output_buf_size)
    get_short_path = ctypes.windll.kernel32.GetShortPathNameW
    r = get_short_path(long_name, output_buf, output_buf_size)
    return output_buf.value if r > 0 else long_name


def play_mp3_native_windows(mp3_path: str) -> None:
    winmm = ctypes.windll.winmm
    alias = f"track_{os.getpid()}_{int(time.time() * 1000) % 10000}"
    safe_path = get_short_path_name(os.path.abspath(mp3_path))

    winmm.mciSendStringW("close all", None, 0, 0)

    ret = winmm.mciSendStringW(f'open "{safe_path}" type mpegvideo alias {alias}', None, 0, 0)
    if ret != 0:
        ret = winmm.mciSendStringW(f"open {safe_path} alias {alias}", None, 0, 0)
        if ret != 0:
            return

    winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
    winmm.mciSendStringW(f"close {alias}", None, 0, 0)


# ============================================================
# BROADCAST MIXER & HUMANIZER
# ============================================================
def apply_stadium_effects(raw_voice_path: str, output_path: str) -> bool:
    """Applies sidechain compression ducking and stadium headset echo via FFmpeg."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    crowd_bg = os.path.join(script_dir, "stadium_ambience.wav")

    # Failsafe: Create silent dummy track if stadium file is missing so script never crashes
    if not os.path.exists(crowd_bg):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5", crowd_bg
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    filter_complex = (
        "[0:a]highpass=f=300,lowpass=f=4000,aecho=0.8:0.88:100|150:0.4|0.3,volume=1.5[voice_fx];"
        "[1:a]volume=0.8[crowd_bg];"
        "[crowd_bg][voice_fx]sidechaincompress=threshold=0.05:ratio=8:attack=10:release=400[ducked_crowd];"
        "[voice_fx][ducked_crowd]amix=inputs=2:duration=first:normalize=0[out]"
    )

    command = [
        "ffmpeg", "-y",
        "-i", raw_voice_path,
        "-stream_loop", "-1",
        "-i", crowd_bg,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-q:a", "2",
        "-ar", "44100",
        output_path
    ]

    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        log.error(f"FFmpeg Stadium Mix failed: {e}")
        return False

def humanize_text(text: str) -> str:
    """Forces the AI to 'breathe' like a human by injecting micro-pauses at punctuation."""
    clean = re.sub(r"<[^>]+>", " ", text).replace("\r", " ").replace("\n", " ").strip()
    clean = clean.replace("!", "!... ")
    clean = clean.replace("।", "।... ")
    clean = clean.replace(",", ",... ")
    return clean


async def generate_and_play_audio(text: str, rate_level: int = 2) -> None:
    if not IS_WINDOWS:
        log.warning("Audio playback needs Windows (text only on this OS).")
        return

    clean_text = humanize_text(text)
    
    # Unpack all three variables (rate, pitch, volume) from your updated AUDIO_PROFILES
    rate_str, pitch_str, vol_str = AUDIO_PROFILES[min(max(rate_level, 1), 4)] 
    
    temp_dir = tempfile.gettempdir()
    raw_mp3 = os.path.join(temp_dir, f"_raw_{os.getpid()}_{time.time_ns()}.mp3")
    mixed_mp3 = os.path.join(temp_dir, f"_mixed_{os.getpid()}_{time.time_ns()}.mp3")

    try:
        for attempt in (1, 2):
            try:
                # Pass the volume=vol_str argument directly into the Communicate object
                comm = edge_tts.Communicate(
                    clean_text, 
                    VOICE_NAME, 
                    rate=rate_str, 
                    pitch=pitch_str, 
                    volume=vol_str
                )
                await asyncio.wait_for(comm.save(raw_mp3), timeout=TTS_TIMEOUT_SECONDS)
                break
            except Exception as e:
                log.warning("TTS attempt %d failed: %s", attempt, e)
                if attempt == 2:
                    return
                await asyncio.sleep(0.5)

        # APPLY THE PROFESSIONAL BROADCAST MIX
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, apply_stadium_effects, raw_mp3, mixed_mp3)
        final_audio = mixed_mp3 if success else raw_mp3

        # PLAY IT VIA WINDOWS MCI
        await loop.run_in_executor(None, play_mp3_native_windows, final_audio)
        
    except Exception as e:
        log.error("TTS Audio Error: %s", e)
    finally:
        for f in (raw_mp3, mixed_mp3):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass


class RadioAnnouncer:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name="radio_announcer")

    async def say(self, text: str, rate_level: int = 2, droppable: bool = False) -> None:
        if droppable and self._queue.qsize() >= MAX_BACKLOG_FOR_MINOR_BALLS:
            log.info("Skipping minor commentary (queue backlog)")
            return
        await self._queue.put((text, rate_level))

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _worker(self) -> None:
        while True:
            text, rate_level = await self._queue.get()
            try:
                print(f"\n🎙️ [MADHUR RADIO PRO | LEVEL {rate_level}]: {text}")
                await generate_and_play_audio(text, rate_level)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self._queue.task_done()


# ============================================================
# LIVE MATCH & DOM EXTRACTOR
# ============================================================
@dataclass(frozen=True)
class BallSnapshot:
    ball: Optional[str]
    circle_val: Optional[str]
    bowler: str
    batsman: str
    runs: Optional[int]
    wkts: Optional[int]
    is_over_complete: bool = False
    is_innings_break: bool = False
    last_dismissal: Optional[dict] = None
    seq: Optional[str] = None
    over_balls: Optional[list] = None

    @property
    def has_ball(self) -> bool:
        return bool(self.ball and self.circle_val)


@dataclass
class MatchState:
    last_key: Optional[str] = None
    last_wkts: Optional[int] = None
    innings_break_seen: bool = False
    match_overs: Optional[int] = None
    # One over can be announced only once even if CREX repeats its last-circle snapshot.
    announced_overs: set[int] = field(default_factory=set)


EXTRACT_JS = r'''() => {
    let ballNum = null;
    let seq = null;
    let circleVal = null;
    let bowler = '';
    let batsman = '';
    let runs = null;
    let wkts = null;
    let isOverComplete = false;
    let isInningsBreak = false;
    let lastDismissal = null;
    let activeBalls = [];

    const bodyText = document.body ? document.body.innerText : '';
    const title = document.title || '';
    const EXTRA_RE = /(?:wd|wide|nb|no\s*ball)/i;
    const BALL_TOKEN = /^(?:[0-6]|w|wkt|out|wd|wide|nb|no\s*ball|lb|b|•|\d+\s*(?:wd|nb|lb|b)|(?:wd|nb|lb|b)\s*\+?\s*\d+)$/i;

    if (/\((?:10\.0|20\.0|50\.0)\)/.test(title) || /Innings Break/i.test(bodyText) || /-10\b/.test(title)) {
        isInningsBreak = true;
    }

    let latestOverNum = null;
    let latestOverLabel = -1;
    let bestRowBallCount = -1;
    const overRows = document.querySelectorAll(
        'div[class*="over-card"], div[class*="recent-over"], div[class*="overRow"], div[class*="overWrap"], div[class*="over"]'
    );
    for (const row of overRows) {
        const rowText = row.innerText || '';
        const m = rowText.match(/\bOver\s+(\d+)\b/i);
        if (!m) continue;
        const parsedOver = parseInt(m[1], 10);
        const rowBalls = [];
        const nodes = row.querySelectorAll('span, b, div[class*="ball"], div[class*="circle"]');
        for (const node of nodes) {
            if (node.children.length !== 0) continue;
            const val = (node.innerText || '').trim();
            if (BALL_TOKEN.test(val)) rowBalls.push(val);
        }
        if (rowBalls.length > 0 && rowBalls.length <= 12) {
            // Nested DOM order is not chronological. Keep the highest visible
            // over label, then the most complete circle row for that over.
            if (parsedOver > latestOverLabel ||
                (parsedOver === latestOverLabel && rowBalls.length > bestRowBallCount)) {
                latestOverLabel = parsedOver;
                latestOverNum = parsedOver - 1;
                bestRowBallCount = rowBalls.length;
                activeBalls = rowBalls;
            }
        }
    }

    if (latestOverNum !== null && activeBalls.length > 0) {
        let legalDeliveries = 0;
        for (const v of activeBalls) {
            if (!EXTRA_RE.test(v)) legalDeliveries++;
        }
        const clamped = Math.min(Math.max(legalDeliveries, 1), 6);
        ballNum = `${latestOverNum}.${clamped}`;
        seq = `${latestOverNum}:${activeBalls.length}`;
        circleVal = activeBalls[activeBalls.length - 1];
        if (legalDeliveries === 6 && !EXTRA_RE.test(circleVal)) {
            isOverComplete = true;
        }
    }

    if (!ballNum) {
        const titleOversMatch = title.match(/\((\d{1,2}\.([1-6]))\)/);
        if (titleOversMatch) ballNum = titleOversMatch[1];
    }

    const toRe = /([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})\s+to\s+([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})(?:,|\n|$)/i;
    const commItems = document.querySelectorAll(
        'div[class*="item"], div[class*="update"], div[class*="comm-card"], div[class*="row"]'
    );
    for (const el of commItems) {
        const text = el.innerText || '';
        if (text.length > 400) continue;
        const matchTo = text.match(toRe);
        if (matchTo) {
            bowler = matchTo[1].replace(/^(?:(?:WKT|WD|NB|LB|OUT|W|B)\s+)+/i, '').trim();
            batsman = matchTo[2].trim();
            if (!circleVal) {
                const badgeEl = el.querySelector('span[class*="run"], span[class*="badge"], div[class*="run"]');
                if (badgeEl) circleVal = badgeEl.innerText.trim();
            }
            break;
        }
    }

    const allDivs = document.querySelectorAll('div');
    for (const d of allDivs) {
        const txt = d.innerText || '';
        if (txt.length > 300 || txt.length < 8) continue;
        if (txt.includes('Wkt #') || (txt.includes('OUT') && (txt.includes(' b ') || txt.includes('c ')))) {
            const cardMatch = txt.match(/([A-Za-z][A-Za-z .'\-]{1,30}?)\s*(\d{1,3})\s*\(\s*(\d{1,3})\s*\)/);
            if (cardMatch) {
                const rawName = cardMatch[1]
                    .replace(/\bOUT\b|\bWkt\s*#?\d*\b/gi, '')
                    .replace(/\s+/g, ' ')
                    .trim();
                const bRuns = cardMatch[2].trim();
                const bBalls = cardMatch[3].trim();

                let howOut = '';
                const howM = txt.match(
                    /(c\s*&\s*b\s+[A-Za-z' .\-]+|\bc\s+[A-Za-z' .\-]+?\s+b\s+[A-Za-z' .\-]+|\bst\s+[A-Za-z' .\-]+?\s+b\s+[A-Za-z' .\-]+|lbw(?:\s+b\s+[A-Za-z' .\-]+)?|run\s*out|hit\s*wicket|\bb\s+[A-Za-z' .\-]+)/i
                );
                if (howM) howOut = howM[1].replace(/\s+/g, ' ').trim();

                lastDismissal = { batter: rawName, runs: bRuns, balls: bBalls, howOut: howOut };
                break;
            }
        }
    }

    const titleScoreMatch = title.match(/([A-Za-z0-9\-]+)\s+(\d{1,3})\s*[-/]\s*([0-9]|10)\s*\(/);
    if (titleScoreMatch) {
        runs = parseInt(titleScoreMatch[2], 10);
        wkts = parseInt(titleScoreMatch[3], 10);
    }
    if (runs === null) {
        const topScoreCards = document.querySelectorAll(
            'div[class*="team-score"], div[class*="score-card"], div[class*="match-header"]'
        );
        for (const c of topScoreCards) {
            const t = c.innerText || '';
            const m = t.match(/([A-Za-z0-9\-]+)\s+(\d{1,3})\s*[-/]\s*([0-9]|10)\b/);
            if (m) {
                runs = parseInt(m[2], 10);
                wkts = parseInt(m[3], 10);
                break;
            }
        }
    }

    return {
        ball: ballNum,
        seq: seq,
        circleVal: circleVal,
        bowler: bowler,
        batsman: batsman,
        runs: runs,
        wkts: wkts,
        isOverComplete: isOverComplete,
        isInningsBreak: isInningsBreak,
        lastDismissal: lastDismissal,
        overBalls: activeBalls
    };
}'''


async def extract_circle_balls_data(page: Page) -> Optional[BallSnapshot]:
    try:
        raw = await page.evaluate(EXTRACT_JS)
        if not raw:
            return None
        return BallSnapshot(
            ball=raw.get("ball"),
            circle_val=raw.get("circleVal"),
            bowler=raw.get("bowler") or "",
            batsman=raw.get("batsman") or "",
            runs=raw.get("runs"),
            wkts=raw.get("wkts"),
            is_over_complete=raw.get("isOverComplete", False),
            is_innings_break=raw.get("isInningsBreak", False),
            last_dismissal=raw.get("lastDismissal"),
            seq=raw.get("seq"),
            over_balls=raw.get("overBalls") or [],
        )
    except Exception as exc:
        if DEBUG:
            log.warning("extract failed: %s", exc)
        return None


# ============================================================
# NETWORK RESOURCE FILTER
# ============================================================
async def route_filter(route: Route) -> None:
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    url_lower = req.url.lower()
    if any(pattern in url_lower for pattern in BLOCKED_URL_PATTERNS):
        await route.abort()
        return
    await route.continue_()


# ============================================================
# MAIN COMMENTARY LOOP
# ============================================================
def print_status(message: str) -> None:
    print(f"\r{message:<70}", end="", flush=True)


async def open_match_page(page: Page, target_url: str) -> None:
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        await page.wait_for_timeout(3000)
    except Exception as exc:
        print("Page load notice:", exc)


def is_backwards(prev: Optional[str], cur: Optional[str]) -> bool:
    try:
        po, pc = (int(x) for x in (prev or "").split(":"))
        co, cc = (int(x) for x in (cur or "").split(":"))
    except ValueError:
        return False
    return co == po and cc < pc


async def announce_over_end(
    page: Page, announcer: RadioAnnouncer, snap: BallSnapshot, state: MatchState
) -> None:
    over_int = int(snap.ball.split(".")[0]) + 1
    if over_int in state.announced_overs:
        log.info("Suppressing duplicate over recap for over %s", over_int)
        return
    # Claim it before the wait so repeated page snapshots cannot enqueue it again.
    state.announced_overs.add(over_int)

    await asyncio.sleep(OVER_SETTLE_SECONDS)
    fresh = await extract_circle_balls_data(page)
    runs = fresh.runs if fresh and fresh.runs is not None else snap.runs
    wkts = fresh.wkts if fresh and fresh.wkts is not None else snap.wkts

    stats = over_stats_from_tokens(snap.over_balls)
    print(f"\n📊 Over {over_int} done | score {runs}/{wkts} | over stats {stats}")
    msg = build_over_summary(over_int, runs, wkts, stats, state.match_overs)
    print("📢 OVER ANNOUNCEMENT:", msg)
    await announcer.say(msg, 2)


async def handle_new_ball(
    page: Page, announcer: RadioAnnouncer, snap: BallSnapshot, state: MatchState
) -> None:
    assert snap.ball and snap.circle_val
    d = classify_delivery(snap.circle_val)
    kind = d["kind"]

    print("\n" + "=" * 65)
    print(f"🏏 BALL: {snap.ball} | BADGE: [{snap.circle_val}] | {snap.bowler} to {snap.batsman}")

    dismissal_data = snap.last_dismissal
    after_score: Optional[tuple[int, int]] = None
    if kind == "wicket":
        await asyncio.sleep(WICKET_CARD_WAIT_SECONDS)
        fresh = await extract_circle_balls_data(page)
        if fresh:
            if fresh.last_dismissal:
                dismissal_data = fresh.last_dismissal
            if (
                fresh.runs is not None
                and fresh.wkts is not None
                and state.last_wkts is not None
                and fresh.wkts > state.last_wkts
            ):
                after_score = (fresh.runs, fresh.wkts)
                state.last_wkts = fresh.wkts

    if dismissal_data:
        print(f"🎯 WICKET DETAILS: {dismissal_data}")
    print("=" * 65)

    text, level = generate_circle_ball_commentary(
        snap.ball, snap.circle_val, snap.bowler, snap.batsman, dismissal_data
    )
    if after_score:
        text += " " + score_after_wicket_phrase(*after_score)
    print("🇮🇳 RADIO HINGLISH:", text)

    droppable = kind == "dot" or (kind == "runs" and d["runs"] in (1, 2, 3))
    await announcer.say(text, level, droppable=droppable)

    if snap.is_over_complete:
        await announce_over_end(page, announcer, snap, state)


async def monitor_loop(page: Page, announcer: RadioAnnouncer, state: MatchState) -> None:
    failures = 0

    while True:
        try:
            snap = await extract_circle_balls_data(page)

            if snap and snap.has_ball:
                key = snap.seq or snap.ball

                if DEBUG and key != state.last_key:
                    print(f"\n[debug] {snap}")

                if state.last_key is None and not ANNOUNCE_LAST_BALL_ON_START:
                    state.last_key = key
                    print(f"\n🔕 Synced at ball {snap.ball} [{snap.circle_val}] - commentary starts from the next delivery")
                elif key != state.last_key:
                    if is_backwards(state.last_key, key):
                        log.info("Ignoring backwards page update %s -> %s", state.last_key, key)
                    else:
                        state.last_key = key
                        await handle_new_ball(page, announcer, snap, state)
                else:
                    print_status(f"⏳ Monitoring... Last ball: {snap.ball} [{snap.circle_val}]")
            else:
                print_status("⏳ Waiting for circle balls strip to update...")

            if snap:
                if snap.is_innings_break and not state.innings_break_seen and state.last_key is not None:
                    state.innings_break_seen = True
                    msg = build_innings_end(snap.runs, snap.wkts)
                    print("\n📢", msg)
                    await announcer.say(msg, 3)
                elif not snap.is_innings_break:
                    state.innings_break_seen = False
                if snap.wkts is not None:
                    state.last_wkts = snap.wkts

            failures = 0
            await asyncio.sleep(POLL_SECONDS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            log.warning("Loop error (%d in a row): %s", failures, exc)
            if failures >= RELOAD_AFTER_FAILURES:
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
                except Exception:
                    pass
                failures = 0
            await asyncio.sleep(3)


async def run_live_stream(target_url: str) -> None:
    print("=" * 65)
    print("🏏 PREET SPORTS - PROFESSIONAL CREX RADIO ENGINE")
    print("🎙 VOICE: Microsoft Madhur Neural (Pro Commentary)")
    print("=" * 65)
    print(f"🎯 TARGET URL: {target_url}\n")

    state = MatchState(match_overs=guess_match_overs(target_url))
    if state.match_overs:
        print(f"📋 Format detected from URL: {state.match_overs}-over match")
    print("🚀 Initializing browser...")

    announcer = RadioAnnouncer()
    announcer.start()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=USER_AGENT,
                )
                page = await context.new_page()
                await page.route("**/*", route_filter)

                print("🌐 Connecting to CREX Match URL...")
                await open_match_page(page, target_url)

                print("✅ CONNECTED TO CREX")
                print("🔴 LIVE COMMENTARY READY (Press CTRL+C to exit)\n")

                await monitor_loop(page, announcer, state)
            finally:
                await browser.close()
    finally:
        await announcer.stop()


# ============================================================
# DEMO MODE  (python crex_radio_pro.py --demo)
# ============================================================
async def run_demo() -> None:
    print("🎧 DEMO MODE - sample commentary, no browser needed\n")
    announcer = RadioAnnouncer()
    announcer.start()

    samples = [
        ("3.2", "1", "Muditha Disanayaka", "Aftab Alam Shah", None),
        ("3.3", "4", "Muditha Disanayaka", "Aftab Alam Shah", None),
        ("3.4", "6", "Muditha Disanayaka", "Aftab Alam Shah", None),
        ("3.5", "W", "Muditha Disanayaka", "Aftab Alam Shah",
         {"batter": "Aftab Alam Shah", "runs": "24", "balls": "11",
          "howOut": "c Ethan DSouza b Muditha Disanayaka"}),
        ("4.1", "Wd", "Vicky Ranjan", "Aryansh Sharma", None),
        ("4.1", "W", "Vicky Ranjan", "Aryansh Sharma",
         {"batter": "Aryansh Sharma", "runs": "0", "balls": "2", "howOut": "b Vicky Ranjan"}),
        ("4.3", "W", "Vicky Ranjan", "Carl Rai",
         {"batter": "Carl Rai", "runs": "7", "balls": "5", "howOut": "lbw b Vicky Ranjan"}),
        ("4.5", "W", "Vicky Ranjan", "Asif Taniwal",
         {"batter": "Asif Taniwal", "runs": "15", "balls": "9", "howOut": "run out"}),
    ]
    for ball, badge, bowler, batsman, dismissal in samples:
        text, level = generate_circle_ball_commentary(ball, badge, bowler, batsman, dismissal)
        print(f"[{badge}] level {level}: {text}\n")
        await announcer.say(text, level)

    stats = over_stats_from_tokens(["1", "4", "0", "W", "6", "1"])
    summary = build_over_summary(6, 61, 3, stats, 10)
    print("OVER:", summary, "\n")
    await announcer.say(summary, 2)

    await announcer.wait_idle()
    await announcer.stop()



# ============================================================
# WEB-APP COMPATIBILITY ADAPTER
# The Pro Commentary functions above are kept intact. This adapter
# lets server.py call the same circle-driven scripts over WebSocket.
# ============================================================
def _web_circle_value(info):
    value = info.get("circle_val")
    if value is None or str(value).strip() == "":
        value = info.get("runs", "")
    return str(value or "").strip().upper()


def _web_ball_names(info):
    matchup = str(info.get("matchup") or "").strip()
    bowler = str(info.get("bowler") or "").strip()
    batsman = str(info.get("batsman") or info.get("striker") or "").strip()
    parts = re.split(r"\s+to\s+", matchup, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        bowler = parts[0].strip(" ,:.") or bowler
        batsman = parts[1].strip(" ,:.") or batsman
    elif "," in matchup:
        left, right = matchup.split(",", 1)
        bowler = left.strip(" ,:.") or bowler
        batsman = re.sub(r"\s*(?:को|to)\s*$", "", right.strip(" ,:."), flags=re.IGNORECASE) or batsman
    return bowler, batsman


def _web_batter_score_suffix(info):
    """Only used at over end or wicket—not spoken after every delivery."""
    players = []
    for prefix in ("striker", "non_striker"):
        name = str(info.get(prefix) or "").strip()
        runs = info.get(f"{prefix}_runs")
        balls = info.get(f"{prefix}_balls")
        if not name or runs is None or balls is None or name.lower().startswith("batter "):
            continue
        try:
            players.append(f"{name} {int(runs)} off {int(balls)}")
        except (TypeError, ValueError):
            continue
    return ", ".join(players)


def build_live_circle_announcement(ball_info, lang="hi"):
    """Use the uploaded final.py manual scripts; never speak CREX source prose."""
    info = ball_info or {}
    token = _web_circle_value(info)
    if not token:
        return {"should_speak": False}

    over_label = str(info.get("over") or info.get("ball") or "").strip()
    bowler, batsman = _web_ball_names(info)
    dismissal = (
        info.get("last_dismissal")
        or info.get("lastDismissal")
        or info.get("dismissal_card")
        or None
    )

    spoken_text, level = generate_circle_ball_commentary(
        over_label, token, bowler, batsman, dismissal
    )
    delivery = classify_delivery(token)
    kind = delivery["kind"]

    # Team score is deliberately announced only on a wicket. Routine balls stay short.
    if kind == "wicket":
        runs = info.get("total_runs")
        wickets = info.get("total_wickets")
        try:
            spoken_text += f" Score {int(runs)} for {int(wickets)}."
        except (TypeError, ValueError):
            pass
        batter_suffix = _web_batter_score_suffix(info)
        if batter_suffix:
            spoken_text += " " + batter_suffix + "."

    badges = {
        "dot": "DOT BALL",
        "runs": "RUNS",
        "wide": "WIDE",
        "noball": "NO BALL",
        "legbye": "LEG BYE",
        "bye": "BYE",
        "wicket": "WICKET",
    }
    rate, pitch, _volume = AUDIO_PROFILES[min(max(int(level), 1), 4)]
    return {
        "spoken_text": spoken_text,
        "source_text": "",
        "should_speak": True,
        "event": kind,
        "badge": badges.get(kind, "BALL"),
        "level": level,
        "rate": rate,
        "pitch": pitch,
        "circle_value": token,
        "over": over_label,
    }


def build_live_over_summary(over_index, balls, score_state=None, lang="hinglish"):
    """Use the attached Pro Commentary over recap and speak score at over end only."""
    unique = []
    seen = set()
    for ball in balls or []:
        if not isinstance(ball, dict):
            continue
        token = str(ball.get("circle_val") or ball.get("runs") or "").strip().upper()
        over = str(ball.get("over") or "").strip()
        key = (over, token, str(ball.get("matchup") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(token)

    stats = over_stats_from_tokens(unique)
    score_state = score_state or {}
    total_runs = score_state.get("total_runs")
    total_wickets = score_state.get("total_wickets")
    try:
        spoken_over = int(over_index) + 1
    except (TypeError, ValueError):
        spoken_over = 1

    # The source function owns the manual score phrasing; pass team score here,
    # not each ball's score, so score is spoken only at over completion.
    summary = build_over_summary(
        spoken_over,
        total_runs if total_runs is not None else 0,
        total_wickets if total_wickets is not None else 0,
        stats,
        None,
    )
    batter_suffix = _web_batter_score_suffix(score_state)
    if batter_suffix:
        summary += " Batters: " + batter_suffix + "."
    return summary


class _WebCommentatorAdapter:
    """Thin compatibility layer for server.py; reuses the uploaded Pro scripts."""
    def classify(self, text="", runs=""):
        token = str(runs or "").strip().upper()
        delivery = classify_delivery(token)
        level = 4 if delivery["kind"] == "wicket" or token == "6" else 3 if token == "4" else 2 if delivery["kind"] not in ("dot",) else 1
        rate, pitch, _volume = AUDIO_PROFILES[level]
        return {
            "event": delivery["kind"],
            "badge": delivery["kind"].upper(),
            "rate": rate,
            "pitch": pitch,
            "should_speak": bool(token),
        }

    def humanize(self, raw_text="", ball_info=None, lang="hi", gemini_api_key=None):
        return build_live_circle_announcement(ball_info or {}, lang=lang)

    async def humanize_async(self, raw_text="", ball_info=None, lang="hi", gemini_api_key=None):
        return self.humanize(raw_text, ball_info=ball_info, lang=lang, gemini_api_key=gemini_api_key)


commentator = _WebCommentatorAdapter()

def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG if DEBUG else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    configure_stdio()
    disable_quickedit()
    prevent_sleep_and_throttle()

    try:
        if DEMO:
            asyncio.run(run_demo())
            return

        print("\n" + "=" * 60)
        print("🏏 PREET SPORTS - CREX RADIO RUNNER")
        print("=" * 60)
        cli_urls = [a for a in sys.argv[1:] if not a.startswith("--")]
        if cli_urls:
            final_url = cli_urls[0]
        else:
            user_url = input("Enter CREX Match URL (Press ENTER to use default):\n> ").strip()
            final_url = user_url or DEFAULT_URL
        asyncio.run(run_live_stream(final_url))
    except KeyboardInterrupt:
        print("\n\n🛑 Script stopped by user.")
    except Exception:
        traceback.print_exc()
        input("\nPress ENTER to close...")


if __name__ == "__main__":
    main()
