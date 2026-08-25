"""Command line interface: `divoom-pi <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import List, Optional

from . import __version__, protocol
from .bluez import BlueZ, is_divoom
from .config import DEFAULT_CONFIG_PATH, Config
from .gateway import Gateway
from .mdns import FILE_PREFIX, AvahiPublisher
from .rfcomm import bluetooth_supported, open_rfcomm

LOG = logging.getLogger("divoom-pi")


# --------------------------------------------------------------------------
# helpers


def setup_logging(level: str) -> None:
    # systemd's journal stamps every line itself, so only print our own
    # timestamps when a human is watching.
    under_systemd = "JOURNAL_STREAM" in os.environ or "INVOCATION_ID" in os.environ
    fmt = "%(levelname)-7s %(name)s: %(message)s"
    if not under_systemd:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def load_config(args: argparse.Namespace) -> Config:
    config = Config.load(getattr(args, "config", None))
    for override, attribute in (("port", "port"), ("listen", "listen"), ("log_level", "log_level")):
        value = getattr(args, override, None)
        if value is not None:
            setattr(config, attribute, value)
    config.validate()
    return config


def _table(rows) -> str:
    if not rows:
        return ""
    width = max(len(str(key)) for key, _ in rows)
    return "\n".join("  %-*s  %s" % (width, key, value) for key, value in rows)


# --------------------------------------------------------------------------
# commands


def command_run(args: argparse.Namespace) -> int:
    config = load_config(args)
    setup_logging(config.log_level)

    LOG.info("divoom-pi %s starting", __version__)
    if config.path:
        LOG.info("configuration: %s", config.path)
    else:
        LOG.info("no configuration file, using defaults")
    for warning in config.warnings:
        LOG.warning("configuration: %s", warning)

    gateway = Gateway(config, LOG)

    def handle_signal(signum, _frame):
        LOG.info("received %s", signal.Signals(signum).name)
        gateway.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    return gateway.run()


def command_scan(args: argparse.Namespace) -> int:
    config = load_config(args)
    setup_logging(args.log_level or "INFO")

    bluez = BlueZ(LOG.getChild("bluez"), executable=config.bluetoothctl)
    if not bluez.available():
        print("bluetoothctl not found - install it with: sudo apt install bluez")
        return 2

    bluez.power_on()
    print("Scanning for %d seconds..." % args.duration)
    devices = bluez.scan(args.duration)
    if not devices:
        print("No Bluetooth devices found at all. Is the adapter up (divoom-pi doctor)?")
        return 1

    divooms = 0
    for mac, name in sorted(devices.items(), key=lambda item: (not is_divoom(item[1]), item[0])):
        marker = "  <-- Divoom" if is_divoom(name) else ""
        print("  %s  %s%s" % (mac, name or "(no name)", marker))
        if is_divoom(name):
            divooms += 1

    print()
    if divooms:
        print("Found %d Divoom device(s)." % divooms)
        print("The RFCOMM channel is 2 for Ditoo/Ditoo Mic/Timoo, 4 for Aurabox/Timebox Mini,")
        print("and 1 for everything else. Try it with: divoom-pi selftest <MAC> --channel <n>")
    else:
        print("No Divoom devices among them. Make sure the device is on and is not")
        print("already connected to a phone - Divooms only accept one connection.")
    return 0 if divooms else 1


def command_pair(args: argparse.Namespace) -> int:
    config = load_config(args)
    setup_logging(args.log_level or "INFO")

    bluez = BlueZ(LOG.getChild("bluez"), executable=config.bluetoothctl)
    if not bluez.available():
        print("bluetoothctl not found - install it with: sudo apt install bluez")
        return 2

    mac = protocol.normalize_mac(args.mac)
    bluez.power_on()

    info = bluez.device_info(mac)
    if not info:
        print("%s is not in BlueZ's device cache; scanning for it first..." % mac)
        bluez.scan(args.duration)
        info = bluez.device_info(mac)
        if not info:
            print("Still not found. Is the device powered on and in range?")
            return 1

    if info.get("Paired", "no").lower() == "yes":
        print("%s is already paired." % mac)
    else:
        print("Pairing with %s..." % mac)
        if not bluez.pair(mac):
            print()
            print("Pairing failed. If this device asks for a PIN (Aurabox and some older")
            print("models use 0000), pair it by hand once:")
            print()
            print("    bluetoothctl")
            print("    [bluetooth]# agent KeyboardOnly")
            print("    [bluetooth]# default-agent")
            print("    [bluetooth]# pair %s" % mac)
            print()
            print("then enter the PIN when prompted. After that divoom-pi can connect.")
            return 1
        print("Paired.")

    if bluez.trust(mac):
        print("Trusted %s - it may now reconnect without asking again." % mac)
    return 0


def command_selftest(args: argparse.Namespace) -> int:
    """Do exactly what Home Assistant does, and print what comes back."""
    config = load_config(args)
    setup_logging(args.log_level or "INFO")

    mac = protocol.normalize_mac(args.mac)
    if args.action == "ping":
        payload = protocol.ping()
    else:
        payload = protocol.light(power=(args.action == "on"), brightness=100 if args.action == "on" else 0)

    if args.direct:
        return _selftest_direct(mac, args.channel, payload, config)
    return _selftest_via_gateway(mac, args.channel, payload, args.host, config.port)


def _selftest_direct(mac: str, channel: int, payload: bytes, config: Config) -> int:
    if not bluetooth_supported():
        print("This Python has no AF_BLUETOOTH support - divoom-pi needs Linux with BlueZ.")
        return 2
    print("Connecting straight over RFCOMM to %s channel %d..." % (mac, channel))
    try:
        sock = open_rfcomm(mac, channel, config.connect_timeout)
    except OSError as err:
        print("Connect failed: %s" % (err.strerror or err))
        print("Try `divoom-pi pair %s` first, and check the channel with `divoom-pi scan`." % mac)
        return 1
    try:
        print("Connected. Sending: %s" % protocol.hexdump(payload))
        sock.sendall(payload)
        return _drain(sock, "device")
    finally:
        sock.close()


def _selftest_via_gateway(mac: str, channel: int, payload: bytes, host: str, port: int) -> int:
    print("Connecting to the gateway at %s:%d..." % (host, port))
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as err:
        print("Could not reach the gateway: %s" % (err.strerror or err))
        print("Is it running?  systemctl status divoom-pi")
        return 1

    try:
        sock.settimeout(5)
        request = bytes([protocol.CMD_CONNECT]) + protocol.parse_mac(mac) + bytes([channel])
        print("Asking it to connect to %s channel %d: %s" % (mac, channel, protocol.hexdump(request)))
        sock.sendall(request)
        # The integration sleeps here too - the RFCOMM connect is not instant.
        time.sleep(2.0)

        print("Sending: %s" % protocol.hexdump(payload))
        sock.sendall(payload)
        result = _drain(sock, "gateway")
        if result == 0:
            return 0

        print()
        print("No reply. A single 0x69 byte back means the Bluetooth connect was still")
        print("in progress; a single 0x96 means it failed. Check the gateway's own log:")
        print("    journalctl -u divoom-pi -n 50")
        return result
    finally:
        sock.close()


def _drain(sock: socket.socket, source: str) -> int:
    sock.settimeout(3)
    received = b""
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            break
        except OSError as err:
            print("Read failed: %s" % (err.strerror or err))
            return 1
        if not chunk:
            break
        received += chunk
        if len(received) > 1:
            break

    if not received:
        print("Nothing came back from the %s." % source)
        return 1
    print("Got back: %s" % protocol.hexdump(received))
    if received == protocol.REPLY_CONNECTING:
        print("-> that is 'still connecting'. Run the selftest again in a few seconds.")
        return 1
    if received == protocol.REPLY_NOT_CONNECTED:
        print("-> that is 'no Bluetooth connection'. See: journalctl -u divoom-pi -n 50")
        return 1
    print("-> the Divoom answered. The whole path works.")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config = load_config(args)
    logging.basicConfig(level=logging.CRITICAL, stream=sys.stderr)

    failures = []
    warnings = []

    def check(label: str, ok: Optional[bool], detail: str = "") -> None:
        if ok is None:
            mark = "warn"
            warnings.append(label)
        elif ok:
            mark = " ok "
        else:
            mark = "FAIL"
            failures.append(label)
        print("[%s] %-34s %s" % (mark, label, detail))

    print("divoom-pi %s" % __version__)
    print()

    check(
        "python",
        sys.version_info >= (3, 7),
        "%d.%d.%d" % sys.version_info[:3],
    )
    check(
        "AF_BLUETOOTH support",
        bluetooth_supported(),
        "" if bluetooth_supported() else "not Linux, or a Python built without Bluetooth",
    )

    bluez = BlueZ(LOG.getChild("bluez"), executable=config.bluetoothctl)
    check("bluetoothctl", bluez.available(), bluez.path or "not found - sudo apt install bluez")

    if bluez.available():
        info = bluez.adapter_info()
        address = info.get("Address", "")
        check("bluetooth adapter", bool(address), address or "no controller found")
        powered = info.get("Powered", "no").lower() == "yes"
        check(
            "adapter powered",
            powered,
            "" if powered else "try: sudo bluetoothctl power on",
        )
        if info.get("Discovering", "").lower() == "yes":
            print("       (a scan is currently running)")

    rfkill = shutil.which("rfkill")
    if rfkill:
        blocked = _rfkill_blocked(rfkill)
        check(
            "bluetooth not rf-killed",
            not blocked,
            "" if not blocked else "try: sudo rfkill unblock bluetooth",
        )

    reason = AvahiPublisher(
        LOG, port=config.port, service_dir=config.mdns_service_dir, enabled=config.mdns_enabled
    ).available()
    check("avahi service directory", reason is None, reason or config.mdns_service_dir)

    if os.path.isdir(config.mdns_service_dir):
        published = sorted(
            entry
            for entry in os.listdir(config.mdns_service_dir)
            if entry.startswith(FILE_PREFIX)
        )
        check(
            "published mDNS records",
            bool(published) or None,
            ", ".join(published) or "none yet - has a scan found your Divoom?",
        )

    for unit in ("bluetooth", "avahi-daemon", "divoom-pi"):
        state = _unit_state(unit)
        check("systemd: %s" % unit, state == "active" or None, state)

    listening = _port_open("127.0.0.1", config.port)
    check(
        "gateway port %d" % config.port,
        listening,
        "accepting connections" if listening else "nothing listening - is divoom-pi running?",
    )

    print()
    print("configuration")
    print(_table(config.describe()))
    for warning in config.warnings:
        print("  ! %s" % warning)

    print()
    if failures:
        print("%d check(s) failed: %s" % (len(failures), ", ".join(failures)))
        return 1
    if warnings:
        print("Everything essential looks fine (%d warning(s))." % len(warnings))
    else:
        print("Everything looks fine.")
    return 0


def _rfkill_blocked(rfkill: str) -> bool:
    try:
        output = subprocess.run(
            [rfkill, "list", "bluetooth"], stdout=subprocess.PIPE, timeout=10
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return False
    return "blocked: yes" in output.lower()


def _unit_state(unit: str) -> str:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "systemd not present"
    try:
        result = subprocess.run(
            [systemctl, "is-active", unit],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.decode("utf-8", "replace").strip() or "unknown"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# argument parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="divoom-pi",
        description="Bluetooth Classic to TCP gateway for Divoom devices.",
    )
    parser.add_argument("--version", action="version", version="divoom-pi %s" % __version__)
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="configuration file (default: %s)" % DEFAULT_CONFIG_PATH,
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="override the configured log level",
    )

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the gateway (what the systemd service does)")
    run.add_argument("--listen", metavar="ADDRESS", help="override the listen address")
    run.add_argument("--port", type=int, help="override the listen port")
    run.set_defaults(func=command_run)

    scan = sub.add_parser("scan", help="scan for Bluetooth devices and flag the Divooms")
    scan.add_argument("--duration", type=int, default=15, help="scan length in seconds")
    scan.set_defaults(func=command_scan)

    pair = sub.add_parser("pair", help="pair with a Divoom device")
    pair.add_argument("mac", help="the Divoom's Bluetooth address")
    pair.add_argument("--duration", type=int, default=15, help="scan length if it must look first")
    pair.set_defaults(func=command_pair)

    selftest = sub.add_parser(
        "selftest",
        help="do what Home Assistant does - connect and send one command",
    )
    selftest.add_argument("mac", help="the Divoom's Bluetooth address")
    selftest.add_argument(
        "--channel", type=int, default=1, help="RFCOMM channel (2 for Ditoo, 4 for Aurabox)"
    )
    selftest.add_argument(
        "--action", choices=["ping", "on", "off"], default="ping", help="what to send"
    )
    selftest.add_argument("--host", default="127.0.0.1", help="gateway host to test through")
    selftest.add_argument(
        "--direct",
        action="store_true",
        help="bypass the gateway and talk to the Divoom over Bluetooth directly",
    )
    selftest.set_defaults(func=command_selftest)

    doctor = sub.add_parser("doctor", help="check the install and print what is wrong")
    doctor.set_defaults(func=command_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except ValueError as err:
        print("error: %s" % err, file=sys.stderr)
        return 2
