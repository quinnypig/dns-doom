"""Native DNS framing/serialization, with the same authoritative-server boundary."""
import socket
import struct
from .bus import Bus
from . import _fastwire as wire


class NativeBus(Bus):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.socket.settimeout(None)
        self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVTIMEO,struct.pack('ll',10,0))
        self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_SNDTIMEO,struct.pack('ll',10,0))
        self.socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        self.round_trips=0

    def next_id(self):
        self.sequence=(self.sequence+1)&65535
        return self.sequence

    def exchange_many(self,requests,names,lenient=()):
        replies=wire.exchange(self.socket.fileno(),requests)
        self.round_trips+=1
        indexed={reply[:2]:reply for reply in replies}
        if len(indexed)!=len(requests):raise RuntimeError('Duplicate DNS response IDs')
        ordered=[]
        for index,(request,name) in enumerate(zip(requests,names)):
            reply=indexed.get(request[:2])
            if reply is None:raise RuntimeError('Unexpected DNS response ID')
            self.transactions+=1;self.payload_bytes+=len(request)+len(reply)
            flags=struct.unpack_from('!H',reply,2)[0]
            if flags&0x8000 and flags&15==3 and index in lenient:
                ordered.append(None);continue   # speculative fetch of a name that does not exist
            if not flags&0x8000 or flags&15:raise RuntimeError(f'DNS RCODE {flags&15} for {name}')
            if len(self.samples)<self.sample_limit:
                self.samples.append({'name':name,'request_bytes':len(request),'response_bytes':len(reply)})
            ordered.append(reply)
        return ordered

    def txt_many(self,names):
        result=[]
        for i in range(0,len(names),64):
            chunk=names[i:i+64]
            result.extend(wire.parse_txt(r) for r in self.exchange_many(
                [wire.query(self.next_id(),name) for name in chunk],chunk))
        return result

    def txt(self,name):return self.txt_many([name])[0]

    def commit(self,changes,previous_pc=None,prerequisite='pc.reg'):
        self.exchange_many([wire.update(self.next_id(),changes,previous_pc,prerequisite)],['UPDATE'])

    def commit_and_fetch(self,changes,previous,prerequisite,name):
        """One round trip: the UPDATE and the next block's immutable plan query."""
        update=wire.update(self.next_id(),changes,previous,prerequisite)
        query=wire.query(self.next_id(),name)
        replies=self.exchange_many([update,query],['UPDATE',name],lenient=(1,))
        return None if replies[1] is None else wire.parse_txt(replies[1])


def compile_native(raw):
    """Decode one plan text into the native structure. Wire names become indices; wire 0 is the state."""
    from .optimized import schedule
    plan,stages=schedule(raw)
    index={'_state':0}
    def wire_of(n):return index.setdefault(n,len(index))
    def part(p):return p if isinstance(p,str) else (wire_of(p[0]),-1 if len(p)==1 else p[1],-1 if len(p)==1 else p[2])
    out_stages=[([(wire_of(o),[part(p) for p in parts]) for _,o,parts in copies],
                 [(wire_of(o),[part(p) for p in parts]) for _,o,parts in queries]) for copies,queries in stages]
    writes=[([part(p) for p in n],[part(p) for p in v]) for n,v in plan['writes']]
    state_write=next(i for i,(n,_) in enumerate(plan['writes']) if n==['state.reg'])
    skips=[(wire_of(c),length) for c,_,length in plan.get('skips',())]
    return plan,wire.compile_plan(out_stages,writes,skips,len(index),state_write)


class NativeSequencer:
    """Runs packed block plans with the C++ wiring loop.

    Only wire shuffling moves to C++: slicing, concatenation, DNS message framing.
    Each plan text is decoded once; the plan itself is still fetched from DNS at
    every clock, and every arithmetic answer is a DNS response.
    """
    def __init__(self,bus):
        self.bus=bus;self.compiled={}

    def step(self,state,raw):
        entry=self.compiled.get(raw)
        if entry is None:entry=self.compiled[raw]=compile_native(raw)
        plan,capsule=entry
        bus=self.bus
        new_state,next_raw,skipped,transactions,payload,trips,bus.sequence,samples=wire.run(
            bus.socket.fileno(),capsule,state,bus.sequence,max(0,bus.sample_limit-len(bus.samples)))
        bus.transactions+=transactions;bus.payload_bytes+=payload;bus.round_trips+=trips
        bus.samples.extend({'name':n,'request_bytes':a,'response_bytes':b} for n,a,b in samples)
        return plan,new_state,next_raw,skipped
