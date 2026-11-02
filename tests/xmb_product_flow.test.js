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
  synthetic_storage: false, product_id: 0x01cd, protocol_version: 3, firmware_revision: 0x0303,
  capabilities: 7, build_id: 0x03030001,
  usb_revision: 0x0100, host_confirmed: true, phase: 2 }, product_flow: flow('ready') };
equal(connection(native).canPlay, true, 'Verified native Dock can keep its Sony storage enumerated');
equal(connection(native).nativeController, true);
equal(connection({ ...native, controller_status: { ...native.controller_status,
  firmware_revision: 0x0306, build_id: 0x03060001, capabilities: 15 } }).canPlay,
  true, '0306 uses its exact identity with the coordinated journey');
equal(connection({ product_flow: { enabled: false } }).title, 'Bienvenido a PSP Dock');
const restoredClose = { ...native, product_flow: flow('attention', {
  startup_recovery: true, purpose: 'cancel', resume_phase: 'finalizing',
  can_retry: true, recovery_required: true, connection: { phase: 'closing' },
  attention_reason: 'Retained failed close',
}) };
equal(product(restoredClose).title, 'Bienvenido a PSP Dock');
equal(product(restoredClose).canRetry, true, 'A real restored finalization snapshot exposes Retry');
equal(product(restoredClose).retryLabel, 'Completar cierre anterior');
equal(product(restoredClose).canChoose || product(restoredClose).canClose, false);
equal(product(restoredClose).diagnostics, 'Retained failed close', 'Welcome does not erase recovery evidence');
equal(product({ ...restoredClose, product_flow: { ...restoredClose.product_flow,
  startup_recovery: false } }).title, 'Cerrando la conexión con la PSP');
equal(connection({ ...native, controller_status: { ...native.controller_status, firmware_revision: 0x0304, build_id: 0x03040001 } }).canPlay,
  true, 'The explicitly supported diagnostic firmware keeps the same product journey');
equal(connection({ ...native, controller_status: { ...native.controller_status,
  firmware_revision: 0x0305, build_id: 0x03050001, capabilities: 15 } }).canPlay,
  true, '0305 requires its shutdown capability as well as its exact build');
equal(connection({ ...native, controller_status: { ...native.controller_status,
  firmware_revision: 0x0305, build_id: 0x03050001, capabilities: 7 } }).canPlay, false);
equal(connection({ ...native, controller_status: { ...native.controller_status, capabilities: 15 } }).canPlay,
  false, 'New capabilities cannot be assigned to an older firmware');
for (const changes of [{ product_id: 0x01cc }, { firmware_revision: 0x0207 }, { firmware_revision: 0x0300 },
  { firmware_revision: 0x0301 }, { firmware_revision: 0x0302 }, { firmware_revision: 0x0305 }, { firmware_revision: undefined, revision: 0x0303 }, { protocol_version: 2 },
  { protocol_version: undefined }, { host_confirmed: false }, { host_confirmed: undefined },
  { capabilities: undefined }, { capabilities: 3 }, { build_id: undefined }, { build_id: 0x03040001 },
  { phase: 1 }, { synthetic_storage: true }, { can_play: false }]) {
  equal(connection({ ...native, controller_status: { ...native.controller_status, ...changes } }).canPlay,
    false, 'Native identity, controller phase and host eligibility must all agree');
}
equal(connection({ ...native, controller_status: { ...native.controller_status, usb_revision: 0x0302 } }).canPlay,
  true, 'The separately verified USB descriptor revision is not the product firmware revision');
equal(connection({ ...native, product_flow: { enabled: false } }).canPlay, false,
  'A native controller without the coordinated journey is insufficient');
equal(product({ ...native, product_flow: flow('finalizing', { can_close: true }) }).canClose, false,
  'Finalization never advertises safe disconnect or permits the close button');
const failedEntry = { ...native, product_flow: flow('attention', {
  can_cancel: true, can_close_app: true, can_retry: false, operation_active: false, entry_completed: false,
}) };
equal(product(failedEntry).canCancel, true, 'Failed read-only entry offers normal disconnect');
equal(product(failedEntry).canDismiss, true, 'A failed entry always has an app-close action');
equal(product(failedEntry).canClose, false, 'App close never claims safe hardware removal');
equal(product({ ...failedEntry, product_flow: { ...failedEntry.product_flow,
  operation_active: true } }).canCancel, false, 'A running operation cannot be cancelled by an idle-only action');
equal(product({ ...failedEntry, session: { running:true } }).canDismiss, false,
  'The close action cannot bypass an active emulator');
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

for (const purpose of ['exit', 'cancel']) {
  const closeOnly = { ...native, product_flow: flow('attention', {
    purpose, resume_phase: 'finalizing', can_retry: true, can_choose: true,
    can_cancel: true, can_review_saves: true, can_close_app: true,
    connection: { phase: 'closing', retryable: true },
  }) };
  const closingView = product(closeOnly);
  equal(closingView.canRetry, true, 'An interrupted native close offers only its finalization retry');
  equal(closingView.canChoose || closingView.canReview || closingView.canCancel || closingView.canDismiss, false);
  equal(closingView.showRefresh, false, 'The product panel exposes only finalization retry during close');
  assert.match(closingView.title, /Cerrando la conexión/); ++assertions;
  equal(connection(closeOnly).kind, 'closing');
  equal(connection(closeOnly).screen, 'product');
  equal(connection(closeOnly).canPlay, false, 'Stale native controller facts cannot reopen the menu');
  equal(connection(closeOnly).canPrepareStorage, false);
  equal(connection(closeOnly).canRefresh, false, 'Connection refresh is not a second close path');
  equal(product(closeOnly, { busy: true }).canRetry, false);
  equal(product(closeOnly, { serviceOffline: true }).canRetry, false);
  equal(product({ ...closeOnly, product_flow: { ...closeOnly.product_flow, resume_phase: 'waiting_storage_return' } }).canRetry,
    false, 'A close receipt cannot authorize another save-return callback');
  equal(product({ ...closeOnly, product_flow: { ...closeOnly.product_flow, purpose: 'initial' } }).canRetry, false);
}

const connectionState = (phase, fields = {}) => ({
  controller_status: { mode: 'await_native', connected: false, can_play: false },
  product_flow: { enabled: false, phase: 'waiting_psp', connection: { phase, ...fields } },
});
for (const phase of ['identifying', 'waiting_storage', 'blocked']) {
  const state = connectionState(phase, { message: 'Estamos comprobando la conexión.', hint: 'Mantén la PSP conectada.' });
  const waiting = connection(state);
  equal(waiting.screen, 'waiting', phase + ' has a visible connection screen before a verified session');
  equal(waiting.kind, phase);
  equal(waiting.canPlay, false);
  equal(waiting.product.enabled, false, 'Presentation cannot create a session');
  equal(waiting.canPrepareStorage, false, 'A partial native connection never falls into manual storage setup');
  equal(waiting.showSteps, false, 'An already detected PSP is not told to connect again');
  equal(waiting.working, phase !== 'blocked');
  assert.doesNotMatch(waiting.title + waiting.detail, /Sincronizando|0302|PDK2|HID/); ++assertions;
}
const blockedConnection = connectionState('blocked', {
  message: 'No se ha podido reconocer esta versión de PSP Dock.',
  hint: 'Actualiza PSP Dock en la consola y vuelve a abrirla.',
  diagnostics: 'descriptor 0100, firmware 0301; PDK2 rejected', retryable: true,
});
equal(connection(blockedConnection).detail, blockedConnection.product_flow.connection.message);
equal(connection(blockedConnection).hint, blockedConnection.product_flow.connection.hint);
equal(connection(blockedConnection).diagnostics, blockedConnection.product_flow.connection.diagnostics);
equal(connection(blockedConnection).canRefresh, true);
assert.doesNotMatch(connection(blockedConnection).title + connection(blockedConnection).detail + connection(blockedConnection).hint,
  /0100|0301|PDK2/); ++assertions;
const legacyFailure = connection({ controller_status: { mode: 'await_hid', connected: false }, error: 'USB 0100 != 0301' });
equal(legacyFailure.kind, 'blocked', 'A discovery failure cannot disappear into the hidden legacy model');
equal(legacyFailure.diagnostics, 'USB 0100 != 0301');
assert.doesNotMatch(legacyFailure.detail, /0100|0301/); ++assertions;
equal(connection({ ...storage, product_flow: { enabled: false, connection: { phase: 'waiting' } } },
  { storageSetupRequested: true }).canPrepareStorage, true, 'No native device preserves the existing manual storage route');
const interruptedReady = { ...native, product_flow: flow('ready', { connection: { phase: 'blocked', message: 'Se perdió la conexión.' } }) };
equal(connection(interruptedReady).screen, 'product', 'A blocked link cannot expose a stale ready menu');
equal(connection(interruptedReady).canPlay, false);
equal(product(interruptedReady).blocking, true);
equal(product({ ...choose, product_flow: flow('choosing', { connection: { phase: 'blocked' } }) }).canChoose,
  false, 'A stale choice cannot write through a rejected connection');
const attentionDetail = product({ ...storage, product_flow: flow('attention', {
  can_review_saves: true, attention_reason: 'WinError 123; C:\\private\\slot.dat' }) });
assert.doesNotMatch(attentionDetail.message, /WinError|private|123/); ++assertions;
assert.match(attentionDetail.message, /guardados.*elección/); ++assertions;
assert.match(attentionDetail.diagnostics, /WinError 123/); ++assertions;
const canceled = product({ ...storage, product_flow: flow('safe_to_disconnect', { purpose: 'cancel', can_close: true }) });
assert.match(canceled.message, /Conexión cerrada/); ++assertions;
assert.doesNotMatch(canceled.message + canceled.kicker, /guardados.*comprobados|sincronización terminada/i); ++assertions;

const measuredProgress = (stage, overrides = {}) => ({ active: true, operation_id: 'measured-operation', stage,
  mode: 'determinate', unit: 'bytes', completed: 25, total: 100, label: 'Copiando el juego', current_item: 'A game', ...overrides });
let measured = product({ ...storage, product_flow: flow('copying_game', { progress: measuredProgress('games') }) });
equal(measured.showProgress, true);
equal(measured.progress.total, 100);
equal(measured.progress.completed, 25);
equal(measured.progress.percent, 25);
measured = product({ ...storage, product_flow: flow('copying_game', { progress: measuredProgress('games', { completed: 300 }) }) });
equal(measured.progress.completed, 100, 'Inconsistent totals cannot render >100 percent');
for (const fields of [{ total: null }, { total: 0 }, { total: -1 }, { total: '100' }, { total: Infinity },
  { completed: -1 }, { unit: 'guessed' }, { mode: 'indeterminate' }, { active: false }, { operation_id: '' },
  { stage: 'finalizing' }]) {
  measured = product({ ...storage, product_flow: flow('copying_game', { progress: measuredProgress('games', fields) }) });
  equal(measured.progress.determinate, false, 'Unknown, stale or inactive totals never produce a percentage');
  equal(measured.progress.percent, null);
}
const pendingMeasure = product({ ...storage, product_flow: flow('copying_game'),
  transfer_status: { active: true, total_bytes: 100, copied_bytes: 100 } });
equal(pendingMeasure.showProgress, true, 'A working phase remains visibly indeterminate before measurements arrive');
equal(pendingMeasure.progress.determinate, false, 'An unrelated legacy transfer cannot provide the session percentage');
for (const phase of ['syncing_in', 'syncing_out']) {
  measured = product({ ...storage, product_flow: flow(phase, { progress: measuredProgress('saves', {
    unit: 'items', completed: 2, total: 7, label: 'Comprobando guardados' }) }) });
  equal(measured.progress.determinate, true, phase + ' can report verified saves from a real plan');
  equal(measured.progress.completed, 2);
  equal(measured.progress.total, 7);
}
measured = product({ ...storage, product_flow: flow('copying_game', { progress: measuredProgress('verifying', {
  mode: 'indeterminate', total: null, label: 'Verificando la copia' }) }) });
equal(measured.progress.percent, null, 'Verification never inherits 100% from the preceding byte copy');
equal(measured.progress.label, 'Verificando la copia');
equal(product({ ...storage, product_flow: flow('finalizing') }).showProgress, true);
equal(product({ ...storage, product_flow: flow('finalizing') }).progress.determinate, false);
equal(product({ ...storage, product_flow: flow('inventory') }).showProgress, true);
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
  const closeNodes = new Map();
  const closing = {
    ui: { state: blockedConnection, appClosing: false, appClosed: false, appClientId: 'fixture-client' },
    xmbConnectionView: () => connection(closing.ui.state), xmbRender: () => {},
    $: (id) => { if (!closeNodes.has(id)) closeNodes.set(id, {}); return closeNodes.get(id); },
    request: async (route, body) => { closing.calls.push({ route, body: normalize(body) }); return closing.reply; }, calls: [], reply: {},
    clearTimeout: () => { closing.cleared += 1; }, cleared: 0,
    window: { close: () => { closing.closed += 1; } }, closed: 0,
    appHeartbeat: async () => { closing.heartbeats += 1; }, heartbeats: 0,
  };
  vm.createContext(closing);
  const closeStart = source.indexOf('async function closeDockApp()');
  vm.runInContext(source.slice(closeStart, source.indexOf('function appPageLeaving()', closeStart)), closing);
  await closing.closeDockApp();
  assert.deepEqual(closing.calls, [{ route: '/api/app/close', body: { client_id: 'fixture-client', reason: 'button' } }]); ++assertions;
  equal(closing.closed, 1, 'Closing before a verified session closes the window normally');
  await closing.closeDockApp();
  equal(closing.calls.length, 1, 'Repeated close cannot start another operation');
  closing.ui = { ...closing.ui, appClosed: false, state: { ...native, product_flow: flow('copying_game') } };
  await closing.closeDockApp();
  equal(closing.calls.length, 1, 'The preconnection close button cannot bypass an active copy');
  closing.ui.state = failedEntry;
  closing.reply = { close_pending: true,
    product_flow: { ...failedEntry.product_flow, window_close_requested: true } };
  const clearedBeforePending = closing.cleared;
  await closing.closeDockApp();
  equal(closing.calls.length, 2, 'Attention requests the worker to finish the connection');
  equal(closing.calls[1].route, '/api/app/close');
  equal(closing.closed, 1, 'A pending native close keeps the same window open');
  equal(closing.ui.appClosed, false, 'Queued close does not mark the app closed');
  equal(closing.ui.appClosing, false, 'The response releases the click guard for normal state refresh');
  equal(closing.cleared, clearedBeforePending, 'Queued close retains the heartbeat timer');
  equal(closing.heartbeats, 1, 'Heartbeat continues while the worker closes the connection');
  equal(closing.ui.state.product_flow.window_close_requested, true, 'The response displays closing immediately');
  equal(product(closing.ui.state).closePending, true, 'The close action is disabled while its request is pending');
  closing.reply = {};
  closing.ui.state = { ...native, product_flow: flow('safe_to_disconnect', { can_close: true }) };
  await closing.closeDockApp();
  equal(closing.closed, 2, 'Verified completion permits the window to close');
  equal(closing.ui.appClosed, true);

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
