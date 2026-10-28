#!/usr/bin/env python3
"""PREET SPORTS - Single-file CREX Live Commentary Runner.

Run:
  python live_commentary.py
  python live_commentary.py "https://crex.com/cricket-live-score/..."
  python live_commentary.py --demo
Install:
  py -m pip install edge-tts playwright
  py -m playwright install chromium
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
import time
import traceback
from dataclasses import dataclass
from typing import Optional

try:
    import edge_tts
    from playwright.async_api import Page, Route, async_playwright
except ImportError as exc:
    print(f"Missing dependency: {exc}. Run: py -m pip install edge-tts playwright")
    raise

IS_WINDOWS = sys.platform == "win32"
DEBUG = "--debug" in sys.argv
DEMO = "--demo" in sys.argv
DEFAULT_URL = "https://crex.com/cricket-live-score/abd-vs-emb-2nd-semi-final-emirates-d10-league-2026-match-updates-14NA"
POLL_SECONDS = 0.5
OVER_SETTLE_SECONDS = 0.8
WICKET_CARD_WAIT_SECONDS = 0.5
PAGE_LOAD_TIMEOUT_MS = 45_000
RELOAD_AFTER_FAILURES = 5
TTS_TIMEOUT_SECONDS = 12
VOICE_NAME = "hi-IN-MadhurNeural"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
ANNOUNCE_LAST_BALL_ON_START = False
MAX_BACKLOG_FOR_MINOR_BALLS = 2
BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
BLOCKED_URL_PATTERNS = ("doubleclick", "googlesyndication", "taboola", "adsystem", "adnxs", "amazon-adsystem")
AUDIO_PROFILES = {1: ("+3%", "+0Hz"), 2: ("+8%", "+1Hz"), 3: ("+12%", "+3Hz"), 4: ("+15%", "+5Hz")}
log = logging.getLogger("crex_radio")


def disable_quickedit() -> None:
    if not IS_WINDOWS:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value & ~0x0040)
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


HINDI_OVER_WORDS = {
    1:"पहले",2:"दूसरे",3:"तीसरे",4:"चौथे",5:"पांचवें",6:"छठे",7:"सातवें",8:"आठवें",9:"नौवें",10:"दसवें",
    11:"ग्यारहवें",12:"बारहवें",13:"तेरहवें",14:"चौदहवें",15:"पंद्रहवें",16:"सोलहवें",17:"सत्रहवें",18:"अठारहवें",19:"उन्नीसवें",20:"बीसवें",
}
HINDI_BALL_WORDS = {1:"पहली गेंद",2:"दूसरी गेंद",3:"तीसरी गेंद",4:"चौथी गेंद",5:"पांचवीं गेंद",6:"आखिरी गेंद"}
NUM_TO_HINDI = {
    0:"शून्य",1:"एक",2:"दो",3:"तीन",4:"चार",5:"पांच",6:"छह",7:"सात",8:"आठ",9:"नौ",10:"दस",
    11:"ग्यारह",12:"बारह",13:"तेरह",14:"चौदह",15:"पंद्रह",16:"सोलह",17:"सत्रह",18:"अठारह",19:"उन्नीस",20:"बीस",
    21:"इक्कीस",22:"बाईस",23:"तेईस",24:"चौबीस",25:"पच्चीस",26:"छब्बीस",27:"सत्ताईस",28:"अट्ठाईस",29:"उनतीस",30:"तीस",
    31:"इकत्तीस",32:"बत्तीस",33:"तैंतीस",34:"चौंतीस",35:"पैंतीस",36:"छत्तीस",37:"सैंतीस",38:"अड़तीस",39:"उनतालीस",40:"चालीस",
    41:"इकतालीस",42:"बयालीस",43:"तैंतालीस",44:"चौवालीस",45:"पैंतालीस",46:"छियालीस",47:"सैंतालीस",48:"अड़तालीस",49:"उनचास",50:"पचास",
    51:"इक्यावन",52:"बावन",53:"तिरेपन",54:"चौवन",55:"पचपन",56:"छप्पन",57:"सत्तावन",58:"अट्ठावन",59:"उनसठ",60:"साठ",
    61:"इकसठ",62:"बासठ",63:"तिरसठ",64:"चौंसठ",65:"पैंसठ",66:"छियासठ",67:"सरसठ",68:"अड़सठ",69:"उनहत्तर",70:"सत्तर",
    71:"इकहत्तर",72:"बहत्तर",73:"तिहत्तर",74:"चौहत्तर",75:"पचहत्तर",76:"छिहत्तर",77:"सतहत्तर",78:"अठहत्तर",79:"उनासी",80:"अस्सी",
    81:"इक्यासी",82:"बयासी",83:"तिरासी",84:"चौरासी",85:"पचासी",86:"छियासी",87:"सत्तासी",88:"अठासी",89:"नवासी",90:"नब्बे",
    91:"इक्यानवे",92:"बानवे",93:"तिरानवे",94:"चौरानवे",95:"पंचानवे",96:"छियानवे",97:"सत्तानवे",98:"अट्ठानवे",99:"निन्यानवे",100:"एक सौ",
}
HINDI_HUNDREDS = {1:"एक सौ",2:"दो सौ",3:"तीन सौ",4:"चार सौ",5:"पांच सौ",6:"छह सौ",7:"सात सौ",8:"आठ सौ",9:"नौ सौ"}


def number_to_hindi_words(value) -> str:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return str(value)
    if number in NUM_TO_HINDI:
        return NUM_TO_HINDI[number]
    if 101 <= number <= 999:
        hundreds, remainder = divmod(number, 100)
        head = HINDI_HUNDREDS.get(hundreds, f"{hundreds} सौ")
        return head if not remainder else f"{head} {NUM_TO_HINDI.get(remainder, str(remainder))}"
    return str(number)


def over_ordinal_hindi(over_number: int) -> str:
    return HINDI_OVER_WORDS.get(over_number, f"{number_to_hindi_words(over_number)}वें")


def parse_ball(ball_text: str) -> tuple[int, int]:
    parts = (ball_text or "").strip().split(".")
    over_number = int(parts[0]) + 1
    index = int(parts[1]) if len(parts) > 1 and parts[1] else 1
    return over_number, min(max(index, 1), 6)


def format_ball_phrase_hindi(ball_text: str) -> str:
    try:
        over_number, index = parse_ball(ball_text)
        return f"{over_ordinal_hindi(over_number)} ओवर की {HINDI_BALL_WORDS.get(index, f'{index}वीं गेंद')}"
    except Exception:
        return "अगली गेंद"


def format_over_phrase_hindi(ball_text: str) -> str:
    try:
        over_number, _ = parse_ball(ball_text)
        return f"{over_ordinal_hindi(over_number)} ओवर में"
    except Exception:
        return "इस ओवर में"


COMMON_NAMES = {
    "muditha":"मुदिथा","disanayaka":"डिसानायका","aftab":"आफताब","alam":"आलम","shah":"शाह","aryansh":"आर्यांश","sharma":"शर्मा",
    "y":"वाई","rai":"राय","ethan":"ईथन","carl":"कार्ल","dsouza":"डिसूज़ा","vicky":"विक्की","ranjan":"रंजन","wahab":"वहाब",
    "hassan":"हसन","zadran":"जादरान","panoly":"पनोली","sabnani":"सबनानी","parashar":"पाराशर","asif":"आसिफ","taniwal":"तानीवाल",
    "noorullah":"नूरुल्लाह","ayobi":"अय्यूबी","singh":"सिंह","khan":"खान","kumar":"कुमार","ali":"अली","ahmed":"अहमद","ahmad":"अहमद",
    "mohammad":"मोहम्मद","mohammed":"मोहम्मद","muhammad":"मुहम्मद","patel":"पटेल","malik":"मलिक","iqbal":"इकबाल","rahman":"रहमान","raza":"रज़ा",
}
PHONIC_RULES = (
    ("sh","श"),("ch","च"),("th","थ"),("ph","फ"),("kh","ख"),("gh","घ"),("bh","भ"),("dh","ध"),("jh","झ"),
    ("ee","ी"),("oo","ू"),("aa","ा"),("ai","ै"),("au","ौ"),("a","ा"),("b","ब"),("c","क"),("d","ड"),
    ("e","े"),("f","फ"),("g","ग"),("h","ह"),("i","ि"),("j","ज"),("k","क"),("l","ल"),("m","म"),("n","न"),
    ("o","ो"),("p","प"),("q","क"),("r","र"),("s","स"),("t","ट"),("u","ु"),("v","व"),("w","व"),("x","क्स"),("y","य"),("z","ज़"),
)
INITIAL_VOWELS = {"aa":"आ","ee":"ई","oo":"ऊ","ai":"ऐ","au":"औ","a":"अ","e":"ए","i":"इ","o":"ओ","u":"उ"}
_NAME_NOISE = re.compile(r"\b(to|w|wd|nb|wide|no\s*runs?|runs?|dots?|out|c|b|wkt|wk|lb|st|\d+)\b", re.I)


def clean_player_name_to_hindi(raw_name: Optional[str]) -> str:
    if not raw_name:
        return ""
    cleaned = _NAME_NOISE.sub("", raw_name)
    cleaned = re.sub(r"[^A-Za-z\s']", " ", cleaned).strip()
    output = []
    for word in cleaned.split():
        key = word.lower().replace("'", "")
        if key in COMMON_NAMES:
            output.append(COMMON_NAMES[key])
            continue
        converted, position = "", 0
        while position < len(key):
            for pattern, replacement in PHONIC_RULES:
                if key.startswith(pattern, position):
                    if position == 0 and pattern in INITIAL_VOWELS:
                        replacement = INITIAL_VOWELS[pattern]
                    converted += replacement
                    position += len(pattern)
                    break
            else:
                position += 1
        output.append(converted or word)
    return " ".join(output)


def classify_delivery(token: Optional[str]) -> dict:
    value = re.sub(r"[\s+]", "", (token or "").strip().lower())
    match = re.search(r"\d+", value)
    runs = int(match.group()) if match else None
    if value in ("w", "wkt", "out"):
        return {"kind":"wicket","runs":0,"legal":True}
    if "wd" in value or "wide" in value:
        return {"kind":"wide","runs":runs if runs else 1,"legal":False}
    if "nb" in value or "noball" in value:
        return {"kind":"noball","runs":runs if runs else 1,"legal":False}
    if "lb" in value:
        return {"kind":"legbye","runs":runs if runs else 1,"legal":True}
    if re.fullmatch(r"\d*b|b\d*", value):
        return {"kind":"bye","runs":runs if runs else 1,"legal":True}
    if value.isdigit():
        return {"kind":"runs" if runs else "dot","runs":runs or 0,"legal":True}
    return {"kind":"dot","runs":0,"legal":True}


def over_stats_from_tokens(tokens) -> dict:
    stats = {"runs":0,"fours":0,"sixes":0,"wickets":0,"legal":0}
    for token in tokens or []:
        delivery = classify_delivery(token)
        stats["runs"] += delivery["runs"]
        stats["legal"] += int(delivery["legal"])
        if delivery["kind"] == "wicket":
            stats["wickets"] += 1
        elif delivery["kind"] == "runs" and delivery["runs"] == 4:
            stats["fours"] += 1
        elif delivery["kind"] == "runs" and delivery["runs"] == 6:
            stats["sixes"] += 1
    return stats


def guess_match_overs(url: str) -> Optional[int]:
    value = (url or "").lower()
    if re.search(r"(?<![a-z0-9])[dt]10(?![a-z0-9])", value):
        return 10
    if re.search(r"(?<![a-z0-9])t20i?(?![a-z0-9])", value) or "twenty20" in value:
        return 20
    if re.search(r"(?<![a-z0-9])(?:odi|50-over)(?![a-z0-9])", value):
        return 50
    return None


SIX_LINES = (
    "उठाकर मारा! गेंद आसमान में... बहुत ऊंची... और ये गई स्टैंड्स में! छह रन!",
    "क्रीज़ से बाहर निकलकर सीधा प्रहार! गेंद मैदान के बाहर, गगनचुंबी छक्का!",
    "बल्ले के बिल्कुल बीचों-बीच लगी! गेंद सीमा रेखा के बहुत पार, दर्शकों के बीच! छह रन!",
    "ये तो सीधा आसमान में! फील्डर बस देखते रह गए, ये छक्का है!",
    "क्या हिट है! स्वीट स्पॉट से निकली और गेंद स्टेडियम के बाहर की तरफ़! मैक्सिमम!",
    "इसे कहते हैं पावर हिटिंग! गेंद हवा में और सीधा सीमा रेखा के उस पार! छह रन!",
)
SIX_TAGS = ("{batsman} का ज़बरदस्त छक्का!","{bowler} की गेंद पर पड़ी ज़बरदस्त मार!","{batsman} पूरे रंग में नज़र आ रहे हैं!","{bowler} के लिए बहुत महंगी साबित हुई ये गेंद!")
FOUR_LINES = (
    "गजब की टाइमिंग! फील्डर को हिलने का भी मौका नहीं मिला, गेंद गोली की रफ्तार से सीमा रेखा के पार! चौका!",
    "कलाइयों का जादू! खूबसूरत प्लेसमेंट, गेंद रस्सी के पार! चार रन!",
    "गैप मिला और गेंद सीधी बाउंड्री पर! फील्डर ने पीछा किया पर बहुत देर हो चुकी थी, चौका!",
    "ज़मीन के साथ-साथ दौड़ी गेंद और बाउंड्री लाइन के पार! शानदार चौका!",
    "क्लासिक शॉट! बेहतरीन टाइमिंग, गेंद चार रन के लिए सीमा रेखा के बाहर!",
)
FOUR_TAGS = ("{batsman} का खूबसूरत शॉट!","{bowler} की लेंथ थोड़ी चूकी और बल्लेबाज़ ने उसे सज़ा दे दी!","{batsman} को मिल गई लय!")
DOT_LINES = ("कसी हुई लाइन और लेंथ, बल्लेबाज़ ने संभलकर खेला, कोई रन नहीं, डॉट बॉल।","शानदार गेंद! बल्लेबाज़ को पूरी तरह छकाया, गेंद सीधे विकेटकीपर के दस्तानों में।","सीधा फील्डर के हाथों में खेला, रन का कोई मौका नहीं।","बल्ले के बाहरी किनारे को छूते-छूते बची गेंद, कोई रन नहीं।")
SINGLE_LINES = ("हल्के हाथों से कवर्स की ओर खेला और तेज़ी से एक सिंगल पूरा किया।","कलाइयों को मोड़कर स्ट्राइक रोटेट की, आसानी से एक रन चुरा लिया।","गेंद को गैप में धकेला और एक रन लेकर स्ट्राइक बदल ली।")
DOUBLE_LINES = ("गैप में खेलकर दो फील्डर्स के बीच से बढ़िया रनिंग, आसानी से दो रन पूरे किए!","सॉफ्ट हैंड्स से धकेला, बढ़िया तालमेल और दौड़कर दो रन निकाले!")
THREE_LINE = "शानदार रनिंग बिटवीन द विकेट्स, दोनों बल्लेबाज़ों ने दौड़कर तीन रन पूरे किए!"
FIVE_LINE = "पांच रन! ओवरथ्रो का फायदा या बेहतरीन दौड़, बल्लेबाज़ों ने पांच रन बटोर लिए!"
WIDE_LINES = ("दिशाहीन गेंदबाज़ी, लेग स्टंप से काफ़ी बाहर, अंपायर ने दोनों हाथ फैलाए, वाइड!","लाइन से भटकी गेंद, वाइड का इशारा! गेंदबाज़ को एक गेंद और डालनी होगी!")
NOBALL_LINES = ("नो बॉल! गेंदबाज़ ने क्रीज़ को ओवरस्टेप किया, अब अगली गेंद फ्री हिट!","अंपायर का हाथ ऊपर, नो बॉल! और अगली गेंद पर मिलेगी फ्री हिट!")
LEGBYE_LINE = "गेंद पैड से लगकर निकल गई, बल्लेबाज़ों ने लेग बाई के {runs} रन दौड़कर चुरा लिए!"
BYE_LINE = "विकेटकीपर के हाथों से निकल गई गेंद, बाई के {runs} रन!"
INTROS_BOTH = ("{bowler} की गेंद, सामने {batsman},","{bowler} दौड़ते हुए आए, गेंद {batsman} की ओर,","{bowler} ने डाली गेंद, {batsman} तैयार,","{bowler} से {batsman} को,")


def build_intro(bowler: str, batsman: str) -> str:
    if bowler and batsman:
        return random.choice(INTROS_BOTH).format(bowler=bowler, batsman=batsman)
    if bowler:
        return f"{bowler} गेंदबाज़ी छोर से तैयार,"
    return ""


def pick_tag(tags, batsman: str, bowler: str) -> str:
    usable = [tag for tag in tags if not (("{batsman}" in tag and not batsman) or ("{bowler}" in tag and not bowler))]
    return random.choice(usable).format(batsman=batsman, bowler=bowler) if usable else ""


_NAME = r"[a-z .'-]"


def build_live_wicket_speech(dismissal_text: str, batter_name: str, batter_runs: str, batter_balls: str, bowler_name: str = "") -> str:
    batter_hi = clean_player_name_to_hindi(batter_name) or "बल्लेबाज़"
    runs_known = str(batter_runs).strip().isdigit()
    balls_known = str(batter_balls).strip().isdigit()
    runs_value = int(batter_runs) if runs_known else 0
    runs_hi = number_to_hindi_words(runs_value)
    balls_hi = number_to_hindi_words(batter_balls) if balls_known else ""
    if runs_known and runs_value > 0:
        status = f"{balls_hi} गेंदों में {runs_hi} रन बनाकर" if balls_hi else f"{runs_hi} रन बनाकर"
    elif runs_known:
        status = "बिना खाता खोले"
    else:
        status = ""
    who = f"{batter_hi} {status}".strip()
    dismissal = re.sub(r"\s+", " ", (dismissal_text or "").lower()).strip()
    match = re.search(rf"\bc\s*&\s*b\s+({_NAME}+)", dismissal)
    if match:
        bowler = clean_player_name_to_hindi(match.group(1))
        return random.choice((f"और ये लिया कैच, खुद गेंदबाज़ ने! {bowler} ने अपनी ही गेंद पर लपका कैच! {who} पवेलियन लौट रहे हैं!",f"कैच एंड बोल्ड! {bowler} ने ज़बरदस्त रिफ्लेक्स दिखाया, {who} वापस लौट रहे हैं!"))
    match = re.search(rf"\bc\s+({_NAME}+?)\s+b\s+({_NAME}+)", dismissal)
    if match:
        fielder, bowler = clean_player_name_to_hindi(match.group(1)), clean_player_name_to_hindi(match.group(2))
        return random.choice((f"गेंद हवा में उठी... और सुरक्षित हाथों में! {fielder} ने कैच लपक लिया! विकेट! {who} पवेलियन लौट रहे हैं, {bowler} को मिली बड़ी कामयाबी!",f"बड़ा विकेट! शॉट गलत लगा और {fielder} ने कोई गलती नहीं की! {who} कैच आउट, {bowler} की गेंद पर!",f"आउट! आउट! {bowler} की गेंद पर {fielder} ने थामा शानदार कैच, {who} पवेलियन लौट रहे हैं!"))
    match = re.search(rf"\bst\s+({_NAME}+?)\s+b\s+({_NAME}+)", dismissal)
    if match:
        keeper, bowler = clean_player_name_to_hindi(match.group(1)), clean_player_name_to_hindi(match.group(2))
        return f"बल्लेबाज़ क्रीज़ से बाहर... और विकेटकीपर {keeper} ने बेल्स उड़ा दीं! स्टंप्ड! {who} पवेलियन लौट रहे हैं, {bowler} की गेंद पर!"
    if "lbw" in dismissal:
        match = re.search(rf"lbw\s+b\s+({_NAME}+)", dismissal)
        bowler = clean_player_name_to_hindi(match.group(1)) if match else clean_player_name_to_hindi(bowler_name)
        return f"ज़ोरदार अपील... और अंपायर ने उंगली उठा दी! एलबीडब्ल्यू! {who} पवेलियन लौट रहे हैं" + (f", {bowler} को मिला विकेट!" if bowler else "!")
    if "run out" in dismissal or "runout" in dismissal:
        return random.choice((f"आपसी तालमेल बिगड़ा... थ्रो सीधा स्टंप्स पर... और रन आउट! {who} पवेलियन लौट रहे हैं!",f"रन चुराने की कोशिश भारी पड़ी! रन आउट! {who} वापस लौट रहे हैं!"))
    if "hit wicket" in dismissal or "hitwicket" in dismissal:
        return f"अपने ही स्टंप्स पर जा लगा बल्ला! हिट विकेट! {who} पवेलियन लौट रहे हैं!"
    match = re.search(rf"\bb\s+({_NAME}+)", dismissal)
    if match:
        bowler = clean_player_name_to_hindi(match.group(1))
        return random.choice((f"गिल्लियां बिखर गईं! क्लीन बोल्ड! {bowler} की शानदार गेंद ने स्टंप उखाड़ दिए! {who} पवेलियन लौट रहे हैं!",f"बोल्ड! बोल्ड! {bowler} ने बल्लेबाज़ को पूरी तरह छका दिया, {who} पवेलियन लौट रहे हैं!",f"ज़बरदस्त गेंद! बल्ले और पैड के बीच से निकली और सीधी स्टंप्स में! {bowler} को मिला विकेट, {who} वापस लौट रहे हैं!"))
    bowler = clean_player_name_to_hindi(bowler_name)
    return f"विकेट! विकेट! {bowler} की गेंद पर बड़ा झटका! {who} पवेलियन लौट रहे हैं!" if bowler else f"विकेट! बड़ा झटका! {who} पवेलियन लौट रहे हैं!"


def score_after_wicket_phrase(runs: int, wickets: int) -> str:
    phrase = f"स्कोर अब {number_to_hindi_words(runs)} रन, {number_to_hindi_words(wickets)} विकेट के नुकसान पर।"
    return phrase + (" और ये आखिरी विकेट! पूरी टीम ऑल आउट!" if wickets >= 10 else "")


def generate_circle_ball_commentary(ball_num: str, circle_val: Optional[str], bowler_raw: Optional[str], batsman_raw: Optional[str], last_dismissal: Optional[dict] = None) -> tuple[str, int]:
    ball_phrase, over_phrase = format_ball_phrase_hindi(ball_num), format_over_phrase_hindi(ball_num)
    bowler, batsman = clean_player_name_to_hindi(bowler_raw), clean_player_name_to_hindi(batsman_raw)
    delivery = classify_delivery(circle_val)
    kind, runs = delivery["kind"], delivery["runs"]
    if kind == "wicket":
        card = last_dismissal or {}
        batter_name = batsman_raw or ""
        runs_text, balls_text, how_out = "", "", ""
        card_name = card.get("batter", "") or ""
        if card_name and ((not batsman_raw) or any(part.lower() in card_name.lower() for part in batsman_raw.split() if len(part) > 2)):
            batter_name, runs_text, balls_text, how_out = card_name, card.get("runs", ""), card.get("balls", ""), card.get("howOut", "")
        return f"{ball_phrase}, {build_live_wicket_speech(how_out, batter_name, runs_text, balls_text, bowler_raw or '')}", 4
    if kind == "wide":
        extra = f" अतिरिक्त {number_to_hindi_words(runs)} रन।" if runs > 1 else ""
        return f"{over_phrase} {random.choice(WIDE_LINES)}{extra}", 2
    if kind == "noball":
        return f"{over_phrase} {random.choice(NOBALL_LINES)}", 2
    intro, tag = build_intro(bowler, batsman), ""
    if kind == "legbye":
        action, level = LEGBYE_LINE.format(runs=number_to_hindi_words(runs)), 2
    elif kind == "bye":
        action, level = BYE_LINE.format(runs=number_to_hindi_words(runs)), 2
    elif runs >= 6:
        action, level = random.choice(SIX_LINES), 4
        tag = pick_tag(SIX_TAGS, batsman, bowler)
    elif runs == 5:
        action, level = FIVE_LINE, 2
    elif runs == 4:
        action, level = random.choice(FOUR_LINES), 3
        tag = pick_tag(FOUR_TAGS, batsman, bowler)
    elif runs == 3:
        action, level = THREE_LINE, 2
    elif runs == 2:
        action, level = random.choice(DOUBLE_LINES), 2
    elif runs == 1:
        action, level = random.choice(SINGLE_LINES), 2
    else:
        action, level = random.choice(DOT_LINES), 1
    return " ".join(part for part in (f"{ball_phrase},", intro, action, tag) if part), level


def _count_phrase(number: int, singular: str, plural: str) -> str:
    return singular if number == 1 else f"{number_to_hindi_words(number)} {plural}"


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


def build_over_summary(over_int: int, runs, wickets, stats: Optional[dict] = None, match_overs: Optional[int] = None) -> str:
    over_word, parts = over_ordinal_hindi(over_int), []
    if stats and stats.get("legal", 0) >= 6:
        if stats["runs"] == 0 and stats["wickets"] == 0:
            parts.append(f"ओवर समाप्त! {over_word} ओवर मेडन! एक भी रन नहीं आया, कमाल की गेंदबाज़ी!")
        elif stats["runs"] == 0:
            wicket_text = _count_phrase(stats["wickets"], "एक विकेट", "विकेट")
            parts.append(f"ओवर समाप्त! विकेट मेडन! {over_word} ओवर में एक भी रन नहीं और {wicket_text}! शानदार ओवर!")
        else:
            details = []
            if stats["sixes"]: details.append(_count_phrase(stats["sixes"], "एक छक्का", "छक्के"))
            if stats["fours"]: details.append(_count_phrase(stats["fours"], "एक चौका", "चौके"))
            if stats["wickets"]: details.append(_count_phrase(stats["wickets"], "एक विकेट", "विकेट"))
            line = f"ओवर समाप्त! {over_word} ओवर में आए {number_to_hindi_words(stats['runs'])} रन"
            if details: line += ", जिसमें " + _join_hindi(details)
            parts.append(line + "।")
    else:
        parts.append(f"ओवर समाप्त! {over_word} ओवर का खेल पूरा हुआ।")
    try:
        total_runs, total_wickets = int(str(runs).strip()), int(str(wickets).strip())
    except (TypeError, ValueError):
        return " ".join(parts)
    if total_wickets == 0:
        parts.append(f"स्कोर {number_to_hindi_words(total_runs)} रन, बिना किसी विकेट के नुकसान के।")
    else:
        parts.append(f"स्कोर {number_to_hindi_words(total_runs)} रन, {number_to_hindi_words(total_wickets)} विकेट के नुकसान पर।")
    run_rate = round(total_runs / max(over_int, 1), 2)
    rate_words = number_to_hindi_words(int(run_rate))
    high, mid = _rr_thresholds(match_overs)
    if run_rate >= high: parts.append(f"करंट रन रेट {rate_words} के पार पहुंच चुका है, बल्लेबाज़ पूरी तरह हावी हैं।")
    elif run_rate >= mid: parts.append(f"रन गति लगभग {rate_words} प्रति ओवर की है, दोनों टीमें अपनी रणनीति पर कायम हैं।")
    else: parts.append(f"रन गति {rate_words} के आसपास थोड़ी धीमी है, अब बड़े शॉट्स की तलाश होगी।")
    if match_overs and over_int < match_overs:
        parts.append(f"पारी में अभी {number_to_hindi_words(match_overs - over_int)} ओवर बाकी हैं।")
    return " ".join(parts)


def build_innings_end(runs, wickets) -> str:
    try:
        return f"पारी समाप्त! स्कोर {number_to_hindi_words(int(runs))} रन, {number_to_hindi_words(int(wickets))} विकेट के नुकसान पर।"
    except (TypeError, ValueError):
        return "पारी समाप्त!"


def get_short_path_name(long_name: str) -> str:
    buffer = ctypes.create_unicode_buffer(260)
    get_short_path = ctypes.windll.kernel32.GetShortPathNameW
    return buffer.value if get_short_path(long_name, buffer, 260) > 0 else long_name


def play_mp3_native_windows(mp3_path: str) -> None:
    winmm = ctypes.windll.winmm
    alias = f"track_{os.getpid()}_{int(time.time() * 1000) % 10000}"
    safe_path = get_short_path_name(os.path.abspath(mp3_path))
    winmm.mciSendStringW("close all", None, 0, 0)
    result = winmm.mciSendStringW(f'open "{safe_path}" type mpegvideo alias {alias}', None, 0, 0)
    if result != 0:
        result = winmm.mciSendStringW(f"open {safe_path} alias {alias}", None, 0, 0)
        if result != 0:
            log.error("Windows MCI could not open the generated MP3.")
            return
    winmm.mciSendStringW(f"play {alias} wait", None, 0, 0)
    winmm.mciSendStringW(f"close {alias}", None, 0, 0)


async def generate_and_play_audio(text: str, rate_level: int = 2) -> None:
    if not IS_WINDOWS:
        log.warning("Windows MCI audio playback is unavailable on this OS.")
        return
    clean_text = re.sub(r"<[^>]+>", " ", text).replace("\r", " ").replace("\n", " ").strip()
    rate, pitch = AUDIO_PROFILES[min(max(rate_level, 1), 4)]
    mp3_path = os.path.join(tempfile.gettempdir(), f"_radio_{os.getpid()}_{time.time_ns()}.mp3")
    try:
        for attempt in (1, 2):
            try:
                comm = edge_tts.Communicate(clean_text, VOICE_NAME, rate=rate, pitch=pitch)
                await asyncio.wait_for(comm.save(mp3_path), timeout=TTS_TIMEOUT_SECONDS)
                break
            except Exception as exc:
                log.warning("TTS attempt %d failed: %s", attempt, exc)
                if attempt == 2:
                    return
                await asyncio.sleep(0.3)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, play_mp3_native_windows, mp3_path)
    except Exception:
        log.exception("TTS audio error")
    finally:
        if os.path.exists(mp3_path):
            try: os.remove(mp3_path)
            except OSError: pass


class RadioAnnouncer:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._worker(), name="radio_announcer")

    async def say(self, text: str, rate_level: int = 2, droppable: bool = False) -> None:
        if droppable and self._queue.qsize() >= MAX_BACKLOG_FOR_MINOR_BALLS:
            log.info("Skipping minor commentary; audio queue backlog is high.")
            return
        await self._queue.put((text, rate_level))

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task = None

    async def _worker(self) -> None:
        while True:
            text, level = await self._queue.get()
            try:
                print(f"\n🎙️ [MADHUR RADIO | LEVEL {level}]: {text}")
                await generate_and_play_audio(text, level)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Radio worker failed")
            finally:
                self._queue.task_done()


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


EXTRACT_JS = r'''() => {
    let ballNum=null, seq=null, circleVal=null, bowler='', batsman='', runs=null, wkts=null;
    let isOverComplete=false, isInningsBreak=false, lastDismissal=null, activeBalls=[];
    const bodyText=document.body?document.body.innerText:'', title=document.title||'';
    const EXTRA_RE=/(?:wd|wide|nb|no\s*ball)/i;
    const BALL_TOKEN=/^(?:[0-6]|w|wkt|out|wd|wide|nb|no\s*ball|lb|b|•|\d+\s*(?:wd|nb|lb|b)|(?:wd|nb|lb|b)\s*\+?\s*\d+)$/i;
    if (/\((?:10\.0|20\.0|50\.0)\)/.test(title)||/Innings Break/i.test(bodyText)||/-10\b/.test(title)) isInningsBreak=true;
    let latestOverLabel=-1,bestCount=-1;
    const overRows=document.querySelectorAll('div[class*="over-card"],div[class*="recent-over"],div[class*="overRow"],div[class*="overWrap"],div[class*="over"]');
    for(const row of overRows){
        const m=(row.innerText||'').match(/\bOver\s+(\d+)\b/i); if(!m)continue;
        const overLabel=Number(m[1]),tokens=[];
        for(const node of row.querySelectorAll('span,b,div[class*="ball"],div[class*="circle"]')){
            if(node.children.length)continue;
            const text=(node.innerText||'').trim();if(BALL_TOKEN.test(text))tokens.push(text);
        }
        if(tokens.length&&tokens.length<=12&&(overLabel>latestOverLabel||(overLabel===latestOverLabel&&tokens.length>bestCount))){
            latestOverLabel=overLabel;bestCount=tokens.length;activeBalls=tokens;
        }
    }
    if(latestOverLabel>0&&activeBalls.length){
        const legal=activeBalls.filter(v=>!EXTRA_RE.test(v)).length;
        ballNum=(latestOverLabel-1)+'.'+Math.min(6,Math.max(1,legal));
        seq=(latestOverLabel-1)+':'+activeBalls.length;
        circleVal=activeBalls[activeBalls.length-1];
        isOverComplete=legal===6&&!EXTRA_RE.test(circleVal);
    }
    if(!ballNum){const m=title.match(/\((\d{1,2}\.([1-6]))\)/);if(m)ballNum=m[1];}
    const toRe=/([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})\s+to\s+([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})(?:,|\n|$)/i;
    for(const el of document.querySelectorAll('div[class*="item"],div[class*="update"],div[class*="comm-card"],div[class*="row"]')){
        const text=el.innerText||'';if(text.length>400)continue;const m=text.match(toRe);
        if(m){bowler=m[1].replace(/^(?:(?:WKT|WD|NB|LB|OUT|W|B)\s+)+/i,'').trim();batsman=m[2].trim();
            if(!circleVal){const badge=el.querySelector('span[class*="run"],span[class*="badge"],div[class*="run"]');if(badge)circleVal=badge.innerText.trim();}
            break;}
    }
    for(const el of document.querySelectorAll('div')){
        const text=el.innerText||'';if(text.length>300||text.length<8)continue;
        if(text.includes('Wkt #')||(text.includes('OUT')&&(text.includes(' b ')||text.includes('c ')))){
            const m=text.match(/([A-Za-z][A-Za-z .'\-]{1,30}?)\s*(\d{1,3})\s*\(\s*(\d{1,3})\s*\)/);if(!m)continue;
            const how=text.match(/(c\s*&\s*b\s+[A-Za-z' .\-]+|\bc\s+[A-Za-z' .\-]+?\s+b\s+[A-Za-z' .\-]+|\bst\s+[A-Za-z' .\-]+?\s+b\s+[A-Za-z' .\-]+|lbw(?:\s+b\s+[A-Za-z' .\-]+)?|run\s*out|hit\s*wicket|\bb\s+[A-Za-z' .\-]+)/i);
            lastDismissal={batter:m[1].replace(/\bOUT\b|\bWkt\s*#?\d*\b/gi,'').replace(/\s+/g,' ').trim(),runs:m[2],balls:m[3],howOut:how?how[1].replace(/\s+/g,' ').trim():''};break;
        }
    }
    const score=title.match(/([A-Za-z0-9\-]+)\s+(\d{1,3})\s*[-/]\s*([0-9]|10)\s*\(/);
    if(score){runs=Number(score[2]);wkts=Number(score[3]);}
    if(runs===null){for(const el of document.querySelectorAll('div[class*="team-score"],div[class*="score-card"],div[class*="match-header"]')){
        const m=(el.innerText||'').match(/([A-Za-z0-9\-]+)\s+(\d{1,3})\s*[-/]\s*([0-9]|10)\b/);if(m){runs=Number(m[2]);wkts=Number(m[3]);break;}
    }}
    return {ball:ballNum,seq,circleVal,bowler,batsman,runs,wkts,isOverComplete,isInningsBreak,lastDismissal,overBalls:activeBalls};
}''';


async def extract_circle_balls_data(page: Page) -> Optional[BallSnapshot]:
    try:
        raw = await page.evaluate(EXTRACT_JS)
        if not raw: return None
        return BallSnapshot(
            ball=raw.get("ball"), circle_val=raw.get("circleVal"), bowler=raw.get("bowler") or "",
            batsman=raw.get("batsman") or "", runs=raw.get("runs"), wkts=raw.get("wkts"),
            is_over_complete=raw.get("isOverComplete", False), is_innings_break=raw.get("isInningsBreak", False),
            last_dismissal=raw.get("lastDismissal"), seq=raw.get("seq"), over_balls=raw.get("overBalls") or [],
        )
    except Exception as exc:
        if DEBUG: log.warning("CREX extract failed: %s", exc)
        return None


async def route_filter(route: Route) -> None:
    request = route.request
    if request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort(); return
    lowered = request.url.lower()
    if any(pattern in lowered for pattern in BLOCKED_URL_PATTERNS):
        await route.abort(); return
    await route.continue_()


def print_status(message: str) -> None:
    print(f"\r{message:<70}", end="", flush=True)


async def open_match_page(page: Page, url: str) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        await page.wait_for_timeout(1500)
    except Exception as exc:
        print("Page load notice:", exc)


def is_backwards(previous: Optional[str], current: Optional[str]) -> bool:
    try:
        po, pc = (int(value) for value in (previous or "").split(":"))
        co, cc = (int(value) for value in (current or "").split(":"))
    except ValueError:
        return False
    return co == po and cc < pc


async def announce_over_end(page: Page, announcer: RadioAnnouncer, snap: BallSnapshot, state: MatchState) -> None:
    await asyncio.sleep(OVER_SETTLE_SECONDS)
    fresh = await extract_circle_balls_data(page)
    runs = fresh.runs if fresh and fresh.runs is not None else snap.runs
    wickets = fresh.wkts if fresh and fresh.wkts is not None else snap.wkts
    over_number = int(snap.ball.split(".")[0]) + 1
    stats = over_stats_from_tokens(snap.over_balls)
    print(f"\n📊 Over {over_number} done | score {runs}/{wickets} | over stats {stats}")
    message = build_over_summary(over_number, runs, wickets, stats, state.match_overs)
    print("📢 OVER ANNOUNCEMENT:", message)
    await announcer.say(message, 2)


async def handle_new_ball(page: Page, announcer: RadioAnnouncer, snap: BallSnapshot, state: MatchState) -> None:
    assert snap.ball and snap.circle_val
    delivery = classify_delivery(snap.circle_val)
    kind = delivery["kind"]
    print("\n" + "=" * 65)
    print(f"🏏 BALL: {snap.ball} | BADGE: [{snap.circle_val}] | {snap.bowler} to {snap.batsman}")
    dismissal = snap.last_dismissal
    after_score = None
    if kind == "wicket":
        await asyncio.sleep(WICKET_CARD_WAIT_SECONDS)
        fresh = await extract_circle_balls_data(page)
        if fresh:
            if fresh.last_dismissal: dismissal = fresh.last_dismissal
            if fresh.runs is not None and fresh.wkts is not None and state.last_wkts is not None and fresh.wkts > state.last_wkts:
                after_score = (fresh.runs, fresh.wkts)
                state.last_wkts = fresh.wkts
    if dismissal: print("🎯 WICKET DETAILS:", dismissal)
    print("=" * 65)
    commentary, level = generate_circle_ball_commentary(snap.ball, snap.circle_val, snap.bowler, snap.batsman, dismissal)
    if after_score: commentary += " " + score_after_wicket_phrase(*after_score)
    print("🇮🇳 RADIO HINDI:", commentary)
    droppable = kind == "dot" or (kind == "runs" and delivery["runs"] in (1, 2, 3))
    await announcer.say(commentary, level, droppable=droppable)
    if snap.is_over_complete: await announce_over_end(page, announcer, snap, state)


async def monitor_loop(page: Page, announcer: RadioAnnouncer, state: MatchState) -> None:
    failures = 0
    while True:
        try:
            snap = await extract_circle_balls_data(page)
            if snap and snap.has_ball:
                key = snap.seq or snap.ball
                if DEBUG and key != state.last_key: print("\n[debug]", snap)
                if state.last_key is None and not ANNOUNCE_LAST_BALL_ON_START:
                    state.last_key = key
                    print(f"\n🔕 Synced at ball {snap.ball} [{snap.circle_val}] — commentary starts on next delivery")
                elif key != state.last_key:
                    if is_backwards(state.last_key, key):
                        log.info("Ignoring backwards page update %s -> %s", state.last_key, key)
                    else:
                        state.last_key = key
                        await handle_new_ball(page, announcer, snap, state)
                else:
                    print_status(f"⏳ Monitoring... Last ball: {snap.ball} [{snap.circle_val}]")
            else: print_status("⏳ Waiting for CREX circle strip to update...")
            if snap:
                if snap.is_innings_break and not state.innings_break_seen and state.last_key is not None:
                    state.innings_break_seen = True
                    message = build_innings_end(snap.runs, snap.wkts)
                    print("\n📢", message)
                    await announcer.say(message, 3)
                elif not snap.is_innings_break:
                    state.innings_break_seen = False
                if snap.wkts is not None: state.last_wkts = snap.wkts
            failures = 0
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            log.warning("Monitor error (%d consecutive): %s", failures, exc)
            if failures >= RELOAD_AFTER_FAILURES:
                try: await page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
                except Exception: pass
                failures = 0
            await asyncio.sleep(1)


async def run_live_stream(url: str) -> None:
    print("=" * 65 + "\n🏏 PREET SPORTS — SINGLE-FILE CREX RADIO ENGINE\n🎙 VOICE: Microsoft Madhur Neural\n" + "=" * 65)
    print("🎯 TARGET URL:", url)
    state = MatchState(match_overs=guess_match_overs(url))
    if state.match_overs: print(f"📋 Format detected: {state.match_overs}-over match")
    announcer = RadioAnnouncer()
    announcer.start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"
            ])
            try:
                context = await browser.new_context(viewport={"width":1280,"height":800}, user_agent=USER_AGENT)
                page = await context.new_page()
                await page.route("**/*", route_filter)
                print("🌐 Connecting to CREX...")
                await open_match_page(page, url)
                print("✅ Connected to CREX. Commentary active; press CTRL+C to exit.")
                await monitor_loop(page, announcer, state)
            finally: await browser.close()
    finally:
        await announcer.stop()


async def run_demo() -> None:
    print("🎧 DEMO MODE — sample commentary")
    announcer = RadioAnnouncer()
    announcer.start()
    samples = [
        ("3.2","1","Muditha Disanayaka","Aftab Alam Shah",None),
        ("3.3","4","Muditha Disanayaka","Aftab Alam Shah",None),
        ("3.4","6","Muditha Disanayaka","Aftab Alam Shah",None),
        ("3.5","W","Muditha Disanayaka","Aftab Alam Shah",{"batter":"Aftab Alam Shah","runs":"24","balls":"11","howOut":"c Ethan DSouza b Muditha Disanayaka"}),
        ("4.1","Wd","Vicky Ranjan","Aryansh Sharma",None),
        ("4.1","W","Vicky Ranjan","Aryansh Sharma",{"batter":"Aryansh Sharma","runs":"0","balls":"2","howOut":"b Vicky Ranjan"}),
        ("4.3","W","Vicky Ranjan","Carl Rai",{"batter":"Carl Rai","runs":"7","balls":"5","howOut":"lbw b Vicky Ranjan"}),
        ("4.5","W","Vicky Ranjan","Asif Taniwal",{"batter":"Asif Taniwal","runs":"15","balls":"9","howOut":"run out"}),
    ]
    for ball, badge, bowler, batter, dismissal in samples:
        line, level = generate_circle_ball_commentary(ball, badge, bowler, batter, dismissal)
        print(f"[{badge}] level {level}: {line}\n")
        await announcer.say(line, level)
    stats = over_stats_from_tokens(["1","4","0","W","6","1"])
    summary = build_over_summary(6,61,3,stats,10)
    print("OVER:", summary, "\n")
    await announcer.say(summary,2)
    await announcer.wait_idle()
    await announcer.stop()


def main() -> None:
    logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    configure_stdio()
    disable_quickedit()
    prevent_sleep_and_throttle()
    try:
        if DEMO:
            asyncio.run(run_demo())
            return
        urls = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
        url = urls[0] if urls else (input("Enter CREX Match URL (ENTER for default):\n> ").strip() or DEFAULT_URL)
        asyncio.run(run_live_stream(url))
    except KeyboardInterrupt:
        print("\n🛑 Script stopped by user.")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
