#!/usr/bin/env bash
#
# divoom-pi installer.
#
#   curl -fsSL https://raw.githubusercontent.com/adman234/divoom-pi/main/install.sh | sudo bash
#
# Turns a freshly flashed Raspberry Pi into a Bluetooth Classic gateway for
# Divoom devices: installs the code to /opt/divoom-pi, drops a config file in
# /etc/divoom-pi, and starts a systemd service that Home Assistant can find.
#
# Options:
#   --uninstall     remove the service, the code and the mDNS records
#   --branch NAME   install from a branch other than main
#   --no-start      install everything but do not start the service
#
set -euo pipefail

REPO="${DIVOOM_PI_REPO:-adman234/divoom-pi}"
BRANCH="${DIVOOM_PI_BRANCH:-main}"
PREFIX="${DIVOOM_PI_PREFIX:-/opt/divoom-pi}"
CONFIG_DIR="${DIVOOM_PI_CONFIG_DIR:-/etc/divoom-pi}"
CONFIG_FILE="$CONFIG_DIR/config.ini"
UNIT_FILE="/etc/systemd/system/divoom-pi.service"
LAUNCHER="/usr/local/bin/divoom-pi"
AVAHI_SERVICE_DIR="/etc/avahi/services"
REQUIRED_PACKAGES=(bluez avahi-daemon python3)

START_SERVICE=1
UNINSTALL=0
TEMP_DIR=""
PYTHON=""

# ---------------------------------------------------------------- output ---

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$(printf '\033[1m'); RED=$(printf '\033[31m')
    GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
    RESET=$(printf '\033[0m')
else
    BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

step() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s !  %s%s\n' "$YELLOW" "$*" "$RESET"; }
good() { printf '%s ok %s%s\n' "$GREEN" "$*" "$RESET"; }
die()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}
trap cleanup EXIT

# --------------------------------------------------------------- checks ----

require_root() {
    if [ "$(id -u)" -eq 0 ]; then
        return 0
    fi

    # Re-exec under sudo when we were run as a file. When piped from curl
    # there is no file to re-run, so say what to type instead.
    if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ] && command -v sudo >/dev/null 2>&1; then
        warn "not running as root, re-running with sudo"
        exec sudo -E bash "${BASH_SOURCE[0]}" "$@"
    fi

    die "this installer needs root. Run it as:
    curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh | sudo bash"
}

check_platform() {
    [ "$(uname -s)" = "Linux" ] || die "divoom-pi runs on Linux (Raspberry Pi OS); this is $(uname -s)."
    command -v systemctl >/dev/null 2>&1 || die "no systemd found - divoom-pi installs itself as a systemd service."
}

# Run after install_packages, so python3 is definitely there by now.
check_python() {
    PYTHON=$(command -v python3 || true)
    [ -n "$PYTHON" ] || die "python3 is not installed and could not be installed automatically."
    "$PYTHON" - <<'PY' || die "divoom-pi needs Python 3.7 or newer."
import sys
sys.exit(0 if sys.version_info >= (3, 7) else 1)
PY
    good "using $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"
}

# ------------------------------------------------------------- packages ----

install_packages() {
    local missing=()
    local package
    for package in "${REQUIRED_PACKAGES[@]}"; do
        if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
            missing+=("$package")
        fi
    done

    if [ ${#missing[@]} -eq 0 ]; then
        good "bluez, avahi-daemon and python3 are already installed"
        return 0
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        die "missing packages: ${missing[*]} - install them with your package manager, then re-run."
    fi

    step "Installing ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
}

# --------------------------------------------------------------- source ----

find_local_source() {
    local script_dir
    [ -n "${BASH_SOURCE[0]:-}" ] || return 1
    [ -f "${BASH_SOURCE[0]}" ] || return 1
    script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    [ -f "$script_dir/divoom_pi/__main__.py" ] || return 1
    printf '%s' "$script_dir"
}

# Writes the source directory to stdout, so everything else it has to say goes
# to stderr.
fetch_source() {
    local local_source
    if local_source=$(find_local_source); then
        info "installing from $local_source" >&2
        printf '%s' "$local_source"
        return 0
    fi

    command -v curl >/dev/null 2>&1 || die "curl is needed to download divoom-pi."
    command -v tar >/dev/null 2>&1 || die "tar is needed to unpack divoom-pi."

    TEMP_DIR=$(mktemp -d)
    step "Downloading $REPO ($BRANCH)" >&2
    curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" \
        | tar -xz -C "$TEMP_DIR" \
        || die "could not download https://github.com/$REPO (branch $BRANCH)."

    local extracted=""
    local candidate
    for candidate in "$TEMP_DIR"/*/; do
        extracted="${candidate%/}"
        break
    done
    [ -n "$extracted" ] || die "the downloaded archive was empty."
    check_source "$extracted"
    printf '%s' "$extracted"
}

# Everything install_files() is going to copy has to actually be there.
check_source() {
    local source="$1"
    local required
    for required in divoom_pi/__main__.py config.example.ini systemd/divoom-pi.service; do
        [ -f "$source/$required" ] || die "$source does not look like divoom-pi: no $required"
    done
}

# -------------------------------------------------------------- install ----

install_files() {
    local source="$1"
    check_source "$source"

    # Parse rather than compile, so this leaves no root-owned __pycache__
    # behind when the installer is run from a git checkout.
    step "Checking the code parses with this Python"
    "$PYTHON" - "$source/divoom_pi" <<'PY' || die "the divoom_pi package does not parse with this Python."
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in sorted(root.rglob("*.py")):
    ast.parse(path.read_text(encoding="utf-8"), str(path))
PY

    if systemctl is-active --quiet divoom-pi 2>/dev/null; then
        step "Stopping the running service"
        systemctl stop divoom-pi
    fi

    step "Installing to $PREFIX"
    install -d "$PREFIX"
    rm -rf "$PREFIX/divoom_pi"
    cp -a "$source/divoom_pi" "$PREFIX/divoom_pi"
    find "$PREFIX/divoom_pi" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    for extra in README.md LICENSE config.example.ini; do
        if [ -f "$source/$extra" ]; then
            cp -a "$source/$extra" "$PREFIX/$extra"
        fi
    done

    step "Writing $CONFIG_FILE"
    install -d "$CONFIG_DIR"
    if [ -f "$CONFIG_FILE" ]; then
        info "keeping your existing configuration"
        cp -a "$source/config.example.ini" "$CONFIG_DIR/config.example.ini"
    else
        cp -a "$source/config.example.ini" "$CONFIG_FILE"
    fi

    step "Installing the divoom-pi command"
    cat > "$LAUNCHER" <<LAUNCHEREOF
#!/bin/sh
# Installed by divoom-pi's install.sh.
PYTHONPATH="$PREFIX\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec "$PYTHON" -m divoom_pi "\$@"
LAUNCHEREOF
    chmod 0755 "$LAUNCHER"

    step "Installing the systemd service"
    sed -e "s|@PREFIX@|$PREFIX|g" \
        -e "s|@CONFIG@|$CONFIG_FILE|g" \
        -e "s|@PYTHON@|$PYTHON|g" \
        "$source/systemd/divoom-pi.service" > "$UNIT_FILE"
    chmod 0644 "$UNIT_FILE"
    systemctl daemon-reload
}

enable_dependencies() {
    step "Making sure Bluetooth and Avahi are running"
    if command -v rfkill >/dev/null 2>&1; then
        rfkill unblock bluetooth 2>/dev/null || true
    fi
    for unit in bluetooth avahi-daemon; do
        systemctl enable --now "$unit" >/dev/null 2>&1 || warn "could not start $unit"
    done
    [ -d "$AVAHI_SERVICE_DIR" ] || install -d "$AVAHI_SERVICE_DIR"

    # bluetoothctl needs a moment after a cold start before the controller
    # answers; powering it on here means the first scan is not wasted.
    if command -v bluetoothctl >/dev/null 2>&1; then
        bluetoothctl power on >/dev/null 2>&1 || true
    fi
}

start_service() {
    step "Starting divoom-pi"
    systemctl enable divoom-pi >/dev/null 2>&1 || warn "could not enable divoom-pi at boot"
    systemctl restart divoom-pi
    sleep 2
    if systemctl is-active --quiet divoom-pi; then
        good "divoom-pi is running"
    else
        warn "divoom-pi did not start. The last few log lines:"
        journalctl -u divoom-pi -n 20 --no-pager || true
        exit 1
    fi
}

summary() {
    local address=""
    address=$(hostname -I 2>/dev/null | awk '{print $1}') || true
    [ -n "$address" ] || address="<the IP of this Pi>"

    printf '\n%sdivoom-pi is installed.%s\n\n' "$BOLD" "$RESET"
    printf 'Gateway address for Home Assistant:  %s (port 7777)\n' "$address"
    printf 'Configuration:                       %s\n' "$CONFIG_FILE"
    printf 'Logs:                                journalctl -u divoom-pi -f\n'
    printf 'Health check:                        divoom-pi doctor\n\n'
    printf 'Next steps:\n'
    printf '  1. Turn your Divoom on, and disconnect it from your phone.\n'
    printf '  2. Find it:        divoom-pi scan\n'
    printf '  3. Test the path:  divoom-pi selftest <MAC> --channel 2 --action on\n'
    printf '  4. In Home Assistant, install the Divoom integration (see the README),\n'
    printf '     then wait for the discovery notification. To add it by hand instead,\n'
    printf '     use host %s and the MAC address from step 2.\n\n' "$address"
}

do_install() {
    check_platform
    install_packages
    check_python
    local source
    source=$(fetch_source)
    install_files "$source"
    enable_dependencies
    if [ "$START_SERVICE" -eq 1 ]; then
        start_service
    else
        info "not starting the service (--no-start)"
    fi
    summary
}

# ------------------------------------------------------------ uninstall ----

do_uninstall() {
    step "Stopping and disabling the service"
    systemctl disable --now divoom-pi >/dev/null 2>&1 || true
    rm -f "$UNIT_FILE"
    systemctl daemon-reload || true

    step "Removing the mDNS records divoom-pi published"
    rm -f "$AVAHI_SERVICE_DIR"/divoom-pi-*.service

    step "Removing $PREFIX and $LAUNCHER"
    rm -rf "$PREFIX"
    rm -f "$LAUNCHER"

    if [ -d "$CONFIG_DIR" ]; then
        info "leaving $CONFIG_DIR in place - delete it by hand if you want it gone"
    fi
    good "divoom-pi removed"
}

# ----------------------------------------------------------------- main ----

usage() {
    cat <<'USAGE'
divoom-pi installer.

    curl -fsSL https://raw.githubusercontent.com/adman234/divoom-pi/main/install.sh | sudo bash

Options:
    --uninstall     remove the service, the code and the mDNS records
    --branch NAME   install from a branch other than main
    --no-start      install everything but do not start the service
    -h, --help      show this message
USAGE
}

main() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            -h|--help) usage; exit 0 ;;
        esac
    done

    # Before parsing, so that re-running under sudo keeps the flags.
    require_root "$@"

    while [ $# -gt 0 ]; do
        case "$1" in
            --uninstall) UNINSTALL=1 ;;
            --no-start) START_SERVICE=0 ;;
            --branch) shift; [ $# -gt 0 ] || die "--branch needs a name"; BRANCH="$1" ;;
            -h|--help) usage; exit 0 ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done

    if [ "$UNINSTALL" -eq 1 ]; then
        do_uninstall
    else
        do_install
    fi
}

main "$@"
