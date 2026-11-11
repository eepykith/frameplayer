import unittest

from frameplayer.hwdec import device_from_identity, select_hwdec_plan
from frameplayer.resolve import ResolveError, classify_url, drm_service, order_items, parse_m3u8, without_software_av1

MASTER = """
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=12000000,RESOLUTION=1920x1080,CODECS="avc1.4d401f,mp4a.40.2",FRAME-RATE=30
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=80000000,RESOLUTION=7680x4320,CODECS="av01.0.13M.08,mp4a.40.2",FRAME-RATE=30
av1.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=50000000,RESOLUTION=7680x4320,CODECS="hev1.1.6.L183.90,mp4a.40.2",FRAME-RATE=30
hevc.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=90000000,RESOLUTION=15360x4320,CODECS="hev1.1.6.L186.90,mp4a.40.2",FRAME-RATE=30
too_wide.m3u8
"""


def iris_plan():
    device = device_from_identity("/dev/video-dec0", "qcom-iris-decoder", frozenset({"h264", "hevc", "vp9"}), True)
    return select_hwdec_plan([device], compiled=frozenset({"v4l2m2m-copy", "vaapi-copy", "auto-copy"}))


class ResolveTests(unittest.TestCase):
    def test_drm_sites_are_refused(self):
        self.assertEqual(drm_service("https://www.netflix.com/watch/1"), "Netflix")
        self.assertEqual(drm_service("https://www.disneyplus.com/play/abc"), "Disney+")
        self.assertIsNone(drm_service("https://www.youtube.com/watch?v=abc"))

    def test_classify(self):
        self.assertEqual(classify_url("/tmp/video.mp4"), "file")
        self.assertEqual(classify_url("https://cdn.example/live/master.m3u8"), "direct")
        self.assertEqual(classify_url("https://www.youtube.com/watch?v=abc"), "extractor")

    def test_8k_hls_prefers_hardware_hevc(self):
        plan = iris_plan()
        items = parse_m3u8(MASTER, "https://cdn.example/master.m3u8", title="8K", plan=plan, headers={})
        ordered = order_items(items, ceiling=4320, prefer_hw=True)
        self.assertEqual(ordered[0].height, 4320)
        self.assertIn("hev1", ordered[0].vcodec)
        self.assertTrue(ordered[0].hw)
        av1 = next(item for item in ordered if item.vcodec.startswith("av01"))
        self.assertFalse(av1.hw)
        wide = next(item for item in items if item.width == 15360)
        self.assertTrue(wide.over_iris)
        self.assertFalse(wide.hw)
        capped = order_items(items, ceiling=1080, prefer_hw=True)
        self.assertEqual(capped[0].height, 1080)

    def test_av1_is_dropped_until_hardware_exists(self):
        plan = iris_plan()
        items = parse_m3u8(MASTER, "https://cdn.example/master.m3u8", title="8K", plan=plan, headers={})
        kept = without_software_av1(items, plan)
        self.assertTrue(kept)
        self.assertFalse(any(item.vcodec.startswith("av01") for item in kept))
        only_av1 = [item for item in items if item.vcodec.startswith("av01")]
        with self.assertRaises(ResolveError) as caught:
            without_software_av1(only_av1, plan)
        self.assertIn("will not decode it on the CPU", str(caught.exception))

    def test_media_playlist_is_one_item(self):
        text = "#EXTM3U\n#EXTINF:4,\nseg.ts\n"
        items = parse_m3u8(text, "https://cdn.example/a.m3u8", title="live", plan=iris_plan(), headers={})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].video_url, "https://cdn.example/a.m3u8")


if __name__ == "__main__":
    unittest.main()
