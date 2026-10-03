"""Sequential same-work benchmarks with independent state validation per repeat."""
import argparse,json,statistics,time
from pathlib import Path
from .compile import load_elf
from .checkpoint import restore
from .reference import Reference
from .server import Server
from .bus import Bus
from .devices import Devices
from .runtime import step

class Track:
    def __init__(self,bus):self.bus=bus;self.written=set()
    def __getattr__(self,name):return getattr(self.bus,name)
    def commit(self,changes,*args,**kwargs):
        self.written.update(n for n,v in changes)
        return self.bus.commit(changes,*args,**kwargs)

def device(out,saved):
    io=Devices(out);io.ms=saved['ms'];io.next_handle=saved['next_handle']
    for h,pos in saved['files'].items():
        f=io.wad.open('rb');f.seek(pos);io.files[int(h)]=f
    return io

def main():
    p=argparse.ArgumentParser();p.add_argument('--server',default='bind',choices=['bind','knot']);p.add_argument('--native',action='store_true');p.add_argument('--pipeline',action='store_true');p.add_argument('--optimize',action='store_true');p.add_argument('--packed',action='store_true');p.add_argument('--blocks',action='store_true');p.add_argument('--steps',type=int,default=20000);p.add_argument('--repeat',type=int,default=3);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    image=load_elf('artifacts/tick-checkpoint/program.elf',optimize=a.optimize)
    saved=restore(image,'artifacts/tick-checkpoint')
    oracle=Reference(image);oracle.regs=image['registers'].copy()
    reference_io=device(out/'reference',saved['devices'])
    for _ in range(a.steps):oracle.step(reference_io)
    reference_io.close()
    if a.blocks:
        from .blocks import compile_blocks
        compile_blocks(image,extra_leaders=[oracle.pc])
    if a.packed:
        from .optimized import pack_plan
        image['packed']=True;image['code']={pc:pack_plan(v) for pc,v in image['code'].items()}
    with Server(image,tables_path='artifacts/rv32/tables-v3.zone',backend=a.server,log_path=out/'server.log'):
        if a.native:
            from .native import NativeBus
            raw=NativeBus()
        else:raw=Bus()
        tracked=Track(raw);bus=tracked
        if a.packed:
            from .optimized import PackedRegisters
            bus=PackedRegisters(tracked)
        if a.pipeline or a.optimize or a.packed:
            from .optimized import step as fast_step
            execute=lambda io:fast_step(bus,io,a.pipeline,a.packed)
        else:execute=lambda io:step(bus,io)
        results=[]
        for run in range(a.repeat):
            io=device(out/f'run-{run}',saved['devices']);tracked.written.clear()
            before=raw.transactions;packets=getattr(raw,'round_trips',raw.transactions)
            start=time.perf_counter()
            executed=0
            while executed<a.steps:
                execute(io);executed+=getattr(bus,'last_instructions',1)
            assert executed==a.steps,'Benchmark boundary was crossed'
            elapsed=time.perf_counter()-start
            result={'seconds':elapsed,'instructions':a.steps,'dns_transactions':raw.transactions-before,'batches':getattr(raw,'round_trips',raw.transactions)-packets}
            assert [int(bus.txt(f'r{i:02}.reg'),16) for i in range(32)]==oracle.regs,'Register mismatch'
            assert int(bus.txt('pc.reg'),16)==oracle.pc,'PC mismatch'
            for n in tracked.written:
                if n.endswith('.mem'):assert int(raw.txt(n),16)==oracle.memory.get(int(n[:-4],16),0),f'Memory mismatch {n}'
            result['verified']=True;results.append(result);print(json.dumps(result),flush=True);io.close()
            changes=[]
            for n in tracked.written:
                if n.endswith('.mem'):v=f"{image['memory'].get(int(n[:-4],16),0):02x}"
                elif n=='state.reg':v=f"{image['entry']:08x}"+''.join(f'{r:08x}' for r in image['registers'])
                elif n=='pc.reg':v=f"{image['entry']:08x}"
                else:v=f"{image['registers'][int(n[1:3])]:08x}"
                changes.append((n,v))
            for i in range(0,len(changes),200):raw.commit(changes[i:i+200])
        raw.close()
    report={'configuration':vars(a),'runs':results,'wiring_revision':3,'median_seconds':statistics.median(r['seconds'] for r in results)}
    (out/'benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
