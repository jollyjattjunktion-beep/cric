# Preet Sports CREX Radio Web App

This is a separate browser-based radio app in `radio_web_app.py`. It reuses the existing commentary functions from `final.py`; **`final.py` is not modified**.

## Run locally

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Start the web app:

   ```bash
   python radio_web_app.py
   ```

3. Open `http://localhost:8080`, paste a CREX match URL, and press **Start live radio**. The browser plays the generated Hindi TTS audio.

## Render

The added `preet-sports-radio-web` service in `render.yaml` runs `radio_web_app.py` separately from the existing broadcaster service. Check the Render deploy logs and open the new service URL after deployment.

## Notes

- The app syncs to the current visible delivery first and announces new deliveries after that.
- Chromium must be installed for Playwright to read the rendered CREX page.
- TTS requires internet access to Microsoft Edge TTS.
- This app is an additional service; the existing `server.py` and `final.py` flow remains unchanged.
