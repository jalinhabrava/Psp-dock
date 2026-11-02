// Product session UI: actual state/input routes with a fake HTTP boundary.
// No PSP, storage writes, browser, service or background task is needed.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../web/app.js'), 'utf8');
const modelStart = source.indexOf('// ==== inicio del modelo de conexión puro ====');
const modelEnd = source.indexOf('// ==== fin del modelo puro ====', modelStart);
assert(modelStart >= 0 && modelEnd > modelStart);
const scope = {};
vm.createContext(scope);
vm.runInContext(source.slice(modelStart, modelEnd), scope);
const { dockProductFlowView: product, dockConnectionView: connection, dockRetroSaveChoices: retroChoices } = scope;
const normalize = (value) => JSON.parse(JSON.stringify(value));
let assertions = 0;
const equal = (actual, expected, label) => { assert.equal(actual, expected, label); ++assertions; };
const stable = { controller_status: { connected: true, mode: 'hid_manual', can_play: true } };
const storage = { selected_device: 'G:\\', controller_status: { connected: false, mode: 'usb_storage', can_play: false } };
const flow = (phase, overrides = {}) => ({ enabled: true, phase, purpose: 'initial',
  offer_id: 'offer-session-one', can_choose: phase === 'choosing',
  counts: { psp_games: 7, pc_games: 2, missing_games: 5, save_actions: 3, save_conflicts: 1 },
  capabilities: { automatic_transition: false, controller_during_storage: false }, ...overrides });

equal(product({}).enabled, false, 'Legacy clients do not enter the new session journey');
equal(connection(stable).screen, 'menu', 'Existing stable HID remains usable');
equal(connection({ controller_status: { connected: true, mode: 'msc_diagnostic', can_play: false } }).screen,
  'menu', '0207 retains its diagnostic navigation');
equal(connection({ ...stable, product_flow: { enabled: false, phase: 'inventory' } }).canPlay,
  true, 'An explicitly disabled product adapter does not block legacy eligibility');
for (const phase of ['entering_storage', 'inventory', 'choosing', 'syncing_in', 'entering_controller',
  'waiting_storage_copy', 'copying_game', 'returning_to_controller', 'waiting_game_exit', 'waiting_storage_return',
  'syncing_out', 'finalizing', 'safe_to_disconnect', 'attention']) {
  const state = { ...stable, product_flow: flow(phase, { blocking: false }) };
  equal(connection(state).screen, 'product', phase + ' uses the product screen');
  equal(connection(state).canPlay, false, phase + ' never grants a game launch');
  equal(product(state).blocking, true, phase + ' ignores a contradictory nonblocking bit');
}
equal(connection({ ...stable, product_flow: flow('waiting_psp') }).screen, 'waiting',
  'The authoritative waiting phase does not reopen a stale connected XMB');
equal(connection({ ...stable, product_flow: flow('ready') }).screen, 'menu');
equal(connection({ ...stable, product_flow: flow('ready') }).canPlay, true);
equal(connection({ ...storage, product_flow: flow('ready') }).canPlay, false,
  'Ready does not fabricate a gamepad');
equal(connection({ ...stable, demo: true, product_flow: flow('ready') }).canPlay, false);
const native = { selected_device: 'G:\\', controller_status: { connected: true, mode: 'native_dock', can_play: true,
  synthetic_storage: false, product_id: 0x01cd, revision: 0x0301, phase: 2 }, product_flow: flow('ready') };
equal(connection(native).canPlay, true, 'Verified native Dock can keep its Sony storage enumerated');
equal(connection(native).nativeController, true);
for (const changes of [{ product_id: 0x01cc }, { revision: 0x0207 }, { revision: 0x0300 }, { revision: 0x0302 }, { phase: 1 }, { synthetic_storage: true }, { can_play: false }]) {
  equal(connection({ ...native, controller_status: { ...native.controller_status, ...changes } }).canPlay,
    false, 'Native identity, controller phase and host eligibility must all agree');
}
equal(connection({ ...native, product_flow: { enabled: false } }).canPlay, false,
  'A native controller without the coordinated journey is insufficient');
equal(product({ ...native, product_flow: flow('finalizing', { can_close: true }) }).canClose, false,
  'Finalization never advertises safe disconnect or permits the close button');
const waitingGameExit = product({ ...native, session: { running: true },
  product_flow: flow('waiting_game_exit', { window_close_requested: true,
    can_choose: true, can_review_saves: true, can_retry: true, can_close: true }) });
equal(waitingGameExit.title, 'Terminando la sesión');
equal(waitingGameExit.message, 'Has pedido salir. Cierra el emulador normalmente para devolver las partidas.');
equal(waitingGameExit.canChoose || waitingGameExit.canReview || waitingGameExit.canRetry || waitingGameExit.canClose,
  false, 'Closing a window while an emulator remains open cannot authorize other actions');
equal(waitingGameExit.working, false, 'Waiting for normal emulator exit does not imply a copy is already running');
const closeMessage = 'Has pedido salir. Espera a que termine la copia actual.';
const closingCopy = product({ ...native, product_flow: flow('copying_game', {
  window_close_requested: true, window_close_message: closeMessage }) });
assert.match(closingCopy.note, /Mantén la PSP conectada/); ++assertions;
assert.ok(closingCopy.note.includes(closeMessage)); ++assertions;
equal(product({ ...native, product_flow: flow('attention', { window_close_message: closeMessage }) }).note,
  closeMessage, 'An interrupted window close stays visible during conflict review');
for (const phase of [null, 'unexpected', '__proto__']) {
  const state = { ...stable, product_flow: flow(phase, { can_choose: true }) };
  equal(connection(state).screen, 'product', 'Unknown phase blocks the XMB');
  equal(product(state).canChoose, false, 'Unknown phase cannot authorize a choice');
}
const choose = { ...storage, product_flow: flow('choosing') };
equal(product(choose).canChoose, true);
assert.match(product(choose).note, /teclado o el ratón/); ++assertions;
assert.doesNotMatch(product(choose).note, /confirma con ✕/); ++assertions;
equal(product({ ...choose, product_flow: flow('choosing', { offer_id: '' }) }).canChoose, false);
equal(product({ ...choose, product_flow: flow('choosing', { can_choose: false }) }).canChoose, false);
equal(product(choose, { busy: true }).canChoose, false);
equal(product(choose, { serviceOffline: true }).canChoose, false);
equal(product(choose, { closed: true }).canChoose, false);
const simultaneous = { ...stable, product_flow: flow('choosing', {
  capabilities: { controller_during_storage: true, automatic_transition: true } }) };
assert.match(product(simultaneous).note, /confirma con ✕/); ++assertions;
assert.doesNotMatch(product({ ...simultaneous, controller_status: { connected: false } }).note,
  /confirma con ✕/); ++assertions;

const ready = { ...stable, product_flow: flow('ready'), preflight: { saves_prepared: false } };
const before = JSON.stringify(ready);
connection(ready);
equal(JSON.stringify(ready), before, 'Presentation never marks unsynchronized saves prepared');
for (const choice of ['games', 'none']) {
  const local = { ...stable, product_flow: flow('ready', { choice, use_pc_saves: true }) };
  equal(product(local).usePcSaves, true, 'Explicit ' + choice + ' may use PC saves for this session');
  equal(product(local, { serviceOffline: true }).usePcSaves, false);
  equal(product({ ...local, product_flow: { ...local.product_flow, phase: 'choosing' } }).usePcSaves, false);
}
for (const choice of ['all', 'saves', null]) equal(product({ ...stable,
  product_flow: flow('ready', { choice, use_pc_saves: true }) }).usePcSaves, false,
  'A contradictory flag never changes the explicitly selected save policy');
equal(product({ ...stable, product_flow: flow('ready', { choice: 'none' }) }).usePcSaves, false,
  'Choice text alone is insufficient without backend per-session authorization');
equal(product({ ...choose, product_flow: flow('syncing_out') }).note.includes('Los juegos permanecen en el PC'), true,
  'Exit describes saves/states only');
const final = { ...storage, product_flow: flow('safe_to_disconnect', { can_close: true }) };
equal(product(final).canClose, true);
equal(product({ ...final, product_flow: flow('safe_to_disconnect') }).canClose, false);
equal(product(final, { serviceOffline: true }).canClose, false);
assert.doesNotMatch(product(final, { serviceOffline: true }).message, /Ya puedes desconectar/); ++assertions;
equal(connection({ ...stable, product_flow: flow('ready') }, { serviceOffline: true }).screen,
  'product', 'Loss of backend keeps the active journey blocked');
const attention = { ...storage, product_flow: flow('attention', { can_review_saves: true, can_retry: true }) };
equal(product(attention).canReview, true);
equal(product(attention).canRetry, true);
equal(product(attention, { busy: true }).canRetry, false);
equal(product({ ...attention, product_flow: flow('attention', { can_retry: true, offer_id: '' }) }).canRetry, false);
equal(product({ ...choose, product_flow: flow('choosing', { can_review_saves: true }) }).canReview, false,
  'Copy permission cannot be bypassed by opening conflict resolution before the choice');

let measured = product({ ...storage, product_flow: flow('copying_game'),
  transfer_status: { active: true, copied_bytes: 25, total_bytes: 100, title: 'A game' } });
equal(measured.showProgress, true);
equal(measured.progress.total, 100);
equal(measured.progress.copied, 25);
measured = product({ ...storage, product_flow: flow('copying_game'),
  transfer_status: { active: true, copied_bytes: 300, total_bytes: 100 } });
equal(measured.progress.copied, 100, 'Inconsistent totals cannot render >100 percent');
equal(product({ ...storage, product_flow: flow('copying_game') }).showProgress, false,
  'No invented transfer progress without an active measured operation');
equal(product({ ...storage, product_flow: flow('inventory') }).working, true);
equal(product({ ...storage, product_flow: flow('choosing', { counts: { psp_games: -2, pc_games: '100' } }) }).counts.psp, null);
equal(product({ ...storage, product_flow: flow('choosing', { counts: { psp_games: -2, pc_games: '100' } }) }).counts.pc, null);

const conflict = { status: 'conflict', rom_id: 'rom-one', revision: 'revision-one', psp_present: true, pc_present: true };
assert.deepEqual(normalize(retroChoices(conflict, false)), ['psp', 'pc']); ++assertions;
assert.deepEqual(normalize(retroChoices(conflict, true)), ['pc', 'skip']); ++assertions;
assert.deepEqual(normalize(retroChoices({ ...conflict, status: 'psp_to_pc' }, true)), ['skip']); ++assertions;
for (const status of ['unsupported', 'blocked', 'none', 'identical']) {
  assert.deepEqual(normalize(retroChoices({ ...conflict, status }, true)), [], status + ' offers no save transfer'); ++assertions;
}
assert.deepEqual(normalize(retroChoices({ ...conflict, revision: null }, false)), []); ++assertions;
assert.deepEqual(normalize(retroChoices({ ...conflict, skipped: true }, true)), ['pc']); ++assertions;

// Exercise the actual click handlers and controller router. Neither showing
// the offer nor Circle submits it; every copy request contains the exact offer.
const start = source.indexOf('async function chooseProductSession(');
const end = source.indexOf('function xmbPreviewGame(', start);
assert(start > 0 && end > start);
const calls = [];
const runtime = {
  ui: { state: choose, busy: false, serviceOffline: false },
  document: { activeElement: null, querySelector: () => null },
  xmbConnectionView: () => connection(runtime.ui.state, { busy: runtime.ui.busy, serviceOffline: runtime.ui.serviceOffline }),
  xmbRender: () => { runtime.renders += 1; }, renders: 0,
  renderSaves: () => { runtime.savesRenders += 1; }, savesRenders: 0,
  mutate: async (route, body) => { calls.push({ route, body: normalize(body) }); return true; },
};
const buttons = ['all', 'saves', 'games', 'none'].map((choice) => ({
  choice, disabled: false, hidden: false,
  getClientRects: () => [{}],
  focus() { runtime.document.activeElement = this; },
  click() { return runtime.chooseProductSession(choice); },
}));
const panel = { querySelectorAll: () => buttons,
  querySelector: () => buttons[3] };
const savesDialog = { open: false, showModal() { this.open = true; } };
runtime.$ = (id) => id === 'dock-session' ? panel : id === 'saves-dialog' ? savesDialog : null;
vm.createContext(runtime);
vm.runInContext(source.slice(start, end), runtime);

async function inputs() {
  equal(calls.length, 0, 'Rendering/loading the handlers has no mutation');
  runtime.productFlowPress('back');
  equal(calls.length, 0, 'Circle only focuses the explicit no-copy choice');
  equal(runtime.document.activeElement.choice, 'none');
  runtime.productFlowPress('accept');
  await Promise.resolve();
  assert.deepEqual(calls.pop(), { route: '/api/session-choice', body: { choice: 'none', offer_id: 'offer-session-one' } }); ++assertions;
  for (const choice of ['all', 'saves', 'games']) {
    await runtime.chooseProductSession(choice);
    assert.deepEqual(calls.pop(), { route: '/api/session-choice', body: { choice, offer_id: 'offer-session-one' } }); ++assertions;
  }
  await runtime.chooseProductSession('unknown');
  equal(calls.length, 0);
  runtime.ui.busy = true;
  await runtime.chooseProductSession('all');
  equal(calls.length, 0, 'A second click during a request cannot submit again');
  runtime.ui.busy = false;
  runtime.ui.state = { ...choose, product_flow: flow('inventory') };
  await runtime.chooseProductSession('all');
  equal(calls.length, 0, 'A stale visible choice cannot submit after its phase changed');
  runtime.ui.state = { ...choose, product_flow: flow('choosing', { offer_id: 'offer-session-two' }) };
  await runtime.chooseProductSession('all');
  equal(calls.pop().body.offer_id, 'offer-session-two', 'Only the current authoritative offer is sent');
  runtime.ui.state = attention;
  runtime.reviewProductSaves();
  equal(savesDialog.open, true);
  equal(runtime.savesRenders, 1);
  equal(calls.length, 0, 'Reviewing conflicts alone never writes');
  await runtime.retryProductSession();
  assert.deepEqual(calls.pop(), { route: '/api/session-retry', body: { offer_id: 'offer-session-one' } }); ++assertions;
  runtime.ui.state = { ...storage, product_flow: flow('attention') };
  await runtime.retryProductSession();
  equal(calls.length, 0, 'Retry is unavailable without explicit backend eligibility');

  // A product exit reviews individual save slots. Its old generic sync
  // button must not offer an endpoint the exit orchestrator rejects.
  const saveNodes = new Map();
  const saveNode = () => ({ children: [], hidden: false, disabled: false,
    classList: { toggle: () => {} },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(event, handler) { this[event] = handler; }, close() {} });
  const saveRuntime = {
    ui: { state: { ...storage, product_flow: flow('attention', { purpose: 'exit', can_review_saves: true }),
      session: { pending_return: true }, preflight: { saves_prepared: true },
      saves: { actions: [{ slot: 'ready-to-return', direction: 'to_psp' }],
        conflicts: [{ slot: 'needs-review', revision: 'plan-one' }] } },
      busy: false, serviceOffline: false, savesSignature: '' },
    dockProductFlowView: product, renderRetroSaveReview: () => {},
    el: (tag, className, textContent) => ({ ...saveNode(), tag, className, textContent }),
    $: (id) => { if (!saveNodes.has(id)) saveNodes.set(id, saveNode()); return saveNodes.get(id); },
    mutate: async () => { saveRuntime.mutations += 1; return false; }, mutations: 0,
  };
  vm.createContext(saveRuntime);
  const savesStart = source.indexOf('function renderSaves()');
  vm.runInContext(source.slice(savesStart, source.indexOf('function renderRetroSaveReview(', savesStart)), saveRuntime);
  saveRuntime.renderSaves();
  equal(saveNodes.get('sync-button').hidden, true, 'Product exit hides unscoped legacy synchronization');
  equal(saveNodes.get('sync-button').disabled, true, 'Hidden product-exit sync remains disabled');
  saveRuntime.ui.state.saves = { actions: [], conflicts: [], unchanged: 2 };
  saveRuntime.renderSaves();
  equal(saveNodes.get('sync-button').hidden, true, 'Resolving every conflict still requires explicit session retry');
  assert.match(saveNodes.get('saves-content').children.map((node) => node.textContent || '').join(' '), /Cierra este panel y elige Reintentar/); ++assertions;
  const syncHandler = source.indexOf("$('sync-button').addEventListener('click', async () => {");
  vm.runInContext(source.slice(syncHandler, source.indexOf("$('resolve-confirm').addEventListener", syncHandler)), saveRuntime);
  await saveNodes.get('sync-button').click();
  equal(saveRuntime.mutations, 0, 'A stale sync click after entering product exit sends no request');
  saveRuntime.ui.state.product_flow = { enabled: false };
  saveRuntime.renderSaves();
  equal(saveNodes.get('sync-button').hidden, false, 'Legacy return retains its synchronization control');
  equal(saveNodes.get('sync-button').disabled, false);
  await saveNodes.get('sync-button').click();
  equal(saveRuntime.mutations, 1, 'Legacy synchronization route remains available');

  const dispatchStart = source.indexOf('function xmbPress(');
  const dispatchEnd = source.indexOf('// ---------- pintado ----------', dispatchStart);
  const route = {
    xmbConnectionView: () => ({ product: { visible: true }, waiting: false }),
    productFlowPress: (button) => route.seen.push(button), seen: [],
    xmbBuildSections: () => { throw new Error('Hidden XMB must not be built'); },
  };
  vm.createContext(route);
  vm.runInContext(source.slice(dispatchStart, dispatchEnd), route);
  for (const button of ['left', 'right', 'accept', 'menu', 'select', 'back']) route.xmbPress(button);
  assert.deepEqual(route.seen, ['left', 'right', 'accept', 'menu', 'select', 'back']); ++assertions;
  equal(calls.length, 0);

  // Using PC saves is an explicit session choice, not a change to the
  // prepared-volume marker. Exercise the existing real launch functions.
  const launchState = { ...stable, games: [{ id: 'one', availability: 'ready' }],
    emulator: { available: true }, runtime_prepared: true, can_launch_game: true, can_launch_retro: true,
    preflight: { saves_prepared: false }, saves: { actions: [{ slot: 'changed' }], conflicts: [] },
    product_flow: flow('ready', { choice: 'none', use_pc_saves: true }),
    retro_saves: { rows: [{ ...conflict, rom_id: 'retro-one' }] }, session: {} };
  const launchRuntime = {
    ui: { state: launchState, selected: 'one', busy: false, serviceOffline: false },
    dockConnectionView: connection,
    xmbConnectionView: () => connection(launchRuntime.ui.state),
    $: () => ({ disabled: false }), toast: () => {},
    openSaves: () => { launchRuntime.reviewed += 1; }, reviewed: 0,
    openSettings: () => {}, openTransfer: () => {}, openRetroTransfer: () => {},
    xmbRetroRomMenu: () => { launchRuntime.romMenus += 1; }, romMenus: 0,
    mutate: (route, body) => launchRuntime.calls.push({ route, body: normalize(body) }), calls: [],
  };
  vm.createContext(launchRuntime);
  const launchStart = source.indexOf('function launch()');
  vm.runInContext(source.slice(launchStart, source.indexOf('function transferFeedback(', launchStart)), launchRuntime);
  const retroLaunchStart = source.indexOf('function xmbLaunchRetro(');
  vm.runInContext(source.slice(retroLaunchStart, source.indexOf('function xmbGoto(', retroLaunchStart)), launchRuntime);
  launchRuntime.launch();
  assert.deepEqual(launchRuntime.calls.pop(), { route: '/api/launch', body: { id: 'one' } }); ++assertions;
  const rom = { id: 'retro-one', availability: 'ready', core_available: true, system_name: 'Game Boy Advance' };
  launchRuntime.xmbLaunchRetro(rom);
  assert.deepEqual(launchRuntime.calls.pop(), { route: '/api/retro-launch', body: { id: 'retro-one' } }); ++assertions;
  equal(launchRuntime.ui.state.preflight.saves_prepared, false, 'Using PC saves does not forge the preparation marker');
  launchRuntime.ui.state = { ...launchState, can_launch_game: false, can_launch_retro: false };
  launchRuntime.launch();
  launchRuntime.xmbLaunchRetro(rom);
  equal(launchRuntime.calls.length, 0, 'Both launches still require backend game eligibility');
  launchRuntime.ui.state = { ...launchState, product_flow: flow('ready', { choice: 'none', use_pc_saves: false }) };
  launchRuntime.launch();
  launchRuntime.xmbLaunchRetro(rom);
  equal(launchRuntime.calls.length, 0, 'Missing per-session authorization retains the import guards');
  launchRuntime.ui.state = { ...launchState, product_flow: flow('copying_game', { choice: 'none', use_pc_saves: true }) };
  launchRuntime.launch();
  launchRuntime.xmbLaunchRetro(rom);
  equal(launchRuntime.calls.length, 0, 'Copy phases cannot launch either emulator');
  launchRuntime.ui.state = launchState;
  launchRuntime.xmbLaunchRetro({ ...rom, availability: 'missing' });
  equal(launchRuntime.romMenus, 1, 'Cross on a missing ROM opens its menu');
  equal(launchRuntime.calls.length, 0, 'Cross on a missing ROM never starts copying directly');

  launchRuntime.ui.state = { ...launchState, selected_device: native.selected_device,
    controller_status: native.controller_status };
  launchRuntime.xmbLaunchRetro(rom);
  assert.deepEqual(launchRuntime.calls.pop(), { route: '/api/retro-launch', body: { id: 'retro-one' } }); ++assertions;
  launchRuntime.ui.state = { ...launchRuntime.ui.state, controller_status: { ...native.controller_status, phase: 1 } };
  launchRuntime.xmbLaunchRetro(rom);
  equal(launchRuntime.calls.length, 0, 'STORAGE phase cannot launch a ROM while the menu snapshot lags');

  const inputStart = source.indexOf('function processNativePspFrame(');
  const inputEnd = source.indexOf('// ===================================================================', inputStart);
  const pad = {
    ui: { state: { ...native, product_flow: flow('choosing') }, nativePadConnected: false,
      nativePadHeld: {}, nativePadDirection: '', nativePadAt: 0, busy: false, serviceOffline: false },
    document: { hidden: false, querySelectorAll: () => [] },
    $: () => ({}), setTimeout: () => 0, Date,
    xmbPress: (button) => pad.pressed.push(button), pressed: [],
    request: async () => pad.frame,
    frame: { connected: true, source: 'psp_winmm', accept: true },
  };
  vm.createContext(pad);
  vm.runInContext(source.slice(inputStart, inputEnd), pad);
  await pad.pollNativePspInput();
  equal(pad.pressed.length, 0, 'Initial held Cross only primes the connected controller');
  pad.frame = { ...pad.frame, accept: false };
  await pad.pollNativePspInput();
  pad.ui.state.product_flow = flow('ready');
  pad.frame = { ...pad.frame, accept: true };
  await pad.pollNativePspInput();
  await pad.pollNativePspInput();
  equal(pad.pressed.length, 0, 'Cross held across STORAGE to CONTROLLER never auto-launches a game');
  pad.frame = { ...pad.frame, accept: false };
  await pad.pollNativePspInput();
  pad.frame = { ...pad.frame, accept: true };
  await pad.pollNativePspInput();
  assert.deepEqual(pad.pressed, ['accept']); ++assertions;
  console.log('PSP Dock product session model and input routes: OK (' + assertions + ' assertions)');
}
inputs().catch((error) => { console.error(error); process.exitCode = 1; });
