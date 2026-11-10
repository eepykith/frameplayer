import math
import unittest

from frameplayer.presentation import (
    apply_layout_uv,
    crop_option,
    crop_rect,
    detect_presentation,
    look_uv,
    resolve_presentation,
)


class PresentationTests(unittest.TestCase):
    def test_keywords(self):
        video = detect_presentation(title="Alps 8K 360 VR", width=7680, height=3840)
        self.assertEqual(video.projection, "equirect-360")
        stereo = detect_presentation(title="Concert VR180 SBS", width=7680, height=3840)
        self.assertEqual(stereo.projection, "equirect-180")
        self.assertEqual(stereo.layout, "sbs-left")
        over = detect_presentation(title="Stage over-under", width=3840, height=3840)
        self.assertEqual(over.layout, "tb-top")

    def test_aspect_does_not_call_a_film_360(self):
        film = detect_presentation(title="Feature film", width=1920, height=1080)
        self.assertEqual(film.projection, "flat")
        self.assertEqual(film.layout, "packed")
        wide = detect_presentation(title="Feature", width=7680, height=2160)
        self.assertEqual(wide.layout, "sbs-left")

    def test_metadata_wins(self):
        found = detect_presentation(title="clip", spherical="equirectangular", stereo="side by side", width=1920, height=1080)
        self.assertEqual(found.projection, "equirect-360")
        self.assertEqual(found.layout, "sbs-left")
        top = detect_presentation(stereo="top and bottom")
        self.assertEqual(top.layout, "tb-top")

    def test_user_override(self):
        detected = detect_presentation(title="360 video")
        chosen = resolve_presentation(detected, "tb-bottom", "flat")
        self.assertEqual(chosen.projection, "flat")
        self.assertEqual(chosen.layout, "tb-bottom")
        auto = resolve_presentation(detected, "auto", "auto")
        self.assertEqual(auto.projection, "equirect-360")

    def test_8k_crop(self):
        self.assertEqual(crop_rect("sbs-left", 7680, 4320), (0, 0, 3840, 4320))
        self.assertEqual(crop_rect("sbs-right", 7680, 4320), (3840, 0, 3840, 4320))
        self.assertEqual(crop_rect("tb-top", 7680, 7680), (0, 0, 7680, 3840))
        self.assertEqual(crop_rect("tb-bottom", 7680, 7680), (0, 3840, 7680, 3840))
        self.assertIsNone(crop_rect("packed", 7680, 4320))
        self.assertEqual(crop_option("sbs-left", 7680, 4320), "3840x4320+0+0")

    def test_equirect_center_and_yaw(self):
        center = look_uv(0, 0, yaw=0, pitch=0, fov_x=math.radians(90), aspect=16 / 9, projection="equirect-360", layout="packed")
        self.assertIsNotNone(center)
        self.assertAlmostEqual(center[0], 0.5, places=3)
        self.assertAlmostEqual(center[1], 0.5, places=3)
        right = look_uv(0, 0, yaw=math.pi / 2, pitch=0, fov_x=math.radians(90), aspect=1, projection="equirect-360", layout="packed")
        self.assertAlmostEqual(right[0], 0.75, places=3)
        left_eye = apply_layout_uv(right[0], right[1], "sbs-left")
        self.assertAlmostEqual(left_eye[0], 0.375, places=3)
        top_eye = apply_layout_uv(0.5, 0.5, "tb-top")
        self.assertAlmostEqual(top_eye[1], 0.25, places=3)

    def test_180_rejects_the_back(self):
        miss = look_uv(0, 0, yaw=math.pi, pitch=0, fov_x=math.radians(40), aspect=1, projection="equirect-180", layout="packed")
        self.assertIsNone(miss)
        hit = look_uv(0, 0, yaw=0, pitch=0, fov_x=math.radians(90), aspect=1, projection="equirect-180", layout="sbs-right")
        self.assertIsNotNone(hit)
        self.assertGreater(hit[0], 0.5)


if __name__ == "__main__":
    unittest.main()
