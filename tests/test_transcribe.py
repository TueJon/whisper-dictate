import base64
from email import policy
from email.parser import BytesParser
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import transcribe


class CloudTranscriptionTests(unittest.TestCase):
    def test_gemini_sends_inline_audio_and_reads_model_text(self):
        response = {
            "status": "completed",
            "steps": [
                {"type": "thought", "content": [{"type": "text", "text": "hidden"}]},
                {"type": "model_output", "content": [{"type": "text", "text": "Grüß dich."}]},
            ],
        }
        with patch.object(transcribe, "post_json", return_value=response) as post:
            self.assertEqual(transcribe.gemini_transcribe(b"audio", "key", "de", 15), "Grüß dich.")
        url, headers, body, timeout = post.call_args.args
        self.assertTrue(url.endswith("/v1beta/interactions"))
        self.assertEqual(headers["x-goog-api-key"], "key")
        self.assertEqual(headers["Api-Revision"], "2026-05-20")
        self.assertEqual(timeout, 15)
        payload = json.loads(body)
        self.assertEqual(payload["model"], "gemini-3.5-transcribe")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["generation_config"]["transcription_config"]["language_codes"], ["de"])
        self.assertEqual(base64.b64decode(payload["input"][0]["data"]), b"audio")

    def test_openai_sends_audio_and_language_as_multipart(self):
        with patch.object(transcribe, "post_json", return_value={"text": "Servus."}) as post:
            self.assertEqual(transcribe.openai_transcribe(b"audio", "key", "de", 15), "Servus.")
        url, headers, body, _ = post.call_args.args
        self.assertTrue(url.endswith("/v1/audio/transcriptions"))
        self.assertEqual(headers["Authorization"], "Bearer key")
        message = BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {headers["Content-Type"]}\r\n\r\n'.encode() + body
        )
        fields = {part.get_param("name", header="Content-Disposition"): part for part in message.iter_parts()}
        self.assertEqual(fields["model"].get_content(), "gpt-transcribe")
        self.assertEqual(fields["languages[]"].get_content(), "de")
        self.assertEqual(fields["file"].get_payload(decode=True), b"audio")

    def test_cloud_failure_uses_the_same_recording_locally(self):
        with TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": directory, "WD_BACKEND": "gemini", "GEMINI_API_KEY": "key"},
            clear=True,
        ):
            audio = Path(directory) / "recording.wav"
            audio.write_bytes(b"audio")
            with patch.object(transcribe, "gemini_transcribe", side_effect=transcribe.CloudError("HTTP 429")), \
                 patch.object(transcribe, "local_transcribe", return_value="lokal") as local, \
                 patch("sys.stderr", new_callable=io.StringIO) as log:
                self.assertEqual(transcribe.transcribe(audio), "lokal")
            local.assert_called_once_with(audio, None)
            self.assertIn("HTTP 429", log.getvalue())

    def test_default_stays_local_even_with_a_cloud_key(self):
        with TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": directory, "OPENAI_API_KEY": "key"}, clear=True,
        ), patch.object(transcribe, "local_transcribe", return_value="lokal") as local:
            audio = Path(directory) / "recording.wav"
            self.assertEqual(transcribe.transcribe(audio), "lokal")
            local.assert_called_once_with(audio, None)

    def test_configure_saves_key_and_provider_with_private_permissions(self):
        with TemporaryDirectory() as directory, patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": directory}, clear=True,
        ), patch("sys.stdin.isatty", return_value=True), patch.object(
            transcribe, "getpass", return_value="test-key",
        ), patch("sys.stdout", new_callable=io.StringIO):
            transcribe.configure("gemini")
            self.assertEqual(transcribe.config_path().stat().st_mode & 0o777, 0o600)
            self.assertEqual(transcribe.read_config(), {"backend": "gemini", "GEMINI_API_KEY": "test-key"})
            transcribe.configure("local")
            self.assertEqual(transcribe.read_config()["backend"], "local")
