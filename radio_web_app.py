"""Standalone CREX radio web app. Leaves final.py untouched."""
from __future__ import annotations
import asyncio, base64, logging, os, queue, re, threading, time
from typing import Any
import edge_tts
from flask import Flask, jsonify, request, Response
from playwright.async_api import async_playwright
from final import build_live_circle_announcement, prepare_tts_pronunciation_text

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("preet-radio-web")
VOICE = os.getenv("RADIO_VOICE", "hi-IN-MadhurNeural")
AUDIO_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=8)
STOP_EVENT = threading.Event()
WORKER = None
LOCK = threading.Lock()
STATE = {"running": False, "status": "idle", "url": "", "ball": "", "badge": "", "score": "", "commentary": "", "error": "", "last_updated": None}

EXTRACT_JS = r"""() => {
 const title=document.title||'', body=document.body?document.body.innerText:'';
 const tokenRe=/^(?:[0-6]|w|wkt|out|wd|wide|nb|no\s*ball|lb|b|\d+\s*(?:wd|nb|lb|b)|(?:wd|nb|lb|b)\s*\+?\s*\d+)$/i;
 const extraRe=/(?:wd|wide|nb|no\s*ball)/i; let best=null;
 for(const row of document.querySelectorAll('div[class*="over-card"],div[class*="recent-over"],div[class*="overRow"],div[class*="overWrap"],div[class*="over"]')){
  const m=(row.innerText||'').match(/\bOver\s+(\d+)\b/i); if(!m)continue; const vals=[];
  for(const el of row.querySelectorAll('span,b,div[class*="ball"],div[class*="circle"]')){if(el.children.length)continue;const v=(el.innerText||'').trim();if(tokenRe.test(v))vals.push(v);}
  if(vals.length&&vals.length<=12){const over=Number(m[1]);if(!best||over>best.over||(over===best.over&&vals.length>best.vals.length))best={over,vals};}
 }
 let ball=null,seq=null,circleVal=null,isOverComplete=false;
 if(best){const legal=best.vals.filter(v=>!extraRe.test(v)).length;ball=(best.over-1)+'.'+Math.min(6,Math.max(1,legal));seq=(best.over-1)+':'+best.vals.length;circleVal=best.vals[best.vals.length-1];isOverComplete=legal===6&&!extraRe.test(circleVal);}
 let bowler='',batsman='';const toRe=/([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})\s+to\s+([A-Za-z'\-]+(?: [A-Za-z'\-]+){0,3})(?:,|\n|$)/i;
 for(const el of document.querySelectorAll('div[class*="item"],div[class*="update"],div[class*="comm-card"],div[class*="row"]')){const t=el.innerText||'';if(t.length>400)continue;const m=t.match(toRe);if(m){bowler=m[1].trim();batsman=m[2].trim();break;}}
 const sm=title.match(/([A-Za-z0-9-]+)\s+(\d{1,3})\s*[-/]\s*([0-9]|10)\s*\(/);
 return {ball,seq,circleVal,bowler,batsman,runs:sm?Number(sm[2]):null,wkts:sm?Number(sm[3]):null,isOverComplete,isInningsBreak:/Innings Break/i.test(body),overBalls:best?best.vals:[]};
}"""

def update_state(**values):
    with LOCK:
        STATE.update(values)
        STATE["last_updated"] = time.time()

async def synthesize(text, level=2):
    profiles = {1: ("+15%", "+10Hz", "+0%"), 2: ("+20%", "+12Hz", "+15%"), 3: ("+25%", "+15Hz", "+35%"), 4: ("+30%", "+20Hz", "+60%")}
    rate, pitch, volume = profiles.get(max(1, min(4, int(level))), profiles[2])
    comm = edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch, volume=volume)
    chunks = []
    async for item in comm.stream():
        if item.get("type") == "audio": chunks.append(item["data"])
    if not chunks: return
    payload = {"audio": base64.b64encode(b"".join(chunks)).decode("ascii"), "text": text, "created": time.time()}
    try: AUDIO_QUEUE.put_nowait(payload)
    except queue.Full:
        try: AUDIO_QUEUE.get_nowait()
        except queue.Empty: pass
        AUDIO_QUEUE.put_nowait(payload)

async def run_monitor(url):
    last_key = None
    update_state(running=True, status="connecting", url=url, error="", commentary="")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--disable-blink-features=AutomationControlled"])
            try:
                page = await browser.new_page(viewport={"width":1280,"height":800}, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(2000)
                update_state(status="monitoring")
                failures = 0
                while not STOP_EVENT.is_set():
                    try:
                        snap = await page.evaluate(EXTRACT_JS)
                        ball, badge = snap.get("ball"), snap.get("circleVal")
                        if ball and badge:
                            key = str(snap.get("seq") or ball) + "|" + str(badge)
                            score = str(snap.get("runs")) + "/" + str(snap.get("wkts")) if snap.get("runs") is not None and snap.get("wkts") is not None else ""
                            update_state(ball=ball, badge=badge, score=score, status="monitoring")
                            if last_key is None: last_key = key
                            elif key != last_key:
                                last_key = key
                                info = {"over":ball,"circle_val":badge,"runs":badge,"bowler":snap.get("bowler",""),"batter":snap.get("batsman",""),"matchup":(snap.get("bowler","")+" to "+snap.get("batsman","")).strip(),"last_dismissal":{},"total_runs":snap.get("runs"),"total_wickets":snap.get("wkts")}
                                meta = build_live_circle_announcement(info, lang="hi") or {}
                                line = str(meta.get("spoken_text") or ("गेंद "+str(ball)+", "+str(badge)+" रन"))
                                speech = str(meta.get("tts_text") or prepare_tts_pronunciation_text(line, info))
                                update_state(commentary=line, status="preparing audio")
                                await synthesize(speech, meta.get("level", 2))
                                update_state(status="monitoring")
                        failures = 0
                    except Exception as exc:
                        failures += 1
                        if failures >= 5:
                            update_state(status="reconnecting", error=str(exc)[:200])
                            try: await page.reload(wait_until="domcontentloaded", timeout=30000)
                            except Exception: pass
                            failures = 0
                    await asyncio.sleep(0.5)
            finally: await browser.close()
    except Exception as exc:
        log.exception("Radio monitor failed")
        update_state(status="error", error=str(exc)[:300])
    finally: update_state(running=False, status="stopped" if not STATE.get("error") else "error")

def worker_main(url):
    asyncio.run(run_monitor(url))

@app.get("/")
def index(): return Response(PAGE, mimetype="text/html")

@app.get("/api/status")
def status():
    with LOCK: return jsonify(dict(STATE, queued_audio=AUDIO_QUEUE.qsize()))

@app.post("/api/start")
def start():
    global WORKER
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    if not re.match(r"^https?://(www\.)?crex\.com/", url, re.I):
        return jsonify({"ok":False,"error":"Enter a valid https://crex.com match URL."}), 400
    if WORKER and WORKER.is_alive(): return jsonify({"ok":False,"error":"A match is already running."}), 409
    STOP_EVENT.clear()
    while not AUDIO_QUEUE.empty():
        try: AUDIO_QUEUE.get_nowait()
        except queue.Empty: break
    WORKER = threading.Thread(target=worker_main, args=(url,), daemon=True)
    WORKER.start()
    return jsonify({"ok":True,"status":"starting"})

@app.post("/api/stop")
def stop():
    STOP_EVENT.set()
    update_state(status="stopping")
    return jsonify({"ok":True})

@app.get("/api/audio")
def audio():
    try: return jsonify(AUDIO_QUEUE.get_nowait())
    except queue.Empty: return jsonify({"audio":None})

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Preet Sports | CREX Radio</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#07111e;color:#edf4ff;font:16px/1.5 system-ui,sans-serif}main{max-width:900px;margin:auto;padding:28px 18px}.brand{color:#69e1b5;font-weight:800;letter-spacing:.12em;font-size:13px}h1{font-size:clamp(28px,5vw,44px);margin:8px 0}.sub,.note{color:#9fb0c7}.panel{background:#101e30;border:1px solid #263a52;border-radius:18px;padding:20px;margin:14px 0}label{display:block;font-size:13px;margin-bottom:8px;color:#b6c8dd}input{width:100%;padding:14px;border-radius:10px;border:1px solid #39516d;background:#081421;color:white;font-size:15px}.actions{display:flex;gap:10px;margin-top:12px}button{border:0;border-radius:10px;padding:12px 18px;font-weight:800;cursor:pointer}#start{background:#5ee0ad;color:#062018}#stop{background:#ff626c;color:#28080b}button:disabled{opacity:.4}.status{color:#a7bad0;font-size:14px}.score{font-size:clamp(32px,6vw,56px);font-weight:850}.ball{display:inline-block;background:#203a54;border-radius:9px;padding:4px 10px;font-weight:800}.commentary{font-size:20px;min-height:64px;margin-top:10px}audio{width:100%;margin-top:12px}.note{font-size:12px}
</style></head><body><main><div class="brand">PREET SPORTS · RADIO ENGINE</div><h1>CREX Live Commentary</h1><div class="sub">Live ball detection with Hindi neural voice played in your browser.</div>
<div class="panel"><label for="url">CREX match URL</label><input id="url" type="url" placeholder="https://crex.com/cricket-live-score/"><div class="actions"><button id="start">▶ Start live radio</button><button id="stop" disabled>■ Stop</button></div><p class="note">The current delivery is used as a baseline; audio begins on the next new delivery.</p></div>
<div class="panel"><div id="status" class="status">IDLE</div><div id="score" class="score">—</div><div id="ball" class="ball">Waiting for ball</div><div id="commentary" class="commentary">Paste a CREX match URL and start the radio.</div><audio id="player" controls autoplay></audio><div id="error" class="note"></div></div></main>
<script>
const $=id=>document.getElementById(id);let active=false;
$('start').onclick=async()=>{const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:$('url').value})});const d=await r.json();if(!r.ok){$('error').textContent=d.error||'Could not start';return}active=true;$('start').disabled=true;$('stop').disabled=false;$('error').textContent='';};
$('stop').onclick=async()=>{await fetch('/api/stop',{method:'POST'});active=false;$('start').disabled=false;$('stop').disabled=true;};
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});const s=await r.json();$('status').textContent=(s.status||'idle').toUpperCase();$('score').textContent=s.score||'—';$('ball').textContent=s.ball?'Ball '+s.ball+' · '+(s.badge||''):'Waiting for ball';if(s.commentary)$('commentary').textContent=s.commentary;if(s.error)$('error').textContent=s.error;if(!s.running&&active&&['error','stopped'].includes(s.status)){active=false;$('start').disabled=false;$('stop').disabled=true;}}catch(e){}}
async function pollAudio(){try{const r=await fetch('/api/audio',{cache:'no-store'});const d=await r.json();if(d.audio){const a=$('player');a.src='data:audio/mpeg;base64,'+d.audio;a.play().catch(()=>{});$('commentary').textContent=d.text||$('commentary').textContent;}}catch(e){}}
setInterval(refresh,500);setInterval(pollAudio,250);refresh();
</script></body></html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), threaded=True)
