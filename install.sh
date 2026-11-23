#!/usr/bin/env bash
# Reproducible installer for whisper-dictate.
# Idempotent: safe to re-run (e.g. after `git pull`).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WD_HOME="$HOME/.local/share/whisper-dictate"
BIN_DIR="$HOME/.local/bin"
SHORTCUT="${SHORTCUT:-}"    # Optional override of the GNOME accelerator
APT_PKGS=(ffmpeg libnotify-bin pulseaudio-utils python3-venv)
case "${XDG_SESSION_TYPE:-}" in
    x11) APT_PKGS+=(xdotool xclip) ;;
    wayland) APT_PKGS+=(ydotool wl-clipboard) ;;
    *) APT_PKGS+=(xdotool xclip ydotool wl-clipboard) ;;
esac

skip_model=false
cpu_only=false
[[ ! -f "$WD_HOME/local-cpu" ]] || cpu_only=true
update_only=false
for arg in "$@"; do
    case "$arg" in
        --no-model) skip_model=true ;;
        --cpu) cpu_only=true ;;
        --gpu) cpu_only=false ;;
        --update) update_only=true ;;
        *) printf 'Usage: %s [--no-model] [--cpu|--gpu] [--update]\n' "$0" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }

install_script() {
    local staging
    staging="$(mktemp "${2}.XXXXXX")"
    if install -m 0755 "$1" "$staging" && mv -fT "$staging" "$2"; then
        return
    fi
    rm -f "$staging"
    return 1
}

install_scripts() {
    install_script "$REPO_DIR/transcribe.py" "$WD_HOME/transcribe.py"
    install_script "$REPO_DIR/bin/whisper-dictate" "$BIN_DIR/whisper-dictate"
    install_script "$REPO_DIR/bin/whisper-dictate-indicator" "$BIN_DIR/whisper-dictate-indicator"
}

if "$update_only"; then
    if [[ ! -x "$WD_HOME/venv/bin/python" || ! -x "$BIN_DIR/whisper-dictate" ]]; then
        warn "No existing install found. Run ./install.sh --no-model first."
        exit 1
    fi
    install_scripts
    say "Scripts updated. Run whisper-dictate --configure gemini to enable cloud dictation."
    exit
fi

# 1) System packages -----------------------------------------------------
missing=()
for p in "${APT_PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
    if command -v apt-get >/dev/null; then
        say "Installing system packages (sudo): ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    else
        warn "Not an apt system. Install equivalents of: ${missing[*]}"
    fi
else
    say "System packages already present."
fi

# /dev/uinput access is required for ydotool to synthesize the paste.
if [[ "${XDG_SESSION_TYPE:-}" != x11 ]] && ! [ -w /dev/uinput ]; then
    warn "/dev/uinput is not writable by you. Add yourself to the 'input'"
    warn "group or add a udev rule, then re-login:"
    warn "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-uinput.rules"
    warn "  sudo usermod -aG input \"$USER\"   # then log out and back in"
fi

# 2) Python venv + faster-whisper ---------------------------------------
say "Creating venv at $WD_HOME/venv"
mkdir -p "$WD_HOME" "$BIN_DIR"
[ -d "$WD_HOME/venv" ] || python3 -m venv "$WD_HOME/venv"
"$WD_HOME/venv/bin/pip" install -q -U pip wheel
if "$cpu_only"; then
    say "Installing faster-whisper for CPU"
    "$WD_HOME/venv/bin/pip" install -q faster-whisper
    touch "$WD_HOME/local-cpu"
    export WD_MODEL="${WD_MODEL:-small}"
    export WD_DEVICE="${WD_DEVICE:-cpu}"
    export WD_COMPUTE="${WD_COMPUTE:-int8}"
else
    say "Installing Python deps (faster-whisper + CUDA libs)"
    "$WD_HOME/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"
    rm -f "$WD_HOME/local-cpu"
fi

# 3) Install the scripts -------------------------------------------------
say "Installing scripts"
install_scripts

# 4) Pre-download the model (skip with --no-model) ----------------------
if ! "$skip_model"; then
    say "Pre-downloading the Whisper model (${WD_MODEL:-large-v3}; once)"
    LD_LIBRARY_PATH="$(echo "$WD_HOME"/venv/lib/python*/site-packages/nvidia/*/lib | tr ' ' ':')${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$WD_HOME/venv/bin/python" - <<'PY'
import os
from faster_whisper import WhisperModel
m = os.environ.get("WD_MODEL", "large-v3")
d = os.environ.get("WD_DEVICE", "cuda")
try:
    WhisperModel(m, device=d, compute_type=os.environ.get("WD_COMPUTE", "auto"))
    print(f"  model '{m}' ready on {d}")
except Exception as e:
    print(f"  GPU load failed ({e!r}); fetching for CPU instead")
    WhisperModel(m, device="cpu", compute_type="int8")
PY
fi

# 5) GNOME keyboard shortcut (idempotent) -------------------------------
if command -v gsettings >/dev/null && [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
    say "Registering GNOME shortcut"
    CMD="$BIN_DIR/whisper-dictate"
    python3 - "$CMD" "$SHORTCUT" <<'PY'
import ast, subprocess, sys
cmd, accel = sys.argv[1], sys.argv[2]
B = "org.gnome.settings-daemon.plugins.media-keys"
base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"

def get(path, key):
    return subprocess.check_output(
        ["gsettings", "get", f"{B}.custom-keybinding:{path}", key]).decode().strip()

def setk(path, key, val):
    subprocess.check_call(["gsettings", "set", f"{B}.custom-keybinding:{path}", key, val])

cur = subprocess.check_output(["gsettings", "get", B, "custom-keybindings"]).decode().strip()
lst = [] if cur in ("@as []", "[]") else ast.literal_eval(cur)

# Reuse an existing slot if we already installed one (idempotent).
slot = None
for p in lst:
    try:
        if ast.literal_eval(get(p, "command")) == cmd:
            slot = p
            if not accel:
                accel = ast.literal_eval(get(p, "binding"))
            break
    except Exception:
        pass
if slot is None:
    i = 0
    while f"{base}/custom{i}/" in lst:
        i += 1
    slot = f"{base}/custom{i}/"
    lst.append(slot)
    subprocess.check_call(["gsettings", "set", B, "custom-keybindings", str(lst)])

accel = accel or "<Control><Alt>d"
setk(slot, "name", "Whisper Dictate")
setk(slot, "command", cmd)
setk(slot, "binding", accel)
print(f"  bound {accel} -> {cmd}  ({slot})")
PY
else
    warn "Not a GNOME session — bind $BIN_DIR/whisper-dictate to a key yourself."
fi

# 6) Top-bar status indicator -------------------------------------------
# The indicator needs an AppIndicator/SNI GNOME extension to be visible.
if command -v gnome-extensions >/dev/null; then
    for ext in ubuntu-appindicators@ubuntu.com appindicatorsupport@rgcjonas.gmail.com; do
        if gnome-extensions list 2>/dev/null | grep -qx "$ext"; then
            gnome-extensions enable "$ext" 2>/dev/null \
                && say "Enabled GNOME extension: $ext" && break
        fi
    done
    gnome-extensions list --enabled 2>/dev/null | grep -qiE 'appindicator' \
        || warn "No AppIndicator extension active — the top-bar icon won't show. Install 'gnome-shell-extension-appindicator' and enable it."
fi

# Autostart on login.
AUTO="$HOME/.config/autostart/whisper-dictate-indicator.desktop"
mkdir -p "$(dirname "$AUTO")"
cat > "$AUTO" <<EOF
[Desktop Entry]
Type=Application
Name=Whisper Dictate Indicator
Comment=Top-bar recording/transcribing status for whisper-dictate
Exec=$BIN_DIR/whisper-dictate-indicator
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

# Start it now (single-instance guarded, so this is safe to repeat).
setsid -f "$BIN_DIR/whisper-dictate-indicator" >/dev/null 2>&1 || true

say "Done. Make sure $BIN_DIR is on your PATH, then press the shortcut to dictate."
