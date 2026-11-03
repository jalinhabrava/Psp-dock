import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from xmb_sounds import UiSoundPlayer, validate_wav


def fixture_wav():
    # Synthetic silence is test input only; no replacement effect is shipped.
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x00\x00' * 2205)
    return stream.getvalue()


class UiSoundsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = fixture_wav()
        self.file = self.root / 'snd_cursor.wav'
        self.file.write_bytes(self.data)
        (self.root / 'manifest.json').write_text(json.dumps({'files': {
            self.file.name: {'size': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}
        }}), encoding='utf-8')
        self.native = Mock()
        self.clock = Mock(return_value=1.0)
        self.player = UiSoundPlayer(self.root, play=self.native, clock=self.clock)

    def test_exact_pcm_and_hash_required(self):
        self.assertTrue(self.player.play('cursor'))
        self.native.assert_called_once_with(self.file)
        self.file.write_bytes(self.data[:-2] + b'XX')
        self.clock.return_value = 2
        self.assertFalse(self.player.play('cursor'))
        self.assertEqual(self.native.call_count, 1)

    def test_no_original_is_silent_not_system_beep(self):
        self.assertFalse(self.player.play('decide'))
        self.native.assert_not_called()
        self.player.stop()
        self.native.assert_not_called()

    def test_debounce_and_stop_owned_playback(self):
        self.assertTrue(self.player.play('cursor'))
        self.assertFalse(self.player.play('cursor'))
        self.player.stop()
        self.native.assert_called_with(None)
        self.player.stop()
        self.assertEqual(self.native.call_count, 2)
        self.clock.return_value += .1
        self.assertTrue(self.player.play('cursor'))

    def test_bounded_decoder_and_names(self):
        for bad in (b'not wave', self.data[:-3], b'X' * 600000):
            with self.assertRaises(ValueError):
                validate_wav(bad)
        for name in ('../cursor', 'C:/audio', 'cursor.wav', 'beep'):
            with self.assertRaises(ValueError):
                self.player.play(name)
        self.native.assert_not_called()

    def test_no_crash_when_audio_device_fails(self):
        self.native.side_effect = RuntimeError('no device')
        self.assertFalse(self.player.play('cursor'))

    def test_symlink_rejected(self):
        with patch('xmb_sounds._no_links', side_effect=ValueError('symlink')):
            self.assertFalse(self.player.play('cursor'))
        self.native.assert_not_called()

    def test_deep_manifest_is_optional_failure_not_startup_recursion(self):
        (self.root / 'manifest.json').write_text('[' * 20000 + '0' + ']' * 20000, encoding='utf-8')
        self.assertIsNone(self.player.asset('cursor'))
        self.assertFalse(self.player.play('cursor'))
        self.native.assert_not_called()


if __name__ == '__main__':
    unittest.main()
