"""Transport microbenchmark: per-query and per-round-trip cost against the live zone (tooling only)."""
import random,socket,struct,sys,time
from .compile import load_elf
from .server import Server
from .native import NativeBus
from . import _fastwire as wire

def main():
    threads=int(sys.argv[1]) if len(sys.argv)>1 else 1
    image=load_elf('artifacts/rv32/fibonacci.elf',optimize=True);image['packed']=True
    rng=random.Random(1)
    names=[f'{rng.randrange(256):02x}.{rng.randrange(256):02x}.{rng.randrange(2)}.add' for _ in range(20000)]
    tables=sys.argv[2] if len(sys.argv)>2 else 'artifacts/rv32/tables-v4.zone'
    with Server(image,tables_path=tables,threads=threads):
        bus=NativeBus()
        for label,batch in ((('tcp single',1),('tcp batch 8',8),('tcp batch 64',64)) if 'empty' not in tables else ()):
            t=time.perf_counter()
            for i in range(0,len(names),batch):bus.txt_many(names[i:i+batch])
            e=time.perf_counter()-t
            print(f'{label:14s} {e:.3f}s  {1e6*e/len(names):6.2f} us/query  {1e6*e/(len(names)/batch):7.2f} us/trip',flush=True)
        udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.connect(('127.0.0.1',15354));udp.settimeout(5)
        for label,batch in ((('udp single',1),('udp batch 8',8),('udp batch 64',64)) if 'empty' not in tables else ()):
            t=time.perf_counter()
            for i in range(0,len(names),batch):
                chunk=names[i:i+batch]
                for j,n in enumerate(chunk):udp.send(wire.query(j+1,n))
                got=0
                while got<len(chunk):
                    reply=udp.recv(4096);got+=1
                    assert wire.parse_txt(reply)
            e=time.perf_counter()-t
            print(f'{label:14s} {e:.3f}s  {1e6*e/len(names):6.2f} us/query  {1e6*e/(len(names)/batch):7.2f} us/trip',flush=True)
        state=bus.txt('state.reg')
        t=time.perf_counter()
        for i in range(4000):
            new=f'{i:08x}'+state[8:]
            bus.commit_and_fetch([(f'{0x20000+i:08x}.mem',f'{i&255:02x}'),('state.reg',new)],state,'state.reg','00000000.code');state=new
        e=time.perf_counter()-t
        print(f'update+fetch   {e:.3f}s  {1e6*e/4000:6.2f} us/commit',flush=True)
        t=time.perf_counter()
        for i in range(4000):
            new=f'{i+4000:08x}'+state[8:]
            bus.commit([(f'{0x20000+i:08x}.mem',f'{i&255:02x}'),('state.reg',new)],state,prerequisite='state.reg');state=new
        e=time.perf_counter()-t
        print(f'update only    {e:.3f}s  {1e6*e/4000:6.2f} us/commit',flush=True)
        socks=[socket.socket(socket.AF_INET,socket.SOCK_DGRAM) for _ in range(threads)]
        for u in socks:u.connect(('127.0.0.1',15354));u.settimeout(5)
        t=time.perf_counter()
        for i in range(0,len(names),64):
            chunk=names[i:i+64];lanes=len(socks);each=(len(chunk)+lanes-1)//lanes
            for lane,u in enumerate(socks):
                for j,n in enumerate(chunk[lane*each:(lane+1)*each]):u.send(wire.query(j+1,n))
            for lane,u in enumerate(socks):
                for _ in chunk[lane*each:(lane+1)*each]:wire.parse_txt(u.recv(4096))
        e=time.perf_counter()-t
        print(f'udp 64 x{threads:<4d}   {e:.3f}s  {1e6*e/len(names):6.2f} us/query',flush=True)
        bus.close()

if __name__=='__main__':main()
