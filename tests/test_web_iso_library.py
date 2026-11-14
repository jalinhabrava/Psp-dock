"""The web catalogue may inspect metadata, never image contents or remote assets."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from psp_dock.media import web_iso_library as iso


class FakeEntry:
    def __init__(self, name, info):
        self.name, self.info, self.calls = name, info, []

    def stat(self, *, follow_symlinks):
        self.calls.append(follow_symlinks)
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


@contextmanager
def listing(entries):
    yield iter(entries)


@contextmanager
def fake_tree(root, entries):
    """Supply synthetic lstat metadata only for freshly enumerated children."""
    pending = {}
    original_lstat = Path.lstat

    def items():
        for item in entries:
            pending[root / item.name] = item
            yield item

    def lstat(path):
        item = pending.pop(path, None)
        return item.stat(follow_symlinks=False) if item else original_lstat(path)

    with patch.object(iso.os, 'scandir', return_value=listing(items())) as scan, \
         patch.object(Path, 'lstat', lstat):
        yield scan


def entry_info(*, directory=False, reparse=False, size=123, inode=10):
    return SimpleNamespace(
        st_mode=(stat.S_IFDIR if directory else stat.S_IFREG) | 0o700,
        st_file_attributes=0x400 if reparse else 0, st_size=size,
        st_mtime=123.25, st_mtime_ns=123250000000, st_dev=7, st_ino=inode,
    )


class WebIsoLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Patch only OS boundaries; pathlib keeps the test host's real flavour.
        windows = patch.object(iso, '_require_windows')
        self.addCleanup(windows.stop)
        windows.start()
        drive = patch.object(iso, '_drive_type', return_value=3)
        self.addCleanup(drive.stop)
        self.drive = drive.start()

    def write(self, name, value=b'SYNTHETIC IMAGE'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_existing_selected_folder_is_returned_without_creation(self):
        selected = self.root / 'Owned images'
        selected.mkdir()
        self.assertEqual(iso.validate_web_iso_folder(selected), selected.resolve())
        self.assertEqual(list(selected.iterdir()), [])

    def test_roots_traversal_unc_devices_ads_and_reserved_names_are_rejected(self):
        bad = ('', '.', '..', 'relative/images', 'C:games', 'C:\\',
               '\\games', '\\\\server\\share\\games', '//server/share/games',
               '/\\server\\share\\games', '\\/server/share/games',
               '\\\\?\\C:\\games', '\\\\.\\C:\\games', '\\??\\C:\\games',
               str(self.root) + '/inner/../images', str(self.root) + '/./images',
               str(self.root) + '/images:stream', str(self.root) + '/CON.iso',
               str(self.root) + '/folder.', str(self.root) + '/folder ',
               str(self.root) + '/wild*card', str(self.root) + '/nul\x00',
               str(self.root) + '/line\nbreak', self.root.anchor)
        with patch.object(iso, '_directory_chain') as check:
            for value in bad:
                with self.subTest(path=value), self.assertRaises(iso.WebIsoLibraryError):
                    iso.validate_web_iso_folder(value)
            check.assert_not_called()
            self.drive.assert_not_called()

    def test_missing_and_non_directory_selection_fail(self):
        regular = self.write('regular.iso')
        for value in (regular, self.root / 'missing'):
            with self.subTest(path=value), self.assertRaises(iso.WebIsoLibraryError):
                iso.validate_web_iso_folder(value)

    def test_psp_removable_network_unknown_and_other_drive_types_fail(self):
        for value in (0, 1, 2, 4, 5, 6):
            self.drive.return_value = value
            with self.subTest(drive=value), self.assertRaises(iso.WebIsoLibraryError):
                iso.validate_web_iso_folder(self.root)

    def test_explicit_psp_root_is_excluded_even_with_a_fixed_drive(self):
        child = self.root / 'ISO'
        child.mkdir()
        with self.assertRaises(iso.WebIsoLibraryError):
            iso.validate_web_iso_folder(child, excluded_roots=[self.root])

    def test_reparse_ancestor_is_rejected_before_resolve(self):
        parent = self.root / 'junction'
        selected = parent / 'images'
        selected.mkdir(parents=True)
        real_lstat = Path.lstat

        def lstat(path):
            if path == parent:
                return entry_info(directory=True, reparse=True)
            return real_lstat(path)

        with patch.object(Path, 'lstat', lstat), patch.object(Path, 'resolve') as resolve:
            with self.assertRaisesRegex(iso.WebIsoLibraryError, 'enlaces'):
                iso.validate_web_iso_folder(selected)
            resolve.assert_not_called()

    def test_real_symlink_selection_is_rejected_even_for_inside_target(self):
        actual = self.root / 'actual'
        actual.mkdir()
        linked = self.root / 'linked'
        try:
            linked.symlink_to(actual, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit creation of test symlinks')
        with self.assertRaises(iso.WebIsoLibraryError):
            iso.validate_web_iso_folder(linked)

    def test_scan_is_stat_only_and_accepts_only_supported_extensions(self):
        files = [self.write('nested/Game' + suffix)
                 for suffix in ('.iso', '.CSO', '.bin', '.cue', '.CHD')]
        for name in ('image.iso.zip', 'image.7z', 'game.iso.exe', 'README.txt'):
            self.write(name)
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}
        with patch('builtins.open', side_effect=AssertionError('content read')), \
             patch.object(Path, 'open', side_effect=AssertionError('content read')):
            report = iso.scan_web_iso_folder(self.root)
        self.assertEqual(report['count'], 5)
        self.assertTrue(report['complete'])
        self.assertFalse(report['truncated'])
        self.assertEqual(report['skipped']['unsupported'], 4)
        self.assertEqual(report['scanned_entries'], 10)
        self.assertTrue(report['metadata_only'])
        self.assertEqual(before, {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files})
        serialized = json.dumps(report)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn('nested', serialized)
        for row in report['entries']:
            self.assertEqual(set(row), {'id', 'name', 'size', 'mtime', 'extension'})
            self.assertRegex(row['id'], r'^iso_[a-f0-9]{32}$')

    def test_terabyte_image_uses_one_non_following_stat_and_no_content_hash(self):
        image = FakeEntry('Huge.iso', entry_info(size=1024**4))
        with fake_tree(self.root, [image]), \
             patch('builtins.open', side_effect=AssertionError('content read')), \
             patch.object(Path, 'open', side_effect=AssertionError('content read')):
            report = iso.scan_web_iso_folder(self.root)
        self.assertEqual(report['entries'][0]['size'], 1024**4)
        self.assertEqual(image.calls, [False])

    def test_reparse_files_and_directories_are_skipped_without_descending(self):
        entries = [FakeEntry('outside', entry_info(directory=True, reparse=True)),
                   FakeEntry('linked.iso', entry_info(reparse=True)),
                   FakeEntry('ordinary.iso', entry_info())]
        with fake_tree(self.root, entries) as scan:
            report = iso.scan_web_iso_folder(self.root)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(report['skipped']['links'], 2)
        self.assertEqual([x['name'] for x in report['entries']], ['ordinary.iso'])
        self.assertTrue(all(item.calls == [False] for item in entries))

    def test_limit_counts_unsupported_entries_without_unbounded_listing(self):
        generated = []

        def items():
            for index in range(1000000):
                item = FakeEntry(str(index) + '.txt', entry_info())
                generated.append(item)
                yield item

        with fake_tree(self.root, items()):
            report = iso.scan_web_iso_folder(self.root, max_entries=3)
        self.assertEqual(report['scanned_entries'], 3)
        self.assertLessEqual(len(generated), 4)
        self.assertEqual(sum(len(item.calls) for item in generated), 3)
        self.assertEqual(report['count'], 0)
        self.assertEqual(report['limit_reasons'], ['max_entries'])
        self.assertFalse(report['complete'])

    def test_depth_and_time_budgets_mark_partial_results(self):
        self.write('root.iso')
        self.write('one/child.iso')
        self.write('one/two/deep.iso')
        report = iso.scan_web_iso_folder(self.root, max_depth=1)
        self.assertEqual({row['name'] for row in report['entries']}, {'root.iso', 'child.iso'})
        self.assertIn('max_depth', report['limit_reasons'])
        self.assertTrue(report['truncated'])
        entries = [FakeEntry('first.iso', entry_info()), FakeEntry('second.iso', entry_info())]
        with fake_tree(self.root, entries), \
             patch.object(iso.time, 'monotonic', side_effect=[0, 0, 0.25, 1.25]):
            report = iso.scan_web_iso_folder(self.root, max_seconds=1)
        self.assertEqual(report['count'], 1)
        self.assertIn('max_seconds', report['limit_reasons'])

    def test_invalid_or_overlarge_limits_fail_before_filesystem_access(self):
        cases = [{'max_entries': 0}, {'max_entries': True}, {'max_entries': 50001},
                 {'max_depth': -1}, {'max_depth': 33}, {'max_depth': 1.5},
                 {'max_seconds': 0}, {'max_seconds': float('nan')},
                 {'max_seconds': float('inf')}, {'max_seconds': 31}]
        with patch.object(iso, 'validate_web_iso_folder') as validate:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(iso.WebIsoLibraryError):
                    iso.scan_web_iso_folder(self.root, **kwargs)
            validate.assert_not_called()

    def test_unreadable_file_is_reported_as_incomplete_without_leaking_paths(self):
        entries = [FakeEntry('unreadable.iso', PermissionError('private path')),
                   FakeEntry('readable.iso', entry_info())]
        with fake_tree(self.root, entries):
            report = iso.scan_web_iso_folder(self.root)
        self.assertEqual(report['count'], 1)
        self.assertEqual(report['skipped']['unreadable'], 1)
        self.assertFalse(report['complete'])
        self.assertNotIn('private path', json.dumps(report))

    def test_changed_directory_identity_is_not_followed(self):
        child = self.root / 'changed'
        child.mkdir()
        real = child.lstat()
        changed = entry_info(directory=True, inode=real.st_ino + 1)
        changed.st_dev = real.st_dev
        with fake_tree(self.root, [FakeEntry('changed', changed)]) as scan:
            with self.assertRaisesRegex(iso.WebIsoLibraryError, 'cambió'):
                iso.scan_web_iso_folder(self.root)
        self.assertEqual(scan.call_count, 1)

    def test_ids_are_stable_per_path_and_stat_but_distinguish_identical_names(self):
        original = self.write('a/Game.iso')
        self.write('b/Game.iso')
        first = iso.scan_web_iso_folder(self.root)['entries']
        second = iso.scan_web_iso_folder(self.root)['entries']
        self.assertEqual(first, second)
        self.assertEqual(len({entry['id'] for entry in first}), 2)
        original.write_bytes(b'A DIFFERENT SIZE')
        third = iso.scan_web_iso_folder(self.root)['entries']
        self.assertEqual(len({e['id'] for e in first} & {e['id'] for e in third}), 1)

    def test_resolution_requires_exact_confirmation_and_rejects_paths_as_ids(self):
        path = self.write('Game.iso')
        row = iso.scan_web_iso_folder(self.root)['entries'][0]
        with patch.object(iso, '_scan') as scan:
            for confirmation in (False, 1, 'true', None):
                with self.subTest(value=confirmation), self.assertRaises(iso.WebIsoLibraryError):
                    iso.resolve_web_iso_entry(self.root, row['id'], confirmed=confirmation)
            for entry_id in ('../Game.iso', str(path), 'iso_' + 'g' * 32, ''):
                with self.subTest(id=entry_id), self.assertRaises(iso.WebIsoLibraryError):
                    iso.resolve_web_iso_entry(self.root, entry_id, confirmed=True)
            scan.assert_not_called()
        self.assertEqual(iso.resolve_web_iso_entry(self.root, row['id'], confirmed=True),
                         path.resolve())
        path.write_bytes(b'CHANGED IMAGE METADATA')
        with self.assertRaises(iso.WebIsoLibraryError):
            iso.resolve_web_iso_entry(self.root, row['id'], confirmed=True)

    def test_resolver_rejects_a_file_becoming_a_reparse_point_after_scan(self):
        path = self.write('Game.iso')
        row = iso.scan_web_iso_folder(self.root)['entries'][0]
        real_lstat = Path.lstat
        file_checks = []

        def lstat(candidate):
            if candidate == path:
                file_checks.append(candidate)
                if len(file_checks) > 1:
                    return entry_info(reparse=True)
            return real_lstat(candidate)

        with patch.object(Path, 'lstat', lstat):
            with self.assertRaises(iso.WebIsoLibraryError):
                iso.resolve_web_iso_entry(self.root, row['id'], confirmed=True)

    def test_picker_returns_actual_unicode_folder_without_copy_or_new_child(self):
        selected = self.root / "Mis imágenes [propias] $variable 'literal'"
        selected.mkdir()
        run = Mock(return_value=SimpleNamespace(
            returncode=0, stdout='PSPDOCK-ISO-FOLDER:' + str(selected) + '\n'))
        self.assertEqual(iso.choose_web_iso_folder(run=run), str(selected.resolve()))
        self.assertEqual(list(selected.iterdir()), [])
        args, kwargs = run.call_args
        self.assertIn('-STA', args[0])
        self.assertIn('FolderBrowserDialog', args[0][-1])
        self.assertIn('ShowNewFolderButton=$false', args[0][-1])
        self.assertNotIn(str(selected), args[0][-1])
        self.assertEqual(kwargs['encoding'], 'utf-8')
        self.assertFalse(kwargs['shell'])

    def test_picker_cancellation_invalid_output_and_failures(self):
        def result(stdout='', code=0):
            return lambda *args, **kwargs: SimpleNamespace(returncode=code, stdout=stdout)

        self.assertIsNone(iso.choose_web_iso_folder(run=result()))
        for run in (result(code=1), result('unexpected'),
                    result('PSPDOCK-ISO-FOLDER:\n'),
                    result('PSPDOCK-ISO-FOLDER:one\nPSPDOCK-ISO-FOLDER:two\n'),
                    Mock(side_effect=subprocess.TimeoutExpired('powershell', 150)),
                    Mock(side_effect=OSError('private failure details'))):
            with self.subTest(run=run), self.assertRaises(iso.WebIsoLibraryError):
                iso.choose_web_iso_folder(run=run)


class WebIsoMatchingTests(unittest.TestCase):
    @staticmethod
    def row(name, index=1):
        return {'id': 'iso_' + f'{index:032x}', 'name': name, 'size': 42,
                'mtime': 123.25, 'extension': Path(name).suffix.lower()}

    def test_exact_title_regions_languages_and_alias_are_suggestions_only(self):
        cases = [
            ({'title': 'Dragon Ball Z: Budokai Tenkaichi 3'},
             'Dragon Ball Z - Budokai Tenkaichi 3 (Europe) (En,Ja).iso'),
            ({'title': 'Grand Theft Auto III', 'iso_aliases': ['Grand Theft Auto 3']},
             'Grand Theft Auto 3 (USA).chd'),
            ({'title': 'Need for Speed Underground 2', 'iso_aliases': ['NFSU2']},
             'NFSU2.iso'),
            ({'title': 'Other display title', 'aliases': ['Wipeout']}, 'Wipeout.bin'),
        ]
        for game, name in cases:
            with self.subTest(name=name):
                result = iso.match_web_iso_candidates(game, [self.row(name)])
                self.assertEqual(result['status'], 'suggested')
                self.assertGreaterEqual(result['candidates'][0]['confidence'], 0.95)
                self.assertTrue(result['requires_confirmation'])
                self.assertFalse(result['confirmed'])
                self.assertFalse(result['candidates'][0]['confirmed'])

    def test_sequels_and_unrecognized_subtitles_are_not_collapsed(self):
        for title, filename in (
            ('Doom', 'Doom II.iso'), ('Doom II', 'Doom III.iso'),
            ('Need for Speed Underground', 'Need for Speed Underground 2.iso'),
            ('Need for Speed Underground 2', 'Need for Speed Underground.iso'),
            ('Resident Evil', 'Resident Evil (Code Veronica).iso'),
            ('Game', 'Other Game.iso'), ('Game 2', 'Game 20.iso'),
            ('Mega Man X', 'Mega Man 10.iso'), ('Mega Man 10', 'Mega Man X.iso'),
            ('Grand Theft Auto IV', 'Grand Theft Auto V.iso'),
        ):
            with self.subTest(title=title, filename=filename):
                result = iso.match_web_iso_candidates({'title': title}, [self.row(filename)])
                self.assertEqual(result['candidates'], [])
                self.assertIsNone(result['suggested_id'])

    def test_multiple_regions_formats_and_discs_stay_ambiguous_even_with_limit_one(self):
        rows = [self.row('Game (USA).iso', 1), self.row('Game (Europe).chd', 2),
                self.row('Game (Disc 1).bin', 3), self.row('Game (Disc 2).cue', 4)]
        result = iso.match_web_iso_candidates({'title': 'Game'}, rows, max_candidates=1)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['candidate_count'], 4)
        self.assertTrue(result['has_more'])
        self.assertEqual(len(result['candidates']), 1)
        self.assertIsNone(result['suggested_id'])

    def test_unknown_parentheses_are_preserved_and_paths_cannot_become_candidates(self):
        rows = [self.row('../Game.iso', 1), self.row('C:\\private\\Game.iso', 2),
                self.row('Game (Unrelated subtitle).iso', 3), self.row('Game.zip', 4)]
        self.assertEqual(iso.match_web_iso_candidates({'title': 'Game'}, rows)['candidates'], [])

    def test_match_report_does_not_echo_extra_paths_or_urls(self):
        row = {**self.row('Game.iso'), 'path': 'C:\\private\\Game.iso',
               'url': 'file:///C:/private/Game.iso', 'relative_path': 'secret/Game.iso'}
        report = iso.match_web_iso_candidates({'title': 'Game'}, [row])
        text = json.dumps(report)
        self.assertNotIn('private', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('file:', text)

    def test_incomplete_scan_does_not_suggest_a_unique_id(self):
        report = {'entries': [self.row('Game.iso')], 'complete': False}
        result = iso.match_web_iso_candidates({'title': 'Game'}, report)
        self.assertEqual(result['candidate_count'], 1)
        self.assertIsNone(result['suggested_id'])
        self.assertFalse(result['scan_complete'])


class WebIsoPlatformTests(unittest.TestCase):
    def test_non_windows_fails_before_path_or_picker_access(self):
        with patch.object(iso.os, 'name', 'posix'), patch.object(iso, '_drive_type') as drive:
            with self.assertRaisesRegex(iso.WebIsoLibraryError, 'Windows'):
                iso.validate_web_iso_folder('C:\\Images')
            with self.assertRaisesRegex(iso.WebIsoLibraryError, 'Windows'):
                iso.choose_web_iso_folder(run=Mock(side_effect=AssertionError('picker')))
            drive.assert_not_called()

    def test_windows_drive_type_uses_unicode_win32_signature(self):
        function = Mock(return_value=3)
        kernel = SimpleNamespace(GetDriveTypeW=function)
        with patch.object(iso.ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertEqual(iso._drive_type('C:\\'), 3)
        function.assert_called_once_with('C:\\')
        self.assertEqual(function.argtypes, [iso.ctypes.c_wchar_p])
        self.assertEqual(function.restype, iso.ctypes.c_uint)

    @unittest.skipUnless(os.name == 'nt', 'Real Windows validation smoke test')
    def test_real_windows_temp_folder_is_fixed_and_accepted(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(iso.validate_web_iso_folder(root), Path(root).resolve())


if __name__ == '__main__':
    unittest.main()
