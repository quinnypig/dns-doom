# Depth-to-Width Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the verified Doom tick (1,389,327 instructions, currently 146 s) by a large factor by reducing the number of dependent DNS round trips per instruction, without adding any local arithmetic.

**Architecture:** The machine is latency-bound: 5.06 M round trips at ~29 µs with only 1.6 queries per trip. Every change below converts dependency depth into batch width. Correctness is held by the existing differential oracle (`tests/test_dns32*.py`, `OracleBus` answers truth-table names via `tables.lookup`) and the live checks (`dns32.verify_live`, `./run-rv32.sh fast`). The purity rule stays: no local ALU, no caching of DNS arithmetic or memory answers, every plan fetched from DNS every clock. Locally reusing a value we just wrote ourselves (the committed state) is wiring, not computation, and the RFC 2136 prerequisite still proves consistency on every commit.

**Tech Stack:** Python 3.14, C++17 CPython extension (`dns32/fastwire.cpp`), stock BIND 9 in Docker (`dns32:local`), unittest.

**Working copy:** `~/.config/superpowers/worktrees/dns-doom-perf`, branch `perf/depth-to-width`. Pushes to `main` auto-deploy, so all work stays on the branch until the full tick verifies.

**Measurement harness (used after every task):**

```sh
cd ~/.config/superpowers/worktrees/dns-doom-perf
./run-rv32.sh fast            # first time only: builds images, tables, checkpoint, runs the tick
docker run --rm --network none --shm-size 4g --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD,dst=/work" --entrypoint python dns32:local \
  -m dns32.benchmark --native --pipeline --optimize --packed --blocks \
  --steps 20000 --repeat 3 --output artifacts/performance/<stage>
```

Baseline to beat for the 20,000-instruction prefix: **2.100 s median**, 120,308 transactions (from `PERFORMANCE.md`, stage `final-blocks`).

---

## File map

- Modify `dns32/optimized.py` — step loop: cached state, commit+prefetch, path-accurate instruction accounting.
- Modify `dns32/native.py` — `commit_and_fetch`, larger pipelined batches, native sequencer wrapper.
- Modify `dns32/compile.py` — parallel load/store addresses, two-stage compare, carry-lookahead add/sub, wider fast-offset set.
- Modify `dns32/tables.py` — new truth tables: `addgp`, `subgp`, `cla`, `cmp4`.
- Modify `dns32/blocks.py` — stores inside blocks, hammock predication, dead-step elimination.
- Modify `dns32/fastwire.cpp` — compiled-plan sequencer.
- Modify `dns32/__main__.py`, `dns32/benchmark.py`, `dns32/demo.py`, `run-rv32.sh` — table file v4, sequencer flag.
- Modify tests: `tests/test_dns32_optimized.py`, `tests/test_dns32_blocks.py`, new `tests/test_dns32_lookahead.py`.
- Modify `PERFORMANCE.md`, `DNS32.md`, `README.md` — new numbers.

---

### Task 1: Drop the per-block state read and fold the next plan fetch into the commit

**Files:**
- Modify: `dns32/optimized.py` (function `step`, class `PackedRegisters`)
- Modify: `dns32/native.py` (class `NativeBus`)
- Modify: `dns32/benchmark.py` (state restore between repeats)
- Test: `tests/test_dns32_optimized.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dns32_optimized.py`:

```python
import unittest
from dns32.compile import compile_instruction
from dns32.optimized import invalidate

class CountingBus(PackedOracleBus):
    def __init__(self,*a):super().__init__(*a);self.state_reads=0;self.code_reads=0
    def txt(self,name):
        if name=='state.reg':self.state_reads+=1
        if name.endswith('.code'):self.code_reads+=1
        return super().txt(name)
    def commit_and_fetch(self,changes,previous,prerequisite,name):
        self.commit(changes,previous,prerequisite);return self.txt(name)

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
        step_count_before=bus.state_reads
        self.assertIsNone(getattr(bus,'cached_state',None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.config/superpowers/worktrees/dns-doom-perf && python3 -m unittest tests.test_dns32_optimized.PrefetchTests -v`
Expected: FAIL with `ImportError: cannot import name 'invalidate'`

- [ ] **Step 3: Implement**

In `dns32/optimized.py` replace `step` and add `invalidate`; update `PackedRegisters.commit`:

```python
def invalidate(bus):
    """Forget locally held copies of our own committed state (after external restores)."""
    for attr in ('cached_state','prefetched_plan','prefetched_pc'):
        try:delattr(bus,attr)
        except AttributeError:pass


def account(bus,plan,wires):
    """Reporting only: count the instructions this block actually executed."""
    labels=list(plan.get('labels',[plan['label']]))
    skipped=[s for s in plan.get('skips',()) if wires[s[0]]=='1']
    for _,start,length in sorted(skipped,key=lambda s:-s[1]):del labels[start:start+length]
    bus.last_instructions=plan.get('instructions',1)-sum(s[2] for s in skipped)
    bus.last_labels=labels


def step(bus,io=None,pipeline=True,packed=False):
    if packed:
        state=getattr(bus,'cached_state',None)
        if state is None:state=bus.txt('state.reg')
        pc=state[:8]
        raw=getattr(bus,'prefetched_plan',None)
        if raw is None or getattr(bus,'prefetched_pc',None)!=pc:raw=bus.txt(pc+'.code')
    else:
        state=None;pc=bus.txt('pc.reg');raw=bus.txt(pc+'.code')
    plan,stages=schedule(raw)
    wires={'_state':state} if packed else {}
    for copies,queries in stages:
        for _,out,parts in copies:wires[out]=expand(parts,wires)
        if queries:
            names=[expand(parts,wires) for _,_,parts in queries]
            values=bus.txt_many(names) if pipeline else [bus.txt(n) for n in names]
            for (_,out,_),value in zip(queries,values):wires[out]=value
    changes=[(expand(n,wires),expand(v,wires)) for n,v in plan['writes']]
    if packed:
        new_state=next(v for n,v in changes if n=='state.reg');next_pc=new_state[:8]
        fetch=getattr(bus,'commit_and_fetch',None)
        if fetch is None:
            bus.commit(changes,state,prerequisite='state.reg');bus.prefetched_plan=None
        else:
            bus.prefetched_plan=fetch(changes,state,'state.reg',next_pc+'.code')
        bus.cached_state=new_state;bus.prefetched_pc=next_pc;bus.last_pc=next_pc
    else:
        bus.commit(changes,pc)
        bus.last_pc=next(v for n,v in changes if n=='pc.reg')
    account(bus,plan,wires)
    if plan['trap']:
        if io is None:raise RuntimeError('ECALL without I/O')
        io.trap(bus)
    return pc,plan['label']
```

In `PackedRegisters.commit`, after `self.bus.commit(other+[('state.reg',updated)],state,prerequisite='state.reg')` add `self.cached_state=updated`. The wrapper's `__getattr__` only forwards reads, so `cached_state` lives on the wrapper, which is what `step` sees.

In `dns32/native.py` add to `NativeBus`:

```python
    def commit_and_fetch(self,changes,previous,prerequisite,name):
        """One round trip: the UPDATE and the next block's immutable plan query."""
        update=wire.update(self.next_id(),changes,previous,prerequisite)
        query=wire.query(self.next_id(),name)
        replies=self.exchange_many([update,query],['UPDATE',name])
        return wire.parse_txt(replies[1])
```

In `dns32/benchmark.py`, after the restore loop (`for i in range(0,len(changes),200):raw.commit(...)`) add `invalidate(bus)` and import it: `from .optimized import invalidate`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: all OK (the existing 5000-case and block suites included).

- [ ] **Step 5: Live benchmark and commit**

Run the measurement harness with `--output artifacts/performance/t1-prefetch`. Record the median. Expected: roughly 15–20% below 2.100 s, batches per block down by 2.

```bash
git add dns32/optimized.py dns32/native.py dns32/benchmark.py tests/test_dns32_optimized.py
git commit -m "Fold next plan fetch into the commit and reuse committed state"
```

---

### Task 2: Shorter arithmetic chains — parallel load/store addresses, two-stage compare, carry lookahead

**Files:**
- Modify: `dns32/compile.py` (`Wiring.alu`, `Wiring.compare`, `Wiring.load`, `Wiring.store`)
- Modify: `dns32/tables.py` (`lookup`, `records`)
- Modify: `dns32/__main__.py`, `dns32/benchmark.py`, `dns32/verify_live.py` — tables file `artifacts/rv32/tables-v4.zone`, `wiring_revision` 4
- Test: `tests/test_dns32_lookahead.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dns32_lookahead.py`:

```python
"""Truth tables and wiring depth for the lookahead arithmetic."""
import unittest
from dns32.compile import Wiring,compile_instruction
from dns32.optimized import schedule
from dns32.tables import lookup
import json

def depth(plan):return len(schedule(json.dumps(plan))[1])

class LookaheadTables(unittest.TestCase):
    def test_addgp_subgp(self):
        for a in (0,1,127,128,254,255):
            for b in (0,1,127,128,254,255):
                s0,s1,g,p=lookup(f'{a:02x}.{b:02x}.addgp')[0:2],lookup(f'{a:02x}.{b:02x}.addgp')[2:4],lookup(f'{a:02x}.{b:02x}.addgp')[4],lookup(f'{a:02x}.{b:02x}.addgp')[5]
                self.assertEqual((int(s0,16),int(s1,16),g,p),((a+b)&255,(a+b+1)&255,str(int(a+b>255)),str(int((a+b)&255==255))))
                r=lookup(f'{a:02x}.{b:02x}.subgp')
                self.assertEqual((int(r[0:2],16),int(r[2:4],16),r[4],r[5]),((a-b)&255,(a-b-1)&255,str(int(a<b)),str(int(a==b))))
    def test_cla(self):
        self.assertEqual(lookup('00000000.cla'),'0000')
        self.assertEqual(lookup('00000010.cla'),'0010')   # g0 → carry into byte1
        self.assertEqual(lookup('00010110.cla'),'0110')   # g0, p1 → carries into bytes 1 and 2
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
    def test_branch_compare_is_three_stages(self):
        plan=compile_instruction(0x1000,(2<<20)|(1<<15)|(1<<12)|(8<<7)|0x63,True)   # BNE r1,r2,+8
        self.assertEqual(depth(plan),4)   # byte compares, cmp4, cond, mux
    def test_word_load_address_bytes_are_parallel(self):
        plan=compile_instruction(0x1000,(16<<20)|(1<<15)|(2<<12)|(3<<7)|0x03,True)  # LW r3,16(r1)
        self.assertEqual(depth(plan),3)   # add16 low, inc16 high, four parallel byte loads
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_dns32_lookahead -v`
Expected: FAIL (`ValueError` from `lookup` for `addgp`; depth assertions fail with 4/6/9).

- [ ] **Step 3: Implement tables**

In `dns32/tables.py`, inside `lookup` before the `a = int(parts[0],16)` line add:

```python
    if op == "cla":
        bits=parts[0];g=[int(bits[i]) for i in (6,4,2,0)];p=[int(bits[i]) for i in (7,5,3,1)]   # byte0 first
        c=[0]
        for i in range(3):c.append(g[i] | (p[i] & c[i]))
        return "".join(str(x) for x in reversed(c))          # c3c2c1c0
    if op == "cmp4":
        return next((v for v in parts[:4] if v != "eq"), "eq")
```

and after the `b = int(parts[1],16)` line:

```python
    if op in ("addgp","subgp"):
        if op == "addgp":s0,s1,g,p=(a+b)&255,(a+b+1)&255,int(a+b>255),int((a+b)&255==255)
        else:s0,s1,g,p=(a-b)&255,(a-b-1)&255,int(a<b),int(a==b)
        return f"{s0:02x}{s1:02x}{g}{p}"
```

In `records()` add:

```python
    for bits in range(256):
        name=f"{bits:08b}.cla";yield name,lookup(name)
    for m in ("lt","eq","gt"):
        for x in ("lt","eq","gt"):
            for y in ("lt","eq","gt"):
                for z in ("lt","eq","gt"):
                    name=f"{m}.{x}.{y}.{z}.cmp4";yield name,lookup(name)
```

and inside the `for a in range(256): for b in range(256):` loop add `for op in ("addgp","subgp"): name=f"{base}.{op}"; yield name,lookup(name)`.

- [ ] **Step 4: Implement wiring**

In `dns32/compile.py`:

Replace the ripple section of `alu` (from `result = []` through the loop) so that in optimize mode add/sub use lookahead:

```python
        if self.optimize and op in ("add","sub"):
            gp=[self.query(self.cut(a,o,o+2),".",self.cut(b,o,o+2),f".{op}gp") for o in (0,2,4,6)]   # byte3..byte0
            carries=self.query(*[self.cut(x,4,6) for x in gp],".cla")                                  # g3p3g2p2g1p1g0p0 → c3c2c1c0
            return self.copy(*[self.query(self.cut(x,0,2),".",self.cut(x,2,4),".",self.cut(carries,i,i+1),".mux")
                               for i,x in enumerate(gp)])
```

(keep the existing ripple loop for the non-optimized baseline and for and/or/xor).

Replace `compare`:

```python
    def compare(self, a, b, signed=False):
        if not self.optimize:
            state = "eq"
            for offset in (0, 2, 4, 6):
                state = self.query(self.cut(a,offset,offset+2), ".", self.cut(b,offset,offset+2),
                                   ".", state, ".", "scmp" if signed and offset == 0 else "cmp")
            return state
        bytes_=[self.query(self.cut(a,o,o+2),".",self.cut(b,o,o+2),".eq.","scmp" if signed and o==0 else "cmp") for o in (0,2,4,6)]
        return self.query(bytes_[0],".",bytes_[1],".",bytes_[2],".",bytes_[3],".cmp4")
```

Replace the address chains in `load` and `store`:

```python
    def load(self, address, size, signed):
        values = [self.query(address if index == 0 else self.alu("add", address, word(index)), ".mem")
                  for index in range(size)]
        fill = self.query(values[-1], ".sign") if signed and size < 4 else "00"
        return self.copy(*([fill] * (4-size)), *reversed(values))

    def store(self, address, value, size):
        for index in range(size):
            current = address if index == 0 else self.alu("add", address, word(index))
            self.writes.append([[current, ".mem"], [self.cut(value,6-index*2,8-index*2)]])
```

Change the tables default in `dns32/__main__.py` to `'artifacts/rv32/tables-v4.zone'` and `report['wiring_revision']=4 if args.optimize else 1`; in `dns32/benchmark.py` use `tables-v4.zone` and `'wiring_revision':4`; grep `dns32/verify_live.py` and `dns32/demo.py` for `tables-v3` and update likewise.

- [ ] **Step 5: Run all tests**

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK. The 5000-case differential suite with `OPTIMIZE=True` validates the lookahead wiring against the independent emulator through `lookup`.

- [ ] **Step 6: Live benchmark and commit**

Harness with `--output artifacts/performance/t2-lookahead` (the first run regenerates `tables-v4.zone`; note the record count printed). Expected: another clear drop versus Task 1; transactions per instruction go up, batches go down.

```bash
git add dns32/compile.py dns32/tables.py dns32/__main__.py dns32/benchmark.py dns32/verify_live.py dns32/demo.py tests/test_dns32_lookahead.py
git commit -m "Shorten arithmetic chains with lookahead tables and parallel address wiring"
```

---

### Task 3: Widen the fast constant-offset set from the real instruction mix

**Files:**
- Modify: `dns32/compile.py` (`FAST_OFFSETS`)
- Create: `dns32/offsets.py` (one-off histogram tool)

- [ ] **Step 1: Measure which constant offsets the tick actually uses**

Create `dns32/offsets.py`:

```python
"""Histogram of constant add/sub offsets executed by the reference emulator (offline tooling)."""
import sys
from collections import Counter
from .compile import load_elf,sx
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
        if op==0x13 and f==0:counts[sx(ins>>20,12)]+=1                    # ADDI
        elif op==0x03 or (op==0x67 and f==0):counts[sx(ins>>20,12)]+=1      # loads, JALR
        elif op==0x23:counts[sx(((ins>>25)<<5)|((ins>>7)&31),12)]+=1       # stores
        cpu.step(io)
        if cpu.pc==saved['return_pc']:break
    total=sum(counts.values());running=0
    for offset,n in counts.most_common(80):
        running+=n;print(f'{offset:6d} {n:9d} {100*running/total:6.2f}%')

if __name__=='__main__':main()
```

Run inside the container: `docker run --rm ... --entrypoint python dns32:local -m dns32.offsets`

- [ ] **Step 2: Choose the set**

Take offsets in frequency order until cumulative coverage reaches 95% or the set has 64 entries, whichever first. Each offset adds 65,536 `add16` records. Set `FAST_OFFSETS` in `dns32/compile.py` to the sorted tuple (keep 0 excluded; include the existing small values). Delete `artifacts/rv32/tables-v4.zone` so it regenerates.

- [ ] **Step 3: Verify BIND still loads within the 6 GiB container**

Run the harness with `--output artifacts/performance/t3-offsets`. Watch `artifacts/performance/t3-offsets/server.log` for load time; check `docker stats` peak memory. If BIND exceeds ~4 GiB resident, halve the set and repeat.

- [ ] **Step 4: Tests and commit**

`python3 -m unittest discover -s tests` → OK (the table oracle computes `add16` for any offset in the set).

```bash
git add dns32/compile.py dns32/offsets.py
git commit -m "Widen fast constant offsets from the measured instruction mix"
```

---

### Task 4: Stores no longer end blocks

**Files:**
- Modify: `dns32/blocks.py`
- Test: `tests/test_dns32_blocks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dns32_blocks.py`:

```python
from dns32.blocks import compile_blocks

def image_of(instructions,base=0x1000):
    code={base+4*i:compile_instruction(base+4*i,ins,True) for i,ins in enumerate(instructions)}
    return {'entry':base,'code':code,'symbols':{},'memory':{}}

ADDI_R1=(1<<20)|(1<<15)|(1<<7)|0x13          # addi r1,r1,1
SW_R1=(1<<20)|(2<<15)|(2<<12)|0x23            # sw r1,0(r2)
LW_R3=(2<<15)|(2<<12)|(3<<7)|0x03             # lw r3,0(r2)
JAL=0x6f                                      # jal r0,0

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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_dns32_blocks -v`
Expected: the first two fail (`instructions` is 2 then 1 with stores ending blocks).

- [ ] **Step 3: Implement**

Rewrite `dns32/blocks.py` grouping:

```python
END={'JAL','JALR','BEQ','BNE','BLT','BGE','BLTU','BGEU','ECALL','FENCE'}
STORES={'SB','SH','SW'};LOADS={'LB','LH','LW','LBU','LHU'}

def cut_store_before_load(group):
    """A store may stay inside a block only if no later instruction in it loads."""
    for i,p in enumerate(group):
        if p['label'] in STORES and any(q['label'] in LOADS for q in group[i+1:]):return group[:i+1]
    return group

def compile_blocks(image,maximum=16,extra_leaders=()):
    code=image['code'];leaders={image['entry']}|{v for v in image.get('symbols',{}).values() if v in code}
    leaders.update(extra_leaders)
    for pc,p in code.items():
        if 'invalid' in p:continue
        if p['label'] in END:leaders.add(pc+4)
        ins=int(p['bits'],16);op=ins&127
        if op==0x6f:
            imm=sx(((ins>>31)<<20)|(((ins>>12)&255)<<12)|(((ins>>20)&1)<<11)|(((ins>>21)&1023)<<1),21)
            leaders.add((pc+imm)&0xffffffff)
        elif op==0x63:
            imm=sx(((ins>>31)<<12)|(((ins>>7)&1)<<11)|(((ins>>25)&63)<<5)|(((ins>>8)&15)<<1),13)
            leaders.add((pc+imm)&0xffffffff)
    result=dict(code);covered=set()
    for start in sorted(code):
        if start in covered and start not in leaders:continue
        group=[];pc=start
        while pc in code and len(group)<maximum:
            if group and pc in leaders:break
            p=code[pc]
            if 'invalid' in p:break
            group.append(p)
            if p['label'] in END:break
            pc+=4
        group=cut_store_before_load(group)
        covered.update(start+4*i for i in range(len(group)))
        if len(group)>1:result[start]=fuse(group)
    image['code']=result
```

- [ ] **Step 4: Run all tests, benchmark, commit**

`python3 -m unittest discover -s tests` → OK. Harness `--output artifacts/performance/t4-stores`.

```bash
git add dns32/blocks.py tests/test_dns32_blocks.py
git commit -m "Keep stores inside blocks unless a later load could observe them"
```

---

### Task 5: Hammock predication — forward branches over short straight-line arms

**Files:**
- Modify: `dns32/blocks.py` (`fuse`, `compile_blocks`, new `hammock`)
- Modify: `dns32/optimized.py` (`account` already handles `skips`)
- Test: `tests/test_dns32_blocks.py`

A hammock is `Bxx rs1,rs2,+N` followed by N/4−1 straight-line instructions (no control flow, no ECALL/FENCE, no stores) ending at target T. We compute the condition as today, execute the arm as wiring, and select each register the arm wrote with the `mux` table: `arm.prior.cond.mux` returns `prior` when `cond` is `1` (branch taken, arm skipped) and `arm` otherwise. Execution continues at T inside the same block.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dns32_blocks.py`:

```python
BRANCHES={0:'BEQ',1:'BNE',4:'BLT',5:'BGE',6:'BLTU',7:'BGEU'}

def branch(f3,rs1,rs2,imm):
    return ((imm>>12)<<31)|(((imm>>5)&63)<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(((imm>>1)&15)<<8)|(((imm>>11)&1)<<7)|0x63

class HammockTests(unittest.TestCase):
    def test_branch_over_short_arm_is_fused_and_counted_per_path(self):
        rng=random.Random(2718);fused=0
        for case in range(400):
            n=rng.randrange(1,7);ins=[branch(rng.choice(list(BRANCHES)),rng.randrange(32),rng.randrange(32),4*(n+1))]
            for i in range(n+1):   # n arm instructions plus one continuation instruction at T
                rd,rs1,rs2=[rng.randrange(32) for _ in range(3)];f=rng.choice([0,2,3,4,6,7]);op=rng.choice([0x13,0x33])
                ins.append(((rng.randrange(4096)<<20) if op==0x13 else (rs2<<20))|(rs1<<15)|(f<<12)|(rd<<7)|op)
            ins.append(JAL)
            image=image_of(ins);memory={}
            for i,word_ in enumerate(ins):memory.update({0x1000+4*i+j:v for j,v in enumerate(word_.to_bytes(4,'little'))})
            image['memory']=memory
            compile_blocks(image)
            block=image['code'][0x1000]
            if block.get('skips'):fused+=1
            oracle=Reference(image);bus=PackedOracleBus(image,image['code'])
            regs=[0]+[rng.choice([0,1,5,0xffffffff,0x80000000,rng.getrandbits(32)]) for _ in range(31)]
            oracle.regs=regs.copy();bus.regs=regs.copy()
            executed=0
            while oracle.pc!=0x1000+4*len(ins):oracle.step();executed+=1
            while bus.pc!=0x1000+4*len(ins):step(bus,pipeline=True,packed=True)
            self.assertEqual(bus.regs,oracle.regs,case);self.assertEqual(bus.pc,oracle.pc)
            self.assertEqual(bus.last_instructions if block.get('skips') else executed,executed)
        self.assertGreater(fused,350)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_dns32_blocks.HammockTests -v`
Expected: FAIL on `assertGreater(fused,350)` (no `skips` produced yet).

- [ ] **Step 3: Implement**

In `dns32/blocks.py` replace `fuse` and extend `compile_blocks`:

```python
BRANCHES={'BEQ','BNE','BLT','BGE','BLTU','BGEU'}

def branch_target(pc,p):
    ins=int(p['bits'],16)
    imm=sx(((ins>>31)<<12)|(((ins>>7)&1)<<11)|(((ins>>25)&63)<<5)|(((ins>>8)&15)<<1),13)
    return (pc+imm)&0xffffffff

def prune(steps,writes):
    """Drop wiring whose output nothing reads (for example a fused branch's own PC mux)."""
    used=set()
    for _,value in writes:
        for part in value:
            if not isinstance(part,str):used.add(part[0])
    for n,_ in writes:
        for part in n:
            if not isinstance(part,str):used.add(part[0])
    kept=[]
    for kind,out,parts in reversed(steps):
        if out in used:
            kept.append([kind,out,parts])
            for part in parts:
                if not isinstance(part,str):used.add(part[0])
    return kept[::-1]

def fuse(items):
    """items: plans, or {'branch':plan,'arm':[plans]} hammocks. Register values are wires."""
    steps=[];registers={};memory=[];pc_value=None;labels=[];skips=[];index=0
    def emit(p):
        nonlocal index
        prefix=f'i{index}_';index+=1
        def rename(part):return part if isinstance(part,str) else [prefix+part[0],*part[1:]]
        for kind,out,parts in p['steps']:
            if kind=='q' and len(parts)==1 and isinstance(parts[0],str) and parts[0] in registers:
                steps.append(['c',prefix+out,registers[parts[0]]])
            else:steps.append([kind,prefix+out,[rename(v) for v in parts]])
        written={}
        for name,value in p['writes']:
            n=[rename(v) for v in name];v=[rename(x) for x in value]
            if n==['pc.reg']:written['pc.reg']=v
            elif len(n)==1 and isinstance(n[0],str) and n[0].endswith('.reg'):written[n[0]]=v
            else:memory.append([n,v])
        labels.append(p['label'])
        return prefix,written
    def read(reg):
        if reg in registers:return registers[reg]
        out=f'prior_{reg[:3]}';steps.append(['q',out,[reg]]);registers[reg]=[out];return [out]
    for item in items:
        if isinstance(item,dict) and 'branch' in item:
            prefix,written=emit(item['branch'])
            cond=next(prefix+out for kind,out,parts in item['branch']['steps'] if kind=='q' and parts[-1]=='.cond')
            before=dict(registers);first=len(labels)
            for p in item['arm']:
                _,w=emit(p)
                for reg,value in w.items():
                    if reg!='pc.reg':registers[reg]=value
            for reg in [r for r in registers if r in {k for p in item['arm'] for k in dict(emit_writes(p))} ]:pass
            for reg,value in list(registers.items()):
                if reg in before and before[reg]==value:continue
                if reg not in {w for p in item['arm'] for w in writes_of(p)}:continue
                prior=before[reg] if reg in before else read_prior(steps,reg)
                parts=[]
                for i in (0,2,4,6):
                    out=f'{cond}_{reg[:3]}_{i}'
                    steps.append(['q',out,[[value[0],*offsets(value,i)],'.',[prior[0],*offsets(prior,i)],'.',[cond],'.mux']])
                    parts.append([out])
                sel=f'{cond}_{reg[:3]}';steps.append(['c',sel,parts]);registers[reg]=[sel]
            skips.append([cond,first,len(item['arm'])])
        else:
            _,written=emit(item)
            for reg,value in written.items():
                if reg=='pc.reg':pc_value=value
                else:registers[reg]=value
    writes=memory+[[[n],v] for n,v in registers.items() if not n.startswith('prior')]+[[['pc.reg'],pc_value]]
    plan={'label':'BLOCK','steps':prune(steps,writes),'writes':writes,'trap':items[-1]['trap'] if not isinstance(items[-1],dict) or 'branch' not in items[-1] else False,
          'instructions':len(labels),'labels':labels}
    if skips:plan['skips']=skips
    return plan
```

The sketch above has two helper references that must be made concrete; implement them as:

```python
def writes_of(p):
    return [n[0] for n,_ in p['writes'] if len(n)==1 and isinstance(n[0],str) and n[0].endswith('.reg') and n[0]!='pc.reg']

def offsets(ref,i):
    """Byte-slice offsets into a register wire reference (a 1- or 3-element ref)."""
    return [i,i+2] if len(ref)==1 else [ref[1]+i,ref[1]+i+2]

def read_prior(steps,reg):
    out=f'prior_{reg[:3]}';steps.append(['q',out,[reg]]);return [out]
```

and delete the dead `for reg in [...]:pass` line and the `read` closure. Register values in `registers` are refs (`[name]` or `[name,start,end]`); `offsets` handles both. Each arm register write costs four `mux` queries in one extra stage; arm-internal reads see the raw arm value, which is correct because the arm either executes entirely or not at all.

In `compile_blocks`, replace the group-building loop body:

```python
        group=[];pc=start;hard=set(extra_leaders)|{image['entry']}|set(image.get('symbols',{}).values())
        while pc in code and sum(1 if not isinstance(g,dict) or 'branch' not in g else 1+len(g['arm']) for g in group)<maximum:
            if group and pc in leaders:break
            p=code[pc]
            if 'invalid' in p:break
            if p['label'] in BRANCHES:
                target=branch_target(pc,p);arm=[code.get(a) for a in range(pc+4,target,4)]
                ok=0<len(arm)<=6 and target in code and target not in hard and all(
                    a and 'invalid' not in a and a['label'] not in END|STORES for a in arm)
                if ok:
                    group.append({'branch':p,'arm':arm});pc=target;continue
            group.append(p)
            if p['label'] in END:break
            pc+=4
        flat=[x for g in group for x in ([g['branch'],*g['arm']] if isinstance(g,dict) and 'branch' in g else [g])]
        if cut_store_before_load(flat) is not flat:
            # A store would precede a load; fall back to plain straight-line grouping for this start.
            group=[];pc=start
            while pc in code and len(group)<maximum:
                if group and pc in leaders:break
                p=code[pc]
                if 'invalid' in p:break
                group.append(p)
                if p['label'] in END or p['label'] in BRANCHES:break
                pc+=4
            group=cut_store_before_load(group)
        covered.update(start+4*i for i in range(len([x for g in group for x in ([g['branch'],*g['arm']] if isinstance(g,dict) and 'branch' in g else [g])])))
        if len(group)>1 or (group and isinstance(group[0],dict)):result[start]=fuse(group)
```

Important: a hammock's `pc` jump to `target` means the arm addresses are covered but T itself is consumed inside this block while remaining a leader with its own plan for other entries.

- [ ] **Step 4: Run all tests**

`python3 -m unittest discover -s tests -v 2>&1 | tail -5` → OK, including the 1000 straight-line blocks and the store suites.

- [ ] **Step 5: Count real hammocks, benchmark, commit**

Inside the container run a quick count:

```sh
python -c "
from dns32.compile import load_elf;from dns32.blocks import compile_blocks
im=load_elf('artifacts/rv32/doom-small.elf',optimize=True);n=len(im['code']);compile_blocks(im)
print(sum(1 for p in im['code'].values() if p.get('skips')),'blocks with hammocks of',n)"
```

Harness `--output artifacts/performance/t5-hammocks`. Expect block clocks per 20,000 instructions to drop noticeably; if the median does not improve, raise the arm limit to 8 and re-measure before deciding.

```bash
git add dns32/blocks.py tests/test_dns32_blocks.py
git commit -m "Predicate short forward branches so blocks run through them"
```

---

### Task 6: Native sequencer — move the per-stage loop into the C++ extension

**Files:**
- Modify: `dns32/fastwire.cpp` (add `compile_plan`, `run`)
- Modify: `dns32/native.py` (class `NativeSequencer`)
- Modify: `dns32/optimized.py` (function `step_native`)
- Modify: `dns32/__main__.py`, `dns32/benchmark.py`, `dns32/demo.py`, `run-rv32.sh`, `deploy` flags: `--sequencer native`

Only wire shuffling moves to C++: concatenating slices, encoding queries, exchanging batches, decoding TXT, building the UPDATE. The plan still comes from DNS each clock and every arithmetic answer is a DNS response.

- [ ] **Step 1: Write the live test**

Append to `dns32/verify_live.py` a `--sequencer native` flag that runs the same 80 cases through `step_native`; this cannot be unit-tested without BIND, so the test is the live suite plus the full tick. (Read `dns32/verify_live.py` first; mirror how it selects `step`.)

- [ ] **Step 2: Implement C++**

Add to `dns32/fastwire.cpp`:

```cpp
struct Part{bool literal;std::string text;int wire;int start;int end;};
struct Step{int out;std::vector<Part> parts;};
struct Stage{std::vector<Step> copies,queries;};
struct Plan{std::vector<Stage> stages;std::vector<std::pair<std::vector<Part>,std::vector<Part>>> writes;int wires;int state_write;std::vector<std::pair<int,int>> skips;/*cond wire, arm length*/};

static std::string build(const std::vector<Part>& parts,const std::vector<std::string>& wires){std::string s;for(auto& p:parts){if(p.literal)s+=p.text;else if(p.start<0)s+=wires[p.wire];else s.append(wires[p.wire],p.start,p.end-p.start);}return s;}
```

`compile_plan(stages, writes, skips, wire_count, state_write_index)` receives Python lists where each part is either a `str` or `(wire_index, start, end)` with `start=-1` for whole-wire; returns a `PyCapsule` owning a `Plan`.

`run(fd, plan_capsule, state, next_id, sample_limit)`:
1. `wires[0]=state`; for each stage: evaluate copies; build query names; send all queries in chunks of at most `BATCH` (256) in one `exchange`; parse each TXT into its wire.
2. Build `changes` from writes; `new_state = changes[state_write].second`; `next_pc = new_state.substr(0,8)`.
3. One exchange with `[update(changes, state, "state.reg"), query(next_pc+".code")]`; verify RCODE of both; parse the second as TXT.
4. Return `(new_state, next_raw, skipped_mask_list, transactions, payload_bytes, round_trips, next_id, samples)` where `skipped_mask_list` is `[wires[cond]=="1" for each skip]`.

Refactor the existing `exchange` body into a C++ function `do_exchange(int fd, const std::vector<std::string>& requests) -> std::vector<std::string>` used by both the Python-facing `exchange` and `run`. Keep the RCODE/AA checks identical to `NativeBus.exchange_many` and `parse`.

- [ ] **Step 3: Python side**

In `dns32/native.py`:

```python
class NativeSequencer:
    """Runs packed block plans with the C++ wiring loop. Plans are decoded once per distinct text."""
    def __init__(self,bus):
        self.bus=bus;self.compiled={}
    def plan(self,raw):
        c=self.compiled.get(raw)
        if c is None:c=self.compiled[raw]=compile_native(raw)
        return c
    def step(self,state,raw):
        meta,capsule=self.plan(raw)
        new_state,next_raw,skipped,tx,payload,trips,self.bus.sequence,samples=wire.run(
            self.bus.socket.fileno(),capsule,state,self.bus.sequence,self.bus.sample_limit-len(self.bus.samples))
        self.bus.transactions+=tx;self.bus.payload_bytes+=payload;self.bus.round_trips+=trips;self.bus.samples.extend(samples)
        return meta,new_state,next_raw,skipped


def compile_native(raw):
    from .optimized import schedule
    plan,stages=schedule(raw)
    if 'invalid' in plan:raise RuntimeError(plan['invalid'])
    index={'_state':0};def wire_of(n):return index.setdefault(n,len(index))
    def part(p):return p if isinstance(p,str) else (wire_of(p[0]),-1 if len(p)==1 else p[1],-1 if len(p)==1 else p[2])
    out_stages=[([(wire_of(o),[part(p) for p in parts]) for _,o,parts in copies],
                 [(wire_of(o),[part(p) for p in parts]) for _,o,parts in queries]) for copies,queries in stages]
    writes=[([part(p) for p in n],[part(p) for p in v]) for n,v in plan['writes']]
    state_write=next(i for i,(n,_) in enumerate(plan['writes']) if n==['state.reg'])
    skips=[(wire_of(c),length) for c,_,length in plan.get('skips',())]
    meta={'plan':plan}
    return meta,wire.compile_plan(out_stages,writes,skips,len(index),state_write)
```

In `dns32/optimized.py`:

```python
def step_native(bus,sequencer,io=None):
    state=getattr(bus,'cached_state',None)
    if state is None:state=bus.txt('state.reg')
    pc=state[:8]
    raw=getattr(bus,'prefetched_plan',None)
    if raw is None or getattr(bus,'prefetched_pc',None)!=pc:raw=bus.txt(pc+'.code')
    meta,new_state,next_raw,skipped=sequencer.step(state,raw)
    plan=meta['plan']
    bus.cached_state=new_state;bus.prefetched_plan=next_raw;bus.prefetched_pc=bus.last_pc=new_state[:8]
    wires={s[0]:('1' if hit else '0') for s,hit in zip(plan.get('skips',()),skipped)}
    account(bus,plan,wires)
    if plan['trap']:
        if io is None:raise RuntimeError('ECALL without I/O')
        io.trap(bus)
    return pc,plan['label']
```

Wire it in `dns32/__main__.py`: add `--sequencer` choices `python|native` (default `native` when `--client native --packed --blocks`); when native, `execute=lambda:step_native(bus,NativeSequencer(raw_bus),io)` with the sequencer constructed once. Mirror in `dns32/benchmark.py`. In `dns32/demo.py` and `run-rv32.sh`, the `fast` flag set gains `--sequencer native`.

- [ ] **Step 4: Build, live tests, benchmark, commit**

```sh
docker run --rm ... --entrypoint bash dns32:local ./build-native.sh
docker run --rm ... --entrypoint python dns32:local -m dns32.verify_live --native --optimized --packed --server bind --sequencer native --output artifacts/performance/t6-live.json
```

Harness `--output artifacts/performance/t6-native-sequencer` (with `--sequencer native`). Expected: per-round-trip cost drops from ~29 µs toward 15–20 µs.

```bash
git add dns32/fastwire.cpp dns32/native.py dns32/optimized.py dns32/__main__.py dns32/benchmark.py dns32/demo.py dns32/verify_live.py run-rv32.sh
git commit -m "Run the wiring loop natively; plans and answers still come from DNS"
```

---

### Task 7: Plan payload — measure, then decide

**Files:**
- Possibly modify: `dns32/server.py` (`txt_record` for `.code`), `dns32/optimized.py` (`schedule`), `dns32/native.py` (`compile_native`)

- [ ] **Step 1: Measure**

After Task 1 the plan fetch shares a round trip with the UPDATE, so its only remaining cost is bytes. From `artifacts/performance/t6-native-sequencer/benchmark.json` and the server log, compute average `.code` response size (sample names in a run report give `response_bytes`). Run the harness twice more with `BATCH` logging to see whether time per round trip differs between plan-carrying trips and plain query trips. If plan-carrying trips are not at least 10% slower than plain ones, stop here and record the finding in `PERFORMANCE.md`.

- [ ] **Step 2: If it matters, compact the encoding**

Encode steps as `k|out|p;p;p` lines with parts `'literal`, `name`, or `name:start:end`, decoded in `schedule` before the existing logic. Keep JSON for `writes`. Regenerate, re-run the full test suite and the harness, record the before/after.

---

### Task 8: Full verified tick, docs, PR

- [ ] **Step 1: Full tick**

```sh
./run-rv32.sh fast
python3 -c "import json;r=json.load(open('artifacts/dns32-tick/run.json'));print({k:r[k] for k in ('status','instructions','elapsed','dns_transactions','network_batches','clock_cycles','reference_pixels_match','reference_registers_match','reference_game_state_match','reference_pc_match')})"
```

All four `reference_*` must be `true`; `instructions` must still be 1,389,327 (path-accurate accounting). Record `elapsed`.

- [ ] **Step 2: Live suites for the record**

`./run-rv32.sh test` — all green, including Knot (Knot only needs the new tables to load).

- [ ] **Step 3: Update docs**

`PERFORMANCE.md`: new headline numbers, extend the stage table with rows t1..t6 (median seconds / 20,000 instructions, transactions, batches), a short paragraph per technique in the "What changed" style already used, and the honest ceiling paragraph (turn-based, not playable). `DNS32.md` and `README.md`: replace the 146.28 s headline and transaction/batch counts. `dns32/demo.html`: confirm the progress max still matches the instruction count.

- [ ] **Step 4: Commit, push branch, open PR**

```bash
git add PERFORMANCE.md DNS32.md README.md artifacts/performance/*/benchmark.json
git commit -m "Record depth-to-width results"
git push -u origin perf/depth-to-width
gh pr create --title "Depth to width: cut dependent DNS round trips per instruction" --body "..."
```

CI runs the tick on the PR. Merge only after CI is green; merging deploys.
