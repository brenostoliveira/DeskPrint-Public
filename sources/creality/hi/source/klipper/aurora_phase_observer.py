"""Optional Klipper observer. No heater or motion commands are generated."""
import json, re, time, uuid, logging, threading, math
from collections import deque

class NativeMaterialLog(logging.Handler):
    """Passive fallback for compiled CFS internals bypassing Python method hooks."""
    PREFIXES=('[box] cut sensor detected','[box] cut to return OK','cmd: ','extrude_process_stage7 tnn:','extrude_process_stage7_ret:',
              'flush; last_tnn:','extrude = ','last flush need retrude:',
              'go to wipe_tower_y posison','last_flush wipe_tower_y')
    def __init__(self, observer):
        super().__init__(logging.INFO);self.observer=observer
    def emit(self, record):
        try:
            if not isinstance(record.msg,str) or not record.msg.startswith(self.PREFIXES):return
            if record.module not in ('gcode','reactor','klippy'):return
            value=record.getMessage()
            if value.startswith('cmd: ') and value not in ('cmd: RETRUDE_PROCESS','cmd: EXTRUDE_PROCESS','cmd: EXTRUDE_PROCESS_MODEL2'):return
            if len(value)<=1024:self.observer.queue_native_signal(value)
        except Exception:
            # Diagnostics must never change the firmware's command result.
            self.observer.native_log_error='Native material observation failed'


class AuroraPhaseObserver:
    COMMANDS = ('START_PRINT','END_PRINT','CANCEL_PRINT','G28','NOZ_CLEAR','M190','M109','BED_MESH_CALIBRATE','CXSAVE_CONFIG','AURORA_PHASE')
    # Optional firmware handlers: absence is reported, never replaced by a stub.
    ASSOCIATIONS = {
        'BOX_LOAD_MATERIAL_WITH_MATERIAL': 'filament-change',
        'BOX_LOAD_MATERIAL_WITHOUT_MATERIAL': 'filament-change',
        'BOX_LOAD_MATERIAL_WITH_MATERIAL_END': 'filament-change-end',
        'BOX_CUT_MATERIAL': 'filament-cutting',
        'BOX_RETRUDE_MATERIAL_WITH_TNN': 'filament-retraction-cycle',
        'BOX_GO_TO_EXTRUDE_POS': 'purge-positioning',
        'BOX_RETRUDE_MATERIAL': 'filament-unloading',
        'BOX_EXTRUDE_MATERIAL': 'cfs-loading',
        # This handler extrudes at the nozzle, after the CFS feeder. Its
        # positive, slot-qualified log starts purge, not another feed stage.
        'BOX_EXTRUDER_EXTRUDE': 'purge-cycle',
        # Compiled routines may include extrusion AND cleaning. Their return
        # cannot, on its own, prove the boundary between those operations.
        'BOX_MATERIAL_CHANGE_FLUSH': 'purge-cycle',
        'FILAMENT_RACK_PRE_FLUSH': 'filament-loading',
        'LOAD_MATERIAL': 'material-operation',
        'QUIT_MATERIAL': 'material-operation',
        'LOAD_MULTICOLOR_MATERIAL': 'material-operation',
        'BOX_MATERIAL_FLUSH': 'purge-cycle',
        'FILAMENT_RACK_FLUSH': 'purge-cycle',
        'EXTRUDE_BEFORE_CLEAR': 'purge-extrusion',
        'TPU_EXTRUDE_BEFORE_CLEAR': 'purge-extrusion',
        'BOX_NOZZLE_CLEAN': 'purge-discard',
        # QUIT wraps checking, cutting and the retract container. Only the nested
        # BOX_RETRUDE_MATERIAL handler starts the yarn animation.
        'BOX_QUIT_MATERIAL': 'filament-retraction-cycle',
        'BOX_QUIT_MATERIAL_END': 'filament-unloading-end',
        'BOX_END_PRINT': 'finalization-material-cycle',
    }
    EXTRUSION_MACROS = ('EXTRUDE_BEFORE_CLEAR', 'TPU_EXTRUDE_BEFORE_CLEAR')
    FAN_COMMANDS = ('M106','M107','SET_PIN')
    NATIVE_PREPARATION_COMMANDS = ('PRINT_PREPARED','PRINT_TEMP_SET')
    # Public methods present in the Hi's native box/filament_rack modules.
    NATIVE_METHODS = {
        'box':{'cmd_cut_material':'BOX_CUT_MATERIAL','cmd_go_to_extrude_pos':'BOX_GO_TO_EXTRUDE_POS',
               'cmd_box_retrude_material':'BOX_RETRUDE_MATERIAL','cmd_box_extrude_material':'BOX_EXTRUDE_MATERIAL',
               'cmd_extruder_extrude':'BOX_EXTRUDER_EXTRUDE','cmd_material_change_flush':'BOX_MATERIAL_CHANGE_FLUSH',
               'cmd_material_flush':'BOX_MATERIAL_FLUSH','cmd_nozzle_clean':'BOX_NOZZLE_CLEAN','cmd_set_temp':'BOX_SET_TEMP'},
        'filament_rack':{'cmd_filament_rack_pre_flush':'FILAMENT_RACK_PRE_FLUSH',
                         'cmd_filament_rack_flush':'FILAMENT_RACK_FLUSH','cmd_filament_rack_set_temp':'FILAMENT_RACK_SET_TEMP'}}
    def __init__(self, config):
        self.printer=config.get_printer()
        self.gcode=self.printer.lookup_object('gcode')
        self.events=[];self.stack=[];self.sequence=0;self.run_id=None;self.phase='idle'
        self.purge_open=False;self.cycle=0;self.frames=[];self.piece_active=False;self.available_commands=[];self.missing_optional=[]
        self.native_preparation=False
        self.cancel_state='idle';self.cancel_retry_after=0.;self.cancel_duplicates=0
        self.completion_token=None;self.completion_acknowledged=False
        self.active_phases=[];self.direct_macro_hooks=[]
        self.native_method_hooks=[];self.missing_native_hooks=[]
        self.native_log=None;self.native_log_error=None;self.native_log_matches=0
        self.native_queue=deque();self.native_queue_lock=threading.Lock();self.native_scheduled=False
        self.native_phase=None;self.native_flush_slot=None;self.native_slot=None;self.native_cut_seen=False
        self.native_purge_seen=False
        self.printer.register_event_handler('virtual_sdcard:reset_file',self.clear_completion)
        self.printer.register_event_handler('klippy:ready',self.install)
        self.printer.register_event_handler('klippy:shutdown',self.shutdown_observation)
    def emit(self, phase, status, details=None):
        if status in ('error','shutdown'):
            self.native_preparation=False
            if hasattr(self,'native_queue_lock'):
                with self.native_queue_lock:self.native_queue.clear()
                self.native_phase=None;self.native_flush_slot=None
                self.native_purge_seen=False
        self.sequence+=1
        if status!='observation':self.phase=phase
        event={'runId':self.run_id,'sequence':self.sequence,'phase':phase,'status':status,'monotonic':time.monotonic(),'source':'runtime-command','boundary':'handler-return; motion may remain queued'}
        event['command']=self.stack[-1] if self.stack else None
        event['context']=self.context()
        if details is not None:event['details']=details
        self.events.append(event);self.events=self.events[-1024:]
        active=getattr(self,'active_phases',[])
        if status=='start':active.append(phase)
        elif status=='end' and phase in active:
            active.pop(len(active)-1-active[::-1].index(phase))
        elif status in ('error','shutdown') or phase=='cancelled':active.clear()
        self.active_phases=active
        self.gcode.respond_info('AURORA_PHASE_EVENT '+json.dumps(event))
    def command_phase(self, phase, status):
        self.emit(phase,status,{'cycle':self.cycle,'boundary':'command-handler',
                                'operationRunId':self.run_id})
    def begin_purge(self):
        if not self.purge_open:
            self.cycle+=1;self.purge_open=True
            self.command_phase('purge-extrusion','start')
    def clear_completion(self, *unused):
        self.completion_token=None;self.completion_acknowledged=False

    def completion(self,eventtime):
        owner=self.printer.lookup_object('print_stats',None)
        stats=owner.get_status(eventtime) if owner is not None else {}
        if stats.get('state')!='complete' or self.native_preparation:
            self.completion_token=None
            return None
        if self.completion_token is None:self.completion_token=uuid.uuid4().hex
        return self.completion_token

    def acknowledge_completion(self,gcmd):
        # Checked atomically on the G-code reactor, never reset a newer job.
        token=gcmd.get('TOKEN','')
        expected=self.completion(self.printer.get_reactor().monotonic())
        sd=self.printer.lookup_object('virtual_sdcard')
        if not expected or token!=expected or sd.work_timer is not None:
            raise gcmd.error('A impressão concluída mudou ou ainda está em atividade.')
        self.gcode.run_script_from_command('SDCARD_RESET_FILE')
        self.completion_acknowledged=True
        self.native_preparation=False;self.piece_active=False;self.run_id=None
        self.active_phases=[];self.stack=[];self.phase='idle';self.purge_open=False
        self.native_phase=None;self.native_flush_slot=None
        with self.native_queue_lock:self.native_queue.clear()

    def get_status(self,eventtime):
        return {'completionAcknowledged':self.completion_acknowledged,'completionToken':self.completion(eventtime),'runId':self.run_id,'sequence':self.sequence,'phase':self.phase,'events':list(self.events),
                'filamentBalance':self.filament_balance.status() if getattr(self,'filament_balance',None) else None,
                'activePhase':next((p for p in reversed(getattr(self,'active_phases',[])) if p!='preparation'),
                    'preparation' if getattr(self,'native_preparation',False) or 'preparation' in getattr(self,'active_phases',[]) else 'markov'),
                'cancelRequested':getattr(self,'cancel_state','idle') in ('pending','accepted'),
                'cancelState':getattr(self,'cancel_state','idle'),
                'duplicateCancelRequests':getattr(self,'cancel_duplicates',0),
                'directMacroHooks':getattr(self,'direct_macro_hooks',[]),
                'nativeMethodHooks':getattr(self,'native_method_hooks',[]),
                'missingNativeHooks':getattr(self,'missing_native_hooks',[]),
                'activePhases':list(getattr(self,'active_phases',[])),
                'availableCommands':self.available_commands,'missingOptionalCommands':self.missing_optional,
                'nativePreparation':getattr(self,'native_preparation',False),
                'nativeLogBridge':{'attached':getattr(self,'native_log',None) is not None,
                    'matches':getattr(self,'native_log_matches',0),'error':getattr(self,'native_log_error',None)},
                **self.filament_status(eventtime)}
    @staticmethod
    def normalized_color(value):
        value=str(value or '').strip().lstrip('#')
        if len(value)==7 and value.startswith('0'):value=value[1:]
        return '#'+value.upper() if re.fullmatch(r'[0-9A-Fa-f]{6}',value) else None
    def filament_status(self,eventtime):
        try:
            owner=self.printer.lookup_object('box',None)
            box=owner.get_status(eventtime) if owner is not None else {}
            units={k:v for k,v in box.items() if re.fullmatch(r'T[1-4]',k) and isinstance(v,dict) and v.get('state')=='connect'}
            active=[k+v['filament'] for k,v in units.items() if v.get('filament') in ('A','B','C','D')]
            confirmed=getattr(self,'native_slot',None)
            slot=confirmed if getattr(self,'native_phase',None) in ('purge-positioning','purge-extrusion') and confirmed else active[0] if len(active)==1 else confirmed
            if units:
                if slot is None:return {'filamentColor':None,'filamentSlot':None}
                unit=units.get(slot[:2],{});index='ABCD'.find(slot[-1])
                colors=unit.get('color_value',[])
                color=self.normalized_color(colors[index]) if 0<=index<len(colors) else None
                if color:self.native_slot=slot
                return {'filamentColor':color,'filamentSlot':slot if color else None}
            rack=self.printer.lookup_object('filament_rack',None)
            data=rack.get_status(eventtime) if rack is not None else {}
            return {'filamentColor':self.normalized_color(data.get('color_value')),'filamentSlot':'external'}
        except Exception:return {'filamentColor':None,'filamentSlot':None}
    def shutdown_observation(self,*args):
        if self.native_log is not None:logging.getLogger().removeHandler(self.native_log)
        self.native_log=None
        self.emit('error','shutdown')
    def queue_native_signal(self,message):
        material=any(re.fullmatch(r'T[0-9]+',name) or name in self.ASSOCIATIONS or name in ('START_PRINT','END_PRINT') for name in self.stack)
        if not self.run_id or not (material or self.native_preparation or self.piece_active):return
        # Existing direct stage hooks win when the firmware actually calls them.
        generic={'tool-selection','filament-change','material-operation','purge-cycle',
                 'preparation','finalization','finalization-material-cycle','filament-retraction-cycle'}
        if any(f.get('phase') and f['phase'] not in generic for f in self.frames):return
        with self.native_queue_lock:
            if len(self.native_queue)>=64:
                self.native_log_error='Native material observation overflow';return
            self.native_queue.append((self.run_id,message))
            if self.native_scheduled:return
            self.native_scheduled=True
        try:self.printer.get_reactor().register_async_callback(lambda eventtime:self.drain_native_signals())
        except Exception:
            with self.native_queue_lock:self.native_scheduled=False
            self.native_log_error='Native observation callback unavailable'
    def set_native_phase(self,phase,reason):
        if self.native_phase==phase:return
        previous=self.native_phase;self.native_phase=None
        try:
            if previous:self.emit(previous,'end',{'source':'native-material-log','signal':reason})
            if phase:
                self.native_phase=phase
                self.emit(phase,'start',{'source':'native-material-log','signal':reason})
        except Exception:self.native_log_error='Native stage publication unavailable'
    def drain_native_signals(self):
        with self.native_queue_lock:
            records=list(self.native_queue);self.native_queue.clear();self.native_scheduled=False
        for run,message in records:
            if run!=self.run_id:continue
            self.native_log_matches+=1
            try:self.native_signal(message)
            except Exception:self.native_log_error='Native material signal rejected'
    def native_signal(self,message):
        if message=='[box] cut sensor detected':
            if not self.native_cut_seen:
                self.native_cut_seen=True;self.set_native_phase('filament-cutting','cut-sensor-detected')
        elif message=='[box] cut to return OK':
            if self.native_phase=='filament-cutting':self.set_native_phase(None,'cut-return-confirmed')
        elif message=='cmd: RETRUDE_PROCESS':
            self.native_purge_seen=False
            self.set_native_phase('filament-unloading','native-retrude-process')
        elif message in ('cmd: EXTRUDE_PROCESS','cmd: EXTRUDE_PROCESS_MODEL2'):
            if self.native_phase!='filament-feeding':
                self.native_cut_seen=False;self.native_purge_seen=False
            self.set_native_phase('filament-feeding','native-extrude-process')
        elif re.fullmatch(r'extrude_process_stage7 tnn: T[1-4][A-D]',message):
            self.native_slot=message.rsplit(' ',1)[1]
        elif message.startswith('extrude_process_stage7_ret: ') and "'ret': True" in message and "'extrude_process_ret_state': 'OK'" in message:
            if self.native_phase=='filament-feeding':self.set_native_phase(None,'native-feed-confirmed')
        elif re.fullmatch(r'flush; last_tnn: (?:T[1-4][A-D]|None), current_tnn: T[1-4][A-D]',message):
            self.native_flush_slot=message.rsplit(' ',1)[1]
            self.set_native_phase('purge-positioning','native-flush-target')
        elif message.startswith('extrude = '):
            match=re.match(r'extrude = ([0-9]+(?:\.[0-9]+)?),',message)
            slot=re.search(r',\s*tnn: (T[1-4][A-D])$',message)
            # The first load omits "flush; last_tnn". Accept its explicit
            # nozzle extrusion only inside a material operation and for the
            # slot already confirmed by the native feeder. Never use a random
            # positive E move or guess which bobbin is active.
            material=any(n in self.ASSOCIATIONS or re.fullmatch(r'T[0-9]+',n) for n in self.stack)
            target=self.native_flush_slot
            if target is None and material and slot and slot[1]==self.native_slot:
                target=slot[1]
            if (match and math.isfinite(float(match[1])) and float(match[1])>0 and target
                    and (slot is None or slot[1]==target)):
                self.native_flush_slot=target;self.native_purge_seen=True
                self.native_slot=self.native_flush_slot
                self.set_native_phase('purge-extrusion','native-positive-flush')
        elif message.startswith('last flush need retrude:') and self.native_phase=='purge-extrusion':
            self.set_native_phase(None,'native-flush-finished')
        elif message=='go to wipe_tower_y posison':
            if (self.native_purge_seen and self.native_flush_slot and
                    self.native_phase in (None,'purge-extrusion','purge-discard')):
                # Exact native action after an observed purge, not the bare
                # wipe_tower_y coordinate. This marks positioning for cleanup;
                # it does not claim sensor-confirmed physical waste release.
                self.set_native_phase('purge-discard','native-wipe-positioning-after-purge')
        elif message=='last_flush wipe_tower_y' and self.native_phase=='purge-discard':
            self.set_native_phase(None,'native-wipe-routine-return')
            self.native_purge_seen=False

    def observe_piece(self,eventtime):
        # A job enters "printing" before heating and homing. Require the native
        # SD layer counter AND deposited-print time after all handlers returned.
        # This timer observes state only: never flushes motion or sends G-code.
        if self.run_id and not self.stack and not self.native_preparation and not self.piece_active:
            sd=self.printer.lookup_object('virtual_sdcard',None)
            stats=self.printer.lookup_object('print_stats',None)
            if sd is not None and stats is not None:
                data=sd.get_status(eventtime);job=stats.get_status(eventtime)
                if (data.get('is_active') is True and job.get('state')=='printing'
                        and (data.get('layer') or 0)>0 and (job.get('print_duration') or 0)>0):
                    self.piece_active=True
                    self.emit('printing','start',{'boundary':'native-sd-layer-and-print-duration'})
        return eventtime+1
    def context(self):
        if 'END_PRINT' in self.stack:return 'finalization'
        changing=any(n in self.ASSOCIATIONS or re.fullmatch(r'T\d+',n) for n in self.stack)
        if changing and getattr(self,'piece_active',False):return 'filament-change'
        return 'printing' if getattr(self,'piece_active',False) else 'preparation'
    def classify(self, name, gcmd):
        if name=='START_PRINT':return 'preparation'
        if name=='END_PRINT':return 'finalization'
        if name=='CANCEL_PRINT':return 'cancelled'
        if name=='BOX_GO_TO_EXTRUDE_POS':
            # START_PRINT uses this for heating too. It signals this movement
            # animation only inside a material operation or an active piece.
            if getattr(self,'piece_active',False) or any(n in self.ASSOCIATIONS for n in self.stack):
                return 'purge-positioning'
            return None
        if name in self.ASSOCIATIONS:return self.ASSOCIATIONS[name]
        if re.fullmatch(r'T\d+',name):return 'tool-selection'
        material=any(n in self.ASSOCIATIONS or re.fullmatch(r'T\d+',n) for n in self.stack)
        if name in ('BOX_SET_TEMP','FILAMENT_RACK_SET_TEMP') and (self.run_id or material):return 'preheating'
        if name=='M109' and material:return 'preheating'
        if any(n in self.ASSOCIATIONS for n in self.stack):return None
        if 'START_PRINT' not in self.stack and not getattr(self,'native_preparation',False):return None
        if 'NOZ_CLEAR' in self.stack:return None
        if name=='NOZ_CLEAR':return 'nozzle-cleaning'
        if name=='G28':
            params=gcmd.get_command_parameters()
            return 'homing-z' if 'Z' in params or not any(a in params for a in ('X','Y','Z')) else 'homing-xy'
        if name=='M190':return 'bed-stabilization'
        if name=='M109':return 'cooling-for-calibration' if float(gcmd.get('S',gcmd.get('R',0)))==45 else 'preheating'
        if name=='BED_MESH_CALIBRATE':return 'calibration'
        if name=='CXSAVE_CONFIG':return 'calibration-save'
    def reset_cancel_guard(self):
        # Only an explicit NEW print re-arms cancellation. SD reset/idle and
        # macro acknowledgement can occur while stop movements are still queued.
        self.cancel_state='idle';self.cancel_retry_after=0.

    def protect_cancel(self, handler):
        def cancel(gcmd):
            state=getattr(self,'cancel_state','idle')
            if state in ('pending','accepted') or time.monotonic()<getattr(self,'cancel_retry_after',0.):
                self.cancel_duplicates=getattr(self,'cancel_duplicates',0)+1
                return
            # Set before invoking firmware: the reactor may yield/reenter here.
            self.cancel_state='pending'
            try:result=handler(gcmd)
            except BaseException:
                # Preserve the firmware error. No automatic retry, and no queued
                # click storm after a partial failure; a later manual retry works.
                self.cancel_state='failed';self.cancel_retry_after=time.monotonic()+2.
                raise
            self.cancel_state='accepted'
            return result
        return cancel

    def wrap(self, name, original):
        def handler(gcmd):
            # Stock Creality Print uses PRINT_TEMP_SET before G28/cleaning/mesh;
            # other paths use PRINT_PREPARED before virtual SD becomes active.
            # These are explicit firmware commands, never inferred
            # from heater targets or manual axis movement.
            native=getattr(self,'native_preparation',False)
            if name in self.NATIVE_PREPARATION_COMMANDS and not self.stack and not self.piece_active:
                if not native:
                    self.reset_cancel_guard()
                    self.run_id=uuid.uuid4().hex;self.piece_active=False
                    self.native_preparation=True;self.emit('preparation','start')
                try:return original(gcmd)
                except BaseException:
                    self.native_preparation=False;self.emit('preparation','error');raise
            if native and name in ('START_PRINT','END_PRINT','CANCEL_PRINT'):
                self.native_preparation=False;self.emit('preparation','end')
            if (self.run_id is None and name in self.ASSOCIATIONS) or (name in ('LOAD_MATERIAL','QUIT_MATERIAL','LOAD_MULTICOLOR_MATERIAL') and not self.piece_active and not self.stack):
                self.run_id=uuid.uuid4().hex;self.purge_open=False;self.cycle=0
            if name=='START_PRINT':
                if not self.stack:self.reset_cancel_guard()
                if not native:self.run_id=uuid.uuid4().hex
                if not native:self.active_phases=[]
                self.piece_active=False
            if name in ('END_PRINT','CANCEL_PRINT') and self.piece_active:
                self.emit('printing','end');self.piece_active=False
            material_boundary=re.fullmatch(r'T[0-9]+',name) or (name in self.ASSOCIATIONS and not self.stack)
            if material_boundary and hasattr(self,'native_queue_lock'):
                self.drain_native_signals();self.set_native_phase(None,'material-operation-start')
                self.native_cut_seen=False;self.native_flush_slot=None;self.native_purge_seen=False
            phase=self.classify(name,gcmd)
            # These macros are containers; only actual positive E moves begin
            # a purge. Multiple E segments before one clean belong to one cycle.
            if phase in ('purge-extrusion','purge-discard'):phase=None
            discard_cycle=name=='BOX_NOZZLE_CLEAN'
            if discard_cycle:
                if self.native_phase in ('purge-extrusion','purge-discard'):
                    self.set_native_phase(None,'direct-clean-command')
                self.native_purge_seen=False
                if self.purge_open:self.command_phase('purge-extrusion','end')
                self.purge_open=False
                self.command_phase('purge-discard','start')
            elif name in self.EXTRUSION_MACROS:
                self.begin_purge()
            if self.run_id is None:return original(gcmd)
            if not hasattr(self,'frames'):self.frames=[]
            # Explicit handoff at the known macro's BOX_NOZZLE_CLEAN call.
            # Close extrusion before discard; do not replay it on macro return.
            if name=='BOX_NOZZLE_CLEAN':
                for frame in reversed(self.frames):
                    if frame['name'] in self.EXTRUSION_MACROS and frame['open']:
                        self.emit(frame['phase'],'end');frame['open']=False
            frame={'name':name,'phase':phase,'open':bool(phase)}
            self.stack.append(name)
            self.frames.append(frame)
            if phase:self.emit(phase,'start')
            try:
                result=original(gcmd)
            except BaseException:
                self.emit(phase or 'unknown','error');raise
            else:
                if discard_cycle:self.command_phase('purge-discard','end')
                # Native purge containers position the nozzle for each cycle.
                # Observe that command's return; never inspect or flush G0/G1.
                if name=='BOX_GO_TO_EXTRUDE_POS' and any(
                        self.ASSOCIATIONS.get(parent)=='purge-cycle'
                        for parent in self.stack[:-1]):
                    self.begin_purge()
                if name in self.FAN_COMMANDS and self.context()=='filament-change':
                    params=dict(gcmd.get_command_parameters())
                    if name!='SET_PIN' or str(params.get('PIN','')).lower().startswith('fan'):
                        # A requested fan output is evidence, not a discard
                        # trigger or proof of measured fan speed.
                        self.emit('fan-control','observation',{'parameters':params})
                if material_boundary and hasattr(self,'native_queue_lock'):
                    self.drain_native_signals();self.set_native_phase(None,'material-operation-return')
                    self.native_flush_slot=None;self.native_purge_seen=False
                if name in ('END_PRINT','CANCEL_PRINT') and hasattr(self,'native_queue_lock'):
                    self.drain_native_signals();self.set_native_phase(None,'job-return')
                    self.native_cut_seen=False;self.native_flush_slot=None
                if frame['open']:self.emit(phase,'end')
                if name in ('END_PRINT','CANCEL_PRINT'):
                    self.run_id=None;self.piece_active=False
            finally:
                self.frames.pop();self.stack.pop()
            return result
        return self.protect_cancel(handler) if name=='CANCEL_PRINT' else handler
    def marker(self,gcmd):
        phase=gcmd.get('PHASE');status=gcmd.get('STATUS')
        if phase not in ('purge-extrusion','bed-prime','printing') or status not in ('start','end'):raise gcmd.error('Invalid Aurora phase marker')
        if self.run_id is None:raise gcmd.error('No Aurora print execution')
        if phase=='printing' and status=='start' and getattr(self,'native_preparation',False):
            self.native_preparation=False;self.emit('preparation','end')
        if phase=='printing' and (status=='start')==self.piece_active:return
        self.emit(phase,status)
        if phase=='printing':self.piece_active=status=='start'
    def install(self):
        from .aurora_filament_balance import Balance
        self.filament_balance=Balance(self)
        self.filament_balance.install()
        self.gcode.register_command('AURORA_ACK_PRINT',self.acknowledge_completion)
        required=set(self.COMMANDS)-{'AURORA_PHASE'}
        missing=required-set(self.gcode.ready_gcode_handlers)
        if missing:raise RuntimeError('Unsupported Aurora command contract: '+','.join(sorted(missing)))
        if 'AURORA_PHASE' in self.gcode.ready_gcode_handlers:raise RuntimeError('Aurora marker already registered')
        handlers=set(self.gcode.ready_gcode_handlers)
        optional=set(self.ASSOCIATIONS)|set(self.FAN_COMMANDS)|set(self.NATIVE_PREPARATION_COMMANDS)|{n for n in handlers if re.fullmatch(r'T\d+',n)}
        self.missing_optional=sorted((set(self.ASSOCIATIONS)|set(self.FAN_COMMANDS))-handlers)
        self.available_commands=sorted((required|optional)&handlers)
        for name in self.COMMANDS+tuple(sorted(optional&handlers)):
            if name=='AURORA_PHASE':
                self.gcode.register_command(name,self.marker);continue
            original=self.gcode.register_command(name,None)
            if original is not None:self.gcode.register_command(name,self.wrap(name,original))
        # Native material code can call GCodeMacro.cmd directly, bypassing the
        # registered dispatcher. Observe that existing entry too, exactly once.
        for name in sorted((required|optional)&handlers):
            macro=self.printer.lookup_object('gcode_macro '+name,None)
            if macro is None or not callable(getattr(macro,'cmd',None)):continue
            original=macro.cmd;observed=self.wrap(name,original)
            def direct(gcmd,n=name,call=original,wrapped=observed):
                return call(gcmd) if self.stack and self.stack[-1]==n else wrapped(gcmd)
            macro.cmd=direct;self.direct_macro_hooks.append(name)
        for object_name,methods in self.NATIVE_METHODS.items():
            owner=self.printer.lookup_object(object_name,None)
            for method,name in methods.items():
                original=getattr(owner,method,None) if owner is not None else None
                label=object_name+'.'+method
                if not callable(original):self.missing_native_hooks.append(label);continue
                def direct_native(gcmd,*args,_name=name,_call=original,**kwargs):
                    if self.stack and self.stack[-1]==_name:return _call(gcmd,*args,**kwargs)
                    return self.wrap(_name,lambda command:_call(command,*args,**kwargs))(gcmd)
                try:setattr(owner,method,direct_native)
                except (AttributeError,TypeError):self.missing_native_hooks.append(label)
                else:self.native_method_hooks.append(label)
        self.native_log=NativeMaterialLog(self);logging.getLogger().addHandler(self.native_log)
        reactor=self.printer.get_reactor()
        self.piece_timer=reactor.register_timer(self.observe_piece,reactor.monotonic()+1)

def load_config(config):return AuroraPhaseObserver(config)
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYING. This printer-integrated bridge remains available as source.
