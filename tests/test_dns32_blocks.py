import random,unittest
from dns32.blocks import fuse
from dns32.compile import compile_instruction
from dns32.optimized import step
from dns32.reference import Reference
from test_dns32_optimized import PackedOracleBus

class BlockTests(unittest.TestCase):
    def test_1000_random_straight_line_blocks(self):
        rng=random.Random(8831)
        for case in range(1000):
            plans=[];memory={}
            for i in range(rng.randrange(2,17)):
                rd,rs1,rs2=[rng.randrange(32) for _ in range(3)]
                f=rng.choice([0,2,3,4,6,7]);op=rng.choice([0x13,0x33])
                ins=((rng.randrange(4096)<<20) if op==0x13 else (rs2<<20))|(rs1<<15)|(f<<12)|(rd<<7)|op
                pc=0x1000+i*4
                plans.append(compile_instruction(pc,ins,True))
                memory.update({pc+j:v for j,v in enumerate(ins.to_bytes(4,'little'))})
            image={'entry':0x1000,'memory':memory}
            oracle=Reference(image);bus=PackedOracleBus(image,{0x1000:fuse(plans)})
            regs=[0]+[rng.getrandbits(32) for _ in range(31)];oracle.regs=regs.copy();bus.regs=regs.copy()
            for _ in plans:oracle.step()
            step(bus,pipeline=True,packed=True)
            self.assertEqual(bus.regs,oracle.regs);self.assertEqual(bus.pc,oracle.pc)
