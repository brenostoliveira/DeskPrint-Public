# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Breno
"""Local leased manual motion; no HTTP/IPC round trip per millimetre."""
import json,math,os,socket,time
from pathlib import Path

class ManualJog:
    def __init__(self,config):
        self.printer=config.get_printer();self.reactor=self.printer.get_reactor()
        self.path=config.get('socket_path','/tmp/aurora-manual-jog.sock')
        self.allowed=config.get('allowed_client','/mnt/UDISK/creality/userdata/theme-studio/aurora-functional-v1/aurora_menu.py')
        self.hold=None;self.intents=[];self.projected=None;self.error=None;self.speed=0;self.last_publish=0;self.owner=None
        self.socket=None;self.fd=None;self.timer=None;self.stopping=False;self.saved_flush=None
        self.saved_low=None;self.expected_position=None;self.stop_reason=None;self.lease_expirations=0;self.horizon=0
        self.printer.register_event_handler('klippy:ready',self.ready)
        self.printer.register_event_handler('klippy:shutdown',self.shutdown)
        self.printer.register_event_handler('klippy:disconnect',self.shutdown)

    def ready(self):
        self.tool=self.printer.lookup_object('toolhead');self.gcode=self.printer.lookup_object('gcode')
        self.stats=self.printer.lookup_object('print_stats')
        Path(self.path).unlink(missing_ok=True)
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);self.socket.setblocking(False)
        self.socket.bind(self.path);os.chmod(self.path,0o600)
        self.fd=self.reactor.register_fd(self.socket.fileno(),self.receive)
        self.timer=self.reactor.register_timer(self.tick,self.reactor.monotonic()+.01)
        self.publish(self.reactor.monotonic(),True)

    def shutdown(self,*args):
        self.hold=None;self.intents=[];self.restore_planner()
        if self.fd is not None:self.reactor.unregister_fd(self.fd);self.fd=None
        if self.timer is not None:self.reactor.unregister_timer(self.timer);self.timer=None
        if self.socket is not None:self.socket.close();self.socket=None
        Path(self.path).unlink(missing_ok=True)
        self.publish(self.reactor.monotonic(),True)

    def geometry(self,eventtime):return self.tool.get_status(eventtime)

    def permitted(self,pid):
        try:return self.allowed.encode() in Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(bytes([0]))
        except OSError:return False

    def receive(self,eventtime):
        for _ in range(30):
            try:data=self.socket.recv(2048)
            except BlockingIOError:break
            try:
                item=json.loads(data);pid=int(item['pid'])
                # UI timestamps use Python's CLOCK_MONOTONIC. The native
                # Klipper reactor may use CLOCK_MONOTONIC_RAW: NTP clock
                # correction makes the two diverge over uptime. Validate the
                # lease in its sender's clock, then schedule in reactor time.
                stamp=float(item['time'])
                if not self.permitted(pid) or not math.isfinite(stamp) or abs(time.monotonic()-stamp)>.3:continue
                now=self.reactor.monotonic()
                action=item['action']
                if self.owner not in (None,pid):continue
                if action=='stop':self.hold=None;continue
                if action=='cancel':
                    self.stopping=bool(self.hold or self.intents or self.saved_flush is not None)
                    self.hold=None;self.intents=[];continue
                if self.stats.get_status(eventtime)['state'] not in ('standby','complete','cancelled'):continue
                axis=item['axis'].lower();sign=int(item['sign']);seconds=float(item['seconds'])
                if axis not in 'xyz' or len(axis)!=1 or sign not in (-1,1) or not 5<=seconds<=20:continue
                geom=self.geometry(eventtime)
                if axis not in geom['homed_axes']:continue
                i='xyz'.index(axis);self.owner=pid;self.error=None
                if action=='hold':
                    self.hold={'axis':i,'sign':sign,'seconds':seconds,'until':now+.45}
                elif action=='step':
                    if len(self.intents)>=1000:continue
                    distance=float(item['distance'])
                    if not math.isfinite(distance) or not 0<distance<=30:continue
                    if self.projected is None or not self.intents:self.projected=list(self.tool.get_position())
                    remaining=geom['axis_maximum'][i]-self.projected[i] if sign>0 else self.projected[i]-geom['axis_minimum'][i]
                    distance=min(distance,max(0,remaining))
                    if distance<.001:continue
                    self.projected[i]+=sign*distance
                    self.intents.append({'axis':i,'sign':sign,'seconds':seconds,'target':self.projected[i]})
            except (KeyError,ValueError,TypeError,OverflowError):continue
        self.publish(eventtime,True)

    def rate(self,geom,axis,seconds,position):
        lower=geom['axis_minimum'][axis];upper=geom['axis_maximum'][axis];extent=upper-lower
        zone=min(15,extent*.05)
        equivalent=extent+2*zone*(2*math.log(2)-1)
        cap=self.tool.max_velocity
        if axis==2:cap=min(cap,self.tool.get_kinematics().max_z_velocity)
        base=min(cap,equivalent/seconds)
        edge=max(0,min(position-lower,upper-position))
        factor=.5+.5*min(1,edge/max(.001,zone))
        return max(.01,base*factor)

    def restore_planner(self):
        if self.saved_flush is not None:
            self.tool.move_queue.set_flush_time(self.saved_flush);self.saved_flush=None
        if self.saved_low is not None:
            self.tool.buffer_time_low=self.saved_low;self.saved_low=None

    def stop_planning(self):
        self.hold=None;self.intents=[];self.projected=None;self.speed=0;self.stopping=True;self.restore_planner()

    def tick(self,eventtime):
        try:
            if self.owner is not None and not self.permitted(self.owner):self.stop_planning()
            if self.hold and eventtime>self.hold['until']:
                self.hold=None;self.stop_reason='heartbeat expired';self.lease_expirations+=1
            if self.stats.get_status(eventtime)['state'] not in ('standby','complete','cancelled'):
                self.stop_planning();self.stopping=False;self.owner=None
                self.publish(eventtime);return eventtime+.1
            # Temporary mutex contention must not discard a live touch lease.
            # Do not plan until the handler completes; detect external motion below.
            if self.gcode.get_mutex().test():
                self.restore_planner()
                self.publish(eventtime);return eventtime+.01
            geom=self.geometry(eventtime)
            work=self.intents[0] if self.intents else self.hold
            if work is None:
                self.speed=0
                if self.stopping or self.saved_flush is not None:
                    # Flush only on release/end, never between motion segments.
                    self.tool.get_last_move_time();self.restore_planner();self.stopping=False
                if geom['print_time']<=geom['estimated_print_time']+.01:
                    self.owner=None;self.expected_position=None
                self.publish(eventtime);return eventtime+.01
            axis=work['axis']
            if 'xyz'[axis] not in geom['homed_axes']:
                self.stop_planning();self.publish(eventtime);return eventtime+.01
            if self.expected_position is not None and any(abs(a-b)>.00001 for a,b in zip(self.tool.get_position(),self.expected_position)):
                self.stop_reason='external motion';self.stop_planning();self.expected_position=None
                self.publish(eventtime);return eventtime+.01
            # Include unscheduled look-ahead as well as the MCU trajectory.
            queued=sum(m.min_move_t for m in self.tool.move_queue.queue)
            horizon=max(0,geom['print_time']-geom['estimated_print_time'])+queued
            self.horizon=horizon
            # The native startup buffer is 250ms: 120ms cannot sustain it.
            # Reserve at most 350ms plus one 25ms segment, including lookahead.
            if horizon>.35:self.publish(eventtime);return eventtime+.01
            pos=list(self.tool.get_position());sign=work['sign']
            rate=self.rate(geom,axis,work['seconds'],pos[axis]);self.speed=rate
            end=geom['axis_maximum'][axis] if sign>0 else geom['axis_minimum'][axis]
            if self.intents:end=work['target']
            remaining=(end-pos[axis])*sign
            if remaining<=.0001:
                if self.intents:self.intents.pop(0)
                else:self.hold=None
                self.stopping=not self.intents and self.hold is None
                self.publish(eventtime);return eventtime+.01
            delta=min(remaining,rate*.025)
            target=[None,None,None];target[axis]=pos[axis]+sign*delta
            # Keep short look-ahead while jogging, allowing adjacent segments
            # to blend; printing settings/firmware velocity limits are intact.
            if self.saved_flush is None:self.saved_flush=self.tool.move_queue.junction_flush
            if self.saved_low is None:self.saved_low=self.tool.buffer_time_low
            # Stock's 1s low-water timer otherwise force-flushes every small batch.
            self.tool.buffer_time_low=.05
            self.tool.move_queue.set_flush_time(min(self.tool.move_queue.junction_flush,.075))
            with self.gcode.get_mutex():self.tool.manual_move(target,rate)
            self.expected_position=list(self.tool.get_position());self.stop_reason=None
        except Exception as error:
            self.error=str(error)[:180];self.stop_planning()
        self.publish(eventtime);return eventtime+.01

    def get_status(self,eventtime):
        geom=self.geometry(eventtime) if hasattr(self,'tool') else {}
        times={}
        for i,axis in enumerate('xyz'):
            if not geom:break
            extent=geom['axis_maximum'][i]-geom['axis_minimum'][i];zone=min(15,extent*.05)
            cap=self.tool.max_velocity if i<2 else min(self.tool.max_velocity,self.tool.get_kinematics().max_z_velocity)
            times[axis]=max(5,(extent+2*zone*(2*math.log(2)-1))/cap)
        return {'version':1,'build':'continuous-jog-v5','requestClock':'python-monotonic','active':bool(self.hold or self.intents),'pending':len(self.intents),
            'speedMmPerSecond':self.speed,'minimumTraverseSeconds':times,'error':self.error,
            'position':geom.get('position',[]),'projected':self.projected,'homedAxes':geom.get('homed_axes',''),
            'maximumQueueSeconds':.35,'leaseSeconds':.45,'queueSeconds':self.horizon,
            'stopReason':self.stop_reason,'leaseExpirations':self.lease_expirations}

    def publish(self,eventtime,force=False):
        if not force and eventtime-self.last_publish<.1:return
        self.last_publish=eventtime
        try:
            path=Path('/tmp/aurora-manual-jog-status.json');temporary=path.with_suffix('.pending')
            temporary.write_text(json.dumps(self.get_status(eventtime)));temporary.replace(path)
        except Exception:pass

def load_config(config):return ManualJog(config)
