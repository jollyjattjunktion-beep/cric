"""Svara TTS adapter for Preet Sports.

Targets Kenpath's documented OpenAI-compatible Svara inference API.
The optional reference recording stays on private server storage and is
base64-encoded into the documented reference_audio field at request time.
"""
from __future__ import annotations

import asyncio
import base64
import html
import io
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import aiohttp

logger = logging.getLogger("PreetSports.Svara")


def _clean(text: str) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()[:4096]


class SvaraTTS:
    """Svara API client; compatible with Preet Sports MP3/PCM TTS interface."""

    VOICES = {"hi": "hi_male", "pa": "pa_male", "en": "en_male"}

    def __init__(
        self,
        language: str = "hi",
        voice: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        reference_audio_path: Optional[str] = None,
        reference_transcript: Optional[str] = None,
    ) -> None:
        self.language = language
        self.base_url = (base_url or os.getenv("SVARA_BASE_URL", "")).strip().rstrip("/")
        if self.base_url and "://" not in self.base_url:
            self.base_url = "http://" + self.base_url
        if not self.base_url:
            raise ValueError("SVARA_BASE_URL is required when Svara is selected.")

        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("SVARA_BASE_URL must be a valid http(s) URL or host:port.")

        self.api_key = (api_key or os.getenv("SVARA_API_KEY", "")).strip()
        self.model = (model or os.getenv("SVARA_MODEL", "svara-tts-v1")).strip()
        self.timeout = float(timeout or os.getenv("SVARA_TTS_TIMEOUT_SECONDS", "30"))
        if self.timeout <= 0:
            raise ValueError("SVARA_TTS_TIMEOUT_SECONDS must be greater than zero.")

        language_key = str(language or "hi").lower()
        default_voice = self.VOICES.get(language_key, "hi_male")
        self.voice = (voice or os.getenv("SVARA_VOICE", "") or default_voice).strip()
        self.speech_url = (
            self.base_url + "/audio/speech"
            if self.base_url.endswith("/v1")
            else self.base_url + "/v1/audio/speech"
        )

        self.reference_audio = self._load_reference_audio(
            reference_audio_path or os.getenv("SVARA_REFERENCE_AUDIO_PATH", "")
        )
        self.reference_transcript = (
            reference_transcript or os.getenv("SVARA_REFERENCE_TRANSCRIPT", "")
        ).strip()
        if self.reference_transcript and not self.reference_audio:
            raise ValueError(
                "SVARA_REFERENCE_TRANSCRIPT requires SVARA_REFERENCE_AUDIO_PATH."
            )

        self.style_tag = os.getenv("SVARA_STYLE_TAG", "").strip()
        if self.style_tag and not re.fullmatch(
            r"<(?:happy|sad|anger|fear|clear)>", self.style_tag
        ):
            raise ValueError(
                "SVARA_STYLE_TAG must be one of: <happy>, <sad>, <anger>, <fear>, <clear>."
            )

    @staticmethod
    def _load_reference_audio(path_value: str) -> str:
        """Convert a private reference file to WAV and base64 for Svara's API."""
        path_value = str(path_value or "").strip()
        if not path_value:
            return ""
        path = Path(path_value).expanduser()
        if not path.is_file():
            raise ValueError("SVARA_REFERENCE_AUDIO_PATH does not point to a readable file.")
        if path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Svara reference audio must be no larger than 25 MB.")

        try:
            from pydub import AudioSegment

            segment = AudioSegment.from_file(str(path))
            segment = segment.set_channels(1).set_frame_rate(24000).set_sample_width(2)
            wav_buffer = io.BytesIO()
            segment.export(wav_buffer, format="wav")
            return base64.b64encode(wav_buffer.getvalue()).decode("ascii")
        except Exception as exc:
            raise ValueError(
                "Could not decode SVARA_REFERENCE_AUDIO_PATH. Use WAV, MP3, or M4A "
                "and ensure ffmpeg is installed on the cricket service."
            ) from exc

    @staticmethod
    def _speed_from_rate(rate: Optional[str]) -> float:
        """Convert broadcaster strings such as +15% into an FFmpeg atempo factor."""
        match = re.fullmatch(r"\s*([+-])\s*(\d+(?:\.\d+)?)\s*%\s*", str(rate or ""))
        if not match:
            return 1.0
        amount = float(match.group(2)) / 100.0
        value = 1.0 + amount if match.group(1) == "+" else 1.0 - amount
        return min(2.0, max(0.5, value))

    @staticmethod
    def _apply_speed(audio: bytes, rate: Optional[str]) -> bytes:
        """Apply speed locally because the Svara inference API currently ignores speed."""
        speed = SvaraTTS._speed_from_rate(rate)
        if not audio or abs(speed - 1.0) < 0.001:
            return audio
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-filter:a", f"atempo={speed:.3f}",
            "-f", "mp3", "pipe:1",
        ]
        try:
            result = subprocess.run(
                command, input=audio, capture_output=True, check=False, timeout=15
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
            logger.warning("Svara local tempo adjustment failed; using original audio: %s",
                           result.stderr.decode("utf-8", errors="ignore")[:200])
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("FFmpeg tempo adjustment unavailable; using original Svara audio: %s", exc)
        return audio

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "audio/mpeg, audio/wav, application/octet-stream",
            "Content-Type": "application/json",
        }
        if self.api_key:
            # Stock Svara currently documents no API-key validation.
            # This header is useful only behind a reverse proxy that enforces it.
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    async def speak_mp3(
        self, text: str, rate: Optional[str] = None, pitch: Optional[str] = None
    ) -> bytes:
        clean = _clean(text)
        if not clean:
            return b""
        if self.style_tag:
            clean = f"{clean} {self.style_tag}"

        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": clean,
            "response_format": "mp3",
        }
        if self.reference_audio:
            payload["reference_audio"] = self.reference_audio
            if self.reference_transcript:
                payload["reference_transcript"] = self.reference_transcript

        # Svara accepts speed but documents it as not yet implemented; tempo is
        # adjusted locally after synthesis to retain the existing broadcaster rate.
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.speech_url, json=payload, headers=self._headers()
                ) as response:
                    if response.status != 200:
                        detail = (await response.text())[:500]
                        raise RuntimeError(
                            f"Svara speech request failed (HTTP {response.status}): {detail}"
                        )
                    audio = await response.read()
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not audio or "json" in content_type or audio.lstrip().startswith(b"{"):
                        raise RuntimeError("Svara did not return audio bytes.")
                    if "wav" in content_type or audio[:4] == b"RIFF":
                        from pydub import AudioSegment

                        segment = AudioSegment.from_file(io.BytesIO(audio), format="wav")
                        output = io.BytesIO()
                        segment.export(output, format="mp3", bitrate="128k")
                        audio = output.getvalue()
                    if rate:
                        audio = await asyncio.to_thread(self._apply_speed, audio, rate)
                    return audio
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Svara request failed: %s", exc)
            raise RuntimeError(f"Svara request failed: {exc}") from exc

    async def speak(
        self, text: str, rate: Optional[str] = None, pitch: Optional[str] = None
    ) -> bytes:
        """Return 44.1 kHz stereo PCM for the existing YouTube audio mixer."""
        audio = await self.speak_mp3(text, rate=rate, pitch=pitch)
        if not audio:
            return b""
        from pydub import AudioSegment

        segment = AudioSegment.from_file(io.BytesIO(audio), format="mp3")
        segment = segment.set_frame_rate(44100).set_channels(2).set_sample_width(2)
        return segment.raw_data
