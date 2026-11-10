import unittest

from frameplayer.hwdec import (
    IRIS_MAX_DIMENSION,
    DecoderDevice,
    codec_family,
    codec_is_hardware,
    describe_active,
    device_from_identity,
    parse_hwdec_help,
    parse_v4l2_formats,
    resolution_tier,
    select_hwdec_plan,
)

IRIS_FORMATS = """
ioctl: VIDIOC_ENUM_FMT
	Type: Video Output Multiplanar

	[0]: 'H264' (H.264, compressed)
		Size: Stepwise 128x128 - 8192x8192 with step 1/1
	[1]: 'HEVC' (HEVC, compressed)
		Size: Stepwise 128x128 - 8192x8192 with step 1/1
	[2]: 'VP90' (VP9, compressed)
		Size: Stepwise 128x128 - 8192x8192 with step 1/1
"""

HELP = """
Valid values (with alternative full names):
  vaapi (h264-vaapi)
  vaapi-copy (h264-vaapi-copy)
  v4l2m2m-copy (h264_v4l2m2m-v4l2m2m-copy)
  auto-copy
"""


class HwdecTests(unittest.TestCase):
    def test_codec_families(self):
        self.assertEqual(codec_family("avc1.4d401f"), "h264")
        self.assertEqual(codec_family("hev1.1.6.L183.90"), "hevc")
        self.assertEqual(codec_family("vp09.00.51.08"), "vp9")
        self.assertEqual(codec_family("av01.0.13M.08"), "av1")

    def test_iris_formats(self):
        codecs = parse_v4l2_formats(IRIS_FORMATS)
        self.assertEqual(codecs, frozenset({"h264", "hevc", "vp9"}))

    def test_steam_frame_plan_prefers_v4l2_copy(self):
        device = device_from_identity("/dev/video-dec0", "qcom-iris-decoder", frozenset({"h264", "hevc", "vp9"}), True)
        plan = select_hwdec_plan([device], have_vaapi=True, compiled=parse_hwdec_help(HELP))
        self.assertEqual(plan.platform, "steam-frame")
        self.assertEqual(plan.chain[0], "v4l2m2m-copy")
        self.assertNotIn("v4l2m2m", plan.chain)
        self.assertIn("vaapi-copy", plan.chain)
        self.assertEqual(plan.hw_codecs, frozenset({"h264", "hevc", "vp9"}))
        self.assertTrue(codec_is_hardware("hev1", 7680, 4320, plan))
        self.assertFalse(codec_is_hardware("av01", 7680, 4320, plan))
        self.assertFalse(codec_is_hardware("hev1", 15360, 4320, plan))
        self.assertGreater(15360, IRIS_MAX_DIMENSION)

    def test_unknown_iris_assumes_valve_codecs(self):
        device = device_from_identity("/dev/video22", "qcom-iris-decoder", frozenset(), False)
        self.assertIsNotNone(device)
        self.assertEqual(device.codecs, frozenset({"h264", "hevc", "vp9"}))
        self.assertFalse(device.formats_known)

    def test_webcam_is_ignored(self):
        self.assertIsNone(device_from_identity("/dev/video0", "Integrated Camera", frozenset(), True))

    def test_desktop_vaapi_plan(self):
        plan = select_hwdec_plan([], have_vaapi=True, compiled=parse_hwdec_help(HELP))
        self.assertEqual(plan.platform, "vaapi")
        self.assertEqual(plan.chain[0], "vaapi-copy")

    def test_no_hardware_uses_auto_copy_when_compiled(self):
        plan = select_hwdec_plan([], compiled=parse_hwdec_help(HELP))
        self.assertEqual(plan.chain, ("auto-copy",))

    def test_active_decoder_reports_fallback(self):
        device = DecoderDevice("/dev/video-dec0", "qcom-iris-decoder", "iris", frozenset({"h264", "hevc", "vp9"}), True)
        plan = select_hwdec_plan([device], compiled=frozenset({"v4l2m2m-copy"}))
        active = describe_active(plan, "v4l2m2m-copy", codec="hevc", width=7680, height=4320)
        self.assertTrue(active.hardware)
        self.assertIn("v4l2m2m-copy", active.summary)
        fallback = describe_active(plan, "no", codec="av1", width=7680, height=4320)
        self.assertTrue(fallback.fallback)
        self.assertIn("AV1", fallback.detail)
        over = describe_active(plan, "no", codec="hevc", width=10000, height=4000)
        self.assertIn("8192", over.detail)
        software = describe_active(select_hwdec_plan([], compiled=frozenset()), "no")
        self.assertFalse(software.fallback)

    def test_resolution_tiers(self):
        self.assertEqual(resolution_tier(7680, 4320), "8K")
        self.assertEqual(resolution_tier(7680, 3840), "8K")
        self.assertEqual(resolution_tier(3840, 2160), "4K")
        self.assertEqual(resolution_tier(1920, 1080), "1080p")


if __name__ == "__main__":
    unittest.main()
