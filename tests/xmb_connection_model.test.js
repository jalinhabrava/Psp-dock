// Manual Windows window: waiting -> verified Dock -> XMB; no USB events,
// resident service or real PSP needed. node tests/xmb_connection_model.test.js
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const source = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const first = source.indexOf('// ==== inicio del modelo de conexión puro ====');
const last = source.indexOf('// ==== fin del modelo puro ====', first);
assert(first >= 0 && last > first);
const scope = {};
vm.createContext(scope);
vm.runInContext(source.slice(first, last), scope);
const { dockConnectionView: view, xmbStep } = scope;
const normalize = (value) => JSON.parse(JSON.stringify(value));
const stable = {
  controller_status: { connected: true, mode: 'hid_manual', can_play: true,
    product_id: 0x01cb, revision: 0x0104 },
};
const diagnostic = {
  controller_status: { connected: true, mode: 'msc_diagnostic', can_play: false,
    product_id: 0x01cc, revision: 0x0207, synthetic_storage: true },
};
const absent = { controller_status: { connected: false, mode: 'await_hid', can_play: false } };
let assertions = 0;
const equal = (actual, expected, label) => { assert.equal(actual, expected, label); ++assertions; };

equal(view(null).waiting, true, 'The initial HTML stays on the connection screen');
equal(view(null).kind, 'loading', 'Initial state does not claim a missing PSP');
equal(view({}).waiting, true, 'An empty state never opens the menu');
equal(view(absent).waiting, true, 'No verified PSP means waiting');
equal(view({ ...absent, games: [{ id: 'cached', availability: 'ready' }], library_source: 'cached_manifest' }).waiting,
  true, 'A remembered PC catalog does not act as a connected PSP');
const storage = { selected_device: 'G:\\', controller_status: { connected: false, mode: 'usb_storage', can_play: false } };
equal(view(storage).waiting, true, 'Sony USB storage alone does not open the XMB');
equal(view(storage).kind, 'storage', 'Native USB storage receives its own helpful context');
assert.match(view(storage).detail, /PSP Dock/); ++assertions;
equal(view(storage).canPrepareStorage, true, 'Validated Sony storage offers a secondary preparation action');
equal(view(storage).storageSetup, false, 'Preparation is never selected automatically');
equal(view(storage, { storageSetupRequested: true }).screen, 'menu', 'An explicit preparation request opens the previous XMB workflow');
equal(view(storage, { storageSetupRequested: true }).storageSetup, true, 'The opened menu is labeled storage preparation');
equal(view(storage, { storageSetupRequested: true }).canPlay, false, 'Storage preparation is not a HID/play authorization');
equal(view({ ...storage, controller_status: { ...storage.controller_status, can_play: true } }, { storageSetupRequested: true }).canPlay,
  false, 'Even contradictory storage eligibility never grants playback');
equal(view(absent, { storageSetupRequested: true }).waiting, true, 'A disconnected PSP cannot remain in preparation');
equal(view(absent).canPrepareStorage, false, 'No PSP means no preparation action');
equal(view({ selected_device: 'G:\\', controller_status: { connected: false, mode: 'await_hid' } }, { storageSetupRequested: true }).waiting,
  true, 'A selected path without validated storage mode does not unlock preparation');
equal(view({ controller_status: storage.controller_status }, { storageSetupRequested: true }).waiting,
  true, 'Storage mode without a selected device does not unlock preparation');
equal(view(storage, { storageSetupRequested: true, serviceOffline: true }).waiting,
  true, 'A stale storage connection cannot unlock preparation');
equal(view(diagnostic).canPrepareStorage, false, 'The synthetic diagnostic disk never becomes a preparation source');
equal(view({ controller_status: { connected: true, mode: 'unrecognized', can_play: true } }).waiting,
  true, 'A generic or unknown controller is insufficient');

equal(view(stable).screen, 'menu', 'Verified stable PDK1 opens the menu');
equal(view(stable).canPlay, true, 'Stable controller eligibility passes to the remaining game guards');
equal(view({ controller_status: { connected: true, mode: 'hid_manual' } }).canPlay,
  false, 'Missing can_play never authorizes a launch');
equal(view(diagnostic).screen, 'menu', '0207 permits menu navigation');
equal(view(diagnostic).diagnostic, true, '0207 has an explicit diagnostic banner');
equal(view(diagnostic).canPlay, false, '0207 cannot play games');
equal(view({ controller_status: { ...diagnostic.controller_status, can_play: true } }).canPlay,
  false, 'The diagnostic mode cannot be overridden by a contradictory eligibility bit');
equal(view({ ...diagnostic, can_launch_game: true, runtime_prepared: true, preflight: { saves_prepared: true } }).canPlay,
  false, 'Remembered prepared games do not grant diagnostic playback');
equal(view({ ...absent, demo: true }).screen, 'menu', 'The existing demo remains directly navigable');
equal(view({ ...stable, demo: true }).canPlay, false, 'Demo never authorizes a real game');
equal(view(absent).screen, 'waiting', 'A disconnect after the menu returns to waiting');
equal(view(stable, { serviceOffline: true }).waiting, true, 'Stale state after losing the backend is not a live connection');
equal(view(stable, { serviceOffline: true }).canPlay, false);

for (const [label, state, options] of [
  ['running game', { session: { running: true } }, {}],
  ['return of saves pending', { session: { pending_return: true } }, {}],
  ['explicit exit pending', { session: { exit_requested: true } }, {}],
  ['session recovery', { recovery_required: true }, {}],
  ['backend transfer', { transfer_status: { active: true } }, {}],
  ['copy waiting for Sony storage', { copy_queue: { status: 'waiting_for_storage' } }, {}],
  ['copy in progress', { copy_queue: { status: 'copying' } }, {}],
  ['interrupted copy requiring review', { copy_queue: { status: 'interrupted_review' } }, {}],
  ['local transfer in progress', {}, { transferInFlight: true }],
  ['mutation in progress', {}, { busy: true }],
  ['texture transfer in progress', {}, { textureActive: true }],
]) {
  const current = view({ ...absent, ...state }, options);
  equal(current.waiting, false, label + ' keeps its existing recovery/progress UI');
  equal(current.protectedWork, true, label + ' is explicitly protected');
  equal(current.canPlay, false, label + ' does not fabricate a connected controller');
}
equal(view({ ...absent, session: { pending_return: true } }, { serviceOffline: true }).waiting,
  false, 'A backend outage also keeps pending recovery visible');
equal(view({ ...absent, copy_queue: { status: 'copied' } }).waiting,
  true, 'A completed copy does not hold the disconnected menu indefinitely');
equal(view(stable, { closed: true }).waiting, true, 'A successfully closed window never reopens from stale state');
equal(view(stable, { closed: true }).canPlay, false);

// Every input, including confirm/side-menu cases, is blocked in the
// pure navigation reducer while the connection screen is active.
const sections = [{ id: 'ajustes', count: 2 }, { id: 'juegos', count: 3 }];
const baseNav = { cat: 1, idx: { juegos: 1 }, level: 'list', subIdx: 0, side: null, confirm: null };
const inputs = ['left', 'right', 'up', 'down', 'accept', 'back', 'menu', 'select', 'start', 'pageLeft', 'pageRight'];
for (const extra of [{}, { confirm: { yes: true } }, { side: { i: 1 } }, { level: 'sub', subIdx: 2 }]) {
  const nav = { ...baseNav, ...extra };
  for (const input of inputs) {
    const result = xmbStep(nav, { sections, waiting: true, sideCount: 3, subCount: 4 }, input);
    assert.deepEqual(normalize(result.nav), nav, input + ' cannot move the hidden menu');
    equal(result.effect, null, input + ' cannot trigger a hidden action');
  }
}
equal(xmbStep(baseNav, { sections, waiting: false }, 'accept').effect.type,
  'activate', 'Normal menu navigation still activates an item');

// Exercise the real runtime entry, not only its reducer: a stray input
// from WinMM/Gamepad/keyboard must not build or touch hidden menu data.
const pressStart = source.indexOf('function xmbPress(');
const pressEnd = source.indexOf('// ---------- pintado ----------', pressStart);
const runtime = {
  xmbConnectionView: () => ({ waiting: true }),
  xmbBuildSections: () => { throw new Error('Hidden menu was built'); },
  xmb: { sections: [], nav: { ...baseNav } },
};
vm.createContext(runtime);
vm.runInContext(source.slice(pressStart, pressEnd), runtime);
for (const input of inputs) runtime.xmbPress(input);
assert.deepEqual(runtime.xmb.nav, baseNav); ++assertions;

// Test the actual runtime opt-in and its reset across disconnect/reconnect.
const connectionStart = source.indexOf('function xmbConnectionView(');
const connectionEnd = source.indexOf('function xmbRenderConnection(', connectionStart);
const preparation = {
  dockConnectionView: view,
  ui: { state: storage, storageSetupRequested: false },
  xmb: { nav: { ...baseNav } },
  render: () => { preparation.renders += 1; }, renders: 0,
};
vm.createContext(preparation);
vm.runInContext(source.slice(connectionStart, connectionEnd), preparation);
equal(preparation.xmbConnectionView().waiting, true, 'USB arrival alone stays on the connection screen');
preparation.requestStorageSetup();
equal(preparation.renders, 1, 'An explicit click requests a render, not a backend mutation');
equal(preparation.ui.storageSetupRequested, true);
equal(preparation.xmbConnectionView().storageSetup, true);
equal(preparation.xmb.nav.cat, 1, 'Preparation opens the Dock workflow section');
preparation.ui.state = absent;
equal(preparation.xmbConnectionView().waiting, true);
equal(preparation.ui.storageSetupRequested, false, 'Disconnect clears the remembered opt-in');
preparation.ui.state = storage;
equal(preparation.xmbConnectionView().waiting, true, 'Reconnecting Sony storage requires another explicit choice');
preparation.ui.state = stable;
equal(preparation.xmbConnectionView().screen, 'menu', 'Stable HID still enters its menu automatically');
equal(preparation.ui.storageSetupRequested, false, 'HID does not carry a storage-preparation opt-in');

// Run the actual window-lifetime functions against a fake HTTP boundary.
// This checks routing, identity and failure handling without a browser.
const lifecycleStart = source.indexOf('async function appHeartbeat(');
const lifecycleEnd = source.indexOf('async function mutate(', lifecycleStart);
function windowScope() {
  const nodes = new Map();
  const calls = [];
  const current = {
    ui: { appClientId: '0123456789abcdef0123456789abcdef', token: 'local-test-token', appClosed: false, appClosing: false,
      appPageHidden: false, appHeartbeatPending: false },
    $: (id) => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    request: async (route, body) => { calls.push({ route, body }); return { ok: true }; },
    fetch: async (route, options) => { calls.push({ route, options }); return {}; },
    setTimeout: (callback, delay) => { current.timer = { callback, delay }; return 17; },
    clearTimeout: () => { current.timer = null; },
    xmbConnectionView: () => ({ waiting: true }),
    xmbRender: () => {},
    window: { close: () => { current.closedWindows += 1; } },
    closedWindows: 0, calls, nodes,
  };
  vm.createContext(current);
  vm.runInContext(source.slice(lifecycleStart, lifecycleEnd), current);
  return current;
}
async function lifetimeTests() {
  let context = windowScope();
  await context.appHeartbeat();
  equal(context.calls[0].route, '/api/app/heartbeat');
  equal(context.calls[0].body.client_id, context.ui.appClientId);
  equal(context.timer.delay, 2000, 'Heartbeat is bounded by the agreed two-second interval');
  context.ui.appPageHidden = true;
  await context.appHeartbeat();
  equal(context.calls.length, 1, 'No new heartbeat is sent after pagehide');

  context = windowScope();
  await context.closeDockApp();
  equal(context.calls.length, 1, 'Closing only requests window close; no sync or USB handoff');
  equal(context.calls[0].route, '/api/app/close');
  equal(context.calls[0].body.reason, 'button');
  equal(context.ui.appClosed, true);
  equal(context.closedWindows, 1, 'Browser close is requested only after backend approval');
  context.appPageLeaving();
  equal(context.calls.length, 1, 'Successful explicit close is not sent twice');

  context = windowScope();
  context.xmbConnectionView = () => ({ waiting: false });
  await context.closeDockApp();
  equal(context.calls.length, 0, 'No hidden close-button action during a game/recovery/menu');

  context = windowScope();
  context.request = async (route, body) => {
    context.calls.push({ route, body });
    if (route === '/api/app/close') throw new Error('La copia sigue activa.');
    return { ok: true };
  };
  await context.closeDockApp();
  equal(context.ui.appClosed, false, 'A rejected close keeps the client open');
  equal(context.ui.appClosing, false);
  equal(context.closedWindows, 0);
  equal(context.$('dock-connect-error').hidden, false);
  equal(context.$('dock-connect-error').textContent, 'La copia sigue activa.');
  equal(context.calls[1].route, '/api/app/heartbeat', 'Lease resumes after a rejected close');

  context = windowScope();
  context.appPageLeaving();
  equal(context.calls.length, 1);
  equal(context.calls[0].route, '/api/app/close', 'Token is not placed in the URL');
  equal(context.calls[0].options.keepalive, true);
  equal(context.calls[0].options.headers['X-Dock-Token'], 'local-test-token');
  equal(JSON.parse(context.calls[0].options.body).client_id, context.ui.appClientId);
  equal(JSON.parse(context.calls[0].options.body).reason, 'pagehide');
  equal(context.ui.appPageHidden, true);
}
lifetimeTests().then(() => {
  console.log('PSP Dock connection/window model and runtime tests: OK (' + assertions + ' assertions)');
}).catch((error) => { console.error(error); process.exitCode = 1; });
