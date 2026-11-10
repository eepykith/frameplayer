import tempfile
import unittest
from pathlib import Path

from frameplayer.library import Library


class LibraryTests(unittest.TestCase):
    def test_history_and_favorites(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = Library(Path(tmp) / "library.json")
            library.remember("https://example/a", "One")
            library.remember("https://example/b", "Two")
            library.set_favorite("https://example/a", True, "One")
            library.remember("https://example/b", "Two again")
            again = Library(Path(tmp) / "library.json")
            self.assertEqual(again.history()[0].title, "Two again")
            self.assertEqual(again.favorites()[0].url, "https://example/a")
            self.assertTrue(again.is_favorite("https://example/a"))
            again.set_favorite("https://example/a", False)
            self.assertFalse(again.is_favorite("https://example/a"))


if __name__ == "__main__":
    unittest.main()
