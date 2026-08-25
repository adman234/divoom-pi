"""The Bluetooth Classic half of the gateway: an RFCOMM client link.

This is the part the ESP32 needed a whole vendored BluetoothSerial stack, four
layers of ESP-IDF Kconfig options and a BR/EDR controller-mode fix to do. On
Linux with BlueZ it is a plain socket:

    socket.socket(AF_BLUETOOTH, SOCK_STREAM, BTPROTO_RFCOMM)
    sock.connect((mac, channel))

Connecting is done on a worker thread because it can block for many seconds
when the Divoom is asleep or out of range, and the gateway's select loop has to
keep answering Home Assistant in the meantime. That is also why send() reports
"still connecting" separately from "not connected": the protocol has a distinct
reply byte for each, and the integration's reconnect() behaves differently.
"""

from __future__ import annotations

import errno
import socket
import sys
import threading
from typing import Callable, Optional

# Result codes from send(), matching the firmware's bt_send_() contract.
SEND_STILL_CONNECTING = 0
SEND_NOT_CONNECTED = -1

DISCONNECTED = "disconnected"
CONNECTING = "connecting"
CONNECTED = "connected"


def bluetooth_supported() -> bool:
    """True when this Python can open BlueZ RFCOMM sockets.

    Windows Python also exposes AF_BLUETOOTH, but with a different address
    format and no BlueZ behind it, so the platform is part of the check.
    """
    return (
        sys.platform.startswith("linux")
        and hasattr(socket, "AF_BLUETOOTH")
        and hasattr(socket, "BTPROTO_RFCOMM")
    )


def open_rfcomm(mac: str, channel: int, timeout: float) -> socket.socket:
    """Open a connected RFCOMM socket, or raise. Used by the CLI self-test too."""
    if not bluetooth_supported():
        raise RuntimeError(
            "this Python build has no AF_BLUETOOTH support - divoom-pi needs Linux with BlueZ"
        )
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    try:
        sock.settimeout(timeout)
        sock.connect((mac, channel))
    except Exception:
        sock.close()
        raise
    return sock


class RfcommLink:
    """A single RFCOMM link to one Divoom device, with async connect."""

    def __init__(
        self,
        on_data: Callable[[bytes], None],
        logger,
        connect_timeout: float = 15.0,
        read_timeout: float = 1.0,
        write_chunk: int = 0,
        write_delay: float = 0.0,
        prepare: Optional[Callable[[str], None]] = None,
        on_state: Optional[Callable[[str, Optional[str]], None]] = None,
    ):
        self._on_data = on_data
        self._on_state = on_state
        self._prepare = prepare
        self._log = logger
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._write_chunk = write_chunk
        self._write_delay = write_delay

        self._lock = threading.RLock()
        self._sock: Optional[socket.socket] = None
        self._state = DISCONNECTED
        # Bumped on every connect/disconnect so that a worker thread whose
        # connect has been superseded can notice and clean up after itself
        # instead of installing a stale socket.
        self._generation = 0
        self.mac: Optional[str] = None
        self.channel: Optional[int] = None

    # -- state ---------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def connected(self) -> bool:
        return self.state == CONNECTED

    def describe(self) -> str:
        with self._lock:
            if self._state == DISCONNECTED:
                return "disconnected"
            return "%s %s channel %s" % (self._state, self.mac, self.channel)

    def _set_state(self, state: str) -> None:
        # Caller holds the lock.
        if self._state == state:
            return
        self._state = state
        if self._on_state is not None:
            try:
                self._on_state(state, self.mac)
            except Exception:  # pragma: no cover - a status hook must never kill the link
                self._log.exception("state callback failed")

    # -- connect / disconnect ------------------------------------------------

    def connect(self, mac: str, channel: int) -> None:
        """Start connecting (returns immediately)."""
        with self._lock:
            if self._state == CONNECTED and self.mac == mac and self.channel == channel:
                self._log.info("already connected to %s channel %d, ignoring connect", mac, channel)
                return
            if self._state == CONNECTING and self.mac == mac and self.channel == channel:
                self._log.info("already connecting to %s channel %d, ignoring connect", mac, channel)
                return

            self._teardown_locked("superseded by a new connect request")
            self._generation += 1
            generation = self._generation
            self.mac = mac
            self.channel = channel
            self._set_state(CONNECTING)

        self._log.info("connecting to %s on RFCOMM channel %d", mac, channel)
        worker = threading.Thread(
            target=self._connect_worker,
            args=(mac, channel, generation),
            name="rfcomm-connect",
            daemon=True,
        )
        worker.start()

    def disconnect(self, reason: str = "requested") -> None:
        with self._lock:
            if self._state == DISCONNECTED and self._sock is None:
                return
            self._generation += 1
            self._teardown_locked(reason)

    def close(self) -> None:
        self.disconnect("shutting down")

    def _teardown_locked(self, reason: str) -> None:
        # Caller holds the lock. Does not bump the generation - callers that
        # need the old worker/reader to bow out do that themselves.
        sock = self._sock
        self._sock = None
        if sock is not None:
            self._log.info("closing Bluetooth link to %s (%s)", self.mac, reason)
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._set_state(DISCONNECTED)

    def _connect_worker(self, mac: str, channel: int, generation: int) -> None:
        sock = None
        try:
            if self._prepare is not None:
                try:
                    self._prepare(mac)
                except Exception:
                    self._log.exception("pre-connect preparation for %s failed, connecting anyway", mac)
            sock = open_rfcomm(mac, channel, self._connect_timeout)
            sock.settimeout(self._read_timeout)
        except socket.timeout:
            self._fail(generation, "timed out after %.0fs" % self._connect_timeout)
            return
        except OSError as err:
            self._fail(generation, "%s (errno %s)" % (err.strerror or err, err.errno))
            return
        except Exception as err:  # pragma: no cover - defensive
            self._fail(generation, str(err))
            return

        with self._lock:
            if generation != self._generation:
                self._log.info("connect to %s completed but was superseded, dropping it", mac)
                try:
                    sock.close()
                except OSError:
                    pass
                return
            self._sock = sock
            self._set_state(CONNECTED)

        self._log.info("connected to %s on RFCOMM channel %d", mac, channel)
        reader = threading.Thread(
            target=self._read_loop, args=(sock, generation), name="rfcomm-reader", daemon=True
        )
        reader.start()

    def _fail(self, generation: int, detail: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._log.warning("could not connect to %s: %s", self.mac, detail)
            self._teardown_locked("connect failed")

    # -- data ----------------------------------------------------------------

    def _read_loop(self, sock: socket.socket, generation: int) -> None:
        while True:
            with self._lock:
                if generation != self._generation:
                    return
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as err:
                if err.errno == errno.EINTR:
                    continue
                self._drop(generation, "read failed: %s" % (err.strerror or err))
                return
            if not data:
                self._drop(generation, "device closed the connection")
                return
            try:
                self._on_data(data)
            except Exception:  # pragma: no cover - a relay failure must not kill the reader
                self._log.exception("failed to relay %d bytes from the Divoom device", len(data))

    def _drop(self, generation: int, reason: str) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._generation += 1
            self._teardown_locked(reason)

    def send(self, data: bytes) -> int:
        """Relay bytes to the device.

        Returns the number of bytes written, SEND_STILL_CONNECTING (0) if the
        link is mid-connect, or SEND_NOT_CONNECTED (-1) if there is no link.
        """
        with self._lock:
            if self._state == CONNECTING:
                return SEND_STILL_CONNECTING
            if self._state != CONNECTED or self._sock is None:
                return SEND_NOT_CONNECTED
            sock = self._sock
            generation = self._generation

        try:
            if self._write_chunk > 0 and len(data) > self._write_chunk:
                for offset in range(0, len(data), self._write_chunk):
                    sock.sendall(data[offset : offset + self._write_chunk])
                    if self._write_delay > 0:
                        threading.Event().wait(self._write_delay)
            else:
                sock.sendall(data)
        except OSError as err:
            self._log.warning("write to %s failed: %s", self.mac, err.strerror or err)
            self._drop(generation, "write failed")
            return SEND_NOT_CONNECTED
        return len(data)
