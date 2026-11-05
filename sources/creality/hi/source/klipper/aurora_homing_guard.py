# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Breno
"""Creality Hi F018 homing checks. No pin reversal, fault clearing or G-code move.

The stock coordinate convention homes towards the minimum on XYZ. In physical
terms: carriage left, bed backwards, bed upwards. Positive Z coordinates move
the bed down; do not turn the physical description into a positive-dir setting.
Only XY uses the native zero-distance direction handshake. Z retains the stock
pressure-probe sequence, without an extra probe/trsync event.
"""
import logging
import math

VERSION = 1
PINS = {'stepper_x': '!PB9', 'stepper_y': 'PB7',
        'stepper_z': 'PA5', 'stepper_z1': 'PB5'}
HOOK = ('        from . import aurora_homing_guard\n'
        '        aurora_homing_guard.before_home(\n'
        '            self, rails, startpos, homepos, homing_axes, endstops, hi, HomingMove)\n')
MACRO_GUARD = ('  {% if printer.motor_control.motor_ready is not defined or not printer.motor_control.motor_ready %}\n'
               '    {action_raise_error("Referenciamento bloqueado: inicializacao dos motores nao confirmada")}\n'
               '  {% endif %}\n')


def require_ready(printer):
    motor = printer.lookup_object('motor_control', None)
    if motor is None or motor.get_status(printer.get_reactor().monotonic()).get('motor_ready') is not True:
        raise printer.command_error('Homing XYZ blocked: motor initialization unconfirmed')


def require_configuration(printer):
    require_ready(printer)
    settings = printer.lookup_object('configfile').get_status(
        printer.get_reactor().monotonic())['settings']
    for section, expected in PINS.items():
        pin = settings.get(section, {}).get('dir_pin', '')
        if not isinstance(pin, str) or pin.strip().upper() != expected:
            raise printer.command_error('Homing XYZ blocked: incompatible direction pin for ' + section)
    rails = printer.lookup_object('toolhead').get_kinematics().rails
    names = set()
    for rail in rails:
        name = rail.get_name()
        if name not in ('stepper_x', 'stepper_y', 'stepper_z'):
            raise printer.command_error('Homing XYZ blocked: incompatible kinematics')
        names.add(name)
        hi = rail.get_homing_info()
        if hi.positive_dir is not False or not rail.get_endstops():
            raise printer.command_error('Homing XYZ blocked: invalid endstop direction for ' + name)
    if names != {'stepper_x', 'stepper_y', 'stepper_z'}:
        raise printer.command_error('Homing XYZ blocked: missing axis')


def before_home(state, rails, startpos, homepos, homing_axes, endstops, hi, move_class):
    printer = state.printer
    require_configuration(printer)
    axes = {'stepper_x': 0, 'stepper_y': 1, 'stepper_z': 2}
    if not rails or not endstops or not math.isfinite(hi.speed) or hi.speed <= 0:
        raise printer.command_error('Homing XYZ blocked: invalid homing parameters')
    selected = set()
    for rail in rails:
        axis = axes.get(rail.get_name())
        if axis is None or rail.get_homing_info().positive_dir is not False:
            raise printer.command_error('Homing XYZ blocked: incompatible homing rail')
        selected.add(axis)
        if (not math.isfinite(startpos[axis]) or not math.isfinite(homepos[axis])
                or homepos[axis] >= startpos[axis]):
            raise printer.command_error('Homing XYZ blocked: target goes away from endstop')
    if len(startpos)!=4 or len(homepos)!=4 or selected != set(homing_axes) or any(
            not math.isfinite(s) or not math.isfinite(h) or (i not in selected and s != h)
            for i, (s, h) in enumerate(zip(startpos, homepos))):
        raise printer.command_error('Homing XYZ blocked: unexpected axis displacement')
    if selected == {2}:
        # Do not trigger extra probe hooks or alter native Z pressure semantics.
        return
    if 2 in selected:
        raise printer.command_error('Homing XYZ blocked: mixed XY/Z rails')
    sync = move_class(printer, endstops)
    try:
        sync.homing_move(list(startpos), hi.speed, check_triggered=False)
        if not sync.stepper_positions or any(sp.halt_pos != sp.start_pos for sp in sync.stepper_positions):
            raise printer.command_error('Homing XYZ blocked: steps during direction synchronization')
        state.toolhead.set_position(startpos, homing_axes=homing_axes)
    except Exception as exc:
        logging.exception('XY direction synchronization failed; homing blocked')
        # Keep the native G28 command-error cleanup (including motor-off).
        raise printer.command_error('Homing XYZ blocked: direction synchronization failed') from exc
    logging.info('Aurora homing guard v%s: XY direction synchronized, zero displacement', VERSION)


def load_config(config):
    printer = config.get_printer()
    printer.lookup_object('gcode').register_command(
        'DPS_HOMING_GUARD', lambda gcmd: require_configuration(printer))
    return HomingGuard(printer)


class HomingGuard:
    def __init__(self, printer):
        self.printer = printer

    def get_status(self, eventtime):
        try:
            require_configuration(self.printer)
            ready, reason = True, ''
        except Exception as exc:
            ready, reason = False, str(exc)
        return {'version': VERSION, 'axes': 'xyz', 'xy_zero_distance_sync': True,
                'configured': ready, 'reason': reason}


def installation_recipe():
    """Signed insertions, never a redistributed copy of the OEM homing/config."""
    return {'version': VERSION, 'module': 'aurora_homing_guard',
            'engine': '/usr/share/klipper/klippy/extras/homing.py',
            'engineOriginalSha256': '95795f239dfe2c059068b84a7e00828b27f8a8d06e147a330aa38a4b63a920c6',
            'historicalEngineSha256': '7e1c6b6a78eefe02f51993704450b779679c4fc9e37acaf5218c0a99133e5d81',
            'historicalBegin': '        # Aurora safety: motor bootstrap may reset physical DIR',
            'historicalEnd': "            logging.info('XY direction synchronization complete (zero displacement)')\n",
            'engineAnchor': '        hi = rails[0].get_homing_info()\n', 'engineHook': HOOK,
            'entryAnchor': '    def cmd_G28(self, gcmd):\n',
            'entryHook': ('        from . import aurora_homing_guard\n'
                          '        aurora_homing_guard.require_configuration(self.printer)\n'),
            'config': '/mnt/UDISK/printer_data/config/sensorless.cfg',
            'macroGuard': MACRO_GUARD, 'macroCommand': '  DPS_HOMING_GUARD\n'}
