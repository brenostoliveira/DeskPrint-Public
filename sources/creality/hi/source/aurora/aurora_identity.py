"""Independent local-name endpoint for the installed Moonraker component API.

One current record, compare-and-set revisions, no rename history or cloud IO.
"""
import asyncio
import fcntl
import json
import os
import tempfile
import unicodedata
from pathlib import Path


def valid_name(value):
    return (isinstance(value, str) and value == value.strip() and 1 <= len(value) <= 48
            and len(value.encode('utf8')) <= 192
            and not any(unicodedata.category(c).startswith('C') for c in value))


class AuroraIdentity:
    def __init__(self, config):
        self.server = config.get_server()
        self.device_id = config.get('device_id')
        self.path = Path(config.get('state_path'))
        self.factory_id = config.get('factory_id')
        self.server.register_endpoint('/server/aurora/identity', ['GET', 'POST'], self.handle)
        self.current = self.transaction(None)
        self.server.register_endpoint('/server/aurora/printer-info', ['GET'], self.printer_info)

    async def printer_info(self, web_request):
        info = await self.server.lookup_component('klippy_apis').get_klippy_info()
        return dict(info, aurora_identity=dict(self.current))

    def transaction(self, request):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = json.loads(self.path.read_text())
            current['factory_id'] = self.factory_id
            if (current.get('schema') != 1 or current.get('device_id') != self.device_id
                    or not valid_name(current.get('name')) or type(current.get('revision')) is not int):
                raise self.server.error('Registro de nome inválido.', 503)
            if request is None:
                return current
            if request['device_id'] != self.device_id:
                raise self.server.error('Identidade da impressora mudou.', 409)
            if request['operation_id'] == current.get('operation_id'):
                return current
            if request['base_revision'] != current['revision']:
                raise self.server.error('O nome foi alterado. Atualize antes de salvar novamente.', 409)
            record = {'schema': 1, 'device_id': self.device_id, 'name': request['name'],
                      'revision': current['revision'] + 1, 'operation_id': request['operation_id'],
                      'factory_id': self.factory_id}
            pending = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=str(self.path.parent),
                                                 prefix='.identity-', delete=False) as output:
                    pending = output.name
                    json.dump(record, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, str(self.path))
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if pending and os.path.exists(pending):
                    os.unlink(pending)
            return record

    async def handle(self, web_request):
        request = None
        if web_request.get_action() == 'POST':
            if str(web_request.get_ip_address()) not in ('127.0.0.1', '::1'):
                raise self.server.error('Renomeie no painel da impressora.', 403)
            request = {k: web_request.get(k) for k in ('name', 'device_id', 'base_revision', 'operation_id')}
            operation = request['operation_id']
            if (not valid_name(request['name']) or type(request['base_revision']) is not int
                    or request['base_revision'] < 0 or not isinstance(operation, str)
                    or len(operation) != 32 or any(c not in '0123456789abcdef' for c in operation)):
                raise self.server.error('Nome ou revisão inválidos.', 400)
        result = await asyncio.get_running_loop().run_in_executor(None, self.transaction, request)
        self.current = result
        return result


def load_component(config):
    return AuroraIdentity(config)
# SPDX-License-Identifier: GPL-3.0-or-later
# See ../klipper/COPYING. The Moonraker component remains available as source.
