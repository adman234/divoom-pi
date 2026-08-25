"""The gateway itself: a TCP server that relays to one Bluetooth Classic link.

One thread runs the select loop over the listening socket and every connected
Home Assistant client. Bluetooth connecting, reading and device discovery each
get their own thread, because all three can block for seconds at a time and the
select loop has to stay responsive - a Home Assistant service call that gets no
answer is exactly the failure this project exists to avoid.
"""

from __future__ import annotations

import selectors
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import protocol
from .bluez import BlueZ, is_divoom
from .config import Config
from .mdns import AvahiPublisher
from .rfcomm import (
    DISCONNECTED,
    SEND_NOT_CONNECTED,
    SEND_STILL_CONNECTING,
    RfcommLink,
    bluetooth_supported,
)


class Client(object):
    """One connected TCP client (normally Home Assistant)."""

    def __init__(self, sock: socket.socket, address, logger):
        self.sock = sock
        self.address = address
        self.parser = protocol.MessageParser(logger)
        self.connected_at = time.time()
        self.bytes_in = 0
        self.bytes_out = 0
        self.close_reason = "closed"

    @property
    def name(self) -> str:
        return "%s:%s" % (self.address[0], self.address[1])


class Gateway(object):
    def __init__(self, config: Config, logger):
        self._config = config
        self._log = logger

        self._stop = threading.Event()
        self._selector: Optional[selectors.BaseSelector] = None
        self._listener: Optional[socket.socket] = None
        # The port actually bound, which differs from the configured one only
        # when the configuration asks for port 0 (used by the tests).
        self.bound_port: Optional[int] = None
        self._wake_r: Optional[socket.socket] = None
        self._wake_w: Optional[socket.socket] = None

        self._clients: Dict[int, Client] = {}
        self._clients_lock = threading.RLock()
        # Only the select loop's own thread may touch the selector, so clients
        # dropped by the Bluetooth or discovery threads are queued here and
        # reaped by the loop instead.
        self._main_thread: Optional[threading.Thread] = None
        self._pending_close: List[Client] = []
        self._pending_lock = threading.Lock()

        self._bluez = BlueZ(logger.getChild("bluez"), executable=config.bluetoothctl)
        self._mdns = AvahiPublisher(
            logger.getChild("mdns"),
            port=config.port,
            service_dir=config.mdns_service_dir,
            enabled=config.mdns_enabled,
        )
        self._link = RfcommLink(
            on_data=self._on_bluetooth_data,
            logger=logger.getChild("bluetooth"),
            connect_timeout=config.connect_timeout,
            read_timeout=config.read_timeout,
            write_chunk=config.write_chunk,
            write_delay=config.write_delay,
            prepare=self._prepare_connection if config.auto_pair else None,
        )

        self._discovered: Dict[str, str] = {}
        self._discovered_lock = threading.Lock()
        self._discovery_thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> int:
        self._main_thread = threading.current_thread()
        if not bluetooth_supported():
            self._log.error(
                "this Python has no AF_BLUETOOTH support - divoom-pi needs Linux with BlueZ"
            )
            return 2

        if not self._bluez.available():
            self._log.warning(
                "bluetoothctl not found: discovery and pairing are disabled. "
                "Install it with: sudo apt install bluez"
            )
        else:
            self._bluez.power_on()

        reason = self._mdns.available()
        if reason is None:
            removed = self._mdns.clear()
            if removed:
                self._log.debug("cleared %d stale mDNS record(s)", removed)
        else:
            self._log.warning("mDNS discovery is off: %s", reason)

        try:
            self._open_listener()
        except OSError as err:
            self._log.error(
                "could not listen on %s:%d: %s", self._config.listen, self._config.port, err
            )
            return 1

        self._selector = selectors.DefaultSelector()
        self._selector.register(self._listener, selectors.EVENT_READ, self._accept)
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._selector.register(self._wake_r, selectors.EVENT_READ, self._drain_wakeup)

        if self._config.scan_enabled:
            self._discovery_thread = threading.Thread(
                target=self._discovery_loop, name="discovery", daemon=True
            )
            self._discovery_thread.start()
        else:
            self._log.info("Bluetooth discovery is disabled in the configuration")

        self._log.info("divoom-pi listening on %s:%d", self._config.listen, self.bound_port)
        try:
            self._serve()
        finally:
            self._shutdown()
        return 0

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._log.info("shutting down")
        self._stop.set()
        self._wake()

    def _wake(self) -> None:
        if self._wake_w is not None:
            try:
                self._wake_w.send(b"\x01")
            except OSError:
                pass

    def _drain_wakeup(self, sock: socket.socket) -> None:
        try:
            sock.recv(4096)
        except OSError:
            pass

    def _open_listener(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._config.listen, self._config.port))
        listener.listen(self._config.max_clients + 2)
        listener.setblocking(False)
        self._listener = listener
        self.bound_port = listener.getsockname()[1]

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._selector.select(timeout=1.0)
            except OSError as err:
                if self._stop.is_set():
                    break
                self._log.warning("select failed: %s", err)
                continue
            for key, _mask in events:
                try:
                    key.data(key.fileobj)
                except Exception:  # pragma: no cover - never let one client kill the loop
                    self._log.exception("error handling %s", key.fileobj)
            self._reap_closed_clients()

    def _shutdown(self) -> None:
        self._link.close()
        self._reap_closed_clients()
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            self._close_socket(client.sock)
        for sock in (self._listener, self._wake_r, self._wake_w):
            if sock is not None:
                self._close_socket(sock)
        if self._selector is not None:
            self._selector.close()
        self._log.info("stopped")

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass

    # -- TCP clients ---------------------------------------------------------

    def _accept(self, listener: socket.socket) -> None:
        try:
            sock, address = listener.accept()
        except OSError as err:
            self._log.warning("accept failed: %s", err)
            return

        with self._clients_lock:
            over_limit = len(self._clients) >= self._config.max_clients
        if over_limit:
            self._log.warning(
                "refusing %s: already serving the configured maximum of %d clients",
                address,
                self._config.max_clients,
            )
            self._close_socket(sock)
            return

        sock.settimeout(5.0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client = Client(sock, address, self._log.getChild("client"))
        with self._clients_lock:
            self._clients[sock.fileno()] = client
        self._selector.register(sock, selectors.EVENT_READ, self._read_client)
        self._log.info("client connected: %s", client.name)
        # Deliberately silent here. Announcing the already-discovered devices
        # to a new client seemed helpful, but the firmware only advertises
        # after a scan, and pushing twenty-odd unsolicited bytes at a client
        # the instant it connects drops them exactly where it is about to read
        # for a reply - which made `divoom-pi selftest` report success on its
        # own gateway's announcement while nothing reached the Divoom.

    def _read_client(self, sock: socket.socket) -> None:
        with self._clients_lock:
            client = self._clients.get(sock.fileno())
        if client is None:
            return

        try:
            data = sock.recv(4096)
        except socket.timeout:
            return
        except OSError as err:
            self._drop_client(client, "read failed: %s" % err)
            return

        if not data:
            self._drop_client(client, "closed the connection")
            return

        client.bytes_in += len(data)
        self._log.debug("%s -> gateway: %s", client.name, protocol.hexdump(data))
        for message in client.parser.feed(data):
            self._handle(client, message)

    def _drop_client(self, client: Client, reason: str) -> None:
        with self._clients_lock:
            if self._clients.pop(client.sock.fileno(), None) is None:
                return  # somebody else already dropped it
        client.close_reason = reason

        if threading.current_thread() is self._main_thread:
            self._finish_close(client)
            return
        with self._pending_lock:
            self._pending_close.append(client)
        self._wake()

    def _reap_closed_clients(self) -> None:
        with self._pending_lock:
            pending = self._pending_close
            self._pending_close = []
        for client in pending:
            self._finish_close(client)

    def _finish_close(self, client: Client) -> None:
        try:
            self._selector.unregister(client.sock)
        except (KeyError, ValueError, OSError):
            pass
        self._close_socket(client.sock)
        self._log.info(
            "client disconnected: %s (%s; %d bytes in, %d bytes out)",
            client.name,
            client.close_reason,
            client.bytes_in,
            client.bytes_out,
        )

    def _send_to(self, client: Client, data: bytes) -> bool:
        try:
            client.sock.sendall(data)
            client.bytes_out += len(data)
            return True
        except OSError as err:
            self._drop_client(client, "write failed: %s" % err)
            return False

    def _broadcast(self, data: bytes) -> None:
        with self._clients_lock:
            clients = list(self._clients.values())
        for client in clients:
            self._send_to(client, data)

    # -- protocol ------------------------------------------------------------

    def _handle(self, client: Client, message) -> None:
        if isinstance(message, protocol.Connect):
            self._log.info(
                "%s asked for a Bluetooth connection to %s on channel %d",
                client.name,
                message.mac,
                message.channel,
            )
            self._link.connect(message.mac, message.channel)
            return

        if isinstance(message, protocol.Disconnect):
            self._log.info("%s asked to disconnect %s", client.name, message.mac)
            self._link.disconnect("requested by %s" % client.name)
            return

        if isinstance(message, protocol.Payload):
            result = self._link.send(message.data)
            if result == SEND_STILL_CONNECTING:
                self._log.debug(
                    "dropped %d byte payload: Bluetooth still connecting", len(message.data)
                )
                self._broadcast(protocol.REPLY_CONNECTING)
            elif result == SEND_NOT_CONNECTED:
                self._log.debug(
                    "dropped %d byte payload: no Bluetooth connection", len(message.data)
                )
                self._broadcast(protocol.REPLY_NOT_CONNECTED)

    def _on_bluetooth_data(self, data: bytes) -> None:
        self._log.debug("device -> gateway: %s", protocol.hexdump(data))
        self._broadcast(data)

    def _prepare_connection(self, mac: str) -> None:
        """Best-effort pairing before the first connect to an unknown device.

        Plenty of Divoom devices accept an RFCOMM connection without pairing at
        all; the ones that don't will usually go through a just-works pairing.
        A device with a fixed PIN needs `bluetoothctl` run interactively once -
        see the README - and this will simply fail and be logged.
        """
        if not self._bluez.available():
            return
        try:
            info = self._bluez.device_info(mac)
        except Exception:
            self._log.debug("could not query pairing state for %s", mac, exc_info=True)
            return
        if not info:
            self._log.debug("%s is not in BlueZ's device cache yet", mac)
            return
        if info.get("Paired", "no").lower() == "yes":
            if info.get("Trusted", "no").lower() != "yes":
                self._bluez.trust(mac)
            return
        self._log.info("%s is not paired yet, attempting a just-works pairing", mac)
        if self._bluez.pair(mac):
            self._bluez.trust(mac)

    # -- discovery -----------------------------------------------------------

    def _discovery_loop(self) -> None:
        config = self._config
        if self._stop.wait(config.scan_initial_delay):
            return

        interval = config.scan_interval
        while not self._stop.is_set():
            if self._link.state != DISCONNECTED and not config.scan_while_connected:
                # An inquiry scan and an active RFCOMM link share one radio.
                # Discovery has all the time in the world; a Home Assistant
                # service call does not.
                self._log.debug("skipping discovery scan: Bluetooth link is busy")
                if self._stop.wait(min(interval, 30.0)):
                    return
                continue

            try:
                found = self._scan_once()
            except Exception:  # pragma: no cover - discovery must never kill the gateway
                self._log.exception("discovery scan failed")
                found = 0

            interval = config.scan_interval if found else config.scan_idle_interval
            if self._stop.wait(interval):
                return

    def _scan_once(self) -> int:
        if not self._bluez.available():
            return 0

        self._log.debug("starting a %ds Bluetooth discovery scan", self._config.scan_duration)
        devices = self._bluez.scan(self._config.scan_duration)
        self._log.debug("scan saw %d device(s): %s", len(devices), devices or "none")

        published = 0
        for mac, name in sorted(devices.items()):
            supported = is_divoom(name)
            if self._config.filter_names and not supported:
                continue
            if not name:
                continue
            with self._discovered_lock:
                is_new = self._discovered.get(mac) != name
                self._discovered[mac] = name
            if is_new:
                self._log.info("discovered Divoom device %s (%s)", name, mac)
            if supported and self._mdns.publish(mac, name):
                published += 1
            self._broadcast(protocol.advertisement(mac, name))

        if not self._discovered:
            self._log.info(
                "no Divoom devices found yet - make sure the device is powered on and "
                "not already connected to a phone"
            )
        return published

    # -- introspection -------------------------------------------------------

    def status(self) -> List[Tuple[str, str]]:
        with self._clients_lock:
            clients = [client.name for client in self._clients.values()]
        with self._discovered_lock:
            discovered = sorted(self._discovered.items())
        return [
            ("listening on", "%s:%s" % (self._config.listen, self.bound_port)),
            ("clients", ", ".join(clients) or "none"),
            ("bluetooth", self._link.describe()),
            (
                "discovered",
                ", ".join("%s (%s)" % (name, mac) for mac, name in discovered) or "none",
            ),
            ("mdns records", ", ".join(sorted(self._mdns.published())) or "none"),
        ]
