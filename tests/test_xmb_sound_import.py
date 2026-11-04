"""Real temporary files with synthetic audio; never touch USB or play audio."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import psp_dock.media.xmb_sound_import as importer
from psp_dock.media.xmb_sounds import SOUNDS, UiSoundPlayer


def synthetic_sounds():
    result = {}
    for index, name in enumerate(SOUNDS):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as audio:
            audio.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            audio.writeframes(index.to_bytes(2, 'little') * 128)
        result[name] = stream.getvalue()
    return result


class DeviceSoundImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.psp = self.base / 'synthetic-psp'
        self.source = self.psp / importer.SOURCE_RELATIVE
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'\0' * importer.RCO_SIZE)
        self.cache = self.base / 'pc' / 'xmb-ui-sounds'
        self.device = {'volume_serial': 'synthetic-volume', 'usb_parent': 'synthetic-PSP',
                       'firmware_revision': 0x0308, 'ui_sounds_ready': True,
                       'root': 'must-not-be-stored'}
        self.check = Mock(return_value=self.psp)
        self.decoder = patch.object(importer, 'extract_sounds', return_value=synthetic_sounds())
        self.decode = self.decoder.start()
        self.addCleanup(self.decoder.stop)

    def run_import(self):
        return importer.import_from_psp(self.psp, self.cache, device=self.device,
                                        verify_device=self.check)

    def test_complete_private_pack_and_device_provenance(self):
        source_before = self.source.read_bytes()
        self.assertEqual(self.run_import(), {'status': 'imported', 'ready': True})
        self.assertEqual(self.check.call_count, 3)
        player = UiSoundPlayer(self.cache, play=Mock())
        for name, raw in synthetic_sounds().items():
            self.assertEqual(player.asset(name)[1], raw)
        manifest = json.loads((self.cache / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['source']['type'], 'connected_psp')
        self.assertEqual(manifest['source']['device']['volume_serial'], 'synthetic-volume')
        self.assertNotIn('root', manifest['source']['device'])
        self.assertFalse(manifest['rights']['redistribute'])
        self.assertFalse(manifest['rights']['synthesized'])
        self.assertEqual(manifest['source']['rco_sha256'], hashlib.sha256(source_before).hexdigest())
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(list(self.cache.parent.glob('.xmb-sounds-*.tmp')), [])

    def test_valid_cache_does_not_read_psp_again_or_replace_files(self):
        self.run_import()
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.cache.iterdir()}
        self.source.unlink()
        self.check.reset_mock()
        with patch.object(importer, '_read_export', side_effect=AssertionError('No PSP read')):
            self.assertEqual(self.run_import(), {'status': 'cached', 'ready': True})
        self.check.assert_not_called()
        self.assertEqual(before, {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in self.cache.iterdir()})

    def test_missing_export_is_optional_and_does_not_create_cache(self):
        self.source.unlink()
        self.assertEqual(self.run_import(), {'status': 'missing_export', 'ready': False})
        self.assertFalse(self.cache.exists())
        self.decode.assert_not_called()

    def test_old_export_file_requires_current_psp_ready_attestation(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(attestation=value):
                self.device['ui_sounds_ready'] = value
                with patch.object(importer, '_read_export', side_effect=AssertionError('Stale export')):
                    self.assertEqual(self.run_import(), {'status':'missing_export','ready':False})
        self.check.assert_not_called()
        self.decode.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_unknown_resource_does_not_publish_or_alter_source(self):
        self.decode.side_effect = ValueError('Unknown profile')
        before = self.source.read_bytes()
        self.assertEqual(self.run_import(), {'status': 'unsupported', 'ready': False})
        self.assertFalse(self.cache.exists())
        self.assertEqual(before, self.source.read_bytes())

    def test_oversized_export_is_rejected_before_read(self):
        self.source.write_bytes(b'X' * (importer.RCO_SIZE + 1))
        with patch.object(importer.os, 'open', side_effect=AssertionError('Must not open')):
            self.assertEqual(self.run_import()['status'], 'unavailable')
        self.decode.assert_not_called()

    def test_invalid_decoded_wav_never_replaces_partial_previous_pack(self):
        self.cache.mkdir(parents=True)
        marker = self.cache / 'keep.txt'
        marker.write_bytes(b'preserve previous incomplete pack')
        self.decode.return_value['cancel'] = b'not a WAV'
        self.assertEqual(self.run_import()['status'], 'cache_unavailable')
        self.assertEqual(marker.read_bytes(), b'preserve previous incomplete pack')
        self.assertEqual(list(self.cache.parent.glob('.xmb-sounds-*.tmp')), [])

    def test_incomplete_previous_pack_is_preserved_when_repaired(self):
        self.cache.mkdir(parents=True)
        (self.cache / 'old.txt').write_bytes(b'previous')
        self.assertTrue(self.run_import()['ready'])
        saved = list(self.cache.parent.glob('xmb-ui-sounds.previous-*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0] / 'old.txt').read_bytes(), b'previous')
        self.assertTrue(importer.cache_status(self.cache)['ready'])

    def test_failed_publication_restores_previous_directory(self):
        self.cache.mkdir(parents=True)
        (self.cache / 'old.txt').write_bytes(b'previous')
        rename = os.rename
        def fail_publish(source, target):
            if Path(source).name.startswith('.xmb-sounds-'):
                raise OSError('Synthetic disk failure')
            return rename(source, target)
        with patch.object(importer.os, 'rename', side_effect=fail_publish):
            self.assertEqual(self.run_import()['status'], 'cache_unavailable')
        self.assertEqual((self.cache / 'old.txt').read_bytes(), b'previous')
        self.assertFalse((self.cache / 'manifest.json').exists())

    def test_device_change_during_read_or_before_publication_propagates(self):
        for point in (2, 3):
            with self.subTest(check=point):
                self.check.side_effect = [self.psp] * (point - 1) + [RuntimeError('Device changed')]
                with self.assertRaisesRegex(RuntimeError, 'Device changed'):
                    self.run_import()
                self.assertFalse(self.cache.exists())
                self.assertEqual(list(self.cache.parent.glob('.xmb-sounds-*.tmp')), [])

    def test_cache_cannot_be_inside_psp(self):
        with self.assertRaises(ValueError):
            importer.import_from_psp(self.psp, self.psp / 'cache', device=self.device,
                                     verify_device=self.check)
        self.check.assert_not_called()

    def test_changed_drive_path_cannot_authorize_old_source(self):
        self.check.return_value = self.base / 'another-drive'
        with patch.object(importer, '_read_export', side_effect=AssertionError('Must not read old drive')):
            with self.assertRaisesRegex(ValueError, 'raíz de la PSP'):
                self.run_import()
        self.assertFalse(self.cache.exists())

    def test_source_symlink_is_rejected(self):
        actual = self.base / 'unrelated.rco'
        self.source.rename(actual)
        try:
            self.source.symlink_to(actual)
        except OSError:
            self.skipTest('This Windows account cannot create symbolic links')
        self.assertEqual(self.run_import()['status'], 'unavailable')
        self.decode.assert_not_called()

    def test_manifest_hash_validation_rejects_tampered_cache(self):
        self.run_import()
        (self.cache / SOUNDS['cursor']).write_bytes(b'invalid')
        self.assertFalse(importer.cache_status(self.cache)['ready'])


if __name__ == '__main__':
    unittest.main()
