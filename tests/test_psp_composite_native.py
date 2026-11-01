"""Cross-language: actual C firmware service <-> actual Python Windows client.

Run by build-wsl.sh using a temporary native library. No PSP USB involved.
The same pdc_service.c/pdc_sha256.c/pdb2_wire.c are linked in the PSP PRX.
"""
import ctypes as C
import os
from pathlib import Path
import struct
import tempfile
import unittest

from psp_winusb_transport import WinUSBTransport, WinUSBError, RESPONSE_CELL
from psp_composite_client import CompositeReader, PROBE_PATH, PROBE_SIZE, PROBE_SHA256
from tests.test_psp_composite_client import FakeUSB, INFO, NONCE, PATH, CONTENT


class NativeBackend(FakeUSB):
    def __init__(self):
        super().__init__()
        self.library = C.CDLL(os.environ['PDC_NATIVE_LIBRARY'])
        self.library.pdc_fixture_create.argtypes = []
        self.library.pdc_fixture_create.restype = C.c_void_p
        self.library.pdc_fixture_free.argtypes = [C.c_void_p]
        self.library.pdc_fixture_free.restype = None
        self.library.pdc_fixture_exchange.argtypes = [C.c_void_p, C.c_void_p,
            C.c_size_t, C.c_void_p, C.c_size_t]
        self.library.pdc_fixture_exchange.restype = C.c_int
        self.library.pdc_fixture_commands.argtypes = [C.c_void_p]
        self.library.pdc_fixture_commands.restype = C.c_uint
        self.library.pdc_fixture_changed.argtypes = [C.c_void_p]
        self.library.pdc_fixture_changed.restype = None
        self.state = self.library.pdc_fixture_create()
        if not self.state:
            raise MemoryError('Native test fixture allocation failed')
        self.packet = None

    def status(self):
        commands = self.library.pdc_fixture_commands(self.state)
        return struct.pack('<4sBBBB16sIi', b'PDC2', 2, 7, 1, 1, NONCE, commands, 0)

    def write(self, wire):
        self.packet = wire
        return len(wire)

    def read(self, length):
        assert length == RESPONSE_CELL
        request = C.create_string_buffer(self.packet, len(self.packet))
        output = C.create_string_buffer(length)
        result = self.library.pdc_fixture_exchange(self.state, request,
                     len(self.packet), output, length)
        if result <= 0:
            raise WinUSBError('Native C firmware rejected the command')
        return output.raw

    def close(self):
        if self.state:
            self.library.pdc_fixture_free(self.state)
            self.state = None
        super().close()


@unittest.skipUnless(os.environ.get('PDC_NATIVE_LIBRARY'),
                     'Native C cross-language test runs in the WSL build')
class NativeCompositeIntegrationTests(unittest.TestCase):
    def test_firmware_c_streams_fixture_to_python_client_byte_for_byte(self):
        backend = NativeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'probe.dat'
            with WinUSBTransport(PATH, backend=backend, enumerate_paths=lambda: [PATH]) as transport:
                client = CompositeReader(transport)
                rows = client.list_directory('PSP/GAME/PSPDockCompositeTest')
                self.assertEqual(rows[0]['name'], 'PROBE.DAT')
                result = client.copy_to_pc(PROBE_PATH, target,
                    expected_sha256=PROBE_SHA256, expected_size=PROBE_SIZE)
            self.assertEqual(result['sha256'], PROBE_SHA256)
            self.assertEqual(target.read_bytes(), CONTENT)
            self.assertFalse(result['psp_writes'])

    def test_native_source_changed_refuses_eof_and_discards_partial(self):
        backend = NativeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'should-not-exist.dat'
            with WinUSBTransport(PATH, backend=backend, enumerate_paths=lambda: [PATH]) as transport:
                client = CompositeReader(transport)
                def changed(current, total):
                    if current == total:
                        backend.library.pdc_fixture_changed(backend.state)
                with self.assertRaisesRegex(WinUSBError, 'source_changed'):
                    client.copy_to_pc(PROBE_PATH, target, progress=changed)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
