import unittest

from psp_dock.media.official_ported2browser import (
    parse_official_homepage, parse_official_detail,
)


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

    def test_socom_direct_author_link_and_controls(self):
        socom_id = "01M6FJS3FP9ER9BWP5FTP5XP7H"
        html = f"""
        <html><head><title>SOCOM II · Ported2Browser</title></head>
        <body><form><input type="hidden" name="return" value="/ports/{socom_id}"></form>
        <h1>SOCOM II</h1>
        <div class="port-actions"><div class="play-row"><div class="play-list">
        <a class="button primary play" href="https://socomlegacy.com/"
           target="_blank" data-destination="402" data-id="{socom_id}"
           data-page="/ports/{socom_id}">Play in browser ↗</a></div></div>
        <ul class="controls">
          <li class="ctl is-unsupported"><span class="ctl-label">Keyboard</span></li>
          <li class="ctl is-supported"><span class="ctl-label">Gamepad</span></li>
          <li class="ctl is-unsupported"><span class="ctl-label">Touch</span></li>
        </ul>
        <div class="port-alert is-disc"><p>Requires disc image.</p></div>
        </div></body></html>
        """
        parsed = parse_official_detail(html, socom_id)
        self.assertEqual(parsed["direct_url"], "https://socomlegacy.com/")
        self.assertEqual(parsed["detail_url"],
                         f"https://ported2browser.com/ports/{socom_id}")
        self.assertEqual(parsed["status"], "direct_candidate")
        self.assertTrue(parsed["requires_disc_image"])
        self.assertFalse(parsed["controls"]["keyboard"])
        self.assertTrue(parsed["controls"]["gamepad"])
        self.assertFalse(parsed["controls"]["touch"])
        self.assertFalse(parsed["runtime_verified"])
        self.assertFalse(parsed["gamepad_tested"])
        self.assertIsNone(parsed["original_platform"])

    def test_non_public_links_and_wrong_identity_are_rejected(self):
        game_id = "01M6FJS3FP9ER9BWP5FTP5XP7H"
        template = ('<html><title>SOCOM II · Ported2Browser</title>'
                    f'<form><input name="return" value="/ports/{game_id}"></form>'
                    '<h1>SOCOM II</h1>'
                    f'<a class="button play" data-id="{game_id}" '
                    f'data-page="/ports/{game_id}" href="{{}}">'
                    'Play in browser</a></html>')
        for unsafe in ('http://socomlegacy.com/', 'https://localhost/',
                       'https://127.0.0.1/', 'https://ported2browser.com/',
                       'https://x.local/', 'https://user:pass@game.example/',
                       'https://game.example:8080/', 'file:///C:/disc.iso'):
            with self.subTest(unsafe=unsafe):
                result = parse_official_detail(template.format(unsafe), game_id)
                self.assertIsNone(result["direct_url"])
                self.assertEqual(result["status"], "no_direct_link")
        with self.assertRaises(ValueError):
            parse_official_detail(template.format("https://socomlegacy.com/"),
                                  "01M7B4G8SQ3YWXN97YN95Z7SYP")

    def test_multiple_direct_links_need_manual_selection(self):
        game_id = "01M6FJS3FP9ER9BWP5FTP5XP7H"
        parts = (f'<html><form><input name="return" value="/ports/{game_id}">'
                 '</form><h1>Game</h1>')
        for host in ('author1.example.com', 'author2.example.com'):
            parts += (f'<a class="button play" href="https://{host}/" '
                      f'data-id="{game_id}" data-page="/ports/{game_id}">'
                      'Play in browser</a>')
        result = parse_official_detail(parts + '</html>', game_id)
        self.assertEqual(result["status"], "multiple_candidates")
        self.assertIsNone(result["direct_url"])
        self.assertEqual(len(result["direct_candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
