"""Truth tables and wiring depth for the shortened comparison and address wiring."""
import json
import unittest
from dns32.compile import compile_instruction
from dns32.optimized import schedule,pack_plan
from dns32.tables import lookup

def depth(plan):
    """Dependent DNS round trips for one packed plan: stages that contain queries."""
    return sum(1 for copies,queries in schedule(json.dumps(pack_plan(plan)))[1] if queries)

def queries(plan):return sum(1 for s in pack_plan(plan)['steps'] if s[0]=='q')

class ConditionTables(unittest.TestCase):
    def test_cond4(self):
        self.assertEqual(lookup('eq.eq.eq.eq.eq.cond4'),'1')
        self.assertEqual(lookup('eq.eq.eq.eq.ne.cond4'),'0')
        self.assertEqual(lookup('eq.lt.gt.eq.lt.cond4'),'1')
        self.assertEqual(lookup('gt.lt.lt.lt.ge.cond4'),'1')
        self.assertEqual(lookup('eq.eq.eq.gt.lt.cond4'),'0')

class WiringDepth(unittest.TestCase):
    def test_branch_is_three_stages(self):
        plan=compile_instruction(0x1000,(2<<20)|(1<<15)|(1<<12)|(8<<7)|0x63,True)   # BNE r1,r2,+8
        self.assertEqual(depth(plan),3)   # byte compares, cond4, target byte select
    def test_near_branch_selects_only_differing_target_bytes(self):
        plan=compile_instruction(0x1000,(2<<20)|(1<<15)|(1<<12)|(8<<7)|0x63,True)   # 0x1004 vs 0x1008 differ in one byte
        self.assertEqual(queries(plan),4+1+1)
    def test_word_load_address_bytes_are_parallel(self):
        plan=compile_instruction(0x1000,(1<<15)|(2<<12)|(3<<7)|0x03,True)  # LW r3,0(r1): byte offsets 0..3 are fast constants
        self.assertEqual(depth(plan),3)   # add16 low, inc16 high, four parallel byte loads
