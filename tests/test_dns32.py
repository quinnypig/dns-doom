"""Randomized differential verification against an independent RV32I interpreter."""
import json
import random
import unittest

from dns32.compile import compile_instruction
from dns32.reference import Reference
from dns32.runtime import step
from dns32.tables import lookup


class OracleBus(Reference):
    def __init__(self,image,plans):
        super().__init__(image)
        self.plans=plans

    def txt(self,name):
        if name.endswith(".code"):
            return json.dumps(self.plans[int(name[:-5],16)])
        if name.endswith((".reg",".mem")):
            return super().txt(name)
        return lookup(name)


class DNS32Tests(unittest.TestCase):
    OPTIMIZE=False
    BUS=OracleBus
    execute=staticmethod(step)
    def test_5000_random_instructions_against_independent_isa(self):
        rng=random.Random(0xd005)
        forms=[]
        for f in range(8):
            forms.append(("i",0x13,f,0))
            forms.append(("r",0x33,f,0))
        forms += [("i",0x13,5,32),("r",0x33,5,32),("r",0x33,0,32)]
        forms += [("i",3,f,0) for f in (0,1,2,4,5)]
        forms += [("s",0x23,f,0) for f in (0,1,2)]
        forms += [("b",0x63,f,0) for f in (0,1,4,5,6,7)]
        forms += [("u",0x37,0,0),("u",0x17,0,0),("j",0x6f,0,0),("i",0x67,0,0)]
        seen=set()
        for test in range(5000):
            kind,op,f,funct=forms[test%len(forms)]
            rd,rs1,rs2=[rng.randrange(32) for _ in range(3)]
            immediate=rng.randrange(4096)
            if kind=="r":ins=(funct<<25)|(rs2<<20)|(rs1<<15)|(f<<12)|(rd<<7)|op
            elif kind=="i":
                if op==0x13 and f in (1,5):immediate=(funct<<5)|rs2
                ins=(immediate<<20)|(rs1<<15)|(f<<12)|(rd<<7)|op
            elif kind in ("s","b"):
                ins=((immediate>>5)<<25)|(rs2<<20)|(rs1<<15)|(f<<12)|((immediate&31)<<7)|op
            else:ins=(rng.getrandbits(20)<<12)|(rd<<7)|op
            pc=0x1000
            memory={pc+i:b for i,b in enumerate(ins.to_bytes(4,"little"))}
            image={"entry":pc,"memory":memory}
            oracle=Reference(image)
            bus=self.BUS(image,{pc:compile_instruction(pc,ins,self.OPTIMIZE)})
            regs=[rng.choice([0,1,31,32,0xffffffff,0x80000000,0x7fffffff,rng.getrandbits(32)]) for _ in range(32)]
            regs[0]=0;oracle.regs=regs.copy();bus.regs=regs.copy()
            if op==3:
                imm=immediate if immediate<2048 else immediate-4096
                address=(regs[rs1]+imm)&0xffffffff
                for i in range(4):
                    value=rng.randrange(256)
                    oracle.memory[(address+i)&0xffffffff]=value
                    bus.memory[(address+i)&0xffffffff]=value
            oracle.step();_,label=self.execute(bus);seen.add(label)
            self.assertEqual(bus.regs,oracle.regs,(test,hex(ins),label))
            self.assertEqual(bus.pc,oracle.pc,(test,hex(ins),label))
            self.assertEqual(bus.memory,oracle.memory,(test,hex(ins),label))
        self.assertGreaterEqual(len(seen),36)

    def test_invalid_instruction_is_not_a_nop(self):
        for ins in (0xffffffff,0x00100073,0x02000033):
            with self.assertRaises(ValueError):compile_instruction(0x1000,ins)


if __name__=="__main__":unittest.main()
