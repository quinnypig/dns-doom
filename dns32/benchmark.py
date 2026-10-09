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
from .optimized import invalidate

def device(out,saved):
    io=Devices(out);io.ms=saved['ms'];io.next_handle=saved['next_handle']
    for h,pos in saved['files'].items():
        f=io.wad.open('rb');f.seek(pos);io.files[int(h)]=f
    return io

def main():
    p=argparse.ArgumentParser();p.add_argument('--server',default='bind',choices=['bind','knot']);p.add_argument('--native',action='store_true');p.add_argument('--pipeline',action='store_true');p.add_argument('--optimize',action='store_true');p.add_argument('--packed',action='store_true');p.add_argument('--blocks',action='store_true');p.add_argument('--connections',type=int,default=1);p.add_argument('--dns-threads',type=int,default=1);p.add_argument('--sequencer',default='python',choices=['python','native']);p.add_argument('--steps',type=int,default=20000);p.add_argument('--repeat',type=int,default=3);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    image=load_elf('artifacts/tick-checkpoint/program.elf',optimize=a.optimize)
    saved=restore(image,'artifacts/tick-checkpoint')
    oracle=Reference(image);oracle.regs=image['registers'].copy()
    reference_io=device(out/'reference',saved['devices'])
    for _ in range(a.steps):oracle.step(reference_io)
    reference_io.close()
    # Every DNS memory record the prefix can change is one the reference changed too
    # (host DMA included); state.reg always changes. This set drives verification and restore.
    touched=sorted(f'{address:08x}.mem' for address,value in oracle.memory.items() if value!=image['memory'].get(address,0))
    if a.blocks:
        from .blocks import compile_blocks
        compile_blocks(image,extra_leaders=[oracle.pc])
    if a.packed:
        from .optimized import pack_plan
        image['packed']=True;image['code']={pc:pack_plan(v) for pc,v in image['code'].items()}
    with Server(image,tables_path='artifacts/rv32/tables-v4.zone',backend=a.server,log_path=out/'server.log',threads=a.dns_threads):
        if a.native:
            from .native import NativeBus
            raw=NativeBus(connections=a.connections)
        else:raw=Bus()
        bus=raw
        if a.packed:
            from .optimized import PackedRegisters
            bus=PackedRegisters(raw)
        if a.sequencer=='native':
            from .native import NativeSequencer
            from .optimized import step_native
            sequencer=NativeSequencer(raw)
            execute=lambda io:step_native(bus,sequencer,io)
        elif a.pipeline or a.optimize or a.packed:
            from .optimized import step as fast_step
            execute=lambda io:fast_step(bus,io,a.pipeline,a.packed)
        else:execute=lambda io:step(bus,io)
        results=[]
        for run in range(a.repeat):
            io=device(out/f'run-{run}',saved['devices'])
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
            for n in touched:assert int(raw.txt(n),16)==oracle.memory.get(int(n[:-4],16),0),f'Memory mismatch {n}'
            result['verified']=True;results.append(result);print(json.dumps(result),flush=True);io.close()
            changes=[(n,f"{image['memory'].get(int(n[:-4],16),0):02x}") for n in touched]
            if a.packed:changes.append(('state.reg',f"{image['entry']:08x}"+''.join(f'{r:08x}' for r in image['registers'])))
            else:changes+=[('pc.reg',f"{image['entry']:08x}")]+[(f'r{i:02}.reg',f"{image['registers'][i]:08x}") for i in range(32)]
            for i in range(0,len(changes),200):raw.commit(changes[i:i+200])
            invalidate(bus)
        raw.close()
    report={'configuration':vars(a),'runs':results,'wiring_revision':4,'median_seconds':statistics.median(r['seconds'] for r in results)}
    (out/'benchmark.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
