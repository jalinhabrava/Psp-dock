import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dock_events import EventJournal


class EventsTests(unittest.TestCase):
    def test_append_redacts_secrets_and_never_fakes_a_hardware_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            journal=EventJournal(Path(temp)/'events.jsonl')
            self.assertTrue(journal.record(
                'game_launch',evidence='pc_action',game_id='a'*24,
                to_psp=0,secret='NOT_LOGGED',
                pdk1_session='ab'*16,password='NOT_LOGGED_EITHER',
                result='started'))
            self.assertTrue(journal.record('game_closed',count=1))
            records=[json.loads(line) for line in journal.path.read_text().splitlines()]
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['game_id'],'a'*24)
            self.assertEqual(records[0]['result'],'started')
            self.assertNotIn('secret',records[0])
            self.assertNotIn('pdk1_session',records[0])
            self.assertNotIn('password',records[0])
            self.assertFalse(records[0]['physical_role_bridge_verified'])
            self.assertFalse(records[0]['firmware_ack_verified'])

    def test_invalid_event_name_and_evidence_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            log=EventJournal(Path(temp)/'events.jsonl')
            with self.assertRaises(ValueError):
                log.record('../bad_path')
            with self.assertRaises(ValueError):
                log.record('usb_role',evidence='hardware_confirmed')
            self.assertFalse(log.path.exists())

    def test_journal_failure_does_not_falsely_signal_save_copy_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            log=EventJournal(Path(temp)/'events.jsonl')
            with patch.object(Path,'open',side_effect=PermissionError('no PC log')):
                self.assertFalse(log.record('save_sync_verified',count=1))
            self.assertEqual(log.write_errors,1)

if __name__=="__main__":
    unittest.main()
