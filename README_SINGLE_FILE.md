# Preet Sports — Single-file CREX commentary runner

## Windows setup

```powershell
py -m pip install -r requirements-single-file.txt
py -m playwright install chromium
py live_commentary.py
```

Paste a CREX match URL when prompted, or pass it directly:

```powershell
py live_commentary.py "https://crex.com/cricket-live-score/your-match-url"
```

Test sample calls with:

```powershell
py live_commentary.py --demo
```

The runner consolidates CREX page monitoring, Hindi commentary generation, and Windows audio playback in one Python file. Edge TTS requires an internet connection; the CREX page is monitored with headless Chromium. Keep the program running during the match.
