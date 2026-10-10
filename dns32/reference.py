"""Independent native-arithmetic RV32I oracle. ONLY for validation/profiling.

This never claims DNS execution and is never imported by the DNS runtime.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from .compile import load_elf
from .devices import Devices


def signed(v):return v-0x100000000 if v & 0x80000000 else v
def extend(v,bits):return v-(1<<bits) if v & (1<<(bits-1)) else v


class Reference:
    def __init__(self,image):
        self.memory=dict(image["memory"])
        self.regs=[0]*32
        self.pc=image["entry"]
        self.steps=0
        self.written=set()

    def read(self,address,size):
        return sum(self.memory.get((address+i)&0xffffffff,0)<<(8*i) for i in range(size))

    def txt(self,name):
        if name=="pc.reg":return f"{self.pc:08x}"
        if name.endswith(".reg"):return f"{self.regs[int(name[1:3])]:08x}"
        if name.endswith(".mem"):return f"{self.memory.get(int(name[:-4],16),0):02x}"
        raise ValueError(name)

    def commit(self,changes,previous_pc=None):
        for name,value in changes:
            if name=="pc.reg":self.pc=int(value,16)
            elif name.endswith(".reg"):
                index=int(name[1:3])
                if index:self.regs[index]=int(value,16)
            elif name.endswith(".mem"):self.memory[int(name[:-4],16)]=int(value,16);self.written.add(int(name[:-4],16))
            else:raise ValueError(name)

    def step(self,io=None):
        pc=self.pc
        ins=self.read(pc,4)
        op=ins&127;rd=(ins>>7)&31;f=(ins>>12)&7
        rs1=(ins>>15)&31;rs2=(ins>>20)&31;funct=ins>>25
        a=self.regs[rs1];b=self.regs[rs2]
        immediate=extend(ins>>20,12)
        next_pc=(pc+4)&0xffffffff;value=None;trap=False
        if op==0x37:value=ins&0xfffff000
        elif op==0x17:value=pc+(ins&0xfffff000)
        elif op==0x6f:
            off=extend(((ins>>31)<<20)|(((ins>>12)&255)<<12)|(((ins>>20)&1)<<11)|(((ins>>21)&1023)<<1),21)
            value=pc+4;next_pc=(pc+off)&0xffffffff
        elif op==0x67:
            value=pc+4;next_pc=((a+immediate)&0xffffffff)&~1
        elif op==0x63:
            off=extend(((ins>>31)<<12)|(((ins>>7)&1)<<11)|(((ins>>25)&63)<<5)|(((ins>>8)&15)<<1),13)
            yes={0:a==b,1:a!=b,4:signed(a)<signed(b),5:signed(a)>=signed(b),6:a<b,7:a>=b}[f]
            if yes:next_pc=(pc+off)&0xffffffff
        elif op==0x03:
            size={0:1,1:2,2:4,4:1,5:2}[f]
            value=self.read((a+immediate)&0xffffffff,size)
            if f in (0,1):value=extend(value,size*8)
        elif op==0x23:
            off=extend(((ins>>25)<<5)|((ins>>7)&31),12)
            address=(a+off)&0xffffffff
            for i in range(1<<f):self.memory[(address+i)&0xffffffff]=(b>>(i*8))&255
        elif op in (0x13,0x33):
            imm=op==0x13
            if imm:b=immediate&0xffffffff
            if f==0:value=a-b if not imm and funct==32 else a+b
            elif f==1:value=a<<(b&31)
            elif f==2:value=int(signed(a)<signed(b))
            elif f==3:value=int(a<b)
            elif f==4:value=a^b
            elif f==5:value=signed(a)>>(b&31) if funct==32 else a>>(b&31)
            elif f==6:value=a|b
            elif f==7:value=a&b
        elif ins==0x73:trap=True
        elif op==0x0f:pass
        else:raise ValueError(f"Reference invalid {ins:08x} at {pc:08x}")
        if rd and value is not None:self.regs[rd]=value&0xffffffff
        self.pc=next_pc;self.steps+=1
        if trap:
            if io is None:raise RuntimeError("Unhandled ECALL")
            io.trap(self)
        return pc,ins


def main():
    p=argparse.ArgumentParser(description="LOCAL reference only: no DNS computation")
    p.add_argument("elf");p.add_argument("--steps",type=int,default=100000000)
    p.add_argument("--output",default="artifacts/reference")
    p.add_argument("--checkpoint",help="Save state at this symbol, then stop when it returns")
    p.add_argument("--checkpoint-hit",type=int,default=1)
    args=p.parse_args();image=load_elf(args.elf,compile_code=False)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    for name in ("expected-state.json","checkpoint.json","memory.bin.gz","reference-view.png","reference-view.png.indices","run.json","console.txt"):
        (out/name).unlink(missing_ok=True)
    import shutil
    if Path(args.elf).resolve()!=(out/"program.elf").resolve():
        shutil.copyfile(args.elf,out/"program.elf")
    cpu=Reference(image);io=Devices(args.output)
    start=time.monotonic();last=start;counts=Counter();checkpoint=None;capture_step=0;hits=0;returned=False
    print("REFERENCE BACKEND — all arithmetic local; this is NOT the DNS result",flush=True)
    for i in range(args.steps):
        if args.checkpoint and checkpoint is None and cpu.pc==image["symbols"][args.checkpoint]:
            hits+=1
            if hits==args.checkpoint_hit:
                from .checkpoint import save
                # Clear only the viewport before saving, so a later successful
                # image cannot be a stale native-rendered framebuffer.
                s=image["symbols"]
                fb=cpu.read(s["screens"],4);vw=cpu.read(s["viewwidth"],4);vh=cpu.read(s["viewheight"],4)
                vx=cpu.read(s["viewwindowx"],4);vy=cpu.read(s["viewwindowy"],4)
                for row in range(vh):
                    for col in range(vw):cpu.memory[fb+(vy+row)*320+vx+col]=0
                checkpoint=save(cpu,image,args.output,args.checkpoint,io)
                capture_step=cpu.steps
                print(f"Saved reference-origin checkpoint at {args.checkpoint} after {cpu.steps:,} instructions",flush=True)
        pc,ins=cpu.step(io);counts[f"{pc:08x}"]+=1
        if checkpoint and cpu.pc==checkpoint["return_pc"]:
            from .checkpoint import capture_view
            view=capture_view(cpu,image["symbols"],io,Path(args.output)/"reference-view.png")
            returned=True
            print(f"Renderer returned after {cpu.steps-capture_step:,} instructions: {view}",flush=True)
            break
        if i%100000==0 and time.monotonic()-last>=5:
            print(f"Reference: {i:,} instructions, PC={pc:08x}, {time.monotonic()-start:.1f}s",flush=True)
            last=time.monotonic()
        if io.exit_code is not None:break
    result={"backend":"LOCAL REFERENCE (not DNS)","instructions":cpu.steps,"seconds":time.monotonic()-start,
            "exit_code":io.exit_code,"frames":io.frames,"pc":f"{cpu.pc:08x}","hot_pcs":counts.most_common(30),
            "renderer_instructions":cpu.steps-capture_step if checkpoint else None}
    Path(args.output,"run.json").write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    if checkpoint and returned:
        expected={'instructions':cpu.steps-capture_step,'pc':f'{cpu.pc:08x}',
                  'registers':{f'r{i:02}':f'{v:08x}' for i,v in enumerate(cpu.regs)},
                  'engine_after':{name:cpu.read(image['symbols'][name],4) for name in ('gametic','maketic','viewangle')}}
        (out/'expected-state.json').write_text(json.dumps(expected,indent=2))
    io.close()
    if args.checkpoint and not returned:
        raise RuntimeError("Requested checkpoint call did not complete; no usable reference result")


if __name__=="__main__":main()
