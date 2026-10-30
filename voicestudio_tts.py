"""VoiceStudio HTTP adapter for Preet Sports.

The VoiceStudio model and voice profile live on the separate Render service.
This adapter keeps the existing cricket pipeline contract: speak_mp3() returns
MP3 bytes and speak() returns 44.1 kHz stereo PCM bytes.
"""
from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger("PreetSports.VoiceStudio")


class VoiceStudioTTS:
    """OpenAI-compatible client for a self-hosted VoiceStudio instance."""

    def __init__(
        self,
        language: str = "hi",
        voice: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.language = language
        self.base_url = (base_url or os.getenv("VOICESTUDIO_BASE_URL", "")).strip().rstrip("/")
        if self.base_url and "://" not in self.base_url:
            self.base_url = "http://" + self.base_url
        self.api_key = (api_key or os.getenv("VOICESTUDIO_API_KEY", "")).strip()
        self.voice = (voice or os.getenv("VOICESTUDIO_VOICE_ID", "")).strip()
        self.speaker = self.voice
        self.model = (model or os.getenv("VOICESTUDIO_MODEL", "omnivoice")).strip()
        self.timeout = float(timeout or os.getenv("VOICESTUDIO_TTS_TIMEOUT_SECONDS", "30"))

        missing = [
            name for name, value in (
                ("VOICESTUDIO_BASE_URL", self.base_url),
                ("VOICESTUDIO_API_KEY", self.api_key),
                ("VOICESTUDIO_VOICE_ID", self.voice),
            ) if not value
        ]
        if missing:
            raise ValueError("VoiceStudio is not configured. Set: " + ", ".join(missing))
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("VOICESTUDIO_BASE_URL must be an http(s) URL or Render host:port.")
        if self.timeout <= 0:
            raise ValueError("VOICESTUDIO_TTS_TIMEOUT_SECONDS must be greater than zero.")

        if self.base_url.endswith("/v1"):
            self.speech_url = self.base_url + "/audio/speech"
            self.voices_url = self.base_url + "/audio/voices"
        else:
            self.speech_url = self.base_url + "/v1/audio/speech"
            self.voices_url = self.base_url + "/v1/audio/voices"

    @staticmethod
    def _speed_from_rate(rate: Optional[str]) -> float:
        """Convert the existing +15% / -5% broadcaster rate to VoiceStudio speed."""
        if not rate:
            return 1.0
        match = re.fullmatch(r"\s*([+-])\s*(\d+(?:\.\d+)?)\s*%\s*", str(rate))
        if not match:
            return 1.0
        amount = float(match.group(2)) / 100.0
        speed = 1.0 + amount if match.group(1) == "+" else 1.0 - amount
        return min(4.0, max(0.25, speed))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "audio/mpeg, audio/wav, application/octet-stream",
        }

    async def speak_mp3(
        self,
        text: str,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> bytes:
        """Synthesize one commentary line and return MP3 bytes."""
        from cricket_streamer import sanitize_for_speech

        clean_text = sanitize_for_speech(text)
        if not clean_text:
            return b""
        if len(clean_text) > 4096:
            clean_text = clean_text[:4096]

        language_code = "hi" if self.language in {"hi", "hinglish"} else "en"
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": clean_text,
            "response_format": "mp3",
            "speed": self._speed_from_rate(rate),
            "language": language_code,
        }
        instructions = os.getenv("VOICESTUDIO_INSTRUCTIONS", "").strip()
        if instructions:
            payload["instructions"] = instructions

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.speech_url,
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    if response.status != 200:
                        detail = (await response.text())[:500]
                        raise RuntimeError(
                            f"VoiceStudio speech request failed (HTTP {response.status}): {detail}"
                        )
                    audio = await response.read()
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not audio:
                        raise RuntimeError("VoiceStudio returned an empty audio response.")
                    if "json" in content_type or audio.lstrip().startswith(b"{"):
                        raise RuntimeError("VoiceStudio returned JSON instead of audio bytes.")
                    if "wav" in content_type or audio[:4] == b"RIFF":
                        from pydub import AudioSegment
                        source = AudioSegment.from_file(io.BytesIO(audio), format="wav")
                        output = io.BytesIO()
                        source.export(output, format="mp3", bitrate="128k")
                        audio = output.getvalue()
                    return audio
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("VoiceStudio request failed: %s", exc)
            raise RuntimeError(f"VoiceStudio request failed: {exc}") from exc

    async def speak(
        self,
        text: str,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> bytes:
        """Return the PCM shape expected by the existing YouTube audio mixer."""
        mp3_data = await self.speak_mp3(text, rate=rate, pitch=pitch)
        if not mp3_data:
            return b""
        from pydub import AudioSegment
        segment = AudioSegment.from_file(io.BytesIO(mp3_data), format="mp3")
        segment = segment.set_frame_rate(44100).set_channels(2).set_sample_width(2)
        return segment.raw_data
