"""Finish an already released native PSP session without removing the cable.

The volume release has its own durable receipt. Only then may HOST_SHUTDOWN
be sent. Recovery observes the same HID/PnP identity; it never remounts a
medium or repeats a save operation. GET_REPORT has no shutdown side effect.
"""
from __future__ import annotations

from copy import deepcopy
import ctypes as C
from ctypes import wintypes as W
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import secrets
import time

from psp_dock.devices.native_dock import (NativeDockError, NativeHid, HID_PATH, USB_PARENT,
    CAP_HOST_SHUTDOWN, HOST_SHUTDOWN, STORAGE, SAFE, ERROR,
    _validate_identity, _validate_status_identity, encode_command, hid_instance)
from psp_dock.devices.native_storage_release import (release_native_storage, StorageReleaseError,
                                    verify_media_released, validate_media_release_receipt)
from psp_dock.devices.psp_usb import PspStorage, validate_identity

SCHEMA = 'pspdock-native-shutdown-v1'
_STAGES = {'release_pending', 'release_blocked', 'media_released', 'sending',
           'waiting_removal', 'complete'}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def native_device_removed(parent, path, *, api_factory=NativeHid):
    """Require both the exact HID and physical native USB node to disappear."""
    if os.name != 'nt' or not USB_PARENT.fullmatch(parent) or not HID_PATH.match(path):
        raise NativeDockError('No se puede verificar la retirada del dispositivo nativo.')
    api = api_factory()
    if path.casefold() in {item.casefold() for item in api.paths()}:
        return False
    cfg = C.WinDLL('cfgmgr32', use_last_error=True)
    cfg.CM_Locate_DevNodeW.argtypes = [C.POINTER(W.DWORD), W.LPWSTR, W.ULONG]
    cfg.CM_Locate_DevNodeW.restype = W.DWORD
    node = W.DWORD()
    result = int(cfg.CM_Locate_DevNodeW(C.byref(node), parent, 0))
    if result == 0:
        return False
    if result != 0x0D:  # CR_NO_SUCH_DEVNODE; other failures are not absence.
        raise NativeDockError('Windows no pudo comprobar la retirada USB: ' + hex(result))
    return True


class NativeShutdown:
    def __init__(self, path, *, api_factory=NativeHid,
                 release=release_native_storage, verify_release=verify_media_released,
                 removed=native_device_removed, persist=_atomic_json,
                 clock=time.monotonic, pause=time.sleep):
        self.path = Path(path) if path is not None else None
        self.api_factory, self.release, self.verify_release = api_factory, release, verify_release
        self.removed, self.persist = removed, persist
        self.clock, self.pause = clock, pause
        self.record = None
        if self.path is not None and self.path.exists():
            try:
                if self.path.stat().st_size > 131072:
                    raise ValueError('Registro de cierre demasiado grande.')
                value = json.loads(self.path.read_text(encoding='utf-8'))
                self._validate_record(value)
                self.record = value
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
                raise NativeDockError('El registro de cierre necesita revisión; no se accede a la tarjeta: ' + str(exc)) from exc

    @staticmethod
    def _validate_record(value):
        if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('stage') not in _STAGES:
            raise ValueError('Formato de cierre desconocido.')
        current = value['observation']
        if not isinstance(current, dict) or type(value.get('cancel')) is not bool:
            raise ValueError('Identidad o motivo de cierre inválido.')
        storage = PspStorage(**value['storage'])
        validate_identity(storage.drive, storage.volume_serial, storage.model,
                          storage.disk_id, storage.usb_device_id)
        _validate_identity(current['protocol_version'], current['firmware_revision'],
                           current['capabilities'], current['build_id'])
        if (not current['capabilities'] & CAP_HOST_SHUTDOWN or
                not isinstance(current['session_id'], str) or
                re.fullmatch('[0-9a-f]{32}', current['session_id']) is None or
                not any(bytes.fromhex(current['session_id'])) or
                not HID_PATH.match(current['path']) or
                not USB_PARENT.fullmatch(current['usb_parent']) or
                current['volume_serial'] != storage.volume_serial or current['root'] != storage.drive):
            raise ValueError('La identidad del cierre no es válida.')
        sequence = value['sequence']
        expected = encode_command(current['session_id'], sequence, SAFE, HOST_SHUTDOWN,
                                  firmware_revision=current['firmware_revision'], build_id=current['build_id'])
        if value['command_hex'] != expected.hex() or type(value.get('attempts')) is not int or value['attempts'] < 0:
            raise ValueError('La orden de cierre no coincide con la sesión registrada.')
        receipt = value.get('release')
        if receipt is not None and (not isinstance(receipt, dict) or
                receipt.get('schema') != 'pspdock-native-storage-release-v1' or
                receipt.get('identity') != value['storage'] or
                str(receipt.get('usb_parent', '')).upper() != current['usb_parent'].upper()):
            raise ValueError('El recibo de expulsión no pertenece a la misma PSP.')
        if receipt and receipt.get('host_eject_accepted') is True:
            validate_media_release_receipt(receipt, require_absent=receipt.get('storage_released') is True)
        if value['stage'] in ('media_released', 'sending', 'waiting_removal', 'complete'):
            validate_media_release_receipt(receipt, require_absent=True)

    @property
    def pending(self):
        return self.record is not None

    def _save(self, **changes):
        if self.path is None:
            raise NativeDockError('El cierre necesita su registro persistente antes de liberar la tarjeta.')
        candidate = {**self.record, **deepcopy(changes)}
        self._validate_record(candidate)
        self.persist(self.path, candidate)
        self.record = candidate

    def _checkpoint(self, receipt):
        # Keep actual release progress in RAM even if its disk checkpoint
        # fails. No command can be sent until _save succeeds on a retry.
        self.record['release'] = deepcopy(receipt)
        self._save()

    def prepare(self, current, storage, *, cancel=False):
        if self.record is not None:
            return
        if self.path is None:
            raise NativeDockError('El cierre necesita su registro persistente antes de liberar la tarjeta.')
        sequence = current['sequence'] + 1
        report = encode_command(current['session_id'], sequence, SAFE, HOST_SHUTDOWN,
                                firmware_revision=current['firmware_revision'], build_id=current['build_id'])
        record = {'schema':SCHEMA, 'stage':'release_pending', 'observation':deepcopy(current),
                  'storage':asdict(storage), 'sequence':sequence, 'command_hex':report.hex(),
                  'attempts':0, 'release':None, 'cancel':bool(cancel)}
        self._validate_record(record)
        self.persist(self.path, record)  # Before releasing storage or touching USB.
        self.record = record

    def _identity(self, status, parent_of):
        expected = self.record['observation']
        if (_validate_status_identity(status) != (
                expected['protocol_version'], expected['firmware_revision'],
                expected['capabilities'], expected['build_id']) or
                status.session != expected['session_id'] or
                parent_of(hid_instance(expected['path']))[1] != expected['usb_parent']):
            raise NativeDockError('La PSP cambió durante el cierre; no se envía una orden a la nueva sesión.')

    def _removed(self):
        current = self.record['observation']
        return self.removed(current['usb_parent'], current['path'])

    def _release_proven(self):
        if self.record is None:
            return False
        try:
            validate_media_release_receipt(self.record.get('release'), require_absent=True)
        except (RuntimeError, ValueError, TypeError):
            return False
        return True

    def _admit(self, status):
        sequence = self.record['sequence']
        already = (self.record['attempts'] > 0 and status.sequence == sequence and
                   (status.pending or status.shutdown_requested))
        if not already and (status.sequence != sequence-1 or status.pending or status.shutdown_requested or
                status.phase not in (STORAGE, SAFE, ERROR) or
                (status.host_confirmed and status.phase == STORAGE) or
                (not status.host_confirmed and status.phase == SAFE)):
            raise NativeDockError('La orden pendiente no pertenece al cierre registrado.')
        return already

    def _renew_failed_terminal(self, status):
        # An explicit retry may advance a terminal command that firmware
        # conclusively consumed and rejected (for example, input drain
        # failed). This is different from uncertain delivery. The medium
        # remains released; no file work or eject is repeated.
        if (self._release_proven() and self.record['attempts'] > 0 and
                status.sequence == self.record['sequence'] and status.result < 0 and
                status.phase == ERROR and not status.pending and not status.shutdown_requested):
            sequence = status.sequence+1
            current = self.record['observation']
            report = encode_command(current['session_id'], sequence, SAFE, HOST_SHUTDOWN,
                firmware_revision=current['firmware_revision'], build_id=current['build_id'])
            self._save(stage='media_released', sequence=sequence, command_hex=report.hex(), attempts=0,
                       last_terminal_failure={'sequence':status.sequence, 'result':status.result})

    @property
    def current(self):
        if self.record is None:
            return None
        return {**self.record['observation'], 'storage_mounted':False, 'storage_ready':False,
                'controller_ready':False, 'controller_available':False, 'safe_ready':False,
                'shutdown_pending':True, 'shutdown_cancel':self.record.get('cancel', False),
                'exit_requested':True}

    def retire_completed(self):
        if self.record is not None and self.record['stage'] == 'complete':
            self.record = None

    def observation(self, *, probe, parent_of):
        """Identity-only observation: it grants no FAT or controller access."""
        if self.record is None:
            return None
        current = self.record['observation']
        # A completed journal is retained for a crash between transport and
        # workflow checkpoints. A different, newly verified session may start.
        if self.record['stage'] == 'complete':
            device = probe()
            if device is not None and device['status']['session'] != current['session_id']:
                self.record = None
                return None
        elif not self._removed():
            api = self.api_factory()
            with api.open(current['path']) as handle:
                self._identity(api.get(handle), parent_of)
        return self.current

    def _complete(self):
        self._save(stage='complete')
        return {'verified':True, 'host_eject_accepted':True, 'storage_released':True,
                'device_removed':True, 'eject_kind':'media',
                'session':self.record['observation']['session_id'],
                'volume_serial':self.record['observation']['volume_serial']}

    def finish(self, *, parent_of):
        try:
            return self._finish(parent_of=parent_of)
        except (OSError, RuntimeError, ValueError):
            # Removal can win a race with opening/checking the retained HID.
            # It resolves only an already proven storage release.
            if self._release_proven() and self._removed():
                return self._complete()
            raise

    def _finish(self, *, parent_of):
        if self.record is None:
            raise NativeDockError('No hay una salida registrada para esta sesión.')
        current = self.record['observation']
        receipt = self.record.get('release') or {}
        if self._release_proven() and self._removed():
            return self._complete()
        api = self.api_factory()
        transport_diagnostic = {}
        with api.open(current['path'], write=True) as handle:
            before = api.get(handle)
            self._identity(before, parent_of)
            if not before.connected or not before.sony_storage:
                raise NativeDockError('La conexión cambió antes de terminar el cierre.')
            self._renew_failed_terminal(before)
            # Check the retained sequence before any volume mutation, then
            # recheck after release before sending the terminal command.
            self._admit(before)
            if not receipt.get('host_eject_accepted'):
                # An interrupted EJECT is not a negative result. Repeating
                # that uncertain operation would erase useful evidence.
                if receipt.get('status') in ('volume_dismounted', 'eject_requested'):
                    raise NativeDockError('La expulsión quedó sin confirmar. Se conserva la sesión; no se repite automáticamente.')
                storage = PspStorage(**self.record['storage'])
                parent = parent_of(hid_instance(current['path']))
                try:
                    receipt = self.release(storage, parent, checkpoint=self._checkpoint)
                except StorageReleaseError as exc:
                    self.record['release'] = deepcopy(exc.receipt)
                    try:
                        self._save(stage='release_blocked')
                    except OSError:
                        pass
                    raise NativeDockError(str(exc)) from exc
                self.record['release'] = deepcopy(receipt)
            check = self.verify_release(self.record['release'])
            if check.get('media_absent') is not True:
                raise NativeDockError('Windows no confirma que la tarjeta siga expulsada; no se corta USB.')
            receipt = deepcopy(self.record['release'])
            receipt.update(storage_released=True, release_check=check)
            self._save(stage='media_released', release=receipt)
            before = api.get(handle)
            self._identity(before, parent_of)
            sequence = self.record['sequence']
            already = self._admit(before)
            if not already:
                self._save(stage='sending', attempts=self.record['attempts']+1)
                try:
                    api.set(handle, bytes.fromhex(self.record['command_hex']))
                except (OSError, RuntimeError, ValueError) as exc:
                    # The PSP can deactivate between SET and its Win32
                    # return. Only actual removal below resolves delivery.
                    self._save(stage='waiting_removal', delivery_error=str(exc))
                else:
                    self._save(stage='waiting_removal')
            deadline = self.clock()+4.0
            while self.clock() < deadline:
                if self._removed():
                    return self._complete()
                try:
                    status = api.get(handle)
                except (OSError, RuntimeError, ValueError):
                    status = None  # A failed GET alone never proves removal.
                if status is not None:
                    self._identity(status, parent_of)
                    if status.sequence > sequence or (status.sequence == sequence and status.result < 0):
                        raise NativeDockError('La PSP rechazó el cierre; la tarjeta permanece expulsada.')
                self.pause(0.025)
            if hasattr(api, 'transport_diagnostic'):
                try:
                    transport_diagnostic = api.transport_diagnostic(handle, before)
                except (OSError, RuntimeError, ValueError) as exc:
                    transport_diagnostic = {'available':False, 'error':str(exc)}
                if transport_diagnostic:
                    self._save(transport_diagnostic=transport_diagnostic)
        detail = 'Windows ya liberó la tarjeta, pero la PSP no confirmó el cierre de USB. Reintentar comprobará sólo el cierre.'
        if transport_diagnostic.get('available'):
            detail += (f" USB: recibidos {transport_diagnostic['received_bytes']}, "
                f"resultado {transport_diagnostic['completion_result']}, descarte {transport_diagnostic['drop_reason']}.")
        raise NativeDockError(detail,
                              message='La tarjeta está liberada; falta cerrar la conexión de la PSP.',
                              hint='Pulsa Reintentar para terminar el cierre. Se conservan los datos de la sesión.',
                              details={'transport':transport_diagnostic})
