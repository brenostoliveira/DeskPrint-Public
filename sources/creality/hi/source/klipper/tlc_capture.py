# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Klipper bridge: guards, bounded queue waiting and public status.

Camera coordination, bindings and web routes live in an independent service.
This module never imports that service. See CAPTURE_BRIDGE_LICENSE.md.
Modified 2026-11-02: optional, session-bound monitoring setup at SD boundaries.
"""
import json
import math
import socket
import threading
import time
import uuid
import re
import os
from pathlib import Path
try:
    from greenlet import getcurrent as command_context  # Existing Klipper reactor dependency.
except ImportError:
    command_context=threading.current_thread  # Non-reactor, isolated fixtures only.

MAX_WAIT = 7.
MAX_MESSAGE = 16384
DEFAULT_SOCKET = '/var/run/deskprinter-capture/control.sock'


class Capture:
    channel_family = getattr(socket, 'AF_UNIX', None)
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.endpoint = config.get('capture_socket', DEFAULT_SOCKET)
        self.timeout = min(MAX_WAIT, config.getfloat('timeout', MAX_WAIT, minval=.1))
        # Consume legacy options so existing printer.cfg remains valid. The
        # bridge neither uses these credentials nor opens their listeners.
        for name in ('gateway_url','gateway_token','direct_camera_port','direct_camera_token'):
            config.get(name, None)
        self.report = dict(result='idle', mode='', frame=0, last_wait=0., busy=False,
                           last_error=None, max_wait=self.timeout, captureSession={})
        self.deadline = self.started = None
        self.lock = threading.Lock()
        self.channels = set()
        self.status_pending = self.local_busy = self.closed = False
        self.status_wake = threading.Event()
        self.status_worker = None
        self.native_original=None;self.native_override=None;self.native_job=None
        self.native_frame_key=None;self.native_confirmed=False;self.native_warning=None
        self.monitor_path=Path(config.get('monitoring_config', '/mnt/UDISK/printer_data/config/deskprinter_monitoring.json'))
        self.monitoring=dict(enabled=False,interval=10,deviceId='',autoOpen=False)
        self.setup=dict(phase='idle',token='',filename='')
        self.setup_source=None;self.setup_homed_source=None;self.setup_seen_source=None
        self.setup_position=None;self.setup_supported=False;self.setup_releasing=False
        self.script_depth={}
        self.monitor_error='';self.layer_reader=None;self.layer_job=None;self.layer_prefix=None
        self.layer_number=0;self.layer_position=-1;self.monitor_attempted=0;self.monitor_busy=False;self.monitor_unsafe=False
        self.monitor_owner=''
        try:
            self.monitor_owner=json.loads((self.monitor_path.parent/'tlc_camera_bindings.json.identity.json').read_text())['printerId']
            saved=json.loads(self.monitor_path.read_text())
            if saved.get('printerId')==self.monitor_owner:self.monitoring=self.monitor_policy(saved)
        except (OSError,ValueError,KeyError,TypeError):pass
        self.gcode.register_command('TLC_MONITORING',self.configure_monitoring)
        self.gcode.register_command('TLC_MONITORING_START',self.start_monitoring)
        self.gcode.register_command('TLC_MONITORING_PARK',self.park_monitoring)
        self.printer.register_event_handler('virtual_sdcard:reset_file',self.reset_layers)
        for name, method in (('TLC_CAPTURE_PREPARE',self.prepare),
                             ('TLC_CAPTURE_WAIT',self.capture),('TLC_CAPTURE_TEST',self.test),
                             ('TLC_TIMELAPSE',self.timelapse_mode)):
            self.gcode.register_command(name, method)
        self.printer.register_event_handler('klippy:ready', self.ready)
        self.printer.register_event_handler('klippy:disconnect', self.close)
        self.printer.register_event_handler('klippy:shutdown', self.close)

    def close(self, *args):
        with self.lock:
            self.closed = True
            channels = list(self.channels)
        self.status_wake.set()
        for channel in channels:
            try:channel.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            channel.close()

    def ready(self):
        original=self.gcode.register_command('M5001',None)
        if original is not None:
            self.native_original=original
            self.gcode.register_command('M5001',self.native_capture_command)
        sd=self.printer.lookup_object('virtual_sdcard',None)
        if sd is not None and callable(getattr(sd,'record_layer_info',None)):
            self.layer_reader=sd.record_layer_info
            def layer_reader(line,*args,**kwargs):
                result=self.layer_reader(line,*args,**kwargs)
                self.monitor_line(sd,line)
                return result
            sd.record_layer_info=layer_reader
        self.install_setup_interception(sd)
        if self.status_worker is None:
            self.status_worker = threading.Thread(target=self.status_loop, daemon=True, name='capture-status')
            self.status_worker.start()
        self.reactor.register_timer(self.refresh, self.reactor.monotonic()+1.)

    @staticmethod
    def monitor_policy(value):
        enabled=value.get('enabled')
        interval=value.get('interval')
        device=value.get('deviceId','')
        if (type(enabled) is not bool or type(interval) is not int or not 1<=interval<=10000
                or not isinstance(device,str) or (device and not re.fullmatch('[a-f0-9]{32}',device))
                or (enabled and not device)):
            raise ValueError('Invalid monitoring capture policy')
        auto=value.get('autoOpen',False)
        if type(auto) is not bool or (auto and not device):raise ValueError('Invalid automatic setup policy')
        analysis=value.get('analysisId','')
        if not isinstance(analysis,str) or (analysis and not re.fullmatch('[a-f0-9]{32}',analysis)):raise ValueError('Invalid analysis identity')
        return dict(enabled=enabled,interval=interval,deviceId=device,autoOpen=auto,analysisId=analysis)

    def configure_monitoring(self,gcmd):
        if self.monitor_busy:raise gcmd.error('Wait until the exposure finishes')
        if self.layer_reader is None:raise gcmd.error('Layer interception unavailable on this firmware')
        owner=gcmd.get('OWNER')
        if not self.monitor_owner or owner!=self.monitor_owner:raise gcmd.error('Monitoring printer identity mismatch')
        policy=self.monitor_policy(dict(enabled=bool(gcmd.get_int('ENABLE',minval=0,maxval=1)),
            interval=gcmd.get_int('INTERVAL',minval=1,maxval=10000),deviceId=gcmd.get('DEVICE',''),
            autoOpen=bool(int(gcmd.get('AUTO_OPEN',int(self.monitoring.get('autoOpen',False))))),
            analysisId=('' if gcmd.get('ANALYSIS')=='none' else gcmd.get('ANALYSIS',self.monitoring.get('analysisId','')))))
        if policy['autoOpen'] and not self.setup_supported:raise gcmd.error('Automatic monitoring setup unavailable on this firmware')
        temporary=self.monitor_path.with_suffix('.pending')
        try:
            with temporary.open('w') as output:
                json.dump(dict(policy,version=1,printerId=owner),output)
                output.flush();os.fsync(output.fileno())
            temporary.chmod(0o600);os.replace(temporary,self.monitor_path)
        finally:
            if temporary.exists():temporary.unlink()
        self.monitoring=policy;self.monitor_error=''

    def install_setup_interception(self,sd):
        # Do not interrupt or discard a native macro. G28 only records that it
        # finished; the top-level SD command boundary owns pause and parking.
        if (sd is None or not callable(getattr(sd,'do_pause',None))
                or not callable(getattr(sd,'do_resume',None))
                or not callable(getattr(self.gcode,'run_script',None))
                or not callable(getattr(self.gcode,'get_mutex',None))):return
        original=self.gcode.register_command('G28',None)
        if original is None:return
        def home(gcmd):
            result=original(gcmd)
            params=gcmd.get_command_parameters()
            if (self.monitoring.get('autoOpen') and getattr(sd,'cmd_from_sd',False) and (not params or 'Z' in params)
                    and self.printer.lookup_object('toolhead').get_status(self.reactor.monotonic()).get('homed_axes')=='xyz'):
                self.setup_homed_source=getattr(sd,'current_file',None)
            return result
        self.gcode.register_command('G28',home)
        run=self.gcode.run_script;resume=sd.do_resume
        def script(text,*args,**kwargs):
            if not self.monitoring.get('autoOpen'):return run(text,*args,**kwargs)
            context=command_context();depth=self.script_depth.get(context,0)
            self.script_depth[context]=depth+1
            try:result=run(text,*args,**kwargs)
            except BaseException:
                self.setup_homed_source=None
                raise
            finally:
                if depth:self.script_depth[context]=depth
                else:self.script_depth.pop(context,None)
            if not depth and self.setup_homed_source is not None and getattr(sd,'cmd_from_sd',False):
                with self.gcode.get_mutex():self.prepare_monitoring_setup(sd)
            return result
        def guarded_resume(*args,**kwargs):
            if self.setup_status()['phase']=='waiting' and not self.setup_releasing:
                raise self.gcode.error('Align the monitoring view and use Start monitoring before resuming')
            return resume(*args,**kwargs)
        self.gcode.run_script=script;sd.do_resume=guarded_resume
        self.setup_supported=True

    def setup_status(self):
        value=dict(self.setup)
        if value['phase']=='waiting':
            sd=self.printer.lookup_object('virtual_sdcard',None)
            if (self.closed or self.printer.is_shutdown() or sd is None
                    or getattr(sd,'current_file',None) is not self.setup_source):value['phase']='cancelled'
        return value

    def prepare_monitoring_setup(self,sd):
        source=getattr(sd,'current_file',None)
        if source is None or source is not self.setup_homed_source:return
        self.setup_homed_source=None
        if source is self.setup_seen_source:return
        if not self.monitoring.get('autoOpen') or self.closed or self.printer.is_shutdown():return
        if not sd.is_active() or getattr(sd,'must_pause_work',False):return
        stats=self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        if (stats.get('state')!='printing' or not stats.get('filename') or self.layer_number>0
                or (getattr(sd,'layer',0) or 0)>0 or bool(getattr(sd,'slow_print',False))
                or stats.get('power_loss')):return
        self.setup_seen_source=source
        head=self.printer.lookup_object('toolhead');limits=head.get_status(self.reactor.monotonic())
        position=list(head.get_position());minimum=limits.get('axis_minimum');maximum=limits.get('axis_maximum')
        if (limits.get('homed_axes')!='xyz' or not minimum or not maximum or len(position)<4
                or not all(math.isfinite(v) for v in position[:3])
                or not minimum[0]<=0<=maximum[0] or not minimum[1]<=180<=maximum[1]
                or position[2]+.4>maximum[2]):
            self.monitor_error='Monitoring setup skipped: axes or parking clearance unavailable';return
        # Stops only the SD dispatch loop. No PAUSE macro, retract, E/G92,
        # homing, calibration or heater command is inserted or repeated.
        sd.do_pause()
        self.setup_source=source;self.setup_position=position
        self.setup=dict(phase='parking',token=uuid.uuid4().hex,filename=stats['filename'])
        head.wait_moves()
        if self.closed or self.printer.is_shutdown() or sd.current_file is not source:return
        try:
            head.manual_move([None,None,position[2]+.4,None],5.)
            head.manual_move([0.,180.,None,None],150.)
            head.wait_moves()
            self.setup['phase']='waiting'
        except Exception:
            self.setup['phase']='error'
            self.monitor_error='Monitoring parking failed; print remains paused'
            raise

    def start_monitoring(self,gcmd):
        if gcmd.get('OWNER')!=self.monitor_owner:raise gcmd.error('Monitoring printer identity mismatch')
        state=self.setup_status();token=gcmd.get('TOKEN')
        if token==state['token'] and state['phase']=='running':return
        if state['phase']!='waiting' or token!=state['token']:raise gcmd.error('Monitoring setup no longer belongs to this print')
        sd=self.printer.lookup_object('virtual_sdcard');head=self.printer.lookup_object('toolhead')
        stats=self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        if (sd.is_active() or stats.get('state')!='paused' or stats.get('filename')!=state['filename']
                or head.get_status(self.reactor.monotonic()).get('homed_axes')!='xyz'):
            raise gcmd.error('Wait for the monitoring pause to finish; check print and axes')
        current=list(head.get_position());expected=self.setup_position
        if (abs(current[0])>.05 or abs(current[1]-180)>.05 or abs(current[2]-expected[2]-.4)>.05
                or abs(current[3]-expected[3])>.01):raise gcmd.error('Position changed during alignment; automatic resume refused')
        head.manual_move([expected[0],expected[1],None,None],150.)
        head.manual_move([None,None,expected[2],None],5.)
        head.wait_moves()
        if self.setup_status()['phase']!='waiting':raise gcmd.error('Print changed during alignment')
        self.setup_releasing=True
        try:sd.do_resume()
        finally:self.setup_releasing=False
        self.setup['phase']='running'

    def park_monitoring(self,gcmd):
        if not self.monitor_owner or gcmd.get('OWNER')!=self.monitor_owner:raise gcmd.error('Monitoring printer identity mismatch')
        now=self.reactor.monotonic();sd=self.printer.lookup_object('virtual_sdcard')
        stats=self.printer.lookup_object('print_stats').get_status(now)
        observer=self.printer.lookup_object('aurora_phase_observer',None)
        if (self.closed or self.printer.is_shutdown() or stats.get('state')!='standby' or sd.is_active()
                or self.monitor_busy or (observer and observer.get_status(now).get('nativePreparation'))):
            raise gcmd.error('Positioning is available only while idle, before starting a print')
        head=self.printer.lookup_object('toolhead');limits=head.get_status(now)
        pos=list(head.get_position());minimum=limits.get('axis_minimum');maximum=limits.get('axis_maximum')
        height=max(pos[2],5.)
        if (limits.get('homed_axes')!='xyz' or not minimum or not maximum
                or not all(math.isfinite(v) for v in pos[:3]) or height>maximum[2]
                or not minimum[0]<=0<=maximum[0] or not minimum[1]<=180<=maximum[1]):
            raise gcmd.error('Home XYZ first and check parking clearance')
        head.wait_moves()
        if self.printer.is_shutdown():raise gcmd.error('Printer stopped before positioning')
        if pos[2]<height:head.manual_move([None,None,height,None],5.)
        if abs(pos[0])>.01 or abs(pos[1]-180)>.01:head.manual_move([0.,180.,None,None],150.)
        head.wait_moves()

    def reset_layers(self,*args):
        self.layer_job=None;self.layer_prefix=None;self.layer_number=0;self.layer_position=-1;self.monitor_attempted=0
        self.monitor_unsafe=False

    def native_enabled(self,sd):
        if self.native_override is not None:return self.native_override
        return bool(sd.get_delay_photography_info()[0])

    def monitor_line(self,sd,line):
        # Called synchronously by the SD reader, before it dispatches this line.
        # Never infer a physical barrier from an asynchronous layer/status poll.
        if not line.startswith((';','END_PRINT')):return
        prefixes=('; layer #',';LAYER:','; layer:','; LAYER:',';AFTER_LAYER_CHANGE',';LAYER_CHANGE')
        prefix=next((p for p in prefixes if line.startswith(p)),None) if line.startswith(';') else None
        final=not prefix and bool(re.match(r'^END_PRINT(?:\s|$)',line))
        if not prefix and not final:return
        if not getattr(sd,'cmd_from_sd',False) or self.monitor_busy:return
        now=self.reactor.monotonic()
        stats=self.printer.lookup_object('print_stats').get_status(now)
        if stats.get('state')!='printing' or not sd.is_active():return
        job=(getattr(sd,'current_file',None),stats.get('filename'),stats.get('start_time'))
        if job!=self.layer_job:
            self.reset_layers();self.layer_job=job
            self.monitor_unsafe,self.layer_prefix=self.monitor_file_policy(getattr(sd,'current_file',None))
        position=getattr(sd,'file_position',None)
        if type(position) in (int,float):
            if position==self.layer_position:return
            if position<self.layer_position:self.monitor_unsafe=True
            self.layer_position=position
        if prefix and (self.layer_prefix is None or prefix==self.layer_prefix):
            self.layer_prefix=prefix
            completed=self.layer_number
            self.layer_number+=1
        elif final:completed=self.layer_number
        else:return
        policy=self.monitoring
        if (not policy['enabled'] or not completed or completed%policy['interval']
                or completed<=self.monitor_attempted):return
        self.monitor_attempted=completed
        # Native timelapse owns its parking and shutter. Never add a second one.
        if self.native_enabled(sd):return
        if self.monitor_unsafe:
            self.monitor_error='Monitoring parking requires verified post-cleanup layer markers and non-sequential/non-spiral G-code';return
        self.monitor_capture(sd,job,completed,dict(policy))

    @staticmethod
    def monitor_file_policy(source):
        # Separate descriptor: never seek or rewrite the SD reader's open file.
        name=getattr(source,'name',None)
        if not name:return True,None
        try:
            with open(name,'rb') as stream:
                raw=stream.read(131072)
                stream.seek(0,2);size=stream.tell()
                if size>131072:
                    stream.seek(max(131072,size-131072));raw+=stream.read(131072)
            text=raw.decode('utf8','replace')
            # Orca/Creality LAYER_CHANGE precedes their retract/wipe. The AFTER
            # marker follows those operations: never park on the earlier one.
            preferred=';AFTER_LAYER_CHANGE' if re.search(r'^;AFTER_LAYER_CHANGE\s*$',text,re.M) else None
            unsafe=preferred is None or bool(re.search(r'^;\s*(?:(?:print_sequence\s*=\s*by object)|(?:(?:spiral_mode|spiral_vase|complete_objects)\s*=\s*(?:1|true)))\s*$',text,re.M|re.I))
            return unsafe,preferred
        except OSError:return True,None

    def monitor_capture(self,sd,job,layer,policy):
        head=self.printer.lookup_object('toolhead')
        def valid():
            stats=self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
            return (not self.closed and not self.printer.is_shutdown() and sd.is_active() and stats.get('state')=='printing'
                and (getattr(sd,'current_file',None),stats.get('filename'),stats.get('start_time'))==job
                and head.get_status(self.reactor.monotonic()).get('homed_axes')=='xyz')
        if not valid():self.monitor_error='Capture skipped: print/axes not ready';return
        limits=head.get_status(self.reactor.monotonic())
        maximum=limits.get('axis_maximum');minimum=limits.get('axis_minimum')
        position=list(head.get_position())
        if (not maximum or not minimum or len(position)<4
                or not all(math.isfinite(v) for v in position[:3])
                or not minimum[0]<=0<=maximum[0] or not minimum[1]<=180<=maximum[1]
                or position[2]+.4>maximum[2]):
            self.monitor_error='Capture skipped: insufficient parking clearance';return
        self.monitor_busy=True;self.monitor_error=''
        context=dict(layer=layer,deviceId=policy['deviceId'],purpose='monitoring',analysisId=policy.get('analysisId',''))
        class Notice:
            def respond_info(_,text):self.monitor_error=text
            def error(_,text):return self.gcode.error(text)
        notice=Notice();moved=False
        try:
            self._begin()
            # Prepare/availability first: no ready camera means no extra travel.
            if not self._call('monitor-prepare',str(job[1]),notice,context):return
            if self.get_status(0).get('result')!='prepared' or not valid():return
            head.wait_moves()
            if not valid():return
            lifted=position[2]+.4
            # Machine coordinates and manual_move preserve G90/G91, G92, E,
            # offsets and feed/speed factors. No extrusion or homing commands.
            moved=True
            head.manual_move([None,None,lifted,None],5.)
            head.manual_move([0.,180.,None,None],150.)
            head.wait_moves()
            if valid() and self.reactor.monotonic()+.15<self.deadline:
                self.reactor.pause(self.reactor.monotonic()+.15)
                if valid():self._call('monitor-frame',str(job[1]),notice,context)
        finally:
            try:
                # Never undo a cancel, pause, shutdown or replacement SD job.
                if moved and valid():
                    head.manual_move([position[0],position[1],None,None],150.)
                    head.manual_move([None,None,position[2],None],5.)
                    head.wait_moves()
            finally:self.monitor_busy=False

    def status_loop(self):
        # One sleeping worker, never a network operation on Klipper's reactor.
        # Status is expendable; capture requests keep their independent deadline.
        while True:
            self.status_wake.wait()
            self.status_wake.clear()
            with self.lock:
                if self.closed:return
                fetch = self.status_pending and not self.local_busy
            try:
                if fetch:self.transport('status','',time.monotonic()+.25)
            except (OSError,ValueError):pass
            finally:
                with self.lock:self.status_pending = False

    def timelapse_mode(self,gcmd):
        # This marker belongs to the actual SD file, not a global preference
        # left behind by the previous print. Legacy Creality uses its own flag.
        stats=self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        if not self.printer.lookup_object('virtual_sdcard').is_active() or stats.get('state')!='printing':
            raise gcmd.error('TLC_TIMELAPSE belongs to an active SD job')
        self.native_override=bool(gcmd.get_int('ENABLE',minval=0,maxval=1))
        self.native_job=str(stats.get('filename',''));self.native_frame_key=None;self.native_confirmed=False

    @staticmethod
    def native_part(raw):
        line=' '.join(raw.split(';',1)[0].upper().split())
        if line=='G92 E0':return 'reset',line
        if line=='G17':return 'plane',line
        if re.fullmatch(r'G2 Z-?(?:\d+(?:\.\d*)?|\.\d+) I0\.86 J0\.86 P1 F20000',line):return 'lift',line
        if line=='G1 X0 Y180 F24000':return 'park',line
        if line=='P15':return 'capture',line
        if line=='M400 P300':return 'post-wait',line
        return None,line

    def native_capture_command(self,gcmd):
        part,line=self.native_part(gcmd.get_raw_command_parameters())
        if part is None:return self.native_original(gcmd)
        if getattr(self,'monitor_busy',False):return
        sd=self.printer.lookup_object('virtual_sdcard')
        stats=self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        job=str(stats.get('filename',''))
        if self.native_job is not None and self.native_job!=job:
            self.native_job=None;self.native_override=None;self.native_frame_key=None
        enabled=self.native_override
        if enabled is None:
            # Public firmware method: same switch used by stock M5001.
            enabled=bool(sd.get_delay_photography_info()[0])
        if not enabled:return
        self._job(gcmd)  # Never generate parking from a manual/idle invocation.
        if part=='capture':
            status=sd.get_status(self.reactor.monotonic())
            frame_key=(job,status.get('file_position'))
            if frame_key==self.native_frame_key:return
            self.native_frame_key=frame_key;self.native_confirmed=False
            self._begin()
            self.printer.lookup_object('toolhead').wait_moves()
            self.reactor.pause(min(self.deadline,self.reactor.monotonic()+.15))
            context=None;policy=self.monitoring
            if (policy['enabled'] and policy.get('analysisId') and self.layer_number>0
                    and self.layer_number%policy['interval']==0 and not self.monitor_unsafe
                    and self.layer_job and self.layer_job[0] is getattr(sd,'current_file',None)):
                context=dict(layer=self.layer_number,deviceId=policy['deviceId'],purpose='monitoring',analysisId=policy['analysisId'])
            self.native_confirmed=self._call('native-frame',job,gcmd,context) and self.get_status(0).get('result')=='complete'
            return
        if part=='post-wait' and self.native_confirmed:
            # Exposure completion replaces only this fixed post-shutter delay.
            # Keep the planner barrier and every original lift/park/reset.
            self.gcode.run_script_from_command('M400');return
        if part in ('reset','plane','lift','park'):
            if part=='plane':self.native_confirmed=False
            self.gcode.run_script_from_command(line)
            # The shutter handler already waits for all lift/park moves. Do
            # not flush the planner while the nozzle is still over the part.
            return
        return self.native_original(gcmd)

    def get_status(self, eventtime):
        with self.lock:
            value = dict(self.report)
            value['captureSession'] = dict(value['captureSession'])
            value['max_wait'] = self.timeout
            value['nativeInterception'] = self.native_original is not None
            value['monitoring']=dict(self.monitoring,printerId=self.monitor_owner,
                supported=self.layer_reader is not None,layer=self.layer_number,
                lastAttemptedLayer=self.monitor_attempted,busy=self.monitor_busy,error=self.monitor_error,
                setupSupported=self.setup_supported,parkSupported=True,setup=self.setup_status())
            return value

    def transport(self, operation, job, deadline, context=None):
        request_id = uuid.uuid4().hex
        remaining = deadline-time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise TimeoutError('Capture acknowledgement timed out')
        value = dict(version=1,operation=operation,requestId=request_id,job=job,deadline=deadline)
        if context is not None:value['monitoring']=context
        if self.channel_family is None:raise OSError('Unix sockets are required on the printer')
        channel = socket.socket(self.channel_family, socket.SOCK_STREAM)
        with self.lock:
            if self.closed:
                channel.close()
                raise OSError('Capture bridge disconnected')
            self.channels.add(channel)
        try:
            channel.settimeout(min(MAX_WAIT,remaining))
            channel.connect(self.endpoint)
            channel.sendall(json.dumps(value).encode()+b'\n')
            raw = bytearray()
            while b'\n' not in raw:
                remaining = deadline-time.monotonic()
                if remaining <= 0:raise TimeoutError('Capture acknowledgement timed out')
                channel.settimeout(remaining)
                part = channel.recv(min(4096,MAX_MESSAGE+1-len(raw)))
                if not part:raise OSError('Capture service disconnected')
                raw.extend(part)
                if len(raw)>MAX_MESSAGE:raise ValueError('Capture response too large')
            if time.monotonic()>=deadline:raise TimeoutError('Late capture reply ignored')
            reply = json.loads(raw)
            if reply.get('requestId')!=request_id or type(reply.get('ok')) is not bool:
                raise ValueError('Capture response identity mismatch')
            report = reply.get('status')
            if not isinstance(report,dict) or not isinstance(report.get('captureSession'),dict):
                raise ValueError('Invalid capture status')
            with self.lock:
                if operation!='status' or not self.local_busy:
                    self.report = {key:report.get(key) for key in self.report}
            return reply
        finally:
            with self.lock:self.channels.discard(channel)
            channel.close()

    def refresh(self, eventtime):
        stats=self.printer.lookup_object('print_stats').get_status(eventtime)
        if stats.get('state') not in ('printing','paused'):
            self.native_override=None;self.native_job=None;self.native_frame_key=None
        with self.lock:
            fetch = not (self.closed or self.status_pending or self.local_busy)
            if fetch:self.status_pending = True
            closed = self.closed
        if closed:return self.reactor.NEVER
        if fetch:
            self.status_wake.set()
        return eventtime+1.

    def _begin(self):
        self.started = self.reactor.monotonic()
        self.deadline = self.started+self.timeout

    def _job(self, gcmd):
        stats = self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        if not self.printer.lookup_object('virtual_sdcard').is_active() or stats.get('state')!='printing':
            raise gcmd.error('TIMELAPSE_TAKE_FRAME requires an active SD print; use TLC_CAPTURE_TEST without motion')
        if self.printer.lookup_object('toolhead').get_status(self.reactor.monotonic()).get('homed_axes')!='xyz':
            raise gcmd.error('All axes must already be homed for timelapse parking')
        return str(stats.get('filename',''))

    def _call(self, operation, job, gcmd, context=None):
        deadline = time.monotonic()+max(0,self.deadline-self.reactor.monotonic())
        done = threading.Event()
        result = {}
        with self.lock:self.local_busy = True
        def send():
            try:result['reply'] = self.transport(operation,job,deadline,context) if context is not None else self.transport(operation,job,deadline)
            except Exception as exc:result['error'] = str(exc)[:200]
            finally:done.set()
        threading.Thread(target=send,daemon=True,name='capture-bridge').start()
        try:
            while not done.is_set():
                if self.printer.is_shutdown():
                    self.close()
                    raise gcmd.error('Printer shutdown during capture')
                now = self.reactor.monotonic()
                if now>=self.deadline:break
                self.reactor.pause(min(self.deadline,now+.005))
            if not done.is_set() or time.monotonic()>=deadline or result.get('error') or not result.get('reply',{}).get('ok'):
                with self.lock:
                    message = result.get('error') or self.report.get('last_error') or 'Capture acknowledgement timed out'
                    self.report.update(result='timeout' if not done.is_set() or time.monotonic()>=deadline else 'failed',
                                       busy=False,last_error=message,last_wait=self.reactor.monotonic()-self.started)
                gcmd.respond_info('TLC photo not confirmed; continuing print: '+message)
                return False
            return True
        finally:
            with self.lock:self.local_busy = False

    def prepare(self, gcmd):
        if self.monitor_busy:raise gcmd.error('Monitoring exposure already active')
        job = self._job(gcmd)
        self._begin()
        self._call('prepare',job,gcmd)

    def capture(self, gcmd):
        if self.monitor_busy:raise gcmd.error('Monitoring exposure already active')
        state = self.get_status(0).get('result')
        if state in ('failed','timeout'):return
        if self.deadline is None or state!='prepared':
            raise gcmd.error('TLC capture was not prepared for this print')
        job = self._job(gcmd)
        self.printer.lookup_object('toolhead').wait_moves()
        self.reactor.pause(min(self.deadline,self.reactor.monotonic()+.15))
        self._call('capture',job,gcmd)

    def test(self, gcmd):
        if self.monitor_busy:raise gcmd.error('Monitoring exposure already active')
        stats = self.printer.lookup_object('print_stats').get_status(self.reactor.monotonic())
        if stats.get('state') in ('printing','paused'):
            raise gcmd.error('TLC_CAPTURE_TEST is only allowed while idle')
        self._begin()
        if self._call('test','test',gcmd):
            gcmd.respond_info('TLC exposure confirmed without motion: %.3fs' % self.get_status(0)['last_wait'])


def load_config(config):
    return Capture(config)
