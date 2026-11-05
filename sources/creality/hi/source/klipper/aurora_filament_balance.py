# SPDX-License-Identifier: GPL-3.0-or-later
"""Passive extrusion accounting. Never generates motion, heater or stop commands.

Values are estimates from completed planner motion, not a scale. Transport by
the CFS upstream motor is not counted a second time. Persist compact aggregates.
"""
import copy
import json
import math
import os
import re
import threading
import uuid
from collections import deque
from decimal import Decimal, InvalidOperation, localcontext

PATH = '/mnt/UDISK/printer_data/config/aurora-filament-balance.json'


def grams(value):
    try:
        number = Decimal(str(value).replace(',', '.'))
        if not number.is_finite() or number < 0:
            raise ValueError('Informe uma quantidade válida em gramas, igual ou maior que zero.')
        return number
    except InvalidOperation:
        raise ValueError('Informe uma quantidade válida em gramas.')


def slot_name(value):
    value = str(value)
    if value != 'external' and not re.fullmatch(r'T[1-4][A-D]', value):
        raise ValueError('Posição de bobina inválida.')
    return value


class Ledger:
    def __init__(self, data=None):
        self.data = copy.deepcopy(data) if data else dict(schema=1, rows={}, alerts=[], job=None, revision=0)
        if self.data.get('schema') != 1 or not isinstance(self.data.get('rows'), dict):
            raise ValueError('Inventário de filamento inválido; preserve o arquivo para recuperação.')

    def baseline(self, slot, value, expected=None):
        slot = slot_name(slot)
        row = self.data['rows'].get(slot)
        if expected is not None and expected != (row or {}).get('spoolId', ''):
            raise ValueError('O saldo desta bobina mudou; atualize antes de aplicar.')
        value = str(grams(value))
        if row:self.data['alerts'] = [a for a in self.data['alerts'] if a['spoolId'] != row['spoolId']]
        self.data['rows'][slot] = dict(spoolId=uuid.uuid4().hex, remainingGrams=value,
            usedGrams='0', low100=False, densitySource='estimate', slot=slot)
        self.data['revision'] += 1
        return self.data['rows'][slot]

    def begin(self, filename):
        job = self.data.get('job')
        if job and not job.get('ended') and job.get('filename') == filename:
            return
        self.data['job'] = dict(id=uuid.uuid4().hex, filename=filename, slots=[], ended=False)
        self.data['revision'] += 1

    def alert(self, row, kind):
        self.data['alerts'].append(dict(id=uuid.uuid4().hex, spoolId=row['spoolId'],
            slot=row['slot'], kind=kind, remainingGrams=row['remainingGrams']))
        self.data['alerts'] = self.data['alerts'][-64:]

    def consume(self, slot, length, density=1.25, diameter=1.75, density_source='estimate'):
        if not math.isfinite(length) or length <= 0:
            return
        row = self.data['rows'].get(slot)
        if not row:
            return
        amount = Decimal(str(length * math.pi * (diameter / 2.) ** 2 * density / 1000.))
        # Preserve arbitrary user quantities; no spool-capacity upper bound.
        with localcontext() as context:
            context.prec = max(40, len(row['remainingGrams']) + 24, len(row['usedGrams']) + 24,
                Decimal(row['remainingGrams']).adjusted()+40)
            row['remainingGrams'] = str(Decimal(row['remainingGrams']) - amount)
            row['usedGrams'] = str(Decimal(row['usedGrams']) + amount)
        row['densitySource'] = density_source
        job = self.data.get('job')
        if job and not job['ended'] and slot not in job['slots']:
            job['slots'].append(slot)
        if Decimal(row['remainingGrams']) <= 100 and not row['low100']:
            row['low100'] = True
            self.alert(row, '100g')
        self.data['revision'] += 1

    def end(self, state):
        job = self.data.get('job')
        if not job or job['ended']:
            return
        job['ended'] = True
        if state in ('complete', 'cancelled'):
            for slot in job['slots']:
                row = self.data['rows'].get(slot)
                if row and Decimal(row['remainingGrams']) <= 50:
                    self.alert(row, '50g')
        self.data['revision'] += 1

    def acknowledge(self, identity):
        self.data['alerts'] = [a for a in self.data['alerts'] if a['id'] != identity]
        self.data['revision'] += 1


class Balance:
    def __init__(self, observer, path=PATH):
        self.observer = observer
        self.printer = observer.printer
        self.reactor = self.printer.get_reactor()
        self.path = path
        self.error = None
        self.pending = deque()
        self.debt = {}
        self.active = False
        self.unassigned_mm = 0.
        self.dirty_at = 0.
        self.checkpoint_revision = -1
        self.lock = threading.Lock()
        self.write_event = threading.Event()
        self.write_value = None
        try:
            with open(path, encoding='utf8') as stream:
                self.ledger = Ledger(json.load(stream))
        except FileNotFoundError:
            self.ledger = Ledger()
        except Exception:
            # Never replace a corrupt balance with an apparently valid zero.
            self.ledger = Ledger()
            self.error = 'Não foi possível ler os saldos de filamento. Preserve o cadastro antes de redefinir.'
        threading.Thread(target=self.writer, name='aurora-filament-checkpoint', daemon=True).start()

    def checkpoint(self):
        if self.error or self.checkpoint_revision == self.ledger.data['revision']:
            return
        with self.lock:
            self.write_value = copy.deepcopy(self.ledger.data)
        self.checkpoint_revision = self.ledger.data['revision']
        self.write_event.set()

    def writer(self):
        while True:
            self.write_event.wait()
            self.write_event.clear()
            with self.lock:
                value, self.write_value = self.write_value, None
            if value is None:
                continue
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                temporary = self.path + '.tmp'
                with open(temporary, 'w', encoding='utf8') as stream:
                    json.dump(value, stream, separators=(',', ':'))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except Exception:
                self.error = 'Não foi possível guardar o saldo de filamento.'

    def printing(self, now):
        stats = self.printer.lookup_object('print_stats').get_status(now)
        observer = self.observer
        active = bool(stats.get('state') in ('printing', 'paused') or observer.native_preparation or 'START_PRINT' in observer.stack)
        if active and not self.active:
            self.ledger.begin(stats.get('filename', ''))
        self.active = active
        return stats

    def install(self):
        extruder = self.printer.lookup_object('extruder')
        original = extruder.move
        def observe(print_time, move):
            result = original(print_time, move)  # exactly once, unchanged
            try:
                self.capture(print_time, move)
            except Exception:
                self.error = 'Contagem de filamento incompleta; confira o saldo da bobina.'
            return result
        extruder.move = observe
        self.printer.lookup_object('gcode').register_command('AURORA_FILAMENT_BALANCE', self.set_balance)
        self.printer.lookup_object('gcode').register_command('AURORA_FILAMENT_ALERT_ACK', self.acknowledge)
        self.printer.register_event_handler('klippy:shutdown', self.shutdown)
        self.reactor.register_timer(self.tick, self.reactor.monotonic() + .5)

    def shutdown(self, *unused):
        try:self.advance(self.print_clock(self.reactor.monotonic()))
        except Exception:pass
        self.pending.clear()
        self.ledger.end('error')
        self.checkpoint()

    def capture(self, at, move):
        now = self.reactor.monotonic()
        if not self.active:self.printing(now)
        if not self.active or self.error:
            return
        slot = self.observer.filament_status(now).get('filamentSlot')
        if len(self.pending) >= 16384:
            self.error = 'Fila de contagem excedida; confira o saldo de filamento.'
            return
        duration = move.accel_t + move.cruise_t + move.decel_t
        unloading = ('filament-unloading' in self.observer.active_phases or self.observer.native_phase == 'filament-unloading'
            or any(n in ('BOX_RETRUDE_MATERIAL','BOX_RETRUDE_MATERIAL_WITH_TNN','BOX_QUIT_MATERIAL','QUIT_MATERIAL') for n in self.observer.stack))
        density, diameter, source = self.material(slot)
        self.pending.append(dict(start=at, duration=duration, e=move.axes_d[3], applied=0.,
            slot=slot, unloading=unloading, density=density, diameter=diameter, source=source,
            accel=move.accel, start_v=move.start_v, cruise_v=move.cruise_v,
            accel_t=move.accel_t, cruise_t=move.cruise_t, distance=move.move_d))

    def material(self, slot):
        # Match metadata tool through the confirmed slot, never by colour.
        # Native Hi default is explicit when a profile density is unavailable.
        density, diameter, source = 1.25, 1.75, 'native-default-1.25g/cm3-1.75mm'
        owner = self.printer.lookup_object('virtual_sdcard', None)
        envelope = (getattr(owner, 'gcode_metadata', None) or {}) if owner else {}
        metadata = envelope.get('metadata', {}) or {}
        # Only use scalar values; arrays require a validated tool-to-slot map.
        for key, default in (('filament_density', density), ('filament_diameter', diameter)):
            value = metadata.get(key)
            if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
                if key == 'filament_density':density, source = value, 'slicer-density'
                else:diameter = value
        return density, diameter, source

    @staticmethod
    def fraction(segment, now):
        t = max(0., now - segment['start'])
        if t >= segment['duration']:
            return 1.
        a, c = segment['accel_t'], segment['cruise_t']
        v, top, acceleration = segment['start_v'], segment['cruise_v'], segment['accel']
        if t <= a:distance = v*t + .5*acceleration*t*t
        elif t <= a+c:distance = (v+top)*.5*a + top*(t-a)
        else:
            d = t-a-c
            distance = (v+top)*.5*a + top*c + top*d - .5*acceleration*d*d
        return min(1., max(0., distance/segment['distance'])) if segment['distance'] else 0.

    def advance(self, now):
        while self.pending:
            segment = self.pending[0]
            fraction = self.fraction(segment, now)
            delta = segment['e'] * (fraction-segment['applied'])
            segment['applied'] = fraction
            slot = segment['slot']
            if segment['unloading']:
                self.debt[slot] = 0.
            elif delta < 0:
                self.debt[slot] = self.debt.get(slot, 0.)-delta
            if delta > 0:
                retract = min(delta, self.debt.get(slot, 0.))
                self.debt[slot] = self.debt.get(slot, 0.)-retract
                if slot is None:self.unassigned_mm += delta-retract
                else:self.ledger.consume(slot, delta-retract, segment['density'], segment['diameter'], segment['source'])
            if fraction < 1.:
                break
            self.pending.popleft()

    def print_clock(self, now):
        return self.printer.lookup_object('mcu').estimated_print_time(now)

    def tick(self, now):
        try:
            self.advance(self.print_clock(now))
            stats = self.printing(now)
            # Finalize only on native terminal state, never handler return.
            if not self.active and stats.get('state') in ('complete', 'cancelled', 'error') and (stats['state']!='complete' or not self.pending):
                self.pending.clear()
                self.debt.clear()
                self.ledger.end(stats['state'])
            if now-self.dirty_at >= 5:
                self.checkpoint()
                self.dirty_at = now
        except Exception:
            self.error = 'Contagem de filamento indisponível; confira os saldos.'
        return now + .5

    def set_balance(self, gcmd):
        if self.error:
            raise gcmd.error(self.error)
        self.advance(self.print_clock(self.reactor.monotonic()))
        try:
            expected=gcmd.get('EXPECTED',None)
            self.ledger.baseline(gcmd.get('SLOT'), gcmd.get('GRAMS'), '' if expected=='none' else expected)
        except ValueError as error:
            raise gcmd.error(str(error))
        self.checkpoint()

    def acknowledge(self, gcmd):
        self.ledger.acknowledge(gcmd.get('ID'))
        self.checkpoint()

    def status(self):
        return dict(revision=self.ledger.data['revision'], rows=copy.deepcopy(self.ledger.data['rows']),
            alerts=copy.deepcopy(self.ledger.data['alerts']), error=self.error,
            unassignedLengthMm=self.unassigned_mm,
            warning='Houve extrusão sem bobina identificada; confira os saldos.' if self.unassigned_mm else None,
            method='completed-planner-extrusion', checkpointSeconds=5)
