"""Keep the real browser model aligned with backend native identities.

Run with Python and Node installed:
    python -m unittest discover -s tests -p test_xmb_native_identity_contract.py -v

This only evaluates the pure connection model in a Node VM. It does not open
a browser, instantiate a transport, probe USB or read PSP files.
"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import native_dock as native


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'web' / 'app.js'
NODE = shutil.which('node')
MODEL_RUNNER = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const first = payload.source.indexOf('// ==== inicio del modelo de conexión puro ====');
const last = payload.source.indexOf('// ==== fin del modelo de conexión puro ====', first);
if (first < 0 || last <= first) throw new Error('Pure connection model not found');
const scope = {};
vm.createContext(scope);
vm.runInContext(payload.source.slice(first, last), scope, { timeout: 1000 });
if (typeof scope.dockConnectionView !== 'function') throw new Error('Connection model missing');
const results = payload.cases.map(({label, controller}) => {
  const view = scope.dockConnectionView({
    demo: false,
    session: { running: false, pending_return: false, exit_requested: false },
    controller_status: controller,
    product_flow: {
      enabled: true, phase: 'ready', purpose: 'initial', choice: 'none',
      entry_completed: true, use_pc_saves: true,
      connection: { phase: 'verified' },
    },
  });
  return { label, screen: view.screen, waiting: view.waiting,
    nativeController: view.nativeController, canPlay: view.canPlay };
});
process.stdout.write(JSON.stringify(results));
"""


def _controller(revision):
    return {
        'connected': True, 'mode': 'native_dock', 'can_play': True,
        'phase': native.CONTROLLER, 'host_confirmed': True,
        'synthetic_storage': False, 'product_id': native.PID,
        # Sony's USB bcdDevice is independent of the verified firmware build.
        'revision': 0x0100, 'usb_revision': 0x0100,
        'protocol_version': native.VERSION, 'firmware_revision': revision,
        'build_id': native.FIRMWARE_BUILDS[revision],
        'capabilities': native.FIRMWARE_CAPABILITIES[revision],
    }


def _model_results(cases):
    result = subprocess.run(
        [NODE, '-e', MODEL_RUNNER], cwd=ROOT,
        input=json.dumps({'source': APP_JS.read_text(encoding='utf-8'), 'cases': cases}),
        encoding='utf-8', capture_output=True, timeout=10,
    )
    if result.returncode:
        raise AssertionError('The real connection model failed: ' + result.stderr)
    rows = json.loads(result.stdout)
    if len(rows) != len(cases):
        raise AssertionError('The connection model did not evaluate every identity')
    return rows


@unittest.skipUnless(NODE, 'Node is required to evaluate the real XMB connection model')
class XmbNativeIdentityContractTests(unittest.TestCase):
    def test_every_backend_identity_opens_launcher_after_no_copy(self):
        self.assertTrue(native.FIRMWARE_BUILDS)
        self.assertEqual(set(native.FIRMWARE_BUILDS), set(native.FIRMWARE_CAPABILITIES))
        self.assertFalse(set(native.FIRMWARE_BUILDS) & native.RETIRED_REVISIONS)
        cases = []
        # Do not repeat a version list in this test: new supported builds must
        # exercise the browser gate as soon as the backend admits them.
        for revision in sorted(native.FIRMWARE_BUILDS):
            controller = _controller(revision)
            native._validate_identity(*(controller[key] for key in (
                'protocol_version', 'firmware_revision', 'capabilities', 'build_id')))
            cases.append({'label': f'firmware {revision:04x}', 'controller': controller})
        for result in _model_results(cases):
            with self.subTest(identity=result['label']):
                self.assertEqual(result['screen'], 'menu', 'A verified ready session must leave the waiting screen')
                self.assertFalse(result['waiting'])
                self.assertTrue(result['nativeController'])
                self.assertTrue(result['canPlay'])

    def test_frontend_keeps_rejecting_incorrect_and_retired_identities(self):
        cases = []
        unknown_revision = max(set(native.FIRMWARE_BUILDS) | native.RETIRED_REVISIONS) + 1
        for revision in sorted(native.FIRMWARE_BUILDS):
            controller = _controller(revision)
            mutations = [
                ('build_id', controller['build_id'] + 1),
                ('capabilities', controller['capabilities'] ^ 1),
                ('protocol_version', native.VERSION + 1),
                ('firmware_revision', unknown_revision),
                ('firmware_revision', str(revision)),
            ]
            mutations.extend((field, None) for field in (
                'protocol_version', 'firmware_revision', 'capabilities', 'build_id'))
            mutations.extend(('firmware_revision', retired) for retired in sorted(native.RETIRED_REVISIONS))
            for field, value in mutations:
                changed = {**controller, field: value}
                label = f'firmware {revision:04x}, {field}={value!r}'
                with self.subTest(identity=label):
                    with self.assertRaises(native.NativeDockError):
                        native._validate_identity(*(changed[key] for key in (
                            'protocol_version', 'firmware_revision', 'capabilities', 'build_id')))
                cases.append({'label': label, 'controller': changed})
        for result in _model_results(cases):
            with self.subTest(identity=result['label']):
                self.assertEqual(result['screen'], 'waiting')
                self.assertTrue(result['waiting'])
                self.assertFalse(result['nativeController'])
                self.assertFalse(result['canPlay'])


if __name__ == '__main__':
    unittest.main()
