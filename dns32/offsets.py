"""Histogram of constant add offsets executed by the reference emulator (offline tooling only).

Counts every constant the wiring adds to a register: ADDI immediates, load/store
immediates plus each byte index, and JALR offsets. Used to choose FAST_OFFSETS.
"""
import sys
from collections import Counter
from .compile import load_elf,sx,FAST_OFFSETS
from .checkpoint import restore
from .reference import Reference
from .benchmark import device

def main():
    image=load_elf('artifacts/tick-checkpoint/program.elf')
    saved=restore(image,'artifacts/tick-checkpoint')
    cpu=Reference(image);cpu.regs=image['registers'].copy()
    io=device('/tmp/offsets-io',saved['devices'])
    counts=Counter();limit=int(sys.argv[1]) if len(sys.argv)>1 else 1400000
    for _ in range(limit):
        ins=cpu.read(cpu.pc,4);op=ins&127;f=(ins>>12)&7
        if op==0x13 and f==0:counts[sx(ins>>20,12)]+=1
        elif op==0x67 and f==0:counts[sx(ins>>20,12)]+=1
        elif op==0x03:
            imm=sx(ins>>20,12)
            for k in range({0:1,1:2,2:4,4:1,5:2}[f]):counts[imm+k]+=1
        elif op==0x23:
            imm=sx(((ins>>25)<<5)|((ins>>7)&31),12)
            for k in range(1<<f):counts[imm+k]+=1
        cpu.step(io)
        if cpu.pc==saved['return_pc']:break
    counts.pop(0,None)
    total=sum(counts.values());running=0
    print(f'{total:,} constant adds over {cpu.steps:,} instructions; current FAST_OFFSETS cover '
          f'{100*sum(counts[o] for o in FAST_OFFSETS)/total:.1f}%')
    chosen=[]
    for offset,n in counts.most_common(96):
        running+=n;chosen.append(offset)
        print(f'{offset:6d} {n:9d} {100*running/total:6.2f}%')
    print('sorted top 64:',sorted(chosen[:64]))

if __name__=='__main__':main()
