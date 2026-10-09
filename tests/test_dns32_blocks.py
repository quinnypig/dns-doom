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

from dns32.blocks import compile_blocks

def image_of(instructions,base=0x1000):
    code={base+4*i:compile_instruction(base+4*i,ins,True) for i,ins in enumerate(instructions)}
    return {'entry':base,'code':code,'symbols':{},'memory':{}}

ADDI_R1=(1<<20)|(1<<15)|(1<<7)|0x13          # addi r1,r1,1
SW_R1=(1<<20)|(2<<15)|(2<<12)|0x23            # sw r1,0(r2)
LW_R3=(2<<15)|(2<<12)|(3<<7)|0x03             # lw r3,0(r2)
JAL=(1<<22)|0x6f                              # jal r0,+4 (a self-jump would make itself a leader)

class StoreBlockTests(unittest.TestCase):
    def test_store_continues_block_when_no_load_follows(self):
        image=image_of([ADDI_R1,SW_R1,ADDI_R1,ADDI_R1,JAL]);compile_blocks(image)
        self.assertEqual(image['code'][0x1000]['instructions'],5)
    def test_store_ends_block_before_a_later_load(self):
        image=image_of([ADDI_R1,SW_R1,ADDI_R1,LW_R3,JAL]);compile_blocks(image)
        self.assertEqual(image['code'][0x1000]['instructions'],2)
        self.assertEqual(image['code'][0x1008]['instructions'],3)
    def test_random_blocks_with_stores_match_oracle(self):
        rng=random.Random(4411)
        for case in range(300):
            plans=[];memory={};n=rng.randrange(2,12)
            for i in range(n):
                rd,rs1,rs2=[rng.randrange(32) for _ in range(3)]
                if rng.random()<0.3:
                    f=rng.choice([0,1,2]);imm=rng.randrange(0,64)*4
                    ins=((imm>>5)<<25)|(rs2<<20)|(rs1<<15)|(f<<12)|((imm&31)<<7)|0x23
                else:
                    f=rng.choice([0,2,3,4,6,7]);op=rng.choice([0x13,0x33])
                    ins=((rng.randrange(4096)<<20) if op==0x13 else (rs2<<20))|(rs1<<15)|(f<<12)|(rd<<7)|op
                pc=0x1000+i*4;plans.append(compile_instruction(pc,ins,True))
                memory.update({pc+j:v for j,v in enumerate(ins.to_bytes(4,'little'))})
            image={'entry':0x1000,'memory':memory}
            oracle=Reference(image);bus=PackedOracleBus(image,{0x1000:fuse(plans)})
            regs=[0]+[rng.randrange(0x2000,0x3000) for _ in range(31)];oracle.regs=regs.copy();bus.regs=regs.copy()
            for _ in plans:oracle.step()
            step(bus,pipeline=True,packed=True)
            self.assertEqual(bus.regs,oracle.regs);self.assertEqual(bus.pc,oracle.pc);self.assertEqual(bus.memory,oracle.memory)

BRANCHES={0:'BEQ',1:'BNE',4:'BLT',5:'BGE',6:'BLTU',7:'BGEU'}

def branch(f3,rs1,rs2,imm):
    return ((imm>>12)<<31)|(((imm>>5)&63)<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(((imm>>1)&15)<<8)|(((imm>>11)&1)<<7)|0x63

class HammockTests(unittest.TestCase):
    def test_branch_over_short_arm_is_fused_and_counted_per_path(self):
        rng=random.Random(2718);fused=0;taken=0
        for case in range(400):
            n=rng.randrange(1,7);ins=[branch(rng.choice(list(BRANCHES)),rng.randrange(4),rng.randrange(4),4*(n+1))]
            for i in range(n+1):   # n arm instructions plus one continuation instruction at the target
                rd,rs1,rs2=[rng.randrange(32) for _ in range(3)];f=rng.choice([0,2,3,4,6,7]);op=rng.choice([0x13,0x33])
                ins.append(((rng.randrange(4096)<<20) if op==0x13 else (rs2<<20))|(rs1<<15)|(f<<12)|(rd<<7)|op)
            ins.append(JAL)
            image=image_of(ins);memory={}
            for i,word_ in enumerate(ins):memory.update({0x1000+4*i+j:v for j,v in enumerate(word_.to_bytes(4,'little'))})
            image['memory']=memory
            compile_blocks(image)
            block=image['code'][0x1000]
            self.assertTrue(block.get('skips'),case);fused+=1
            oracle=Reference(image);bus=PackedOracleBus(image,image['code'])
            regs=[0]+[rng.choice([0,1,5,0xffffffff,0x80000000,rng.getrandbits(32)]) for _ in range(31)]
            oracle.regs=regs.copy();bus.regs=regs.copy()
            executed=0;end=0x1000+4*len(ins)
            while oracle.pc!=end:oracle.step();executed+=1
            taken+=executed<len(ins)
            step(bus,pipeline=True,packed=True)
            self.assertEqual(bus.pc,end);self.assertEqual(bus.regs,oracle.regs,case)
            self.assertEqual(bus.last_instructions,executed,case)
            self.assertEqual(len(bus.last_labels),executed,case)
        self.assertEqual(fused,400);self.assertGreater(taken,50);self.assertGreater(400-taken,50)
