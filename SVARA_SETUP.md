# Preet Sports — Svara TTS v1 setup

Model: https://huggingface.co/kenpath/svara-tts-v1
Inference server: https://github.com/Kenpath/svara-tts-inference

## Integration

The Svara provider calls POST /v1/audio/speech and returns MP3 to the current Preet Sports ball queue. Existing Edge/Madhur remains the default. Official male voice IDs are hi_male, pa_male and en_male.

## GPU hosting

Kenpath's inference server uses an embedded vLLM engine and CUDA Docker image. Its deployment guide lists a minimum NVIDIA GPU with 16 GB VRAM, 16 GB system RAM and 50 GB storage; recommended is 24 GB+ VRAM, 32 GB RAM and 100 GB storage.

Do not deploy the official inference server on the existing CPU-only Render 2c-8g service. Host Svara on a GPU-capable provider, then point SVARA_BASE_URL from cricket-ai-streamer to that service. GGUF exports may support separate CPU experimentation, but that is not the official vLLM Docker deployment.

## Environment settings

Set these on cricket-ai-streamer:

- SVARA_BASE_URL: the private base URL of the inference API.
- SVARA_MODEL=svara-tts-v1
- SVARA_VOICE=hi_male (or pa_male / en_male)
- SVARA_TTS_TIMEOUT_SECONDS=30
- SVARA_STYLE_TAG=<happy> optional to request energetic delivery; other documented tags include <sad>, <anger>, <fear>, and <clear>.
- SVARA_REFERENCE_AUDIO_PATH: optional path to an authorized recording on private storage attached to the cricket service.
- SVARA_REFERENCE_TRANSCRIPT: optional exact transcript of the recording; it improves cloning quality.

The adapter converts the configured recording to mono 24 kHz WAV, base64-encodes it and sends the exact fields expected by Kenpath's API: reference_audio and reference_transcript. The recording is not committed to GitHub. Use only a voice you own or have explicit permission to clone. M4A decoding requires ffmpeg, which the cricket Dockerfile installs.

## API security

The stock Svara server currently does not enforce Bearer/API-key authentication; the upstream examples use a placeholder SDK key. Sending SVARA_API_KEY does not secure the stock API by itself. Keep the inference API on a private network/VPN or put it behind an authenticated reverse proxy. Do not expose an unauthenticated synthesis endpoint publicly.

## Runtime notes

- Svara's current request model accepts a speed field but documents it as not implemented. The adapter applies the existing broadcaster rate locally with FFmpeg atempo.
- Pitch is not mapped because the inference endpoint does not document a pitch field.
- The model card lists Apache-2.0; review the model, inference code, tokenizer/codec and all required components before monetized production use.
- This PR does not create a Render service or trigger deployment.