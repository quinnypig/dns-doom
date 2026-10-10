import argparse
from collections import Counter
import json
import shutil
from pathlib import Path
import time

from .bus import Bus
from .compile import load_elf
from .devices import Devices
from .runtime import step
from .server import Server


def main():
    parser=argparse.ArgumentParser(description="Run a compiled RV32I binary through DNS truth tables")
    parser.add_argument("elf")
    parser.add_argument("--steps",type=int,default=10000)
    parser.add_argument("--seconds",type=float,default=300)
    parser.add_argument("--output",default="artifacts/dns32")
    parser.add_argument("--tables")
    parser.add_argument("--checkpoint",help="Explicitly start from reference-origin initialized scene")
    parser.add_argument("--profile",action="store_true",help="Profile execution, excluding zone loading and output")
    parser.add_argument("--server",choices=("bind","knot"),default="bind")
    parser.add_argument("--client",choices=("python","native"),default="python")
    parser.add_argument("--pipeline",action="store_true")
    parser.add_argument("--optimize",action="store_true")
    parser.add_argument("--packed",action="store_true")
    parser.add_argument("--blocks",action="store_true",help="Fuse straight-line instructions between state commits")
    parser.add_argument("--connections",type=int,default=1,help="TCP connections the native sequencer spreads lookups over")
    parser.add_argument("--dns-threads",type=int,default=1,help="named worker threads")
    parser.add_argument("--transport",choices=("tcp","udp"),default="tcp",help="Transport for the native sequencer's lookups")
    parser.add_argument("--commit-every",type=int,default=1,help="Blocks registers may be carried as wires before a commit; stores and traps always commit")
    parser.add_argument("--sequencer",choices=("python","native"),default="python",help="Where the wiring loop runs; native requires --client native --packed --blocks")
    parser.add_argument("--preview",type=float,default=0,help="Seconds between DNS framebuffer previews (0 disables)")
    args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    for name in ("run.json","progress.json","failure.json","dns-view.png","dns-view.png.indices","console.txt"):
        (out/name).unlink(missing_ok=True)
    if (args.pipeline or args.packed) and args.client!='native':parser.error('Pipelining/packing requires --client native')
    if args.blocks and not (args.optimize and args.packed):parser.error('--blocks requires --optimize --packed')
    if args.sequencer=='native' and not (args.client=='native' and args.packed and args.blocks):parser.error('--sequencer native requires --client native --packed --blocks')
    args.tables=args.tables or ('artifacts/rv32/tables-v4.zone' if args.optimize else 'artifacts/rv32/tables.zone')
    image=load_elf(args.elf,optimize=args.optimize)
    if Path(args.elf).resolve()!=(out/"program.elf").resolve():
        shutil.copyfile(args.elf,out/"program.elf")
    checkpoint=None
    if args.checkpoint:
        from .checkpoint import restore
        checkpoint=restore(image,args.checkpoint)
        print("CHECKPOINT MODE: scene initialized outside DNS; subsequent instructions run through DNS",flush=True)
    if args.blocks:
        from .blocks import compile_blocks
        compile_blocks(image)
    if args.packed:
        from .optimized import pack_plan
        image['packed']=True
        image['code']={pc:pack_plan(p) for pc,p in image['code'].items()}
    (out/"symbols.json").write_text(json.dumps(image["symbols"],indent=2))
    print(f"ELF entry {image['entry']:08x}; {len(image['code']):,} code words; {len(image['memory']):,} nonzero initialized bytes",flush=True)
    io=Devices(out)
    if checkpoint and checkpoint.get("devices"):
        saved=checkpoint["devices"]
        io.ms=saved["ms"];io.next_handle=saved["next_handle"]
        for handle,pos in saved["files"].items():
            f=io.wad.open("rb");f.seek(pos);io.files[int(handle)]=f
    counts=Counter();milestones=[];status="limit";executed=0
    with Server(image,tables_path=args.tables,log_path=out/"named.log",backend=args.server,threads=args.dns_threads) as server:
        if args.client=="native":
            from .native import NativeBus
            bus=NativeBus(connections=args.connections,transport=args.transport)
        else:bus=Bus()
        if args.packed:
            from .optimized import PackedRegisters
            bus=PackedRegisters(bus)
        if args.sequencer=='native':
            from .native import NativeSequencer
            from .optimized import step_native
            sequencer=NativeSequencer(bus.bus)
            execute=lambda:step_native(bus,sequencer,io,args.commit_every)
        elif args.pipeline or args.optimize or args.packed:
            from .optimized import step as optimized_step
            execute=lambda:optimized_step(bus,io,args.pipeline,args.packed)
        else:execute=lambda:step(bus,io)
        start=time.monotonic();last=start;last_preview=start
        profiler=None
        if args.profile:
            import cProfile
            profiler=cProfile.Profile();profiler.enable()
        try:
            cycles=0
            for cycles in range(1,args.steps+1):
                pc,label=execute()
                executed+=getattr(bus,'last_instructions',1)
                counts.update(getattr(bus,'last_labels',[label]))
                now=time.monotonic()
                if now-last>=5:
                    event={"instructions":executed,"pc":pc,"elapsed":round(now-start,2),"dns_transactions":bus.transactions}
                    milestones.append(event)
                    (out/"progress.json").write_text(json.dumps(event))
                    print(json.dumps(event),flush=True);last=now
                if checkpoint and args.preview and now-last_preview>=args.preview:
                    from .checkpoint import capture_view
                    capture_view(bus,image['symbols'],io,out/'preview-next.png')
                    (out/'preview-next.png').replace(out/'preview.png')
                    last_preview=time.monotonic()
                if io.exit_code is not None:
                    status="exited";break
                current_pc=(bus.last_pc if args.pipeline or args.optimize or args.packed else bus.txt("pc.reg")) if checkpoint else None
                if checkpoint and current_pc==f"{checkpoint['return_pc']:08x}":
                    status="checkpoint-returned";break
                if now-start>=args.seconds:break
                if executed>=args.steps:break
            elapsed=time.monotonic()-start
            if profiler:
                profiler.disable();profiler.dump_stats(str(out/"execution.prof"))
            if args.sequencer=='native':
                from .optimized import flush
                flush(bus)
            regs={f"r{i:02}":bus.txt(f"r{i:02}.reg") for i in range(32)}
            pc=bus.txt("pc.reg")
            extra={}
            if checkpoint and status=="checkpoint-returned":
                from .checkpoint import capture_view
                extra["view"]=capture_view(bus,image["symbols"],io,out/"dns-view.png")
                actual=(out/"dns-view.png.indices").read_bytes()
                expected=Path(args.checkpoint,"reference-view.png.indices").read_bytes()
                extra["reference_pixels_match"]=actual==expected
                extra["engine_before"]=checkpoint["engine_before"]
                extra["engine_after"]={name:int.from_bytes(io.read(bus,image["symbols"][name],4),"little")
                                       for name in ("gametic","maketic","viewangle")}
                import hashlib
                extra["pixels_sha256"]=hashlib.sha256(actual).hexdigest()
                extra["checkpoint_symbol"]=checkpoint["symbol"]
                expected_state=Path(args.checkpoint,'expected-state.json')
                if expected_state.exists():
                    oracle=json.loads(expected_state.read_text())
                    extra['reference_registers_match']=regs==oracle['registers']
                    extra['reference_game_state_match']=extra['engine_after']==oracle['engine_after']
                    extra['reference_pc_match']=pc==oracle['pc']
                if not all(v for k,v in extra.items() if k.startswith('reference_')):
                    status='verification-failed'
            if "results" in image["symbols"]:
                raw=io.read(bus,image["symbols"]["results"],48)
                extra["results"]=[int.from_bytes(raw[i:i+4],"little") for i in range(0,48,4)]
            report={"backend":"DNS / stock "+args.server,"elf":args.elf,"status":status,"exit_code":io.exit_code,
                    "frames":io.frames,"instructions":executed,"elapsed":elapsed,"pc":pc,"registers":regs,
                    "dns_transactions":bus.transactions,"payload_bytes":bus.payload_bytes,"instruction_counts":dict(counts),
                    "milestones":milestones,"sample_dns_requests":bus.samples,"messages":io.messages,**extra}
            report["checkpoint_origin"]=checkpoint["origin"] if checkpoint else None
            report["elf_sha256"]=image["sha256"]
            report["client"]=args.client
            report["optimization"]={"pipeline":args.pipeline,"packed":args.packed,"wiring":args.optimize,"blocks":args.blocks,"sequencer":args.sequencer,"connections":args.connections,"dns_threads":args.dns_threads,"transport":args.transport,"commit_every":args.commit_every}
            report['wiring_revision']=4 if args.optimize else 1
            report["clock_cycles"]=cycles
            report["preview_interval"]=args.preview
            report["network_batches"]=getattr(bus,'round_trips',bus.transactions)
            (out/"run.json").write_text(json.dumps(report,indent=2))
            if checkpoint:
                from .report import write
                write(out)
            print(json.dumps({k:v for k,v in report.items() if k not in ("registers","sample_dns_requests","milestones","messages")},indent=2),flush=True)
        except Exception as error:
            (out/"failure.json").write_text(json.dumps({"error":repr(error),"instructions_attempted":executed,
                "elapsed":time.monotonic()-start,"dns_transactions":bus.transactions},indent=2))
            raise
        finally:
            bus.close();io.close()
    # Keep the server log; the container's live zone and journal are transient.
    print(f"Report: {out/'run.json'}; transient BIND state: {server.root}",flush=True)
    if status=='verification-failed':raise SystemExit('DNS result differs from independent reference')


if __name__=="__main__":main()
