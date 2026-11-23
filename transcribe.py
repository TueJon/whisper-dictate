#!/usr/bin/env python3
"""Transcribe a dictation recording and write only the transcript to stdout."""

import argparse
import base64
from getpass import getpass
from http.client import HTTPException
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


MODELS = {"gemini": "gemini-3.5-transcribe", "openai": "gpt-transcribe"}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
PROVIDERS = ("local", *MODELS)


class CloudError(Exception):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def config_path():
    root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(root) / "whisper-dictate" / "config.json"


def read_config():
    path = config_path()
    try:
        with path.open(encoding="utf-8") as file:
            config = json.load(file)
    except FileNotFoundError:
        return {}
    if not isinstance(config, dict):
        raise ValueError("Expected a JSON object in the dictation configuration.")
    if not all(isinstance(value, str) for value in config.values()):
        raise ValueError("Dictation configuration values must be strings.")
    return config


def configure(provider):
    config = read_config()
    if provider in KEY_NAMES:
        key_name = KEY_NAMES[provider]
        if not sys.stdin.isatty():
            raise ValueError("Run cloud setup in an interactive terminal.")
        key = getpass(f"{key_name} (Enter to keep the current key): ").strip()
        key = key or config.get(key_name) or os.environ.get(key_name)
        if not key:
            raise ValueError(f"A {key_name} is required to enable {provider}.")
        if not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
            raise ValueError("The API key must contain only printable, non-space ASCII.")
        config[key_name] = key
    config["backend"] = provider
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # NamedTemporaryFile creates the replacement with mode 0600.
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        staging = Path(file.name)
        try:
            json.dump(config, file, indent=2)
            file.write("\n")
            file.close()
            staging.replace(path)
        finally:
            staging.unlink(missing_ok=True)
    print(f"Whisper Dictate backend: {provider}")


def post_json(url, headers, body, timeout):
    request = Request(url, data=body, headers={"User-Agent": "Whisper-Dictate/1", **headers}, method="POST")
    try:
        with build_opener(NoRedirects).open(request, timeout=timeout) as response:
            data = json.load(response)
    except HTTPError as exc:
        exc.close()
        # Provider error bodies can contain excerpts of the audio or request.
        raise CloudError(f"HTTP {exc.code}") from None
    except (HTTPException, OSError):
        raise CloudError("network or timeout error") from None
    except (UnicodeError, ValueError):
        raise CloudError("invalid JSON response") from None
    if not isinstance(data, dict):
        raise CloudError("invalid response")
    return data


def gemini_transcribe(audio, key, lang, timeout):
    payload = {
        "model": MODELS["gemini"],
        "input": [{"type": "audio", "mime_type": "audio/wav",
                   "data": base64.b64encode(audio).decode("ascii")}],
        "store": False,
    }
    if lang:
        payload["generation_config"] = {"transcription_config": {"language_codes": [lang]}}
    result = post_json(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        {"x-goog-api-key": key, "Content-Type": "application/json", "Api-Revision": "2026-05-20"},
        json.dumps(payload).encode("utf-8"), timeout,
    )
    if result.get("status") != "completed":
        raise CloudError("incomplete transcription")
    try:
        return "".join(
            part["text"] for step in result["steps"] if step["type"] == "model_output"
            for part in step["content"] if part["type"] == "text"
        )
    except (KeyError, TypeError):
        raise CloudError("invalid transcription response") from None


def openai_transcribe(audio, key, lang, timeout):
    boundary = uuid4().hex
    fields = {"model": MODELS["openai"]}
    if lang:
        fields["languages[]"] = lang
    parts = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
         f'{value}\r\n').encode("utf-8")
        for name, value in fields.items()
    ]
    parts.extend((
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.wav"\r\n'
         'Content-Type: audio/wav\r\n\r\n').encode("ascii"),
        audio,
        f"\r\n--{boundary}--\r\n".encode("ascii"),
    ))
    result = post_json(
        "https://api.openai.com/v1/audio/transcriptions",
        {"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        b"".join(parts), timeout,
    )
    if not isinstance(result.get("text"), str):
        raise CloudError("invalid transcription response")
    return result["text"]


def local_transcribe(audio, lang):
    from faster_whisper import WhisperModel

    model_name = os.environ.get("WD_MODEL", "large-v3")
    beam = int(os.environ.get("WD_BEAM", "1"))
    device = os.environ.get("WD_DEVICE", "cuda")
    compute = os.environ.get("WD_COMPUTE", "auto")

    def run(device, compute):
        model = WhisperModel(model_name, device=device, compute_type=compute)
        segments, _ = model.transcribe(
            str(audio), language=lang, beam_size=beam, vad_filter=True,
            condition_on_previous_text=False,
        )
        return "".join(segment.text for segment in segments)

    try:
        return run(device, compute)
    except Exception as exc:
        if device == "cpu" and compute == "int8":
            raise
        print(f"[whisper-dictate] {device} path failed: {exc!r}. Falling back to CPU.", file=sys.stderr)
        return run("cpu", "int8")


def report_fallback(provider, error):
    detail = f"{provider}: {error}. Falling back to local Whisper."
    print(f"[whisper-dictate] {detail}", file=sys.stderr)
    if os.environ.get("WD_DESKTOP") == "1":
        try:
            subprocess.run(
                ["notify-send", "-a", "Whisper Dictate", "Cloud dictation unavailable", detail],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def transcribe(audio):
    config = read_config()
    provider = os.environ.get("WD_BACKEND", config.get("backend", "local"))
    lang = os.environ.get("WD_LANG", "").strip() or None
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown backend. Choose one of: {', '.join(PROVIDERS)}")
    if provider != "local":
        key_name = KEY_NAMES[provider]
        key = os.environ.get(key_name) or config.get(key_name)
        limit = 14_000_000 if provider == "gemini" else 25_000_000
        try:
            if not key:
                raise CloudError(f"{key_name} is missing")
            size = audio.stat().st_size
            if size >= limit:
                raise CloudError("recording exceeds the cloud upload limit")
            # The recorder writes 32 KB/s. Allow extra time for longer clips.
            default_timeout = min(120, max(15, size / 64_000))
            timeout = float(os.environ.get("WD_CLOUD_TIMEOUT", default_timeout))
            if not 0 < timeout <= 300:
                raise ValueError("WD_CLOUD_TIMEOUT must be between 0 and 300 seconds.")
            backend = gemini_transcribe if provider == "gemini" else openai_transcribe
            return backend(audio.read_bytes(), key, lang, timeout)
        except CloudError as exc:
            report_fallback(provider, exc)
    return local_transcribe(audio, lang)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?", type=Path)
    parser.add_argument("--configure", choices=PROVIDERS, metavar="BACKEND",
                        help="Choose gemini, openai or local and save it for the desktop shortcut.")
    args = parser.parse_args()
    if bool(args.audio) == bool(args.configure):
        parser.error("provide one audio file or use --configure BACKEND")
    try:
        if args.configure:
            configure(args.configure)
        else:
            sys.stdout.write(transcribe(args.audio).strip())
    except (ValueError, OSError) as exc:
        parser.exit(1, f"whisper-dictate: {exc}\n")


if __name__ == "__main__":
    main()
