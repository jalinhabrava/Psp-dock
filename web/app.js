'use strict';

const $ = (id) => document.getElementById(id);
const ui = {
  state: null, selected: null, token: null, busy: false, serviceOffline: false,
  signature: '', savesSignature: '', devicesSignature: '', transfer: [], resolve: null,
  toastTimer: null, gamepadAt: 0, gamepadDirection: '', gamepadHeld: {},
  gamepadId: '', skippedCopies: new Set(), operation: '',
  transferInFlight: false, transferRun: 0, transferPollTimer: null,
  transferStatus: null, transferCancelRequested: false, transferCancelling: false,
  transferFinished: false,
  nativePadConnected: false, nativePadLastAt: 0,
  nativePadHeld: {}, nativePadDirection: '', nativePadAt: 0,
  graphicsGame: null,
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
  try { result = await response.json(); } catch { throw new Error('El servicio local no ha devuelto una respuesta válida.'); }
  if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
  return result;
}
async function mutate(path, body, success) {
  if (ui.busy) return false;
  if (ui.serviceOffline) { toast('Reconecta el servicio local de PSP Dock antes de continuar.'); return false; }
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
    if (path === '/api/prepare-ppsspp' || path === '/api/transfer') await refresh();
    const cancelledTransfer = path === '/api/transfer' &&
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
  try {
    const state = await request('/api/state');
    ui.state = state;
    ui.serviceOffline = false;
    $('error').hidden = !state.error && !state.recovery_required;
    $('error').textContent = state.recovery_required ? 'Sesión interrumpida: revisa PPSSPP y la recuperación antes de continuar.' : state.error || '';
    render();
  } catch (error) {
    ui.serviceOffline = true;
    $('error').hidden = false;
    $('error').textContent = `Servicio local no disponible. ${error.message}`;
    if (ui.state) render();
    else {
      $('device-status').classList.add('offline');
      $('device-label').textContent = 'Servicio sin conexión';
      $('device-role').textContent = 'RECONEXIÓN PENDIENTE';
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
    label.textContent = 'Servicio local sin conexión';
    role.textContent = 'NO SE PUEDEN REALIZAR OPERACIONES';
  } else if (state.scanning) {
    label.textContent = 'Buscando PSP…';
    role.textContent = 'LEYENDO EL ESTADO DEL DOCK';
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
    state.selected_device ? 'No se han encontrado juegos. Comprueba la carpeta ISO de la PSP y la biblioteca configurada.' :
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
    const missing = ui.transfer.filter((game) =>
      games.some((current) => current.id === game.id && current.availability === 'missing'));
    if (missing.length !== ui.transfer.length) {
      ui.transfer = missing;
      renderTransferSelection();
      renderTransferControls();
    }
  }
  const pending = games.filter((game) => game.availability === 'missing');
  $('transfer-all').hidden = !pending.length;
  $('transfer-all').disabled = !state.selected_device || state.recovery_required || state.session?.running || ui.serviceOffline;
  $('session-status').textContent = ui.serviceOffline ? 'Servicio desconectado · las operaciones están pausadas' :
    state.recovery_required ? 'Sesión interrumpida · recupera PPSSPP antes de continuar' :
      state.session?.running ? '● PPSSPP en ejecución · vuelve al Dock al terminar' :
        state.session?.pending_return && state.session?.exit_requested ?
          'Salida solicitada · devuelve manualmente el almacenamiento para revisar las partidas' :
        state.session?.pending_return && state.controller_status?.connected ?
          'De vuelta en el XMB · puedes jugar a otro título o elegir Salir del Dock' :
        state.session?.pending_return ? 'Partida terminada · vuelve a almacenamiento para devolver los guardados' :
          state.controller_status?.connected ?
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
}
function preflightKey(state) {
  return JSON.stringify([
    state.selected_device,
    (state.games || []).filter(g => g.availability === 'missing').map(g => [g.id, g.size, g.modified_ns]),
  ]);
}
function renderRuntimeReadiness(state, prepared) {
  const panel = $('runtime-readiness');
  const known = typeof state.runtime_prepared === 'boolean';
  panel.hidden = state.demo || !known || !(state.selected_device || prepared ||
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
  else if (ready) label.textContent = 'PPSSPP preparado en el PC. No tendrás que prepararlo al activar el mando HID.';
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
  const skipped = state.copy_policy === 'skip_missing' ||
    ui.skippedCopies.has(preflightKey(state));
  const prepared = plan.saves_prepared === true || state.saves_prepared === true;
  // An explicit Exit is valid even if the user only navigated the XMB
  // without launching a game. Persistent HID should never trap them in
  // a menu merely because there are no changed saves to return.
  const returnPending = !!(state.session?.pending_return ||
    state.session?.exit_requested);
  const playing = !!state.session?.running;
  const inHidXmb = !playing && !state.session?.exit_requested &&
    state.controller_status?.connected === true && prepared;
  panel.hidden = disconnected && !state.games?.length && !returnPending && !state.recovery_required && !state.controller_status?.connected;
  renderRuntimeReadiness(state, prepared);
  $('preflight-summary').textContent = [
    plan.ready_games + ' en PC',
    plan.missing_games + ' por copiar',
    plan.connected || state.demo ?
      plan.save_actions + ' guardados pendientes · ' + plan.save_conflicts + ' conflictos' :
      returnPending ? 'Partidas por comparar al volver al almacenamiento' : 'Partidas sin comparar (PSP desconectada)',
  ].join(' · ');
  $('preflight-kicker').textContent = state.demo ? 'MODO DEMOSTRACIÓN' :
    plan.connected ? 'ALMACENAMIENTO USB DETECTADO' : 'PSP DOCK · ESTADO DEL DISPOSITIVO';
  let title = 'Preparando el inventario de tu PSP';
  let description = plan.reason || 'Comprobando juegos y guardados.';
  if (ui.serviceOffline) {
    title = 'Servicio local desconectado';
    description = 'Las operaciones están pausadas hasta recuperar la conexión con PSP Dock.';
  } else if (state.demo) {
    title = 'Demostración del modo Dock';
    description = 'Recorre la biblioteca sintética. La demo no escribe partidas reales.';
  } else if (state.recovery_required) {
    title = 'Necesitas recuperar una sesión anterior';
    description = 'Revisa si PPSSPP sigue abierto y completa la recuperación antes de copiar o sincronizar archivos.';
  } else if (playing) {
    title = 'La partida está en marcha';
    description = 'PPSSPP está ejecutándose. Cuando cierres el juego volverás a este XMB para elegir otro o salir del Dock.';
  } else if (inHidXmb) {
    title = state.session?.pending_return ?
      'De vuelta en el XMB · ¿otra partida?' :
      'PSP Dock activo · elige cuándo jugar';
    description = state.runtime_prepared === true ?
      'Mando PSP activo sin límite de tiempo. Selecciona un juego o elige Salir del Dock cuando quieras terminar; el cambio a almacenamiento todavía es manual.' :
      'PPSSPP necesita volver a prepararse en el PC. Sal del Dock y vuelve manualmente a almacenamiento USB antes de continuar.';
  } else if (returnPending) {
    title = disconnected ? 'Vuelve a conectar tu Memory Stick' : 'Devolución de partidas pendiente';
    description = disconnected ?
      state.session?.return_sync_authorized ?
        'Salida autorizada: mantén START+SELECT en la PSP para cerrar HID, retira el cable con seguridad y vuelve manualmente al USB Sony original. PSP Dock devolverá las partidas si no hay conflictos.' :
        'Sal del modo HID en la PSP y vuelve manualmente al almacenamiento USB de la misma Memory Stick. Entonces podrás comprobar y devolver los guardados.' :
      plan.save_conflicts ? 'Hay versiones de partidas diferentes. Elige la correcta antes de devolver los guardados.' :
      plan.save_actions ? 'Comprueba los cambios y confirma las copias pendientes antes de desconectar.' :
        'No hay diferencias de partidas. Confirma la comprobación final para cerrar la devolución.';
  } else if (state.session?.return_result === 'verified' && plan.connected) {
    title = 'Partidas devueltas y verificadas';
    description = 'La PSP y el PC están sincronizados. Antes de quitar el cable, usa la extracción segura del almacenamiento de Windows. El cambio automático de USB sigue pendiente.';
  } else if (state.controller_status?.connected) {
    title = 'Mando PSP HID detectado en Windows';
    description = prepared && state.runtime_prepared === true ?
      'Mando HID PDK1 detectado y PPSSPP preparado. Puedes elegir un juego local; este modo se activa manualmente.' :
      prepared ? 'Las partidas están preparadas pero PPSSPP sigue pendiente en el PC. Vuelve al almacenamiento USB y prepara PPSSPP antes de jugar.' :
      'Mando detectado; todavía hace falta preparar las partidas en modo almacenamiento antes de jugar.';
  } else if (disconnected && prepared) {
    title = state.runtime_prepared === true ? 'Partidas y PPSSPP preparados · esperando mando' : 'Partidas listas · PPSSPP pendiente';
    description = state.runtime_prepared === true ?
      'El almacenamiento ya no está disponible. Activa manualmente el mando HID PDK1 en tu PSP y vuelve al Dock para iniciar PPSSPP.' :
      'Antes de usar el modo HID, vuelve al almacenamiento USB y utiliza «Preparar PPSSPP» en el PC.';
  } else if (disconnected) {
    title = 'Biblioteca de la última PSP';
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
    state.controller_status?.connected ?
      'HID PDK1 comprobado; es una conexión manual. El puente automático entre USB y mando aún está pendiente.' :
      'Mando PSP: el cambio automático USB almacenamiento ↔ HID todavía no está implementado.';
  const canAct = !!plan.connected && !state.recovery_required && !playing && !ui.busy && !ui.serviceOffline;
  $('preflight-copy').hidden = !canAct || !plan.missing_games || returnPending;
  $('preflight-saves').hidden = !canAct || !(returnPending || plan.save_actions || plan.save_conflicts || !prepared);
  const canRetryRuntime = !!state.selected_device && !state.demo && !state.scanning &&
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
  const canLaunch = !ui.serviceOffline && !state.scanning && !state.demo && !!state.can_launch_game &&
    state.runtime_prepared === true && manualHid && game.availability === 'ready' &&
    !!state.emulator?.available && !state.session?.running &&
    !state.session?.exit_requested && !state.recovery_required && !pendingSaves && prepared;
  $('launch-button').disabled = !canLaunch;
  let note = 'Mando PSP HID PDK1 detectado. El juego se abrirá en PPSSPP con la biblioteca local; la conexión HID es manual.';
  if (ui.serviceOffline) note = 'El servicio local está desconectado. Reconéctalo para jugar.';
  else if (state.recovery_required) note = 'Hay una sesión interrumpida. Recupérala antes de jugar.';
  else if (state.session?.running) note = 'PPSSPP está abierto. Termina la sesión antes de lanzar otra partida.';
  else if (state.session?.pending_return && (state.session?.exit_requested || !manualHid))
    note = 'La salida del Dock está pendiente. Vuelve a almacenamiento USB y confirma la devolución de partidas.';
  else if (state.demo) note = 'Biblioteca sintética: estos juegos no se pueden ejecutar.';
  else if (game.availability === 'missing') note = state.selected_device ?
    'Este juego está en la PSP. Cópialo al PC tras revisar la transferencia.' :
    'Este juego aún no está en el PC. Vuelve a almacenamiento USB si quieres copiarlo.';
  else if (game.availability === 'different') note = 'Las versiones difieren; revisa los archivos manualmente. No se sobrescribe ningún juego.';
  else if (pendingSaves) note = 'Revisa y confirma los guardados pendientes antes de abrir PPSSPP.';
  else if (!prepared) note = state.selected_device ?
    'Confirma la preparación de partidas, incluso si no hay archivos por copiar.' :
    'Reconecta la PSP en almacenamiento para revisar y preparar las partidas.';
  else if (!state.emulator?.available) note = 'Configura la ruta de PPSSPP en Ajustes para ejecutar este juego.';
  else if (state.runtime_prepared !== true) note = state.selected_device ?
    state.runtime_error ?
      'La preparación de PPSSPP en el PC ha fallado. Las partidas están listas; pulsa «Preparar PPSSPP» para reintentarlo.' :
      'PPSSPP aún no está preparado en el PC. Pulsa «Preparar PPSSPP» antes de activar el mando HID.' :
    'PPSSPP no está preparado. Vuelve al almacenamiento USB para prepararlo y evita gastar la sesión HID.';
  else if (state.session?.pending_return && manualHid && !state.session?.exit_requested)
    note = 'Has vuelto al XMB. Puedes jugar a otro título con el mismo mando HID o elegir Salir del Dock para devolver los guardados.';
  else if (state.selected_device) note = 'Las partidas están preparadas. Termina el almacenamiento USB y activa manualmente el mando HID PDK1 antes de jugar.';
  else if (!manualHid) note = 'Esperando el mando PSP HID PDK1 en Windows. El cambio desde almacenamiento USB se realiza manualmente.';
  else if (!state.can_launch_game) note = 'Falta una verificación del lanzador. Revisa el estado de la sesión antes de continuar.';
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
  if (state.saves?.actions?.length || state.saves?.conflicts?.length || state.preflight?.saves_prepared !== true) {
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
  if (!state.can_launch_game || state.controller_status?.connected !== true) {
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
  $('transfer-summary').textContent = ui.transfer.length ?
    `${ui.transfer.length} ${ui.transfer.length === 1 ? 'juego pendiente' : 'juegos pendientes'} · ${bytes(total)} en total.` :
    'No quedan juegos seleccionados pendientes de copiar en el PC.';
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
  $('transfer-confirm').textContent = ui.transferFinished ? 'Copiar juegos pendientes' : 'Confirmar copia';
}
function renderTransferStatus(status) {
  if (!status || typeof status !== 'object') return;
  const phase = ['idle', 'copying', 'verifying', 'done', 'cancelled', 'failed'].includes(status.phase) ?
    status.phase : 'idle';
  const total = Math.max(0, Number(status.total_bytes) || 0);
  const copied = Math.max(0, Math.min(total, Number(status.copied_bytes) || 0));
  const index = Math.max(0, Math.trunc(Number(status.game_index) || 0));
  const count = Math.max(0, Math.trunc(Number(status.games_total) || 0));
  const item = count ? `juego ${Math.min(index || 1, count)} de ${count}` : 'juegos';
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
    transferFeedback('Todos los juegos seleccionados se han copiado y verificado en el PC.');
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
  $('transfer-progress-bytes').textContent = 'Esperando datos del servicio local…';
  transferFeedback('');
  renderTransferControls();
  void pollTransferStatus(run);
  let completed = false;
  try {
    completed = await mutate('/api/transfer', { ids: ui.transfer.map((game) => game.id) },
      'Juegos copiados y verificados en el PC.');
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
        transferFeedback('La copia no llegó a comenzar. Revisa el error del servicio local antes de reintentar.');
      }
    } catch {
      transferFeedback('No se pudo consultar el estado final. Comprueba la biblioteca antes de reintentar.');
    }
    ui.transferFinished = true;
    ui.transfer = ui.transfer.filter((game) =>
      ui.state?.games?.some((current) => current.id === game.id && current.availability === 'missing'));
    renderTransferSelection();
    renderTransferControls();
  }
  if (completed) $('transfer-dialog').close();
}
function openTransfer(ids) {
  if (ui.transferInFlight || ui.busy || $('transfer-dialog').open || ui.serviceOffline ||
      !ui.state?.selected_device || ui.state?.recovery_required || ui.state?.session?.running ||
      ui.state?.session?.pending_return) return;
  ui.transfer = (ui.state?.games || []).filter((game) => ids.includes(game.id) && game.availability === 'missing');
  if (!ui.transfer.length) return;
  ui.transferFinished = false;
  ui.transferStatus = null;
  ui.transferCancelRequested = false;
  ui.transferCancelling = false;
  renderTransferSelection();
  renderTransferControls();
  $('transfer-dialog').showModal();
}
function openSaves() {
  if ($('saves-dialog').open) return;
  renderSaves();
  $('saves-dialog').showModal();
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
  if (ui.serviceOffline) return 'Operación enviada; reconecta el servicio para comprobar el resultado.';
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
  mutate('/api/exit-dock', {},
    'Salida solicitada. Mantén START+SELECT 1,5 segundos en la PSP, termina HID y vuelve manualmente al USB Sony para devolver las partidas.');
}
function renderSaves() {
  const saves = ui.state?.saves || { actions: [], conflicts: [], unchanged: 0 };
  const signature = JSON.stringify([saves, ui.state?.selected_device, ui.state?.recovery_required,
    ui.state?.session?.running, ui.state?.session?.pending_return, ui.state?.preflight?.saves_prepared,
    ui.serviceOffline, ui.busy, ui.operation]);
  if (signature === ui.savesSignature) return;
  ui.savesSignature = signature;
  const content = $('saves-content');
  content.replaceChildren();
  const actions = saves.actions || [];
  const conflicts = saves.conflicts || [];
  const returnPending = !!ui.state?.session?.pending_return;
  const prepared = ui.state?.preflight?.saves_prepared === true;
  content.append(el('p', 'save-summary', ui.state?.selected_device ?
    `${actions.length} por sincronizar · ${conflicts.length} ${conflicts.length === 1 ? 'conflicto' : 'conflictos'} · ${saves.unchanged || 0} sin cambios` :
    'Sin comparación actual · requiere almacenamiento USB de la PSP'));
  if (!actions.length && !conflicts.length) {
    const message = !ui.state?.selected_device ? returnPending && ui.state?.controller_status?.connected && !ui.state?.session?.exit_requested ?
      'Primero elige Salir del Dock. Después regresa manualmente al almacenamiento USB de la misma PSP.' :
      ui.state?.session?.exit_requested ?
      'La salida del Dock ya está solicitada. Cambia manualmente la PSP a almacenamiento USB y vuelve a conectar la misma Memory Stick.' :
      'Conecta la PSP en modo almacenamiento para comprobar las partidas.' :
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
      const button = el('button', '', `Usar ${choice.toUpperCase()}`);
      button.type = 'button';
      button.disabled = ui.busy || !!ui.state?.recovery_required || !ui.state?.selected_device ||
        !!ui.state?.session?.running || ui.serviceOffline;
      button.addEventListener('click', () => {
        ui.resolve = { slot: conflict.slot, choice };
        $('resolve-summary').textContent = `Conservar la partida «${conflict.slot}» de ${choice === 'pc' ? 'este PC' : 'la PSP'} y reemplazar la otra versión.`;
        $('resolve-dialog').showModal();
      });
      buttons.append(button);
    });
    row.append(text, buttons);
    content.append(row);
  });
  const canSync = !!ui.state?.selected_device && !ui.state?.recovery_required && !ui.state?.session?.running &&
    !ui.busy && !ui.serviceOffline && (actions.length > 0 || !conflicts.length && (returnPending || !prepared));
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
async function openGraphics() {
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
    for (const key of ['emulator', 'library', 'memstick', 'psp_root', 'scale', 'backend']) {
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
  const returning = !!ui.state?.session?.pending_return;
  const ok = await mutate('/api/sync', {}, (next) => syncResultMessage(next, returning));
  if (ok && !ui.serviceOffline && ui.state?.preflight?.saves_prepared &&
      !ui.state?.saves?.actions?.length && !ui.state?.saves?.conflicts?.length) $('saves-dialog').close();
});
$('resolve-confirm').addEventListener('click', async () => {
  const returning = !!ui.state?.session?.pending_return;
  if (ui.resolve && await mutate('/api/resolve', ui.resolve, (next) => {
    if (next?.preflight?.saves_prepared) return syncResultMessage(next, returning);
    return 'Conflicto resuelto. Revisa las partidas que queden pendientes.';
  })) {
    $('resolve-dialog').close();
    if (!ui.serviceOffline && ui.state?.preflight?.saves_prepared &&
        !ui.state?.saves?.actions?.length && !ui.state?.saves?.conflicts?.length) $('saves-dialog').close();
  }
});
document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(button.dataset.close).close()));
document.addEventListener('keydown', (event) => {
  if (ui.busy || document.querySelector('dialog[open]') || event.altKey || event.ctrlKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
  if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); moveSelection(event.key === 'ArrowRight' ? 1 : -1); }
  if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault();
    const games = ui.state?.games || [];
    if (games.length) selectGame(games[event.key === 'Home' ? 0 : games.length - 1].id, true);
  }
  if (event.key === 'ArrowDown' && ui.selected) {
    event.preventDefault();
    focusGameAction(1);
  }
  if (event.key === 'ArrowUp' && ui.selected) {
    event.preventDefault();
    focusGameAction(-1);
  }
  if (event.key === 'Escape') document.querySelector('.game-card.selected')?.focus({ preventScroll: true });
  if (event.key === 'Enter' && event.target.classList.contains('game-card')) { event.preventDefault(); launch(); }
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
  // On PSP HID prefer the Windows-native WinMM source to the browser:
  // otherwise one physical CROSS can fire twice (WinMM + Gamepad API).
  if (ui.nativePadConnected && Date.now() - ui.nativePadLastAt < 600) {
    requestAnimationFrame(gamepadLoop);
    return;
  }
  let pad = null;
  try { pad = navigator.getGamepads && Array.from(navigator.getGamepads()).find((item) => item?.connected !== false) || null; }
  catch { /* Some browsers do not allow polling before controller interaction. */ }
  const identity = pad ? `${pad.index}:${pad.id}` : '';
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
      'Mando HID en Windows · pulsa un botón para activar la navegación' :
      'Mando del navegador: sin detectar';
  if ($('gamepad-status').textContent !== status) $('gamepad-status').textContent = status;
  if (pad) {
    const input = controllerInput(pad);
    const edge = (name) => input[name] && !ui.gamepadHeld[name];
    const direction = input.left ? 'left' : input.right ? 'right' : input.up ? 'up' : input.down ? 'down' : '';
    const navigate = direction && (direction !== ui.gamepadDirection || time - ui.gamepadAt > 230);
    const dialog = Array.from(document.querySelectorAll('dialog[open]')).at(-1);
    if (!ui.busy) {
      if (edge('back')) {
        if (dialog) dialog.close();
        else document.querySelector('.game-card.selected')?.focus({ preventScroll: true });
      } else if (dialog) {
        if (navigate) {
          navigateDialogControl(dialog, direction);
          ui.gamepadAt = time;
        } else if (edge('accept')) {
          const target = document.activeElement;
          if (target?.tagName === 'BUTTON' && !target.disabled) target.click();
          else focusDialogControl(dialog, 1);
        }
      } else if (!ui.serviceOffline) {
        if (edge('settings')) openSettings();
        else if (edge('graphics')) openGraphics();
        else if (edge('saves')) openSaves();
        else if (edge('pageLeft') || edge('pageRight')) moveSelection(edge('pageRight') ? 5 : -5);
        else if (navigate) {
          if (direction === 'left' || direction === 'right') moveSelection(direction === 'right' ? 1 : -1);
          else focusGameAction(direction === 'down' ? 1 : -1);
          ui.gamepadAt = time;
        } else if (edge('accept')) {
          const target = document.activeElement;
          if (target?.tagName === 'BUTTON' && !target.disabled &&
              !target.classList.contains('game-card') &&
              target.getClientRects().length) target.click();
          else if (ui.state?.games?.length) launch();
          else $('refresh-button').click();
        }
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
    if (edge('back')) {
      // Closing the progress dialog by Circle must not mask an active
      // verified copy. The user must choose its Cancelar copia action.
      if (dialog && !(dialog.id === 'transfer-dialog' && ui.transferInFlight)) dialog.close();
      else if (!dialog) document.querySelector('.game-card.selected')?.focus({ preventScroll: true });
    } else if (dialog) {
      if (navigate) {
        navigateDialogControl(dialog, direction);
        ui.nativePadAt = now;
      } else if (edge('accept')) {
        const target = document.activeElement;
        if (target?.tagName === 'BUTTON' && !target.disabled) target.click();
        else focusDialogControl(dialog, 1);
      }
    } else if (edge('exit')) {
      // SELECT only focuses the Exit action; CROSS explicitly confirms it.
      const button = $('preflight-exit-dock');
      if (button && !button.hidden && !button.disabled) button.focus({ preventScroll: true });
    } else if (edge('settings')) openSettings();
    else if (edge('graphics')) openGraphics();
    else if (edge('saves')) openSaves();
    else if (edge('pageLeft') || edge('pageRight')) moveSelection(edge('pageRight') ? 5 : -5);
    else if (navigate) {
      if (direction === 'left' || direction === 'right') moveSelection(direction === 'right' ? 1 : -1);
      else focusGameAction(direction === 'down' ? 1 : -1);
      ui.nativePadAt = now;
    } else if (edge('accept')) {
      const target = document.activeElement;
      if (target?.tagName === 'BUTTON' && !target.disabled &&
          !target.classList.contains('game-card') &&
          target.getClientRects().length) target.click();
      else if (ui.state?.games?.length) launch();
      else $('refresh-button').click();
    }
  }
  ui.nativePadHeld = input;
  ui.nativePadDirection = direction;
}
async function pollNativePspInput() {
  try {
    if (!document.hidden && !ui.serviceOffline) {
      const frame = await request('/api/controller-input');
      if (frame.connected === true && frame.source === 'psp_winmm') {
        if (!ui.nativePadConnected) {
          // First frame only establishes initial held inputs. This is
          // essential: holding CROSS while plugging in USB never opens
          // the first game or presses a preflight button.
          ui.nativePadHeld = frame;
          ui.nativePadDirection = frame.left ? 'left' : frame.right ? 'right' :
            frame.up ? 'up' : frame.down ? 'down' : '';
          ui.nativePadAt = Date.now();
          ui.gamepadId = '';
        } else {
          processNativePspFrame(frame, Date.now());
        }
        ui.nativePadConnected = true;
        ui.nativePadLastAt = Date.now();
        $('gamepad-status').textContent = 'PSP HID · control directo de Windows';
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
    setTimeout(pollNativePspInput, ui.nativePadConnected ? 80 : 400);
  }
}
function updateClock() { $('clock').textContent = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(new Date()); }
updateClock();
setInterval(updateClock, 30000);
refresh();
setInterval(() => { if (!document.hidden && !ui.busy) refresh(); }, 3000);
requestAnimationFrame(gamepadLoop);
void pollNativePspInput();
