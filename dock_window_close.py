"""Finish a native session from the close button or one restored window.

Chrome owns its caption button, so this adapter does not intercept WM_CLOSE
or subclass another process. The host calls it only after its own observed
app window disappears. It restores that same localhost UI once, records the
exit request on the PC, and lets Dock's existing worker perform the normal
verified return. The close button queues that same intent while keeping the
current window and lease alive. A second X drains and leaves recovery data.
"""
from __future__ import annotations

import threading


class NativeWindowClose:
    def __init__(self, dock, *, persist):
        self.dock = dock
        self.persist = persist
        self._lock = threading.RLock()
        self._restored_once = False
        self._requested = False
        self._visible = False
        self._abandoned = False
        self._complete = False
        self._status = ''
        self._reason = ''
        self._identity = {}

    def _record(self, status):
        if status == self._status:
            return
        self.persist({
            'schema':'pspdock-window-close-v1', 'status':status,
            'reason':self._reason, 'identity':dict(self._identity),
            'restore_attempts':int(self._restored_once),
            'exit_requested':self._requested, 'recovery_preserved':not self._complete,
        })
        self._status = status

    def pending(self):
        with self._lock:
            return self._requested and not self._abandoned and not self._complete

    def request(self, *, reason='button', visible=True):
        """Keep an existing window open while the worker finishes its session.

        No data lock, USB command or copy is performed here. In particular a
        click during a transfer can queue the exit without waiting for that
        transfer's lock. A repeated click cannot restart a return or cancel.
        """
        state = self.dock.product_flow.snapshot()
        if not state['enabled'] or state['phase'] in ('waiting_psp','safe_to_disconnect'):
            return False
        with self._lock:
            if self.pending():
                return True
            if self._abandoned or self._complete:
                raise ValueError('El cierre anterior terminó; revisa el estado de la aplicación.')
            current = self.dock.native_transport.current
            identity = ({key:current[key] for key in ('session_id','volume_serial')}
                        if current else dict(self.dock.product_session.entry.get('identity', {})))
            self._reason, self._identity = reason, identity
            self._requested = True
            try:
                self._record('close_requested')
            except (OSError, ValueError):
                self._requested = False
                raise
            self._visible = visible
            return True

    def restore_once(self, lifecycle, restore):
        """Return True only when shutdown was deferred for one UI restore.

        This runs without Dock's data lock so a copying worker cannot leave
        the user looking at an empty desktop until a long transfer finishes.
        It neither requests a USB phase nor starts any save operation.
        """
        state = self.dock.product_flow.snapshot()
        if (lifecycle.reason == 'button' or not state['enabled'] or
                state['phase'] in ('waiting_psp','safe_to_disconnect')):
            return False
        with self._lock:
            if self._restored_once:
                self.abandon('second_window_close')
                return False
            self._restored_once = True
            self._requested = True
            self._visible = False
            self._reason = lifecycle.reason
            current = self.dock.native_transport.current
            if current:
                self._identity = {key:current[key] for key in ('session_id','volume_serial')}
            else:
                self._identity = dict(self.dock.product_session.entry.get('identity', {}))
            try:
                # Durable intent is required before restoring or asking the
                # normal worker to finish the session.
                self._record('restoring_window')
                lifecycle.defer_for_window_restore()
                if restore() is False:
                    raise OSError('No se pudo restaurar la ventana de PSP Dock.')
            except (OSError, ValueError) as exc:
                self._abandoned = True
                self._reason = str(exc)
                try:
                    self._record('restore_failed')
                except OSError:
                    pass
                lifecycle.cancel_window_restore()
                return False
            return True

    def confirm_visible(self, *, clients):
        """Require both the host HWND observation and a new UI heartbeat."""
        with self._lock:
            if self.pending() and not self._visible and clients > 0:
                self._record('window_restored')
                self._visible = True

    def abandon(self, reason):
        """Second X / failed restoration: never authorize another operation."""
        with self._lock:
            if not self._requested or self._complete or self._abandoned:
                return
            self._abandoned = True
            self._reason = reason
            try:
                self._record('forced_closed')
            except OSError:
                # Existing product/session journals still retain every
                # unfinished operation. A journal error cannot restart one.
                pass

    def advance(self):
        """Called only by Dock's existing worker, while holding its data lock."""
        with self._lock:
            if not self.pending() or not self._visible:
                return False
            if self.dock.running():
                self._record('waiting_for_emulator')
                return False
            state = self.dock.product_flow.snapshot()
            phase = state['phase']
            if self.dock.product_flow.can_cancel_entry() and not state.get('cancel_requested'):
                # An exit before the first controller session does not
                # authorize importing or returning saves. The product
                # callback finishes admitted work, then only ejects.
                self.dock.product_session.request_cancel()
                self._record('cancelling_entry')
                return True
            if phase == 'ready':
                self.dock.request_exit()
                self._record('return_requested')
                return True
            if phase == 'safe_to_disconnect':
                self._complete = True
                self._record('verified_closed')
                return False
            if phase == 'attention':
                self._record('review_required')
            return False

    def decorate(self, flow):
        """Presentation facts only: no I/O, USB requests or process polling."""
        with self._lock:
            result = dict(flow)
            pending = self.pending()
            message = ''
            if pending:
                waiting_game = bool(self.dock.recovery_required or
                                    self.dock.session.get('running') or self.dock.process is not None)
                if waiting_game:
                    message = ('Has pedido salir. Cierra el emulador normalmente para devolver las partidas.'
                               if not self.dock.recovery_required else
                               'Has pedido salir. Comprueba que el emulador anterior está cerrado y revisa la recuperación antes de devolver las partidas.')
                    result.update(phase='waiting_game_exit', blocking=True,
                                  can_choose=False, can_review_saves=False,
                                  can_retry=False, can_close=False, message=message)
                else:
                    if not flow.get('entry_completed', False):
                        message = ('Has pedido salir antes de empezar. El Dock terminará la operación en curso '
                                   'y cerrará la conexión; no iniciará otra sincronización.')
                    else:
                        message = ('Has pedido salir. Revisa lo pendiente; la ventana se cerrará cuando las partidas estén verificadas y la PSP expulsada.'
                                   if flow['phase']=='attention' else
                                   'Has pedido salir. El Dock terminará la operación y devolverá las partidas antes de cerrar.')
                    result['can_choose'] = False
                    if flow['phase'] == 'ready':
                        result.update(phase='waiting_storage_return', blocking=True,
                                      can_review_saves=False, can_retry=False, can_close=False)
            result.update(window_close_requested=pending, window_close_message=message)
            return result
