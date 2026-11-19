"""No USB hardware: faults injected into the real WinUSB transport/client."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from psp_dock.devices.psp_bulk_protocol import Frame, Op
from psp_dock.devices.psp_winusb_transport import (WinUSBTransport, WinUSBError, FIRMWARE_REVISION,
    RESPONSE_CELL, parse_firmware_status, validate_device, USBInterface, SetupPacket, PipeInfo)
from psp_dock.devices.psp_composite_client import (CompositeReader, PROBE_PATH, PROBE_SHA256,
    PROBE_SIZE, validate_remote_path)

NONCE = bytes(range(1, 17))
PATH = (r'\\?\usb#vid_054c&pid_01cb&mi_01#7&12345678&0&0001#'
        r'{12345678-1234-5678-9012-123456789abc}')
INFO = {'vid': 0x054c, 'pid': 0x01cb, 'revision': FIRMWARE_REVISION,
        'device_class': 0, 'configurations': 1, 'interface': 1, 'alternate': 0,
        'class': 0xff, 'subclass': 0x50, 'protocol': 2,
        'pipes': [(0x82, 2, 512), (0x03, 2, 512)]}
CONTENT = bytes((i * 17 + 3) % 256 for i in range(PROBE_SIZE))


class FakeUSB:
    hardware = False

    def __init__(self):
        self.info = copy.deepcopy(INFO)
        self.nonce = NONCE
        self.count = 0
        self.writes = []
        self.opened = False
        self.closed = 0
        self.command = None
        self.fault = None
        self.position = 0
        self.file = None
        self.list_pages = None

    def open(self, path):
        assert path == PATH
        self.opened = True

    def close(self):
        self.closed += 1
        self.opened = False

    def descriptors(self):
        return self.info

    def set_timeouts(self):
        pass

    def status(self):
        return struct.pack('<4sBBBB16sIi', b'PDC2', 2, 7, 1, 1,
                           self.nonce, self.count, 0)

    def write(self, packet):
        self.writes.append(packet)
        if self.fault == 'short_write':
            return 10
        size = struct.unpack_from('<I', packet, 36)[0]
        self.command = Frame.decode(packet[:44+size])
        assert not any(packet[44+size:]) and len(packet) == 512
        return len(packet)

    def read(self, size):
        assert size == RESPONSE_CELL
        req = self.command
        self.count += 1
        if req.seq != self.count:
            raise OSError('Repeated sequence')
        if self.fault == 'timeout':
            raise TimeoutError('Simulated USB disconnected')
        mode = Op.ERROR
        data = b'invalid_request'
        if req.op == Op.HELLO:
            mode, data = Op.HELLO_ACK, json.dumps({
                'protocol': 'PDB2-stream-v1', 'read_only': True,
                'max_chunk': 61440, 'volume_serial': None, 'capabilities': 7,
            }).encode()
        elif req.op == Op.STAT:
            self.file = req.payload.decode('ascii')
            self.position = 0
            mode, data = Op.META, json.dumps({
                'size': len(CONTENT), 'hash_at_eof': True, 'read_only': True,
            }).encode()
        elif req.op == Op.READ:
            count = struct.unpack_from('<I', req.payload)[0]
            assert req.payload[4:].decode('ascii') == self.file
            if req.offset != self.position:
                raise OSError('Nonsequential offset')
            if self.position == len(CONTENT):
                mode, data = Op.EOF, hashlib.sha256(CONTENT).digest()
                if self.fault == 'wrong_digest':
                    data = b'\x00' * 32
                if self.fault == 'session_changed':
                    self.nonce = bytes([23]) * 16
            else:
                mode, data = Op.DATA, CONTENT[self.position:self.position+count]
                self.position += len(data)
        elif req.op == Op.LIST:
            page = self.list_pages.get(req.offset) if self.list_pages else {
                'entries': [{'name': 'PROBE.DAT', 'size': len(CONTENT), 'directory': False}],
                'next': None,
            }
            mode, data = Op.LIST_DATA, json.dumps(page).encode()
        wire = Frame(mode, req.session, req.seq, data, req.offset).encode()
        padded = wire.ljust(RESPONSE_CELL, b'\0')
        if self.fault == 'corrupt_crc':
            return padded[:40] + bytes([padded[40] ^ 1]) + padded[41:]
        if self.fault == 'short_read':
            return padded[:20]
        if self.fault == 'wrong_padding':
            return padded[:-1] + b'x'
        return padded


def make_transport(fake=None):
    fake = fake or FakeUSB()
    return WinUSBTransport(PATH, backend=fake, enumerate_paths=lambda: [PATH]), fake


class CompositeClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ctypes_usb_layout_and_firmware_status(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(USBInterface), 9)
        self.assertEqual(ctypes.sizeof(SetupPacket), 8)
        self.assertEqual(ctypes.sizeof(PipeInfo), 12)
        self.assertEqual(parse_firmware_status(FakeUSB().status()).session, NONCE)
        for bad in (b'PDK1' + FakeUSB().status()[4:], b'\0'*32, b'PDC2'):
            with self.assertRaises(WinUSBError):
                parse_firmware_status(bad)

    def test_synthetic_file_stream_and_independent_digest_on_pc(self):
        transport, backend = make_transport()
        with transport:
            reader = CompositeReader(transport)
            listing = reader.list_directory('PSP/GAME/PSPDockCompositeTest')
            self.assertEqual(listing[0]['name'], 'PROBE.DAT')
            out = self.root/'copied-probe.dat'
            result = reader.copy_to_pc(PROBE_PATH, out,
                expected_size=PROBE_SIZE, expected_sha256=PROBE_SHA256)
        self.assertEqual(out.read_bytes(), CONTENT)
        self.assertEqual(result['sha256'], PROBE_SHA256)
        self.assertEqual(result['usb_backend'], 'test')
        self.assertFalse(result['psp_writes'])
        self.assertFalse(result['fat_volume_identity_verified'])
        self.assertEqual(backend.closed, 1)

    def test_exact_interface_descriptor_mismatch_blocks_all_bulk_commands(self):
        for change in ({'pid': 0x02d2}, {'revision': 0x0104}, {'revision': 0x0203}, {'class': 3},
                       {'interface': 0}, {'pipes': [(0x81, 3, 8)]}):
            fake = FakeUSB()
            fake.info.update(change)
            with self.subTest(change=change), self.assertRaises(WinUSBError):
                make_transport(fake)
            self.assertEqual(fake.writes, [])
            self.assertEqual(fake.closed, 1)

    def test_no_unenumerated_arbitrary_device_open(self):
        fake = FakeUSB()
        with self.assertRaises(WinUSBError):
            WinUSBTransport(PATH, backend=fake, enumerate_paths=lambda: [])
        self.assertFalse(fake.opened)
        with self.assertRaises(WinUSBError):
            WinUSBTransport(PATH.replace('mi_01', 'mi_00'), backend=fake,
                            enumerate_paths=lambda: [PATH])
        self.assertFalse(fake.opened)

    def test_timeout_partial_crc_and_padding_failures_never_retry(self):
        for fault in ('timeout', 'short_write', 'short_read', 'corrupt_crc', 'wrong_padding'):
            transport, fake = make_transport()
            fake.fault = fault
            with self.subTest(fault=fault), transport:
                with self.assertRaises((OSError, ValueError)):
                    CompositeReader(transport)
                count = len(fake.writes)
                with self.assertRaises(WinUSBError):
                    transport.exchange(Frame(Op.HELLO, NONCE, 1, b'PDB2-READONLY'))
                self.assertEqual(count, len(fake.writes))

    def test_hash_or_session_mismatch_keeps_partial_from_publication(self):
        for fault in ('wrong_digest', 'session_changed'):
            transport, fake = make_transport()
            with self.subTest(fault=fault), transport:
                reader = CompositeReader(transport)
                fake.fault = fault
                target = self.root/(fault+'.dat')
                with self.assertRaises(WinUSBError):
                    reader.copy_to_pc(PROBE_PATH, target)
                self.assertFalse(target.exists())
                self.assertEqual(list(self.root.glob('*.part')), [])

    def test_no_overwrite_or_traversal(self):
        transport, _ = make_transport()
        with transport:
            reader = CompositeReader(transport)
            target = self.root/'keep.dat'
            target.write_bytes(b'ORIGINAL')
            with self.assertRaises(FileExistsError):
                reader.copy_to_pc(PROBE_PATH, target)
            self.assertEqual(target.read_bytes(), b'ORIGINAL')
        for path in ('flash0:/kd/file', 'PSP/SYSTEM/controls.ini',
                     'ISO/../outside.iso', 'ISO/x\\y.iso', 'PSP/GAME/.private/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_remote_path(path)

    def test_directory_pagination_rejects_loops_and_duplicate_names(self):
        for pages in ({0: {'entries': [], 'next': 0}},
                      {0: {'entries': [{'name': '..', 'directory': True, 'size': 0}], 'next': None}},
                      {0: {'entries': [{'name': 'a', 'directory': True, 'size': 0}], 'next': 1},
                       1: {'entries': [{'name': 'A', 'directory': True, 'size': 0}], 'next': None}}):
            transport, fake = make_transport()
            fake.list_pages = pages
            with transport:
                reader = CompositeReader(transport)
                with self.assertRaises(WinUSBError):
                    reader.list_directory('ISO')
                calls = len(fake.writes)
                with self.assertRaises(WinUSBError):
                    reader.list_directory('ISO')
                self.assertEqual(len(fake.writes), calls)

    def test_invalid_status_poisoning_prevents_following_bulk_operations(self):
        transport, fake = make_transport()
        with transport:
            reader = CompositeReader(transport)
            with patch.object(fake, 'status', return_value=b'INVALID'):
                with self.assertRaises(WinUSBError):
                    transport.check_session()
            written = len(fake.writes)
            with self.assertRaises(WinUSBError):
                reader.list_directory('ISO')
            self.assertEqual(len(fake.writes), written)

    def test_local_cancel_discards_partial_and_invalidates_reader(self):
        transport, fake = make_transport()
        with transport:
            reader = CompositeReader(transport)
            def stop_copy(current, total):
                raise InterruptedError('Synthetic user cancellation')
            out = self.root / 'cancelled.dat'
            with self.assertRaises(InterruptedError):
                reader.copy_to_pc(PROBE_PATH, out, progress=stop_copy)
            self.assertFalse(out.exists())
            self.assertEqual(list(self.root.iterdir()), [])
            written = len(fake.writes)
            with self.assertRaises(WinUSBError):
                reader.copy_to_pc(PROBE_PATH, out)
            self.assertEqual(len(fake.writes), written)

    def test_trailing_spaces_or_dots_in_each_path_component_are_rejected(self):
        for bad in ('PSP/GAME/dir./EBOOT.PBP','PSP/SAVEDATA/slot /PARAM.SFO'):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                validate_remote_path(bad)


if __name__ == '__main__':
    unittest.main()
