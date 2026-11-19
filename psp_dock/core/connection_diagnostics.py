"""Bounded, PC-only connection timings; never a source of USB permission."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
import time


class ConnectionDiagnostics:
    def __init__(self, *, persist=None, clock=time.perf_counter, max_events=80):
        self._persist = persist
        self._clock = clock
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._next_save = 0
        self._saved_version = 0
        self._started = clock()
        self._next = 0
        self._active = {}
        self._stats = {}
        self._events = deque(maxlen=max_events)
        self._persistence_error = ''

    def _snapshot(self, *, events):
        now = self._clock()
        result = {'schema':'pspdock-connection-timing-v1',
                  'elapsed_ms':round((now-self._started)*1000, 3),
                  'active':[{'stage':row['stage'], 'elapsed_ms':round((now-row['started'])*1000, 3)}
                            for row in self._active.values()],
                  'stages':{key:dict(value) for key,value in self._stats.items()},
                  'persistence_error':self._persistence_error}
        if events:
            result['events'] = list(self._events)
        return result

    def snapshot(self, *, events=True):
        """No I/O: available while a Windows call or USB callback blocks."""
        with self._lock:
            return self._snapshot(events=events)

    def _prepare_save(self):
        """Capture under the state lock; callers persist after releasing it."""
        if self._persist is None:
            return None
        self._next_save += 1
        return self._next_save, self._snapshot(events=True)

    def _save(self, pending):
        if pending is None:
            return
        version, value = pending
        # A slow atomic write/fsync must never hold the snapshot lock. A
        # separate lock serializes writers; versioning prevents an older
        # capture from replacing newer evidence after thread scheduling.
        with self._persist_lock:
            if version <= self._saved_version:
                return
            try:
                self._persist(value)
            except (OSError, ValueError) as exc:
                # Diagnostics never change USB admission or hide its exception.
                with self._lock:
                    self._persistence_error = type(exc).__name__ + ': ' + str(exc)[:200]
            else:
                self._saved_version = version
                with self._lock:
                    self._persistence_error = ''

    @contextmanager
    def measure(self, stage, *, important=False):
        # Call sites supply fixed stage names, not device paths or file data.
        pending = None
        with self._lock:
            self._next += 1
            token, started = self._next, self._clock()
            self._active[token] = {'stage':stage, 'started':started}
            if important:
                self._events.append({'stage':stage, 'event':'started',
                    'utc':datetime.now(timezone.utc).isoformat(),
                    'at_ms':round((started-self._started)*1000, 3)})
                pending = self._prepare_save()
        # Important spans include the time needed to persist their start.
        self._save(pending)
        failure = None
        try:
            yield
        except Exception as exc:
            failure = exc
            raise
        finally:
            pending = None
            with self._lock:
                elapsed = max(0.0, (self._clock()-started)*1000)
                self._active.pop(token, None)
                before = self._stats.get(stage, {'count':0, 'errors':0, 'total_ms':0, 'max_ms':0})
                self._stats[stage] = {'count':before['count']+1,
                    'errors':before['errors']+int(failure is not None),
                    'last_ms':round(elapsed,3),
                    'total_ms':round(before['total_ms']+elapsed,3),
                    'max_ms':round(max(before['max_ms'], elapsed),3)}
                if important or failure is not None or elapsed >= 250:
                    event = {'stage':stage, 'event':'error' if failure else 'completed',
                             'duration_ms':round(elapsed,3),
                             'at_ms':round((self._clock()-self._started)*1000,3)}
                    if failure is not None:
                        event['error_type'] = type(failure).__name__
                        event['error'] = str(failure)[:1000]
                        details = getattr(failure, 'details', {})
                        # No session nonce, paths, file contents or user data.
                        allowed = ('operation','requested_phase','requested_sequence','phase','sequence',
                                   'result','pending','host_confirmed','polls','elapsed_seconds')
                        event['details'] = {key:details[key] for key in allowed
                                            if isinstance(details,dict) and key in details and
                                            isinstance(details[key],(str,int,float,bool,type(None)))}
                        for name in ('before', 'last_observed'):
                            status = details.get(name) if isinstance(details,dict) else None
                            if isinstance(status,dict):
                                event['details'][name] = {key:status[key] for key in
                                    ('phase','sequence','result','pending','host_confirmed')
                                    if key in status and type(status[key]) in (int,bool)}
                    self._events.append(event)
                    pending = self._prepare_save()
            self._save(pending)
