"""Wire-protocol tests.

These are the tests worth having: the framing is the one part of divoom-pi that
has to match the ESP32 firmware and the Home Assistant integration byte for
byte, and it is the one part that can be checked without a Divoom on the desk.
Everything else needs real Bluetooth hardware - see `divoom-pi selftest`.
"""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from divoom_pi import protocol  # noqa: E402

QUIET = logging.getLogger("test")
QUIET.addHandler(logging.NullHandler())
QUIET.propagate = False


class MacTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(protocol.format_mac(bytes.fromhex("b12181bfa8eb")), "B1:21:81:BF:A8:EB")
        self.assertEqual(protocol.parse_mac("b1:21:81:bf:a8:eb"), bytes.fromhex("b12181bfa8eb"))

    def test_accepts_alternative_spellings(self):
        for spelling in ("B1:21:81:BF:A8:EB", "b1-21-81-bf-a8-eb", "b12181bfa8eb"):
            self.assertEqual(protocol.normalize_mac(spelling), "B1:21:81:BF:A8:EB")

    def test_rejects_nonsense(self):
        for bad in ("", "not a mac", "b1:21:81:bf:a8"):
            with self.assertRaises(ValueError):
                protocol.parse_mac(bad)


class BuildTest(unittest.TestCase):
    def test_ping_matches_the_integration(self):
        # send_command("get view") in custom_components/divoom/devices/divoom.py:
        # length 3 little-endian, command 0x46, checksum 0x0049 little-endian.
        self.assertEqual(protocol.ping(), bytes.fromhex("01030046490002"))

    def test_light_on_matches_the_integration(self):
        # show_light(color=[1,1,1], brightness=100, power=True) -> "set view".
        self.assertEqual(
            protocol.light(power=True, brightness=100),
            bytes.fromhex("010d004501010101640001000000bb0002"),
        )

    def test_checksum_widens_past_65535(self):
        # The integration switches to a four-byte checksum once the sum no
        # longer fits in two, and the Pixoo Max relies on that.
        small = protocol.build_message(bytes([0x01]) * 4)
        self.assertEqual(len(small) - 4 - 2, 2)

        payload = bytes([0xFF]) * 300  # sums to 76500
        message = protocol.build_message(payload)
        self.assertEqual(message[0], protocol.FRAME_START)
        self.assertEqual(message[-1], protocol.FRAME_END)
        self.assertEqual(len(message) - len(payload) - 2, 4)

    def test_advertisement_layout(self):
        advert = protocol.advertisement("B1:21:81:BF:A8:EB", "Ditoo")
        self.assertEqual(advert[0], protocol.ADVERTISE)
        self.assertEqual(advert[1:7], bytes.fromhex("b12181bfa8eb"))
        self.assertEqual(advert[7], 5)
        self.assertEqual(advert[8:], b"Ditoo")


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = protocol.MessageParser(QUIET)

    def connect_bytes(self, channel=None):
        raw = bytes([protocol.CMD_CONNECT]) + protocol.parse_mac("B1:21:81:BF:A8:EB")
        return raw if channel is None else raw + bytes([channel])

    def test_connect_with_channel(self):
        messages = self.parser.feed(self.connect_bytes(2))
        self.assertEqual(messages, [protocol.Connect("B1:21:81:BF:A8:EB", 2)])

    def test_connect_without_channel_defaults_to_one(self):
        messages = self.parser.feed(self.connect_bytes())
        self.assertEqual(messages, [protocol.Connect("B1:21:81:BF:A8:EB", 1)])

    def test_connect_split_across_reads(self):
        raw = self.connect_bytes(2)
        self.assertEqual(self.parser.feed(raw[:3]), [])
        self.assertEqual(self.parser.feed(raw[3:]), [protocol.Connect("B1:21:81:BF:A8:EB", 2)])

    def test_disconnect(self):
        raw = bytes([protocol.CMD_DISCONNECT]) + protocol.parse_mac("B1:21:81:BF:A8:EB")
        self.assertEqual(self.parser.feed(raw), [protocol.Disconnect("B1:21:81:BF:A8:EB")])

    def test_payload(self):
        ping = protocol.ping()
        self.assertEqual(self.parser.feed(ping), [protocol.Payload(ping)])

    def test_payload_split_across_reads(self):
        ping = protocol.ping()
        self.assertEqual(self.parser.feed(ping[:4]), [])
        self.assertEqual(self.parser.feed(ping[4:]), [protocol.Payload(ping)])
        self.assertEqual(self.parser.pending, 0)

    def test_connect_followed_by_payload_in_one_read(self):
        ping = protocol.ping()
        messages = self.parser.feed(self.connect_bytes(2) + ping)
        self.assertEqual(
            messages, [protocol.Connect("B1:21:81:BF:A8:EB", 2), protocol.Payload(ping)]
        )

    def test_payload_containing_an_unescaped_0x02(self):
        # Escaping is off by default, so 0x02 can appear inside a payload. The
        # frame still ends at the read boundary, not at the first 0x02.
        payload = protocol.build_command(0x44, bytes([0x02, 0x02, 0x02]))
        self.assertIn(0x02, payload[1:-1])
        self.assertEqual(self.parser.feed(payload), [protocol.Payload(payload)])

    def test_garbage_is_resynchronised(self):
        ping = protocol.ping()
        self.assertEqual(self.parser.feed(b"\xaa\xbb" + ping), [protocol.Payload(ping)])

    def test_unterminated_payload_is_dropped_eventually(self):
        parser = protocol.MessageParser(QUIET, max_message=32)
        self.assertEqual(parser.feed(bytes([protocol.FRAME_START]) + b"\x00" * 64), [])
        self.assertEqual(parser.pending, 0)


if __name__ == "__main__":
    unittest.main()
