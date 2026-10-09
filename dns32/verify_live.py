"""Live BIND differential checks for the RV32 instruction wiring."""
import json
from pathlib import Path

from .bus import Bus
from .compile import compile_instruction
from .reference import Reference
from .runtime import step
from .server import Server


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--server',default='bind');p.add_argument('--native',action='store_true');p.add_argument('--optimized',action='store_true');p.add_argument('--packed',action='store_true');p.add_argument('--connections',type=int,default=1);p.add_argument('--dns-threads',type=int,default=1);p.add_argument('--sequencer',default='python',choices=['python','native']);p.add_argument('--output',default='artifacts/dns32-live-tests.json');args=p.parse_args()
    if args.native:
        from .native import NativeBus
        bus_class=NativeBus
    else:bus_class=Bus
    if args.optimized:
        from .optimized import step as fast_step
        execute=lambda b:fast_step(b,pipeline=args.native,packed=args.packed)
    else:execute=step
    cases=[]
    for f in range(8):
        for a,b in [(0x80000001,31),(0xffffffff,1),(0x12345678,4)]:
            cases.append(((2<<20)|(1<<15)|(f<<12)|(3<<7)|0x33,a,b))
            imm=b&4095
            cases.append(((imm<<20)|(1<<15)|(f<<12)|(3<<7)|0x13,a,b))
    for f in (0,5):cases.append(((32<<25)|(2<<20)|(1<<15)|(f<<12)|(3<<7)|0x33,0x80000001,4))
    for f in (0,1,4,5,6,7):
        for a,b in [(0xffffffff,1),(1,0xffffffff),(0,0)]:
            cases.append(((2<<20)|(1<<15)|(f<<12)|(8<<7)|0x63,a,b))
    for f in (0,1,2,4,5):cases.append(((1<<15)|(f<<12)|(3<<7)|3,0x20001,0))
    for f in (0,1,2):cases.append(((2<<20)|(1<<15)|(f<<12)|0x23,0x20001,0x89abcdef))
    cases += [(0x123451b7,0,0),(0x12345197,0,0),(0x008001ef,0,0),((1<<15)|(3<<7)|0x67,0x12345679,0)]
    code={};memory={}
    for i,(ins,a,b) in enumerate(cases):
        pc=0x1000+i*4;code[pc]=compile_instruction(pc,ins,args.optimized)
        memory.update({pc+j:v for j,v in enumerate(ins.to_bytes(4,"little"))})
    image={"entry":0x1000,"memory":memory,"code":code}
    if args.packed:
        from .optimized import pack_plan,PackedRegisters
        image['packed']=True;image['code']={pc:pack_plan(v) for pc,v in code.items()}
    with Server(image,tables_path="artifacts/rv32/tables-v4.zone" if args.optimized else "artifacts/rv32/tables.zone",backend=args.server,threads=args.dns_threads):
        bus=bus_class(connections=args.connections) if args.native else bus_class()
        if args.packed:bus=PackedRegisters(bus)
        if args.sequencer=='native':
            from .native import NativeSequencer
            from .optimized import step_native
            sequencer=NativeSequencer(bus.bus)
            execute=lambda b:step_native(b,sequencer)
        for i,(ins,a,b) in enumerate(cases):
            pc=0x1000+i*4
            oracle=Reference({"entry":pc,"memory":memory})
            oracle.regs[1]=a;oracle.regs[2]=b
            for j,v in enumerate((0x80,0xff,0x7f,0xaa)):oracle.memory[0x20001+j]=v
            changes=[("pc.reg",f"{pc:08x}")]+[(f"r{r:02}.reg",f"{oracle.regs[r]:08x}") for r in range(32)]
            changes += [(f"{0x20001+j:08x}.mem",f"{v:02x}") for j,v in enumerate((0x80,0xff,0x7f,0xaa))]
            bus.commit(changes)
            oracle.step();execute(bus)
            actual=[int(bus.txt(f"r{r:02}.reg"),16) for r in range(32)]
            assert actual==oracle.regs,(i,f"{ins:08x}",actual,oracle.regs)
            assert int(bus.txt("pc.reg"),16)==oracle.pc,(i,"PC mismatch")
            for j in range(4):assert int(bus.txt(f"{0x20001+j:08x}.mem"),16)==oracle.memory[0x20001+j]
        # No fallback: corrupt the server's AND table and observe the wrong result.
        andpc=next(pc for pc,p in code.items() if p["label"]=="AND")
        bus.commit([("pc.reg",f"{andpc:08x}"),("r01.reg","ffffffff"),("r02.reg","ffffffff"),("ff.ff.and","00")])
        execute(bus)
        assert bus.txt("r03.reg")=="00000000"
        report={"live_instruction_cases":len(cases),"dns_transactions":bus.transactions,"fault_injection":"passed"}
        # Atomic stale-state rejection is essential for both memory layouts.
        try:bus.commit([('pc.reg','00000000')],'deadbeef')
        except RuntimeError:pass
        else:raise AssertionError('Stale update was accepted')
        report.update(server=args.server,native=args.native,optimized=args.optimized,packed=args.packed,sequencer=args.sequencer,stale_update='rejected')
        if args.packed:
            before=bus.txt('state.reg')
            bus.commit([('r31.reg','12345678')])
            try:bus.commit([('state.reg',before)],before,prerequisite='state.reg')
            except RuntimeError:pass
            else:raise AssertionError('DNS accepted a stale packed state with the same PC')
            report['packed_state_prerequisite']='DNS rejected stale registers with unchanged PC'
        Path(args.output).write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True);bus.close()


if __name__=="__main__":main()
