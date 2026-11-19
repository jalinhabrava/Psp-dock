"""Host UI leases: no USB polling, process launching or save operations."""
from __future__ import annotations

import re
import threading
import time


class UiLifecycle:
    """A visible app owns the service; closing one tab cannot close another."""

    def __init__(self, *, now=time.monotonic, lease_seconds=90,
                 startup_seconds=30, close_grace_seconds=3):
        self.now = now
        self.lease_seconds = lease_seconds
        self.startup_seconds = startup_seconds
        self.close_grace_seconds = close_grace_seconds
        self.started = now()
        self.clients = {}
        self.closed_clients = {}
        self.ever_connected = False
        self.multiple_clients_seen = False
        self.native_seen = False
        self.closed_at = None
        self.reason = ''
        self.stopping = False
        self.lock = threading.RLock()

    @staticmethod
    def client_id(value):
        if not isinstance(value, str) or re.fullmatch('[0-9a-f]{32}', value) is None:
            raise ValueError('Identificador de ventana no válido.')
        return value

    def heartbeat(self, client_id):
        client_id = self.client_id(client_id)
        with self.lock:
            if self.stopping:
                raise ValueError('La app se está cerrando; espera a que termine.')
            stamp = self.now()
            self.closed_clients = {key: value for key, value in self.closed_clients.items()
                                   if stamp - value < 120}
            # A keepalive request already in flight must not resurrect a
            # document whose pagehide/close was processed first.
            if client_id in self.closed_clients:
                return {'ok': True, 'ignored': True, 'clients': len(self.clients),
                        'closing': False}
            if client_id not in self.clients and len(self.clients) >= 8:
                raise ValueError('Hay demasiadas ventanas de PSP Dock abiertas.')
            self.clients[client_id] = stamp
            self.ever_connected = True
            self.multiple_clients_seen |= len(self.clients) > 1
            self.closed_at = None
            self.reason = ''
            return {'ok': True, 'clients': len(self.clients), 'closing': False}

    def release(self, client_id, reason):
        client_id = self.client_id(client_id)
        if reason not in ('button', 'pagehide'):
            raise ValueError('Motivo de cierre de ventana no válido.')
        with self.lock:
            stamp = self.now()
            self.clients.pop(client_id, None)
            self.closed_clients[client_id] = stamp
            if len(self.closed_clients) > 128:
                oldest = min(self.closed_clients, key=self.closed_clients.get)
                self.closed_clients.pop(oldest)
            if not self.clients and self.closed_at is None:
                self.closed_at, self.reason = stamp, reason
            return {'ok': True, 'closing': not self.clients,
                    'other_clients': len(self.clients)}

    def snapshot(self):
        with self.lock:
            return {'clients': len(self.clients), 'connected_once': self.ever_connected,
                    'closing': self.stopping, 'reason': self.reason}

    def defer_for_window_restore(self):
        """Rearm a bounded startup lease for the same service's restored UI.

        Caller must already have observed its native app window disappear
        and have a native session to finish. Old document tombstones stay
        intact; a late heartbeat cannot stand in for the restored window.
        """
        with self.lock:
            if not self.stopping or self.clients:
                raise ValueError('El cierre de esta ventana todavía no está confirmado.')
            self.started = self.now()
            self.ever_connected = False
            self.native_seen = False
            self.closed_at = None
            self.reason = ''
            self.stopping = False

    def cancel_window_restore(self):
        with self.lock:
            self.reason = 'window_restore_failed'
            self.stopping = True

    def should_stop(self, native_visible=None):
        """Commit shutdown only after reload grace / real window disappearance.

        A minimized native app is still visible to IsWindowVisible. Its
        continued presence overrides stale JS timers. The native shortcut
        is disabled after multiple documents were seen, because a browser
        tab may share the service without an individually identifiable HWND.
        """
        with self.lock:
            if self.stopping:
                return True
            stamp = self.now()
            if native_visible is True:
                self.native_seen = True
            if not self.ever_connected:
                if stamp - self.started < self.startup_seconds:
                    return False
                self.reason, self.stopping = 'ui_start_timeout', True
                return True
            if native_visible is not True:
                self.clients = {key: value for key, value in self.clients.items()
                                if stamp - value < self.lease_seconds}
            if (self.native_seen and native_visible is False and
                    not self.multiple_clients_seen and self.clients and
                    stamp - max(self.clients.values()) >= 5):
                self.clients.clear()
                self.closed_at, self.reason = stamp, 'native_window_closed'
            if self.clients:
                return False
            # Reload and background throttling do not close a native window.
            if native_visible is True and self.reason != 'button':
                return False
            if self.closed_at is None:
                self.closed_at, self.reason = stamp, 'heartbeat_expired'
            if stamp - self.closed_at < self.close_grace_seconds:
                return False
            self.stopping = True
            return True
