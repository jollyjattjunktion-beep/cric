# Preet Sports — VoiceStudio deployment and voice-profile setup

This integration adds VoiceStudio as an optional TTS provider. The existing
Edge and Sarvam providers remain unchanged. The cricket service calls VoiceStudio
over Render's private network and receives MP3 bytes through the existing
current-ball queue, stale-event protection and browser/YouTube audio pipeline.

## Important model-licensing gate

VoiceStudio's own checked-in model inventory currently records **no model with
commercial use cleared**. Its default OmniVoice model is specifically marked
non-commercial (CC-BY-NC weights, with separate audio-tokenizer terms). Other
cloning engines in the catalogue have unresolved licence/component reviews.
The application is AGPL-3.0, but that does not grant commercial rights to model
weights, dependencies, cloned voices or generated audio.

You may use this deployment for technical and non-commercial voice testing.
**Do not use generated audio in monetized Preet Sports broadcasts until the
exact engine, weights, tokenizer and required components have been independently
cleared for your intended commercial use.** A model's MIT/Apache label alone
does not establish that the complete synthesis stack is cleared.

## Render resources added to render.yaml

- `preet-voicestudio`: official `ghcr.io/debpalash/voicestudio:stable`
  image, 2 CPU / 8 GB RAM (`2c-8g`), 10 GB persistent disk.
- `cricket-ai-streamer`: receives VoiceStudio's internal host:port and API
  key through Render `fromService` references. The browser never receives
  the VoiceStudio admin key.
- Both services must be in the same Render region. The blueprint places the
  VoiceStudio service in Oregon, matching Render's default region; if the
  existing `cricket-ai-streamer` is in another region, change the new
  service's `region` before syncing the blueprint.

The current Render pricing page lists the `2c-8g` web plan at **$135/month**.
The 10 GB persistent disk is additional (Render lists SSD storage at $0.25/GB
per month). Confirm the current price in your own Render workspace before
creating the service. CPU-only generation may still be too slow for live
ball-by-ball commentary; benchmark cold-start and warm synthesis latency before
increasing traffic or choosing a larger plan.

## Deploy

1. Merge the integration PR only after the GitHub Actions tests pass.
2. In Render, open the Blueprint associated with this repository and sync the
   updated `render.yaml`. Review the new paid VoiceStudio service and disk
   before confirming resource creation.
3. Deploy `preet-voicestudio`. The official image exposes port 3900 and stores
   its model cache, SQLite data and voice profiles under
   `/app/omnivoice_data`. The persistent disk is mounted there.
4. Wait for the service health check `/health` to pass. The first model
   installation can download approximately 2.4 GB; watch the VoiceStudio
   service logs until startup and model setup are complete.
5. Open the VoiceStudio service's HTTPS URL in a browser. In Render's
   Environment tab, reveal/copy the generated `OMNIVOICE_API_KEY` only for
   the setup session. The Docker UI asks for this administrator key and
   exchanges it for a short-lived browser session. Never put the key in GitHub,
   the Preet Sports browser UI, or a committed `.env` file.
6. For your own voice only, use the VoiceStudio **Voice Clone** workspace with
   a clean 5–15 second reference recording. Confirm the profile is saved.
7. Retrieve the saved profile ID using the VoiceStudio API:

   ```bash
   curl -sS "https://YOUR-VOICESTUDIO-SERVICE.onrender.com/v1/audio/voices" \
     -H "Authorization: Bearer YOUR_OMNIVOICE_API_KEY"
   ```

   Find the saved profile's `voice_id` in the returned `voices` list (profile
   type). Do not guess or invent this ID.
8. In the Render Environment settings for `cricket-ai-streamer`, add
   `VOICESTUDIO_VOICE_ID` with that exact profile ID, then restart/redeploy
   the cricket service. The Blueprint already wires
   `VOICESTUDIO_BASE_URL` and `VOICESTUDIO_API_KEY` internally.
9. Open the Preet Sports broadcaster UI, choose **VoiceStudio — Preet's Cloned
   Voice**, and start a non-monetized test match/session. Check
   `/api/status` → `tts_debug` and both Render services' logs.

## Manual API smoke test

Use the public VoiceStudio HTTPS URL and administrator key only for a direct
setup smoke test. Replace the profile ID with the real ID returned above.

```bash
curl -sS "https://YOUR-VOICESTUDIO-SERVICE.onrender.com/v1/audio/speech" \
  -H "Authorization: Bearer YOUR_OMNIVOICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"omnivoice","voice":"YOUR_PROFILE_ID","input":"Preet Sports voice test. चार रन!","response_format":"mp3","speed":1.0,"language":"hi"}' \
  --output preet-voice-test.mp3
```

The application integration expects the endpoint to return audio bytes. It
converts WAV fallback responses to MP3 and keeps the existing PCM interface for
the YouTube streaming engine.

## Environment variables

Set these only in the Render dashboard or local untracked `.env` file:

- `VOICESTUDIO_BASE_URL`: Render private host:port, injected by Blueprint.
- `VOICESTUDIO_API_KEY`: VoiceStudio administrator key, injected by Blueprint.
- `VOICESTUDIO_VOICE_ID`: saved profile ID; add manually after cloning.
- `VOICESTUDIO_MODEL`: VoiceStudio engine ID; default `omnivoice` is for
  non-commercial testing only until its licence restrictions are resolved.
- `VOICESTUDIO_TTS_TIMEOUT_SECONDS`: defaults to 30 seconds for the VoiceStudio
  provider; tune only after measuring real CPU inference latency.
- `VOICESTUDIO_INSTRUCTIONS`: optional engine-specific instruction; support
  varies by engine.

Do not upload voice reference recordings, profile databases, model weights,
API keys or generated voice data to the Git repository.
