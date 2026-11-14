import unittest

from psp_dock.media.official_ported2browser import parse_official_homepage


ID = "01M7B4G8SQ3YWXN97YN95Z7SYP"
CARD = (
    f'<article class="card" data-port-id="{ID}">'
    f'<a href="https://ported2browser.com/ports/{ID}">'
    '<div><h2>Midnight Club 2</h2><p>mcii.online</p></div></a>'
    '</article>'
)


class OriginalPortIndexTests(unittest.TestCase):
    def test_cards_dedupe_and_are_not_game_urls(self):
        html = ('<html><template data-catalog-preload="recent" data-total="330">'
                + CARD + '</template>'
                '<template data-catalog-preload="popular" data-total="330">'
                + CARD + '</template></html>')
        result = parse_official_homepage(html)
        self.assertEqual(result["source"], "https://ported2browser.com/")
        self.assertEqual(result["reported_total"], 330)
        self.assertEqual(result["extracted"], 1)
        self.assertFalse(result["complete"])
        game = result["games"][0]
        self.assertEqual(game["title"], "Midnight Club 2")
        self.assertEqual(game["author"], "mcii.online")
        self.assertEqual(game["tabs"], ["recent", "popular"])
        self.assertIsNone(game["confirmed_launch_url"])
        self.assertEqual(game["status"], "directory_only")

    def test_disc_and_broken_link_flags(self):
        html = ('<template data-catalog-preload="recent" data-total="330">'
                f'<article class="card is-broken" data-port-id="{ID}">'
                f'<a href="https://ported2browser.com/ports/{ID}">'
                '<div><b class="alert-badge is-disc" '
                'aria-label="Disc image: Requires disc image."></b>'
                '<h2>SOCOM II</h2><p>Example</p></div></a></article>'
                '</template>')
        result = parse_official_homepage(html)
        self.assertCountEqual(result["games"][0]["flags"],
                              ["broken_link", "requires_disc_image"])
        self.assertEqual(len(result["games"][0]["alerts"]), 1)

    def test_untrusted_or_unrelated_exports_fail(self):
        for example in ('<h1>ported2browser.org</h1>', '', '<html/>',
                        '<template data-catalog-preload="recent" data-total="330">'
                        f'<article data-port-id="{ID}"><a href="https://evil.org">'
                        '<h2>Game</h2><p>Host</p></a></article></template>'):
            with self.subTest(example=example[:50]):
                with self.assertRaises(ValueError):
                    parse_official_homepage(example)

    def test_oversized_rejected(self):
        with self.assertRaises(ValueError):
            parse_official_homepage("a" * (4 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
