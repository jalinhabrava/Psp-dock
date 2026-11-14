"""Catalogue import/merge/refresh tests are offline and use only synthetic HTML."""
from copy import deepcopy
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.request import Request

from psp_dock.media import web_port_catalog as catalog
from scripts.catalog.import_ported2browser import main as import_main

ROOT = Path(__file__).resolve().parents[1]


def card(identifier="sample", title="A game", extra="", href=None):
    link = f"/games/{identifier}/" if href is None else href
    return (f'<article data-game="{identifier}" class="featured game-card" {extra}>'
            f'<h3><a href="{link}">{title}</a></h3></article>')


def html_many(count=500):
    return "".join(card(f"game-{number:04}", f"Game {number}") for number in range(count)).encode()


def small_parse(html):
    return catalog.parse_ported2browser(html, min_entries=1)


class ParseCatalogTests(unittest.TestCase):
    def test_metadata_entities_nested_title_and_explicit_platform(self):
        raw = card("test-game", "Caf&eacute; &amp; <strong>Co.</strong>",
                   'data-platform="PS2" data-genre="action" '
                   'data-input="keyboard-pointer-touch" data-files="required" '
                   'data-available="false" data-url="https://untrusted.net/game"')
        game = small_parse(raw)["games"][0]
        self.assertEqual(game["title"], "Caf\u00e9 & Co.")
        self.assertEqual(game["platform"], "PS2")
        self.assertEqual(game["genre"], "action")
        self.assertEqual(game["controls"], ["keyboard", "mouse", "touch"])
        self.assertIs(game["requires_files"], True)
        self.assertIs(game["available"], False)
        self.assertEqual(game["source_page"], catalog.SOURCE_URL + "test-game/")
        self.assertEqual(game["launch_state"], "catalog_only")
        self.assertNotIn("url", game)

    def test_unknown_requirements_and_controls_stay_unknown(self):
        game = small_parse(card())["games"][0]
        self.assertIsNone(game["requires_files"])
        self.assertIsNone(game["available"])
        self.assertEqual(game["input"], "undocumented")
        self.assertEqual(game["controls"], [])
        self.assertNotIn("platform", game)
        for value, expected in [("included", False), ("not-requested", False),
                                ("required", True), ("unexpected", None)]:
            with self.subTest(value=value):
                row = small_parse(card(extra=f'data-files="{value}"'))["games"][0]
                self.assertIs(row["requires_files"], expected)

    def test_no_source_or_runtime_url_is_invented(self):
        for href in ("https://elsewhere.net/games/sample/", "/games/another/",
                     "javascript:alert(1)", "/games/", "https://[invalid"):
            with self.subTest(href=href):
                row = small_parse(card(href=href))["games"][0]
                self.assertNotIn("url", row)
                self.assertNotIn("source_page", row)
                self.assertEqual(row["launch_state"], "catalog_only")

    def test_save_name_fallback_and_whitespace(self):
        raw = ('<article class="game-card" data-game="fallback">'
               '<button data-save-name="  An   explicit   title "></button></article>')
        self.assertEqual(small_parse(raw)["games"][0]["title"], "An explicit title")

    def test_hidden_and_unrelated_markup_are_not_games(self):
        raw = ('<script>' + card("script") + '</script><template>' + card("template")
               + '<template>' + card("nested") + '</template></template>'
               + '<div data-game="outside"><h3>Not a card</h3></div>' + card("real"))
        self.assertEqual([g["id"] for g in small_parse(raw)["games"]], ["real"])

    def test_bad_or_unclosed_cards_do_not_contaminate_the_next(self):
        raw = ('<article class="game-card" data-game="unclosed"><h3>Wrong'
               + card("valid", "Right") + card("../escape")
               + card("x" * 161) + card("", "Missing ID")
               + '<article class="game-card" data-game="no-title"></article>')
        games = small_parse(raw)["games"]
        self.assertEqual([(row["id"], row["title"]) for row in games], [("valid", "Right")])

    def test_unique_ids_and_order_make_serialization_deterministic(self):
        left = small_parse(card("b", "Bee") + card("a", "Aye") + card("b", "Ignored"))
        right = small_parse(card("a", "Aye") + card("b", "Bee"))
        self.assertEqual(left["catalog_total"], 2)
        self.assertEqual(catalog.serialize_catalog(left), catalog.serialize_catalog(right))
        self.assertEqual(left["games"][1]["title"], "Bee")

    def test_text_and_document_limits(self):
        row = small_parse(card(title="a" * 3000, extra='data-genre="' + "b" * 1000 + '"'))["games"][0]
        self.assertEqual(len(row["title"]), 256)
        self.assertEqual(len(row["genre"]), 96)
        for raw in (b"\xff", b"x" * (catalog.MAX_HTML_BYTES + 1), None):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(catalog.CatalogError):
                small_parse(raw)
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_ported2browser(card() * 3, min_entries=1, max_cards=2)

    def test_default_sanity_threshold_counts_unique_valid_cards(self):
        with self.assertRaisesRegex(catalog.CatalogError, "expected at least 500"):
            catalog.parse_ported2browser(card() * 500)
        snapshot = catalog.parse_ported2browser(html_many())
        self.assertEqual(snapshot["catalog_total"], 500)
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_ported2browser(b"<html>Login required</html>")

    def test_source_must_be_the_known_https_index(self):
        for source in ("http://ported2browser.sbs/games/", "https://127.0.0.1/games/",
                       "https://ported2browser.sbs.attacker.net/games/",
                       "https://ported2browser.sbs/games/?redirect=https://other.net/"):
            with self.subTest(source=source), self.assertRaises(catalog.CatalogError):
                catalog.parse_ported2browser(card(), source_url=source, min_entries=1)


class MergeCatalogTests(unittest.TestCase):
    def test_alias_preserves_curated_fields_and_nested_objects_are_independent(self):
        discovered = small_parse(card("diablo") + card("nfs-underground-2"))
        curated = {"games": [
            {"id": "diabloweb", "title": "Diablo curated", "url": "https://d07riv.github.io/diabloweb/",
             "source_page": "https://ported2browser.org/games/diablo/",
             "launch_state": "needs_pointer", "controls": ["mouse"],
             "keyboard_map": {"a": "Enter"}, "notes": "Keep me", "embed": "iframe"},
            {"id": "nfs-underground-2", "title": "NFS", "url": "https://nfsu2.xyz/",
             "controls": ["gamepad"], "platform": "PC"},
        ]}
        before = deepcopy((curated, discovered))
        merged = catalog.build_merged_catalog(curated, discovered)
        self.assertEqual(merged["catalog_total"], 2)
        self.assertEqual([row["id"] for row in merged["games"]], ["diabloweb", "nfs-underground-2"])
        for expected, actual in zip(curated["games"], merged["games"]):
            for key, value in expected.items():
                self.assertEqual(actual[key], value)
        self.assertNotIn("launch_state", merged["games"][1])
        self.assertEqual(merged["games"][0]["source_id"], "diablo")
        merged["games"][0]["keyboard_map"]["a"] = "Changed"
        merged["games"][1]["controls"].append("mouse")
        self.assertEqual((curated, discovered), before)

    def test_iso_requirement_is_not_lost_to_unknown_discovery_metadata(self):
        curated = [{"id": "budokai-tenkaichi-3", "title": "DBZ",
                    "source_page": "https://ported2browser.org/games/dragon-ball-z-bt-3/",
                    "url": "https://dragonballbt3.online/runtime.html",
                    "launch_state": "requires_original_iso"}]
        merged = catalog.build_merged_catalog(curated, small_parse(card("dragon-ball-z-bt-3")))
        self.assertEqual(merged["catalog_total"], 1)
        self.assertIs(merged["games"][0]["requires_files"], True)
        self.assertEqual(merged["games"][0]["launch_state"], "requires_original_iso")

    def test_discovery_cannot_inject_a_runtime_or_authorize_a_launch(self):
        snapshot = small_parse(card())
        snapshot["games"][0].update(url="https://unsafe.net/", launch_state="ready",
                                    keyboard_map={"a": "x"}, embed="native")
        snapshot["games"].extend([dict(snapshot["games"][0]), {}, "bad", None])
        merged = catalog.build_merged_catalog({"games": []}, snapshot)
        self.assertEqual(merged["catalog_total"], 1)
        self.assertEqual(merged["games"][0]["launch_state"], "catalog_only")
        for key in ("url", "embed", "keyboard_map"):
            self.assertNotIn(key, merged["games"][0])

    def test_curated_duplicates_are_removed_and_new_games_are_sorted(self):
        curated = [{"id": "a", "title": "First"}, {"id": "a", "title": "Second"}]
        merged = catalog.build_merged_catalog(curated, small_parse(card("c") + card("b") + card("a")))
        self.assertEqual([row["id"] for row in merged["games"]], ["a", "b", "c"])
        self.assertEqual(merged["games"][0]["title"], "First")

    def test_missing_or_broken_optional_snapshot_falls_back_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            curated_path = root / "ports.json"
            curated_path.write_text(json.dumps({"games": [{"id": "kept", "url": "https://game.net/"}]}))
            snapshot_path = root / "snapshot.json"
            with patch.object(catalog, "_open_catalog", side_effect=AssertionError("network")):
                missing = catalog.load_catalog_snapshot(snapshot_path, curated_path)
                self.assertEqual(missing["games"][0]["id"], "kept")
                self.assertIn("catalog_error", missing)
                for raw in (b"{broken", b"\xff", b'{"version":2,"games":[]}',
                            catalog.serialize_catalog(small_parse(card()))):
                    snapshot_path.write_bytes(raw)
                    result = catalog.load_catalog_snapshot(snapshot_path, curated_path)
                    self.assertEqual(result["catalog_total"], 1)
                    self.assertIn("catalog_error", result)


class DetailUrlTests(unittest.TestCase):
    def test_only_public_https_syntax_is_accepted(self):
        self.assertEqual(catalog.validated_external_url("https://PHOBOSLAB.ORG:443/wipegame/"),
                         "https://phoboslab.org/wipegame/")
        self.assertEqual(catalog.validated_external_url("https://8.8.8.8/game"),
                         "https://8.8.8.8/game")
        rejected = (
            "http://games.net/", "javascript:alert(1)", "data:text/html,hello",
            "https://localhost/", "https://localhost./", "https://printer.local/",
            "https://private.internal/", "https://127.0.0.1/", "https://10.0.0.1/",
            "https://172.16.1.1/", "https://192.168.1.1/", "https://169.254.169.254/",
            "https://100.64.0.1/", "https://[::1]/", "https://[fc00::1]/",
            "https://[::ffff:127.0.0.1]/", "https://[fe80::1%25eth0]/",
            "https://2130706433/", "https://0177.0.0.1/", "https://0x7f.0x0.0x0.0x1/",
            "https://user:secret@games.net/", "https://games.net:444/",
            "https://games.net/\\evil", "https://games.net/\npath", "https://games.net/%0Apath",
            " https://games.net/", "https://[invalid/", "https://-bad.net/",
            "https://ported2browser.sbs/games/sample/", "https://www.ported2browser.org/",
            "https://ported2browser.com/", "https://ported2browser.sbs./",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(catalog.validated_external_url(url))

    def test_explicit_labels_remain_unverified_and_decode_entities(self):
        for label, confidence in (
                ("Open original game \u2197", "explicit_original"),
                ("Open the game directly \u2197", "explicit_original"),
                ("Open game host \u2197 (opens a new tab)", "explicit_host")):
            with self.subTest(label=label):
                raw = f'<a class="button" href="https://game.net/play?a=1&amp;b=2">{label}</a>'
                result = catalog.parse_game_detail(raw.encode(), "sample")
                self.assertEqual(result["url"], "https://game.net/play?a=1&b=2")
                self.assertEqual(result["confidence"], confidence)
                self.assertEqual(result["launch_state"], "unverified")
                self.assertEqual(result["source_page"], catalog.SOURCE_URL + "sample/")

    def test_explicit_link_has_priority_over_preview(self):
        html = ('<a class="external-game-preview" href="https://preview.net/">Preview</a>'
                '<a href="https://original.net/"><span>Open original game</span></a>')
        self.assertEqual(catalog.parse_game_detail(html, "sample")["url"], "https://original.net/")
        result = catalog.parse_game_detail(
            '<a class="external-game-preview wide" href="//game.net/">Preview</a>', "sample")
        self.assertEqual(result["confidence"], "external_preview")
        self.assertEqual(result["launch_state"], "unverified")

    def test_detail_ignores_hidden_internal_unsafe_and_unlabelled_links(self):
        html = ('<template><a href="https://hidden.net/">Open original game</a></template>'
                '<a href="/games/another/">Open original game</a>'
                '<a href="https://localhost/">Open original game</a>'
                '<a href="https://[invalid">Open original game</a>'
                '<a href="&#10;https://control.net/">Open original game</a>'
                '<a href="https://unlabelled.net/">Navigation</a>'
                '<iframe src="https://frame.net/"></iframe>')
        self.assertIsNone(catalog.parse_game_detail(html, "sample"))

    def test_detail_limits_and_invalid_slugs(self):
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_game_detail(b"x" * (catalog.MAX_DETAIL_BYTES + 1), "sample")
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_game_detail(b"", "../escape")
        with self.assertRaises(catalog.CatalogError):
            catalog.parse_game_detail('<a href="/">x</a>' * 1025, "sample")


class FakeResponse(BytesIO):
    def __init__(self, data, headers=None, url=catalog.SOURCE_URL):
        super().__init__(data)
        self.headers = headers or {}
        self.url = url
        self.status = 200
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read1(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class RefreshCatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cache = self.root / "catalog.json"
        self.old = b"keep last good snapshot"
        self.cache.write_bytes(self.old)

    def assert_preserved(self):
        self.assertEqual(self.cache.read_bytes(), self.old)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_success_only_fetches_index_and_publishes_validated_json(self):
        response = FakeResponse(html_many(), {"Content-Type": "text/html; charset=utf-8"})
        opener = Mock(return_value=response)
        result = catalog.refresh_catalog(self.cache, opener=opener, timeout=4)
        self.assertEqual(result["catalog_total"], 500)
        self.assertEqual(self.cache.read_bytes(), catalog.serialize_catalog(result))
        opener.assert_called_once()
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, catalog.SOURCE_URL)
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertEqual(opener.call_args.kwargs["timeout"], 4)
        self.assertTrue(all(size <= 64 * 1024 for size in response.read_sizes))
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_size_limits_cover_declared_missing_and_lying_content_length(self):
        for headers in ({"Content-Length": "101"}, {}, {"Content-Length": "1"}):
            with self.subTest(headers=headers):
                response = FakeResponse(b"x" * 101, headers)
                with self.assertRaises(catalog.CatalogError):
                    catalog.refresh_catalog(self.cache, opener=Mock(return_value=response), max_bytes=100)
                self.assert_preserved()

    def test_bad_types_encodings_lengths_status_and_final_url_leave_cache_intact(self):
        cases = [
            FakeResponse(b"", {"Content-Type": "application/octet-stream"}),
            FakeResponse(b"", {"Content-Encoding": "gzip"}),
            FakeResponse(b"", {"Content-Length": "-1"}),
            FakeResponse(b"", {"Content-Length": "not-a-number"}),
            FakeResponse(b"", url="https://localhost/games/"),
        ]
        bad_status = FakeResponse(b"")
        bad_status.status = 403
        cases.append(bad_status)
        for response in cases:
            with self.subTest(headers=response.headers, url=response.url):
                with self.assertRaises(catalog.CatalogError):
                    catalog.refresh_catalog(self.cache, opener=Mock(return_value=response))
                self.assert_preserved()

    def test_timeouts_transport_errors_and_short_index_preserve_cache(self):
        for error in (TimeoutError("timed out"), URLError("offline"), OSError("connection lost")):
            with self.subTest(error=type(error).__name__), self.assertRaises(catalog.CatalogError):
                catalog.refresh_catalog(self.cache, opener=Mock(side_effect=error))
            self.assert_preserved()
        with self.assertRaises(catalog.CatalogError):
            catalog.refresh_catalog(self.cache, opener=Mock(return_value=FakeResponse(card().encode())))
        self.assert_preserved()

    def test_elapsed_deadline_is_checked_between_chunks(self):
        response = FakeResponse(html_many())
        with patch.object(catalog.time, "monotonic", side_effect=[0, 0, 16]):
            with self.assertRaisesRegex(catalog.CatalogError, "timed out"):
                catalog.refresh_catalog(self.cache, opener=Mock(return_value=response), timeout=15)
        self.assert_preserved()

    def test_redirect_targets_are_validated_before_another_request(self):
        handler = catalog._CatalogueRedirect()
        request = Request(catalog.SOURCE_URL)
        for target in ("https://127.0.0.1/games/", "https://other.net/games/",
                       "http://ported2browser.sbs/games/", "https://ported2browser.sbs/private/"):
            with self.subTest(target=target), self.assertRaises(catalog.CatalogError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://www.ported2browser.sbs/games/")
        self.assertEqual(redirected.full_url, "https://www.ported2browser.sbs/games/")
        self.assertLessEqual(handler.max_redirections, 3)

    def test_atomic_publish_failure_preserves_old_bytes_and_cleans_temp_file(self):
        with patch.object(catalog.os, "replace", side_effect=OSError("disk busy")):
            with self.assertRaises(catalog.CatalogError):
                catalog.refresh_catalog(self.cache, opener=Mock(return_value=FakeResponse(html_many())))
        self.assert_preserved()

    def test_invalid_configuration_does_not_open_a_connection(self):
        for options in ({"timeout": 0}, {"timeout": float("nan")}, {"timeout": 61},
                        {"max_bytes": 0}, {"max_bytes": catalog.MAX_HTML_BYTES + 1},
                        {"min_entries": 0}, {"min_entries": "500"}, {"max_bytes": True},
                        {"source_url": "https://127.0.0.1/games/"}):
            opener = Mock()
            with self.subTest(options=options), self.assertRaises(catalog.CatalogError):
                catalog.refresh_catalog(self.cache, opener=opener, **options)
            opener.assert_not_called()
            self.assert_preserved()


class BundledSnapshotTests(unittest.TestCase):
    def test_bundled_snapshot_is_complete_unique_deterministic_discovery(self):
        path = ROOT / "web/catalog-snapshot.json"
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        games = snapshot["games"]
        self.assertGreaterEqual(len(games), 500)
        self.assertEqual(snapshot["catalog_total"], len(games))
        self.assertEqual(len({game["id"] for game in games}), len(games))
        self.assertEqual(raw, catalog.serialize_catalog(snapshot))
        for game in games:
            self.assertNotIn("url", game)
            self.assertEqual(game["launch_state"], "catalog_only")
            self.assertTrue(game["requires_files"] is None or isinstance(game["requires_files"], bool))

    def test_real_curated_entries_survive_the_complete_offline_merge(self):
        curated_path = ROOT / "web/ports.json"
        curated = json.loads(curated_path.read_bytes())
        with patch.object(catalog, "_open_catalog", side_effect=AssertionError("network")):
            merged = catalog.load_catalog_snapshot(ROOT / "web/catalog-snapshot.json", curated_path)
        self.assertNotIn("catalog_error", merged)
        self.assertGreaterEqual(merged["catalog_total"], 500)
        by_id = {row["id"]: row for row in merged["games"]}
        self.assertEqual(len(by_id), merged["catalog_total"])
        for game in curated["games"]:
            for key, value in game.items():
                self.assertEqual(by_id[game["id"]][key], value, (game["id"], key))
        self.assertNotIn("diablo", by_id)
        self.assertNotIn("dragon-ball-z-bt-3", by_id)
        self.assertEqual(by_id["diabloweb"]["source_id"], "diablo")
        self.assertEqual(by_id["budokai-tenkaichi-3"]["source_id"], "dragon-ball-z-bt-3")
        for identifier in ("nfs-underground-2", "2048", "hexgl", "wipeout"):
            self.assertNotIn("launch_state", by_id[identifier])

    def test_cli_offline_reproducibility_check_and_failure_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.html"
            output = root / "catalog.json"
            source.write_text(card(), encoding="utf-8")
            args = ["--html", str(source), "--output", str(output), "--min-entries", "1"]
            with patch("sys.stdout", new_callable=StringIO), patch("sys.stderr", new_callable=StringIO):
                self.assertEqual(import_main(args), 0)
                expected = output.read_bytes()
                self.assertEqual(import_main(args + ["--check"]), 0)
                self.assertEqual(output.read_bytes(), expected)
                output.write_bytes(b"changed")
                self.assertEqual(import_main(args + ["--check"]), 1)
                self.assertEqual(output.read_bytes(), b"changed")
                self.assertEqual(import_main(["--html", str(source), "--output", str(output)]), 1)
                self.assertEqual(output.read_bytes(), b"changed")


if __name__ == "__main__":
    unittest.main()
