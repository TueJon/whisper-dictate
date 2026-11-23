from pathlib import Path
import os
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest


REPO = Path(__file__).resolve().parents[1]


class ToggleTests(unittest.TestCase):
    def test_second_press_during_recorder_exec_stops_the_recording(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            runtime = root / "runtime"
            state = runtime / "whisper-dictate"
            app.mkdir()
            wrapper = root / "whisper-dictate"
            wrapper.write_text(
                (REPO / "bin/whisper-dictate").read_text().replace(
                    '$HOME/.local/share/whisper-dictate', str(app)
                )
            )
            recorder = root / "recorder.py"
            recorder.write_text(
                "import signal, sys, time\n"
                "from pathlib import Path\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
                "Path(sys.argv[-1]).write_bytes(b'audio')\n"
                "while True: time.sleep(0.1)\n"
            )
            hooks = root / "hooks.bash"
            hooks.write_text(
                "ffmpeg() {\n"
                "    printf '%s' \"$BASHPID\" > \"$WD_TEST_ROOT/pre-exec.tmp\"\n"
                "    mv \"$WD_TEST_ROOT/pre-exec.tmp\" \"$WD_TEST_ROOT/pre-exec\"\n"
                "    while [[ ! -f \"$WD_TEST_ROOT/release\" ]]; do sleep 0.01; done\n"
                "    exec -a ffmpeg \"$WD_TEST_PYTHON\" \"$WD_TEST_ROOT/recorder.py\" \"$@\"\n"
                "}\n"
                "flock() {\n"
                "    if [[ $1 == -w ]]; then touch \"$WD_TEST_ROOT/stop-waiting\"; fi\n"
                "    command flock \"$@\"\n"
                "}\n"
                "notify-send() { :; }\n"
                "paplay() { :; }\n"
            )
            venv_bin = app / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            python = venv_bin / "python"
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            env = os.environ | {
                "BASH_ENV": str(hooks), "WD_TEST_ROOT": str(root),
                "WD_TEST_PYTHON": sys.executable, "XDG_RUNTIME_DIR": str(runtime),
            }

            def wait_for(path):
                deadline = time.monotonic() + 3
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(path.exists(), path.name)

            start = subprocess.Popen(["bash", str(wrapper)], env=env)
            stop = None
            pid = None
            try:
                wait_for(root / "pre-exec")
                pid = int((root / "pre-exec").read_text())
                self.assertFalse((state / "rec.pid").exists())
                stop = subprocess.Popen(["bash", str(wrapper)], env=env)
                wait_for(root / "stop-waiting")
                (root / "release").touch()
                self.assertEqual(start.wait(timeout=4), 0)
                self.assertEqual(stop.wait(timeout=9), 0)
                self.assertFalse((state / "rec.pid").exists())
                self.assertFalse((state / "rec.wav").exists())
                self.assertEqual((state / "state").read_text(), "idle")
                # An init that delays reaping can leave the process as a zombie.
                cmdline = Path(f"/proc/{pid}/cmdline")
                if cmdline.exists():
                    self.assertEqual(cmdline.read_bytes(), b"")
            finally:
                (root / "release").touch()
                for process in (start, stop):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                if pid is not None:
                    try:
                        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                        if os.fsencode(root) in cmdline:
                            os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except FileNotFoundError:
                        pass
