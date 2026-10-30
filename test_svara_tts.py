import base64
import os
import tempfile
import unittest
import wave
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from svara_tts import SvaraTTS


class FakeResponse:
    def __init__(self, status=200, body=b"ID3svara", text=""):
        self.status, self.body = status, body
        self.headers, self.message = {"Content-Type": "audio/mpeg"}, text

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def read(self): return self.body
    async def text(self): return self.message


class FakeSession:
    def __init__(self, response): self.response, self.request = response, None
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def post(self, url, *, json, headers):
        self.request = {"url": url, "json": json, "headers": headers}
        return self.response


class SvaraTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        options = dict(
            language="hinglish",
            base_url="http://svara:8000",
            voice="hi_male",
            timeout=10,
        )
        options.update(kwargs)
        return SvaraTTS(**options)

    def make_wav(self, path):
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\x00\x00" * 2400)

    def test_missing_url(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SVARA_BASE_URL"):
                SvaraTTS()

    def test_official_voice_ids(self):
        self.assertEqual(SvaraTTS(language="hi", base_url="http://x").voice, "hi_male")
        self.assertEqual(SvaraTTS(language="pa", base_url="http://x").voice, "pa_male")
        self.assertEqual(SvaraTTS(language="en", base_url="http://x").voice, "en_male")

    def test_reference_transcript_requires_reference_file(self):
        with patch.dict(os.environ, {"SVARA_REFERENCE_TRANSCRIPT": "spoken words"}, clear=True):
            with self.assertRaisesRegex(ValueError, "requires SVARA_REFERENCE_AUDIO_PATH"):
                self.client()

    async def test_openai_request_uses_documented_reference_audio_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reference.wav"
            self.make_wav(path)
            client = self.client(
                reference_audio_path=str(path),
                reference_transcript="यह एक परीक्षण रिकॉर्डिंग है।",
            )
            fake = FakeSession(FakeResponse())
            with patch("aiohttp.ClientSession", return_value=fake):
                audio = await client.speak_mp3("शानदार चौका!")
            self.assertEqual(audio, b"ID3svara")
            payload = fake.request["json"]
            self.assertEqual(fake.request["url"], "http://svara:8000/v1/audio/speech")
            self.assertEqual(payload["voice"], "hi_male")
            self.assertEqual(payload["model"], "svara-tts-v1")
            self.assertEqual(payload["response_format"], "mp3")
            self.assertTrue(base64.b64decode(payload["reference_audio"]).startswith(b"RIFF"))
            self.assertEqual(payload["reference_transcript"], "यह एक परीक्षण रिकॉर्डिंग है।")
            self.assertNotIn("speed", payload)

    async def test_reference_file_missing_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not point to a readable file"):
            self.client(reference_audio_path="/no/such/private-reference.wav")


    def test_local_tempo_mapping_and_ffmpeg_filter(self):
        self.assertAlmostEqual(SvaraTTS._speed_from_rate("+15%"), 1.15)
        self.assertAlmostEqual(SvaraTTS._speed_from_rate("-10%"), 0.9)
        completed = SimpleNamespace(returncode=0, stdout=b"ID3sped", stderr=b"")
        with patch("svara_tts.subprocess.run", return_value=completed) as run:
            result = SvaraTTS._apply_speed(b"ID3source", "+15%")
        self.assertEqual(result, b"ID3sped")
        self.assertIn("atempo=1.150", run.call_args.args[0])

    async def test_http_error(self):
        client, fake = self.client(), FakeSession(
            FakeResponse(401, b"", "unauthorized")
        )
        with patch("aiohttp.ClientSession", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                await client.speak_mp3("test")


if __name__ == "__main__":
    unittest.main()
