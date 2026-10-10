"""Offline checks of the native wiring loop: plan validation and transport trust."""
import json,socket,threading,unittest
from dns32.compile import compile_instruction
from dns32.optimized import pack_plan
from dns32.tables import lookup
try:
    from dns32 import _fastwire as wire
    from dns32.native import compile_native
except ImportError:wire=None

def question(packet):return packet[12:]

def encode_txt(reply_to,text,rcode=0,question_override=None):
    ident=reply_to[:2];q=question_override if question_override is not None else question(reply_to)
    header=ident+bytes([0x84,rcode])+b'\x00\x01\x00\x01\x00\x00\x00\x00'
    name=b'\xc0\x0c';data=bytes([len(text)])+text.encode()
    return header+q+name+b'\x00\x10\x00\x01\x00\x00\x00\x00'+len(data).to_bytes(2,'big')+data

class FakeServer:
    """Answers from the truth tables, with hooks to misbehave."""
    def __init__(self):
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.udp.bind(('127.0.0.1',0))
        self.tcp=socket.socket(socket.AF_INET,socket.SOCK_STREAM);self.tcp.bind(('127.0.0.1',0));self.tcp.listen(1)
        self.wrong_question=False;self.plans={};self.state=None;self.stop=False
        threading.Thread(target=self.serve_udp,daemon=True).start();threading.Thread(target=self.serve_tcp,daemon=True).start()
    def name_of(self,packet):
        labels=[];p=12
        while packet[p]:n=packet[p];labels.append(packet[p+1:p+1+n].decode());p+=n+1
        return '.'.join(labels[:-2])
    def answer(self,packet):
        name=self.name_of(packet)
        if name.endswith('.code'):text=json.dumps(self.plans[int(name[:-5],16)])
        elif name=='state.reg':text=self.state
        else:text=lookup(name)
        if self.wrong_question:return encode_txt(packet,text,question_override=b'\x04evil\x02rv\x04test\x00\x00\x10\x00\x01')
        return encode_txt(packet,text)
    def serve_udp(self):
        while not self.stop:
            try:packet,addr=self.udp.recvfrom(4096)
            except OSError:return
            self.udp.sendto(self.answer(packet),addr)
    def serve_tcp(self):
        conn,_=self.tcp.accept()
        while not self.stop:
            head=conn.recv(2)
            if len(head)<2:return
            packet=conn.recv(int.from_bytes(head,'big'))
            opcode=(packet[2]>>3)&15
            if opcode==5:   # UPDATE: accept, record the new state
                self.state=None;reply=packet[:2]+bytes([0xa8,0])+packet[4:12]
                # locate the last TXT in the update section and treat it as the new state
                reply=reply[:12]
                conn.sendall(len(reply).to_bytes(2,'big')+reply)
            else:
                reply=self.answer(packet);conn.sendall(len(reply).to_bytes(2,'big')+reply)

@unittest.skipIf(wire is None,'native extension not built')
class NativeValidation(unittest.TestCase):
    def test_compile_plan_rejects_out_of_range_write_wires(self):
        with self.assertRaises(ValueError):wire.compile_plan([],[((0,-1,-1),[(2000000,-1,-1)])],[],1,0)
        with self.assertRaises(ValueError):wire.compile_plan([],[((0,-1,-1),[(0,-1,-1)])],[(7,1)],1,0)
        with self.assertRaises(ValueError):wire.compile_plan([],[((0,-1,-1),[(0,-1,-1)])],[],0,0)

@unittest.skipIf(wire is None,'native extension not built')
class NativeTransportTrust(unittest.TestCase):
    def test_reply_with_different_question_is_rejected(self):
        server=FakeServer()
        pc=0x1000;plan=pack_plan(compile_instruction(pc,(2<<20)|(1<<15)|(3<<7)|0x33,True))   # add r3,r1,r2
        server.plans[pc]=plan
        state=f'{pc:08x}'+'00000000'+'00000011'+'00000011'+'00000000'*29
        _,capsule=compile_native(json.dumps(plan))
        tcp=socket.create_connection(server.tcp.getsockname());udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.connect(server.udp.getsockname());udp.settimeout(1)
        try:
            server.wrong_question=True
            with self.assertRaisesRegex(RuntimeError,'timed out|does not match'):
                wire.run([tcp.fileno()],[udp.fileno()],capsule,state,state,True,1,0)
        finally:
            server.stop=True;tcp.close();udp.close();server.udp.close();server.tcp.close()
