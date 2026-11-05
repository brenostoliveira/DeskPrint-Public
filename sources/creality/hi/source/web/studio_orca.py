# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Breno
# See studio_orca.LICENSE; this grant applies to this file only.
"""Publish Aurora inventory through Orca's existing Moonraker lane_data contract.

Disabled until explicitly enabled by Desktop integration. No commands to hardware,
no new listener, no independent Aurora owner and no repeated database writes.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import urllib.error
import urllib.parse

OWNER = 'deskprinter-studio'
NAMESPACE = 'deskprinter_orca'
LANES = 'lane_data'
PROTOCOL = 1


def rgb(value):
    value = str(value or '').strip()
    if re.fullmatch(r'#0[0-9a-fA-F]{6}', value):
        return '' if value == '#0000000' else '#' + value[2:].upper()
    return value.upper() if re.fullmatch(r'#[0-9a-fA-F]{6}', value) else ''


def inventory(snapshot, printer_id):
    if not isinstance(snapshot, dict) or snapshot.get('available') is not True:
        raise ValueError('A Aurora não disponibilizou o cadastro de filamentos.')
    rows = snapshot.get('rows')
    if not isinstance(rows, list) or len(rows) > 17:
        raise ValueError('Inventário de filamentos inválido.')
    targets = set()
    cfs = []
    external = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('profile'), dict):
            raise ValueError('Cadastro de bobina inválido.')
        target = row.get('target')
        if target is None:
            if external is not None:raise ValueError('Bobina externa duplicada.')
            external = row
            continue
        if (not isinstance(target, list) or len(target) != 2 or
                not re.fullmatch(r'T[1-4]', str(target[0])) or not isinstance(target[1],str) or target[1] not in ('A','B','C','D')):
            raise ValueError('Posição CFS inválida.')
        key = ''.join(target)
        if key in targets:raise ValueError('Posição CFS duplicada.')
        targets.add(key)
        cfs.append((key, (int(target[0][1:]) - 1) * 4 + 'ABCD'.index(target[1]), row))
    # MoonrakerPrinterAgent only consumes indexed lanes, not vt_tray. Keep the
    # external spool separate; never append it as another CFS/tool-change slot.
    mode = 'cfs' if cfs else 'external'
    candidates = cfs if cfs else [('external', 0, external)] if external else []
    lanes = {}
    for key, index, row in candidates:
        profile = row['profile']
        material = profile.get('materialType') if row.get('present') is True else ''
        if not isinstance(material, str) or len(material) > 80:material = ''
        color=rgb(profile.get('color')) if material else ''
        colors=(row.get('appearance') or {}).get('colors') if material and isinstance(row.get('appearance'),dict) else None
        if (not isinstance(colors,list) or not 1<=len(colors)<=4 or
                any(not isinstance(c,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in colors)):
            colors=[]
        colors=[c.upper() for c in colors]
        native=str(profile.get('color') or '').upper()
        primary=('#'+native[2:]) if re.fullmatch(r'#0[0-9A-F]{6}',native) else rgb(native)
        if colors and colors[0]!=primary:colors=[]
        if len(colors)>1:color=colors[0]
        lane=dict(lane=str(index), color=color,
            material=material.strip(), _dps_owner=OWNER, _dps_printer=printer_id, _dps_source=key)
        if len(colors)>1:lane.update(_dps_color_stops=colors,_dps_color_mode='gradient')
        lanes['dps-'+key]=lane
    rack = external['profile'] if external else {}
    return lanes, dict(mode=mode, external=dict(color=rgb(rack.get('color')),
        material=str(rack.get('materialType') or '')[:80], included=mode == 'external'),
        slots=len(cfs), externalAlongsideCfs=False)


class FilamentSync:
    def __init__(self, printer_id, config_path, native, upstream, interval=5):
        if not re.fullmatch('[a-f0-9]{32}', str(printer_id)):
            raise ValueError('Identidade da impressora ausente.')
        self.printer_id=printer_id;self.path=Path(config_path)
        self.native=native;self.upstream=upstream;self.interval=interval
        self.stop=threading.Event();self.wake=threading.Event()
        self.lock=threading.RLock();self.cycle=threading.Lock();self.thread=None;self.cleanup=None
        self.state=dict(version=PROTOCOL,enabled=False,status='disabled',message='')

    def start(self):
        if self.thread is not None:return
        self.thread=threading.Thread(target=self.run,name='studio-orca-filaments',daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()

    def settings(self):
        if not self.path.exists():return dict(enabled=False)
        if self.path.is_symlink() or self.path.stat().st_size > 4096:
            raise ValueError('Configuração da ponte inválida.')
        value=json.loads(self.path.read_text())
        if value.get('version')!=PROTOCOL or value.get('printerId')!=self.printer_id or type(value.get('enabled')) is not bool:
            raise ValueError('Configuração pertence a outra impressora ou versão.')
        return value

    def status(self):
        with self.lock:return dict(self.state)

    def configure(self, enabled):
        if type(enabled) is not bool:raise ValueError('Estado da ponte inválido.')
        with self.cycle:
            previous=self.settings()
            if previous['enabled']!=enabled:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                fd,name=tempfile.mkstemp(prefix='.orca-filaments-',dir=str(self.path.parent))
                try:
                    with os.fdopen(fd,'w') as stream:
                        json.dump(dict(version=PROTOCOL,printerId=self.printer_id,enabled=enabled),stream)
                        stream.flush();os.fsync(stream.fileno())
                    os.chmod(name,0o600);os.replace(name,self.path)
                finally:
                    if os.path.exists(name):os.unlink(name)
            if not enabled:self.cleanup=True
            with self.lock:self.state.update(enabled=enabled,status='pending',message='')
        self.wake.set()
        return self.status()

    def get(self, namespace):
        try:result=self.upstream('/server/database/item?namespace='+namespace)
        except urllib.error.HTTPError as error:
            if error.code==404:return {}
            raise
        value=result.get('value')
        if not isinstance(value,dict):raise ValueError('Cadastro Moonraker inválido.')
        return value

    def put(self, namespace, key, value):
        if self.stop.is_set():raise ValueError('Ponte encerrada.')
        self.upstream('/server/database/item',json.dumps(dict(namespace=namespace,key=key,value=value)).encode())

    def delete(self,key):
        # Recheck before deleting an obsolete record. Never remove a record
        # that another writer has taken over since the cycle began.
        if self.stop.is_set():raise ValueError('Ponte encerrada.')
        self.upstream('/server/database/item?'+urllib.parse.urlencode(dict(namespace=LANES,key=key)),b'',method='DELETE')

    def mirror(self, desired, metadata):
        lanes=self.get(LANES);record=self.get(NAMESPACE).get('bridge')
        if record is not None and (not isinstance(record,dict) or record.get('owner')!=OWNER or record.get('printerId')!=self.printer_id):
            raise ValueError('Outro serviço é responsável pela sincronização de filamentos.')
        known={}
        if record:
            for field in ('lanes','pending'):
                value=record.get(field,{})
                if not isinstance(value,dict):raise ValueError('Registro da ponte inválido.')
                for key,row in value.items():known.setdefault(key,[]).append(row)
        for key,row in lanes.items():
            if key not in known or row not in known[key]:
                raise ValueError('O cadastro de filamentos já é mantido por outro serviço ou foi alterado externamente.')
        if lanes==desired and record and record.get('metadata')==metadata and not record.get('pending'):
            return
        # Journal at most current + next snapshot, not a growing history. A crash
        # during a per-key Moonraker update can be reconciled on the next pass.
        journal=dict(owner=OWNER,printerId=self.printer_id,lanes=lanes,pending=desired,metadata=metadata)
        self.put(NAMESPACE,'bridge',journal)
        for key,row in desired.items():
            current=self.get(LANES)
            if any(k not in known and k not in desired for k in current):
                raise ValueError('O cadastro mudou durante a sincronização.')
            actual=current.get(key)
            if actual==row:continue
            if actual is not None and actual not in known.get(key,[]):
                raise ValueError('Uma posição mudou durante a sincronização.')
            self.put(LANES,key,row)
        for key,row in lanes.items():
            if key not in desired:
                if self.get(LANES).get(key)!=row:raise ValueError('Uma posição mudou durante a sincronização.')
                self.delete(key)
        if self.get(LANES)!=desired:raise ValueError('Moonraker não confirmou os filamentos.')
        self.put(NAMESPACE,'bridge',dict(owner=OWNER,printerId=self.printer_id,lanes=desired,metadata=metadata))

    def sync_once(self):
        with self.cycle:
            if self.stop.is_set():return
            enabled=self.settings()['enabled']
            if not enabled:
                # No network work at startup before the first opt-in. When
                # disabled after activation, remove only our exact records.
                if self.cleanup is True or (self.cleanup is None and self.path.exists()):
                    self.mirror({},dict(mode='disabled'))
                self.cleanup=False
                with self.lock:self.state=dict(version=PROTOCOL,enabled=False,status='disabled',message='')
                return
            try:
                lanes,metadata=inventory(self.native(dict(cmd='materials')),self.printer_id)
            except Exception:
                # Do not keep stale colors when Aurora is unreachable. Existing
                # lanes remain ownership checked; foreign data is never cleared.
                self.mirror({},dict(mode='unavailable'))
                raise ValueError('Aguardando o cadastro de filamentos da Aurora.')
            self.mirror(lanes,metadata)
            with self.lock:self.state=dict(version=PROTOCOL,enabled=True,status='ready',message='',**metadata)

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:self.sync_once()
            except Exception as error:
                with self.lock:self.state.update(status='error',message=str(error)[:240] if isinstance(error,ValueError) else 'Não foi possível sincronizar os filamentos.')
            self.wake.wait(self.interval)
