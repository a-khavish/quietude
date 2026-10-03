#!/usr/bin/env bash
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# install.sh - set Quietude up on this machine, or take her off it again.
#
#   ./install.sh                  full install
#   ./install.sh --uninstall      remove everything installed, keep the profile
#   ./install.sh --uninstall --purge-data   ...and erase the profile too
#   ./install.sh --help           every flag
#
# This replaces the Dockerfile and compose file the rebuild originally
# called for. Running natively turned out to be the better fit for this
# particular application, and the reason is the microphone: in Docker,
# giving a container access to the mic means passing through /dev/snd or
# bind-mounting the PipeWire socket plus its cookie - host-specific,
# broken differently on each distro, and the part of the setup most
# likely to leave someone with a container that runs but can't hear
# anything. Natively there is nothing to pass through, because the
# browser holds the mic, as it already did for the webcam.
#
# What a container would still have bought is dependency isolation.
# That's handled here by a virtualenv for Python - nothing is installed
# into the system interpreter - and by only asking the package manager
# for libraries that genuinely must be system-wide. Nothing outside the
# prefix, the XDG directories and that one package-manager call is
# touched, and --uninstall reverses the first two exactly.
#
# A note on the rm calls below: every one writes its variables as
# "${VAR:?}" rather than "$VAR". If a variable is somehow unset or
# empty, that makes bash abort with an error instead of expanding to a
# path like "/" and removing something catastrophic. An uninstaller is
# exactly the wrong place to find out a variable was empty.

set -euo pipefail

# ============================================================
# Paths and constants
# ============================================================

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="${QUIETUDE_PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"
VENV_DIR="${QUIETUDE_VENV:-$HOME/.local/share/quietude/venv}"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/quietude"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/quietude"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}/quietude"

MODELS_DIR="$DATA_HOME/models"
VOSK_DIR="$MODELS_DIR/vosk"
WHISPER_DIR="$MODELS_DIR/whisper"
PIPER_DIR="$MODELS_DIR/piper"

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
DESKTOP_DIR="$PREFIX/share/applications"
ICON_THEME_DIR="$PREFIX/share/icons/hicolor"
ICON_DIR="$ICON_THEME_DIR/scalable/apps"

# Sizes shipped as PNGs. Desktops pick a raster icon over an SVG at
# small sizes, and a panel or dock asked to scale a 512px SVG down to
# 22px gets mush - so the small sizes come from a simplified version of
# the mark with the rings dropped. See branding/README.md.
ICON_PNG_SIZES=(16 22 24 32 48 64 128 256 512)

LAUNCHER="$BIN_DIR/quietude"
UNIT_FILE="$SYSTEMD_USER_DIR/quietude.service"
DESKTOP_FILE="$DESKTOP_DIR/quietude.desktop"
ICON_FILE="$ICON_DIR/quietude.svg"
# The file the desktop entries point at directly. See
# write_desktop_entry for why that is a path and not a theme name.
ICON_DOCK_FILE="$ICON_THEME_DIR/256x256/apps/quietude.png"
ICON_INDEX="$ICON_THEME_DIR/index.theme"

# The two WM_CLASS fields Quietude's window reports. These MUST match
# WM_INSTANCE and WM_CLASS in backend/quietude/desktop.py, which explain at
# length why there are two of them and why the first one is a hostname.
#
# WM_CLASS has an instance field and a class field, and desktops
# disagree about which one they match a window to an application by -
# GNOME tries the instance then the class, most docks match the instance
# only. There used to be two .desktop files here, one per field, because
# the browser window took its instance from the address it opened and
# could not be told otherwise.
#
# The Electron window sets both itself, and sets them to the same pair
# the GTK window used to, so one entry matching the instance covers every
# desktop. See WM_INSTANCE and WM_CLASS in backend/quietude/desktop.py.
WM_CLASS_CLASS="quietude"

# Written into any index.theme this script creates, so --uninstall can
# tell a file it wrote from one that was already there.
ICON_INDEX_MARKER="# Created by Quietude's install.sh"
BRANDING_DIR="$REPO_DIR/branding"

FONT_DIR="$REPO_DIR/frontend/public/fonts"

# Models. Pinned by name so an install is reproducible, and downloaded
# rather than bundled - together they're a few hundred MB, which has no
# business in a git repo.
VOSK_MODEL="vosk-model-small-en-us-0.15"
VOSK_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL}.zip"

# base.en rather than small or medium: on CPU int8 it transcribes a short
# command in well under a second on modest hardware, and these are fixed
# commands rather than dictation. Override with QUIETUDE_WHISPER_MODEL for
# more accuracy at the cost of latency.
WHISPER_REPO="${QUIETUDE_WHISPER_MODEL:-Systran/faster-whisper-base.en}"

PIPER_VOICE="${QUIETUDE_PIPER_VOICE:-en_US-amy-medium}"
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium"

FONT_BASE="https://cdn.jsdelivr.net/fontsource/fonts"
declare -a FONTS=(
  "orbitron-700.woff2|$FONT_BASE/orbitron@latest/latin-700-normal.woff2"
  "orbitron-900.woff2|$FONT_BASE/orbitron@latest/latin-900-normal.woff2"
  "jetbrains-mono-400.woff2|$FONT_BASE/jetbrains-mono@latest/latin-400-normal.woff2"
  "jetbrains-mono-700.woff2|$FONT_BASE/jetbrains-mono@latest/latin-700-normal.woff2"
)

# ============================================================
# Output
# ============================================================

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_CYAN=$'\033[36m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_GREEN=$'\033[32m'
else
  C_RESET=""; C_CYAN=""; C_DIM=""; C_RED=""; C_YELLOW=""; C_GREEN=""
fi

STEP=0
step()  { STEP=$((STEP + 1)); printf '\n%s[%d]%s %s\n' "$C_CYAN" "$STEP" "$C_RESET" "$1"; }
info()  { printf '    %s\n' "$1"; }
dim()   { printf '    %s%s%s\n' "$C_DIM" "$1" "$C_RESET"; }
warn()  { printf '    %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
ok()    { printf '    %s+%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
die()   { printf '\n%serror:%s %s\n\n' "$C_RED" "$C_RESET" "$1" >&2; exit 1; }

# ============================================================
# Flags
# ============================================================

DO_UNINSTALL=0
PURGE_DATA=0
SKIP_MODELS=0
SKIP_FONTS=0
SKIP_SYSTEM_DEPS=0
SKIP_SERVICE=0
MODELS_ONLY=0
FONTS_ONLY=0
DESKTOP_ONLY=0
DO_DOCTOR=0
ASSUME_YES=0

# Set if the optional voice stack wouldn't install, so the closing
# message can say so instead of claiming an install that can hear you.
VOICE_INSTALL_FAILED=0

usage() {
  cat <<USAGE
Quietude installer

  ./install.sh [options]

Install options
  --no-models         Skip the speech and voice models. Quietude installs and
                      text chat works; voice doesn't until you run
                      --models-only.
  --no-fonts          Skip the two webfonts. The interface falls back to
                      your system monospace.
  --no-system-deps    Don't invoke the package manager. Use if you've
                      installed the system libraries yourself, or have no
                      sudo. The script prints what it needs.
  --no-service        Don't install the systemd user service or desktop
                      entry. Just the venv and the 'quietude' command.
  --models-only       Only (re)download models into an existing install.
  --fonts-only        Only (re)download the webfonts.
  --desktop-only      Only reinstall the icon and the desktop entries and
                      refresh the desktop's caches. Use this if the dock
                      or menu is showing the wrong icon or name - it
                      needs no rebuild and no downloads.

Diagnostics
  --doctor            Print what your desktop can actually see of Quietude:
                      which entries are installed, whether they validate,
                      whether the icon resolves, and what WM_CLASS her
                      window will report. Changes nothing.
  -y, --yes           Don't prompt for confirmation.

Removal options
  --uninstall         Remove the venv, launcher, service and desktop
                      entry. Your profile, face model and downloaded
                      models are kept - see --purge-data.
  --purge-data        With --uninstall, also erase everything under
                      $DATA_HOME,
                      $CONFIG_HOME and
                      $CACHE_HOME.
                      This destroys your account, encryption key and face
                      model. It cannot be undone.

Environment
  QUIETUDE_PREFIX         Install prefix (default: \$HOME/.local)
  QUIETUDE_VENV           Virtualenv location
  QUIETUDE_ELECTRON_FLAGS Extra Chromium switches for the window, e.g.
                          '--disable-gpu' or '--ozone-platform=wayland'
  QUIETUDE_WHISPER_MODEL  HuggingFace repo for faster-whisper
                      (default: $WHISPER_REPO)
  QUIETUDE_PIPER_VOICE    piper voice to install at first run
                      (default: $PIPER_VOICE). More can be added later
                      from inside Quietude - say "voice library".
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)       DO_UNINSTALL=1 ;;
    --purge-data)      PURGE_DATA=1 ;;
    --no-models)       SKIP_MODELS=1 ;;
    --no-fonts)        SKIP_FONTS=1 ;;
    --no-system-deps)  SKIP_SYSTEM_DEPS=1 ;;
    --no-service)      SKIP_SERVICE=1 ;;
    --models-only)     MODELS_ONLY=1 ;;
    --fonts-only)      FONTS_ONLY=1 ;;
    --desktop-only)    DESKTOP_ONLY=1 ;;
    --doctor)          DO_DOCTOR=1 ;;
    -y|--yes)          ASSUME_YES=1 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 die "unknown option: $1  (try --help)" ;;
  esac
  shift
done

if [[ $PURGE_DATA -eq 1 && $DO_UNINSTALL -eq 0 ]]; then
  die "--purge-data only makes sense together with --uninstall"
fi

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  local prompt="$1" answer
  # Read from the terminal, not stdin: this script may be piped, and a
  # destructive prompt must never be answered by whatever is on stdin.
  if [[ ! -t 0 && ! -r /dev/tty ]]; then
    die "need confirmation but no terminal is attached - re-run with --yes if you're sure"
  fi
  printf '\n%s [y/N] ' "$prompt"
  read -r answer < /dev/tty
  [[ "$answer" =~ ^[Yy]$ ]]
}

# ============================================================
# Uninstall
# ============================================================

do_uninstall() {
  printf '\n%sRemoving Quietude%s\n' "$C_CYAN" "$C_RESET"

  info "This will remove:"
  dim "  $VENV_DIR"
  dim "  $LAUNCHER"
  dim "  $UNIT_FILE"
  dim "  $DESKTOP_FILE"
  dim "  $ICON_FILE  (and the PNG icon sizes)"
  [[ -d "$REPO_DIR/electron/node_modules" ]] \
    && dim "  $REPO_DIR/electron/node_modules  (Electron)"

  if [[ $PURGE_DATA -eq 1 ]]; then
    printf '\n    %s!%s And it will ERASE, irreversibly:\n' "$C_RED" "$C_RESET"
    dim "  $DATA_HOME"
    dim "    your profile, encryption key, face model and downloaded models"
    dim "  $CONFIG_HOME"
    dim "  $CACHE_HOME"
    confirm "Erase Quietude AND all of her data? This cannot be undone." \
      || die "cancelled - nothing was removed"
  else
    printf '\n'
    info "Your profile and models are being kept:"
    dim "  $DATA_HOME"
    dim "  (add --purge-data to erase them too)"
    confirm "Remove Quietude?" || die "cancelled - nothing was removed"
  fi

  step "Stopping the service"
  if command -v systemctl >/dev/null 2>&1; then
    # Stop before disable: disabling a running unit leaves it running.
    systemctl --user stop quietude.service 2>/dev/null || true
    systemctl --user disable quietude.service 2>/dev/null || true
    ok "service stopped and disabled"
  else
    dim "no systemctl - nothing to stop"
  fi

  step "Removing installed files"
  for path in "$LAUNCHER" "$UNIT_FILE" "$DESKTOP_FILE" \
              "$ICON_FILE"; do
    if [[ -e "$path" ]]; then
      rm -f -- "${path:?}"
      ok "removed $path"
    fi
  done

  # Each PNG size is removed individually rather than by deleting any
  # directory: hicolor/48x48/apps is shared with every other application
  # on the system.
  local removed_icons=0
  for size in "${ICON_PNG_SIZES[@]}"; do
    local png="$ICON_THEME_DIR/${size}x${size}/apps/quietude.png"
    if [[ -f "$png" ]]; then
      rm -f -- "${png:?}"
      removed_icons=$((removed_icons + 1))
    fi
  done
  [[ $removed_icons -gt 0 ]] && ok "removed $removed_icons PNG icon sizes"

  # Only an index.theme this script wrote, identified by its own marker.
  # If the user had one already it describes their other icons too, and
  # deleting it would break them.
  if [[ -f "$ICON_INDEX" ]] && head -1 "$ICON_INDEX" | grep -qF "$ICON_INDEX_MARKER"; then
    rm -f -- "${ICON_INDEX:?}"
    ok "removed $ICON_INDEX"
  fi

  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -f -t "$ICON_THEME_DIR" 2>/dev/null || true
  fi

  if [[ -d "$VENV_DIR" ]]; then
    # Guard against QUIETUDE_VENV having been pointed somewhere unfortunate.
    # Every virtualenv has a pyvenv.cfg; a home directory does not.
    if [[ -f "$VENV_DIR/pyvenv.cfg" ]]; then
      rm -rf -- "${VENV_DIR:?}"
      ok "removed $VENV_DIR"
    else
      warn "$VENV_DIR doesn't look like a virtualenv (no pyvenv.cfg) - left alone"
    fi
  fi

  # Electron lives in the repository rather than under the prefix, so it
  # is removed here rather than with the installed files above.
  if [[ -d "$REPO_DIR/electron/node_modules" ]]; then
    rm -rf -- "$REPO_DIR/electron/node_modules"
    ok "removed Electron"
  fi

  command -v systemctl >/dev/null 2>&1 && { systemctl --user daemon-reload 2>/dev/null || true; }
  command -v update-desktop-database >/dev/null 2>&1 && {
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  }

  if [[ $PURGE_DATA -eq 1 ]]; then
    step "Erasing data"
    for dir in "$DATA_HOME" "$CONFIG_HOME" "$CACHE_HOME"; do
      if [[ -d "$dir" ]]; then
        rm -rf -- "${dir:?}"
        ok "erased $dir"
      fi
    done
  fi

  printf '\n%sQuietude has been removed.%s\n' "$C_GREEN" "$C_RESET"
  if [[ $PURGE_DATA -eq 0 && -d "$DATA_HOME" ]]; then
    dim "Your profile and models are still at $DATA_HOME."
    dim "Reinstalling picks up exactly where you left off."
    dim "To erase them: ./install.sh --uninstall --purge-data"
  fi
  printf '\n'
  dim "The repo itself is untouched - delete this directory to finish."
  printf '\n'
}

if [[ $DO_UNINSTALL -eq 1 ]]; then
  do_uninstall
  exit 0
fi

# ============================================================
# Preflight
# ============================================================

require_linux() {
  [[ "$(uname -s)" == "Linux" ]] \
    || die "Quietude is Linux-only. This looks like $(uname -s)."
}

detect_pm() {
  for pm in apt-get dnf pacman zypper apk; do
    if command -v "$pm" >/dev/null 2>&1; then
      echo "$pm"
      return
    fi
  done
  echo "unknown"
}

# System packages, per manager. Deliberately short - everything that can
# live in the virtualenv does.
#
#   python3 venv/dev   to build the virtualenv and any wheel needing a
#                      compiler
#   nodejs + npm       to build the interface, and to fetch the window.
#                      Also what Electron is installed with.
#
# Notably absent: anything audio-related beyond espeak. No ALSA headers,
# no PulseAudio or PipeWire libraries, no portaudio - because the backend
# never opens an audio device. That absence is the clearest single
# consequence of keeping capture and playback in the browser.
packages_for() {
  case "$1" in
    apt-get) echo "python3 python3-venv python3-dev nodejs npm espeak-ng libgomp1 libgl1 curl unzip" ;;
    dnf)     echo "python3 python3-devel nodejs npm espeak-ng libgomp mesa-libGL curl unzip" ;;
    pacman)  echo "python python-virtualenv nodejs npm espeak-ng libglvnd curl unzip" ;;
    zypper)  echo "python3 python3-devel nodejs npm espeak-ng libgomp1 Mesa-libGL1 curl unzip" ;;
    apk)     echo "python3 python3-dev nodejs npm espeak-ng libgomp mesa-gl curl unzip" ;;
    *)       echo "" ;;
  esac
}

install_cmd_for() {
  case "$1" in
    apt-get) echo "sudo apt-get install -y" ;;
    dnf)     echo "sudo dnf install -y" ;;
    pacman)  echo "sudo pacman -S --needed --noconfirm" ;;
    zypper)  echo "sudo zypper install -y" ;;
    apk)     echo "sudo apk add" ;;
    *)       echo "" ;;
  esac
}

install_system_deps() {
  step "System packages"

  local pm; pm="$(detect_pm)"
  if [[ "$pm" == "unknown" ]]; then
    warn "couldn't identify your package manager"
    info "Install these yourself, then re-run with --no-system-deps:"
    dim "  python3 (with venv + headers), nodejs, npm, espeak-ng, libgomp, libGL, curl, unzip"
    confirm "Continue anyway?" || die "cancelled"
    return
  fi

  local packages cmd
  packages="$(packages_for "$pm")"
  cmd="$(install_cmd_for "$pm")"

  info "Detected $pm. Quietude needs:"
  dim "  $packages"

  if ! command -v sudo >/dev/null 2>&1; then
    warn "sudo isn't available - can't install system packages"
    info "Install the above as root, then re-run with --no-system-deps"
    confirm "Continue without them?" || die "cancelled"
    return
  fi

  info "Running: $cmd $packages"
  if [[ "$pm" == "apt-get" ]]; then
    sudo apt-get update -qq || warn "apt-get update failed - continuing with the current index"
  fi

  # shellcheck disable=SC2086
  if $cmd $packages; then
    ok "system packages installed"
  else
    warn "the package manager reported a problem"
    info "If these are already present under other names, re-run with --no-system-deps"
    confirm "Continue anyway?" || die "cancelled"
  fi
}

check_tools() {
  step "Checking what's available"

  local missing=()
  command -v python3 >/dev/null 2>&1 || missing+=("python3")
  command -v curl    >/dev/null 2>&1 || missing+=("curl")
  command -v unzip   >/dev/null 2>&1 || missing+=("unzip")
  if [[ $FONTS_ONLY -eq 0 && $MODELS_ONLY -eq 0 ]]; then
    command -v npm   >/dev/null 2>&1 || missing+=("npm")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    die "missing required tools: ${missing[*]}
    Install them and re-run, or run without --no-system-deps to let this script try."
  fi

  local py_version
  py_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    die "Python 3.10 or newer is required (found $py_version)"
  fi
  ok "python $py_version"

  # Checked here rather than discovered at the build, which happens
  # after several hundred megabytes of models have downloaded - and
  # fails with a Vite syntax error that says nothing about Node. Several
  # distros' own `nodejs` package is far too old: Ubuntu 22.04 and
  # Debian 11 both ship Node 12, which this script would have just
  # installed itself.
  if [[ $MODELS_ONLY -eq 0 && $FONTS_ONLY -eq 0 ]] && command -v node >/dev/null 2>&1; then
    local node_major
    node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [[ "$node_major" -lt 18 ]]; then
      die "Node 18 or newer is needed to build the interface (found $(node --version)).
    Your distribution's 'nodejs' package is too old. Install a current Node
    from https://nodejs.org, nodesource, nvm or fnm, then re-run this script
    with --no-system-deps."
    fi
    ok "node $(node --version)"
  fi

  command -v npm >/dev/null 2>&1 && ok "npm $(npm --version)"

  if command -v espeak-ng >/dev/null 2>&1; then
    ok "espeak-ng (fallback voice available)"
  else
    warn "espeak-ng not found - Quietude stays silent until a piper voice is installed"
  fi

  python3 -c 'import venv' 2>/dev/null \
    || die "python3's venv module is missing. On Debian/Ubuntu: sudo apt-get install python3-venv"
}

# ============================================================
# Download helper
# ============================================================

# curl with the flags that matter for an unattended install: fail on an
# HTTP error rather than saving the error page, follow redirects (all of
# these hosts redirect to a CDN), retry a few times, and write to a temp
# file so an interrupted download never leaves a truncated file that
# looks complete.
fetch() {
  local url="$1" dest="$2" label="${3:-$(basename "$dest")}"
  local tmp="${dest}.part"

  if curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$tmp" "$url"; then
    mv -f "$tmp" "$dest"
    return 0
  fi
  rm -f -- "${tmp:?}"
  warn "couldn't download $label"
  dim "  $url"
  return 1
}

# ============================================================
# Install steps
# ============================================================

make_dirs() {
  step "Creating directories"
  mkdir -p "$DATA_HOME" "$CONFIG_HOME" "$CACHE_HOME" \
           "$MODELS_DIR" "$VOSK_DIR" "$WHISPER_DIR" "$PIPER_DIR" \
           "$BIN_DIR" "$DATA_HOME/faces" "$DATA_HOME/uploads"
  # The profile, the encryption key and the face model all live here.
  chmod 700 "$DATA_HOME"
  ok "data:   $DATA_HOME  (mode 700)"
  ok "config: $CONFIG_HOME"
  ok "cache:  $CACHE_HOME"
}

build_venv() {
  step "Python environment"

  if [[ -f "$VENV_DIR/pyvenv.cfg" ]]; then
    info "reusing the existing virtualenv at $VENV_DIR"
  else
    info "creating a virtualenv at $VENV_DIR"
    python3 -m venv "$VENV_DIR" || die "couldn't create the virtualenv"
  fi

  local pip="$VENV_DIR/bin/pip"
  info "upgrading pip"
  "$pip" install --quiet --upgrade pip setuptools wheel \
    || warn "couldn't upgrade pip - continuing with what's there"

  # Core first, and a failure here is fatal - without these Quietude can't
  # start at all.
  info "installing core dependencies (the slow part - a few minutes)"
  if ! "$pip" install --quiet -r "$REPO_DIR/backend/requirements.txt"; then
    die "core dependency installation failed.
    Run this to see the full error:
      $pip install -r $REPO_DIR/backend/requirements.txt
    A package needing a compiler or headers is the usual cause - re-run
    without --no-system-deps, or install the missing system library."
  fi
  ok "core dependencies installed"

  # Voice second, and best-effort on purpose. Speech recognition and
  # synthesis are the parts most likely to have no wheel for a very new
  # interpreter, and none of them are needed for text chat, face login,
  # the account flow or the interface. Aborting the whole install over
  # them - which is what a single requirements file did - threw away
  # everything that *did* work. So this warns and carries on, and Quietude
  # reports voice as unavailable at runtime rather than pretending.
  info "installing the voice stack (speech recognition and synthesis)"
  if "$pip" install --quiet -r "$REPO_DIR/backend/requirements-voice.txt"; then
    ok "voice stack installed"
  else
    VOICE_INSTALL_FAILED=1
    warn "the voice stack couldn't be installed"
    dim "  Quietude will install and work normally for text chat; voice features"
    dim "  will be unavailable and she'll say so rather than pretending to listen."
    dim "  To see why:"
    dim "    $pip install -r $REPO_DIR/backend/requirements-voice.txt"
  fi

  # Editable, so the repo stays the source of truth: edit a file in
  # backend/ and the next launch picks it up, with no reinstall step to
  # forget.
  "$pip" install --quiet -e "$REPO_DIR/backend" || die "couldn't install the quietude package"
  ok "quietude installed (editable - edits in backend/ take effect immediately)"
}

download_vosk() {
  if [[ -d "$VOSK_DIR/am" ]] || [[ -f "$VOSK_DIR/README" ]]; then
    ok "wake-word model already present"
    return 0
  fi

  info "wake-word model ($VOSK_MODEL, ~40 MB)"
  local zip="$CACHE_HOME/${VOSK_MODEL}.zip"
  fetch "$VOSK_URL" "$zip" "$VOSK_MODEL" || return 1

  local extract="$CACHE_HOME/vosk-extract"
  rm -rf -- "${extract:?}"
  mkdir -p "$extract"
  unzip -q "$zip" -d "$extract" || { warn "couldn't unzip the model"; return 1; }

  # The archive holds one top-level directory; the engine wants its
  # *contents* at VOSK_DIR, not that directory nested inside it.
  local inner
  inner="$(find "$extract" -maxdepth 1 -mindepth 1 -type d | head -1)"
  [[ -n "$inner" ]] || { warn "the model archive looked wrong"; return 1; }

  rm -rf -- "${VOSK_DIR:?}"
  mv "$inner" "$VOSK_DIR"
  # Record which model this is. Without it the model chooser inside the
  # app cannot tell what is in this directory, so it lists this model as
  # one you still need to download - and downloading it fetches the same
  # 40 MB a second time into a second directory.
  printf '%s\n' "$VOSK_MODEL" > "$VOSK_DIR/.quietude-model-key"
  rm -rf -- "${extract:?}"
  rm -f -- "${zip:?}"
  ok "wake-word model installed"
}

download_whisper() {
  if [[ -f "$WHISPER_DIR/model.bin" ]]; then
    ok "transcription model already present"
    return 0
  fi

  info "transcription model ($WHISPER_REPO, ~150 MB)"
  # snapshot_download rather than letting faster-whisper fetch on first
  # use: downloading at install time is what makes the running app fully
  # offline, and the engine is loaded with local_files_only=True so it
  # can never quietly reach for the network later.
  "$VENV_DIR/bin/python" - "$WHISPER_REPO" "$WHISPER_DIR" <<'PY' || return 1
import shutil
import sys
from pathlib import Path

repo, dest = sys.argv[1], Path(sys.argv[2])
try:
    from huggingface_hub import snapshot_download
except ImportError:
    print("    huggingface_hub is missing - it ships with faster-whisper", file=sys.stderr)
    sys.exit(1)

try:
    path = snapshot_download(repo_id=repo, allow_patterns=["*.bin", "*.json", "*.txt", "*.model"])
except Exception as e:
    print(f"    download failed: {e}", file=sys.stderr)
    sys.exit(1)

dest.mkdir(parents=True, exist_ok=True)
for item in Path(path).iterdir():
    if item.is_file():
        # Copy, not symlink: the hub cache is fair game for pruning, and
        # a dangling symlink would break Quietude long after the install.
        shutil.copy2(item, dest / item.name)
print(f"    copied into {dest}")
PY
  [[ -f "$WHISPER_DIR/model.bin" ]] || { warn "the transcription model didn't land"; return 1; }
  # Which repo this is, for the model chooser - see download_vosk.
  printf '%s\n' "$WHISPER_REPO" > "$WHISPER_DIR/.quietude-model-key"
  ok "transcription model installed"
}

download_piper_voice() {
  if [[ -f "$PIPER_DIR/${PIPER_VOICE}.onnx" && -f "$PIPER_DIR/${PIPER_VOICE}.onnx.json" ]]; then
    ok "voice model already present"
    return 0
  fi

  info "voice model ($PIPER_VOICE, ~65 MB)"
  # The .json goes first and the .onnx second, because the engine only
  # offers a voice when both exist - so an interrupted download leaves a
  # voice that simply isn't listed, rather than one that's listed and
  # then fails mid-sentence.
  local config="$PIPER_DIR/${PIPER_VOICE}.onnx.json"
  local model="$PIPER_DIR/${PIPER_VOICE}.onnx"

  fetch "$PIPER_BASE/${PIPER_VOICE}.onnx.json" "$config" "voice config" || return 1
  if ! fetch "$PIPER_BASE/${PIPER_VOICE}.onnx" "$model" "voice model"; then
    # Leave no half-installed voice behind.
    rm -f -- "${config:?}"
    return 1
  fi
  ok "voice model installed"
}

download_models() {
  step "Models (downloaded once, then entirely offline)"

  if [[ $VOICE_INSTALL_FAILED -eq 1 ]]; then
    warn "skipping - the voice packages that read these models aren't installed"
    dim "  No point fetching a few hundred MB nothing can load. Once the"
    dim "  voice stack installs, run './install.sh --models-only'."
    return
  fi

  local failed=0
  download_vosk        || failed=1
  download_whisper     || failed=1
  download_piper_voice || failed=1

  if [[ $failed -eq 1 ]]; then
    warn "some models couldn't be downloaded"
    dim "  Quietude still installs and text chat works."
    dim "  Re-run './install.sh --models-only' once you're online to finish."
  fi
}

download_fonts() {
  step "Fonts"
  mkdir -p "$FONT_DIR"

  local failed=0
  for entry in "${FONTS[@]}"; do
    local name="${entry%%|*}" url="${entry##*|}"
    [[ -f "$FONT_DIR/$name" ]] && continue
    fetch "$url" "$FONT_DIR/$name" "$name" || failed=1
  done

  if [[ $failed -eq 1 ]]; then
    warn "some fonts couldn't be downloaded"
    dim "  Not a problem: the interface falls back to your system monospace."
    dim "  Re-run './install.sh --fonts-only' to try again."
  else
    ok "fonts installed"
  fi
}

build_frontend() {
  step "Building the interface"

  cd "$REPO_DIR/frontend"
  info "installing npm packages"
  # npm ci when there's a lockfile: reproducible, and it won't silently
  # update anything.
  if [[ -f package-lock.json ]]; then
    # `npm ci` is preferred - reproducible, and it won't silently update
    # anything. But it is strict about the lockfile, and some distros
    # pair a current Node with a much older npm (Ubuntu ships Node 22
    # with npm 9), which can reject a lockfile a newer npm wrote. Fall
    # back to `npm install` rather than failing the install over it.
    if ! npm ci --silent 2>/dev/null; then
      warn "npm ci failed (often an npm older than the lockfile) - trying npm install"
      npm install --silent || die "npm install failed too.
    Run 'cd $REPO_DIR/frontend && npm install' to see the error."
    fi
  else
    npm install --silent || die "npm install failed"
  fi

  info "building"
  npm run build --silent || die "the frontend build failed"
  [[ -f "$REPO_DIR/frontend/dist/index.html" ]] || die "the build produced no dist/index.html"
  ok "built to frontend/dist"
  cd "$REPO_DIR"
}

install_window_engine() {
  step "The window"

  # The interface is a web page, and something has to draw it.
  #
  # That something is Electron, which is Chromium - the engine the
  # interface is built and tested against. There is no second option any
  # more, and that is the point: there were three, each a fallback for
  # the one above, and a problem in the first was not a failure but a
  # silent demotion to the second, which drew the same interface with
  # different bugs.
  #
  # The cost is a 230 MB download, once. Node is already needed to build
  # the interface, so it adds no system package.
  if [[ -x "$REPO_DIR/electron/node_modules/electron/dist/electron" ]]; then
    ok "Electron is already installed"
    return 0
  fi

  info "downloading Electron (~230 MB, once)"
  cd "$REPO_DIR/electron"

  # Failing here fails the install.
  #
  # It used to warn and carry on, which produced the worst outcome
  # available: an install that reported success and then had nothing to
  # draw the interface in. "It installed fine but it won't open" is a
  # much harder thing to be told than "the download failed", and it is
  # the same problem.
  # Captured rather than piped. `npm ... | tail` reports tail's exit
  # status, not npm's, so the failure was being read as a success and
  # only caught by the binary check below - with the wrong explanation
  # attached to it.
  local npm_output
  if ! npm_output="$(npm install --silent --no-audit --no-fund 2>&1)"; then
    cd "$REPO_DIR"
    printf '%s\n' "$npm_output" | tail -12 | sed 's/^/      /'
    die "couldn't download Electron, which is what the window is.

    This is almost always the network. Check you're online and run
    ./install.sh again - everything else is already done, so it will
    pick up where it stopped.

    Behind a proxy, npm needs to be told about it:
      npm config set proxy http://your-proxy:port
      npm config set https-proxy http://your-proxy:port

    To see the actual error:
      cd $REPO_DIR/electron && npm install"
  fi

  if [[ ! -x "$REPO_DIR/electron/node_modules/electron/dist/electron" ]]; then
    cd "$REPO_DIR"
    die "npm finished but Electron's binary isn't there.

    The package installs, then downloads the engine separately, and that
    second step is what failed. Run this to see why:
      cd $REPO_DIR/electron && node node_modules/electron/install.js

    If your network blocks GitHub releases, set a mirror:
      export ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
    and run ./install.sh again."
  fi

  ok "Electron installed"
  cd "$REPO_DIR"
  fix_chrome_sandbox
}

fix_chrome_sandbox() {
  # Chromium will not run without a sandbox, and npm cannot give it one.
  #
  # The engine ships a small helper, chrome-sandbox, which has to be
  # owned by root with the setuid bit set. npm unpacks as whoever ran
  # it, so it arrives owned by you and without the bit - Chromium finds
  # it, sees it is not configured, and aborts:
  #
  #   The SUID sandbox helper binary was found, but is not configured
  #   correctly. Rather than run without sandboxing I'm aborting now.
  #
  # This is the first thing anybody hits installing Electron from npm,
  # and the proper repair needs root for exactly two commands.
  #
  # It is not fatal if it cannot be done. Chromium's other sandbox uses
  # kernel namespaces and needs no privileges at all; desktop.py checks
  # for this helper at launch and tells the engine to use namespaces
  # instead when it is not set up. That is a real sandbox, not a weaker
  # one - this is simply the better-supported of the two.
  local helper="$REPO_DIR/electron/node_modules/electron/dist/chrome-sandbox"
  [[ -f "$helper" ]] || return 0

  # Already correct, which is the case on a second run.
  if [[ -u "$helper" && "$(stat -c %u "$helper" 2>/dev/null)" == "0" ]]; then
    ok "the sandbox helper is set up"
    return 0
  fi

  if [[ $NO_SYSTEM_DEPS -eq 1 ]]; then
    skip "the sandbox helper (--no-system-deps); namespaces will be used instead"
    return 0
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    warn "no sudo, so the sandbox helper is left alone"
    dim "  The window will use the kernel's namespace sandbox instead."
    return 0
  fi

  info "the sandbox helper needs root (two commands, shown below)"
  dim "  sudo chown root:root $helper"
  dim "  sudo chmod 4755 $helper"
  if sudo chown root:root "$helper" 2>/dev/null && sudo chmod 4755 "$helper" 2>/dev/null; then
    ok "the sandbox helper is set up"
  else
    warn "couldn't set up the sandbox helper"
    dim "  Not a problem: the window will use the kernel's namespace"
    dim "  sandbox instead. Run the two commands above yourself if you'd"
    dim "  rather have the other one."
  fi
}

install_launcher() {
  step "The 'quietude' command"

  cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# Quietude launcher - written by install.sh. Edit install.sh, not this.
exec "$VENV_DIR/bin/python" -m quietude "\$@"
LAUNCHER_EOF
  chmod +x "$LAUNCHER"
  ok "installed $LAUNCHER"

  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      warn "$BIN_DIR isn't on your PATH"
      dim "  Add this to ~/.bashrc or ~/.zshrc:"
      dim "    export PATH=\"\$HOME/.local/bin:\$PATH\""
      dim "  Until then, run Quietude with: $LAUNCHER"
      ;;
  esac
}

# ============================================================
# Desktop integration: icons and .desktop entries
# ============================================================
#
# Split out so it can be re-run on its own with --desktop-only, and
# inspected with --doctor. Getting Quietude's icon into the dock took two
# attempts; a step you can re-run and a step you can inspect is what
# the second attempt was missing.

refresh_desktop_caches() {
  # Desktops cache both the icon theme index and the list of
  # applications. Without a refresh a newly installed icon can go
  # unnoticed until the next login, which is indistinguishable from the
  # icon not working - so these are run, and reported, rather than being
  # fired into /dev/null.
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    if gtk-update-icon-cache -q -f -t "$ICON_THEME_DIR" 2>/dev/null; then
      ok "refreshed the icon theme cache"
    else
      warn "gtk-update-icon-cache failed - the icon may not appear until you log out"
    fi
  fi
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null \
      && ok "refreshed the application database" \
      || warn "update-desktop-database failed"
  fi
  # XFCE and LXQt menus notice a new entry sooner with this.
  command -v xdg-desktop-menu >/dev/null 2>&1 \
    && xdg-desktop-menu forceupdate 2>/dev/null || true
  # KDE keeps its own index, and will happily show a stale icon without
  # being told. Plasma 6 first, then 5.
  if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 && ok "rebuilt the KDE service cache" || true
  elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 && ok "rebuilt the KDE service cache" || true
  fi
}

write_icon_theme_index() {
  # hicolor is the fallback theme every desktop searches, and a theme
  # directory is supposed to carry an index.theme describing its sizes.
  # The system copy under /usr/share has one; a freshly created
  # ~/.local/share/icons/hicolor does not, and gtk-update-icon-cache
  # refuses to index a directory without one.
  #
  # Only written if absent: if the user already has this file, it
  # describes their other icons too and is none of our business.
  [[ -f "$ICON_INDEX" ]] && return 0

  mkdir -p "$ICON_THEME_DIR"
  {
    printf '%s\n' "$ICON_INDEX_MARKER"
    printf '%s\n' "# Describes the sizes in this theme directory. Required by"
    printf '%s\n' "# gtk-update-icon-cache, which won't index a theme without it."
    printf '%s\n' "[Icon Theme]"
    printf '%s\n' "Name=Hicolor"
    printf '%s\n' "Comment=Fallback icon theme"
    printf 'Directories=scalable/apps'
    for size in "${ICON_PNG_SIZES[@]}"; do
      printf ',%sx%s/apps' "$size" "$size"
    done
    printf '\n\n'
    printf '%s\n' "[scalable/apps]"
    printf '%s\n' "Context=Applications"
    printf '%s\n' "Type=Scalable"
    printf '%s\n' "Size=48"
    printf '%s\n' "MinSize=16"
    printf '%s\n' "MaxSize=512"
    for size in "${ICON_PNG_SIZES[@]}"; do
      printf '\n[%sx%s/apps]\n' "$size" "$size"
      printf 'Context=Applications\n'
      printf 'Type=Fixed\n'
      printf 'Size=%s\n' "$size"
    done
  } > "$ICON_INDEX"
  ok "wrote $ICON_INDEX"
}

write_desktop_entry() {
  # $1 path, $2 StartupWMClass, $3 extra lines
  local path="$1" wmclass="$2" extra="$3"

  # Icon= is an absolute path, not the theme name "quietude".
  #
  # Both are legal. The theme name is the tidier of the two and it is
  # what this used to say - but a theme name is not a file, it is a
  # lookup, and the lookup goes through the desktop's own icon cache.
  # GNOME Shell and Plasma each build that cache once and hold it for
  # the life of the session, so an icon installed after you logged in
  # resolves to nothing until you log out again. Nothing in the entry
  # is wrong when that happens and nothing reports an error; the dock
  # just draws an empty square, which is indistinguishable from the
  # icon itself being broken and sends you looking in the wrong place.
  #
  # A path is read straight off disk. No cache, no theme, no relogin.
  # The cost is that the desktop scales one file instead of picking the
  # size-specific variant, which at dock sizes is not a visible
  # difference. The theme copies are still installed, because the menu
  # and some desktops do prefer a name, and this file lives inside that
  # theme anyway - so there is one icon on disk, pointed at two ways.
  local icon="quietude"
  [[ -f "$ICON_DOCK_FILE" ]] && icon="$ICON_DOCK_FILE"

  cat > "$path" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Name=Quietude
GenericName=Local AI Assistant
Comment=Nothing leaves this room.
Exec=$LAUNCHER
Icon=$icon
Terminal=false
Categories=Utility;
Keywords=assistant;ai;voice;local;
StartupNotify=true
StartupWMClass=$wmclass
${extra}
DESKTOP_EOF
}

install_desktop_integration() {
  mkdir -p "$DESKTOP_DIR" "$ICON_DIR"

  # The icon set ships in branding/ rather than being written inline
  # here: it's a real mark with a small-size variant, and a designed
  # asset belongs in a file you can open, not a heredoc in a shell
  # script.
  if [[ -f "$BRANDING_DIR/icon.svg" ]]; then
    install -Dm644 "$BRANDING_DIR/icon.svg" "$ICON_FILE"
    ok "installed $ICON_FILE"

    local installed=0
    for size in "${ICON_PNG_SIZES[@]}"; do
      local png="$BRANDING_DIR/png/quietude-${size}.png"
      [[ -f "$png" ]] || continue
      install -Dm644 "$png" "$ICON_THEME_DIR/${size}x${size}/apps/quietude.png"
      installed=$((installed + 1))
    done
    [[ $installed -gt 0 ]] && ok "installed $installed PNG icon sizes"
    write_icon_theme_index
  else
    warn "branding/icon.svg is missing - the launcher will use a generic icon"
  fi

  # The launcher opens a browser itself, so the entry runs it directly
  # rather than declaring a URL handler - one code path for "start
  # Quietude", whether it came from a menu or a terminal.
  #
  # Two entries, one per WM_CLASS field. See WM_CLASS_CLASS above for
  # why; the short version is that desktops disagree about which field
  # identifies a window, and an entry each costs nothing.
  write_desktop_entry "$DESKTOP_FILE" "$WM_CLASS_CLASS" ""
  ok "installed $DESKTOP_FILE"

  # Validate rather than hope. A single malformed line makes a desktop
  # skip the whole file, and it does so silently - which looks exactly
  # like the icon not working.
  if command -v desktop-file-validate >/dev/null 2>&1; then
    local bad=0
    for f in "$DESKTOP_FILE"; do
      desktop-file-validate "$f" >/dev/null 2>&1 || { bad=1; warn "$f failed validation:"; desktop-file-validate "$f" 2>&1 | sed 's/^/      /'; }
    done
    [[ $bad -eq 0 ]] && ok "the desktop entry validates"
  fi

  refresh_desktop_caches

  # The entries live under $PREFIX/share, which only counts if that path
  # is on the XDG data search path. It is by default when PREFIX is
  # ~/.local; it is not if someone set QUIETUDE_PREFIX elsewhere, and the
  # symptom is a menu entry that never appears.
  local data_dirs="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
  if [[ "$PREFIX/share" != "$HOME/.local/share" \
     && ":$data_dirs:" != *":$PREFIX/share:"* ]]; then
    warn "$PREFIX/share isn't on XDG_DATA_DIRS, so the desktop won't find the"
    warn "launcher or the icon. Add it to XDG_DATA_DIRS, or install with the"
    warn "default QUIETUDE_PREFIX=\$HOME/.local."
  fi
}

install_service() {
  step "Service and desktop entry"

  mkdir -p "$SYSTEMD_USER_DIR" "$DESKTOP_DIR" "$ICON_DIR"

  # A *user* service, not a system one. Quietude is one person's assistant
  # holding one person's encrypted profile: she has no business running
  # as root or before login, and a user unit means she starts with the
  # session and stops with it.
  cat > "$UNIT_FILE" <<UNIT_EOF
[Unit]
Description=Quietude - local AI assistant
Documentation=file://$REPO_DIR/README.md
After=graphical-session.target

[Service]
Type=simple
# --window none: a service has no session to open a window into,
# and systemd restarting her must not spawn one behind your back.
ExecStart=$VENV_DIR/bin/python -m quietude --window none
# SIGTERM is all that's needed: lifecycle.py handles it, closes the
# worker processes in a known order, and escalates to SIGKILL itself if
# one won't go. TimeoutStopSec is systemd's own backstop behind that.
KillSignal=SIGTERM
TimeoutStopSec=20
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT_EOF
  ok "installed $UNIT_FILE"

  install_desktop_integration

  dim "Not enabled by default, and not the usual way to start her -"
  dim "this runs her headless as a background service, with no window."
  dim "To have the window open at login, use the app settings page."
  dim "  systemctl --user start quietude          background, no window"
}

verify() {
  step "Verifying"

  local python="$VENV_DIR/bin/python"
  local problems=0

  "$python" -c 'import quietude' 2>/dev/null \
    && ok "quietude imports" || { warn "quietude won't import"; problems=1; }

  "$python" -c 'import flask, cryptography, numpy' 2>/dev/null \
    && ok "core dependencies import" || { warn "a core dependency is missing"; problems=1; }

  if "$python" -c 'import cv2; cv2.face.LBPHFaceRecognizer_create()' 2>/dev/null; then
    ok "face recognition available"
  else
    warn "cv2.face is unavailable - check opencv-contrib-python-headless installed"
    dim "  if cv2 won't import at all, a missing libGL is the usual cause"
    problems=1
  fi

  if [[ -x "$REPO_DIR/electron/node_modules/electron/dist/electron" ]]; then
    ok "the window is installed"
  else
    warn "the window isn't installed - there'll be nothing to open the interface in"
    dim "  re-run ./install.sh once you're online"
    problems=1
  fi

  if "$python" -c 'import vosk, faster_whisper' 2>/dev/null; then
    ok "speech packages import"
  else
    # Not counted as a problem: voice is optional, and the install is
    # genuinely fine without it. The closing message says so separately.
    warn "speech packages won't import - voice chat will be unavailable"
  fi

  # Ask the app itself what it can see, rather than re-implementing the
  # same checks here and letting the two drift apart.
  "$python" - <<'PY' || problems=1
from quietude.core import speech_engine, tts_engine

vosk_ok, whisper_ok = speech_engine.models_present()
print(f"    {'+' if vosk_ok else '!'} wake-word model {'found' if vosk_ok else 'MISSING'}")
print(f"    {'+' if whisper_ok else '!'} transcription model {'found' if whisper_ok else 'MISSING'}")

voices = tts_engine.get_voices()
if voices:
    print(f"    + {len(voices)} voice(s) available, using {voices[0]['name']}")
else:
    print("    ! no voices available - Quietude will be silent")
PY

  [[ -f "$REPO_DIR/frontend/dist/index.html" ]] \
    && ok "interface built" || { warn "the interface isn't built"; problems=1; }

  return $problems
}

finish() {
  printf '\n%s%s%s\n' "$C_GREEN" "Quietude is installed." "$C_RESET"

  # Missing models aren't an install failure - text chat works fine
  # without them - but "installed" shouldn't imply the voice features
  # are there when they aren't. Say which half is missing, and how to
  # finish, rather than letting the first wake word be the discovery.
  # Asks the venv whether the packages actually import, rather than
  # trusting pip's exit code. The two can disagree: pip is perfectly
  # happy to "succeed" having installed nothing useful, and an import is
  # the only thing that answers the question the user cares about.
  if ! "$VENV_DIR/bin/python" -c 'import vosk, faster_whisper' 2>/dev/null; then
    VOICE_INSTALL_FAILED=1
  fi

  if [[ $VOICE_INSTALL_FAILED -eq 1 ]]; then
    printf '\n'
    warn "Voice features are NOT available - the voice packages aren't usable."
    dim "  Everything else works: text chat, face login, account management."
    dim "  Quietude will tell you voice is unavailable rather than pretending to listen."
    dim "  To retry:"
    dim "    $VENV_DIR/bin/pip install -r backend/requirements-voice.txt"
    dim "    ./install.sh --models-only"
  elif [[ ! -d "$VOSK_DIR/am" && ! -f "$VOSK_DIR/README" ]] || [[ ! -f "$WHISPER_DIR/model.bin" ]]; then
    printf '\n'
    warn "Voice input is NOT available - the speech models aren't downloaded."
    dim "  Quietude works normally for text chat. To finish, once you're online:"
    dim "    ./install.sh --models-only"
  fi

  printf '\n'
  info "Start her:"
  dim "  quietude"
  printf '\n'
  info "She opens in her own window - not a browser. The address she"
  info "serves on only answers that window, so a browser pointed at it"
  info "gets a polite refusal."
  info "First run walks you through the agreement, setup and face registration."
  printf '\n'
  info "Once she's open, say \"app settings\" to choose what closing the"
  info "window does and whether she starts when you log in."
  printf '\n'
  info "That's the whole command line:"
  dim "  quietude                                 start her"
  dim "  quietude --port 8080                     start her on a different port"
  dim "  Ctrl+C, or close the window               stop her"
  printf '\n'
  info "Starting at login is a switch on the app settings page, not a"
  info "command - say \"app settings\" once she's open."
  printf '\n'
  info "Optional - conversation mode (the one feature that isn't local):"
  dim "  $VENV_DIR/bin/pip install -r backend/requirements-gemini.txt"
  printf '\n'
  info "To remove her:"
  dim "  ./install.sh --uninstall                 keeps your profile"
  dim "  ./install.sh --uninstall --purge-data    erases everything"
  printf '\n'
}

# ============================================================
# --doctor: what the desktop can actually see
# ============================================================
#
# Exists because "the icon is still wrong" is not a diagnosable report.
# Desktop integration has about six independent ways to fail - a missing
# file, a file the desktop skipped, an unresolvable icon name, a stale
# cache, a prefix that isn't searched, a window whose WM_CLASS doesn't
# match anything - and from the outside all six look identical. This
# prints the state of each one. It changes nothing.

doctor_row() {
  # $1 = ok|warn|bad, $2 = label, $3 = detail
  local mark
  case "$1" in
    ok)   mark="$C_GREEN+$C_RESET" ;;
    warn) mark="$C_YELLOW~$C_RESET" ;;
    *)    mark="$C_RED""x""$C_RESET" ;;
  esac
  printf '  %b %-34s %s\n' "$mark" "$2" "$3"
}

do_doctor() {
  printf '\n%sQuietude desktop integration%s\n\n' "$C_CYAN" "$C_RESET"

  # ---- the session ------------------------------------------------
  printf '%sSession%s\n' "$C_CYAN" "$C_RESET"
  doctor_row ok "display server" "${XDG_SESSION_TYPE:-unknown}"
  doctor_row ok "desktop" "$(printf '%s' "${XDG_CURRENT_DESKTOP:-unknown}${DESKTOP_SESSION:+ ($DESKTOP_SESSION)}")"

  local data_dirs="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
  if [[ "$PREFIX/share" == "$HOME/.local/share" || ":$data_dirs:" == *":$PREFIX/share:"* ]]; then
    doctor_row ok "entries are on the search path" "$PREFIX/share"
  else
    doctor_row bad "entries are NOT searched" "add $PREFIX/share to XDG_DATA_DIRS"
  fi

  # ---- the entries ------------------------------------------------
  printf '\n%sDesktop entries%s\n' "$C_CYAN" "$C_RESET"
  local f
  for f in "$DESKTOP_FILE"; do
    if [[ ! -f "$f" ]]; then
      doctor_row bad "$(basename "$f")" "missing - run ./install.sh --desktop-only"
      continue
    fi
    local wmclass icon
    wmclass=$(grep -m1 '^StartupWMClass=' "$f" | cut -d= -f2- || true)
    icon=$(grep -m1 '^Icon=' "$f" | cut -d= -f2- || true)
    doctor_row ok "$(basename "$f")" "StartupWMClass=$wmclass"
    # The Icon= line is the one that decides what the dock draws, so it
    # gets checked rather than just printed. A path is followed to the
    # file; a bare name means an older entry that still goes through the
    # desktop's icon cache, which is the failure this moved away from.
    if [[ "$icon" == /* ]]; then
      if [[ -f "$icon" ]]; then
        doctor_row ok "  Icon=" "$icon"
      else
        doctor_row bad "  Icon=" "$icon - MISSING. Run ./install.sh --desktop-only"
      fi
    else
      doctor_row warn "  Icon=" "$icon (a theme name - your desktop may be caching it; run ./install.sh --desktop-only)"
    fi
    if command -v desktop-file-validate >/dev/null 2>&1; then
      if desktop-file-validate "$f" >/dev/null 2>&1; then
        doctor_row ok "  validates" "yes"
      else
        doctor_row bad "  validates" "no - the desktop will skip this file:"
        desktop-file-validate "$f" 2>&1 | sed 's/^/        /'
      fi
    fi
  done

  # Entries left behind by an earlier install, anywhere the desktop
  # looks. This has bitten here: a desktop picks one entry per
  # StartupWMClass, and if an older copy with a stale Icon= wins, the
  # new one is never read and reinstalling appears to do nothing.
  local seen=0 other
  while IFS= read -r other; do
    [[ "$other" == "$DESKTOP_FILE" ]] && continue
    doctor_row bad "a second entry claims this app" "$other"
    seen=$((seen + 1))
  done < <(
    { printf '%s\n' "$DESKTOP_DIR"
      printf '%s\n' "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" | tr ':' '\n' \
        | sed 's|$|/applications|'
    } | sort -u | while read -r d; do
      [[ -d "$d" ]] || continue
      grep -rls '^StartupWMClass=quietude' "$d" 2>/dev/null || true
    done
  )
  [[ $seen -eq 0 ]] && doctor_row ok "no leftover entries" "only the one this installs"
  [[ $seen -gt 0 ]] && printf '        %s\n' "delete the file(s) above, then: ./install.sh --desktop-only"

  # ---- the icon ---------------------------------------------------
  printf '\n%sIcon%s\n' "$C_CYAN" "$C_RESET"
  [[ -f "$ICON_FILE" ]] \
    && doctor_row ok "scalable/apps/quietude.svg" "present" \
    || doctor_row warn "scalable/apps/quietude.svg" "missing"

  local have=() missing=()
  local size
  for size in "${ICON_PNG_SIZES[@]}"; do
    if [[ -f "$ICON_THEME_DIR/${size}x${size}/apps/quietude.png" ]]; then
      have+=("$size")
    else
      missing+=("$size")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    doctor_row ok "PNG sizes" "all ${#have[@]} present"
  else
    doctor_row warn "PNG sizes" "have ${have[*]:-none}; missing ${missing[*]}"
  fi

  [[ -f "$ICON_INDEX" ]] \
    && doctor_row ok "index.theme" "present" \
    || doctor_row warn "index.theme" "missing - gtk-update-icon-cache won't index this theme"

  if [[ -f "$ICON_THEME_DIR/icon-theme.cache" ]]; then
    if [[ -n "$(find "$ICON_THEME_DIR" -name 'quietude.png' -newer "$ICON_THEME_DIR/icon-theme.cache" -print -quit 2>/dev/null)" ]]; then
      doctor_row warn "icon-theme.cache" "older than the icons - run ./install.sh --desktop-only"
    else
      doctor_row ok "icon-theme.cache" "up to date"
    fi
  else
    doctor_row warn "icon-theme.cache" "absent (not fatal; desktops can look the icon up directly)"
  fi

  # The question that actually matters: does the name "quietude" resolve?
  # Asked of GTK's icon lookup, which is the code path a dock uses -
  # every check above can pass while this one fails.
  local probe
  probe=$(XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}" python3 - <<'DOCTOR_PY' 2>/dev/null || true
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
theme = Gtk.IconTheme.get_default()
if theme is None:
    raise SystemExit("nodisplay")
info = theme.lookup_icon("quietude", 48, 0)
print(info.get_filename() if info else "unresolved")
DOCTOR_PY
)
  case "$probe" in
    "")            doctor_row warn "name 'quietude' resolves" "couldn't check (no python3-gi)" ;;
    nodisplay)     doctor_row warn "name 'quietude' resolves" "couldn't check (no display attached)" ;;
    unresolved)    doctor_row warn "name 'quietude' resolves" "no - the menu may show a generic icon (the dock won't: it uses the path above)" ;;
    *)             doctor_row ok   "name 'quietude' resolves" "$probe" ;;
  esac

  # What a dock would actually paint. Everything above can be green
  # while the file itself is blank or transparent, and an empty square
  # in a dock looks identical either way - so the pixels get counted.
  if [[ -f "$ICON_DOCK_FILE" ]]; then
    local pixels
    pixels=$(python3 - "$ICON_DOCK_FILE" <<'PIXEL_PY' 2>/dev/null || true
import sys, zlib, struct
# Decode enough PNG by hand to avoid depending on Pillow being installed.
raw = open(sys.argv[1], "rb").read()
pos, w, h, idat, bits, ctype = 8, 0, 0, b"", 0, 0
while pos < len(raw):
    ln = struct.unpack(">I", raw[pos:pos+4])[0]
    typ = raw[pos+4:pos+8]
    data = raw[pos+8:pos+8+ln]
    if typ == b"IHDR":
        w, h, bits, ctype = struct.unpack(">IIBB", data[:10])
    elif typ == b"IDAT":
        idat += data
    pos += 12 + ln
if ctype != 6 or bits != 8:
    raise SystemExit("")
buf = zlib.decompress(idat)
stride = w * 4
prev = bytearray(stride)
opaque = 0
total = w * h
out = bytearray()
i = 0
for _ in range(h):
    ft = buf[i]; i += 1
    line = bytearray(buf[i:i+stride]); i += stride
    for x in range(stride):
        a = line[x-4] if x >= 4 else 0
        b = prev[x]
        c = prev[x-4] if x >= 4 else 0
        if ft == 1: line[x] = (line[x] + a) & 255
        elif ft == 2: line[x] = (line[x] + b) & 255
        elif ft == 3: line[x] = (line[x] + (a + b) // 2) & 255
        elif ft == 4:
            pa, pb, pc = abs(b-c), abs(a-c), abs(a+b-2*c)
            pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[x] = (line[x] + pr) & 255
    prev = line
    for x in range(0, stride, 4):
        if line[x+3] > 40 and (line[x] + line[x+1] + line[x+2]) > 90:
            opaque += 1
print(f"{opaque * 100 // total}")
PIXEL_PY
)
    if [[ -z "$pixels" ]]; then
      doctor_row warn "the icon has visible pixels" "couldn't read the PNG"
    elif [[ "$pixels" -lt 20 ]]; then
      doctor_row bad "the icon has visible pixels" "only ${pixels}% - this would look blank"
    else
      doctor_row ok "the icon has visible pixels" "${pixels}% of it is lit"
    fi
  fi

  # ---- the window -------------------------------------------------
  printf '\n%sWindow identity%s\n' "$C_CYAN" "$C_RESET"

  # Which engine will draw the window, in the order the launcher tries.
  ELECTRON_BIN="$REPO_DIR/electron/node_modules/electron/dist/electron"
  if [[ -x "$ELECTRON_BIN" ]]; then
    doctor_row ok "window engine" "Chromium, via Electron"
  else
    doctor_row bad "window engine" "not installed - run ./install.sh"
  fi
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    # Asked of desktop.py rather than restated here, so this can't drift
    # away from what actually gets launched.
    local reported
    reported=$("$VENV_DIR/bin/python" - <<'DOCTOR_PY' 2>&1 || true
try:
    from quietude.desktop import ElectronWindow, WM_INSTANCE, WM_CLASS, find_electron
except Exception as exc:
    print(f"error: {exc}")
    raise SystemExit
print(f"instance={WM_INSTANCE}")
print(f"class={WM_CLASS}")
print(f"binary={find_electron() or 'NOT INSTALLED'}")
print(f"ready={'yes' if ElectronWindow.is_available() else 'no'}")
DOCTOR_PY
)
    local r_instance r_class r_binary r_ready
    r_instance=$(printf '%s\n' "$reported" | sed -n 's/^instance=//p' || true)
    r_class=$(printf '%s\n' "$reported" | sed -n 's/^class=//p' || true)
    r_binary=$(printf '%s\n' "$reported" | sed -n 's/^binary=//p' || true)
    r_ready=$(printf '%s\n' "$reported" | sed -n 's/^ready=//p' || true)

    if [[ -n "$r_instance" ]]; then
      doctor_row ok "her window will report" "WM_CLASS = ($r_instance, $r_class)"
    else
      doctor_row bad "couldn't ask desktop.py" "$reported"
    fi
    if [[ "$r_ready" == "yes" ]]; then
      doctor_row ok "the window can open" "$r_binary"
    else
      doctor_row bad "the window cannot open" "Electron is not installed - run ./install.sh"
    fi
    if [[ -n "$r_instance" && "$r_instance" != "$WM_CLASS_CLASS" ]]; then
      doctor_row bad "WM_CLASS mismatch" \
        "desktop.py says '$r_instance', the entry was written for '$WM_CLASS_CLASS'"
    fi
  else
    doctor_row warn "window identity" "no virtualenv at $VENV_DIR"
  fi

  # The entry's StartupWMClass against the instance field the window
  # reports. GNOME tries instance then class; most docks match the
  # instance, so that is the one to match.
  if [[ -f "$DESKTOP_FILE" ]]; then
    local w
    w=$(grep -m1 '^StartupWMClass=' "$DESKTOP_FILE" | cut -d= -f2- || true)
    if [[ "$w" == "$WM_CLASS_CLASS" ]]; then
      doctor_row ok "the entry matches the window" "StartupWMClass=$w"
    else
      doctor_row bad "the entry does not match" "StartupWMClass=$w, window says $WM_CLASS_CLASS"
    fi
  fi

  # ---- and what a running window actually says --------------------
  if command -v xprop >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
    local ids found=0
    ids=$(xprop -root _NET_CLIENT_LIST 2>/dev/null | sed 's/.*# //; s/,//g' || true)
    local id
    for id in $ids; do
      local cls
      cls=$(xprop -id "$id" WM_CLASS 2>/dev/null | grep -o '".*"' || true)
      if [[ "$cls" == *quietude* ]]; then
        printf '\n%sA window of hers is open right now%s\n' "$C_CYAN" "$C_RESET"
        doctor_row ok "measured WM_CLASS" "$cls"
        found=1
      fi
    done
    [[ $found -eq 0 ]] && dim $'\n  (start Quietude and re-run this to see her window\'s real WM_CLASS)'
  fi

  printf '\n'
  dim "If everything above is green and the dock still disagrees, it is"
  dim "holding a cached copy. GNOME on X11: Alt+F2, r, Enter. GNOME on"
  dim "Wayland: log out and back in. KDE: plasmashell --replace."
  printf '\n'
}

# ============================================================
# Main
# ============================================================

require_linux

if [[ $DO_DOCTOR -eq 1 ]]; then
  do_doctor
  exit 0
fi

if [[ $DESKTOP_ONLY -eq 1 ]]; then
  printf '\n%sReinstalling the icon and desktop entries%s\n' "$C_CYAN" "$C_RESET"
  step "Desktop integration"
  install_desktop_integration
  printf '\n'
  ok "done"
  dim "If the dock still shows the old icon, it is holding a cached copy:"
  dim "  GNOME on X11      Alt+F2, type r, Enter"
  dim "  GNOME on Wayland  log out and back in"
  dim "  KDE               plasmashell --replace >/dev/null 2>&1 & disown"
  dim "  others            log out and back in"
  dim "Run ./install.sh --doctor to see what the desktop can see."
  printf '\n'
  exit 0
fi

if [[ $FONTS_ONLY -eq 1 ]]; then
  download_fonts
  printf '\n'
  dim "Rebuild the interface to pick them up: cd frontend && npm run build"
  printf '\n'
  exit 0
fi

if [[ $MODELS_ONLY -eq 1 ]]; then
  [[ -x "$VENV_DIR/bin/python" ]] || die "no virtualenv at $VENV_DIR - run a full install first"
  make_dirs
  download_models
  printf '\n'
  verify || true
  printf '\n'
  exit 0
fi

printf '\n%s%s%s\n' "$C_CYAN" "Installing Quietude" "$C_RESET"
dim "  repo:    $REPO_DIR"
dim "  venv:    $VENV_DIR"
dim "  data:    $DATA_HOME"
dim "  command: $LAUNCHER"

[[ $SKIP_SYSTEM_DEPS -eq 0 ]] && install_system_deps
check_tools
make_dirs
build_venv
[[ $SKIP_MODELS -eq 0 ]] && download_models
[[ $SKIP_FONTS  -eq 0 ]] && download_fonts
build_frontend
install_window_engine
install_launcher
[[ $SKIP_SERVICE -eq 0 ]] && install_service

if verify; then
  finish
else
  printf '\n%swarning:%s installed, but some checks failed - see above.\n' \
    "$C_YELLOW" "$C_RESET"
  dim "Text chat should still work. Fix the warnings and re-run to confirm."
  printf '\n'
fi
