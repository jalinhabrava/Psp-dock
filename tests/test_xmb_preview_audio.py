"""Deterministic MCI preview tests with private synthetic files only."""
from pathlib import Path
import tempfile
import unittest

from xmb_preview_audio import PreviewAudioPlayer


class PreviewAudioTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.sound = self.root/'synthetic.mp3'
        self.sound.write_bytes(b'ID3' + b'\0' * 256)
        self.calls = []
        self.player = PreviewAudioPlayer(command=self.command)

    def command(self, message):
        self.calls.append(message)
        return 0

    def test_loop_volume_and_switch_use_one_owned_channel(self):
        self.assertTrue(self.player.play(self.sound))
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(self.calls[0].startswith('open "'))
        self.assertIn('setaudio ', self.calls[1])
        self.assertTrue(self.calls[2].endswith(' repeat'))
        self.assertTrue(self.player.play(self.sound))
        self.assertEqual(len(self.calls), 3, 'No restart on duplicate UI polling')
        next_sound = self.root/'second.mp3'
        next_sound.write_bytes(b'ID3' + b'\0' * 256)
        self.assertTrue(self.player.play(next_sound))
        self.assertTrue(self.calls[3].startswith('stop '))
        self.assertTrue(self.calls[4].startswith('close '))
        self.assertTrue(self.calls[5].startswith('open "'))
        self.player.stop()
        self.assertTrue(self.calls[-1].startswith('close '))
        count = len(self.calls)
        self.player.stop()
        self.assertEqual(len(self.calls), count)

    def test_failed_open_and_play_cannot_own_audio(self):
        def fail_on(verb):
            def send(message):
                self.calls.append(message)
                return 277 if message.startswith(verb + ' ') else 0
            return send
        failure = PreviewAudioPlayer(command=fail_on('open'))
        self.assertFalse(failure.play(self.sound))
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        second = PreviewAudioPlayer(command=fail_on('play'))
        self.assertFalse(second.play(self.sound))
        self.assertEqual([name.split(' ',1)[0] for name in self.calls],
                         ['open', 'setaudio', 'play', 'stop', 'close'])
        self.calls.clear()
        second.stop()
        self.assertEqual(self.calls, [])

    def test_missing_or_non_mp3_is_silent(self):
        self.assertFalse(self.player.play(self.root/'absent.mp3'))
        self.assertFalse(self.player.play(self.root/'untrusted.wav'))
        self.assertFalse(self.player.play(self.root/'too-big.mp3'))
        self.assertEqual(self.calls, [])

    def test_symlink_resource_rejected(self):
        alias = self.root/'alias.mp3'
        try:
            alias.symlink_to(self.sound)
        except OSError:
            self.skipTest('Symbolic links unavailable')
        self.assertFalse(self.player.play(alias))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
