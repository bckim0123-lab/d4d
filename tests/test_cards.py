import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4d_web import cards


class CardFontTests(unittest.TestCase):
    def test_available_windows_korean_font_is_used(self):
        font = object()
        with mock.patch.object(cards, "CARD_FONT_PATHS", ("C:/Windows/Fonts/malgun.ttf", "noto.ttc")):
            with mock.patch("PIL.ImageFont.truetype", return_value=font) as load:
                self.assertIs(cards._card_font(20), font)
                load.assert_called_once_with("C:/Windows/Fonts/malgun.ttf", 20)

    def test_missing_windows_font_falls_back_to_linux_cjk(self):
        font = object()
        noto = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
        with mock.patch.object(cards, "CARD_FONT_PATHS", ("C:/Windows/Fonts/malgun.ttf", noto)):
            with mock.patch("PIL.ImageFont.truetype", side_effect=[OSError("missing"), font]) as load:
                self.assertIs(cards._card_font(24), font)
                self.assertEqual(load.call_args_list[-1], mock.call(noto, 24))

    def test_missing_korean_fonts_report_an_actionable_error(self):
        with mock.patch.object(cards, "CARD_FONT_PATHS", ("missing.ttf",)):
            with mock.patch("PIL.ImageFont.truetype", side_effect=OSError("missing")):
                with self.assertRaisesRegex(RuntimeError, "한글 글꼴"):
                    cards._card_font(20)


if __name__ == "__main__":
    unittest.main()
