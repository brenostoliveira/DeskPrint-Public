"""Klipper endpoint restricted to the already configured Creality Hi logo."""
import math
import time


class LogoSoundClock:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.pin = None
        self.history = []
        self.printer.register_event_handler('klippy:ready', self.ready)
        hooks = self.printer.lookup_object('webhooks')
        hooks.register_endpoint('logo_sound/status', self.status)
        hooks.register_endpoint('logo_sound/set', self.set)

    def ready(self):
        config = self.printer.lookup_object('configfile').get_status(self.reactor.monotonic())['config']
        section = config.get('output_pin LED', {})
        if section.get('pin') != 'nozzle_mcu:PB12' or str(section.get('pwm', 'false')).lower() not in ('false', '0'):
            raise self.printer.config_error('Logo clock requires the verified digital nozzle_mcu:PB12 LED')
        pin = self.printer.lookup_object('output_pin LED')
        if pin.is_pwm or pin.resend_interval:
            raise self.printer.config_error('Logo clock requires a digital LED without a resend timer')
        self.pin = pin

    def require_ready(self, request):
        if self.pin is None:
            raise request.error('Logo clock is not ready')

    def status(self, request):
        self.require_ready(request)
        request.send({'version': 1, 'pin': 'nozzle_mcu:PB12', 'value': int(self.pin.last_value),
                      'minimum_lead': .030, 'minimum_interval': .100, 'history': list(self.history)})

    def set(self, request):
        self.require_ready(request)
        value = request.get_int('value')
        if value not in (0, 1):
            raise request.error('Logo value must be zero or one')
        now = time.monotonic()
        reactor_now = self.reactor.monotonic()
        requested = request.get_float('at', now + .030)
        if not math.isfinite(requested) or not now - .100 <= requested <= now + .500:
            raise request.error('Logo deadline outside the bounded host clock window')
        mcu = self.pin.mcu_pin.get_mcu()
        target = max(now + .030, requested)
        # The nozzle MCU's own clock is independent of the motion lookahead.
        print_time = mcu.estimated_print_time(reactor_now + target - now)
        scheduled = max(print_time, self.pin.last_print_time + .100)
        # Refuse a conflicting pre-existing future SET_PIN instead of growing
        # the queue or modifying its clock/order.
        if scheduled > mcu.estimated_print_time(reactor_now + .500):
            raise request.error('Logo has a conflicting future command')
        self.pin._set_pin(scheduled, value, 0.)
        actual_at = target + scheduled - print_time
        record = {'received_at': now, 'requested_at': requested, 'scheduled_at': actual_at, 'value': value}
        self.history.append(record)
        self.history = self.history[-32:]
        request.send(record)


def load_config(config):
    return LogoSoundClock(config)
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYING. This printer-integrated bridge remains available as source.
