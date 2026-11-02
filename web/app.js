'use strict';

const $ = (id) => document.getElementById(id);
const ui = {
  state: null, selected: null, token: null, busy: false, serviceOffline: false,
  signature: '', savesSignature: '', devicesSignature: '', transfer: [], resolve: null,
  toastTimer: null, gamepadAt: 0, gamepadDirection: '', gamepadHeld: {},
  gamepadId: '', skippedCopies: new Set(), operation: '',
  transferInFlight: false, transferRun: 0, transferPollTimer: null,
  transferStatus: null, transferCancelRequested: false, transferCancelling: false,
  transferFinished: false, transferKind: 'psp',
  nativePadConnected: false, nativePadLastAt: 0,
  nativePadHeld: {}, nativePadDirection: '', nativePadAt: 0,
  nativePadFlowKey: '',
  graphicsGame: null,
  textureProgress: null,
  appClosing: false, appClosed: false,
  appClientId: '', appHeartbeatTimer: null, appHeartbeatPending: false, appPageHidden: false,
  storageSetupRequested: false,
  productFlowKey: '', productRequestError: '', connectionRequestError: '',
};
const bytes = (value) => {
  const n = Number(value) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1).replace('.', ',')} KB`;
  return n >= 1073741824 ? `${(n / 1073741824).toFixed(2).replace('.', ',')} GB` : `${Math.round(n / 1048576)} MB`;
};
function el(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}
function toast(message) {
  clearTimeout(ui.toastTimer);
  $('toast').textContent = message;
  $('toast').hidden = false;
  ui.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5500);
}
async function request(path, body) {
  const options = { cache: 'no-store' };
  if (body !== undefined) {
    if (!ui.token) {
      const response = await fetch('/api/token', { cache: 'no-store' });
      if (!response.ok) throw new Error('No se pudo iniciar una sesión local segura.');
      ui.token = (await response.json()).token;
    }
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json', 'X-Dock-Token': ui.token };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let result;
  try { result = await response.json(); } catch { throw new Error('PSP Dock no ha devuelto una respuesta válida.'); }
  if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
  return result;
}
async function appHeartbeat() {
  if (ui.appClosing || ui.appClosed || ui.appPageHidden || ui.appHeartbeatPending) return;
  ui.appHeartbeatPending = true;
  clearTimeout(ui.appHeartbeatTimer);
  try {
    await request('/api/app/heartbeat', { client_id: ui.appClientId });
  } catch {
    // /api/state supplies the visible connection error. A transient
    // missed heartbeat must not close a window or invent device state.
  } finally {
    ui.appHeartbeatPending = false;
    if (!ui.appClosing && !ui.appClosed && !ui.appPageHidden)
      ui.appHeartbeatTimer = setTimeout(appHeartbeat, 2000);
  }
}
async function closeDockApp() {
  const connection = xmbConnectionView();
  if (!(connection.waiting || connection.product?.canClose) || ui.appClosing || ui.appClosed) return;
  ui.appClosing = true;
  ui.connectionRequestError = '';
  $('dock-connect-error').hidden = true;
  xmbRender();
  try {
    await request('/api/app/close', { client_id: ui.appClientId, reason: 'button' });
    ui.appClosed = true;
    clearTimeout(ui.appHeartbeatTimer);
    xmbRender();
    // Chrome app windows can close here; an ordinary browser tab may
    // refuse. The closed screen remains useful in that case.
    window.close();
  } catch (error) {
    if (connection.product?.visible) ui.productRequestError = error.message;
    ui.connectionRequestError = error.message;
    $('dock-connect-error').textContent = 'No se pudo cerrar la aplicación. Puedes intentarlo de nuevo.';
    $('dock-connect-error').hidden = false;
  } finally {
    ui.appClosing = false;
    xmbRender();
    if (!ui.appClosed) void appHeartbeat();
  }
}
function appPageLeaving() {
  ui.appPageHidden = true;
  clearTimeout(ui.appHeartbeatTimer);
  if (ui.appClosed || !ui.token || !ui.appClientId) return;
  // sendBeacon cannot set X-Dock-Token. keepalive retains the same
  // origin/token checks; the server's lease covers an interrupted send.
  void fetch('/api/app/close', {
    method: 'POST', keepalive: true, cache: 'no-store',
    headers: { 'Content-Type': 'application/json', 'X-Dock-Token': ui.token },
    body: JSON.stringify({ client_id: ui.appClientId, reason: 'pagehide' }),
  }).catch(() => {});
}
async function mutate(path, body, success) {
  if (ui.busy) return false;
  if (ui.serviceOffline) { toast('Espera a que PSP Dock recupere la conexión antes de continuar.'); return false; }
  if (ui.state?.recovery_required) { toast('Sesión interrumpida: revisa PPSSPP y la recuperación antes de continuar.'); return false; }
  ui.busy = true;
  ui.operation = path;
  document.body.classList.add('busy');
  document.body.setAttribute('aria-busy', 'true');
  document.querySelectorAll('.mutation-error').forEach((node) => node.remove());
  if (ui.state) {
    renderPreflight();
    if ($('saves-dialog').open) renderSaves();
  }
  try {
    await request(path, body);
    await refresh();
    const message = typeof success === 'function' ? success(ui.state) : success;
    if (message) toast(message);
    return true;
  } catch (error) {
    if (path === '/api/session-choice' || path === '/api/session-retry') ui.productRequestError = error.message;
    if (path === '/api/prepare-ppsspp' || path === '/api/transfer' ||
        path === '/api/retro-transfer' || path === '/api/retro-save-sync') await refresh();
    const cancelledTransfer = (path === '/api/transfer' || path === '/api/retro-transfer') &&
      (ui.transferStatus?.phase === 'cancelled' || /cancelad|cancelled/i.test(error.message));
    if (cancelledTransfer) {
      toast('Copia cancelada. Los juegos terminados y los archivos de la PSP se han conservado.');
      return false;
    }
    toast(error.message);
    const openDialog = Array.from(document.querySelectorAll('dialog[open]')).at(-1);
    if (openDialog) {
      const notice = el('p', 'form-error mutation-error', error.message);
      notice.setAttribute('role', 'alert');
      openDialog.querySelector('.dialog-actions').before(notice);
    }
    return false;
  } finally {
    ui.busy = false;
    ui.operation = '';
    document.body.classList.remove('busy');
    document.body.removeAttribute('aria-busy');
    // Refresh completed while busy; restore controls and status messages now.
    if (ui.state) render();
  }
}
async function refresh() {
  if (ui.appClosing || ui.appClosed) return;
  try {
    const state = await request('/api/state');
    if (state.retro_install) {
      try { state.retro_install.progress = await request('/api/retro-install-status'); }
      catch { /* XMB still works when an optional status request fails. */ }
    }
    ui.state = state;
    ui.serviceOffline = false;
    $('error').hidden = !state.error && !state.recovery_required;
    $('error').textContent = state.recovery_required ? 'Sesión interrumpida: revisa PPSSPP y la recuperación antes de continuar.' : state.error || '';
    render();
  } catch (error) {
    ui.serviceOffline = true;
    $('error').hidden = false;
    $('error').textContent = `PSP Dock no responde. ${error.message}`;
    if (ui.state) render();
    else {
      $('device-status').classList.add('offline');
      $('device-label').textContent = 'La app no responde';
      $('device-role').textContent = 'RECONEXIÓN PENDIENTE';
      xmbRender();
    }
  }
}
function renderDevices() {
  const state = ui.state;
  const devices = state.devices || [];
  const manualHid = state.controller_status?.connected === true;
  const signature = JSON.stringify([devices, state.selected_device, state.demo, state.scanning, state.session,
    state.recovery_required, state.controller_status, state.preflight?.saves_prepared, ui.serviceOffline]);
  if (signature === ui.devicesSignature) return;
  ui.devicesSignature = signature;
  const status = $('device-status');
  status.classList.toggle('connected', (!!state.selected_device && !state.demo || manualHid) && !ui.serviceOffline);
  status.classList.toggle('offline', ui.serviceOffline);
  status.classList.toggle('attention', !!(state.recovery_required || state.session?.exit_requested ||
    state.session?.pending_return && !manualHid));
  const label = $('device-label');
  const role = $('device-role');
  label.replaceChildren();
  if (ui.serviceOffline) {
    label.textContent = 'La app no responde';
    role.textContent = 'NO SE PUEDEN REALIZAR OPERACIONES';
  } else if (state.scanning) {
    label.textContent = 'Buscando PSP…';
    role.textContent = 'LEYENDO EL ESTADO DEL DOCK';
  } else if (manualHid && state.controller_status.mode === 'msc_diagnostic') {
    label.textContent = 'PSP Dock · modo diagnóstico';
    role.textContent = 'DISCO DE PRUEBA DE 512 KiB · NAVEGACIÓN';
  } else if (manualHid && state.product_flow?.enabled === true) {
    label.textContent = 'PSP Dock conectada';
    role.textContent = state.session?.exit_requested ? 'FINALIZANDO LA SESIÓN' : 'MODO MANDO';
  } else if (manualHid) {
    label.textContent = state.session?.exit_requested ? 'Salida del Dock solicitada' : 'Mando PSP HID detectado';
    role.textContent = state.session?.exit_requested ? 'VOLVER A ALMACENAMIENTO MANUALMENTE' : 'MODO MANDO MANUAL · PDK1';
  } else if (devices.length > 1) {
    const select = el('select');
    select.setAttribute('aria-label', 'Seleccionar PSP');
    devices.forEach((device) => {
      const option = el('option', '', device.label || device.root);
      option.value = device.root;
      option.selected = device.root === state.selected_device;
      select.append(option);
    });
    select.addEventListener('change', () => mutate('/api/select-device', { root: select.value }, 'Dispositivo seleccionado.'));
    label.append(select);
    role.textContent = state.selected_device ? 'ALMACENAMIENTO SELECCIONADO' : 'ELIGE UNA UNIDAD';
  } else if (state.demo) label.textContent = 'Demostración · sin PSP real';
  else if (state.selected_device) label.textContent = `${devices.find((d) => d.root === state.selected_device)?.label || 'PSP'} conectada`;
  else if (state.session?.pending_return) label.textContent = 'Devolución pendiente';
  else if (state.preflight?.saves_prepared) label.textContent = 'Esperando mando PSP HID';
  else label.textContent = 'PSP sin conectar';
  if (!ui.serviceOffline && !state.scanning && devices.length <= 1 && !manualHid) {
    role.textContent = state.demo ? 'MUESTRA SINTÉTICA' : state.recovery_required ? 'RECUPERACIÓN NECESARIA' :
      state.session?.running ? 'PPSSPP EN EJECUCIÓN' : state.session?.pending_return ? 'PARTIDAS POR DEVOLVER' :
      state.selected_device ? 'ALMACENAMIENTO USB · NO HID' :
        state.preflight?.saves_prepared ? 'CAMBIO USB MANUAL PENDIENTE' : 'SIN ALMACENAMIENTO USB';
  }
}
function render() {
  const state = ui.state;
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.visible) {
    if ($('saves-dialog').open) renderSaves();
    xmbRender();
    return;
  }
  const games = state.games || [];
  renderDevices();
  renderPreflight();
  $('demo-note').hidden = !state.demo;
  $('empty-view').hidden = games.length > 0;
  $('library').hidden = !games.length;
  $('library-eyebrow').textContent = state.controller_status?.connected ? 'PSP HID · ELIGE TU JUEGO' :
    state.library_source === 'cached_manifest' ? 'BIBLIOTECA RECORDADA EN EL PC' : 'ELIGE TU PRÓXIMA PARTIDA';
  $('empty-title').textContent = state.selected_device ? 'Tu biblioteca empieza aquí.' : 'La partida continúa aquí.';
  $('empty-description').replaceChildren();
  $('empty-description').textContent = state.scanning ? 'Comprobando unidades y biblioteca…' :
    state.selected_device ? (state.retro?.emulators?.length ?
      'No hay juegos PSP en la carpeta ISO. Tus emuladores y ROMs aparecen en la sección Retro del XMB.' :
      'No se han encontrado juegos. Comprueba la carpeta ISO de la PSP y la biblioteca configurada.') :
      state.controller_status?.connected ? 'El mando HID está detectado, pero no hay juegos locales preparados. Vuelve al almacenamiento USB para revisar la biblioteca.' :
      'Conecta tu PSP por USB y activa el modo USB. Tu biblioteca aparecerá en este espacio.';
  $('library-count').textContent = `${games.length} ${games.length === 1 ? 'juego' : 'juegos'}`;
  if (!games.some((game) => game.id === ui.selected)) ui.selected = games[0]?.id || null;
  const signature = JSON.stringify([games, state.demo]);
  if (signature !== ui.signature) {
    ui.signature = signature;
    $('game-track').replaceChildren();
    games.forEach((game, i) => {
      const card = el('button', 'game-card');
      card.type = 'button';
      card.dataset.id = game.id;
      card.dataset.availability = game.availability;
      const availabilityLabel = { ready: 'Disponible en el PC', missing: 'Pendiente de copiar', different: 'Versión diferente', checking: 'Esperando inventario de PSP' };
      card.setAttribute('aria-label', `${game.title}. ${availabilityLabel[game.availability] || 'Estado desconocido'}.`);
      card.title = game.title;
      card.style.setProperty('--cover-bg', ['#274e6b', '#196d72', '#555a6e', '#446c66', '#2d566d', '#6f5e53'][i % 6]);
      const media = el('span', 'cover-media');
      const fallback = el('span', 'cover-fallback');
      fallback.setAttribute('aria-hidden', 'true');
      fallback.append(el('span', 'fallback-symbol', 'PSP'), el('span', 'fallback-label', game.format || 'JUEGO'));
      const art = el('img', 'game-cover');
      art.alt = '';
      art.loading = 'lazy';
      art.decoding = 'async';
      art.addEventListener('load', () => media.classList.add('has-art'), { once: true });
      art.addEventListener('error', () => { art.remove(); media.classList.add('no-art'); }, { once: true });
      art.src = '/api/icon/' + encodeURIComponent(game.id);
      media.append(fallback, art);
      const flag = el('span', 'cover-availability', game.availability === 'ready' ? 'EN PC' : game.availability === 'missing' ? 'COPIAR' : game.availability === 'checking' ? 'COMPROBAR' : 'REVISAR');
      card.append(
        media, el('span', 'cover-band', state.demo ? 'DEMO · PSP' : 'PSP · BIBLIOTECA'),
        el('span', 'cover-index', `VOL. ${String(i + 1).padStart(2, '0')}`),
        flag, el('span', 'cover-title', game.title),
        el('span', 'cover-type', `${game.format || 'JUEGO'} / BIBLIOTECA LOCAL`)
      );
      card.addEventListener('click', () => selectGame(game.id));
      card.addEventListener('dblclick', () => { selectGame(game.id); launch(); });
      $('game-track').append(card);
    });
  }
  renderSelected(false);
  if ($('transfer-dialog').open && ui.transferFinished && !ui.transferInFlight) {
    // A failed/cancelled transfer can have committed earlier games. As the
    // PC catalog refreshes, remove verified games from the retry selection.
    const catalog = ui.transferKind === 'retro' ? state.retro?.roms || [] : games;
    const missing = ui.transfer.filter((game) =>
      catalog.some((current) => current.id === game.id && current.availability === 'missing'));
    if (missing.length !== ui.transfer.length) {
      ui.transfer = missing;
      renderTransferSelection();
      renderTransferControls();
    }
  }
  const pending = games.filter((game) => game.availability === 'missing');
  $('transfer-all').hidden = !pending.length;
  $('transfer-all').disabled = !state.selected_device || state.recovery_required || state.session?.running || ui.serviceOffline;
  $('session-status').textContent = ui.serviceOffline ? 'PSP Dock no responde · las operaciones están pausadas' :
    state.recovery_required ? 'Sesión interrumpida · recupera PPSSPP antes de continuar' :
      state.session?.running ? `● ${state.session?.launcher === 'retroarch' ? 'RetroArch' : 'PPSSPP'} en ejecución · vuelve al Dock al terminar` :
        state.session?.pending_return && state.session?.exit_requested ?
          'Salida solicitada · devuelve manualmente el almacenamiento para revisar las partidas' :
        state.session?.pending_return && state.controller_status?.connected ?
          'De vuelta en el XMB · puedes jugar a otro título o elegir Salir del Dock' :
        state.session?.pending_return ? 'Partida terminada · vuelve a almacenamiento para devolver los guardados' :
          state.controller_status?.connected ?
            state.can_launch_retro && !games.length ? 'PSP HID PDK1 conectado · tus ROMs retro están preparadas para RetroArch' :
            state.runtime_prepared !== true ? 'Mando HID detectado · PPSSPP aún no está preparado en el PC' :
              state.can_launch_game ? 'Mando PSP HID PDK1 conectado · puedes abrir tus juegos locales en PPSSPP' :
                'Mando HID detectado · aún queda una verificación pendiente' :
            ui.operation === '/api/sync' ? 'Sincronizando partidas y preparando PPSSPP automáticamente…' :
            state.preflight?.saves_prepared ? 'Partidas preparadas · activa manualmente el modo mando HID en la PSP' :
              'Elige un juego y revisa primero las partidas.';
  const count = (state.saves?.actions?.length || 0) + (state.saves?.conflicts?.length || 0);
  $('save-count').hidden = !count;
  $('save-count').textContent = count;
  if ($('saves-dialog').open) renderSaves();
  xmbRender();
}
function preflightKey(state) {
  return JSON.stringify([
    state.selected_device,
    (state.games || []).filter(g => g.availability === 'missing').map(g => [g.id, g.size, g.modified_ns]),
  ]);
}
function renderRuntimeReadiness(state, prepared) {
  const panel = $('runtime-readiness');
  const retroOnly = !state.games?.length && !!state.retro?.emulators?.length;
  const known = typeof state.runtime_prepared === 'boolean';
  panel.hidden = retroOnly || state.demo || !known || !(state.selected_device || prepared ||
    state.controller_status?.connected || state.session?.pending_return);
  if (panel.hidden) return;

  const preparing = ui.operation === '/api/prepare-ppsspp';
  const syncing = ui.operation === '/api/sync' && !state.session?.pending_return;
  const ready = state.runtime_prepared === true;
  const failure = typeof state.runtime_error === 'string' ? state.runtime_error.trim() : '';
  panel.classList.toggle('ready', ready && !preparing && !syncing);
  panel.classList.toggle('working', preparing || syncing);
  panel.classList.toggle('needs-attention', !ready && !!failure && !preparing && !syncing);
  const label = $('runtime-readiness-label');
  if (preparing) label.textContent = 'Preparando PPSSPP en el PC…';
  else if (syncing) label.textContent = 'Sincronizando las partidas y preparando PPSSPP automáticamente en el PC…';
  else if (ready) label.textContent = 'PPSSPP preparado en el PC. Ya puedes elegir un juego compatible.';
  else if (state.preflight?.ready_games === 0) label.textContent =
    'Para preparar PPSSPP en el PC, copia antes al menos un juego verificado. Las partidas sincronizadas se conservan.';
  else if (failure) label.textContent = prepared ?
    `PPSSPP pendiente: ${failure}. Las partidas preparadas se conservan; puedes reintentar desde el almacenamiento USB.` :
    `PPSSPP pendiente: ${failure}. Primero revisa y prepara las partidas de la PSP.`;
  else if (!state.emulator?.available) label.textContent = 'PPSSPP no está configurado. Indica su ejecutable en Ajustes para preparar el PC.';
  else if (!prepared) label.textContent = 'PPSSPP se preparará automáticamente cuando termines de sincronizar las partidas.';
  else if (!state.selected_device) label.textContent = 'PPSSPP pendiente en el PC. Vuelve al almacenamiento USB para prepararlo antes de activar HID.';
  else label.textContent = 'PPSSPP pendiente en el PC. Usa «Preparar PPSSPP» para reintentar sin repetir la sincronización.';
}
function renderPreflight() {
  const state = ui.state;
  const plan = state?.preflight;
  const panel = $('dock-preflight');
  if (!plan) { panel.hidden = true; return; }
  const disconnected = !state.selected_device && !state.demo;
  const retroOnly = !state.games?.length && !!state.retro?.emulators?.length;
  const retroRoms = state.retro?.roms || [];
  const retroReady = retroRoms.filter(rom => rom.availability === 'ready').length;
  const retroMissing = retroRoms.filter(rom => rom.availability === 'missing').length;
  const retroSavePending = state.selected_device ? Number(state.retro_saves?.pending || 0) : 0;
  const skipped = state.copy_policy === 'skip_missing' ||
    ui.skippedCopies.has(preflightKey(state));
  const prepared = plan.saves_prepared === true || state.saves_prepared === true;
  // An explicit Exit is valid even if the user only navigated the XMB
  // without launching a game. Persistent HID should never trap them in
  // a menu merely because there are no changed saves to return.
  const returnPending = !!(state.session?.pending_return ||
    state.session?.exit_requested);
  const playing = !!state.session?.running;
  const diagnostic = state.controller_status?.mode === 'msc_diagnostic';
  const product = dockProductFlowView(state, { serviceOffline: ui.serviceOffline });
  const inHidXmb = !playing && !state.session?.exit_requested &&
    state.controller_status?.connected === true && !diagnostic &&
    (prepared || product.enabled && product.phase === 'ready');
  panel.hidden = disconnected && !state.games?.length && !retroOnly && !returnPending && !state.recovery_required && !state.controller_status?.connected;
  renderRuntimeReadiness(state, prepared);
  $('preflight-summary').textContent = [
    plan.ready_games + ' en PC',
    plan.missing_games + ' por copiar',
    ...(retroOnly ? [`${retroReady} ROMs retro en PC`, `${retroMissing} ROMs por copiar`] : []),
    ...(retroSavePending ? [`${retroSavePending} guardados retro por revisar`] : []),
    plan.connected || state.demo ?
      plan.save_actions + ' guardados pendientes · ' + plan.save_conflicts + ' conflictos' :
      returnPending ? 'Partidas por comparar al volver al almacenamiento' : 'Partidas sin comparar (PSP desconectada)',
  ].join(' · ');
  $('preflight-kicker').textContent = state.demo ? 'MODO DEMOSTRACIÓN' :
    plan.connected ? 'ALMACENAMIENTO USB DETECTADO' : 'PSP DOCK · ESTADO DEL DISPOSITIVO';
  let title = 'Preparando el inventario de tu PSP';
  let description = plan.reason || 'Comprobando juegos y guardados.';
  if (ui.serviceOffline) {
    title = 'PSP Dock no responde';
    description = 'Las operaciones están pausadas hasta recuperar la conexión con PSP Dock.';
  } else if (state.demo) {
    title = 'Demostración del modo Dock';
    description = 'Recorre la biblioteca sintética. La demo no escribe partidas reales.';
  } else if (state.recovery_required) {
    title = 'Necesitas recuperar una sesión anterior';
    description = 'Revisa si PPSSPP sigue abierto y completa la recuperación antes de copiar o sincronizar archivos.';
  } else if (playing) {
    title = 'La partida está en marcha';
    description = (state.session?.launcher === 'retroarch' ? 'RetroArch' : 'PPSSPP') + ' está ejecutándose. Cuando cierres el juego volverás a este XMB para elegir otro o salir del Dock.';
  } else if (inHidXmb) {
    title = state.session?.pending_return ?
      'De vuelta en el XMB · ¿otra partida?' :
      'PSP Dock activo · elige cuándo jugar';
    description = product.enabled ?
      'La PSP está en modo mando. Puedes recorrer tu biblioteca, copiar un juego pendiente o salir para devolver tus guardados.' : retroOnly ?
      'Mando PSP activo: elige una ROM verificada en Retro. RetroArch necesita el núcleo instalado. La vuelta a USB sigue siendo manual.' :
      state.runtime_prepared === true ?
      'Mando PSP activo sin límite de tiempo. Selecciona un juego o elige Salir del Dock cuando quieras terminar; el cambio a almacenamiento todavía es manual.' :
      'PPSSPP necesita volver a prepararse en el PC. Sal del Dock y vuelve manualmente a almacenamiento USB antes de continuar.';
  } else if (returnPending) {
    title = disconnected ? 'Vuelve a conectar tu Memory Stick' :
      retroSavePending ? 'Partidas retro pendientes de devolución' : 'Devolución de partidas pendiente';
    description = disconnected ?
      state.session?.return_sync_authorized ?
        'Salida autorizada: mantén START+SELECT en la PSP para cerrar HID, retira el cable con seguridad y vuelve manualmente al USB Sony original. PSP Dock devolverá las partidas si no hay conflictos.' :
        'Sal del modo HID en la PSP y vuelve manualmente al almacenamiento USB de la misma Memory Stick. Entonces podrás comprobar y devolver los guardados.' :
      retroSavePending ? `Hay ${retroSavePending} SRAM retro pendientes. Abre Partidas y elige PSP → PC, PC → PSP u Omitir para cada juego; no se escribirá en la PSP sin confirmar.` :
      plan.save_conflicts ? 'Hay versiones de partidas diferentes. Elige la correcta antes de devolver los guardados.' :
      plan.save_actions ? 'Comprueba los cambios y confirma las copias pendientes antes de desconectar.' :
        'No hay diferencias de partidas. Confirma la comprobación final para cerrar la devolución.';
  } else if (state.session?.return_result === 'verified' && plan.connected) {
    title = 'Partidas devueltas y verificadas';
    description = 'La PSP y el PC están sincronizados. Antes de quitar el cable, usa la extracción segura del almacenamiento de Windows. El cambio automático de USB sigue pendiente.';
  } else if (diagnostic) {
    title = 'PSP Dock · modo diagnóstico 0207';
    description = 'Puedes navegar por el menú. La PSP ofrece un disco de prueba de 512 KiB; los juegos están desactivados.';
  } else if (state.controller_status?.connected) {
    title = 'Mando PSP HID detectado en Windows';
    description = prepared && retroOnly ?
      'Abre la sección Retro para iniciar tus ROMs verificadas en RetroArch. El mando puede necesitar configuración inicial en RetroArch.' :
      prepared && state.runtime_prepared === true ?
      'Mando HID PDK1 detectado y PPSSPP preparado. Puedes elegir un juego local; este modo se activa manualmente.' :
      prepared ? 'Las partidas están preparadas pero PPSSPP sigue pendiente en el PC. Vuelve al almacenamiento USB y prepara PPSSPP antes de jugar.' :
      'Mando detectado; todavía hace falta preparar las partidas en modo almacenamiento antes de jugar.';
  } else if (disconnected && prepared) {
    title = retroOnly ? 'Biblioteca retro preparada · esperando mando' :
      state.runtime_prepared === true ? 'Partidas y PPSSPP preparados · esperando mando' : 'Partidas listas · PPSSPP pendiente';
    description = retroOnly ?
      'Activa manualmente el mando HID PDK1 de la PSP; después abre Retro para elegir una ROM.' :
      state.runtime_prepared === true ?
      'El almacenamiento ya no está disponible. Activa manualmente el mando HID PDK1 en tu PSP y vuelve al Dock para iniciar PPSSPP.' :
      'Antes de usar el modo HID, vuelve al almacenamiento USB y utiliza «Preparar PPSSPP» en el PC.';
  } else if (disconnected) {
    title = retroOnly ? 'Emuladores retro de la última PSP' : 'Biblioteca de la última PSP';
    description = 'El catálogo se conserva en Windows. Reconecta tu PSP para comparar juegos y partidas.';
  } else if (plan.missing_games && !skipped) {
    title = plan.missing_games + (plan.missing_games === 1 ? ' juego pendiente de copiar' : ' juegos pendientes de copiar');
    description = 'Elige si copias al PC los juegos que faltan o sigues con los disponibles. La decisión se toma en modo almacenamiento.';
  } else if (plan.save_conflicts) {
    title = 'Hay partidas que necesitan tu elección';
    description = 'Revisa los guardados antes de iniciar una partida. Ningún conflicto se resuelve sin tu elección.';
  } else if (plan.save_actions) {
    title = 'Hay guardados pendientes de sincronizar';
    description = 'Comprueba el origen y el destino de cada partida. Se copiarán únicamente tras tu confirmación.';
  } else if (plan.missing_games && skipped) {
    title = 'Continúas con tu biblioteca local';
    description = 'Has omitido esta copia. Los juegos pendientes seguirán visibles pero no se lanzarán hasta transferirlos.';
  } else if (plan.different_games) {
    title = 'Hay juegos con versiones distintas';
    description = 'Se conservan ambas versiones. Elige manualmente qué hacer; no sobrescribiremos archivos diferentes.';
  } else if (retroOnly && retroMissing) {
    title = `${retroMissing} ROMs pendientes de copiar`;
    description = 'Abre Retro, selecciona un emulador y una ROM y confirma su copia al PC. No se sobrescribe ninguna ROM existente.';
  } else if (retroOnly && !prepared) {
    title = 'ROMs retro disponibles · prepara las partidas';
    description = 'Confirma la comparación de guardados para preparar esta Memory Stick. RetroArch no necesita PPSSPP.';
  } else if (retroOnly) {
    title = 'Biblioteca retro lista en Windows';
    description = 'Activa el mando HID de la PSP y selecciona la ROM en Retro. Necesitarás RetroArch y su núcleo instalado.';
  } else if (!prepared && plan.connected && plan.ready_games) {
    title = 'Revisa las partidas para preparar el juego';
    description = 'Confirma la comparación de los guardados aunque no haya diferencias. Después podrás abrir juegos en PPSSPP.';
  } else if (plan.ready_games) {
    title = state.runtime_prepared === true ? 'Juegos y PPSSPP listos en el PC' : 'Partidas listas · falta preparar PPSSPP';
    description = state.runtime_prepared === true ?
      'La biblioteca y PPSSPP están preparados. Puedes activar el mando HID manualmente para elegir y jugar.' :
      'La sincronización suele preparar PPSSPP automáticamente. Si quedó pendiente, vuelve a intentarlo desde esta pantalla antes de activar HID.';
  } else if (plan.connected) {
    title = 'No hay juegos listos en el PC';
    description = 'Conecta una PSP con juegos o configura la biblioteca del PC para iniciar la preparación.';
  }
  $('preflight-title').textContent = title;
  $('preflight-description').textContent = description;
  $('preflight-caveat').textContent = state.demo ?
    'Vista sintética: el inicio y la conexión HID de juegos reales están desactivados.' :
    diagnostic ? 'El disco de prueba es sintético. No contiene los juegos ni las partidas de tu Memory Stick.' :
    product.enabled ? 'Los juegos conservan sus comprobaciones de copia, guardados y emulador antes de iniciarse.' : state.controller_status?.connected ?
      'HID PDK1 comprobado; es una conexión manual. El puente automático entre USB y mando aún está pendiente.' :
      'Mando PSP: el cambio automático USB almacenamiento ↔ HID todavía no está implementado.';
  const canAct = !!plan.connected && !diagnostic && !product.enabled && !state.recovery_required && !playing && !ui.busy && !ui.serviceOffline;
  $('preflight-copy').hidden = !canAct || !plan.missing_games || returnPending;
  $('preflight-saves').hidden = !canAct || !(returnPending || plan.save_actions || plan.save_conflicts || !prepared);
  const canRetryRuntime = !!state.selected_device && !state.demo && !diagnostic && !state.scanning &&
    prepared && state.runtime_prepared === false && !state.recovery_required && !playing && !returnPending;
  $('preflight-prepare-runtime').hidden = !canRetryRuntime;
  $('preflight-prepare-runtime').disabled = ui.busy || ui.serviceOffline || !plan.ready_games;
  $('preflight-prepare-runtime').textContent = ui.operation === '/api/prepare-ppsspp' ? 'Preparando PPSSPP…' : 'Preparar PPSSPP';
  $('preflight-prepare-runtime').title = !plan.ready_games ?
    'Primero copia al PC al menos un juego verificado' : state.runtime_error ?
    'Reintentar la preparación de PPSSPP en el PC; las partidas ya están guardadas' :
    'Preparar el entorno de PPSSPP en el PC antes de usar el modo mando';
  $('preflight-skip').hidden = !canAct || !plan.missing_games || skipped || returnPending;
  $('preflight-exit-dock').hidden = !inHidXmb || ui.busy || ui.serviceOffline;
  $('preflight-copy').textContent = skipped ? 'Revisar juegos pendientes' :
    'Copiar ' + plan.missing_games + (plan.missing_games === 1 ? ' juego' : ' juegos');
  $('preflight-saves').textContent = returnPending ? 'Revisar devolución' :
    plan.save_actions || plan.save_conflicts ? 'Revisar partidas' : 'Preparar partidas para jugar';
  const activeStep = returnPending && !playing && !inHidXmb ? 'phase-return' :
    playing || state.controller_status?.connected ? 'phase-play' : !plan.connected && !prepared ? 'phase-connect' :
      plan.missing_games && !skipped || plan.save_actions || plan.save_conflicts || !prepared ||
        state.runtime_prepared === false ? 'phase-copy' :
        'phase-play';
  for (const id of ['phase-connect', 'phase-compare', 'phase-copy', 'phase-play', 'phase-return']) {
    $(id).classList.toggle('current', id === activeStep);
    $(id).classList.toggle('done', plan.connected && (
      id === 'phase-connect' || id === 'phase-compare' || id === 'phase-copy' && prepared && !plan.save_conflicts && !plan.save_actions
    ) || id === 'phase-play' && returnPending && !playing && !inHidXmb);
    if (id === activeStep) $(id).setAttribute('aria-current', 'step');
    else $(id).removeAttribute('aria-current');
  }
}
function selectGame(id, focus = false) {
  ui.selected = id;
  renderSelected(true, focus);
}
function renderSelected(scroll, focus = false) {
  const state = ui.state;
  const game = state?.games?.find((g) => g.id === ui.selected);
  if (!game) return;
  const index = state.games.indexOf(game);
  $('selection-progress').textContent = `${String(index + 1).padStart(2, '0')} / ${String(state.games.length).padStart(2, '0')}`;
  $('game-prev').disabled = state.games.length < 2;
  $('game-next').disabled = state.games.length < 2;
  document.querySelectorAll('.game-card').forEach((card) => {
    const selected = card.dataset.id === game.id;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', String(selected));
    card.tabIndex = selected ? 0 : -1;
    if (selected && scroll) card.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'nearest', inline: 'center' });
    if (selected && focus) card.focus({ preventScroll: true });
  });
  $('game-title').textContent = game.title;
  $('game-format').textContent = `${game.format || 'JUEGO'}${state.demo ? ' · MUESTRA SINTÉTICA' : ' · PLAYSTATION PORTABLE'}`;
  const availability = { ready: 'Disponible en PC', missing: 'Pendiente de copiar al PC', different: 'Versión diferente en PC', checking: 'Comprobando en PSP...' };
  $('game-meta').textContent = `${bytes(game.size)}  /  ${availability[game.availability] || 'Estado desconocido'}`;
  const saves = state.saves || {};
  const pendingSaves = !!(saves.actions?.length || saves.conflicts?.length);
  const prepared = state.preflight?.saves_prepared === true;
  const manualHid = state.controller_status?.connected === true;
  const connection = dockConnectionView(state, { serviceOffline: ui.serviceOffline });
  const usePcSaves = connection.product?.usePcSaves === true;
  const canLaunch = !ui.serviceOffline && !state.scanning && !state.demo && !!state.can_launch_game &&
    connection.canPlay && state.runtime_prepared === true && manualHid && game.availability === 'ready' &&
    !!state.emulator?.available && !state.session?.running &&
    !state.session?.exit_requested && !state.recovery_required &&
    (usePcSaves || !pendingSaves && prepared);
  $('launch-button').disabled = !canLaunch;
  let note = connection.product?.enabled ? 'Mando PSP conectado. El juego se abrirá en PPSSPP con la biblioteca del PC.' :
    'Mando PSP HID PDK1 detectado. El juego se abrirá en PPSSPP con la biblioteca local; la conexión HID es manual.';
  if (ui.serviceOffline) note = 'La app no responde. Espera a que recupere la conexión para jugar.';
  else if (state.recovery_required) note = 'Hay una sesión interrumpida. Recupérala antes de jugar.';
  else if (state.session?.running) note = 'PPSSPP está abierto. Termina la sesión antes de lanzar otra partida.';
  else if (state.session?.pending_return && (state.session?.exit_requested || !manualHid))
    note = 'La salida del Dock está pendiente. Vuelve a almacenamiento USB y confirma la devolución de partidas.';
  else if (state.demo) note = 'Biblioteca sintética: estos juegos no se pueden ejecutar.';
  else if (connection.diagnostic) note = 'Modo diagnóstico 0207: puedes navegar. Los juegos están desactivados mientras se usa el disco de prueba de 512 KiB.';
  else if (game.availability === 'missing') note = connection.product?.enabled ?
    'Abre Opciones y elige Copiar al Dock. El mando volverá a estar disponible cuando termine la copia.' : state.selected_device ?
    'Este juego está en la PSP. Cópialo al PC tras revisar la transferencia.' :
    'Este juego aún no está en el PC. Vuelve a almacenamiento USB si quieres copiarlo.';
  else if (game.availability === 'different') note = 'Las versiones difieren; revisa los archivos manualmente. No se sobrescribe ningún juego.';
  else if (pendingSaves && !usePcSaves) note = 'Revisa y confirma los guardados pendientes antes de abrir PPSSPP.';
  else if (!prepared && !usePcSaves) note = state.selected_device ?
    'Confirma la preparación de partidas, incluso si no hay archivos por copiar.' :
    'Reconecta la PSP en almacenamiento para revisar y preparar las partidas.';
  else if (!state.emulator?.available) note = 'Configura la ruta de PPSSPP en Ajustes para ejecutar este juego.';
  else if (state.runtime_prepared !== true) note = connection.product?.enabled ?
    'PPSSPP todavía no está preparado. Revisa su configuración en Ajustes antes de jugar.' : state.selected_device ?
    state.runtime_error ?
      'La preparación de PPSSPP en el PC ha fallado. Las partidas están listas; pulsa «Preparar PPSSPP» para reintentarlo.' :
      'PPSSPP aún no está preparado en el PC. Pulsa «Preparar PPSSPP» antes de activar el mando HID.' :
    'PPSSPP no está preparado. Vuelve al almacenamiento USB para prepararlo y evita gastar la sesión HID.';
  else if (state.session?.pending_return && manualHid && !state.session?.exit_requested)
    note = 'Has vuelto al menú. Puedes jugar a otro título o elegir Salir del Dock para devolver los guardados.';
  else if (state.selected_device && !connection.nativeController) note = 'Las partidas están preparadas. Termina el almacenamiento USB y activa manualmente el mando HID PDK1 antes de jugar.';
  else if (!manualHid) note = 'Esperando el mando PSP HID PDK1 en Windows. El cambio desde almacenamiento USB se realiza manualmente.';
  else if (!state.can_launch_game) note = 'Falta una verificación del lanzador. Revisa el estado de la sesión antes de continuar.';
  else if (usePcSaves) note = 'Esta sesión utiliza las partidas del PC, como elegiste al entrar. Al salir se comprobarán los cambios antes de devolverlos a la PSP.';
  $('launch-note').textContent = note;
  $('launch-button').title = canLaunch ? 'Abrir el juego en PPSSPP' : note;
  $('transfer-button').hidden = game.availability !== 'missing';
  $('transfer-button').disabled = !state.selected_device || state.recovery_required || state.session?.running || !!state.session?.pending_return || ui.serviceOffline;
  $('graphics-open').disabled = !!state.demo || ui.busy || ui.serviceOffline ||
    !!state.session?.running || !!state.session?.exit_requested || !!state.recovery_required;
  if (game.availability === 'different') $('game-meta').textContent += ' · Se conservan ambas versiones; revisa los archivos manualmente.';
}
function moveSelection(delta) {
  const games = ui.state?.games || [];
  if (!games.length) return;
  const index = games.findIndex((game) => game.id === ui.selected);
  const next = ((Math.max(0, index) + delta % games.length) + games.length) % games.length;
  selectGame(games[next].id, true);
}
function launch() {
  if (!ui.selected) return;
  if (ui.busy || ui.serviceOffline) return;
  if (ui.state?.recovery_required) { toast('Sesión interrumpida: revisa PPSSPP y la recuperación antes de continuar.'); return; }
  const state = ui.state;
  const game = state?.games?.find((item) => item.id === ui.selected);
  if (state.demo) { toast('Esta es una muestra sintética. El inicio está desactivado.'); return; }
  if (state.controller_status?.mode === 'msc_diagnostic') { toast('Modo diagnóstico: puedes navegar, pero los juegos están desactivados.'); return; }
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  if (state.session?.running) { toast('PPSSPP ya está en ejecución.'); return; }
  if (state.session?.pending_return && (state.session?.exit_requested || !state.controller_status?.connected)) {
    if (state.selected_device) openSaves();
    else toast('Termina la salida del Dock volviendo al modo almacenamiento USB y revisando las partidas.');
    return;
  }
  if (game?.availability === 'missing') {
    if (state.selected_device) openTransfer([ui.selected]);
    else toast('Reconecta la PSP en modo almacenamiento antes de copiar este juego.');
    return;
  }
  if (game?.availability === 'different') {
    toast('Las versiones diferentes se conservan. Revisa los archivos manualmente antes de jugar.');
    return;
  }
  if (!connection.product?.usePcSaves &&
      (state.saves?.actions?.length || state.saves?.conflicts?.length || state.preflight?.saves_prepared !== true)) {
    if (state.selected_device) openSaves();
    else toast('Antes de jugar debes preparar las partidas. Reconecta la PSP en modo almacenamiento.');
    return;
  }
  if (!state.emulator?.available) { openSettings(); return; }
  if (state.runtime_prepared !== true) {
    toast(state.selected_device ?
      'Las partidas están preparadas. Usa «Preparar PPSSPP» para completar la preparación del PC.' :
      'Vuelve al modo almacenamiento USB y prepara PPSSPP antes de utilizar el mando HID.');
    return;
  }
  if (!state.can_launch_game || !dockConnectionView(state).canPlay) {
    toast(state.selected_device ?
      'Las partidas están listas. Entra manualmente en modo mando HID PDK1 y vuelve a intentarlo.' :
      'Todavía no se detecta el mando PSP HID PDK1 necesario para iniciar el juego.');
    return;
  }
  if (!$('launch-button').disabled) mutate('/api/launch', { id: ui.selected }, 'Abriendo PPSSPP en el PC…');
}
function transferFeedback(message) {
  const note = $('transfer-progress-feedback');
  note.hidden = !message;
  if (note.textContent !== message) note.textContent = message;
}
function renderTransferSelection() {
  const total = ui.transfer.reduce((sum, game) => sum + Number(game.size || 0), 0);
  const itemName = ui.transferKind === 'retro' ? 'ROM' : 'juego';
  $('transfer-summary').textContent = ui.transfer.length ?
    `${ui.transfer.length} ${ui.transfer.length === 1 ? itemName + ' pendiente' : itemName === 'ROM' ? 'ROMs pendientes' : 'juegos pendientes'} · ${bytes(total)} en total.` :
    'No quedan archivos seleccionados pendientes de copiar en el PC.';
  $('transfer-list').replaceChildren(...ui.transfer.map((game) => {
    const li = el('li');
    li.append(el('strong', '', game.title), el('span', '', bytes(game.size)));
    return li;
  }));
}
function renderTransferControls() {
  const copying = ui.transferInFlight;
  $('transfer-progress-panel').hidden = !copying && !ui.transferFinished;
  $('transfer-list').hidden = copying;
  $('transfer-close-x').disabled = copying;
  $('transfer-close').hidden = copying;
  $('transfer-close').textContent = ui.transferFinished ? 'Cerrar' : 'Cancelar';
  $('transfer-cancel-progress').hidden = !copying;
  $('transfer-cancel-progress').disabled = !copying || ui.transferCancelRequested || ui.transferCancelling ||
    ['done', 'cancelled', 'failed'].includes(ui.transferStatus?.phase);
  $('transfer-cancel-progress').textContent = ui.transferCancelRequested ? 'Cancelación solicitada…' :
    ui.transferCancelling ? 'Solicitando cancelación…' : 'Cancelar copia';
  $('transfer-confirm').hidden = copying;
  $('transfer-confirm').disabled = copying || ui.busy || !ui.transfer.length || ui.serviceOffline;
  $('transfer-confirm').textContent = ui.transferFinished ? (ui.transferKind === 'retro' ? 'Copiar ROM pendiente' : 'Copiar juegos pendientes') : 'Confirmar copia';
}
function renderTransferStatus(status) {
  if (!status || typeof status !== 'object') return;
  const phase = ['idle', 'copying', 'verifying', 'done', 'cancelled', 'failed'].includes(status.phase) ?
    status.phase : 'idle';
  const total = Math.max(0, Number(status.total_bytes) || 0);
  const copied = Math.max(0, Math.min(total, Number(status.copied_bytes) || 0));
  const index = Math.max(0, Math.trunc(Number(status.game_index) || 0));
  const count = Math.max(0, Math.trunc(Number(status.games_total) || 0));
  const itemType = ui.transferKind === 'retro' ? 'ROM' : 'juego';
  const item = count ? `${itemType} ${Math.min(index || 1, count)} de ${count}` : (itemType === 'ROM' ? 'ROMs' : 'juegos');
  const title = typeof status.title === 'string' ? status.title : '';
  const game = index > 0 ? ui.transfer[index - 1] : null;
  const filename = typeof game?.relative === 'string' ?
    game.relative.split(/[\\/]/).at(-1) : '';
  const file = title && filename && title !== filename ? `${title} · ${filename}` :
    title || filename || (phase === 'idle' ? 'Preparando el primer juego…' : 'Biblioteca PSP');
  let heading = 'Esperando a que comience la copia…';
  if (phase === 'copying') heading = `Copiando ${item} al PC…`;
  else if (phase === 'verifying') heading = `Verificando ${item}…`;
  else if (phase === 'done') heading = 'Copia y verificación completadas';
  else if (phase === 'cancelled') heading = 'Copia cancelada';
  else if (phase === 'failed') heading = 'No se pudo completar la copia';
  if ($('transfer-phase').textContent !== heading) $('transfer-phase').textContent = heading;
  if ($('transfer-progress-file').textContent !== file) $('transfer-progress-file').textContent = file;
  const percentage = total ? Math.min(100, Math.max(0, Math.floor(100 * copied / total))) :
    phase === 'done' ? 100 : null;
  const progress = $('transfer-progress');
  if (percentage === null) {
    progress.removeAttribute('value');
    $('transfer-percent').textContent = '—';
  } else {
    progress.value = percentage;
    $('transfer-percent').textContent = `${percentage} %`;
  }
  $('transfer-progress-bytes').textContent = total ? `${bytes(copied)} de ${bytes(total)} copiados` :
    'Calculando tamaño total…';
  if (phase === 'verifying') {
    transferFeedback(ui.transferCancelRequested ?
      'Cancelación solicitada. Esperando a que termine la verificación segura…' :
      'Comprobando la integridad del archivo actual. El porcentaje de copia puede llegar al 100 % antes de finalizar la verificación.');
  } else if (phase === 'cancelled') {
    transferFeedback('Copia cancelada. Se descarta el archivo parcial del PC; los juegos ya terminados y todos los archivos de la PSP se conservan.');
  } else if (phase === 'failed') {
    transferFeedback(`La copia se ha detenido: ${typeof status.error === 'string' && status.error ? status.error : 'error desconocido'}.`);
  } else if (phase === 'done') {
    transferFeedback('Todos los archivos seleccionados se han copiado y verificado en el PC.');
  } else if (ui.transferCancelRequested) {
    transferFeedback('Cancelación solicitada. Esperando a que la copia termine de forma segura…');
  } else if (!ui.transferCancelling) {
    transferFeedback('');
  }
  ui.transferStatus = status;
  renderTransferControls();
}
async function pollTransferStatus(run) {
  if (!ui.transferInFlight || run !== ui.transferRun) return;
  try {
    const status = await request('/api/transfer-status');
    if (ui.transferInFlight && run === ui.transferRun) renderTransferStatus(status);
  } catch {
    if (ui.transferInFlight && run === ui.transferRun && !ui.transferCancelRequested)
      transferFeedback('No se puede actualizar el progreso momentáneamente. La copia continúa en el PC.');
  } finally {
    if (ui.transferInFlight && run === ui.transferRun)
      ui.transferPollTimer = setTimeout(() => pollTransferStatus(run), 500);
  }
}
async function cancelTransfer() {
  if (!ui.transferInFlight || ui.transferCancelRequested || ui.transferCancelling ||
      ['done', 'cancelled', 'failed'].includes(ui.transferStatus?.phase)) return;
  ui.transferCancelling = true;
  transferFeedback('Solicitando cancelación de la copia…');
  renderTransferControls();
  try {
    await request('/api/cancel-transfer', {});
    if (ui.transferInFlight) {
      ui.transferCancelRequested = true;
      transferFeedback('Cancelación solicitada. Esperando a que la operación se detenga con seguridad…');
    }
  } catch (error) {
    if (ui.transferInFlight)
      transferFeedback(`No se pudo solicitar la cancelación: ${error.message}`);
  } finally {
    ui.transferCancelling = false;
    renderTransferControls();
  }
}
async function startTransfer() {
  if (ui.transferInFlight || ui.busy || ui.serviceOffline || !ui.transfer.length ||
      !ui.state?.selected_device || ui.state?.recovery_required || ui.state?.session?.running ||
      ui.state?.session?.pending_return) return;
  ui.transferInFlight = true;
  ui.transferFinished = false;
  ui.transferStatus = null;
  ui.transferCancelRequested = false;
  ui.transferCancelling = false;
  const run = ++ui.transferRun;
  $('transfer-progress').value = 0;
  $('transfer-percent').textContent = '0 %';
  $('transfer-phase').textContent = 'Iniciando copia…';
  $('transfer-progress-file').textContent = 'Preparando el primer juego…';
  $('transfer-progress-bytes').textContent = 'Esperando datos de PSP Dock…';
  transferFeedback('');
  renderTransferControls();
  void pollTransferStatus(run);
  let completed = false;
  try {
    completed = await mutate(ui.transferKind === 'retro' ? '/api/retro-transfer' : '/api/transfer',
      { ids: ui.transfer.map((game) => game.id) },
      ui.transferKind === 'retro' ? 'ROMs copiadas y verificadas en el PC.' : 'Juegos copiados y verificados en el PC.');
  } finally {
    ui.transferInFlight = false;
    ++ui.transferRun;
    clearTimeout(ui.transferPollTimer);
    ui.transferPollTimer = null;
    try {
      const finalStatus = await request('/api/transfer-status');
      renderTransferStatus(finalStatus);
      if (!completed && finalStatus.phase === 'idle') {
        $('transfer-phase').textContent = 'No se pudo iniciar la copia';
        transferFeedback('La copia no llegó a comenzar. Revisa el error de PSP Dock antes de reintentar.');
      }
    } catch {
      transferFeedback('No se pudo consultar el estado final. Comprueba la biblioteca antes de reintentar.');
    }
    ui.transferFinished = true;
    const catalog = ui.transferKind === 'retro' ? ui.state?.retro?.roms : ui.state?.games;
    ui.transfer = ui.transfer.filter((game) =>
      catalog?.some((current) => current.id === game.id && current.availability === 'missing'));
    renderTransferSelection();
    renderTransferControls();
  }
  if (completed) $('transfer-dialog').close();
}
function openTransfer(ids) {
  if (ui.transferInFlight || ui.busy || $('transfer-dialog').open || ui.serviceOffline ||
      !ui.state?.selected_device || ui.state?.recovery_required || ui.state?.session?.running ||
      ui.state?.session?.pending_return) return;
  ui.transferKind = 'psp';
  ui.transfer = (ui.state?.games || []).filter((game) => ids.includes(game.id) && game.availability === 'missing');
  if (!ui.transfer.length) return;
  ui.transferFinished = false;
  ui.transferStatus = null;
  ui.transferCancelRequested = false;
  ui.transferCancelling = false;
  $('transfer-title').textContent = 'Copiar juegos al PC';
  renderTransferSelection();
  renderTransferControls();
  $('transfer-dialog').showModal();
}
function openRetroTransfer(rom) {
  if (ui.transferInFlight || ui.busy || $('transfer-dialog').open || ui.serviceOffline ||
      !ui.state?.selected_device || ui.state?.recovery_required || ui.state?.session?.running ||
      ui.state?.session?.pending_return || rom?.availability !== 'missing') return;
  ui.transferKind = 'retro';
  ui.transfer = [rom];
  ui.transferFinished = false;
  ui.transferStatus = null;
  ui.transferCancelRequested = false;
  ui.transferCancelling = false;
  $('transfer-title').textContent = 'Copiar ROM al PC';
  renderTransferSelection();
  renderTransferControls();
  $('transfer-dialog').showModal();
}
function openSaves() {
  // En el XMB las partidas son una sección propia, no un diálogo.
  renderSaves();
  xmbGoto('partidas');
}
function preparePpsspp() {
  const state = ui.state;
  if (ui.busy || ui.serviceOffline || !state?.selected_device || state.demo ||
      state.scanning || state.recovery_required || state.session?.running ||
      state.session?.pending_return || state.preflight?.saves_prepared !== true ||
      state.runtime_prepared !== false) return;
  if (!state.preflight?.ready_games) {
    toast('Copia primero al PC al menos un juego verificado para preparar PPSSPP.');
    return;
  }
  if (!state.emulator?.available) {
    toast('Configura PPSSPP en Ajustes antes de preparar el emulador en el PC.');
    openSettings();
    return;
  }
  mutate('/api/prepare-ppsspp', {}, (next) => next?.runtime_prepared === true ?
    'PPSSPP preparado en el PC. Ya puedes activar el mando HID manualmente.' :
    'La preparación de PPSSPP sigue pendiente. Revisa el aviso y vuelve a intentarlo.');
}
function syncResultMessage(state, returning = false) {
  if (ui.serviceOffline) return 'Operación enviada; espera a que PSP Dock responda para comprobar el resultado.';
  if (state?.product_flow?.enabled === true) return state.product_flow.phase === 'safe_to_disconnect' ?
    'Guardados devueltos y comprobados.' : state.product_flow.phase === 'attention' ?
      'Cambios comprobados. Revisa los pendientes antes de continuar.' : 'Sincronización actualizada.';
  if (returning) return state?.session?.pending_return ?
    'Partidas sin conflictos sincronizadas; aún quedan pendientes de devolución.' :
    'Devolución de partidas comprobada.';
  if (state?.preflight?.saves_prepared !== true)
    return 'Partidas sin conflictos sincronizadas. Revisa las partidas pendientes antes de jugar.';
  if (state?.runtime_prepared === true)
    return 'Partidas sincronizadas y PPSSPP preparado automáticamente en el PC.';
  if (!state?.preflight?.ready_games)
    return 'Partidas preparadas. Copia al PC al menos un juego verificado antes de preparar PPSSPP.';
  if (!state?.emulator?.available)
    return 'Partidas preparadas. Configura PPSSPP en Ajustes antes de preparar el PC.';
  return 'Partidas preparadas. PPSSPP quedó pendiente en el PC; puedes reintentar con «Preparar PPSSPP».';
}
function exitDock() {
  const state = ui.state;
  if (ui.busy || ui.serviceOffline || state.session?.running ||
      state.session?.exit_requested || state.controller_status?.connected !== true) return;
  const product = xmbConnectionView().product;
  mutate('/api/exit-dock', {}, product?.enabled ?
    'Preparando la devolución de partidas y estados compatibles…' :
    'Salida solicitada. Mantén START+SELECT 1,5 segundos en la PSP, termina HID y vuelve manualmente al USB Sony para devolver las partidas.');
}
function renderSaves() {
  const saves = ui.state?.saves || { actions: [], conflicts: [], unchanged: 0 };
  const signature = JSON.stringify([saves, ui.state?.selected_device, ui.state?.recovery_required,
    ui.state?.session?.running, ui.state?.session?.pending_return, ui.state?.preflight?.saves_prepared,
    ui.state?.retro_saves, ui.state?.product_flow, ui.serviceOffline, ui.busy, ui.operation]);
  if (signature === ui.savesSignature) return;
  ui.savesSignature = signature;
  const content = $('saves-content');
  content.replaceChildren();
  const actions = saves.actions || [];
  const conflicts = saves.conflicts || [];
  const returnPending = !!ui.state?.session?.pending_return;
  const prepared = ui.state?.preflight?.saves_prepared === true;
  const product = dockProductFlowView(ui.state, { serviceOffline: ui.serviceOffline, busy: ui.busy });
  const canReview = !product.enabled || product.canReview;
  const returningToPsp = saves.direction_scope === 'to_psp' || ui.state?.product_flow?.purpose === 'exit';
  const productExit = product.enabled && returningToPsp;
  content.append(el('p', 'save-summary', ui.state?.selected_device ?
    `${actions.length} por sincronizar · ${conflicts.length} ${conflicts.length === 1 ? 'conflicto' : 'conflictos'} · ${saves.unchanged || 0} sin cambios` :
    'Sin comparación actual · requiere almacenamiento USB de la PSP'));
  if (!actions.length && !conflicts.length) {
    const message = !ui.state?.selected_device ? returnPending && ui.state?.controller_status?.connected && !ui.state?.session?.exit_requested ?
      'Primero elige Salir del Dock. Después regresa manualmente al almacenamiento USB de la misma PSP.' :
      ui.state?.session?.exit_requested ?
      'La salida del Dock ya está solicitada. Cambia manualmente la PSP a almacenamiento USB y vuelve a conectar la misma Memory Stick.' :
      'Conecta la PSP en modo almacenamiento para comprobar las partidas.' :
      productExit ? 'No quedan partidas PSP pendientes de revisión. Cierra este panel y elige Reintentar para continuar la salida.' :
      returnPending ? 'No hay diferencias pendientes. Confirma la devolución para dar por cerrada la sesión antes de desconectar.' :
        prepared ? 'Partidas preparadas. No hay nada pendiente de copiar.' :
          'Las partidas coinciden. Confirma esta comprobación para preparar los juegos antes de abrir PPSSPP.';
    content.append(el('p', 'quiet', message));
  }
  actions.forEach((action) => {
    const row = el('div', 'save-row');
    const slot = el('span', 'save-slot', action.slot);
    slot.append(el('small', '', action.direction === 'to_pc' ? 'Copiar desde la PSP al PC' : 'Copiar desde el PC a la PSP'));
    const direction = el('span', 'save-direction', action.direction === 'to_pc' ? 'PSP → PC' : 'PC → PSP');
    direction.classList.toggle('writes-psp', action.direction !== 'to_pc');
    row.append(slot, direction);
    content.append(row);
  });
  conflicts.forEach((conflict) => {
    const row = el('div', 'save-row');
    const text = el('span', '', conflict.slot);
    text.append(el('small', '', 'Ambas versiones han cambiado'));
    const buttons = el('div', 'conflict-actions');
    ['pc', 'psp'].forEach((choice) => {
      const button = el('button', '', returningToPsp ?
        choice === 'pc' ? 'Devolver la del PC' : 'Conservar la PSP sin copiar' : `Usar ${choice.toUpperCase()}`);
      button.type = 'button';
      button.disabled = !canReview || ui.busy || !!ui.state?.recovery_required || !ui.state?.selected_device ||
        !!ui.state?.session?.running || ui.serviceOffline ||
        (product.enabled && returningToPsp && (typeof conflict.revision !== 'string' || !conflict.revision));
      button.addEventListener('click', () => {
        ui.resolve = { slot: conflict.slot, choice,
          ...(product.enabled && returningToPsp ? { revision: conflict.revision } : {}) };
        $('resolve-title').textContent = 'Conservar esta versión';
        $('resolve-confirm').textContent = 'Usar esta versión';
        $('resolve-summary').textContent = returningToPsp && choice === 'psp' ?
          `Conservar la partida «${conflict.slot}» de la PSP sin copiar ni reemplazar ninguna versión.` :
          `Conservar la partida «${conflict.slot}» de ${choice === 'pc' ? 'este PC' : 'la PSP'} y reemplazar la otra versión.`;
        $('resolve-dialog').showModal();
      });
      buttons.append(button);
    });
    row.append(text, buttons);
    content.append(row);
  });
  renderRetroSaveReview(content, canReview);
  if (productExit && (actions.length || conflicts.length || ui.state?.retro_saves?.pending))
    content.append(el('p', 'quiet', 'Resuelve las partidas que necesitan revisión. Después cierra este panel y elige Reintentar para continuar la devolución y finalizar la conexión.'));
  const canSync = !productExit && canReview && !!ui.state?.selected_device && !ui.state?.recovery_required && !ui.state?.session?.running &&
    !ui.busy && !ui.serviceOffline && (actions.length > 0 || !conflicts.length && (returnPending || !prepared));
  $('sync-button').hidden = productExit;
  $('sync-button').disabled = !canSync;
  $('sync-button').textContent = actions.length ? conflicts.length ? 'Sincronizar sin conflictos' : 'Confirmar sincronización' :
    conflicts.length ? 'Resuelve primero los conflictos' :
      returnPending ? 'Confirmar devolución' : prepared ? 'Partidas preparadas' : 'Preparar partidas para jugar';
  const syncing = ui.operation === '/api/sync' && !returnPending;
  const resolving = ui.operation === '/api/resolve' && !returnPending;
  $('sync-runtime-note').hidden = !syncing && !resolving;
  $('sync-runtime-note').textContent = syncing ?
    'Sincronizando tus partidas. Si no quedan conflictos, PPSSPP se prepara automáticamente en el PC antes de activar HID…' :
    'Resolviendo la partida. Si es el último conflicto, PPSSPP se preparará automáticamente en el PC…';
}
function renderRetroSaveReview(content, canReview) {
  const state = ui.state || {};
  const rows = state.retro_saves?.rows || [];
  if (!rows.length) return;
  content.append(el('h3', 'retro-review-heading', 'Partidas de emuladores'));
  content.append(el('p', 'quiet', 'Las partidas internas compatibles se revisan por separado. Los estados instantáneos no compatibles se conservan en su dispositivo.'));
  const returning = state.product_flow?.purpose === 'exit' || state.session?.exit_requested === true;
  rows.forEach((save) => {
    const row = el('div', 'save-row');
    const info = el('span', '', save.title || save.rom_id);
    const descriptions = { identical: 'Coinciden PSP y PC', none: 'Todavía no hay una partida guardada',
      psp_to_pc: 'La versión de la PSP tiene cambios', pc_to_psp: 'La versión del PC tiene cambios',
      conflict: 'Las dos versiones han cambiado', unsupported: 'Formato no compatible', blocked: 'Necesita revisión' };
    info.append(el('small', '', save.skipped ? 'Conservada sin copiar durante esta salida' : save.reason || descriptions[save.status] || 'Estado sin verificar'));
    row.append(info);
    const choices = dockRetroSaveChoices(save, returning);
    if (choices.length) {
      const buttons = el('div', 'conflict-actions');
      choices.forEach((choice) => {
        const button = el('button', '', choice === 'skip' ? 'Conservar sin copiar' :
          choice === 'pc' ? 'Devolver la del PC' : 'Traer la de la PSP');
        button.type = 'button';
        button.disabled = !canReview || ui.busy || ui.serviceOffline || !state.selected_device ||
          state.retro_saves?.offline || !!state.session?.running || !!state.recovery_required;
        button.addEventListener('click', () => requestRetroSaveReview(save, choice));
        buttons.append(button);
      });
      row.append(buttons);
    }
    content.append(row);
  });
}
function requestRetroSaveReview(initial, choice) {
  const state = ui.state || {};
  const product = xmbConnectionView().product;
  const current = (state.retro_saves?.rows || []).find((row) => row.rom_id === initial.rom_id);
  const returning = state.product_flow?.purpose === 'exit' || state.session?.exit_requested === true;
  if (ui.busy || ui.serviceOffline || !state.selected_device || state.retro_saves?.offline ||
      state.session?.running || state.recovery_required || product?.enabled && !product.canReview ||
      !current || current.revision !== initial.revision || !dockRetroSaveChoices(current, returning).includes(choice)) return;
  ui.resolve = { kind: 'retro', id: current.rom_id, choice, revision: current.revision };
  $('resolve-title').textContent = choice === 'skip' ? 'Conservar sin copiar' : 'Conservar esta versión';
  $('resolve-confirm').textContent = choice === 'skip' ? 'Conservar sin copiar' : 'Usar esta versión';
  const warning = choice === 'pc' && current.suggested_path ?
    ` La ruta PSP «${current.source_relative}» es sugerida; comprueba que este emulador utiliza esa ubicación.` : '';
  $('resolve-summary').textContent = choice === 'skip' ?
    `Conservar la partida «${current.title}» sin copiar ni reemplazar ninguna versión durante esta salida.` :
    `Copiar la partida interna «${current.title}» ${choice === 'pc' ? 'del PC a la PSP' : 'de la PSP al PC'}, con respaldo y comprobación de la copia.${warning}`;
  if (!$('resolve-dialog').open) $('resolve-dialog').showModal();
}
async function openGraphics() {
  // Los gráficos por juego viven en la sección Gráficos del XMB.
  if (ui.selected && !ui.busy && !ui.serviceOffline) { xmbOpenGraphics(ui.selected); return; }
  if (!ui.selected || ui.busy || ui.serviceOffline || ui.state?.session?.running) return;
  const game = ui.state?.games?.find((item) => item.id === ui.selected);
  if (!game || ui.state?.recovery_required) return;
  if ($('graphics-dialog').open) return;
  ui.graphicsGame = game.id;
  $('graphics-error').hidden = true;
  $('graphics-game').textContent = game.title;
  $('graphics-monitor').textContent = 'Detectando monitor y GPU del PC…';
  $('graphics-current').textContent = '';
  $('graphics-save').disabled = true;
  $('graphics-dialog').showModal();
  try {
    const data = await request('/api/graphics/' + encodeURIComponent(game.id));
    if (ui.graphicsGame !== game.id || !$('graphics-dialog').open) return;
    $('graphics-quality').value = data.choice.quality;
    $('graphics-aspect').value = data.choice.aspect;
    $('graphics-texture').value = data.choice.texture || 'off';
    const display = data.plan.monitor;
    $('graphics-monitor').textContent = 'Monitor principal: ' +
      display.width + ' × ' + display.height +
      (display.ultrawide ? ' · ULTRAWIDE' : '') +
      '  ·  GPU: ' + (data.hardware?.gpu || 'no identificada');
    $('graphics-current').textContent = 'Render recomendado: ' +
      data.plan.internal_resolution + '× PSP (' + data.plan.internal_width +
      ' × ' + data.plan.internal_height + ')  ·  Backend: ' + data.plan.backend;
    const disc = data.game_metadata?.disc_id;
    $('graphics-pack').textContent = disc ?
      'Edición: ' + disc + (data.game_metadata.disc_version ? ' · versión ' + data.game_metadata.disc_version : '') +
      ' · paquete HD: ' + (data.hd_texture_pack_installed ? 'detectado en PPSSPP' : 'no instalado') :
      'ID de edición no reconocido: las mejoras de texturas integradas siguen disponibles, pero no se puede asociar un paquete HD de forma segura.';
    $('graphics-warning').textContent = data.plan.warning;
    $('graphics-save').disabled = false;
  } catch (error) {
    $('graphics-error').textContent = error.message;
    $('graphics-error').hidden = false;
  }
}
function graphicsWarningForChoice() {
  $('graphics-warning').textContent = $('graphics-aspect').value === 'stretch' ?
    'El estiramiento llena toda la ultrawide, pero deforma el juego: no amplía su campo de visión. Puedes cambiarlo después.' :
    'Conserva el encuadre PSP sin deformarlo. La ampliación real a 21:9 requiere un parche específico compatible con ese juego.';
}
async function saveGraphics() {
  if (!ui.graphicsGame || ui.busy) return;
  const gameId = ui.graphicsGame;
  const ok = await mutate('/api/graphics', {
    id: gameId, quality: $('graphics-quality').value,
    aspect: $('graphics-aspect').value,
    texture: $('graphics-texture').value,
  }, 'Perfil gráfico guardado para este juego en PPSSPP privado.');
  if (ok) {
    $('graphics-dialog').close();
    ui.graphicsGame = null;
  }
}
async function openSettings() {
  if ($('settings-dialog').open) return;
  $('settings-error').hidden = true;
  $('settings-dialog').showModal();
  $('settings-save').disabled = true;
  try {
    const settings = await request('/api/settings');
    for (const key of ['emulator', 'retroarch', 'retro_install_policy', 'library', 'memstick', 'psp_root', 'scale', 'backend']) {
      const field = $('settings-form').elements.namedItem(key);
      field.value = settings[key] ?? (key === 'scale' ? '3' : key === 'backend' ? 'directx11' : '');
    }
    $('settings-save').disabled = !!ui.state?.recovery_required || !!ui.state?.session?.running || !!ui.state?.session?.pending_return;
    if ($('settings-save').disabled) {
      $('settings-error').textContent = ui.state?.recovery_required ? 'Recupera la sesión interrumpida antes de cambiar los ajustes.' : 'Cierra y devuelve la sesión a la PSP antes de cambiar las carpetas.';
      $('settings-error').hidden = false;
    }
  } catch (error) {
    $('settings-error').textContent = error.message;
    $('settings-error').hidden = false;
  }
}
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const fields = Object.fromEntries(new FormData(event.currentTarget));
  fields.scale = Number(fields.scale);
  if (await mutate('/api/settings', fields, 'Ajustes guardados.')) $('settings-dialog').close();
});
$('settings-open').addEventListener('click', openSettings);
$('graphics-open').addEventListener('click', openGraphics);
$('graphics-save').addEventListener('click', saveGraphics);
$('graphics-aspect').addEventListener('change', graphicsWarningForChoice);
$('empty-settings').addEventListener('click', openSettings);
$('refresh-button').addEventListener('click', async () => {
  $('refresh-button').disabled = true;
  await refresh();
  $('refresh-button').disabled = false;
});
$('games-tab').addEventListener('click', () => { if (ui.selected) selectGame(ui.selected, true); });
$('saves-open').addEventListener('click', openSaves);
$('game-prev').addEventListener('click', () => moveSelection(-1));
$('game-next').addEventListener('click', () => moveSelection(1));
$('launch-button').addEventListener('click', launch);
$('transfer-button').addEventListener('click', () => openTransfer([ui.selected]));
$('transfer-all').addEventListener('click', () => openTransfer((ui.state?.games || []).filter((g) => g.availability === 'missing').map((g) => g.id)));
$('preflight-copy').addEventListener('click', () => openTransfer((ui.state?.games || []).filter((g) => g.availability === 'missing').map((g) => g.id)));
$('preflight-saves').addEventListener('click', openSaves);
$('preflight-prepare-runtime').addEventListener('click', preparePpsspp);
$('preflight-exit-dock').addEventListener('click', exitDock);
$('preflight-skip').addEventListener('click', async () => {
  const key=preflightKey(ui.state);
  const saved=await mutate('/api/copy-policy',{choice:'skip_missing'},
    'Decisión recordada: usar solo juegos disponibles; no se ha copiado nada.');
  if (saved) {
    ui.skippedCopies.add(key);
    renderPreflight();
  }
});
$('transfer-confirm').addEventListener('click', startTransfer);
$('transfer-cancel-progress').addEventListener('click', cancelTransfer);
$('transfer-dialog').addEventListener('cancel', (event) => {
  // Escape must not dismiss the progress UI while the verified copy is active.
  if (ui.transferInFlight) {
    event.preventDefault();
    if (!ui.transferCancelRequested) $('transfer-cancel-progress').focus();
  }
});
$('sync-button').addEventListener('click', async () => {
  // An exit is resumed by the explicit session retry after reviewing its
  // individual slots; the legacy unscoped sync cannot complete this flow.
  if (ui.state?.product_flow?.enabled === true &&
      (ui.state.product_flow.purpose === 'exit' || ui.state.saves?.direction_scope === 'to_psp')) return;
  const returning = !!ui.state?.session?.pending_return;
  const ok = await mutate('/api/sync', {}, (next) => syncResultMessage(next, returning));
  if (ok && !ui.serviceOffline && ui.state?.preflight?.saves_prepared &&
      !ui.state?.saves?.actions?.length && !ui.state?.saves?.conflicts?.length) $('saves-dialog').close();
});
$('resolve-confirm').addEventListener('click', async () => {
  if (ui.resolve?.kind === 'retro') {
    const { id, choice, revision } = ui.resolve;
    if (await mutate('/api/retro-save-sync', { id, choice, revision },
      choice === 'skip' ? 'Guardado retro omitido para esta salida.' : 'Partida retro copiada y comprobada.')) {
      $('resolve-dialog').close();
      ui.resolve = null;
    }
    return;
  }
  const returning = !!ui.state?.session?.pending_return;
  if (ui.resolve && await mutate('/api/resolve', ui.resolve, (next) => {
    if (next?.preflight?.saves_prepared) return syncResultMessage(next, returning);
    return 'Conflicto resuelto. Revisa las partidas que queden pendientes.';
  })) {
    $('resolve-dialog').close();
    if (!ui.serviceOffline && ui.state?.preflight?.saves_prepared &&
        !ui.state?.saves?.actions?.length && !ui.state?.saves?.conflicts?.length &&
        !ui.state?.retro_saves?.pending) $('saves-dialog').close();
  }
});
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(button.dataset.close).close()));
$('storage-location-confirm').addEventListener('click', async () => {
  const path=$('storage-location-path').value.trim();
  if (!path) {
    $('storage-location-error').textContent='Introduce una ruta de carpeta nueva en un disco fijo.';
    $('storage-location-error').hidden=false;
    return;
  }
  $('storage-location-confirm').disabled=true;
  try {
    const ok=await mutate('/api/storage-root',{path},
      'Biblioteca, partidas, texturas y recursos XMB copiados y verificados en la nueva ubicación.');
    if (ok) {
      $('storage-location-dialog').close();
      await xmbLoadSettings();
    }
  } finally {
    $('storage-location-confirm').disabled=false;
  }
});
$('storage-location-browse').addEventListener('click', async () => {
  $('storage-location-browse').disabled=true;
  try {
    const selected=await request('/api/choose-storage-root',{});
    if (!selected.cancelled && selected.path) $('storage-location-path').value=selected.path;
  } catch(error) {
    $('storage-location-error').textContent=error.message;
    $('storage-location-error').hidden=false;
  } finally {
    $('storage-location-browse').disabled=false;
  }
});
document.addEventListener('keydown', (event) => {
  // Native button behavior (including Enter on «Cerrar app») remains
  // available; no keyboard shortcut may operate the hidden XMB.
  const connection = xmbConnectionView();
  if (connection.waiting) return;
  if (ui.busy || document.querySelector('dialog[open]') || event.altKey || event.ctrlKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  const button = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down', Enter: 'accept', ' ': 'accept',
    Escape: 'back', Backspace: 'back', t: 'menu', T: 'menu', ContextMenu: 'menu', PageUp: 'pageLeft', PageDown: 'pageRight', Home: 'start' }[event.key];
  if (!button) return;
  event.preventDefault();
  if (connection.product?.visible) productFlowPress(button);
  else xmbPress(button);
});
function focusGameAction(delta = 1) {
  const candidates = [
    'launch-button', 'transfer-button', 'preflight-copy', 'preflight-skip',
    'preflight-prepare-runtime', 'preflight-exit-dock', 'preflight-saves',
    'graphics-open', 'saves-open',
  ];
  const buttons = candidates.map($).filter((button) =>
    button && !button.disabled && !button.hidden && button.getClientRects().length);
  const index = buttons.indexOf(document.activeElement);
  const next = index < 0 ? delta > 0 ? 0 : -1 : index + delta;
  const target = buttons[next] || document.querySelector('.game-card.selected');
  target?.focus({ preventScroll: true });
}
function focusDialogControl(dialog, delta) {
  const controls = Array.from(dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)'))
    .filter((node) => node.getClientRects().length);
  if (!controls.length) return;
  const index = controls.indexOf(document.activeElement);
  const next = index < 0 ? delta > 0 ? 0 : controls.length - 1 : (index + delta + controls.length) % controls.length;
  controls[next].focus();
}
function navigateDialogControl(dialog, direction) {
  const current = document.activeElement;
  // A PSP has no keyboard: left/right must change the selected OPTION,
  // not simply move focus past every configurable graphics parameter.
  if ((direction === 'left' || direction === 'right') &&
      current?.tagName === 'SELECT' && !current.disabled) {
    const allowed = Array.from(current.options)
      .map((option, index) => option.disabled ? null : index)
      .filter((index) => index !== null);
    if (allowed.length) {
      const position = allowed.indexOf(current.selectedIndex);
      const delta = direction === 'right' ? 1 : -1;
      const next = allowed[(Math.max(position, 0) + delta + allowed.length) % allowed.length];
      current.selectedIndex = next;
      current.dispatchEvent(new Event('change', { bubbles: true }));
    }
  } else {
    focusDialogControl(dialog, direction === 'left' || direction === 'up' ? -1 : 1);
  }
}
function controllerInput(pad) {
  const pressed = (index) => !!pad.buttons?.[index]?.pressed || (pad.buttons?.[index]?.value || 0) > .65;
  const horizontal = pad.axes?.[0] || 0;
  const vertical = pad.axes?.[1] || 0;
  return {
    left: pressed(14) || horizontal < -.6,
    right: pressed(15) || horizontal > .6,
    up: pressed(12) || vertical < -.6,
    down: pressed(13) || vertical > .6,
    accept: pressed(0), back: pressed(1), saves: pressed(3), graphics: pressed(2),
    settings: pressed(9),
    pageLeft: pressed(4), pageRight: pressed(5), exit: pressed(8),
  };
}
function gamepadLoop(time) {
  if (ui.appClosed) return;
  // On PSP HID prefer the Windows-native WinMM source to the browser:
  // otherwise one physical CROSS can fire twice (WinMM + Gamepad API).
  if (ui.nativePadConnected && Date.now() - ui.nativePadLastAt < 600) {
    requestAnimationFrame(gamepadLoop);
    return;
  }
  let pad = null;
  try { pad = navigator.getGamepads && Array.from(navigator.getGamepads()).find((item) => item?.connected !== false) || null; }
  catch { /* Some browsers do not allow polling before controller interaction. */ }
  const flowKey = ui.state?.product_flow?.enabled === true ?
    `${ui.state.product_flow.offer_id}:${ui.state.product_flow.phase}:${ui.state.controller_status?.phase}` : '';
  const identity = pad ? `${pad.index}:${pad.id}:${flowKey}` : '';
  if (identity !== ui.gamepadId) {
    ui.gamepadId = identity;
    ui.gamepadAt = 0;
    ui.gamepadDirection = '';
    // Prime buttons as held on a newly plugged-in controller, so a
    // physically held CROSS cannot auto-launch Dariusburst on connect.
    ui.gamepadHeld = pad ? controllerInput(pad) : {};
  }
  const status = pad ? 'Mando del navegador: conectado' :
    ui.state?.controller_status?.connected ?
      'Mando PSP · pulsa un botón para activar la navegación' :
      'Mando del navegador: sin detectar';
  if ($('gamepad-status').textContent !== status) $('gamepad-status').textContent = status;
  if (pad) {
    const input = controllerInput(pad);
    const edge = (name) => input[name] && !ui.gamepadHeld[name];
    const direction = input.left ? 'left' : input.right ? 'right' : input.up ? 'up' : input.down ? 'down' : '';
    const navigate = direction && (direction !== ui.gamepadDirection || time - ui.gamepadAt > 230);
    const dialog = Array.from(document.querySelectorAll('dialog[open]')).at(-1);
    if (!ui.busy) {
      if (dialog) {
        if (edge('back')) {
          if (!(dialog.id === 'transfer-dialog' && ui.transferInFlight)) dialog.close();
        } else if (navigate) {
          navigateDialogControl(dialog, direction);
          ui.gamepadAt = time;
        } else if (edge('accept')) {
          const target = document.activeElement;
          if (target?.tagName === 'BUTTON' && !target.disabled) target.click();
          else focusDialogControl(dialog, 1);
        }
      } else if (!ui.serviceOffline) {
        const button = edge('exit') ? 'select' : edge('settings') ? 'start' : edge('graphics') ? 'menu' :
          edge('pageLeft') ? 'pageLeft' : edge('pageRight') ? 'pageRight' : edge('accept') ? 'accept' :
            edge('back') ? 'back' : navigate ? direction : '';
        if (navigate) ui.gamepadAt = time;
        if (button) xmbPress(button);
      }
    }
    ui.gamepadHeld = input;
    ui.gamepadDirection = direction;
  } else {
    ui.gamepadHeld = {};
    ui.gamepadDirection = '';
  }
  requestAnimationFrame(gamepadLoop);
}
function processNativePspFrame(input, now) {
  // The physical PSP buttons are mapped explicitly by Windows WinMM,
  // independent of Chromium's nonstandard Gamepad API mapping.
  const edge = (name) => !!input[name] && !ui.nativePadHeld[name];
  const direction = input.left ? 'left' : input.right ? 'right' :
    input.up ? 'up' : input.down ? 'down' : '';
  const navigate = direction &&
    (direction !== ui.nativePadDirection || now - ui.nativePadAt > 230);
  const dialog = Array.from(document.querySelectorAll('dialog[open]')).at(-1);
  if (!ui.busy && !ui.serviceOffline && !ui.state?.session?.running) {
    if (dialog) {
      if (edge('back')) {
        // Closing the progress dialog by Circle must not mask an active
        // verified copy. The user must choose its Cancelar copia action.
        if (!(dialog.id === 'transfer-dialog' && ui.transferInFlight)) dialog.close();
      } else if (navigate) {
        navigateDialogControl(dialog, direction);
        ui.nativePadAt = now;
      } else if (edge('accept')) {
        const target = document.activeElement;
        if (target?.tagName === 'BUTTON' && !target.disabled) target.click();
        else focusDialogControl(dialog, 1);
      }
    } else {
      // XMB: SELECT solo lleva a «Salir del Dock»; ✕ y «Sí» lo confirman.
      const button = edge('exit') ? 'select' : edge('settings') ? 'start' : edge('graphics') ? 'menu' :
        edge('pageLeft') ? 'pageLeft' : edge('pageRight') ? 'pageRight' : edge('accept') ? 'accept' :
          edge('back') ? 'back' : navigate ? direction : '';
      if (navigate) ui.nativePadAt = now;
      if (button) xmbPress(button);
    }
  }
  ui.nativePadHeld = input;
  ui.nativePadDirection = direction;
}
async function pollNativePspInput() {
  try {
    if (!document.hidden && !ui.serviceOffline && !ui.appClosing && !ui.appClosed) {
      const frame = await request('/api/controller-input');
      if (frame.connected === true && frame.source === 'psp_winmm') {
        const flowKey = ui.state?.product_flow?.enabled === true ?
          `${ui.state.product_flow.offer_id}:${ui.state.product_flow.phase}:${ui.state.controller_status?.phase}` : '';
        if (!ui.nativePadConnected || ui.nativePadFlowKey !== flowKey) {
          // First frame only establishes initial held inputs. This is
          // essential: holding CROSS while plugging in USB never opens
          // the first game or presses a preflight button.
          ui.nativePadHeld = frame;
          ui.nativePadDirection = frame.left ? 'left' : frame.right ? 'right' :
            frame.up ? 'up' : frame.down ? 'down' : '';
          ui.nativePadAt = Date.now();
          ui.gamepadId = '';
          ui.nativePadFlowKey = flowKey;
        } else {
          processNativePspFrame(frame, Date.now());
        }
        ui.nativePadConnected = true;
        ui.nativePadLastAt = Date.now();
        $('gamepad-status').textContent = 'Mando PSP conectado';
      } else if (ui.nativePadConnected) {
        ui.nativePadConnected = false;
        ui.nativePadHeld = {};
        ui.nativePadDirection = '';
        ui.gamepadId = '';
      }
    }
  } catch {
    ui.nativePadConnected = false;
    ui.nativePadHeld = {};
    ui.nativePadDirection = '';
    ui.gamepadId = '';
  } finally {
    if (!ui.appClosed) setTimeout(pollNativePspInput, ui.nativePadConnected ? 80 : 400);
  }
}
// ===================================================================
// XMB · modelo de navegación puro (sin DOM). Probado por
// tests/xmb_navigation_model.test.js. Secciones en horizontal,
// elementos en vertical, submenú, menú △ y confirmación Sí/No.
// ===================================================================
// ==== inicio del modelo de conexión puro ====
function dockLinkView(state) {
  const raw = state?.product_flow?.connection;
  const hasStatus = raw !== null && typeof raw === 'object';
  const stages = {
    waiting: ['Conecta tu PSP', 'Esperando el modo Dock', 'Abre PSP Dock en la consola y conecta el cable cuando indique LISTO.'],
    identifying: ['Identificando tu PSP', 'PSP detectada', 'Estamos comprobando la conexión con PSP Dock. Mantén el cable conectado.'],
    waiting_storage: ['Preparando la Memory Stick', 'Esperando acceso a tu biblioteca', 'La PSP está detectada. Windows está preparando el acceso a la Memory Stick.'],
    verified: ['PSP conectada', 'Conexión comprobada', 'Preparando tu sesión en este PC.'],
    blocked: ['No se pudo completar la conexión', 'La conexión necesita atención', 'La PSP todavía no está lista para iniciar la sesión.'],
  };
  const fallbackError = typeof state?.error === 'string' ? state.error.trim() : '';
  const phase = hasStatus ? Object.hasOwn(stages, raw.phase) ? raw.phase : 'blocked' : fallbackError ? 'blocked' : 'waiting';
  const value = (key, fallback = '') => typeof raw?.[key] === 'string' && raw[key].trim() ? raw[key].trim() : fallback;
  return {
    present: hasStatus || !!fallbackError, phase,
    title: stages[phase][0], status: stages[phase][1],
    message: value('message', stages[phase][2]), hint: value('hint'),
    diagnostics: value('diagnostics', fallbackError),
    retryable: raw?.retryable === true || !hasStatus && !!fallbackError,
    working: ['identifying', 'waiting_storage'].includes(phase),
    blocked: phase === 'blocked',
    // This is presentation only. It cannot grant a controller or file access.
    pending: hasStatus && ['identifying', 'waiting_storage', 'blocked'].includes(phase) || !!fallbackError && !hasStatus,
  };
}
function dockProgressView(flow, phase) {
  const raw = flow.progress || {};
  const permitted = {
    inventory: ['inventory'], syncing_in: ['inventory', 'saves', 'games', 'verifying'],
    copying_game: ['games', 'verifying'], syncing_out: ['inventory', 'saves', 'verifying'],
    finalizing: ['finalizing'],
  };
  const defaults = {
    entering_storage: 'Comprobando la conexión con tu biblioteca',
    inventory: 'Comparando juegos y guardados', syncing_in: 'Preparando el contenido elegido',
    entering_controller: 'Preparando el mando', waiting_storage_copy: 'Preparando el acceso al juego',
    copying_game: 'Preparando la copia del juego', returning_to_controller: 'Volviendo al modo mando',
    waiting_storage_return: 'Preparando tus guardados para la PSP', syncing_out: 'Comparando los guardados que han cambiado',
    finalizing: 'Esperando la liberación del dispositivo por Windows',
  };
  const current = raw.active === true && typeof raw.operation_id === 'string' && !!raw.operation_id.trim() &&
    (permitted[phase] || []).includes(raw.stage);
  const unit = ['bytes', 'items'].includes(raw.unit) ? raw.unit : null;
  const validCompleted = Number.isSafeInteger(raw.completed) && raw.completed >= 0;
  const determinate = current && raw.mode === 'determinate' && unit !== null && validCompleted &&
    Number.isSafeInteger(raw.total) && raw.total > 0;
  const total = determinate ? raw.total : null;
  const completed = current && validCompleted ? Math.min(raw.completed, total ?? raw.completed) : 0;
  const text = (key) => current && typeof raw[key] === 'string' ? raw[key].trim() : '';
  return {
    visible: Object.hasOwn(defaults, phase), active: current, determinate,
    operationId: current ? raw.operation_id : '', stage: current ? raw.stage : phase,
    unit, total, completed, percent: determinate ? Math.floor(completed / total * 100) : null,
    label: text('label') || (current && raw.stage === 'verifying' ? 'Verificando los datos copiados' : defaults[phase] || ''),
    currentItem: text('current_item'),
  };
}
function dockProductFlowView(state, options = {}) {
  const flow = state?.product_flow || {};
  const enabled = flow.enabled === true;
  const link = dockLinkView(state);
  const stages = {
    waiting_psp: ['Conecta tu PSP', 'PREPARANDO TU SESIÓN'],
    entering_storage: ['Preparando tu PSP', 'CONECTANDO TU BIBLIOTECA'],
    inventory: ['Sincronizando PSP', 'COMPARANDO TU BIBLIOTECA'],
    choosing: ['¿Qué quieres sincronizar ahora?', 'TU SESIÓN, A TU MEDIDA'],
    syncing_in: ['Sincronizando PSP', 'PREPARANDO TU SESIÓN'],
    entering_controller: ['Preparando el mando', 'TU BIBLIOTECA ESTÁ LISTA'],
    ready: ['PSP Dock listo', 'ELIGE TU PRÓXIMA PARTIDA'],
    waiting_storage_copy: ['Preparando la copia', 'CONECTANDO TU BIBLIOTECA'],
    copying_game: ['Copiando al Dock', 'TU JUEGO, EN ESTE PC'],
    returning_to_controller: ['Preparando el mando', 'COPIA COMPLETADA'],
    waiting_game_exit: ['Terminando la sesión', 'CIERRA EL EMULADOR PARA CONTINUAR'],
    waiting_storage_return: ['Preparando la sincronización', 'DEVOLVIENDO TUS GUARDADOS'],
    syncing_out: ['Sincronizando PSP', 'DEVOLVIENDO TUS GUARDADOS'],
    finalizing: ['Finalizando la conexión', 'GUARDADOS COMPROBADOS'],
    safe_to_disconnect: ['Todo listo', 'SESIÓN FINALIZADA'],
    attention: ['Revisa tu sesión', 'PSP DOCK NECESITA TU ATENCIÓN'],
  };
  // Unknown/missing phases cannot expose the menu or copy choices.
  const phase = Object.hasOwn(stages, flow.phase) ? flow.phase : 'attention';
  const connectionInterrupted = enabled && phase === 'ready' && link.present && link.phase !== 'verified';
  const visible = enabled && !options.closed &&
    (options.serviceOffline === true || connectionInterrupted || !['ready', 'waiting_psp'].includes(phase));
  const online = !options.serviceOffline && !options.closed;
  const actionable = enabled && online && !options.busy && !connectionInterrupted;
  const offerId = typeof flow.offer_id === 'string' ? flow.offer_id : '';
  const count = (key) => Number.isSafeInteger(flow.counts?.[key]) && flow.counts[key] >= 0 ? flow.counts[key] : null;
  const progress = dockProgressView(flow, phase);
  const copying = ['syncing_in', 'copying_game', 'syncing_out'].includes(phase);
  const automatic = flow.capabilities?.automatic_transition === true;
  const canceling = flow.purpose === 'cancel';
  const title = options.serviceOffline ? 'Esperando a la aplicación' : connectionInterrupted ? link.title : stages[phase][0];
  let message = phase === 'choosing' ? '¿Quieres copiar todos los juegos y sincronizar toda la PSP ahora?' :
    typeof flow.message === 'string' && flow.message.trim() ? flow.message : {
    entering_storage: 'Estamos conectando la PSP para comparar juegos y guardados.',
    inventory: 'Comprobando qué tienes en la PSP y qué está disponible en este PC.',
    choosing: '¿Quieres copiar todos los juegos y sincronizar toda la PSP ahora?',
    syncing_in: 'Preparando únicamente lo que has elegido para esta sesión.',
    entering_controller: automatic ? 'La PSP pasará al modo mando cuando termine la preparación.' : 'Sigue las indicaciones de la PSP para activar el modo mando.',
    waiting_storage_copy: automatic ? 'Preparando el acceso a la PSP para copiar el juego.' : 'Sigue las indicaciones de la PSP para activar el almacenamiento.',
    copying_game: 'La navegación está pausada mientras se copia y verifica el juego.',
    returning_to_controller: automatic ? 'Volviendo al modo mando para continuar en el Dock.' : 'Sigue las indicaciones de la PSP para volver al modo mando.',
    waiting_game_exit: 'Has pedido salir. Cierra el emulador normalmente para devolver las partidas.',
    waiting_storage_return: automatic ? 'Preparando la devolución de partidas y estados compatibles.' : 'Sigue las indicaciones de la PSP para activar el almacenamiento y devolver tus guardados.',
    syncing_out: 'Copiando a la PSP las partidas y los estados compatibles que han cambiado.',
    finalizing: 'Preparando la desconexión de la PSP. Mantén el cable conectado hasta que termine.',
    safe_to_disconnect: flow.can_close === true ? (canceling ? 'Conexión cerrada. Ya puedes desconectar la PSP.' : 'Tus guardados compatibles están comprobados. Ya puedes desconectar la PSP.') : 'Terminando las comprobaciones antes de desconectar.',
    attention: 'Revisa el aviso antes de continuar con la sesión.',
  }[phase] || '';
  if (phase === 'attention') message = flow.can_review_saves === true ?
    'Hay guardados que necesitan tu elección. Revísalos antes de continuar.' :
    'No se ha podido completar esta etapa. Comprueba la conexión y revisa el aviso antes de continuar.';
  if (canceling && phase === 'safe_to_disconnect') message = flow.can_close === true ?
    'Conexión cerrada. Ya puedes desconectar la PSP.' : 'Terminando las comprobaciones antes de desconectar.';
  if (connectionInterrupted) message = [link.message, link.hint].filter(Boolean).join(' ');
  if (options.serviceOffline) message = 'La conexión con PSP Dock se ha interrumpido. Espera a que se recupere para comprobar el resultado.';
  const linkAvailable = !link.present || link.phase === 'verified';
  const canChoose = actionable && linkAvailable && phase === 'choosing' && flow.can_choose === true && !!offerId;
  const canReview = actionable && linkAvailable && phase === 'attention' && flow.can_review_saves === true;
  const canRetry = actionable && linkAvailable && phase === 'attention' && flow.can_retry === true && !!offerId;
  const canClose = actionable && phase === 'safe_to_disconnect' && flow.can_close === true;
  let note = '';
  if (phase === 'choosing') note = flow.capabilities?.controller_during_storage === true && state?.controller_status?.connected === true ?
    'Elige con la cruceta y confirma con ✕, o utiliza el teclado y el ratón. Los conflictos se revisan antes de reemplazar una partida.' :
    'Elige con el teclado o el ratón de este PC. Los conflictos se revisan antes de reemplazar una partida.';
  else if (['waiting_storage_return', 'syncing_out'].includes(phase)) note = 'La salida sólo sincroniza partidas y estados compatibles. Los juegos permanecen en el PC.';
  else if (copying) note = 'Mantén la PSP conectada hasta que termine la comprobación.';
  const closeNote = typeof flow.window_close_message === 'string' ? flow.window_close_message.trim() : '';
  if (closeNote && closeNote !== message) note = [note, closeNote].filter(Boolean).join(' ');
  return {
    enabled, phase, visible, blocking: enabled && (phase !== 'ready' || !online || connectionInterrupted),
    usePcSaves: enabled && online && !connectionInterrupted && phase === 'ready' && flow.use_pc_saves === true && ['games', 'none'].includes(flow.choice),
    title, kicker: canceling ? 'CERRANDO LA CONEXIÓN' : stages[phase][1], message, note, offerId,
    choosing: phase === 'choosing', canChoose, canReview, canRetry, canClose,
    counts: { psp: count('psp_games'), pc: count('pc_games'), missing: count('missing_games'), saves: count('save_actions'), conflicts: count('save_conflicts') },
    showCounts: ['choosing', 'syncing_in', 'entering_controller'].includes(phase),
    showProgress: online && !connectionInterrupted && progress.visible,
    progress,
    working: online && !['choosing', 'attention', 'safe_to_disconnect', 'waiting_psp', 'waiting_game_exit', 'ready'].includes(phase),
    diagnostics: [typeof flow.attention_reason === 'string' ? flow.attention_reason : '',
      connectionInterrupted || phase === 'attention' ? link.diagnostics : ''].filter(Boolean).join('\n'),
    unsupportedStatesNote: state?.retro?.emulators?.length ? 'Los estados instantáneos sin compatibilidad verificada se conservan en cada dispositivo; no se convierten automáticamente. Las partidas internas compatibles se sincronizan por separado.' : '',
    showRefresh: options.serviceOffline === true || phase === 'attention' || connectionInterrupted && link.retryable, automatic,
  };
}
function dockRetroSaveChoices(row, returning = false) {
  if (!row || typeof row.revision !== 'string' || !row.revision ||
      !['psp_to_pc', 'pc_to_psp', 'conflict'].includes(row.status)) return [];
  const choices = [];
  if (!returning && ['psp_to_pc', 'conflict'].includes(row.status) && row.psp_present === true) choices.push('psp');
  if (['pc_to_psp', 'conflict'].includes(row.status) && row.pc_present === true) choices.push('pc');
  if (returning && !row.skipped) choices.push('skip');
  return choices;
}
function dockConnectionView(state, options = {}) {
  const current = state || {};
  const controller = current.controller_status || {};
  const session = current.session || {};
  const demo = current.demo === true;
  const product = dockProductFlowView(current, options);
  const link = dockLinkView(current);
  const diagnostic = controller.connected === true && controller.mode === 'msc_diagnostic';
  const nativeIdentity = controller.connected === true && controller.mode === 'native_dock' &&
    controller.product_id === 0x01cd && controller.protocol_version === 3 && controller.firmware_revision === 0x0302 &&
    controller.host_confirmed === true && controller.synthetic_storage === false && (!link.present || link.phase === 'verified');
  const nativeController = nativeIdentity && controller.phase === 2 && product.enabled && product.phase === 'ready';
  const stable = controller.connected === true && controller.mode === 'hid_manual' || nativeController;
  const storage = !demo && !options.serviceOffline && controller.mode === 'usb_storage' &&
    controller.connected !== true && typeof current.selected_device === 'string' && current.selected_device.trim().length > 0;
  const storageSetup = storage && !link.pending && options.storageSetupRequested === true && !options.closed;
  const transfer = options.transferInFlight === true || current.transfer_status?.active === true ||
    ['waiting_for_storage', 'copying'].includes(current.copy_queue?.status);
  const protectedWork = !!(session.running || session.pending_return || session.exit_requested ||
    current.recovery_required || transfer || options.busy || options.textureActive ||
    current.copy_queue?.status === 'interrupted_review');
  const connected = !options.serviceOffline && (stable || diagnostic);
  const waiting = options.closed === true ||
    !product.visible && (product.enabled && product.phase === 'waiting_psp' ||
      link.pending && link.phase !== 'waiting' && !protectedWork && !demo || !(demo || connected || protectedWork || storageSetup));
  const kind = options.closed ? 'closed' : options.serviceOffline ? 'offline' : !state ? 'loading' :
    link.present && ['identifying', 'waiting_storage', 'blocked'].includes(link.phase) ? link.phase :
    controller.mode === 'usb_storage' || current.selected_device ? 'storage' : 'connect';
  const copy = {
    loading: ['Comprobando conexión…', 'Puedes conectar la consola en cualquier momento.'],
    connect: ['Esperando el modo Dock', 'Si el cable ya está conectado, abre PSP Dock en la consola.'],
    storage: ['PSP conectada en almacenamiento USB', storage ?
      'Puedes preparar la biblioteca aquí, o salir del modo USB de Sony y abrir PSP Dock en la consola.' :
      'Sal del modo USB de Sony y abre PSP Dock en la consola para entrar al menú.'],
    offline: ['La app no puede actualizar la conexión', 'Espera un momento. Si no se recupera, cierra esta ventana y vuelve a abrir PSP Dock en Windows.'],
    closed: ['Esta ventana ya está desconectada', 'Puedes cerrar esta ventana.'],
    identifying: [link.status, link.message],
    waiting_storage: [link.status, link.message],
    blocked: [link.status, link.message],
  }[kind];
  const partial = ['identifying', 'waiting_storage', 'blocked'].includes(kind);
  return {
    waiting, screen: waiting ? 'waiting' : product.visible ? 'product' : 'menu', kind, diagnostic: diagnostic && !demo,
    product, nativeController,
    canPrepareStorage: storage && !link.pending && !options.closed, storageSetup,
    canPlay: !demo && !options.serviceOffline && !options.closed && stable && controller.can_play === true && !product.blocking &&
      !(partial && waiting),
    protectedWork, status: copy[0], detail: copy[1],
    title: kind === 'closed' ? 'Hasta la próxima' : partial ? link.title : kind === 'offline' ? 'Esperando a la aplicación' : 'Conecta tu PSP',
    intro: kind === 'closed' ? 'Tus datos se conservan en el PC.' : partial ?
      'La sesión empezará cuando Windows termine de comprobar la conexión.' : 'Abre Dock en la consola para preparar tu sesión.',
    hint: partial ? link.hint : '', diagnostics: partial ? link.diagnostics : '',
    working: kind === 'loading' || partial && link.working,
    showSteps: !partial && !['closed', 'offline'].includes(kind),
    canRefresh: !options.closed && (kind === 'offline' || partial && link.retryable),
  };
}
// ==== fin del modelo de conexión puro ====

function xmbStep(nav, view, button) {
  const next = { ...nav, idx: { ...nav.idx } };
  const clamp = (value, count) => Math.max(0, Math.min(Math.max(1, count) - 1, value));
  const done = (effect = null) => ({ nav: next, effect });
  if (view.waiting === true) return done();
  if (next.confirm) {
    if (button === 'left' || button === 'right' || button === 'up' || button === 'down')
      next.confirm = { yes: !next.confirm.yes };
    else if (button === 'accept') { const yes = next.confirm.yes; next.confirm = null; return done({ type: 'confirm', yes }); }
    else if (button === 'back') { next.confirm = null; return done({ type: 'confirm', yes: false }); }
    return done();
  }
  if (next.side) {
    const count = Math.max(1, view.sideCount || 0);
    if (button === 'up') next.side = { i: (next.side.i - 1 + count) % count };
    else if (button === 'down') next.side = { i: (next.side.i + 1) % count };
    else if (button === 'accept') { const index = next.side.i; next.side = null; return done({ type: 'pick', index }); }
    else if (button === 'back' || button === 'menu' || button === 'left') next.side = null;
    return done();
  }
  const sections = view.sections || [];
  const games = sections.findIndex((section) => section.id === 'juegos');
  if (button === 'start') {
    next.level = 'list'; next.subIdx = 0;
    if (games >= 0) next.cat = games;
    return done();
  }
  if (next.level === 'sub') {
    const count = view.subCount || 0;
    if (button === 'up') next.subIdx = clamp(next.subIdx - 1, count);
    else if (button === 'down') next.subIdx = clamp(next.subIdx + 1, count);
    else if (button === 'pageLeft') next.subIdx = clamp(next.subIdx - 5, count);
    else if (button === 'pageRight') next.subIdx = clamp(next.subIdx + 5, count);
    else if (button === 'back' || button === 'left') { next.level = 'list'; next.subIdx = 0; }
    else if ((button === 'accept' || button === 'right') && count) return done({ type: 'row', index: next.subIdx });
    return done();
  }
  const section = sections[next.cat];
  const count = section?.count || 0;
  const current = clamp(next.idx[section?.id] || 0, count);
  if (button === 'left') next.cat = clamp(next.cat - 1, sections.length);
  else if (button === 'right') next.cat = clamp(next.cat + 1, sections.length);
  else if (section && button === 'up') next.idx[section.id] = clamp(current - 1, count);
  else if (section && button === 'down') next.idx[section.id] = clamp(current + 1, count);
  else if (section && button === 'pageLeft') next.idx[section.id] = clamp(current - 5, count);
  else if (section && button === 'pageRight') next.idx[section.id] = clamp(current + 5, count);
  else if (section && count && button === 'accept') return done({ type: 'activate', section: section.id, index: current });
  else if (section && count && button === 'menu') return done({ type: 'menu', section: section.id, index: current });
  else if (button === 'select') return done({ type: 'select' });
  return done();
}
function xmbMonthColors(month, hour) {
  // Aproximación del tema «Original» de la PSP: un color por mes y
  // más oscuro de noche, más claro a primera hora de la tarde.
  const base = ['#a9adb4', '#c9a83a', '#7fae3f', '#d9799e', '#3f9a50', '#8c62bf',
    '#2aa0a6', '#2f6fd0', '#5a4ab3', '#cf8a28', '#8a5a34', '#b3302d'][((month % 12) + 12) % 12];
  const light = .45 + .55 * Math.max(0, Math.cos((hour - 13) / 24 * 2 * Math.PI)) ** .7;
  const shade = (hex, factor) => {
    const n = parseInt(hex.slice(1), 16);
    return '#' + [n >> 16, (n >> 8) & 255, n & 255]
      .map((v) => Math.max(0, Math.min(255, Math.round(v * factor))).toString(16).padStart(2, '0')).join('');
  };
  return { top: shade(base, .95 * light + .1), bottom: shade(base, .28 * light + .04) };
}
// ==== fin del modelo puro ====

const XMB_SVG = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const XMB_ICONS = {
  ajustes: XMB_SVG('<path d="M3.5 9.5h17v9.5h-17z"/><path d="M8.5 9.5V7a1.5 1.5 0 0 1 1.5-1.5h4A1.5 1.5 0 0 1 15.5 7v2.5"/><path d="M3.5 13.5h17"/><path d="M10.5 12.5h3v2h-3z"/>'),
  dock: XMB_SVG('<path d="M12 2.8v15.5"/><path d="M9.2 5.8L12 2.8l2.8 3"/><path d="M12 12.5l-4.3-3V7.4"/><circle cx="7.7" cy="6.6" r=".9"/><path d="M12 14.5l4.3-2.8V9.6"/><path d="M15.4 8.4h1.8v1.8h-1.8z"/><circle cx="12" cy="19.6" r="1.7"/>'),
  juegos: XMB_SVG('<path d="M7.3 7.5h9.4c2.2 0 3.6 1.4 4.2 3.6l1 4.4c.4 1.9-1.6 3.2-3 1.9L16.5 15h-9L5.1 17.4c-1.4 1.3-3.4 0-3-1.9l1-4.4C3.7 8.9 5.1 7.5 7.3 7.5z"/><path d="M7.2 9.9v3.4M5.5 11.6h3.4"/><circle cx="16.2" cy="10.3" r=".55"/><circle cx="17.9" cy="12" r=".55"/><circle cx="14.5" cy="12" r=".55"/><circle cx="16.2" cy="13.7" r=".55"/>'),
  retro: XMB_SVG('<rect x="2.5" y="6" width="19" height="12" rx="3"/><path d="M6.5 9.5v5M4 12h5M15.5 10.5h.01M18 13.5h.01"/><path d="M9 6V4h6v2"/>'),
  partidas: XMB_SVG('<path d="M7 3.5h10a1 1 0 0 1 1 1V20a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 6 20V4.5a1 1 0 0 1 1-1z"/><path d="M8.5 3.5v3.5M10.5 3.5v3.5M12.5 3.5v3.5M14.5 3.5v3.5"/><path d="M8.5 11.5h7v5.5h-7z"/>'),
  graficos: XMB_SVG('<path d="M3 5h18v11.5H3z"/><path d="M9 20h6M12 16.5V20"/><path d="M12 7.6l.9 2 2.2.2-1.7 1.5.5 2.2-1.9-1.1-1.9 1.1.5-2.2-1.7-1.5 2.2-.2z"/>'),
  play: XMB_SVG('<path d="M8 5l11 7-11 7z"/>'),
  copy: XMB_SVG('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
  info: XMB_SVG('<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8v.01"/>'),
  sync: XMB_SVG('<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>'),
  exit: XMB_SVG('<path d="M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10"/>'),
  folder: XMB_SVG('<path d="M3 7h6l2 2h10v10H3z"/>'),
  pad: XMB_SVG('<rect x="3" y="8" width="18" height="9" rx="4.5"/><path d="M7 11v3M5.5 12.5h3"/>'),
  search: XMB_SVG('<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>'),
  reset: XMB_SVG('<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>'),
  hd: XMB_SVG('<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 9v6M7 12h3M10 9v6M14 9v6h1.5a3 3 0 0 0 0-6z"/>'),
  theme: XMB_SVG('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z"/>'),
  warn: XMB_SVG('<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.01"/>'),
};
const XMB_SECTIONS = [['ajustes', 'Ajustes'], ['dock', 'Dock'], ['juegos', 'Juegos'], ['retro', 'Retro'], ['partidas', 'Partidas'], ['graficos', 'Gráficos']];
const XMB_GFX = {
  quality: [['auto', 'Automático · según el monitor'], ['balanced', 'Equilibrado · menos carga'], ['quality', 'Calidad · más resolución'],
    ['ultra', 'Ultra · sobremuestreo'], ['manual', 'Manual · escala de Ajustes']],
  aspect: [['fit', 'Original · sin deformar'], ['stretch', 'Estirar a toda la pantalla']],
  texture: [['off', 'Originales'], ['xbrz2', 'xBRZ 2× (CPU)'], ['xbrz3', 'xBRZ 3× (CPU)'], ['xbrz4', 'xBRZ 4× (CPU)'], ['bicubic2', 'Bicubic 2× (CPU)']],
};
const XMB_GFX_NAMES = { quality: 'Calidad de renderizado', aspect: 'Proporción de pantalla', texture: 'Mejorar texturas' };
const xmb = {
  nav: { cat: 2, idx: {}, level: 'list', subIdx: 0, side: null, confirm: null },
  sections: [], sub: null, side: null, confirm: null,
  gfx: {}, gfxLoading: new Set(), settings: null, els: new Map(), icons: new Map(),
  theme: 'month', mediaGame: null, mediaTimer: null, mediaSound: true,
};
try { xmb.theme = localStorage.getItem('psp-dock-theme') || 'month'; } catch { /* sin almacenamiento */ }
try { xmb.mediaSound = localStorage.getItem('psp-dock-xmb-sound') !== 'off'; } catch { /* sin almacenamiento */ }

function xmbConnectionView() {
  const connection = dockConnectionView(ui.state, {
    serviceOffline: ui.serviceOffline, transferInFlight: ui.transferInFlight,
    busy: ui.busy, textureActive: ui.textureProgress?.active, closed: ui.appClosed,
    storageSetupRequested: ui.storageSetupRequested,
    transferStatus: ui.transferStatus,
  });
  // Opt-in belongs to the current storage connection. A disconnect or
  // switch into HID must not reopen preparation on the next USB arrival.
  if (!connection.canPrepareStorage) ui.storageSetupRequested = false;
  return connection;
}
function requestStorageSetup() {
  const connection = xmbConnectionView();
  if (!connection.waiting || !connection.canPrepareStorage || ui.busy || ui.appClosing || ui.appClosed) return;
  ui.storageSetupRequested = true;
  xmb.nav = { ...xmb.nav, cat: 1, level: 'list', subIdx: 0, side: null, confirm: null };
  render();
}
function xmbRenderConnection(connection) {
  const waiting = $('dock-connect');
  const changed = waiting.hidden === connection.waiting;
  waiting.hidden = !connection.waiting;
  const productVisible = connection.product?.visible === true;
  renderProductFlow(connection.product);
  const menu = $('xmb-menu');
  menu.hidden = connection.waiting || productVisible;
  menu.inert = connection.waiting || productVisible;
  menu.setAttribute('aria-hidden', String(menu.hidden));
  $('xmb').classList.toggle('waiting', connection.waiting || productVisible);
  $('xmb').classList.toggle('has-diagnostic', connection.diagnostic && !menu.hidden);
  $('dock-diagnostic').hidden = !connection.diagnostic || menu.hidden;
  $('dock-connect-status').dataset.kind = connection.kind;
  $('dock-connect-status-title').textContent = connection.status;
  $('dock-connect-status-detail').textContent = connection.detail;
  $('dock-connect-prepare').hidden = !connection.canPrepareStorage || !connection.waiting;
  $('dock-connect-prepare').disabled = ui.appClosing || ui.appClosed;
  $('dock-connect-title').textContent = connection.title;
  $('dock-connect-intro').textContent = connection.intro;
  $('dock-connect-steps').hidden = !connection.showSteps;
  $('dock-connect-hint').textContent = connection.hint;
  $('dock-connect-hint').hidden = !connection.hint;
  $('dock-connect-progress').hidden = !connection.working;
  waiting.setAttribute('aria-busy', String(connection.working || ui.appClosing));
  $('dock-connect-refresh').hidden = !connection.canRefresh;
  $('dock-connect-refresh').disabled = ui.appClosing || ui.appClosed;
  $('dock-connect-footer').textContent = ui.appClosing ? 'Cerrando PSP Dock…' : ui.appClosed ? 'La app ya no está esperando conexiones.' :
    connection.working ? 'Comprobando la conexión. Todavía no se están copiando archivos.' :
    connection.kind === 'blocked' ? 'La sesión todavía no ha comenzado.' : 'La app está esperando tu PSP.';
  $('dock-connect-error').hidden = !ui.connectionRequestError;
  $('dock-connect-error').textContent = ui.connectionRequestError ? 'No se pudo cerrar la aplicación. Puedes intentarlo de nuevo.' : '';
  renderDockDiagnostics('dock-connect', [connection.diagnostics, ui.connectionRequestError].filter(Boolean).join('\n'));
  $('dock-connect-close').disabled = ui.appClosing || ui.appClosed;
  if (connection.waiting) {
    xmbPreviewGame(null);
    if (changed) {
      // No active operation reaches this branch. Discard transient
      // confirmations and dialogs so reconnecting cannot accept one.
      xmb.nav = { ...xmb.nav, level: 'list', subIdx: 0, side: null, confirm: null };
      xmb.sub = null; xmb.side = null; xmb.confirm = null;
      document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
      waiting.focus({ preventScroll: true });
    }
  } else if (changed && !productVisible) {
    $('dock-connect-error').hidden = true;
    $('xmb').focus({ preventScroll: true });
  }
}

function renderDockDiagnostics(prefix, text) {
  const details = $(prefix + '-diagnostics');
  details.hidden = !text;
  $(prefix + '-diagnostics-text').textContent = text;
  if (!text) details.open = false;
}

function renderProductFlow(product) {
  const panel = $('dock-session');
  const wasVisible = !panel.hidden;
  panel.hidden = product?.visible !== true;
  if (panel.hidden) {
    if (wasVisible) $('xmb').focus({ preventScroll: true });
    ui.productFlowKey = '';
    return;
  }
  const key = `${product.phase}:${product.offerId}`;
  const changed = ui.productFlowKey !== key;
  if (changed) {
    ui.productFlowKey = key;
    ui.productRequestError = '';
    xmb.nav = { ...xmb.nav, level: 'list', subIdx: 0, side: null, confirm: null };
    xmb.sub = null; xmb.side = null; xmb.confirm = null;
    // The conflict dialogs remain useful in attention. Every other phase
    // discards an old dialog before its controls can act on a new session.
    document.querySelectorAll('dialog[open]').forEach((dialog) => {
      if (!(product.phase === 'attention' && ['saves-dialog', 'resolve-dialog'].includes(dialog.id))) dialog.close();
    });
  }
  xmbPreviewGame(null);
  $('dock-session-title').textContent = product.title;
  $('dock-session-kicker').textContent = product.kicker;
  $('dock-session-message').textContent = product.message;
  $('dock-session-note').textContent = product.note;
  $('dock-session-note').hidden = !product.note;
  $('dock-session-unsupported').textContent = product.unsupportedStatesNote;
  $('dock-session-unsupported').hidden = !product.unsupportedStatesNote;
  $('dock-session-spinner').hidden = !product.working;
  panel.setAttribute('aria-busy', String(product.working || ui.busy));
  $('dock-session-counts').hidden = !product.showCounts;
  const displayCount = (value) => value === null ? '—' : String(value);
  $('dock-session-psp-count').textContent = displayCount(product.counts.psp);
  $('dock-session-pc-count').textContent = displayCount(product.counts.pc);
  $('dock-session-missing-count').textContent = displayCount(product.counts.missing);
  $('dock-session-save-count').textContent = displayCount(product.counts.saves) +
    (product.counts.conflicts ? ` · ${product.counts.conflicts} por revisar` : '');
  $('dock-session-choices').hidden = !product.choosing;
  panel.querySelectorAll('[data-session-choice]').forEach((button) => { button.disabled = !product.canChoose; });
  $('dock-session-review').hidden = !product.canReview;
  $('dock-session-retry').hidden = !product.canRetry;
  $('dock-session-refresh').hidden = !product.showRefresh;
  $('dock-session-refresh').disabled = ui.busy;
  $('dock-session-close').hidden = !product.canClose;
  $('dock-session-close').disabled = ui.appClosing || ui.appClosed;
  $('dock-session-error').textContent = ui.productRequestError ?
    'No se pudo completar la solicitud. Comprueba el estado antes de reintentar.' : '';
  $('dock-session-error').hidden = !ui.productRequestError;
  renderDockDiagnostics('dock-session', [product.diagnostics, ui.productRequestError].filter(Boolean).join('\n'));
  $('dock-session-progress').hidden = !product.showProgress;
  const progress = $('dock-session-progress-bar');
  if (product.progress.determinate) {
    progress.max = product.progress.total;
    progress.value = product.progress.completed;
  } else {
    progress.removeAttribute('value');
    progress.removeAttribute('max');
  }
  $('dock-session-progress-label').textContent = product.progress.label;
  $('dock-session-progress-percent').textContent = product.progress.determinate ? `${product.progress.percent} %` : '';
  $('dock-session-progress-percent').hidden = !product.progress.determinate;
  const amount = product.progress.unit === 'bytes' ? bytes : (value) => String(value);
  $('dock-session-progress-detail').textContent = product.progress.determinate ?
    `${amount(product.progress.completed)} de ${amount(product.progress.total)}${product.progress.unit === 'items' ? ' elementos comprobados' : ''}` :
    product.progress.active && product.progress.unit === 'items' && product.progress.completed > 0 ?
      `${product.progress.completed} elementos comprobados · calculando el total` :
      product.progress.stage === 'finalizing' ? 'Mantén la PSP conectada hasta que aparezca el aviso de desconexión.' :
        'Espera mientras se completa esta comprobación.';
  $('dock-session-progress-item').textContent = product.progress.currentItem;
  $('dock-session-progress-item').hidden = !product.progress.currentItem;
  if (changed && !document.querySelector('dialog[open]')) {
    const target = product.canChoose ? panel.querySelector('[data-session-choice="none"]') :
      product.canReview ? $('dock-session-review') : product.canRetry ? $('dock-session-retry') :
        product.canClose ? $('dock-session-close') : panel;
    target.focus({ preventScroll: true });
  }
}
async function chooseProductSession(choice) {
  const product = xmbConnectionView().product;
  if (!product?.canChoose || !['all', 'saves', 'games', 'none'].includes(choice)) return;
  ui.productRequestError = '';
  // Capture this exact offer. The backend rejects a choice if the PSP or
  // inventory changes before the request reaches it.
  const operation = mutate('/api/session-choice', { choice, offer_id: product.offerId });
  xmbRender();
  await operation;
}
async function retryProductSession() {
  const product = xmbConnectionView().product;
  if (!product?.canRetry) return;
  ui.productRequestError = '';
  const operation = mutate('/api/session-retry', { offer_id: product.offerId });
  xmbRender();
  await operation;
}
function reviewProductSaves() {
  if (!xmbConnectionView().product?.canReview) return;
  renderSaves();
  if (!$('saves-dialog').open) $('saves-dialog').showModal();
}
function productFlowPress(button) {
  const product = xmbConnectionView().product;
  if (!product?.visible || ui.busy || ui.serviceOffline || document.querySelector('dialog[open]')) return;
  const panel = $('dock-session');
  const controls = Array.from(panel.querySelectorAll('button:not(:disabled), summary'))
    .filter((node) => !node.hidden && node.getClientRects().length);
  if (!controls.length) return;
  if (button === 'back') {
    const diagnostics = $('dock-session-diagnostics');
    if (diagnostics?.open) { diagnostics.open = false; diagnostics.querySelector('summary').focus(); return; }
    // Circle never submits a skip choice or acknowledges successful exit.
    if (product.canChoose) panel.querySelector('[data-session-choice="none"]').focus();
    return;
  }
  const index = controls.indexOf(document.activeElement);
  if (['left', 'right', 'up', 'down'].includes(button)) {
    const delta = ['left', 'up'].includes(button) ? -1 : 1;
    controls[(index < 0 ? delta > 0 ? 0 : controls.length - 1 : (index + delta + controls.length) % controls.length)].focus();
  } else if (button === 'accept') {
    if (index >= 0) controls[index].click();
    else controls[controls.length - 1].focus();
  }
}

function xmbPreviewGame(gameId) {
  if (xmb.mediaGame === gameId) return;
  xmb.mediaGame = gameId;
  clearTimeout(xmb.mediaTimer);
  const bg = $('xmb-game-background'), overlay = $('xmb-game-overlay');
  const sound = $('xmb-game-audio');
  sound.pause();
  sound.removeAttribute('src');
  sound.load();
  bg.hidden = true; bg.removeAttribute('src');
  overlay.hidden = true; overlay.removeAttribute('src');
  if (!gameId || ui.serviceOffline) return;
  // Debounce fast D-pad movement. Never request all 35 games at once.
  xmb.mediaTimer = setTimeout(() => {
    if (xmb.mediaGame !== gameId || document.hidden || ui.state?.session?.running) return;
    const uri = '/api/media/' + encodeURIComponent(gameId) + '/';
    for (const [kind,element] of [['background',bg],['overlay',overlay]]) {
      element.onload = () => { if (xmb.mediaGame === gameId) element.hidden = false; };
      element.onerror = () => { element.hidden = true; };
      element.src = uri + kind;
    }
    if (xmb.mediaSound) {
      sound.volume = .22;
      sound.loop = true;
      sound.src = uri + 'sound';
      void sound.play().catch(() => {
        // Chrome may require a click before browser audio is unlocked.
        // Never simulate keyboard input or weaken global browser policy.
      });
    }
  }, 350);
}

function xmbText(id) { return ($(id)?.textContent || '').trim(); }
function xmbUsable(id) { const node = $(id); return !!node && !node.hidden && !node.disabled; }
function xmbGfxLabel(key, value) { return (XMB_GFX[key].find(([v]) => v === value) || [value, value])[1]; }
function xmbGfxSummary(choice) {
  if (!choice) return 'Cargando perfil…';
  return [xmbGfxLabel('quality', choice.quality).split(' · ')[0], choice.aspect === 'stretch' ? 'Estirada' : 'Original',
    choice.texture && choice.texture !== 'off' ? xmbGfxLabel('texture', choice.texture).replace(' (CPU)', '') : null]
    .filter(Boolean).join(' · ');
}
function xmbGfxKey(id) { return id || 'default'; }
async function xmbLoadGraphics(id, force = false) {
  const key = xmbGfxKey(id);
  if (xmb.gfxLoading.has(key) || (!force && xmb.gfx[key])) return;
  xmb.gfxLoading.add(key);
  try {
    xmb.gfx[key] = await request(id ? '/api/graphics/' + encodeURIComponent(id) : '/api/graphics');
  } catch (error) {
    xmb.gfx[key] = { error: error.message };
  } finally {
    xmb.gfxLoading.delete(key);
    xmbRender();
  }
}
async function xmbSaveGraphics(id, body, message) {
  const ok = await mutate('/api/graphics', id ? { id, ...body } : body, message);
  if (ok) { await xmbLoadGraphics(id, true); if (!id) xmb.gfx = { default: xmb.gfx.default }; }
  return ok;
}
async function xmbLoadSettings() {
  try { xmb.settings = await request('/api/settings'); } catch { xmb.settings = null; }
  xmbRender();
}
function xmbSettingsLocked() {
  const state = ui.state || {};
  return state.recovery_required ? 'Recupera la sesión interrumpida antes de cambiar los ajustes.' :
    state.session?.running || state.session?.pending_return ? 'Cierra y devuelve la sesión a la PSP antes de cambiar los ajustes.' : '';
}
async function xmbStorageLocation() {
  const locked=xmbSettingsLocked();
  if (locked) { toast(locked); return; }
  if (!xmb.settings) await xmbLoadSettings();
  if (!xmb.settings) return;
  $('storage-location-current').textContent = 'Actualmente: ' + (xmb.settings.storage_root || xmb.settings.library);
  $('storage-location-path').value = '';
  $('storage-location-error').hidden = true;
  $('storage-location-dialog').showModal();
}
async function xmbSaveSetting(key, value, label) {
  const locked = xmbSettingsLocked();
  if (locked) { toast(locked); return; }
  if (!xmb.settings) await xmbLoadSettings();
  if (!xmb.settings) { toast('No se pudieron leer los ajustes actuales.'); return; }
  const fields = {};
  for (const name of ['emulator', 'retroarch', 'retro_install_policy', 'retro_button_layout', 'library', 'memstick', 'psp_root', 'scale', 'backend']) fields[name] = xmb.settings[name] ?? '';
  fields[key] = value;
  fields.scale = Number(fields.scale) || 3;
  if (await mutate('/api/settings', fields, `${label}: guardado.`)) await xmbLoadSettings();
}

function xmbGameIcon(game) { return { game }; }
function xmbBuildSections() {
  const state = ui.state || {};
  const diagnostic = state.controller_status?.mode === 'msc_diagnostic';
  const games = state.games || [];
  const saves = state.saves || { actions: [], conflicts: [], unchanged: 0 };
  if (ui.state) renderSaves();
  const availability = { ready: ['pc', 'EN PC'], missing: ['copy', 'COPIAR'], different: ['conf', 'REVISAR'], checking: ['inh', 'COMPROBANDO'] };
  const availabilityText = { ready: 'En el PC', missing: 'Solo en la PSP', different: 'Versión distinta en el PC', checking: 'Comprobando en la PSP' };
  const out = {};

  const settingsValue = (key, fallback) => xmb.settings?.[key] ?? fallback;
  out.ajustes = [
    { key: 'gfx-default', t: 'Gráficos predeterminados', s: () => xmbGfxSummary(xmb.gfx.default?.choice) + ' · para juegos sin perfil propio', icon: 'graficos',
      focus: () => xmbLoadGraphics(null), activate: () => xmbOpenGraphics(null) },
    { key: 'retro-buttons', t: 'Controles RetroArch · PSP', icon: 'pad',
      s: () => settingsValue('retro_button_layout','nintendo') === 'psp' ? '✕ = A · ○ = B · SELECT+△ abre menú' : '✕ = B · ○ = A · SELECT+△ abre menú',
      activate: () => xmbValues('Distribución de botones para RetroArch',
        [['nintendo','Nintendo: ✕ = B / ○ = A (predeterminada)'],['psp','PSP: ✕ = A / ○ = B']],
        settingsValue('retro_button_layout','nintendo'),
        value => xmbSaveSetting('retro_button_layout',value,'Controles RetroArch')) },
    { key:'retro-auto',t:'Instalación de emuladores retro',icon:'retro',
      s:() => ({auto:'Automática al detectar homebrew PSP',ask:'Preguntar antes de descargar',off:'Desactivada'})[settingsValue('retro_install_policy','ask')],
      activate:()=>xmbValues('Instalación de emuladores oficiales',
        [['auto','Automática · solo núcleos detectados'],
         ['ask','Preguntar antes de descargar'],['off','No descargar emuladores']],
         settingsValue('retro_install_policy','ask'),
         value=>xmbSaveSetting('retro_install_policy',value,'Instalación retro')) },
    { key: 'backend', t: 'Motor gráfico', s: () => ({ directx11: 'Direct3D 11', vulkan: 'Vulkan', gles: 'OpenGL' })[settingsValue('backend', 'directx11')] || settingsValue('backend', ''), icon: 'hd',
      activate: () => xmbValues('Motor gráfico', [['directx11', 'Direct3D 11'], ['vulkan', 'Vulkan'], ['gles', 'OpenGL']], settingsValue('backend', 'directx11'),
        (value) => xmbSaveSetting('backend', value, 'Motor gráfico')) },
    { key: 'scale', t: 'Escala manual de respaldo', s: () => `${settingsValue('scale', 3)}×`, icon: 'hd',
      activate: () => xmbValues('Escala manual de respaldo', [1, 2, 3, 4, 5, 6].map((n) => [String(n), n === 1 ? '1× · Original' : `${n}×`]),
        String(settingsValue('scale', 3)), (value) => xmbSaveSetting('scale', Number(value), 'Escala manual')) },
    { key: 'theme', t: 'Color del fondo', s: () => xmb.theme === 'month' ? 'Según el mes, como la PSP' : 'Azul PSP Dock', icon: 'theme',
      activate: () => xmbValues('Color del fondo', [['month', 'Según el mes, como la PSP'], ['dock', 'Azul PSP Dock']], xmb.theme, (value) => {
        xmb.theme = value;
        try { localStorage.setItem('psp-dock-theme', value); } catch { /* sin almacenamiento */ }
        xmbApplyTheme();
      }) },
    { key: 'sound', t: 'Música de selección de juegos', icon: 'info',
      s: () => xmb.mediaSound ? 'Activada · audio original PSP' : 'Silenciada',
      activate: () => xmbValues('Música del XMB', [['on','Activada'],['off','Silenciada']],
        xmb.mediaSound ? 'on' : 'off', (value) => {
          xmb.mediaSound = value === 'on';
          try { localStorage.setItem('psp-dock-xmb-sound',value); } catch {}
          const previous = xmb.mediaGame;
          xmbPreviewGame(null);
          xmbPreviewGame(previous);
        }) },
    { key: 'pad', t: 'Mando', s: () => xmbText('gamepad-status') || 'Sin detectar', icon: 'pad', activate: () => toast(xmbText('gamepad-status')) },
    { key: 'storage', t: 'Carpeta principal de datos',
      s: () => settingsValue('storage_root',settingsValue('library','')),
      icon: 'folder', activate: () => xmbStorageLocation() },
    ...[['emulator', 'Emulador PPSSPP'], ['retroarch', 'RetroArch (consolas clásicas)'], ['library', 'Biblioteca del PC'], ['memstick', 'Memory Stick de PPSSPP'], ['psp_root', 'Raíz de la PSP']].map(([key, t]) => ({
      key: 'path-' + key, t, s: () => settingsValue(key, '') || (key === 'psp_root' ? 'Detección automática' : 'Sin configurar'), icon: 'folder',
      activate: () => { openSettings(); toast('Las carpetas se escriben con el teclado. ○ cierra sin guardar.'); },
    })),
  ];

  const dock = [];
  if (ui.serviceOffline || state.error || state.recovery_required)
    out.dockWarning = true, dock.push({ key: 'warning', t: 'Aviso', s: xmbText('error'), icon: 'warn', activate: () => toast(xmbText('error')) });
  if (state.copy_queue?.status === 'copied')
    dock.push({ key: 'copy-completed', t: 'Juego copiado y verificado',
      s: state.product_flow?.enabled === true ? 'La copia está disponible en la biblioteca del PC.' : 'Vuelve a iniciar el mando HID desde PSP Dock para jugarlo.',
      icon: 'copy',activate: () => toast(state.product_flow?.enabled === true ?
        'La copia del juego se ha comprobado en el PC.' : 'La ISO ya está en el PC. El retorno USB → HID automático sigue pendiente del puente firmware seguro.') });
  if (state.copy_queue?.status === 'interrupted_review')
    out.dockWarning = true, dock.push({ key: 'copy-interrupted',
      t: 'Copia interrumpida · necesita revisión',
      s: 'No se repetirá automáticamente ni se sobrescribirá el juego.',
      icon: 'warn',activate: () => toast('La copia requiere revisión manual antes de intentarla otra vez.') });
  if (state.session?.return_result === 'retro_review' && state.retro_saves?.pending)
    dock.push({ key:'retro-save-pending', t:'Revisar guardados retro',
      s:`${state.retro_saves.pending} SRAM pendientes; la devolución espera tu decisión`,
      icon:'partidas',activate:()=>xmbGoto('partidas') });
  if (!$('dock-preflight').hidden) dock.push({ key: 'status', t: xmbText('preflight-title') || 'Estado del Dock', s: xmbText('preflight-description'), icon: 'info',
    activate: () => toast([xmbText('preflight-summary'), xmbText('runtime-readiness-label'), xmbText('preflight-caveat')].filter(Boolean).join(' · ')) });
  if (xmbUsable('preflight-copy')) dock.push({ key: 'copy', t: xmbText('preflight-copy'), s: 'Copia al PC los juegos que solo están en la PSP', icon: 'copy', activate: () => $('preflight-copy').click() });
  if (xmbUsable('preflight-saves')) dock.push({ key: 'saves', t: xmbText('preflight-saves'), s: 'Abre la sección Partidas', icon: 'partidas', activate: () => xmbGoto('partidas') });
  if (xmbUsable('preflight-prepare-runtime')) dock.push({ key: 'runtime', t: xmbText('preflight-prepare-runtime'), s: xmbText('runtime-readiness-label'), icon: 'play', activate: () => $('preflight-prepare-runtime').click() });
  if (xmbUsable('preflight-skip')) dock.push({ key: 'skip', t: xmbText('preflight-skip'), s: 'No copia nada; los juegos pendientes siguen visibles', icon: 'play',
    activate: () => xmbConfirm('¿Seguir solo con los juegos que ya están en el PC?<small>No se copiará nada.</small>', true, () => $('preflight-skip').click()) });
  dock.push({ key: 'refresh', t: 'Buscar PSP de nuevo', s: xmbText('device-label') + (xmbText('device-role') ? ' · ' + xmbText('device-role').toLowerCase() : ''), icon: 'search',
    activate: async () => { toast('Buscando PSP…'); await refresh(); } });
  const canExit = xmbUsable('preflight-exit-dock');
  dock.push({ key: 'exit', t: diagnostic ? 'Cerrar la prueba en la PSP' : 'Salir del Dock', s: diagnostic ? 'Mantén START+SELECT en la consola para terminar la prueba USB.' : canExit ? 'Devuelve tus partidas y vuelve a jugar en la PSP' : 'Disponible con el mando PSP conectado y PPSSPP cerrado', icon: 'exit',
    dim: !canExit,
    activate: () => diagnostic ? toast('Mantén START+SELECT en la PSP para cerrar la prueba de USB Storage + HID.') : canExit ?
      xmbConfirm('¿Salir del Dock?<small>' + (state.product_flow?.enabled === true ?
        'Se devolverán a la PSP las partidas y los estados compatibles que han cambiado. Mantén el cable conectado hasta terminar.' :
        'Después mantén START+SELECT 1,5 s en la PSP y vuelve al modo USB para devolver las partidas.') + '</small>', false, () => $('preflight-exit-dock').click()) :
      toast('Salir del Dock se activa cuando la PSP está conectada como mando y no hay ningún juego abierto.') });
  out.dock = dock;

  out.juegos = games.length ? games.map((game) => ({
    key: 'game-' + game.id, t: game.title, game,
    s: `${availabilityText[game.availability] || 'Estado desconocido'} · ${game.format || 'JUEGO'} · ${bytes(game.size)}`,
    chip: state.demo ? ['inh', 'DEMO'] : availability[game.availability], icon: xmbGameIcon(game),
    focus: () => { if (ui.selected !== game.id) { ui.selected = game.id; renderSelected(false); } },
    activate: () => xmbLaunch(game), menu: () => xmbGameMenu(game),
  })) : [{ key: 'empty', t: diagnostic ? 'PSP Dock conectada' : xmbText('empty-title') || 'Conecta tu PSP', s: diagnostic ? 'Prueba USB Storage + HID · puedes recorrer las secciones del menú.' : xmbText('empty-description'), icon: diagnostic ? 'info' : 'search',
    activate: async () => { toast('Buscando PSP…'); await refresh(); } }];

  const retro = state.retro || {};
  const installer=state.retro_install || {};
  out.retro = (retro.emulators || []).map((emulator) => {
    const roms = (retro.roms || []).filter((rom) => rom.emulator_id === emulator.id);
    const ready = roms.filter((rom) => rom.availability === 'ready').length;
    return {
      key: 'emulator-' + emulator.id, t: emulator.name, icon: 'retro',
      s: `${emulator.systems.join(' / ').toUpperCase()} · ${roms.length} ROMs · ${ready} en el PC`,
      chip: ready ? ['pc', `${ready} EN PC`] : roms.length ? ['copy', 'COPIAR'] : ['inh', 'VACÍO'],
      activate: () => xmbRetroEmulatorMenu(emulator),
      menu: () => xmbRetroEmulatorMenu(emulator),
    };
  });
  if ((retro.emulators || []).length) {
    const prog=installer.progress || {};
    const active=prog.active === true;
    const missing=installer.missing_cores?.length || 0;
    const reviewed=installer.requires_license_review?.length || 0;
    const licensing=reviewed ? ` · ${reviewed} núcleo(s) requieren licencia comercial o instalación independiente` : '';
    const text=active ? `${prog.item || 'Descargando'} · ${prog.bytes_total ? Math.round((prog.bytes_done || 0)*100/prog.bytes_total)+'%' : 'conectando'}` :
      prog.phase === 'failed' ? `Instalación no completada: ${prog.error || 'error de descarga'}` :
      installer.external ? 'RetroArch externo: se respetará su ubicación original' :
      installer.status === 'ready' ? 'Núcleos con licencia permitida instalados' + licensing :
      `RetroArch portable y ${missing} núcleo(s) pendientes${licensing} · en ${installer.managed_path || 'la carpeta elegida'}`;
    out.retro.unshift({ key:'retro-installer',t:active ? 'Instalando emuladores…' :
      installer.status === 'ready' ? 'Emuladores Windows preparados' : 'Instalar emuladores Windows',
      s:text,icon:active ? 'sync' : 'retro',
      chip:active ? ['inh','DESCARGANDO'] : installer.status === 'ready' ? ['pc','LISTOS'] : missing ? ['copy','INSTALAR'] : null,
      activate:()=>{
        if (active) { toast(text);return; }
        if (installer.external) {toast('Has elegido un RetroArch externo. El Dock no lo modificará; instala allí los núcleos mediante su actualizador.');return;}
        if (!installer.needed) {toast(text);return;}
        if (!state.selected_device) {toast('Conecta la PSP en modo almacenamiento USB Sony para autorizar los emuladores detectados.');return;}
        xmbConfirm(`¿Instalar los emuladores correspondientes a esta PSP?<small>Se descargarán RetroArch 1.22.2 y ${missing} núcleo(s) permitidos para este producto en la carpeta seleccionada en Ajustes.${reviewed ? ' Los núcleos con licencia no comercial requieren revisión y NO se descargarán automáticamente.' : ''} No se descargan ROMs ni BIOS ni se escribe en la PSP.</small>`,
          false,()=>mutate('/api/install-retro',{},'Instalación en el PC iniciada. Puedes seguir usando el XMB.'));
      },
    });
  }
  if (!out.retro.length) out.retro = [{ key:'no-retro', t:'No hay emuladores reconocidos',
    s:'Se detectan gpSP, TempGBA, Snes9xTYL, NesterJ, PicoDrive, Daedalus y otros.',
    icon:'search', activate:()=>toast('Conecta la PSP en almacenamiento USB para detectar los emuladores instalados y sus ROMs.') }];

  const partidas = [];
  partidas.push({ key: 'sync', t: xmbText('sync-button') || 'Sincronizar partidas',
    s: document.querySelector('#saves-content .save-summary')?.textContent || '', icon: 'sync', dim: !xmbUsable('sync-button'),
    activate: () => xmbUsable('sync-button') ?
      xmbConfirm(`¿${xmbEscape(xmbText('sync-button'))}?<small>PSP → PC trae la partida al ordenador. PC → PSP escribe en la Memory Stick.</small>`, true, () => $('sync-button').click()) :
      toast(document.querySelector('#saves-content .quiet')?.textContent || xmbText('sync-button')) });
  (saves.actions || []).forEach((action) => partidas.push({
    key: 'save-' + action.slot, t: action.slot, icon: 'partidas',
    s: action.direction === 'to_pc' ? 'Copiar de la PSP al PC' : 'Copiar del PC a la PSP · escribe en la Memory Stick',
    chip: action.direction === 'to_pc' ? ['pc', 'PSP → PC'] : ['toPsp', 'PC → PSP'],
    activate: () => toast('Se copiará al confirmar la sincronización, en la primera fila.') }));
  (saves.conflicts || []).forEach((conflict) => partidas.push({
    key: 'conflict-' + conflict.slot, t: conflict.slot, icon: 'warn', s: 'Ha cambiado en la PSP y en el PC', chip: ['conf', 'CONFLICTO'],
    activate: () => xmbResolve(conflict.slot) }));
  const saveRows = (state.retro_saves?.rows || []).filter(row =>
    row.status !== 'none' && row.status !== 'unsupported' && (row.status !== 'identical' || row.skipped));
  saveRows.forEach(row => partidas.push({
    key:'retro-sram-'+row.rom_id,t:`Retro · ${row.title}`,icon:'retro',
    s:row.skipped ? 'Devolución omitida por esta sesión' :
      row.status === 'psp_to_pc' ? 'Guardar desde PSP → PC · confirmación necesaria' :
      row.status === 'pc_to_psp' ? 'Devolver al PSP desde PC · confirmación necesaria' :
      row.status === 'conflict' ? 'Versiones diferentes: elige qué conservar' :
      row.status === 'blocked' ? row.reason : 'SRAM sin cambios',
    chip:row.skipped ? ['inh','OMITIDA'] :
      row.status === 'conflict' ? ['conf','CONFLICTO'] :
      row.status === 'psp_to_pc' ? ['pc','PSP → PC'] :
      row.status === 'pc_to_psp' ? ['toPsp','PC → PSP'] : null,
    activate:()=>xmbRetroSaveMenu(row),menu:()=>xmbRetroSaveMenu(row),
  }));
  if (!(saves.actions || []).length && !(saves.conflicts || []).length) {
    const quiet = document.querySelector('#saves-content .quiet')?.textContent;
    if (quiet) partidas.push({ key: 'saves-note', t: 'Sin cambios pendientes', s: quiet, icon: 'info', activate: () => toast(quiet) });
  }
  out.partidas = partidas;

  out.graficos = [
    ...games.map((game) => ({ key: 'gfx-' + game.id, t: game.title, icon: xmbGameIcon(game), gfxId: game.id,
      s: () => xmb.gfx[game.id]?.error || xmbGfxSummary(xmb.gfx[game.id]?.choice),
      chip: () => xmb.gfx[game.id]?.choice ? xmb.gfx[game.id].custom ? ['pc', 'PROPIO'] : ['inh', 'PREDETERMINADO'] : null,
      focus: () => xmbLoadGraphics(game.id), activate: () => xmbOpenGraphics(game.id) })),
    { key: 'gfx-default-all', t: 'Predeterminado para todos', s: () => xmbGfxSummary(xmb.gfx.default?.choice) + ' · lo usan los juegos sin perfil propio',
      icon: 'graficos', focus: () => xmbLoadGraphics(null), activate: () => xmbOpenGraphics(null) },
  ];
  const pending = (saves.actions?.length || 0) + (saves.conflicts?.length || 0) +
    (state.retro_saves?.pending || 0);
  return XMB_SECTIONS.map(([id, label]) => ({ id, label, items: out[id], badge: id === 'partidas' && pending ? pending :
    id === 'dock' && out.dockWarning ? '!' : null }));
}

function xmbTexturePackAction(id, data) {
  const disc = data.game_metadata?.disc_id;
  if (!disc) {
    toast('No se ha podido identificar la edición del juego.');
    return;
  }
  const sources = data.hd_texture_pack_sources || (data.hd_texture_pack_offers || [])
    .map(offer => ({ ...offer, downloadable:true }));
  const draw = (rows, searched) => {
    const items = rows.map((source) => ({
      t: source.title,
      s: source.publisher + ' · ' + (source.downloadable ?
        ('Instalable con verificación · ' + bytes(source.size_bytes)) :
        'Fuente encontrada · sin ZIP aprobado para instalación'),
    }));
    items.push({
      t: searched ? 'Actualizar búsqueda de Internet' : 'Buscar más packs en Internet',
      s: 'Consultar catálogo público de GitHub · solo información',
    });
    xmbSide('Packs HD · ' + disc, items, async (index) => {
      if (index === rows.length) {
        toast('Buscando proyectos de texturas HD en GitHub…');
        try {
          const result = await request('/api/texture-search/' + encodeURIComponent(disc));
          draw(result.sources || [], true);
          toast(result.unverified_new ?
            'Se han encontrado ' + result.unverified_new + ' fuentes adicionales por revisar.' :
            'Búsqueda terminada: no hay nuevos packs para esta edición.');
        } catch (error) {
          toast('La búsqueda de texturas no pudo completarse: ' + error.message);
        }
        return;
      }
      const source = rows[index];
      if (!source.downloadable) {
        xmbConfirm('Proyecto encontrado: ' + xmbEscape(source.title) +
          '<small>' + xmbEscape(source.publisher) +
          '. Todavía no se ha aprobado un ZIP para instalación automática. ' +
          'Fuente: ' + xmbEscape(source.source_url) +
          '. ¿Copiar enlace al portapapeles del PC?</small>', false,
          () => {
            if (!navigator.clipboard?.writeText) {
              toast('Fuente del proyecto: ' + source.source_url);
              return;
            }
            void navigator.clipboard.writeText(source.source_url).then(
              () => toast('Enlace del proyecto HD copiado al PC.'),
              () => toast('No se pudo copiar el enlace: ' + source.source_url)
            );
          });
        return;
      }
      xmbConfirm('¿Descargar e instalar "' + xmbEscape(source.title) +
        '" en PPSSPP?<small>Autor: ' + xmbEscape(source.publisher) +
        ' · ' + bytes(source.size_bytes) +
        '. Se comprobará la edición exacta y SHA-256 antes de instalar.</small>',
        false, async () => {
          if (await xmbInstallTexturePack(id, source.id)) {
            await xmbLoadGraphics(id, true);
          }
        });
    });
  };
  draw(sources, false);
}
async function xmbInstallTexturePack(gameId, offerId) {
  const poll = async () => {
    try {
      const status = await request('/api/texture-status');
      if (status.offer_id === offerId) {
        ui.textureProgress = status;
        xmbRender();
      }
    } catch { /* A rejected download is reported by mutate itself. */ }
  };
  // A separate GET thread can show progress while a verified download
  // temporarily holds Dock.lock for the private PPSSPP destination.
  const timer = setInterval(poll, 550);
  try {
    const ok = await mutate('/api/install-texture-pack',
      { id:gameId, offer_id:offerId },
      'Pack de texturas HD descargado y verificado. Se activará en PPSSPP al jugar.');
    return ok;
  } finally {
    clearInterval(timer);
    ui.textureProgress = null;
    xmbRender();
  }
}
function xmbGraphicsRows(id) {
  const data = xmb.gfx[xmbGfxKey(id)];
  const game = (ui.state?.games || []).find((item) => item.id === id);
  const name = game ? game.title : 'Predeterminado';
  if (!data || data.error) return [{ key: 'loading', t: data?.error ? 'No se pudo leer el perfil' : 'Leyendo el perfil…', s: data?.error || '', icon: 'info', activate: () => xmbLoadGraphics(id, true) }];
  const choice = data.choice, base = data.default_choice || choice;
  const tag = (key) => !id ? '' : data.custom && choice[key] !== base[key] ? 'PROPIO' : 'PREDET.';
  const write = (key, value) => xmbSaveGraphics(id, { ...choice, [key]: value },
    id ? `Guardado en el perfil de ${name}.` : 'Predeterminado guardado. Lo usan los juegos sin perfil propio.');
  const rows = [];
  if (id) rows.push({ key: 'own', t: 'Perfil de este juego', icon: 'ajustes',
    s: data.custom ? `Propio · solo afecta a ${name}` : 'Usa el predeterminado',
    activate: () => xmbValues('Perfil de este juego', [['default', 'Usar el predeterminado'], ['own', 'Perfil propio']], data.custom ? 'own' : 'default', (value) => {
      if (value === 'own' && !data.custom) xmbSaveGraphics(id, { ...choice }, `${name} ahora tiene perfil propio.`);
      if (value === 'default' && data.custom) xmbSaveGraphics(id, { reset: true }, `${name} vuelve a usar el predeterminado.`);
    }, name) });
  for (const key of ['quality', 'aspect', 'texture']) rows.push({
    key, t: XMB_GFX_NAMES[key], icon: key === 'quality' ? 'hd' : 'graficos', tag: tag(key),
    s: xmbGfxLabel(key, choice[key]),
    activate: () => xmbValues(XMB_GFX_NAMES[key], XMB_GFX[key], choice[key], (value) => value !== choice[key] && write(key, value), id ? name : 'Predeterminado',
      id ? base[key] : null) });
  const plan = data.plan || {};
  rows.push({ key: 'render', t: 'Resolución resultante', icon: 'info',
    s: plan.internal_width ? `${plan.internal_resolution}× PSP · ${plan.internal_width} × ${plan.internal_height} · ${plan.backend || ''}` : '—',
    activate: () => toast([plan.monitor ? `Monitor ${plan.monitor.width} × ${plan.monitor.height}${plan.monitor.ultrawide ? ' ultrawide' : ''}` : '',
      data.hardware?.gpu ? `GPU ${data.hardware.gpu}` : '', plan.warning].filter(Boolean).join(' · ')) });
  if (id) {
    const disc = data.game_metadata?.disc_id;
    rows.push({ key: 'pack', t: 'Pack de texturas HD', icon: 'folder',
      s: disc ? `${disc} · ${data.hd_texture_pack_installed ? 'detectado en PPSSPP' : 'no instalado'}` : 'Edición sin identificar',
      activate: () => xmbTexturePackAction(id, data) });
    const others = (ui.state?.games || []).filter((item) => item.id !== id);
    if (others.length) rows.push({ key: 'copy', t: 'Copiar ajustes de otro juego', s: 'Trae calidad, proporción y texturas', icon: 'copy',
      activate: () => xmbSide('Copiar ajustes de…', others.map((other) => ({ t: other.title, s: xmbGfxSummary(xmb.gfx[other.id]?.choice) })), async (index) => {
        const other = others[index];
        await xmbLoadGraphics(other.id);
        const from = xmb.gfx[other.id]?.choice;
        if (from) xmbSaveGraphics(id, { quality: from.quality, aspect: from.aspect, texture: from.texture || 'off' }, `Ajustes de ${other.title} copiados a ${name}.`);
      }) });
    if (data.custom) rows.push({ key: 'reset', t: 'Restablecer', s: 'Volver al predeterminado', icon: 'reset',
      activate: () => xmbConfirm(`¿Quitar el perfil propio de ${xmbEscape(name)}?<small>Volverá a usar el predeterminado.</small>`, false,
        () => xmbSaveGraphics(id, { reset: true }, `${name} vuelve a usar el predeterminado.`)) });
  }
  return rows;
}
function xmbOpenGraphics(id) {
  const state = ui.state || {};
  if (state.session?.running || state.session?.exit_requested || state.recovery_required) {
    toast('Cierra PPSSPP y termina la devolución antes de cambiar los gráficos.');
    return;
  }
  const section = XMB_SECTIONS.findIndex(([key]) => key === 'graficos');
  const items = xmb.sections[section]?.items || [];
  const index = items.findIndex((item) => (item.gfxId || null) === (id || null) && (id || item.key === 'gfx-default-all'));
  xmb.nav = { ...xmb.nav, cat: section, level: 'sub', subIdx: 0, side: null, idx: { ...xmb.nav.idx, graficos: Math.max(0, index) } };
  xmb.sub = { id, title: id ? (state.games || []).find((game) => game.id === id)?.title || 'Juego' : 'Predeterminado para todos' };
  xmbLoadGraphics(id, true);
  if (id) xmbLoadGraphics(null);
  xmbRender();
}
function xmbValues(title, options, current, pick, owner = null, defaultValue = null) {
  xmbSide(owner ? `${owner} · ${title}` : title, options.map(([value, label]) => ({ t: label, s: value === defaultValue ? 'predeterminado' : '' })),
    (index) => pick(options[index][0]), Math.max(0, options.findIndex(([value]) => value === current)));
}
function xmbSide(title, opts, pick, start = 0) {
  xmb.side = { title, opts, pick };
  xmb.nav = { ...xmb.nav, side: { i: start } };
  xmbRender();
}
function xmbConfirm(html, yesByDefault, done) {
  xmb.confirm = { html, done };
  xmb.nav = { ...xmb.nav, confirm: { yes: !!yesByDefault } };
  xmbRender();
}
function xmbResolve(slot) {
  const state = ui.state || {};
  const product = xmbConnectionView().product;
  if (product?.enabled && !product.canReview) {
    toast('Los guardados se revisan durante la preparación de la sesión o al salir del Dock.');
    return;
  }
  if (ui.busy || state.recovery_required || !state.selected_device || state.session?.running || ui.serviceOffline) {
    toast('Para resolver un conflicto, conecta la PSP en modo almacenamiento USB y cierra PPSSPP.');
    return;
  }
  xmbSide(`Conflicto · ${slot}`, [{ t: 'Usar la de la PSP', s: 'Reemplaza la copia del PC' }, { t: 'Usar la del PC', s: 'Escribe en la Memory Stick' }], (index) => {
    const choice = index === 0 ? 'psp' : 'pc';
    xmbConfirm(`¿Conservar la partida «${xmbEscape(slot)}» de ${choice === 'pc' ? 'este PC' : 'la PSP'} y reemplazar la otra?`, false, () => {
      ui.resolve = { slot, choice };
      $('resolve-confirm').click();
    });
  });
}
function xmbLaunch(game) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  ui.selected = game.id;
  renderSelected(false);
  if (ui.state?.controller_status?.mode === 'msc_diagnostic') {
    toast('Modo diagnóstico: puedes navegar, pero los juegos están desactivados.');
    return;
  }
  if (game.availability === 'missing' && ui.state?.controller_status?.connected) {
    // CROSS on a missing title opens the same contextual right-hand menu.
    // Neither CROSS nor TRIANGLE may queue a copy directly.
    xmbGameMenu(game);
    return;
  }
  const toastBefore = ui.toastTimer;
  const navBefore = xmb.nav.cat;
  launch();
  // launch() explica la mayoría de bloqueos; si no dijo nada, muestra el motivo.
  if (ui.toastTimer === toastBefore && !ui.busy && navBefore === xmb.nav.cat &&
      !document.querySelector('dialog[open]') && $('launch-button').disabled) toast(xmbText('launch-note'));
}
function xmbQueueMissingGame(game) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  if (ui.state?.controller_status?.mode === 'msc_diagnostic') {
    toast('El disco de prueba no contiene juegos. La copia está desactivada en modo diagnóstico.');
    return;
  }
  const automatic = connection.product?.enabled && connection.product.automatic;
  xmbConfirm('¿Copiar ' + xmbEscape(game.title) +
    ' al PC?<small>Se pausará la navegación mientras se copia y verifica el juego. ' +
    (automatic ? 'Después volverás al menú del Dock.' :
      'Sigue las indicaciones de la PSP para pasar a almacenamiento y volver al mando.') + '</small>', false,
    async () => {
      await mutate('/api/queue-game-copy',{id:game.id},
        automatic ? 'Preparando la copia del juego…' : 'Copia solicitada. Sigue las indicaciones de la PSP para activar el almacenamiento.');
    });
}
function xmbGameMenu(game) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  const diagnostic = ui.state?.controller_status?.mode === 'msc_diagnostic';
  const opts = [
    { t: diagnostic ? 'Juegos desactivados' : game.availability === 'missing' ? 'Sincronizar con Dock' : 'Jugar', s: diagnostic ? 'Disco de prueba · sólo navegación' : game.title },
    { t: 'Ajustes gráficos de este juego', s: xmbGfxSummary(xmb.gfx[game.id]?.choice) },
    { t: 'Partidas', s: 'Abre la sección Partidas' },
    { t: 'Información', s: `${game.format || 'JUEGO'} · ${bytes(game.size)}` },
  ];
  xmbLoadGraphics(game.id);
  xmbSide(game.title, opts, (index) => {
    if (index === 0) {
      if (diagnostic) { toast('Modo diagnóstico: puedes navegar, pero los juegos están desactivados.'); return; }
      // Only an explicitly selected menu action can begin the
      // confirmation dialog; TRIANGLE itself has no side effects.
      if (game.availability === 'missing') xmbQueueMissingGame(game);
      else xmbLaunch(game);
    }
    else if (index === 1) xmbOpenGraphics(game.id);
    else if (index === 2) xmbGoto('partidas');
    else { ui.selected = game.id; renderSelected(false); toast(`${game.relative || game.title} · ${xmbText('launch-note')}`); }
  });
}
function xmbRetroEmulatorMenu(emulator) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  const roms = (ui.state?.retro?.roms || []).filter((rom) => rom.emulator_id === emulator.id);
  if (!roms.length) {
    xmbSide(emulator.name, [{t:'No se han encontrado ROMs',s:'Comprueba la carpeta ROMS del emulador en tu PSP.'}],
      () => toast('No se han localizado archivos ROM compatibles en la carpeta de este emulador.'));
    return;
  }
  xmbSide(emulator.name, roms.map((rom) => ({
    t:rom.title,
    s:`${rom.system_name} · ${rom.availability === 'ready' ? 'En el PC' :
      rom.availability === 'missing' ? 'Copiar desde PSP' :
      rom.availability === 'checking' ? 'Comprobando' : 'Versión diferente'}`,
  })), index => xmbRetroRomMenu(roms[index]));
}
function xmbRetroRomMenu(rom) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  const save = (ui.state?.retro_saves?.rows || []).find(row => row.rom_id === rom.id);
  const items = [
    { t:'Jugar en Windows', s:rom.windows_emulator + (rom.core_available ? ' · núcleo instalado' : ' · núcleo pendiente') },
    { t:'Copiar ROM al PC', s:rom.availability === 'missing' ? bytes(rom.size) + ' · transferencia verificada' : 'No se sobrescribe el archivo local' },
    { t:'Partida retro (SRAM)', s:save ? (save.status === 'unsupported' ? save.reason :
      save.status === 'none' ? 'Aún no existe una SRAM guardada' :
      save.status === 'identical' ? 'Coinciden PSP y PC' :
      save.status === 'blocked' ? save.reason : 'Tienes versiones por revisar') : 'Conecta la PSP en almacenamiento para verificar la SRAM' },
    { t:'Información', s:rom.system_name + ' · ' + rom.format + ' · ' + bytes(rom.size) },
  ];
  xmbSide(rom.title, items, (index) => {
    if (index === 0) xmbLaunchRetro(rom);
    else if (index === 1) {
      if (rom.availability !== 'missing') toast('Solo se copian ROMs ausentes del PC; no se sobrescriben versiones diferentes.');
      else if (connection.diagnostic) toast('El disco de diagnóstico no contiene tus ROMs.');
      else if (connection.product?.enabled) xmbQueueMissingGame(rom);
      else if (!ui.state?.selected_device) toast('Vuelve al almacenamiento USB Sony para copiar esta ROM al PC.');
      else openRetroTransfer(rom);
    } else if (index === 2) save ? xmbRetroSaveMenu(save) : toast('Revisa las SRAM con la PSP en almacenamiento USB Sony.');
    else toast(`${rom.title} · ${rom.system_name} · ${rom.windows_emulator} · ${rom.relative}. Solo las SRAM compatibles se sincronizan con tu aprobación; nunca los savestates.`);
  });
}
function xmbRetroSaveMenu(initial) {
  const state=ui.state || {};
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  const row=(state.retro_saves?.rows || []).find(item => item.rom_id === initial.rom_id) || initial;
  const choices=[];
  const returning = state.product_flow?.purpose === 'exit' || state.session?.exit_requested === true;
  const allowed = connection.product?.enabled && !connection.product.canReview ? [] : dockRetroSaveChoices(row, returning);
  if (allowed.includes('psp'))
    choices.push({t:'Conservar SRAM de la PSP',s:'PSP → PC · se respalda la versión del PC',choice:'psp'});
  if (allowed.includes('pc'))
    choices.push({t:'Conservar SRAM del PC',s:'PC → PSP · se respalda antes la versión PSP',choice:'pc'});
  if (allowed.includes('skip'))
    choices.push({t:'Omitir este guardado',s:'Dejar ambas versiones sin copiar y continuar la salida',choice:'skip'});
  choices.push({t:'Información del guardado',s:row.reason ||
    (row.status === 'identical' ? 'La SRAM coincide en ambos lados' :
     row.status === 'none' ? 'Todavía no se ha guardado una partida en el juego' :
     row.skipped ? 'Omitida durante esta salida del Dock' : 'Las SRAM necesitan una decisión explícita'),choice:'info'});
  xmbSide(`SRAM · ${row.title}`,choices,(index)=>{
    const choice=choices[index]?.choice;
    if (choice === 'info') {
      toast(`${row.title}: ${row.reason || row.status} · ${row.source_relative || 'sin ruta PSP'}. Los savestates de emuladores no son portables.`);
      return;
    }
    if (!state.selected_device || state.retro_saves?.offline || ui.serviceOffline || ui.busy) {
      toast('Vuelve a almacenamiento Sony original antes de importar o devolver una partida.');
      return;
    }
    const direction=choice === 'pc' ? 'PC → PSP' : choice === 'psp' ? 'PSP → PC' : 'ninguna copia';
    const warning=choice === 'pc' && row.suggested_path ?
      `<small>La ruta PSP ${xmbEscape(row.source_relative)} es sugerida: este homebrew podría utilizar otra carpeta. Comprueba la ubicación en tu PSP.</small>` : '';
    const confirmation=choice === 'skip' ?
      `¿Omitir la SRAM de «${xmbEscape(row.title)}»?<small>Se conservarán ambos archivos diferentes. Solo se omite durante esta salida.</small>` :
      `¿Conservar la SRAM ${direction} de «${xmbEscape(row.title)}»?<small>Se hará un respaldo SHA-256 del destino antes de reemplazarlo. Nunca se copiarán savestates.</small>${warning}`;
    xmbConfirm(confirmation,false,()=>mutate('/api/retro-save-sync',
      {id:row.rom_id,choice,revision:row.revision},
      choice === 'skip' ? 'Guardado retro omitido para esta salida.' : `SRAM ${direction} verificada y respaldada.`));
  });
}
function xmbLaunchRetro(rom) {
  const state = ui.state || {};
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking || connection.diagnostic) return;
  if (state.demo || ui.busy || ui.serviceOffline || state.recovery_required) {
    toast('Los juegos retro solo se ejecutan con el Dock real y sin recuperación pendiente.');
    return;
  }
  if (state.session?.running) { toast('Ya hay un emulador abierto en Windows.'); return; }
  if (rom.availability === 'missing') {
    if (connection.product?.enabled) xmbRetroRomMenu(rom);
    else if (state.selected_device) openRetroTransfer(rom);
    else toast('ROM no copiada. Vuelve al almacenamiento Sony para transferirla antes de jugar.');
    return;
  }
  if (rom.availability !== 'ready') { toast('Comprueba la ROM: la copia del PC no coincide con la PSP.'); return; }
  const save=(state.retro_saves?.rows || []).find(item => item.rom_id === rom.id);
  if (!connection.product?.usePcSaves && save && ['psp_to_pc','conflict','blocked'].includes(save.status) && !save.skipped) {
    toast('Revisa la SRAM de tu PSP antes de jugar. Abre Partidas → Retro para evitar dividir tu progreso.');
    return;
  }
  if (!rom.core_available) {
    toast(`Configura RetroArch en Ajustes e instala el núcleo compatible con ${rom.system_name}.`);
    return;
  }
  if (!connection.product?.usePcSaves &&
      (state.saves?.actions?.length || state.saves?.conflicts?.length || state.preflight?.saves_prepared !== true)) {
    if (state.selected_device) openSaves();
    else toast('Primero prepara las partidas en el almacenamiento Sony original.');
    return;
  }
  if (state.selected_device && !connection.nativeController || state.controller_status?.connected !== true ||
      !connection.canPlay || !state.can_launch_retro) {
    toast(connection.product?.enabled ? 'Espera a que PSP Dock confirme la conexión y la preparación de este juego.' :
      'Activa manualmente el mando HID PDK1 de la PSP y comprueba que las partidas estén preparadas.');
    return;
  }
  mutate('/api/retro-launch',{id:rom.id},`Abriendo ${rom.system_name} en RetroArch…`);
}
function xmbGoto(id) {
  const connection = xmbConnectionView();
  if (connection.waiting || connection.product?.blocking) return;
  const cat = XMB_SECTIONS.findIndex(([key]) => key === id);
  if (cat < 0) return;
  xmb.nav = { ...xmb.nav, cat, level: 'list', subIdx: 0, side: null, confirm: null };
  xmbRender();
}
function xmbCurrentItems() {
  const section = xmb.sections[xmb.nav.cat];
  return xmb.nav.level === 'sub' ? xmbGraphicsRows(xmb.sub?.id || null) : section?.items || [];
}
function xmbPress(button) {
  const connection = xmbConnectionView();
  if (connection.product?.visible) { productFlowPress(button); return; }
  if (connection.waiting) return;
  if (!xmb.sections.length) xmb.sections = xmbBuildSections();
  if (ui.textureProgress?.active) return;
  if (!xmb.nav.confirm &&
      ['waiting_for_storage','copying'].includes(ui.state?.copy_queue?.status)) {
    if (button === 'back' && ui.state.copy_queue.status === 'waiting_for_storage') {
      xmbConfirm('¿Cancelar la solicitud de copia de juego?', false,
        async () => mutate('/api/cancel-game-copy',{},'Solicitud de copia cancelada.'));
    }
    return;
  }
  if (ui.state?.session?.running && button !== 'back') return;
  const view = {
    waiting: connection.waiting,
    sections: xmb.sections.map((section) => ({ id: section.id, count: section.items.length })),
    subCount: xmb.nav.level === 'sub' ? xmbGraphicsRows(xmb.sub?.id || null).length : 0,
    sideCount: xmb.side?.opts.length || 0,
  };
  const before = xmb.nav;
  const { nav, effect } = xmbStep(xmb.nav, view, button);
  xmb.nav = nav;
  if (before.level === 'sub' && nav.level === 'list') xmb.sub = null;
  if (!nav.side && before.side) { const side = xmb.side; xmb.side = null; if (effect?.type === 'pick') side?.pick(effect.index); }
  if (!nav.confirm && before.confirm) { const confirm = xmb.confirm; xmb.confirm = null; if (effect?.yes) confirm?.done(); }
  if (effect?.type === 'activate' || effect?.type === 'menu') {
    const item = xmb.sections[nav.cat]?.items[effect.index];
    if (effect.type === 'activate') item?.activate?.();
    else if (item?.menu) item.menu();
  } else if (effect?.type === 'row') xmbGraphicsRows(xmb.sub?.id || null)[effect.index]?.activate?.();
  else if (effect?.type === 'select') {
    xmbGoto('dock');
    const items = xmb.sections.find((section) => section.id === 'dock')?.items || [];
    xmb.nav.idx = { ...xmb.nav.idx, dock: Math.max(0, items.length - 1) };
    toast(connection.diagnostic ? 'Cierra la prueba con START+SELECT en la PSP.' : 'Salir del Dock · pulsa ✕ para confirmar.');
  }
  xmbRender();
}

// ---------- pintado ----------
const XMB_CAT_GAP = 12, XMB_CAT_X = 24;
function xmbIconNode(icon) {
  const box = document.createElement('div');
  if (typeof icon === 'string') { box.className = 'xmb-ic round'; box.innerHTML = XMB_ICONS[icon] || XMB_ICONS.info; return box; }
  box.className = 'xmb-ic';
  const game = icon.game;
  box.dataset.fallback = (game.title || 'PSP').toUpperCase();
  let img = xmb.icons.get(game.id);
  if (!img && !ui.state?.demo) {
    // Una sola petición por juego: el servidor responde sin caché.
    img = document.createElement('img');
    img.alt = '';
    img.decoding = 'async';
    img.addEventListener('load', () => img.classList.add('ok'), { once: true });
    img.addEventListener('error', () => img.remove(), { once: true });
    img.src = '/api/icon/' + encodeURIComponent(game.id);
    xmb.icons.set(game.id, img);
  }
  if (img) box.append(img);
  return box;
}
function xmbItemNode(key, item) {
  let node = xmb.els.get(key);
  const val = (v) => typeof v === 'function' ? v() : v;
  const chip = val(item.chip);
  const signature = JSON.stringify([item.t, val(item.s), chip, item.tag, typeof item.icon === 'string' ? item.icon : item.icon?.game?.id, !!item.dim]);
  if (!node) {
    node = document.createElement('div');
    node.className = 'xmb-item';
    node.setAttribute('role', 'option');
    xmb.els.set(key, node);
  }
  if (node.dataset.sig !== signature) {
    node.dataset.sig = signature;
    node.replaceChildren();
    const text = document.createElement('div');
    text.className = 'xmb-tx';
    const title = document.createElement('b');
    title.textContent = item.t;
    if (chip) { const c = document.createElement('span'); c.className = 'xmb-chip ' + chip[0]; c.textContent = chip[1]; title.append(c); }
    if (item.tag) { const c = document.createElement('span'); c.className = 'xmb-chip ' + (item.tag === 'PROPIO' ? 'pc' : 'inh'); c.textContent = item.tag; title.append(c); }
    const sub = document.createElement('small');
    sub.textContent = val(item.s) || '';
    text.append(title, sub);
    node.append(xmbIconNode(item.icon), text);
    node.classList.toggle('dim-item', !!item.dim);
  }
  return node;
}
function xmbPlaceColumn(prefix, items, focus, x, collapsed, used) {
  const list = $('xmb-list');
  items.forEach((item, i) => {
    const r = i - focus;
    if (r < -2 || r > 6 || collapsed && r !== 0) return;
    const key = prefix + item.key;
    const node = xmbItemNode(key, item);
    used.add(key);
    node.classList.toggle('on', r === 0 && !collapsed);
    node.classList.toggle('collapsed', collapsed);
    node.setAttribute('aria-selected', String(r === 0 && !collapsed));
    node.style.left = x + 'cqw';
    node.style.top = (r === 0 ? 20.5 : r > 0 ? 21 + r * 5.6 : 7.2 + (r + 1) * 5.2) + 'cqw';
    node.style.opacity = collapsed ? '.45' : r < 0 ? (r === -1 ? '.55' : '.25') : '';
    node.onclick = () => {
      if (xmbConnectionView().waiting) return;
      if (prefix === 'sub:') { if (r === 0) xmbPress('accept'); else { xmb.nav = { ...xmb.nav, subIdx: i }; xmbRender(); } }
      else if (collapsed) xmbPress('back');
      else if (r === 0) xmbPress('accept');
      else { xmb.nav = { ...xmb.nav, idx: { ...xmb.nav.idx, [xmb.sections[xmb.nav.cat].id]: i } }; xmbRender(); }
    };
    if (node.parentNode !== list) list.append(node);
  });
}
function xmbRender() {
  const connection = xmbConnectionView();
  xmbRenderConnection(connection);
  if (connection.waiting || connection.product?.visible) return;
  const state = ui.state || {};
  xmb.sections = xmbBuildSections();
  const nav = xmb.nav;
  nav.cat = Math.max(0, Math.min(xmb.sections.length - 1, nav.cat));
  const section = xmb.sections[nav.cat];
  const focus = Math.max(0, Math.min(section.items.length - 1, nav.idx[section.id] || 0));
  nav.idx[section.id] = focus;
  const inSub = nav.level === 'sub';
  const rows = inSub ? xmbGraphicsRows(xmb.sub?.id || null) : [];
  if (inSub) nav.subIdx = Math.max(0, Math.min(rows.length - 1, nav.subIdx));
  if (!inSub) section.items[focus]?.focus?.();
  // The PSP's original XMB presentation travels with the game in the
  // PC cache: PIC1 background, optional PIC0 overlay and SND0 sound.
  // Switching category or launching PPSSPP stops preview sound at once.
  xmbPreviewGame(
    state?.session?.running ? null :
    inSub ? xmb.sub?.id || null :
    section.id === 'juegos' ? section.items[focus]?.game?.id || null : null
  );

  const cats = $('xmb-cats');
  if (cats.childElementCount !== xmb.sections.length) {
    cats.replaceChildren(...xmb.sections.map((item, i) => {
      const node = document.createElement('button');
      node.type = 'button';
      node.className = 'xmb-cat';
      node.tabIndex = -1;
      node.style.left = (XMB_CAT_X + i * XMB_CAT_GAP) + 'cqw';
      node.innerHTML = XMB_ICONS[item.id] + '<span></span><em class="xmb-badge" hidden></em>';
      node.querySelector('span').textContent = item.label;
      node.addEventListener('click', () => { if (xmbConnectionView().waiting) return; xmb.nav = { ...xmb.nav, cat: i, level: 'list', side: null, confirm: null }; xmb.sub = null; xmbRender(); });
      return node;
    }));
  }
  Array.from(cats.children).forEach((node, i) => {
    node.classList.toggle('on', i === nav.cat);
    node.setAttribute('aria-current', i === nav.cat ? 'page' : 'false');
    const badge = node.querySelector('.xmb-badge');
    badge.hidden = !xmb.sections[i].badge;
    badge.textContent = xmb.sections[i].badge || '';
  });
  cats.style.transform = `translateX(${-nav.cat * XMB_CAT_GAP}cqw)`;
  cats.classList.toggle('faded', inSub);

  const used = new Set();
  xmbPlaceColumn('main:' + section.id + ':', section.items, focus, inSub ? 4 : XMB_CAT_X - 3.6, inSub, used);
  if (inSub) xmbPlaceColumn('sub:', rows, nav.subIdx, 26, false, used);
  for (const [key, node] of xmb.els) if (!used.has(key)) { node.remove(); xmb.els.delete(key); }

  $('xmb-crumb').textContent = [state.demo ? 'DEMOSTRACIÓN · biblioteca sintética' : connection.storageSetup ? 'PREPARACIÓN · almacenamiento USB' : '', inSub ? `Gráficos › ${xmb.sub?.title || ''}` : ''].filter(Boolean).join('   ');
  const hid = state.controller_status?.connected === true;
  $('xmb-hid').classList.toggle('on', hid);
  $('xmb-hid').classList.toggle('storage', !hid && !!state.selected_device);
  $('xmb-hid-label').textContent = ui.serviceOffline ? 'Sin conexión con la app' : connection.diagnostic ? 'PSP · diagnóstico' :
    hid ? 'PSP conectada' : state.selected_device ? 'Biblioteca PSP' : 'Sin PSP';
  $('xmb-busy').hidden = !ui.busy;
  $('xmb-busy').textContent = ui.busy ? 'Procesando…' : '';

  const side = $('xmb-side');
  side.classList.toggle('open', !!nav.side && !!xmb.side);
  if (nav.side && xmb.side) {
    $('xmb-side-title').textContent = xmb.side.title.toUpperCase();
    $('xmb-side-list').replaceChildren(...xmb.side.opts.map((opt, i) => {
      const li = document.createElement('li');
      li.className = i === nav.side.i ? 'on' : '';
      li.textContent = opt.t;
      if (opt.s) { const small = document.createElement('small'); small.textContent = opt.s; li.append(small); }
      li.addEventListener('click', () => { xmb.nav = { ...xmb.nav, side: { i } }; xmbPress('accept'); });
      return li;
    }));
  }
  $('xmb-confirm').classList.toggle('open', !!nav.confirm && !!xmb.confirm);
  if (nav.confirm && xmb.confirm) {
    // El texto de confirmación lo escribe solo este archivo; los nombres de
    // partidas o juegos se insertan escapados.
    $('xmb-confirm-text').innerHTML = xmb.confirm.html;
    $('xmb-yes').classList.toggle('on', nav.confirm.yes);
    $('xmb-no').classList.toggle('on', !nav.confirm.yes);
  }
  $('xmb-running').classList.toggle('open', !!state.session?.running);
  const queue=state.copy_queue || {};
  const copying=['waiting_for_storage','copying'].includes(queue.status);
  const pack=ui.textureProgress;
  $('xmb-copy-status').hidden=!copying && !pack?.active;
  if (pack?.active) {
    $('xmb-copy-heading').textContent =
      pack.phase === 'validating' ? 'Verificando paquete HD' :
      'Descargando texturas HD';
    const done = pack.downloaded_bytes || 0;
    const total = pack.total_bytes || 0;
    $('xmb-copy-description').textContent =
      bytes(done) + ' / ' + bytes(total) +
      (total ? ' · ' + Math.floor(done / total * 100) + ' %' : '') +
      '. Los controles del XMB permanecen bloqueados durante la instalación.';
  } else if (copying) {
    $('xmb-copy-heading').textContent=queue.status==='copying' ?
      'Copiando y verificando el juego' : 'Esperando almacenamiento Sony';
    $('xmb-copy-description').textContent=queue.status==='copying' ?
      'La PSP no debe desconectarse mientras la copia esté en curso.' :
      'Mantén START+SELECT en PSP Dock para detener HID, desconecta el cable y vuelve manualmente al modo USB de Sony. La copia se iniciará al reconocer la misma Memory Stick.';
  }
  xmbRenderHints(inSub ? rows[nav.subIdx] : section.items[focus]);
}
function xmbRenderHints(item) {
  const nav = xmb.nav, hints = [];
  const add = (glyph, label) => hints.push([glyph, label]);
  if (ui.state?.session?.running) add('', 'PPSSPP en ejecución');
  else if (nav.confirm) { add('◀▶', 'Elegir'); add('✕', 'Aceptar'); add('○', 'Cancelar'); }
  else if (nav.side) { add('▲▼', 'Elegir'); add('✕', 'Aceptar'); add('○', 'Cerrar'); }
  else if (nav.level === 'sub') { add('▲▼', 'Ajuste'); add('✕', 'Cambiar'); add('○', 'Volver'); }
  else {
    add('◀▶', 'Sección'); add('▲▼', 'Elegir');
    if (item?.game) { add('✕', xmbConnectionView().diagnostic ? 'Información' : item.game.availability === 'missing' ? 'Menú' : 'Jugar'); add('△', 'Opciones'); }
    else if (item) add('✕', 'Entrar');
  }
  $('xmb-hints').replaceChildren(...hints.map(([glyph, label]) => {
    const span = document.createElement('span');
    if (glyph) { const i = document.createElement('i'); i.textContent = glyph; span.append(i); }
    span.append(label);
    return span;
  }));
}
function xmbApplyTheme() {
  const now = new Date();
  const colors = xmb.theme === 'month' ? xmbMonthColors(now.getMonth(), now.getHours()) : { top: '#0b5672', bottom: '#052433' };
  $('xmb').style.setProperty('--xmb-top', colors.top);
  $('xmb').style.setProperty('--xmb-bottom', colors.bottom);
}
function xmbWaves() {
  const canvas = $('xmb-waves'), ctx = canvas.getContext('2d');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const frame = (time) => {
    const w = canvas.clientWidth, h = canvas.clientHeight, dpr = devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const s = still ? 0 : time / 1000;
    for (let k = 0; k < 3; k++) {
      ctx.beginPath();
      for (let x = 0; x <= w; x += 8) {
        const y = h * (.62 + k * .05) + Math.sin(x / w * 3.2 + s * (.35 + k * .12) + k) * h * .06 + Math.sin(x / w * 7 + s * .5) * h * .012;
        if (x) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      }
      ctx.strokeStyle = `rgba(255,255,255,${.22 - k * .05})`;
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
      ctx.fillStyle = `rgba(255,255,255,${.035 - k * .008})`;
      ctx.fill();
    }
    if (!still && !document.hidden) requestAnimationFrame(frame);
    else if (!still) document.addEventListener('visibilitychange', () => requestAnimationFrame(frame), { once: true });
  };
  requestAnimationFrame(frame);
}
function xmbEscape(text) { return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]); }
xmbApplyTheme();
setInterval(xmbApplyTheme, 60000);
xmbWaves();
xmbLoadSettings();

function appNewClientId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, '0')).join('');
}
ui.appClientId = appNewClientId();
$('dock-connect-close').addEventListener('click', closeDockApp);
$('dock-connect-refresh').addEventListener('click', refresh);
$('dock-connect-prepare').addEventListener('click', requestStorageSetup);
document.querySelectorAll('[data-session-choice]').forEach((button) => {
  button.addEventListener('click', () => chooseProductSession(button.dataset.sessionChoice));
});
$('dock-session-review').addEventListener('click', reviewProductSaves);
$('dock-session-retry').addEventListener('click', retryProductSession);
$('dock-session-refresh').addEventListener('click', refresh);
$('dock-session-close').addEventListener('click', closeDockApp);
window.addEventListener('pagehide', appPageLeaving);
window.addEventListener('pageshow', () => {
  if (!ui.appPageHidden || ui.appClosed) return;
  ui.appPageHidden = false;
  // A document restored from the back/forward cache is a new lease.
  // Its former ID may have a server-side tombstone from pagehide.
  ui.appClientId = appNewClientId();
  void appHeartbeat();
});
void appHeartbeat();
xmbRender();

function updateClock() {
  // Como la PSP: día/mes y hora.
  const now = new Date();
  $('clock').textContent = `${now.getDate()}/${now.getMonth() + 1} ${new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(now)}`;
}
updateClock();
setInterval(updateClock, 30000);
refresh();
setInterval(() => {
  // A session choice may keep its POST open while the worker copies.
  // GET snapshots remain readable and must keep its progress visible.
  if (!document.hidden && (!ui.busy || ui.state?.product_flow?.enabled === true)) refresh();
}, 3000);
requestAnimationFrame(gamepadLoop);
void pollNativePspInput();
