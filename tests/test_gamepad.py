import unittest

from frameplayer.gamepad import PadEvent, actions_for, normalize_axis, parse_js_packet
import struct


class GamepadTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        packet = struct.pack("=IhBB", 10, 32767, 2, 3)
        parsed = parse_js_packet(packet)
        self.assertEqual(parsed[1:], (32767, 2, 3))

    def test_triggers_rest_at_zero(self):
        self.assertAlmostEqual(normalize_axis(2, -32767), 0)
        self.assertAlmostEqual(normalize_axis(2, 32767), 1)
        self.assertAlmostEqual(normalize_axis(0, 0), 0)

    def test_watch_controls(self):
        press = lambda name: PadEvent(name, 1, True, True)
        self.assertEqual(actions_for(press("a"), chrome_visible=False, spherical=False, focus_is_button=False), ("play_pause",))
        self.assertEqual(actions_for(press("a"), chrome_visible=True, spherical=False, focus_is_button=True), ("activate",))
        self.assertEqual(actions_for(press("dpad_left"), chrome_visible=False, spherical=True, focus_is_button=False), ("seek_left",))
        self.assertEqual(actions_for(press("dpad_left"), chrome_visible=True, spherical=True, focus_is_button=False), ("focus_prev",))
        self.assertEqual(actions_for(press("r3"), chrome_visible=False, spherical=True, focus_is_button=False), ("reset_view",))
        self.assertEqual(actions_for(press("x"), chrome_visible=False, spherical=False, focus_is_button=False), ("toggle_stats",))
        self.assertEqual(actions_for(press("guide"), chrome_visible=True, spherical=False, focus_is_button=True), ())
        self.assertEqual(actions_for(PadEvent("a", 0, False, False), chrome_visible=False, spherical=False, focus_is_button=False), ())


if __name__ == "__main__":
    unittest.main()
