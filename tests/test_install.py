from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest


REPO = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_cpu_preset_survives_update_and_reinstall_and_can_be_changed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            bin_dir = root / "bin"
            venv_bin = app / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            bin_dir.mkdir()
            install = root / "install.sh"
            install.write_text(
                (REPO / "install.sh").read_text()
                .replace('WD_HOME="$HOME/.local/share/whisper-dictate"', f'WD_HOME="{app}"')
                .replace('BIN_DIR="$HOME/.local/bin"', f'BIN_DIR="{bin_dir}"')
                .replace('AUTO="$HOME/.config/autostart/whisper-dictate-indicator.desktop"',
                         f'AUTO="{root}/autostart/whisper-dictate-indicator.desktop"')
            )
            (root / "transcribe.py").symlink_to(REPO / "transcribe.py")
            for program in ("whisper-dictate", "whisper-dictate-indicator"):
                (bin_dir / program).symlink_to(REPO / "bin" / program)

            def stub(path, command):
                path.write_text("#!/bin/sh\n" + command + "\n")
                path.chmod(0o755)

            stub(venv_bin / "python", "exit 0")
            stub(venv_bin / "pip", 'printf "%s\\n" "$*" >> "$WD_TEST_LOG"')
            for program in ("dpkg", "setsid", "gnome-extensions"):
                stub(bin_dir / program, "exit 0")

            env = os.environ | {
                "PATH": f'{bin_dir}:{os.environ["PATH"]}', "WD_TEST_LOG": str(root / "pip.log"),
                "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "",
            }

            def run(*args):
                subprocess.run(["bash", str(install), *args], env=env, check=True,
                               capture_output=True, timeout=5)
                return (root / "pip.log").read_text()

            self.assertIn("install -q faster-whisper", run("--cpu", "--no-model"))
            self.assertTrue((app / "local-cpu").exists())
            before_update = (root / "pip.log").read_text()
            self.assertEqual(run("--update"), before_update)
            self.assertTrue((app / "local-cpu").exists())
            self.assertIn("install -q faster-whisper", run("--no-model").removeprefix(before_update))
            self.assertTrue((app / "local-cpu").exists())
            self.assertIn("install -q -r", run("--gpu", "--no-model"))
            self.assertFalse((app / "local-cpu").exists())
