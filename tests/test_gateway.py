"""End-to-end tests of the TCP side of the gateway, with Bluetooth faked out.

A real Divoom is needed to test the Bluetooth half (`divoom-pi selftest` does
that), but everything Home Assistant actually touches - accepting clients,
parsing the stream, relaying both directions, and the two single-byte "I could
not deliver that" replies - is exercised here with a stand-in RFCOMM link.
"""

import logging
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from divoom_pi import gateway as gateway_module  # noqa: E402
from divoom_pi import protocol  # noqa: E402
from divoom_pi.config import Config  # noqa: E402
from divoom_pi.rfcomm import (  # noqa: E402
    CONNECTED,
    CONNECTING,
    DISCONNECTED,
    SEND_NOT_CONNECTED,
    SEND_STILL_CONNECTING,
)

MAC = "B1:21:81:BF:A8:EB"

QUIET = logging.getLogger("test-gateway")
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


class FakeLink(object):
    """Stands in for RfcommLink, recording what the gateway asks of it."""

    def __init__(self, on_data, logger, **_kwargs):
        self.on_data = on_data
        self.state = DISCONNECTED
        self.connects = []
        self.disconnects = []
        self.sent = []
        self.send_result = None

    def connect(self, mac, channel):
        self.connects.append((mac, channel))
        self.state = CONNECTED

    def disconnect(self, reason="requested"):
        self.disconnects.append(reason)
        self.state = DISCONNECTED

    def close(self):
        self.disconnect("closing")

    def describe(self):
        return self.state

    def send(self, data):
        self.sent.append(data)
        if self.send_result is not None:
            return self.send_result
        return len(data)


class FakeBlueZ(object):
    def available(self):
        return False

    def power_on(self):
        return False

    def device_info(self, _mac):
        return {}


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self._real_link = gateway_module.RfcommLink
        self._real_supported = gateway_module.bluetooth_supported
        gateway_module.RfcommLink = FakeLink
        gateway_module.bluetooth_supported = lambda: True

        config = Config()
        config.listen = "127.0.0.1"
        # Let the OS pick the port and read it back from the gateway, rather
        # than guessing one that might still be in use by the previous test.
        config.port = 0
        config.scan_enabled = False
        config.mdns_enabled = False
        self.config = config

        self.gateway = gateway_module.Gateway(config, QUIET)
        self.gateway._bluez = FakeBlueZ()
        self.link = self.gateway._link

        self.thread = threading.Thread(target=self.gateway.run, daemon=True)
        self.thread.start()
        self._wait_for_port()

    def tearDown(self):
        self.gateway.stop()
        self.thread.join(timeout=5)
        gateway_module.RfcommLink = self._real_link
        gateway_module.bluetooth_supported = self._real_supported

    def _wait_for_port(self):
        self._wait(lambda: self.gateway.bound_port is not None, "gateway never bound a port")

    def client(self):
        sock = socket.create_connection(("127.0.0.1", self.gateway.bound_port), timeout=5)
        sock.settimeout(5)
        self.addCleanup(sock.close)
        return sock

    def _wait(self, predicate, message):
        deadline = time.time() + 5
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    # -- tests ---------------------------------------------------------------

    def test_connect_message_opens_the_bluetooth_link(self):
        sock = self.client()
        sock.sendall(bytes([protocol.CMD_CONNECT]) + protocol.parse_mac(MAC) + bytes([2]))
        self._wait(lambda: self.link.connects, "gateway never asked for a Bluetooth connection")
        self.assertEqual(self.link.connects[0], (MAC, 2))

    def test_payload_is_relayed_verbatim(self):
        sock = self.client()
        sock.sendall(bytes([protocol.CMD_CONNECT]) + protocol.parse_mac(MAC) + bytes([2]))
        self._wait(lambda: self.link.connects, "no connect")

        ping = protocol.ping()
        sock.sendall(ping)
        self._wait(lambda: self.link.sent, "payload never reached the Bluetooth link")
        self.assertEqual(self.link.sent[0], ping)

    def test_device_data_is_relayed_back_to_every_client(self):
        first, second = self.client(), self.client()
        self._wait(lambda: len(self.gateway._clients) == 2, "clients never registered")

        self.link.on_data(b"\x01\x02\x03")
        for sock in (first, second):
            self.assertEqual(sock.recv(16), b"\x01\x02\x03")

    def test_payload_while_connecting_replies_0x69(self):
        sock = self.client()
        self.link.state = CONNECTING
        self.link.send_result = SEND_STILL_CONNECTING
        sock.sendall(protocol.ping())
        self.assertEqual(sock.recv(16), protocol.REPLY_CONNECTING)

    def test_payload_without_a_link_replies_0x96(self):
        sock = self.client()
        self.link.send_result = SEND_NOT_CONNECTED
        sock.sendall(protocol.ping())
        self.assertEqual(sock.recv(16), protocol.REPLY_NOT_CONNECTED)

    def test_disconnect_message_closes_the_link(self):
        sock = self.client()
        sock.sendall(bytes([protocol.CMD_DISCONNECT]) + protocol.parse_mac(MAC))
        self._wait(lambda: self.link.disconnects, "gateway never disconnected the link")

    def test_client_limit_is_enforced(self):
        self.config.max_clients = 1
        self.client()
        self._wait(lambda: len(self.gateway._clients) == 1, "first client never registered")

        extra = socket.create_connection(("127.0.0.1", self.gateway.bound_port), timeout=5)
        self.addCleanup(extra.close)
        extra.settimeout(5)
        self.assertEqual(extra.recv(16), b"", "the gateway should have closed the extra client")

    def test_new_clients_get_no_unsolicited_data(self):
        # A client must be able to connect, send a command and read the answer
        # without the gateway having pushed anything at it first - anything it
        # does push lands in front of the reply the client is waiting for.
        self.gateway._discovered[MAC] = "Ditoo"
        sock = self.client()
        sock.settimeout(0.5)
        with self.assertRaises(socket.timeout):
            sock.recv(64)

    def test_a_dropped_client_does_not_take_the_gateway_down(self):
        first = self.client()
        first.close()
        self._wait(lambda: not self.gateway._clients, "client was never cleaned up")

        second = self.client()
        second.sendall(bytes([protocol.CMD_CONNECT]) + protocol.parse_mac(MAC) + bytes([2]))
        self._wait(lambda: self.link.connects, "gateway stopped working after a client left")


if __name__ == "__main__":
    unittest.main()
