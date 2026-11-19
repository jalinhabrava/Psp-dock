from pathlib import Path
import subprocess
import unittest
import sys
from unittest.mock import Mock

from psp_dock.media.linux_sounds import LinuxSoundOutput


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux adapter requires a Linux host')
class LinuxSoundTests(unittest.TestCase):
    def test_plays_without_shell_and_replaces_only_owned_process(self):
        old, new = Mock(), Mock()
        old.poll.return_value = None
        spawn = Mock(side_effect=[old, new])
        player = LinuxSoundOutput(which=lambda name: '/usr/bin/pw-play', spawn=spawn)
        player.play(Path('/tmp/private/first.wav'))
        player.play(Path('/tmp/private/second.wav'))
        old.terminate.assert_called_once()
        old.wait.assert_called_once_with(timeout=0.15)
        self.assertEqual(spawn.call_args.args[0], ['/usr/bin/pw-play', '/tmp/private/second.wav'])
        self.assertNotIn('shell', spawn.call_args.kwargs)
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        new.poll.return_value = 0
        player.play(None)
        new.terminate.assert_not_called()
        new.wait.assert_called_once_with()

    def test_missing_audio_tool_fails_without_starting_process(self):
        spawn = Mock()
        player = LinuxSoundOutput(which=lambda name: None, spawn=spawn)
        player.play(None)
        with self.assertRaises(OSError):
            player.play(Path('/tmp/private/first.wav'))
        spawn.assert_not_called()

    def test_stop_timeout_is_bounded_and_targets_owned_child_only(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('pw-play', .15), 0]
        player = LinuxSoundOutput(which=lambda name: '/usr/bin/pw-play', spawn=Mock(return_value=child))
        player.play(Path('/tmp/private/first.wav'))
        player.play(None)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertIsNone(player.process)


if __name__ == '__main__':
    unittest.main()
