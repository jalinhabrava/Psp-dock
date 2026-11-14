"""Browser scanner guards without live browsing or access to private Chrome."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.catalog.audit_ported2browser_browser import (
    _atomic_json, _listing_cards, _source_url, collect,
)

GAME = "01M7B4G8SQ3YWXN97YN95Z7SYP"


class _FakeLocator:
    def __init__(self, items):
        self.items = items

    def evaluate_all(self, expression):
        if "data-port-id" not in expression:
            raise AssertionError("The scanner did not query the original cards.")
        return self.items


class _FakePage:
    def __init__(self, items):
        self.items = items

    def locator(self, selector):
        if selector != "#catalog-results article.card":
            raise AssertionError("Only the active original catalogue is allowed.")
        return _FakeLocator(self.items)


class OfficialBrowserAuditTests(unittest.TestCase):
    def test_listing_only_collects_source_detail_links(self):
        rows = [
            dict(id=GAME, title="Midnight Club 2", author="mcii.online",
                 href="https://ported2browser.com/ports/" + GAME,
                 flags=[], alerts=[], broken=False),
            dict(id=GAME, title="Duplicate", author="X",
                 href="https://ported2browser.com/ports/" + GAME,
                 flags=[], alerts=[], broken=False),
            dict(id="01M6FJS3FP9ER9BWP5FTP5XP7H",
                 title="Fake", author="Y",
                 href="https://ported2browser.sbs/games/socom-ii/",
                 flags=[], alerts=[], broken=False),
            dict(id="..", title="Path traversal", author="Z",
                 href="https://ported2browser.com/ports/..",
                 flags=[], alerts=[], broken=False),
        ]
        found = _listing_cards(_FakePage(rows))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], GAME)
        self.assertEqual(found[0]["status"], "directory_only")
        self.assertIsNone(found[0]["direct_url"])
        self.assertEqual(found[0]["source"], "https://ported2browser.com/")

    def test_directory_navigations_cannot_leave_actual_domain(self):
        allowed = ("https://ported2browser.com/",
                   "https://ported2browser.com?page=2",
                   "https://ported2browser.com/ports/" + GAME)
        for url in allowed:
            self.assertEqual(_source_url(url, detail="/ports/" in url), url)
        for url in ("http://ported2browser.com/",
                    "https://ported2browser.org/",
                    "https://ported2browser.sbs/",
                    "https://127.0.0.1/",
                    "https://user:pass@ported2browser.com/",
                    "https://ported2browser.com/admin",
                    "https://ported2browser.com/ports/" + GAME + "?token=1",
                    "https://ported2browser.com/ports/../settings"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    _source_url(url, detail="/ports/" in url)

    def test_partial_report_is_written_atomically_without_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"audit.json"
            report = {
                "source": "https://ported2browser.com/",
                "listed": 1,
                "games": [{"id": GAME, "status": "directory_only"}],
            }
            _atomic_json(path, report)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), report)
            self.assertFalse(path.with_name(path.name + ".partial").exists())

    def test_only_console_and_pc_platforms_allowed_for_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            for forbidden in ("Web browser", "Browser", "Tabletop", "Mobile",
                              "Java", "Android / iOS", "Software"):
                with self.subTest(forbidden=forbidden):
                    with self.assertRaises(ValueError):
                        collect(profile_dir=Path(folder),
                                output=Path(folder)/"report.json",
                                platform=forbidden)


if __name__ == "__main__":
    unittest.main()
