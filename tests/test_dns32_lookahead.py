"""Truth tables and wiring depth for the lookahead arithmetic."""
import json
import unittest
from dns32.compile import compile_instruction
from dns32.optimized import schedule,pack_plan
from dns32.tables import lookup

def depth(plan):
    """Dependent DNS round trips for one packed plan: stages that contain queries."""
    return sum(1 for copies,queries in schedule(json.dumps(pack_plan(plan)))[1] if queries)

class LookaheadTables(unittest.TestCase):
    def test_addgp_subgp(self):
        for a in (0,1,127,128,254,255):
            for b in (0,1,127,128,254,255):
                r=lookup(f'{a:02x}.{b:02x}.addgp')
                self.assertEqual((int(r[0:2],16),int(r[2:4],16),r[4],r[5]),((a+b)&255,(a+b+1)&255,str(int(a+b>255)),str(int((a+b)&255==255))))
                r=lookup(f'{a:02x}.{b:02x}.subgp')
                self.assertEqual((int(r[0:2],16),int(r[2:4],16),r[4],r[5]),((a-b)&255,(a-b-1)&255,str(int(a<b)),str(int(a==b))))
    def test_cla(self):
        self.assertEqual(lookup('00000000.cla'),'0000')
        self.assertEqual(lookup('00000010.cla'),'0010')   # g0 → carry into byte1
        self.assertEqual(lookup('00000110.cla'),'0110')   # g0, p1 → carries into bytes 1 and 2
        self.assertEqual(lookup('01010110.cla'),'1110')   # ... and p2 propagates into byte 3
        self.assertEqual(lookup('10000000.cla'),'0000')   # g3 produces carry-out only, not carry-in
    def test_cmp4(self):
        self.assertEqual(lookup('eq.eq.eq.eq.cmp4'),'eq')
        self.assertEqual(lookup('eq.lt.gt.eq.cmp4'),'lt')
        self.assertEqual(lookup('gt.lt.lt.lt.cmp4'),'gt')
        self.assertEqual(lookup('eq.eq.eq.gt.cmp4'),'gt')

class WiringDepth(unittest.TestCase):
    def test_register_add_is_three_stages(self):
        plan=compile_instruction(0x1000,(2<<20)|(1<<15)|(3<<7)|0x33,True)   # ADD r3,r1,r2
        self.assertEqual(depth(plan),3)
    def test_branch_compare_is_four_stages(self):
        plan=compile_instruction(0x1000,(2<<20)|(1<<15)|(1<<12)|(8<<7)|0x63,True)   # BNE r1,r2,+8
        self.assertEqual(depth(plan),4)   # byte compares, cmp4, cond, mux
    def test_word_load_address_bytes_are_parallel(self):
        plan=compile_instruction(0x1000,(1<<15)|(2<<12)|(3<<7)|0x03,True)  # LW r3,0(r1): byte offsets 0..3 are fast constants
        self.assertEqual(depth(plan),3)   # add16 low, inc16 high, four parallel byte loads
