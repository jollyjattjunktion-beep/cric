import os
import unittest
from unittest.mock import patch

from voicestudio_tts import VoiceStudioTTS


class FakeResponse:
    def __init__(self, status=200, body=b"ID3fake-mp3", content_type="audio/mpeg", text=""):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, json, headers):
        self.request = {"url": url, "json": json, "headers": headers}
        return self.response


class VoiceStudioAdapterTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, **overrides):
        config = {
            "language": "hinglish",
            "base_url": "http://voice-internal:3900",
            "api_key": "test-token",
            "voice": "preet-profile-01",
            "model": "omnivoice",
            "timeout": 30,
        }
        config.update(overrides)
        return VoiceStudioTTS(**config)

    def test_configuration_requires_url_key_and_profile(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "VOICESTUDIO_BASE_URL"):
                VoiceStudioTTS()

    def test_rate_mapping_clamps_and_handles_existing_profiles(self):
        self.assertEqual(VoiceStudioTTS._speed_from_rate("+15%"), 1.15)
        self.assertEqual(VoiceStudioTTS._speed_from_rate("-10%"), 0.9)
        self.assertEqual(VoiceStudioTTS._speed_from_rate("+500%"), 4.0)
        self.assertEqual(VoiceStudioTTS._speed_from_rate("-90%"), 0.25)
        self.assertEqual(VoiceStudioTTS._speed_from_rate(None), 1.0)

    async def test_sends_profile_and_returns_binary_mp3(self):
        client = self.make_client()
        session = FakeSession(FakeResponse())
        with patch("aiohttp.ClientSession", return_value=session):
            audio = await client.speak_mp3("FOUR! शानदार शॉट", rate="+15%", pitch="+5Hz")

        self.assertEqual(audio, b"ID3fake-mp3")
        self.assertEqual(session.request["url"], "http://voice-internal:3900/v1/audio/speech")
        self.assertEqual(session.request["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(session.request["json"]["voice"], "preet-profile-01")
        self.assertEqual(session.request["json"]["model"], "omnivoice")
        self.assertEqual(session.request["json"]["language"], "hi")
        self.assertAlmostEqual(session.request["json"]["speed"], 1.15)
        self.assertEqual(session.request["json"]["response_format"], "mp3")

    async def test_accepts_v1_base_url_without_duplicate_prefix(self):
        client = self.make_client(base_url="https://voice.example/v1")
        self.assertEqual(client.speech_url, "https://voice.example/v1/audio/speech")
        self.assertEqual(client.voices_url, "https://voice.example/v1/audio/voices")

    async def test_empty_text_does_not_make_network_request(self):
        client = self.make_client()
        with patch("aiohttp.ClientSession") as session_factory:
            audio = await client.speak_mp3("   ")
        self.assertEqual(audio, b"")
        session_factory.assert_not_called()

    async def test_http_errors_are_reported_without_leaking_token(self):
        client = self.make_client()
        session = FakeSession(FakeResponse(status=401, body=b"", text="unauthorized"))
        with patch("aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as raised:
                await client.speak_mp3("Test line")
        self.assertNotIn("test-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
