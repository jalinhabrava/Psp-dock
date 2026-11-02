"""Bind the product workflow to Dock's real, verified file operations.

Only the native 0301 transport activates this adapter. The existing Sony
manual workflow and the 0207 synthetic diagnostic retain their own gates.
"""
from __future__ import annotations

from functools import partial
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

from dock_product_flow import FlowCallbacks, ProductFlow, ProductFlowError, plan_session_return
from native_dock import NativeDockTransport
from retro_runtime import private_save_paths
from retro_saves import RAW_PROFILES, _baseline_key, copy_sram_verified


class DockProductSession:
    def __init__(self, dock, transport=None):
        self.dock = dock
        self.api = sys.modules[type(dock).__module__]
        self.transport = transport or NativeDockTransport()
        self.observation = None
        self._callback_state = threading.local()
        self.entry_path = dock.data / 'product-entry.json'
        self.entry = self.api.read_json(self.entry_path, {})
        self.flow = ProductFlow(
            dock.data / 'product-session.json',
            FlowCallbacks(*(partial(self._invoke_callback, action) for action in (
                self.inventory, self.sync_initial, self.copy_one,
                self.sync_return, self.request_role, self.finalize_exit))),
            enabled=False, capabilities=self.transport.capabilities)
        # A crashed native session remains visible and blocked even before
        # its matching PSP is connected again. An old completed one does not.
        self.flow.enabled = self.flow.snapshot()['phase'] != 'waiting_psp'

    def snapshot(self):
        result = self.flow.snapshot()
        if self.dock.recovery_required or self.dock.session.get('running'):
            result['can_choose'] = False
            result['can_retry'] = False
            result['can_review_saves'] = False
        result['progress'] = self.dock.transfer_progress()
        result['unsupported_state_formats'] = [
            'Los estados de emuladores sin formato compatible verificado se conservan en el PC.'
        ] if self.dock.last_emulators else []
        window_close = getattr(self.dock, 'window_close', None)
        return window_close.decorate(result) if window_close is not None else result

    def root(self):
        current = self.observation
        return Path(current['root']) if current and current.get('storage_ready') else None

    def tick(self, devices):
        if self.dock.demo or self.dock.stop.is_set():
            return False
        try:
            self.observation = self.transport.observe(devices)
        except (OSError, ValueError):
            self.observation = None
            if self.flow.enabled:
                self.flow.observe(None)
            raise
        if self.dock.stop.is_set():
            return False
        if self.observation is not None:
            self.flow.enabled = True
        if not self.flow.enabled:
            return False
        state = self.flow.snapshot()
        # Keep the verified completion visible until the app closes. A new
        # manually opened app creates a fresh waiting session.
        if state['phase'] != 'safe_to_disconnect':
            self.flow.observe(self.observation)
        if self.dock.stop.is_set():
            return False
        if (self.observation and self.observation.get('exit_requested') and
                self.flow.snapshot()['phase'] == 'ready' and not self.dock.running()):
            self.request_exit()
        if self.dock.running() or self.dock.stop.is_set():
            # A prior emulator may outlive a crashed Windows app. The
            # durable running flag blocks work until explicit recovery has
            # confirmed that process is closed, even though this Flow
            # object has no in-memory process handle after restarting.
            return False
        return self.flow.advance()

    def _invoke_callback(self, callback, context):
        # Admission is checked after the coordinator persists its intent.
        # Once admitted, the whole callback may finish its existing copy
        # and verification if a second X requests shutdown in the meantime.
        self._require_idle()
        if self.dock.stop.is_set():
            raise ProductFlowError('La app se está cerrando; no se iniciará otra operación.')
        self._callback_state.active = True
        try:
            return callback(context)
        finally:
            self._callback_state.active = False

    def _require_idle(self):
        if self.dock.stop.is_set() and not getattr(self._callback_state, 'active', False):
            raise ProductFlowError('La app se está cerrando; no se iniciará otra operación.')
        if self.dock.recovery_required:
            raise ProductFlowError('Cierra el emulador anterior y revisa la recuperación antes de continuar.')
        if self.dock.running():
            raise ProductFlowError('Cierra el emulador normalmente antes de sincronizar la PSP.')

    def assert_storage(self, root):
        device = self.transport.assert_storage(root)
        self.observation = self.transport.current
        return device

    def _context_root(self, context):
        self._require_idle()
        current = self.transport.current
        expected = context.get('identity', {})
        if (not current or current['session_id'] != expected.get('session_id') or
                current['volume_serial'] != expected.get('volume_serial')):
            raise ProductFlowError('La operación pertenece a otra sesión o Memory Stick.')
        root = Path(current['root'])
        self.assert_storage(root)
        if {key: self.observation[key] for key in ('session_id', 'volume_serial')} != expected:
            raise ProductFlowError('La PSP cambió durante la comprobación del almacenamiento.')
        expected_phase = {'initial':1, 'game_copy':3, 'exit':4}.get(context.get('purpose'))
        if expected_phase is not None and self.observation.get('phase') != expected_phase:
            raise ProductFlowError('La PSP no confirmó la fase de almacenamiento de esta operación.')
        self.dock.validate_layout(root)
        return root

    def _save_entry(self):
        self.api.atomic_json(self.entry_path, self.entry)

    def _require_entry(self, context=None):
        current = context.get('identity') if context else (
            {key: self.observation[key] for key in ('session_id', 'volume_serial')}
            if self.observation else None)
        if (not isinstance(self.entry, dict) or self.entry.get('schema') != 'pspdock-product-entry-v1'
                or not current or self.entry.get('identity', {}).get('volume_serial') != current.get('volume_serial')):
            raise ProductFlowError('Falta la comparación de entrada de esta Memory Stick.')
        # Explicit recovery may establish a fresh HID session after reopening
        # the same card. The workflow has required fresh user consent first.
        return self.entry

    def _save_snapshots(self, root):
        d, a = self.dock, self.api
        psp = a.inside(root, 'PSP/SAVEDATA')
        pc = a.inside(Path(d.config['memstick']), 'PSP/SAVEDATA')
        if list(psp.glob('.dock-journal-*.json')) or list(pc.glob('.dock-journal-*.json')):
            raise ProductFlowError('Hay una copia de partidas interrumpida. Revisa el journal antes de continuar.')
        d.retro_save_plan()
        retro_psp, retro_pc, paths = {}, {}, {}
        for row in d.last_retro_save_rows:
            rom = next((item for item in d.last_roms if item['id'] == row['rom_id']), None)
            emulator = next((item for item in d.last_emulators
                             if rom and item['id'] == rom['emulator_id']), None)
            if (not rom or not emulator or (emulator['name'], rom['system']) not in RAW_PROFILES or
                    row['status'] in ('unsupported', 'blocked') or not isinstance(row.get('source_relative'), str)):
                continue
            retro_psp[row['rom_id']] = row.get('psp_signature')
            retro_pc[row['rom_id']] = row.get('pc_signature')
            paths[row['rom_id']] = {'relative': row['source_relative'], 'system': rom['system'],
                                    'suggested_path': row.get('suggested_path') is True}
        return {'psp': a.slots(psp), 'pc': a.slots(pc),
                'retro_psp': retro_psp, 'retro_pc': retro_pc, 'retro_paths': paths}

    def _report(self, context, *, capture=False):
        root = self._context_root(context)
        d = self.dock
        games, retro = d.games(), d.retro_catalog()
        saves, retro_saves = d.save_plan(), d.retro_save_plan()
        snapshots = self._save_snapshots(root)
        missing = [g['id'] for g in games if g['availability'] == 'missing']
        missing += [g['id'] for g in retro['roms'] if g['availability'] == 'missing']
        counts = {
            'psp_games': sum(g.get('on_psp', True) for g in games) + len(retro['roms']),
            'pc_games': sum(g.get('on_pc', g['availability'] == 'ready') for g in games) +
                        sum(g['availability'] == 'ready' for g in retro['roms']),
            'missing_games': len(missing),
            'save_actions': len(saves['actions']) + sum(r['status'] in ('psp_to_pc', 'pc_to_psp')
                                                       for r in retro_saves['rows']),
            'save_conflicts': len(saves['conflicts']) + retro_saves['conflicts'],
        }
        material = {'identity': context['identity'], 'games': games, 'roms': retro['roms'],
                    'snapshots': snapshots}
        fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        if capture:
            if d.session.get('pending_return'):
                raise ProductFlowError('Hay partidas de una sesión anterior pendientes de devolver. Revísalas primero.')
            # A preparation flag from an earlier dock session is not proof
            # that today's selected saves were synchronized. Only an actual
            # successful saves/all operation may establish that flag again.
            d.session.pop('prepared_volume', None)
            d.session.pop('product_volume', None)
            d.session.pop('product_choice', None)
            self.api.atomic_json(d.data/'session.json', d.session)
            self.entry = {'schema': 'pspdock-product-entry-v1',
                          'identity': dict(context['identity']), **snapshots,
                          'skipped_saves': [], 'skipped_retro': [],
                          'return_data_verified': False, 'choice': None}
            self._save_entry()
        return {'fingerprint': fingerprint, 'counts': counts, 'missing_game_ids': missing}

    def inventory(self, context):
        return self._report(context, capture=True)

    def choose(self, choice, offer_id):
        self._require_idle()
        self.flow.choose(choice, offer_id)
        try:
            self._require_entry()
            self.entry['choice'] = choice
            self._save_entry()
            self.dock.session.update(product_volume=self.entry['identity']['volume_serial'],
                                     product_choice=choice)
            self.api.atomic_json(self.dock.data / 'session.json', self.dock.session)
        except (OSError, ValueError) as exc:
            self.flow.halt(str(exc))
            raise

    def sync_initial(self, context):
        root = self._context_root(context)
        fresh = self._report(context)
        if fresh['fingerprint'] != context['inventory']['fingerprint']:
            raise ProductFlowError('El contenido cambió desde la elección. Revisa el inventario de nuevo.')
        d = self.dock
        choice = context['choice']
        if choice not in ('saves', 'games', 'all'):
            raise ProductFlowError('No hay autorización de copia para esta elección.')
        if choice in ('saves', 'all'):
            plan, retro = d.save_plan(), d.retro_save_plan()
            conflicts = list(plan['conflicts']) + [dict(row, kind='retro') for row in retro['rows']
                                                  if row['status'] == 'conflict']
            if conflicts:
                return {'verified': False, 'conflicts': conflicts,
                        'reason': 'Elige qué versión conservar de las partidas en conflicto.'}
            final = d.sync()
            if final['conflicts'] or final['actions']:
                return {'verified': False, 'conflicts': final['conflicts'],
                        'reason': 'La sincronización de partidas requiere revisión.'}
            for item in list(d.retro_save_plan()['rows']):
                if item['status'] in ('psp_to_pc', 'pc_to_psp'):
                    self._context_root(context)
                    d.sync_retro_save(item['rom_id'], 'psp' if item['status'] == 'psp_to_pc' else 'pc', item['revision'])
        if choice in ('games', 'all'):
            ids = [g['id'] for g in d.games() if g['availability'] == 'missing']
            if ids:
                d.transfer(ids)
            ids = [g['id'] for g in d.retro_catalog()['roms'] if g['availability'] == 'missing']
            for start in range(0, len(ids), 100):
                self._context_root(context)
                d.transfer_retro(ids[start:start + 100])
        self._context_root(context)
        self.entry.update(self._save_snapshots(root))
        self.entry['choice'] = choice
        self._save_entry()
        return {'verified': True, 'conflicts': [], 'inventory': self._report(context)}

    def request_role(self, context):
        self._require_idle()
        if context['role'] == 'controller':
            d = self.dock
            root = self._context_root(context)
            self._require_entry(context)
            if context['purpose'] == 'initial':
                # The outbound comparison starts when entry preparation is
                # finished. Runtime configuration lives outside SAVEDATA.
                self.entry.update(self._save_snapshots(root))
                self._save_entry()
            if any(g['availability'] == 'ready' for g in d.games()):
                try:
                    d.prepare_runtime()
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    d.runtime_error = str(exc)  # Menu/retro remain available.
        result = self.transport.request_role(context)
        self.observation = self.transport.current
        return result

    def copy_one(self, context):
        self._context_root(context)
        d = self.dock
        game = next((g for g in d.games() if g['id'] == context['game_id']), None)
        retro = False
        if game is None:
            game = next((g for g in d.retro_catalog()['roms'] if g['id'] == context['game_id']), None)
            retro = True
        if game is None or game['availability'] == 'different':
            raise ProductFlowError('El juego solicitado ya no corresponde al inventario de esta PSP.')
        if game['availability'] == 'missing':
            (d.transfer_retro if retro else d.transfer)([game['id']])
        self._context_root(context)
        catalog = d.retro_catalog()['roms'] if retro else d.games()
        if not any(g['id'] == game['id'] and g['availability'] == 'ready' for g in catalog):
            raise ProductFlowError('La copia no quedó verificada en la biblioteca del PC.')
        return {'verified': True}

    def request_copy(self, game_id):
        self.flow.request_copy(game_id)
        return self.snapshot()

    def request_exit(self):
        self._require_idle()
        self.flow.request_exit()
        try:
            d = self.dock
            d.session.update(exit_requested=True, return_sync_authorized=True,
                             return_sync_attempted=False, close_after_verified_return=True)
            self.api.atomic_json(d.data / 'session.json', d.session)
        except OSError as exc:
            self.flow.halt(str(exc))
            raise

    def retry(self, offer_id):
        self._require_idle()
        self.flow.retry(offer_id)

    def _return_plans(self, root):
        entry = self._require_entry()
        now = self._save_snapshots(root)
        native = plan_session_return(entry['psp'], entry['pc'], now['psp'], now['pc'])
        retro = plan_session_return(entry['retro_psp'], entry['retro_pc'], now['retro_psp'], now['retro_pc'])
        for plan, key in ((native, 'skipped_saves'), (retro, 'skipped_retro')):
            skipped = set(entry.get(key, []))
            plan['actions'] = [row for row in plan['actions'] if row['slot'] not in skipped]
            plan['conflicts'] = [row for row in plan['conflicts'] if row['slot'] not in skipped]
        for row in list(retro['actions']):
            slot = row['slot']
            if (slot not in entry['retro_paths'] or slot not in now['retro_paths'] or
                    entry['retro_paths'][slot] != now['retro_paths'][slot]):
                retro['actions'].remove(row)
                retro['conflicts'].append({**row, 'reason': 'La ruta o el formato SRAM cambió durante la sesión.'})
        signature = hashlib.sha256(json.dumps({'identity': entry['identity'], 'native': native,
                                               'retro': retro, 'now': now}, sort_keys=True).encode()).hexdigest()
        for plan in (native, retro):
            for row in plan['conflicts']:
                row['revision'] = signature
            for row in plan['actions']:
                row['revision'] = signature
        return native, retro, now

    def return_plan(self):
        root = self.root()
        if root is None:
            return {'actions': [], 'conflicts': [], 'unchanged': 0}
        self.assert_storage(root)
        return self._return_plans(root)[0]

    def retro_return_view(self):
        root = self.root()
        if root is None:
            return self.dock.retro_save_plan()
        self.assert_storage(root)
        _, plan, _ = self._return_plans(root)
        by_id = {row['slot']: {**row, 'status': 'pc_to_psp'} for row in plan['actions']}
        by_id.update({row['slot']: {**row, 'status': 'conflict'} for row in plan['conflicts']})
        rows = []
        for row in self.dock.retro_save_plan()['rows']:
            override = by_id.get(row['rom_id'])
            if override:
                row = {**row, 'status': override['status'], 'revision': override['revision'],
                       'reason': override.get('reason', ''), 'skipped': False}
            elif row['status'] not in ('unsupported', 'blocked'):
                row = {**row, 'status': 'identical' if row.get('pc_present') else 'none',
                       'skipped': row['rom_id'] in self.entry.get('skipped_retro', [])}
            rows.append(row)
        return {'rows': rows, 'offline': False, 'pending': len(by_id),
                'conflicts': len(plan['conflicts']),
                'unmanaged': sum(row['status'] in ('unsupported', 'blocked') for row in rows)}

    def _copy_retro_out(self, root, action):
        d, a = self.dock, self.api
        slot = action['slot']
        current = next((row for row in d.last_retro_save_rows if row['rom_id'] == slot), None)
        paths = self.entry['retro_paths'].get(slot)
        if (not paths or not current or current['status'] in ('blocked', 'unsupported') or
                current.get('source_relative') != paths['relative']):
            raise ProductFlowError('La ruta o el formato de esta SRAM no está verificado.')
        pc, _ = private_save_paths(Path(d.config['storage_root']), self.entry['identity']['volume_serial'], slot)
        psp = a.inside(root, paths['relative'])
        storage = Path(d.config['storage_root'])
        key = _baseline_key(self.entry['identity']['volume_serial'], slot)
        self.assert_storage(root)
        saved = copy_sram_verified(
            source=pc, target=psp, source_root=storage, target_root=root,
            storage_root=storage, backup_dir=storage/'retro-save-backups'/key[:24],
            journal_dir=storage/'retro-save-journals', expected_source=action['source_hash'],
            expected_target=action['target_hash'], system=paths['system'])
        baselines = a.read_json(d.retro_save_baselines_path, {})
        baselines[key] = saved
        a.atomic_json(d.retro_save_baselines_path, baselines)

    def sync_return(self, context):
        if context.get('direction') != 'to_psp':
            raise ProductFlowError('La salida sólo devuelve partidas hacia la PSP.')
        root = self._context_root(context)
        self._require_entry(context)
        native, retro, _ = self._return_plans(root)
        conflicts = list(native['conflicts']) + [dict(row, kind='retro') for row in retro['conflicts']]
        if conflicts:
            return {'verified': False, 'direction': 'to_psp', 'conflicts': conflicts,
                    'reason': 'Hay partidas modificadas en la PSP que necesitan una decisión.'}
        d, a = self.dock, self.api
        for action in native['actions']:
            self._context_root(context)
            source = a.inside(Path(d.config['memstick'])/'PSP/SAVEDATA', action['slot'])
            target = a.inside(root/'PSP/SAVEDATA', action['slot'])
            a.copy_slot(source, target, Path(d.config['storage_root'])/'backups',
                        action['source_hash'], action['target_hash'])
        for action in retro['actions']:
            self._context_root(context)
            self._copy_retro_out(root, action)
        self._context_root(context)
        final_native, final_retro, _ = self._return_plans(root)
        if (final_native['actions'] or final_native['conflicts'] or
                final_retro['actions'] or final_retro['conflicts']):
            raise ProductFlowError('Los datos cambiaron durante la devolución; revisa el resultado.')
        self.entry['return_data_verified'] = True
        self.entry['return_identity'] = dict(context['identity'])
        self._save_entry()  # Durable before firmware SAFE / host eject.
        d.session['return_result'] = 'data_verified'
        a.atomic_json(d.data/'session.json', d.session)
        return {'verified': True, 'direction': 'to_psp', 'conflicts': []}

    def finalize_exit(self, context):
        self._require_idle()
        entry = self._require_entry(context)
        if not entry.get('return_data_verified') or entry.get('return_identity') != context['identity']:
            raise ProductFlowError('La devolución no está verificada para esta sesión.')
        try:
            result = self.transport.finish_session()
        finally:
            # SAFE is terminal even when Windows vetoes eject. Publishing
            # the old EXIT_SYNC observation would invite another FAT scan.
            self.observation = self.transport.current
        if result.get('verified') is not True or result.get('host_eject_accepted') is not True:
            raise ProductFlowError('Windows no ha confirmado la expulsión segura de esta PSP.')
        d = self.dock
        d.session.update(pending_return=False, exit_requested=False, return_sync_authorized=False,
                         return_sync_attempted=False, return_result='verified')
        self.api.atomic_json(d.data/'session.json', d.session)
        self.observation = None
        return result

    def resolve_save(self, body):
        self._require_idle()
        root = self.root()
        if root is None:
            raise ProductFlowError('Conecta el almacenamiento de la misma PSP para revisar las partidas.')
        self.assert_storage(root)
        plan, _, _ = self._return_plans(root)
        row = next((item for item in plan['conflicts'] if item['slot'] == body.get('slot')), None)
        if row is None or body.get('revision') != row['revision'] or body.get('choice') not in ('pc', 'psp'):
            raise ProductFlowError('La comparación de la partida cambió; revisa sus versiones de nuevo.')
        if body['choice'] == 'psp':
            self.entry['skipped_saves'] = [*self.entry.get('skipped_saves', []), row['slot']]
            self._save_entry()
        else:
            a, d = self.api, self.dock
            a.copy_slot(a.inside(Path(d.config['memstick'])/'PSP/SAVEDATA', row['slot']),
                        a.inside(root/'PSP/SAVEDATA', row['slot']),
                        Path(d.config['storage_root'])/'backups', row['source_hash'], row['target_hash'])
        return self.return_plan()

    def resolve_retro(self, rom_id, choice, revision):
        self._require_idle()
        root = self.root()
        if root is None:
            raise ProductFlowError('Conecta el almacenamiento de la PSP para revisar la SRAM.')
        self.assert_storage(root)
        _, plan, _ = self._return_plans(root)
        row = next((item for item in plan['actions'] + plan['conflicts'] if item['slot'] == rom_id), None)
        if not row or row['revision'] != revision or choice not in ('pc', 'skip'):
            raise ProductFlowError('La comparación SRAM cambió o la dirección no está autorizada.')
        if choice == 'skip':
            self.entry['skipped_retro'] = [*self.entry.get('skipped_retro', []), rom_id]
            self._save_entry()
        else:
            self._copy_retro_out(root, row)
        return self.retro_return_view()
