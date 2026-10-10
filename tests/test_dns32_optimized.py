from test_dns32 import DNS32Tests,OracleBus
from dns32.optimized import pack_plan,step

class OptimizedDNS32Tests(DNS32Tests):
    OPTIMIZE=True

class PackedOracleBus(OracleBus):
    def __init__(self,image,plans):super().__init__(image,{pc:pack_plan(p) for pc,p in plans.items()})
    def txt(self,name):
        if name=='state.reg':return f'{self.pc:08x}'+''.join(f'{r:08x}' for r in self.regs)
        return super().txt(name)
    def txt_many(self,names):return [self.txt(n) for n in names]
    def commit(self,changes,previous_pc=None,prerequisite='pc.reg'):
        if previous_pc is not None:assert self.txt(prerequisite)==previous_pc
        for name,value in changes:
            if name=='state.reg':
                self.pc=int(value[:8],16)
                self.regs=[int(value[i:i+8],16) for i in range(8,264,8)]
            else:super().commit([(name,value)])

class PackedDNS32Tests(DNS32Tests):
    OPTIMIZE=True
    BUS=PackedOracleBus
    execute=staticmethod(lambda bus:step(bus,pipeline=True,packed=True))

del DNS32Tests

import unittest
from dns32.compile import compile_instruction
from dns32.optimized import invalidate

class CountingBus(PackedOracleBus):
    def __init__(self,*a):super().__init__(*a);self.state_reads=0;self.code_reads=0
    def txt(self,name):
        if name=='state.reg':self.state_reads+=1
        if name.endswith('.code'):self.code_reads+=1
        return super().txt(name)
    def commit(self,changes,previous_pc=None,prerequisite='pc.reg'):
        reads=self.state_reads;super().commit(changes,previous_pc,prerequisite);self.state_reads=reads   # the oracle's own prerequisite check is not a sequencer read
    def commit_and_fetch(self,changes,previous,prerequisite,name):
        self.commit(changes,previous,prerequisite)
        return self.txt(name) if int(name[:-5],16) in self.plans else None   # NXDOMAIN past the last plan

class PrefetchTests(unittest.TestCase):
    def test_state_and_plan_are_not_refetched_between_blocks(self):
        pc=0x1000;plans={}
        for i in range(3):  # three ADDI r1,r1,1
            ins=(1<<20)|(1<<15)|(1<<7)|0x13
            plans[pc+4*i]=compile_instruction(pc+4*i,ins,True)
        bus=CountingBus({'entry':pc,'memory':{}},plans)
        for _ in range(3):step(bus,pipeline=True,packed=True)
        self.assertEqual(bus.regs[1],3)
        self.assertEqual(bus.state_reads,1)      # only the very first block reads state from DNS
        self.assertEqual(bus.code_reads,3)       # every plan is still fetched from DNS, once per block
        invalidate(bus)
        self.assertIsNone(getattr(bus,'cached_state',None))
