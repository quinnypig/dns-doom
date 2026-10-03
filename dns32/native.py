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

    def exchange_many(self,requests,names):
        replies=wire.exchange(self.socket.fileno(),requests)
        self.round_trips+=1
        indexed={reply[:2]:reply for reply in replies}
        if len(indexed)!=len(requests):raise RuntimeError('Duplicate DNS response IDs')
        ordered=[]
        for request,name in zip(requests,names):
            reply=indexed.get(request[:2])
            if reply is None:raise RuntimeError('Unexpected DNS response ID')
            self.transactions+=1;self.payload_bytes+=len(request)+len(reply)
            flags=struct.unpack_from('!H',reply,2)[0]
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
