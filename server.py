"""Web Server & Real-Time Broadcast Hub for Cricket AI Broadcaster.

Provides modern Web UI, WebSocket live feed, and in-browser audio streaming.
Voice audio is synthesized and streamed straight to the browser in memory —
never written to disk.
"""
import os
import json
import html
import base64
import io
import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, Any, Set, Optional

from dotenv import load_dotenv

load_dotenv()

import aiohttp
from aiohttp import web

try:
    from playwright.async_api import async_playwright
except ImportError:  # REST/HTML feed remains a fallback if Chromium is unavailable.
    async_playwright = None

# Import existing core modules
from cricket_streamer import CrexParser, create_tts, get_live_matches, resolve_match_url
from human_commentator import commentator
from final import build_live_circle_announcement, build_live_over_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("WebServer")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR  # Flat project: index.html, app.js and style.css sit beside server.py.


CREX_CIRCLE_EXTRACT_JS = r'''() => {
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


def _normalize_rendered_circle(snapshot):
    """Normalize the live circle-strip snapshot emitted by final.py's Playwright extractor."""
    if not isinstance(snapshot, dict):
        return None
    over = str(snapshot.get("ball") or "").strip()
    token_value = snapshot.get("circleVal")
    token = "" if token_value is None else str(token_value).strip().upper()
    if not re.fullmatch(r"\d{1,3}\.\d{1,2}", over) or not token:
        return None
    bowler = str(snapshot.get("bowler") or "").strip()
    batter = str(snapshot.get("batsman") or "").strip()
    matchup = f"{bowler} to {batter}" if bowler and batter else ""
    return {
        "over": over,
        "circle_val": token,
        "runs": token,
        "matchup": matchup,
        "bowler": bowler,
        "batter": batter,
        "last_dismissal": snapshot.get("lastDismissal") or snapshot.get("last_dismissal") or {},
        "commentary": "",
        "spoken_line": "",
        "rendered_circle": True,
        "browser_seq": str(snapshot.get("seq") or ""),
    }


def _walk_nested_objects(value):
    """Walk dictionaries and lists from CREX app-root-state."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nested_objects(child)


def _parse_crex_state_json(raw):
    """Parse CREX's Angular app-root-state transport JSON safely."""
    if not raw:
        return None
    raw = raw.strip()
    replacements = {"q": '"', "a": "&", "s": "'", "l": "<", "g": ">"}
    candidates = [raw]
    candidates.append(re.sub(r"&(q|a|s|l|g);", lambda m: replacements[m.group(1)], raw))
    entities = {"quot": '"', "amp": "&", "apos": "'", "lt": "<", "gt": ">", "#34": '"', "#39": "'"}
    candidates.append(re.sub(r"&(quot|amp|apos|lt|gt|#34|#39);", lambda m: entities[m.group(1)], raw))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, (dict, list)):
                return value
        except Exception:
            continue
    return None


def _clean_crex_text(value):
    # Numeric zero is a real dot-ball token; never collapse it to an empty string.
    text = html.unescape("" if value is None else str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_crex_state_balls(html_text):
    """Extract actual source-commentary deliveries from CREX app-root-state."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text or "", "lxml")
        state_tag = soup.find("script", id="app-root-state")
        scripts = [state_tag] if state_tag else []
        scripts.extend(tag for tag in soup.find_all("script") if tag is not state_tag)

        states = []
        for tag in scripts:
            if tag is None:
                continue
            raw = tag.string or tag.get_text("", strip=False)
            if not raw or len(raw) > 5_000_000:
                continue
            lower = raw.lower()
            if tag is not state_tag and not any(
                marker in lower for marker in ("getballfeeds", "ballfeeds", "app-root-state")
            ):
                continue
            value = _parse_crex_state_json(raw)
            if value is not None:
                states.append(value)
                if tag is state_tag:
                    break
        if not states:
            return []

        state = states[0]
        feed_values = []
        for node in _walk_nested_objects(state):
            for key, value in node.items():
                if "getballfeeds" in str(key).lower() or "ballfeeds" in str(key).lower():
                    feed_values.append(value)

        def item_lists(value):
            if isinstance(value, list):
                # Feed entries have the distinctive source fields o/b/c2.
                if any(isinstance(item, dict) and
                       ("o" in item or "over" in item) and
                       ("b" in item or "runs" in item or "ball" in item or
                        "c2" in item or "commentary" in item or "description" in item)
                       for item in value):
                    yield value
                for child in value:
                    yield from item_lists(child)
            elif isinstance(value, dict):
                for child in value.values():
                    yield from item_lists(child)

        if not feed_values:
            # Fallback when a bundle changes the endpoint's exact key but the objects
            # remain embedded inside app-root-state.
            feed_values = [state]

        output = []
        seen = set()
        for feed in feed_values:
            for records in item_lists(feed):
                for item in records:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type", "b")).lower() not in ("b", "ball", "delivery"):
                        continue
                    over = _clean_crex_text(item.get("o", item.get("over", "")))
                    run_token = _clean_crex_text(item.get("b", item.get("runs", ""))).upper()
                    matchup = _clean_crex_text(item.get("c1", item.get("matchup", "")))
                    commentary = _clean_crex_text(
                        item.get("c2", item.get("commentary", item.get("description", item.get("c", ""))))
                    )
                    # The delivery-circle value alone is sufficient for radio speech.
                    if not commentary and not run_token:
                        continue
                    if not over:
                        on = item.get("on")
                        if isinstance(on, int) and on >= 0:
                            over = f"{on // 6}.{on % 6 + 1}"
                    if not over:
                        continue
                    identity = (over, run_token, matchup, commentary)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    spoken = f"Over {over}: {matchup}. {commentary}" if matchup else f"Over {over}: {commentary}"
                    output.append({
                        "over": over,
                        "matchup": matchup,
                        "circle_val": run_token,
                        "runs": run_token,
                        "commentary": commentary,
                        "spoken_line": spoken,
                    })
        if output:
            logger.info("CREX app-root-state extracted %d delivery commentary items; latest=%s",
                        len(output), output[-1].get("over"))
        return output
    except Exception:
        logger.exception("Could not parse CREX app-root-state commentary.")
        return []



# ---------------- Current-ball-only speech helpers ----------------
def _normal_space(value: Any) -> str:
    return " ".join(str(value or "").split())


def _ball_over_index(ball: Dict[str, Any]) -> Optional[int]:
    """Read a CREX delivery label such as 33.6 and return its zero-based over."""
    label = _normal_space(ball.get("over"))
    match = re.search(r"(\d+)(?:\.(\d+))?", label)
    return int(match.group(1)) if match else None


def _ball_order(ball: Dict[str, Any], original_index: int = 0) -> tuple[int, int]:
    """Numeric order for over.ball labels; ties prefer earlier DOM entries (usually newest)."""
    label = _normal_space(ball.get("over"))
    match = re.search(r"(\d+)(?:\.(\d+))?", label)
    if not match:
        return (-1, -original_index)
    over = int(match.group(1))
    delivery = int(match.group(2) or 0)
    return (over * 6 + delivery, -original_index)


def _latest_source_ball(balls: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick one newest source delivery, never turn a page of historic balls into a voice queue."""
    if not balls:
        return None
    return max(enumerate(balls), key=lambda pair: _ball_order(pair[1], pair[0]))[1]


def _ball_identity(ball: Dict[str, Any]) -> str:
    """Stable delivery key; rendered sequence distinguishes repeated wides/dots at the same label."""
    over = _normal_space(ball.get("over")).lower()
    matchup = _normal_space(ball.get("matchup")).lower()
    runs = _normal_space((ball.get("circle_val") or ball.get("runs"))).upper()
    rendered_seq = _normal_space(ball.get("browser_seq"))
    if ball.get("rendered_circle") and rendered_seq:
        return f"{over}|seq:{rendered_seq}|{matchup}|{runs}"
    if not over or over.lower() == "live":
        text = _normal_space(ball.get("commentary"))[:100].lower()
        return f"{over}|{matchup}|{runs}|{text}"
    return f"{over}|{matchup}|{runs}"

def _ball_slot_key(ball: Dict[str, Any]) -> str:
    """Stable source-merge slot for one over.ball position."""
    over = _normal_space(ball.get("over")).lower()
    match = re.fullmatch(r"(\d{1,3})\.(\d{1,2})", over)
    if match:
        return f"{int(match.group(1))}.{int(match.group(2))}"
    return _ball_identity(ball)


def _merge_ball_sources(*sources: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Merge DOM, embedded state and API feeds; later sources refresh the same circle slot."""
    merged: Dict[str, Dict[str, Any]] = {}
    for source in sources:
        for raw_ball in source or []:
            if not isinstance(raw_ball, dict):
                continue
            ball = dict(raw_ball)
            slot = _ball_slot_key(ball)
            existing = dict(merged.get(slot, {}))
            # Preserve useful commentary/matchup when an API only has the circle value.
            for key, value in ball.items():
                if value is not None and value != "":
                    existing[key] = value
            token = _normal_space((ball.get("circle_val") or ball.get("runs", ""))).upper()
            if token:
                existing["circle_val"] = token
                existing["runs"] = token
            merged[slot] = existing

    return sorted(
        merged.values(),
        key=lambda item: _ball_order(item),
    )


def _ball_identity(ball: Dict[str, Any]) -> str:
    """Stable delivery identity that doesn't change if CREX refreshes the commentary wording."""
    over = _normal_space(ball.get("over")).lower()
    matchup = _normal_space(ball.get("matchup")).lower()
    runs = _normal_space((ball.get("circle_val") or ball.get("runs"))).upper()
    if not over or over.lower() == "live":
        text = _normal_space(ball.get("commentary"))[:100].lower()
        return f"{over}|{matchup}|{runs}|{text}"
    return f"{over}|{matchup}|{runs}"


def _delivery_stats(token: Any) -> tuple[int, bool, int, int, int]:
    """Return (runs, legal_ball, fours, sixes, wickets) from the source's delivery badge."""
    raw = _normal_space(token).lower().replace(" ", "")
    nums = re.findall(r"\d+", raw)
    n = int(nums[0]) if nums else 1
    if "wd" in raw or "wide" in raw:
        return (max(1, n), False, 0, 0, 0)
    if "nb" in raw or "noball" in raw:
        return (max(1, n), False, 0, 0, 0)
    if raw in {"w", "wkt", "out"} or "wicket" in raw:
        return (0, True, 0, 0, 1)
    if "lb" in raw or raw.endswith("b"):
        return (max(1, n), True, 0, 0, 0)
    if raw.isdigit():
        runs = int(raw)
        return (runs, True, int(runs == 4), int(runs == 6), 0)
    return (0, True, 0, 0, 0)


def _over_announcement(over_index: int, balls: list[Dict[str, Any]], lang: str, score_state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Use the uploaded Pro Commentary over recap once per completed over."""
    rows = [b for b in balls if _ball_over_index(b) == over_index]
    if not rows:
        return None
    return build_live_over_summary(over_index, rows, score_state=score_state, lang=lang)


def _audio_duration_seconds(mp3_data: bytes) -> float:
    """Decode the MP3 length so commentary state covers the complete spoken clip."""
    if not mp3_data:
        return 0.0
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(mp3_data), format="mp3")
        return max(0.6, len(audio) / 1000.0)
    except Exception as exc:
        logger.debug("MP3 duration decode failed; using conservative byte estimate: %s", exc)
        return max(1.2, len(mp3_data) * 8.0 / 48000.0)

def _source_badge(ball: Dict[str, Any]) -> str:
    token = _normal_space((ball.get("circle_val") or ball.get("runs"))).upper().replace(" ", "")
    if token in {"W", "WKT", "OUT"} or "WICKET" in token:
        return "WICKET"
    if token == "6":
        return "SIX"
    if token == "4":
        return "FOUR"
    if "WD" in token or "WIDE" in token:
        return "WIDE"
    if "NB" in token or "NOBALL" in token:
        return "NO BALL"
    return "BALL"


class BroadcastHub:
    def __init__(self):
        self.active_websockets: Set[web.WebSocketResponse] = set()
        self.is_running = False
        self.broadcast_task: Optional[asyncio.Task] = None
        self.youtube_engine = None
        self.youtube_task: Optional[asyncio.Task] = None
        self.latest_match_state: Dict[str, Any] = {}
        self.latest_commentary = ""
        self.latest_feed_debug: Dict[str, Any] = {"status": "not_started"}
        self.latest_tts_debug: Dict[str, Any] = {"status": "not_started"}
        self.latest_start_debug: Dict[str, Any] = {"status": "not_started"}

    async def broadcast_ws(self, msg_type: str, data: Any):
        """Pushes real-time JSON payload to all connected browser clients."""
        payload = json.dumps({"type": msg_type, "data": data})
        stale = []
        for ws in self.active_websockets:
            try:
                await ws.send_str(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.active_websockets.discard(ws)

    async def start_broadcast(self, url: str, lang: str = "hi", stream_key: str = "",
                               tts_provider: str = "edge", tts_api_key: str = "", tts_speaker: str = ""):
        if self.is_running:
            return {"ok": False, "error": "Broadcast is already running"}

        url = url.strip()
        if not url:
            return {"ok": False, "error": "Match link or search query is required."}

        # Log start attempts so /api/status explains why the pipeline is idle.
        self.latest_start_debug = {
            "status": "resolving_match",
            "url": url[:320],
            "language": lang,
            "voice_engine": tts_provider,
        }
        try:
            # Smart Match Resolution: accepts a CREX URL or a supported match query.
            resolved_url, resolve_note = resolve_match_url(url)
            target_url = resolved_url if resolved_url else url
            self.latest_start_debug.update({"status": "fetching_match_page", "resolved_url": target_url[:320]})
        except Exception as exc:
            self.latest_start_debug = {
                **self.latest_start_debug,
                "status": "error",
                "phase": "resolve_match_url",
                "error": str(exc)[:500],
            }
            logger.exception("Broadcast start failed during match resolution")
            return {"ok": False, "error": f"Could not resolve match URL: {exc}"}
        source_lang = "hi" if lang in ("hi", "hinglish") else "en"

        # Pre-flight check: request CREX's actual source-language commentary.
        parser = CrexParser(target_url, lang=source_lang)
        try:
            html_text = await parser.fetch_html_async()
            if not html_text:
                self.latest_start_debug = {
                    **self.latest_start_debug,
                    "status": "error",
                    "phase": "fetch_match_page",
                    "error": "Unable to fetch content from match URL.",
                }
                return {"ok": False, "error": "Unable to fetch content from match URL."}
            init_data = parser.parse(html_text)
            self.latest_match_state = init_data
        except ValueError as ve:
            # If direct target fails or returned 404, attempt fuzzy match fallback using active fixtures
            fallback_url, fallback_note = resolve_match_url(url, force_fuzzy=True)
            if fallback_note and fallback_url != target_url:
                logger.info(f"Direct link returned 404 ({ve}), auto-resolved to live match: {fallback_url}")
                target_url = fallback_url
                resolve_note = fallback_note
                try:
                    parser = CrexParser(target_url, lang=source_lang)
                    html_text = await parser.fetch_html_async()
                    init_data = parser.parse(html_text)
                    self.latest_match_state = init_data
                except Exception as fe:
                    return {"ok": False, "error": f"Match link failed: {fe}"}
            else:
                self.latest_start_debug = {
                    **self.latest_start_debug,
                    "status": "error",
                    "phase": "preflight_fetch",
                    "error": str(ve)[:500],
                }
                logger.warning("Broadcast start preflight rejected match URL: %s", ve)
                return {"ok": False, "error": str(ve)}
        except Exception as e:
            logger.warning(f"Pre-flight connection error: {e}", exc_info=True)
            self.latest_start_debug = {
                **self.latest_start_debug,
                "status": "error",
                "phase": "preflight",
                "error": str(e)[:500],
            }
            return {"ok": False, "error": f"Failed to connect to match URL: {e}"}

        self.is_running = True
        self.latest_start_debug = {
            **self.latest_start_debug,
            "status": "running",
            "phase": "pipeline_scheduled",
            "resolved_url": target_url[:320],
        }
        self.broadcast_task = asyncio.create_task(self._run_broadcast_pipeline(
            target_url, lang, stream_key, parser=parser,
            tts_provider=tts_provider, tts_api_key=tts_api_key, tts_speaker=tts_speaker
        ))

        # Launch YouTube Live RTMP stream if stream_key is provided
        if stream_key.strip():
            try:
                from cricket_streamer import CricketStreamingEngine
                self.youtube_engine = CricketStreamingEngine(
                    url=target_url,
                    mode="youtube",
                    stream_key=stream_key.strip(),
                    language=lang,
                    tts_provider=tts_provider,
                    tts_api_key=tts_api_key or None,
                    tts_speaker=tts_speaker or None
                )
                self.youtube_task = asyncio.create_task(self.youtube_engine.run())
                logger.info(f"YouTube Live RTMP broadcast engine launched for stream key: {stream_key[:4]}****")
            except Exception as ye:
                logger.error(f"Failed to start YouTube streaming engine: {ye}")

        await self.broadcast_ws("status", {
            "is_running": True,
            "has_youtube": bool(stream_key.strip()),
            "target": "YouTube Live & Web" if stream_key.strip() else "Web Browser"
        })
        if resolve_note:
            await self.broadcast_ws("match_resolved", {
                "original": url,
                "resolved_url": target_url,
                "note": resolve_note
            })
        if self.latest_match_state:
            await self.broadcast_ws("match_state", self.latest_match_state)
        return {
            "ok": True,
            "resolved_url": target_url,
            "note": resolve_note,
            "youtube_streaming": bool(stream_key.strip())
        }

    async def stop_broadcast(self):
        if not self.is_running:
            return {"ok": False, "error": "Broadcast is not running"}

        self.is_running = False
        self.latest_start_debug = {
            **self.latest_start_debug,
            "status": "stopped",
            "phase": "user_stop",
        }

        # Stop YouTube Live engine if active
        if self.youtube_engine:
            self.youtube_engine.is_running = False
        if self.youtube_task and not self.youtube_task.done():
            self.youtube_task.cancel()
            try:
                await self.youtube_task
            except asyncio.CancelledError:
                pass
        self.youtube_task = None
        self.youtube_engine = None

        # Stop Web broadcast pipeline
        if self.broadcast_task and not self.broadcast_task.done():
            self.broadcast_task.cancel()
            try:
                await self.broadcast_task
            except asyncio.CancelledError:
                pass
        self.broadcast_task = None
        await self.broadcast_ws("status", {"is_running": False})
        return {"ok": True}

    async def _run_broadcast_pipeline(self, url: str, lang: str, stream_key: str, parser: Optional[CrexParser] = None,
                                       tts_provider: str = "edge", tts_api_key: str = "", tts_speaker: str = ""):
        logger.info(
            "Starting current-ball-only broadcast for URL: %s (Lang: %s, Voice: %s)",
            url, lang.upper(), tts_provider
        )
        if parser is None:
            parser = CrexParser(url, lang=lang)
        tts = create_tts(language=lang, provider=tts_provider,
                         api_key=tts_api_key or None, speaker=tts_speaker or None)

        # At most one waiting event. New source deliveries replace any not-yet-played event.
        ball_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        seen_feed_keys: set[str] = set()
        last_latest_key: Optional[str] = None
        last_latest_ball: Optional[Dict[str, Any]] = None
        last_latest_state: Dict[str, Any] = {}
        latest_event_version = 0
        pending_over_announcements: Dict[str, str] = {}
        pending_over_positions: Dict[str, str] = {}
        pending_over_indices: Dict[str, int] = {}
        announced_over_ids: set[str] = set()
        claimed_over_ids: set[str] = set()
        # Snapshot from final.py's actual rendered CREX circle strip, polled separately.
        rendered_circle_state: Dict[str, Any] = {"status": "starting", "ball": None, "updated_at": 0.0}
        # Only summarize overs whose first delivery was observed after this broadcast started.
        tracked_full_overs: set[int] = set()

        async def queue_latest(event: Dict[str, Any]) -> None:
            """Keep one waiting delivery while carrying all unspoken over recaps forward."""
            ball = event.get("ball") or {}
            next_over = _ball_over_index(ball)
            incoming = list(event.get("over_announcements", []))
            incoming_ids = {item.get("id") for item in incoming if item.get("id")}
            for recap_id, recap_text in pending_over_announcements.items():
                if recap_id in announced_over_ids or recap_id in incoming_ids:
                    continue
                recap_over = pending_over_indices.get(recap_id)
                position = pending_over_positions.get(recap_id, "after_ball")
                if next_over is not None and recap_over is not None and next_over > recap_over:
                    position = "before_ball"
                    pending_over_positions[recap_id] = position
                incoming.append({"id": recap_id, "text": recap_text, "position": position, "over_index": recap_over})
                incoming_ids.add(recap_id)
                claimed_over_ids.add(recap_id)
            if ball_queue.full():
                try:
                    dropped = ball_queue.get_nowait()
                    for recap in dropped.get("over_announcements", []):
                        recap_id = recap.get("id")
                        if not recap_id or recap_id in announced_over_ids or recap_id in incoming_ids:
                            continue
                        recap_text = recap.get("text") or pending_over_announcements.get(recap_id, "")
                        if not recap_text:
                            continue
                        recap_over = pending_over_indices.get(recap_id, recap.get("over_index"))
                        position = pending_over_positions.get(recap_id, recap.get("position", "after_ball"))
                        if next_over is not None and recap_over is not None and next_over > recap_over:
                            position = "before_ball"
                            pending_over_positions[recap_id] = position
                        incoming.append({"id": recap_id, "text": recap_text, "position": position, "over_index": recap_over})
                        incoming_ids.add(recap_id)
                    logger.info("Discarding waiting ball %s; prioritizing %s",
                                _normal_space(dropped.get("ball", {}).get("over")),
                                _normal_space(ball.get("over")))
                except asyncio.QueueEmpty:
                    pass
            event["over_announcements"] = incoming
            ball_queue.put_nowait(event)
        async def rendered_circle_watcher() -> None:
            """Read the live, rendered CREX circle strip using the same JS as final.py."""
            if async_playwright is None:
                rendered_circle_state.update({
                    "status": "playwright_unavailable",
                    "error": "Install Playwright and Chromium in the Render image.",
                })
                logger.warning("Rendered-circle watcher unavailable; using HTML/API fallback")
                return

            try:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(
                        headless=True,
                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                    )
                    try:
                        context = await browser.new_context(
                            viewport={"width": 1280, "height": 800},
                            user_agent=(
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                        )
                        page = await context.new_page()
                        page.set_default_timeout(3500)

                        async def light_route(route):
                            if route.request.resource_type in {"image", "media", "font"}:
                                await route.abort()
                            else:
                                await route.continue_()

                        await page.route("**/*", light_route)
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(500)
                        loop = asyncio.get_running_loop()
                        last_key = None
                        rendered_circle_state["status"] = "watching"

                        while self.is_running:
                            try:
                                snapshot = await page.evaluate(CREX_CIRCLE_EXTRACT_JS)
                                candidate = _normalize_rendered_circle(snapshot)
                                if candidate:
                                    key = (
                                        candidate["over"],
                                        candidate["circle_val"],
                                        candidate["browser_seq"],
                                        candidate["matchup"],
                                    )
                                    if key != last_key:
                                        logger.info(
                                            "Rendered CREX circle updated: %s [%s] %s",
                                            candidate["over"], candidate["circle_val"],
                                            candidate["matchup"],
                                        )
                                        last_key = key
                                    rendered_circle_state.update({
                                        "status": "live",
                                        "ball": candidate,
                                        "updated_at": loop.time(),
                                        "seq": candidate["browser_seq"],
                                        "error": "",
                                    })
                                await asyncio.sleep(0.35)
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                rendered_circle_state.update({
                                    "status": "poll_error",
                                    "error": str(exc)[:160],
                                })
                                await asyncio.sleep(0.5)
                    finally:
                        await browser.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                rendered_circle_state.update({
                    "status": "error",
                    "error": str(exc)[:240],
                })
                logger.warning("Rendered CREX circle watcher failed; HTML/API fallback remains active: %s", exc)

        async def broadcast_audio_bytes(text: str, badge: str, mp3_data: bytes,
                                        kind: str) -> Optional[float]:
            """Broadcast already-generated MP3 without delaying a newer circle."""
            if not mp3_data:
                logger.warning("TTS returned empty audio for event type %s", kind)
                return None
            duration = _audio_duration_seconds(mp3_data)
            audio_url = "data:audio/mpeg;base64," + base64.b64encode(mp3_data).decode("ascii")
            self.latest_commentary = text
            await self.broadcast_ws("commentary", {
                "text": text,
                "badge": badge,
                "audio_url": audio_url,
                "duration": round(duration, 1),
                "kind": kind,
            })
            logger.info("Broadcast in-memory audio (%s, approx %.1fs): %s", kind, duration, text)
            return duration

        async def broadcast_audio(text: str, badge: str, rate: Optional[str],
                                  pitch: Optional[str], kind: str) -> Optional[float]:
            """Short timeout wrapper for rare non-ball announcements."""
            try:
                mp3_data = await asyncio.wait_for(
                    tts.speak_mp3(text, rate=rate, pitch=pitch),
                    timeout=4.5,
                )
            except asyncio.TimeoutError:
                logger.warning("TTS timed out for event type %s", kind)
                return None
            return await broadcast_audio_bytes(text, badge, mp3_data, kind)

        async def speak_over_summary(summary: Dict[str, Any]) -> Optional[float]:
            """Speak a mandatory over recap exactly once, retaining it when TTS fails."""
            recap_id = str(summary.get("id") or "")
            recap_text = _normal_space(summary.get("text") or "")
            if not recap_id or not recap_text or recap_id in announced_over_ids:
                return None
            try:
                mp3_data = await asyncio.wait_for(
                    tts.speak_mp3(recap_text, rate="+5%", pitch="+0Hz"),
                    timeout=4.5,
                )
            except asyncio.TimeoutError:
                logger.warning("Over recap %s TTS timed out; retaining it for retry", recap_id)
                claimed_over_ids.discard(recap_id)
                return None
            if not mp3_data:
                logger.warning("Over recap %s returned empty audio; retaining it for retry", recap_id)
                claimed_over_ids.discard(recap_id)
                return None
            duration = await broadcast_audio_bytes(recap_text, "OVER COMPLETE", mp3_data, "over_summary")
            if duration is None:
                claimed_over_ids.discard(recap_id)
                return None
            announced_over_ids.add(recap_id)
            pending_over_announcements.pop(recap_id, None)
            pending_over_positions.pop(recap_id, None)
            pending_over_indices.pop(recap_id, None)
            claimed_over_ids.discard(recap_id)
            await asyncio.sleep(duration + 0.12)
            return duration
        async def voice_worker():
            """Speak pending over summaries and the single newest delivery, never a ball backlog."""
            event: Optional[Dict[str, Any]] = None
            while self.is_running:
                if event is None:
                    try:
                        event = await asyncio.wait_for(ball_queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

                current_event, event = event, None
                try:
                    # Keep over recaps durable until successfully broadcast.
                    summary_map = {
                        str(item.get("id")): dict(item)
                        for item in current_event.get("over_announcements", []) if item.get("id")
                    }
                    current_ball_index = _ball_over_index(current_event.get("ball", {}))
                    for recap_id, recap_text in pending_over_announcements.items():
                        if recap_id in announced_over_ids:
                            continue
                        recap_over = pending_over_indices.get(recap_id)
                        position = pending_over_positions.get(recap_id, "after_ball")
                        if current_ball_index is not None and recap_over is not None and current_ball_index > recap_over:
                            position = "before_ball"
                            pending_over_positions[recap_id] = position
                        summary_map.setdefault(recap_id, {"id": recap_id, "text": recap_text, "position": position, "over_index": recap_over})
                    summaries_before_ball = [item for item in summary_map.values() if item.get("position") == "before_ball"]
                    summaries_after_ball = [item for item in summary_map.values() if item.get("position") != "before_ball"]
                    # Over-end call must be heard before any ball from the next over.
                    for summary in summaries_before_ball:
                        await speak_over_summary(summary)

                    # New source delivery arrived while a TTS call was running.
                    # Drop this old ball; the next queue item carries the newest one.
                    if current_event["version"] != latest_event_version:
                        # A stale waiting delivery must not consume its recap.
                        for summary in summaries_after_ball:
                            recap_id = summary.get("id")
                            if recap_id and recap_id not in announced_over_ids:
                                claimed_over_ids.discard(recap_id)
                        logger.info(
                            "Dropping stale delivery %s and marking its attached over recaps handled",
                            current_event.get("ball", {}).get("over"),
                        )
                        continue

                    ball = current_event["ball"]
                    ball_over = _normal_space(ball.get("over")) or "Live"
                    source_text = _normal_space(ball.get("commentary"))
                    circle_token = _normal_space((ball.get("circle_val") or ball.get("runs", ""))).upper()
                    if not source_text and not circle_token:
                        logger.info("Skipping delivery %s because CREX has no circle value or commentary text", ball_over)
                        continue

                    # The producer already built this call directly from the CREX circle
                    # token. Never synthesize or fall back to the scraped commentary paragraph.
                    radio_text = _normal_space(current_event.get("radio_text") or f"Live ball {ball_over}.")
                    badge = current_event.get("badge") or _source_badge(ball)
                    rate = current_event.get("rate") or "+15%"
                    pitch = current_event.get("pitch") or "+10Hz"

                    self.latest_tts_debug = {
                        "status": "generating",
                        "provider": tts_provider,
                        "voice": getattr(tts, "voice", None) or getattr(tts, "speaker", None),
                        "language": lang,
                        "event_id": current_event.get("event_id") or f"{ball_over}|{circle_token}",
                        "over": ball_over,
                        "source_text_length": len(source_text),
                        "radio_text_length": len(radio_text),
                        "text_length": len(radio_text),
                        "rate": rate,
                        "pitch": pitch,
                        "radio_text_preview": radio_text[:180],
                        "circle_value": circle_token,
                        "active_striker": current_event["fresh_data"].get("striker"),
                        "active_striker_score": current_event["fresh_data"].get("striker_runs"),
                        "active_striker_balls": current_event["fresh_data"].get("striker_balls"),
                        "active_non_striker": current_event["fresh_data"].get("non_striker"),
                        "active_non_striker_score": current_event["fresh_data"].get("non_striker_runs"),
                        "active_non_striker_balls": current_event["fresh_data"].get("non_striker_balls"),
                    }
                    # Race TTS synthesis against the next delivery. If the match updates
                    # first, cancel old synthesis immediately instead of making the newest ball
                    # wait for a stale sentence to finish generating.
                    tts_task = asyncio.create_task(
                        tts.speak_mp3(radio_text, rate=rate, pitch=pitch)
                    )
                    next_delivery_task = asyncio.create_task(ball_queue.get())
                    done, _ = await asyncio.wait(
                        {tts_task, next_delivery_task},
                        timeout=4.5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if next_delivery_task in done:
                        event = next_delivery_task.result()

                    if current_event["version"] != latest_event_version:
                        if not tts_task.done():
                            tts_task.cancel()
                        if not next_delivery_task.done():
                            next_delivery_task.cancel()
                        await asyncio.gather(tts_task, next_delivery_task, return_exceptions=True)
                        # Retain recaps in the pending registry; the newest ball will inherit them.
                        for recap in summaries_after_ball:
                            recap_id = recap.get("id")
                            if recap_id and recap_id not in announced_over_ids:
                                claimed_over_ids.discard(recap_id)
                        # Do not overwrite the newer ball's queue/debug state with
                        # diagnostics from a cancelled stale synthesis.
                        if (
                            self.latest_tts_debug.get("over") == ball_over
                            and self.latest_tts_debug.get("circle_value") == circle_token
                        ):
                            self.latest_tts_debug.update({
                                "status": "dropped_stale",
                                "over": ball_over,
                                "note": "A newer delivery arrived; stale synthesis was cancelled.",
                            })
                        continue

                    if tts_task not in done:
                        if not tts_task.done():
                            tts_task.cancel()
                        if not next_delivery_task.done():
                            next_delivery_task.cancel()
                        await asyncio.gather(tts_task, next_delivery_task, return_exceptions=True)
                        event_id = current_event.get("event_id") or f"{ball_over}|{circle_token}"
                        self.latest_tts_debug.update({
                            "status": "timeout",
                            "error": "Edge TTS exceeded the 4.5-second low-latency limit; browser fallback is available",
                            "event_id": event_id,
                            "over": ball_over,
                            "circle_value": circle_token,
                        })
                        logger.warning("TTS timed out for ball %s; browser fallback may already have spoken it", ball_over)
                        try:
                            await self.broadcast_ws("tts_error", {
                                "event_id": event_id,
                                "over": ball_over,
                                "circle_value": circle_token,
                                "message": "Edge Madhur did not return audio within 4.5 seconds",
                            })
                        except Exception:
                            pass
                        continue

                    if next_delivery_task not in done:
                        next_delivery_task.cancel()
                        await asyncio.gather(next_delivery_task, return_exceptions=True)

                    mp3_data = tts_task.result()
                    if not mp3_data:
                        self.latest_tts_debug["status"] = "empty_audio"
                        logger.warning("TTS returned empty audio for ball %s", ball_over)
                        continue
                    if current_event["version"] != latest_event_version:
                        if (
                            self.latest_tts_debug.get("over") == ball_over
                            and self.latest_tts_debug.get("circle_value") == circle_token
                        ):
                            self.latest_tts_debug.update({
                                "status": "dropped_stale",
                                "over": ball_over,
                                "note": "A newer delivery arrived during synthesis; stale audio was discarded.",
                            })
                        logger.info("TTS completed for old ball %s; discarding audio before broadcast", ball_over)
                        continue

                    duration = _audio_duration_seconds(mp3_data)
                    audio_url = "data:audio/mpeg;base64," + base64.b64encode(mp3_data).decode("ascii")
                    self.latest_commentary = radio_text
                    await self.broadcast_ws("commentary", {
                        "event_id": current_event.get("event_id") or f"{ball_over}|{circle_token}",
                        "version": current_event.get("version", 0),
                        "text": radio_text,
                        "badge": badge,
                        "over": ball_over,
                        "circle_value": circle_token,
                        "audio_url": audio_url,
                        "duration": round(duration, 1),
                        "kind": "ball",
                    })
                    self.latest_tts_debug.update({
                        "status": "audio_broadcast",
                        "audio_bytes": len(mp3_data),
                        "over": ball_over,
                        "circle_value": circle_token,
                        "radio_text_preview": radio_text[:180],
                    })
                    logger.info("On-air current ball %s: %s", ball_over, radio_text)

                    # Wait for either the approximate clip duration or a new delivery. If a new
                    # delivery arrives first, immediately prioritize it; the browser replaces
                    # the currently playing clip rather than building an audio backlog.
                    try:
                        event = await asyncio.wait_for(ball_queue.get(), timeout=duration)
                    except asyncio.TimeoutError:
                        event = None

                    # Recaps after a completed final ball are mandatory. If a new ball arrives
                    # first, its event inherits the still-pending recap and worker ordering handles it.
                    if event is None and summaries_after_ball:
                        for summary in summaries_after_ball:
                            if summary.get("id") in announced_over_ids:
                                continue
                            await speak_over_summary(summary)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    is_current_failure = (
                        isinstance(locals().get("current_event"), dict)
                        and current_event.get("version") == latest_event_version
                    )
                    if is_current_failure:
                        self.latest_tts_debug = {
                            **self.latest_tts_debug,
                            "status": "error",
                            "error": str(exc)[:240],
                        }
                        try:
                            await self.broadcast_ws("tts_error", {
                                "message": str(exc)[:180],
                                "over": self.latest_tts_debug.get("over"),
                                "circle_value": self.latest_tts_debug.get("circle_value"),
                            })
                        except Exception:
                            pass
                    else:
                        logger.warning("Ignoring TTS error from stale ball: %s", str(exc)[:180])
                    logger.exception("Voice worker error")
                    event = None

        voice_task = asyncio.create_task(voice_worker(), name="current_ball_voice_worker")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=60)
        consecutive_errors = 0
        api_feed_task: Optional[asyncio.Task] = None
        api_feed_cache: list[Dict[str, Any]] = []
        api_feed_last_error = ""
        rendered_circle_task: Optional[asyncio.Task] = asyncio.create_task(
            rendered_circle_watcher(), name="rendered_crex_circle_watcher"
        )

        try:
            logger.info("Starting CREX poll loop (0.35-second interval); speech backlog is disabled")
            async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
                while self.is_running:
                    try:
                        # Harvest the background feed before HTML parsing. This also
                        # works if one HTML request returns empty or times out.
                        if api_feed_task is not None and api_feed_task.done():
                            try:
                                result = api_feed_task.result()
                                if result:
                                    api_feed_cache = list(result)
                                api_feed_last_error = ""
                            except asyncio.CancelledError:
                                raise
                            except Exception as api_exc:
                                api_feed_last_error = str(api_exc)[:180]
                                logger.debug("Background CREX circle refresh failed: %s", api_feed_last_error)
                            api_feed_task = None

                        # Never await this API call in the scoreboard loop.
                        if api_feed_task is None:
                            api_feed_task = asyncio.create_task(
                                parser.fetch_ball_feeds_async(session=session),
                                name="crex_live_circle_feed",
                            )

                        # Bound slow HTML responses so the independent rendered-circle
                        # watcher can still drive the latest delivery into the voice queue.
                        try:
                            html_text = await asyncio.wait_for(
                                parser.fetch_html_async(session=session), timeout=1.0
                            )
                        except asyncio.TimeoutError:
                            html_text = ""

                        if html_text:
                            fresh_data = parser.parse(html_text)
                            dom_balls = list(fresh_data.get("balls") or [])
                            state_balls = _extract_crex_state_balls(html_text)
                        else:
                            fresh_data = dict(last_latest_state or self.latest_match_state or {})
                            dom_balls = []
                            state_balls = []

                        loop = asyncio.get_running_loop()
                        rendered_ball = rendered_circle_state.get("ball")
                        rendered_age = (
                            max(0.0, loop.time() - float(rendered_circle_state.get("updated_at", 0.0)))
                            if rendered_ball else 999.0
                        )
                        rendered_fresh = bool(rendered_ball and rendered_age <= 2.0)

                        # The real rendered circle wins ties and outranks stale historic
                        # feed entries. HTML/API remain fallback sources when Chromium is late.
                        rendered_source = [rendered_ball] if rendered_fresh else []
                        raw_balls = _merge_ball_sources(
                            api_feed_cache, state_balls, dom_balls, rendered_source
                        )
                        if not fresh_data and not raw_balls:
                            await asyncio.sleep(0.35)
                            continue
                        if raw_balls:
                            fresh_data["balls"] = raw_balls
                            fresh_data["paragraphs"] = [
                                b.get("spoken_line", "") for b in raw_balls
                            ]
                        else:
                            logger.warning(
                                "No CREX circle feed yet. URL=%s; scoreboard=%s/%s at %s overs; api_error=%s",
                                url, fresh_data.get("total_runs"), fresh_data.get("total_wickets"),
                                fresh_data.get("overs"), api_feed_last_error,
                            )

                        feed_debug = dict(getattr(parser, "_feed_debug", {}) or {})
                        feed_debug["parser_delivery_count"] = len(raw_balls)
                        feed_debug["api_cache_delivery_count"] = len(api_feed_cache)
                        feed_debug["dom_delivery_count"] = len(dom_balls)
                        feed_debug["embedded_state_delivery_count"] = len(state_balls)
                        feed_debug["api_refresh_in_flight"] = bool(api_feed_task and not api_feed_task.done())
                        feed_debug["rendered_circle_status"] = rendered_circle_state.get("status")
                        feed_debug["rendered_circle_error"] = rendered_circle_state.get("error", "")
                        feed_debug["rendered_circle_age_seconds"] = round(rendered_age, 2) if rendered_fresh else None
                        if rendered_fresh:
                            feed_debug["rendered_ball_over"] = _normal_space(rendered_ball.get("over"))
                            feed_debug["rendered_circle_value"] = _normal_space(rendered_ball.get("circle_val")).upper()
                        if raw_balls:
                            newest_circle = _latest_source_ball(raw_balls) or {}
                            feed_debug["latest_ball_over"] = _normal_space(newest_circle.get("over"))
                            feed_debug["latest_circle_value"] = _normal_space(
                                newest_circle.get("circle_val", newest_circle.get("runs", ""))
                            ).upper()
                        feed_debug["score"] = {
                            "team": fresh_data.get("batting_team"),
                            "runs": fresh_data.get("total_runs"),
                            "wickets": fresh_data.get("total_wickets"),
                            "overs": fresh_data.get("overs"),
                        }
                        self.latest_feed_debug = feed_debug
                        self.latest_match_state = fresh_data
                        await self.broadcast_ws("match_state", fresh_data)

                        latest_ball = _latest_source_ball(raw_balls)
                        if rendered_fresh and rendered_ball and (
                            latest_ball is None or _ball_order(rendered_ball) >= _ball_order(latest_ball)
                        ):
                            latest_ball = rendered_ball

                        # Baseline on the first real rendered circle instead of announcing the
                        # current ball just because the browser watcher caught up after startup.
                        if rendered_fresh and not rendered_circle_state.get("baseline_done"):
                            rendered_circle_state["baseline_done"] = True
                            if last_latest_key is not None and _ball_order(rendered_ball) > _ball_order(last_latest_ball or {}):
                                last_latest_key = _ball_identity(rendered_ball)
                                last_latest_ball = rendered_ball
                                last_latest_state = fresh_data
                                seen_feed_keys.add(last_latest_key)
                                logger.info(
                                    "Synced rendered circle at %s [%s]; voice starts on the next delivery",
                                    rendered_ball.get("over"), rendered_ball.get("circle_val")
                                )
                                latest_ball = None

                        # Keep the visual ball-by-ball feed useful even when speech skips stale
                        # balls. On startup, don't replay the match's full historical feed.
                        if latest_ball and last_latest_key is None:
                            latest_key = _ball_identity(latest_ball)
                            for old_ball in raw_balls:
                                old_key = _ball_identity(old_ball)
                                if old_key != latest_key:
                                    seen_feed_keys.add(old_key)
                            if latest_key not in seen_feed_keys:
                                seen_feed_keys.add(latest_key)
                                await self.broadcast_ws("feed_item", {
                                    "over": _normal_space(latest_ball.get("over")),
                                    "runs": _normal_space((latest_ball.get("circle_val") or latest_ball.get("runs"))),
                                    "matchup": _normal_space(latest_ball.get("matchup")),
                                    "badge": _source_badge(latest_ball),
                                    "commentary": _normal_space(latest_ball.get("commentary")),
                                })

                            # Seed the current page snapshot only. Its latest visible ball
                            # may be historical; speech starts only after the next new delivery.
                            last_latest_key = latest_key
                            last_latest_ball = latest_ball
                            last_latest_state = fresh_data

                        elif latest_ball:
                            latest_key = _ball_identity(latest_ball)

                            # Add any truly new feed lines in chronological order, but do not
                            # enqueue those old lines for speech. The voice queue gets only the
                            # single latest delivery.
                            new_feed = []
                            for candidate in raw_balls:
                                candidate_key = _ball_identity(candidate)
                                if candidate_key not in seen_feed_keys:
                                    seen_feed_keys.add(candidate_key)
                                    new_feed.append(candidate)
                            for candidate in sorted(new_feed, key=lambda b: _ball_order(b)):
                                candidate_over = _ball_over_index(candidate)
                                delivery_match = re.search(r"\d+\.(\d+)", _normal_space(candidate.get("over")))
                                if (candidate_over is not None and delivery_match
                                        and int(delivery_match.group(1)) == 1):
                                    tracked_full_overs.add(candidate_over)
                                await self.broadcast_ws("feed_item", {
                                    "over": _normal_space(candidate.get("over")),
                                    "runs": _normal_space((candidate.get("circle_val") or candidate.get("runs"))),
                                    "matchup": _normal_space(candidate.get("matchup")),
                                    "badge": _source_badge(candidate),
                                    "commentary": _normal_space(candidate.get("commentary")),
                                })

                            if latest_key != last_latest_key:
                                old_over = _ball_over_index(last_latest_ball or {}) if last_latest_ball else None
                                new_over = _ball_over_index(latest_ball)
                                if (old_over is not None and new_over is not None
                                        and new_over > old_over and old_over in tracked_full_overs):
                                    over_id = f"{last_latest_state.get('batting_team', '')}:{old_over}"
                                    if over_id not in announced_over_ids and over_id not in pending_over_announcements:
                                        summary_text = _over_announcement(old_over, raw_balls, lang, fresh_data)
                                        if summary_text:
                                            pending_over_announcements[over_id] = summary_text
                                            pending_over_indices[over_id] = old_over
                                            pending_over_positions[over_id] = "before_ball"
                                            logger.info("Queued completed-over announcement for over %s", old_over + 1)

                                # Ignore a stale/backwards page snapshot rather than speaking it.
                                if (_ball_order(latest_ball) >= _ball_order(last_latest_ball or {})):
                                    last_latest_key = latest_key
                                    last_latest_ball = latest_ball
                                    latest_event_version += 1
                                    # Attach each over recap to exactly one live event.
                                    # Reattaching every pending recap to every later ball caused
                                    # the same completed-over sentence to repeat on subsequent balls.
                                    pending_summaries = []
                                    for over_id, summary in list(pending_over_announcements.items()):
                                        if over_id in announced_over_ids:
                                            continue
                                        summary_over = pending_over_indices.get(over_id)
                                        position = pending_over_positions.get(over_id, "after_ball")
                                        if new_over is not None and summary_over is not None and new_over > summary_over:
                                            position = "before_ball"
                                            pending_over_positions[over_id] = position
                                        pending_summaries.append({"id": over_id, "text": summary, "position": position, "over_index": summary_over})
                                        claimed_over_ids.add(over_id)
                                    # Generate the manual circle call immediately at detection,
                                    # publish its text now, then synthesize audio asynchronously.
                                    circle_token = _normal_space(
                                        latest_ball.get("circle_val", latest_ball.get("runs", ""))
                                    ).upper()
                                    live_info = {
                                        **fresh_data,
                                        **latest_ball,
                                        "circle_val": circle_token,
                                        "runs": circle_token,
                                        "over": _normal_space(latest_ball.get("over")),
                                    }
                                    # Generate the manual ball call with the same pro scripts
                                    # used by final.py; the CREX paragraph is never a speech fallback.
                                    radio_meta = build_live_circle_announcement(live_info, lang=lang) or {}
                                    radio_preview = _normal_space(
                                        radio_meta.get("spoken_text") or f"Live ball {live_info['over']}."
                                    )
                                    event_id = latest_key
                                    radio_badge = radio_meta.get("badge") or _source_badge(latest_ball)
                                    radio_rate = radio_meta.get("rate") or "+5%"
                                    radio_pitch = radio_meta.get("pitch") or "+1Hz"

                                    self.latest_commentary = radio_preview
                                    self.latest_tts_debug = {
                                        "status": "queued_for_tts",
                                        "provider": tts_provider,
                                        "voice": getattr(tts, "voice", None) or getattr(tts, "speaker", None),
                                        "language": lang,
                                        "over": live_info["over"],
                                        "circle_value": circle_token,
                                        "radio_text_preview": radio_preview[:180],
                                        "rate": radio_rate,
                                        "pitch": radio_pitch,
                                        "active_striker": fresh_data.get("striker"),
                                        "active_striker_score": fresh_data.get("striker_runs"),
                                        "active_striker_balls": fresh_data.get("striker_balls"),
                                        "active_non_striker": fresh_data.get("non_striker"),
                                        "active_non_striker_score": fresh_data.get("non_striker_runs"),
                                        "active_non_striker_balls": fresh_data.get("non_striker_balls"),
                                    }
                                    await self.broadcast_ws("commentary_preview", {
                                        "event_id": event_id,
                                        "version": latest_event_version,
                                        "text": radio_preview,
                                        "badge": radio_badge,
                                        "over": live_info["over"],
                                        "circle_value": circle_token,
                                        "status": "preparing_audio",
                                        "voice_fallback_after_ms": 1300,
                                    })
                                    await queue_latest({
                                        "version": latest_event_version,
                                        "event_id": event_id,
                                        "ball": latest_ball,
                                        "fresh_data": fresh_data,
                                        "radio_text": radio_preview,
                                        "badge": radio_badge,
                                        "rate": radio_rate,
                                        "pitch": radio_pitch,
                                        "over_announcements": pending_summaries,
                                    })
                                else:
                                    logger.info("Ignoring backwards CREX delivery snapshot: %s", latest_ball.get("over"))

                            last_latest_state = fresh_data

                        consecutive_errors = 0
                    except asyncio.CancelledError:
                        raise
                    except ValueError as exc:
                        consecutive_errors += 1
                        logger.warning("Parse/fetch error (%d): %s", consecutive_errors, exc)
                        if consecutive_errors >= 3:
                            await self.broadcast_ws("error", {"message": str(exc)})
                    except Exception as exc:
                        consecutive_errors += 1
                        logger.warning("CREX poll error (%d): %s", consecutive_errors, exc, exc_info=True)
                        if consecutive_errors >= 5:
                            await self.broadcast_ws("error", {"message": f"Connection issue with match feed: {exc}"})
                            consecutive_errors = 0

                    await asyncio.sleep(0.35)

        except asyncio.CancelledError:
            logger.info("Broadcast pipeline cancelled by user")
            raise
        except Exception as exc:
            logger.error("Broadcast pipeline error: %s", exc, exc_info=True)
            await self.broadcast_ws("error", {"message": f"Pipeline error: {exc}"})
        finally:
            if rendered_circle_task is not None and not rendered_circle_task.done():
                rendered_circle_task.cancel()
                try:
                    await rendered_circle_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            if api_feed_task is not None and not api_feed_task.done():
                api_feed_task.cancel()
                try:
                    await api_feed_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            was_running = self.is_running
            self.is_running = False
            if self.latest_start_debug.get("status") == "running":
                self.latest_start_debug = {
                    **self.latest_start_debug,
                    "status": "pipeline_stopped",
                    "phase": "pipeline_finally",
                    "note": "Pipeline ended or encountered an unrecoverable error; inspect Render logs.",
                }
            if not voice_task.done():
                voice_task.cancel()
                try:
                    await voice_task
                except asyncio.CancelledError:
                    pass
            await self.broadcast_ws("status", {"is_running": False})


hub = BroadcastHub()

# Web Route Handlers
async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30.0, autoping=True)
    await ws.prepare(request)
    hub.active_websockets.add(ws)

    # Send initial status
    await ws.send_str(json.dumps({
        "type": "status",
        "data": {"is_running": hub.is_running}
    }))
    if hub.latest_match_state:
        await ws.send_str(json.dumps({
            "type": "match_state",
            "data": hub.latest_match_state
        }))

    try:
        async for msg in ws:
            pass
    finally:
        hub.active_websockets.discard(ws)

    return ws

async def handle_api_status(request: web.Request) -> web.Response:
    return web.json_response({
        "is_running": hub.is_running,
        "match_state": hub.latest_match_state,
        "latest_commentary": hub.latest_commentary,
        "feed_debug": hub.latest_feed_debug,
        "tts_debug": hub.latest_tts_debug,
        "start_debug": hub.latest_start_debug,
        "server_build": "pro-commentary-engine-20261022-1",
    })

async def handle_api_live_matches(request: web.Request) -> web.Response:
    matches = await asyncio.to_thread(get_live_matches)
    return web.json_response({"ok": True, "matches": matches})

async def handle_api_resolve(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        if not query:
            return web.json_response({"ok": False, "error": "Query is required"}, status=400)
        resolved_url, note = resolve_match_url(query)
        return web.json_response({"ok": True, "resolved_url": resolved_url, "note": note})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_api_start(request: web.Request) -> web.Response:
    # Record entry before validation so /api/status can prove whether the browser
    # actually reached this endpoint, even when the JSON body or form values fail.
    hub.latest_start_debug = {
        "status": "api_start_received",
        "phase": "request_entered",
        "method": request.method,
        "path": request.path,
    }
    try:
        body = await request.json()
        url = str(body.get("url", "") or "").strip()
        lang = str(body.get("lang", "hi") or "hi").strip()
        stream_key = str(body.get("stream_key", "") or "").strip()
        tts_provider = str(body.get("tts_provider", "edge") or "edge").strip() or "edge"
        tts_api_key = str(body.get("sarvam_api_key", "") or "").strip() or os.getenv("SARVAM_API_KEY", "").strip()
        tts_speaker = str(body.get("sarvam_speaker", "") or "").strip()

        hub.latest_start_debug.update({
            "phase": "request_validated",
            "url": url[:320],
            "language": lang,
            "voice_engine": tts_provider,
        })
        logger.info("Received /api/start request (url=%s, lang=%s, tts_provider=%s)",
                    url[:320], lang, tts_provider)

        if not url:
            hub.latest_start_debug.update({"status": "rejected", "phase": "validation", "error": "Match URL or team name is required"})
            return web.json_response({"ok": False, "error": "Match URL or team name is required"}, status=400)

        if tts_provider == "sarvam" and not tts_api_key:
            hub.latest_start_debug.update({"status": "rejected", "phase": "validation", "error": "Sarvam API key is required"})
            return web.json_response({"ok": False, "error": "Sarvam API key is required to use the Sarvam voice engine"}, status=400)

        hub.latest_start_debug.update({"status": "calling_broadcast", "phase": "hub.start_broadcast"})
        res = await hub.start_broadcast(url=url, lang=lang, stream_key=stream_key,
                                         tts_provider=tts_provider, tts_api_key=tts_api_key, tts_speaker=tts_speaker)
        status_code = 200 if res.get("ok") else 400
        if res.get("ok"):
            logger.info("Broadcast start accepted (url=%s)", (res.get("resolved_url") or url)[:320])
        else:
            hub.latest_start_debug.update({"status": "rejected", "phase": "hub.start_broadcast", "error": str(res.get("error", "unknown error"))[:500]})
            logger.error("Broadcast start rejected: %s", res.get("error", "unknown error"))
        return web.json_response(res, status=status_code)
    except Exception as e:
        hub.latest_start_debug.update({
            "status": "error",
            "phase": "handle_api_start",
            "error": str(e)[:500],
        })
        logger.exception("Unhandled exception in /api/start")
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def handle_api_stop(request: web.Request) -> web.Response:
    res = await hub.stop_broadcast()
    return web.json_response(res)

async def handle_favicon(request: web.Request) -> web.Response:
    return web.Response(status=204)

def create_app() -> web.Application:
    app = web.Application()

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/favicon.ico", handle_favicon)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/api/live-matches", handle_api_live_matches)
    app.router.add_post("/api/resolve", handle_api_resolve)
    app.router.add_post("/api/start", handle_api_start)
    app.router.add_post("/api/stop", handle_api_stop)

    # Static assets
    app.router.add_static("/static/", path=WEB_DIR, name="static")

    return app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000" if os.getenv("RENDER") else "8088"))
    host = os.getenv("HOST", "0.0.0.0")
    app = create_app()
    logger.info(f"Cricket AI Web UI running on http://{host}:{port}")
    web.run_app(app, host=host, port=port)
