"""Offline register packing plus dependency scheduling; no guest arithmetic."""
import json
from functools import lru_cache
from .runtime import expand


def pack_plan(plan):
    if 'invalid' in plan:return plan
    plan=json.loads(json.dumps(plan))
    for step in plan['steps']:
        kind,out,parts=step
        if kind=='q' and len(parts)==1 and isinstance(parts[0],str) and parts[0].endswith('.reg'):
            name=parts[0]
            index=0 if name=='pc.reg' else 8+int(name[1:3])*8
            step[:]=['c',out,[['_state',index,index+8]]]
    fields=[[['_state',i,i+8]] for i in range(0,264,8)]
    memory=[]
    for name,value in plan['writes']:
        if len(name)==1 and isinstance(name[0],str) and name[0].endswith('.reg'):
            index=0 if name[0]=='pc.reg' else 1+int(name[0][1:3])
            fields[index]=value
        else:memory.append([name,value])
    plan['writes']=memory+[[['state.reg'],[part for field in fields for part in field]]]
    return plan


@lru_cache(maxsize=8192)
def schedule(raw):
    # The exact plan is still fetched from DNS on every step. Only decoding and
    # its static dependency schedule are memoized; DNS answers are never cached.
    plan=json.loads(raw)
    if 'invalid' in plan:raise RuntimeError(plan['invalid'])
    pending=plan['steps'].copy();known={'_state'};stages=[]
    def ready(step):return all(isinstance(p,str) or p[0] in known for p in step[2])
    while pending:
        copies=[]
        while True:
            batch=[s for s in pending if s[0]=='c' and ready(s)]
            if not batch:break
            for s in batch:copies.append(s);known.add(s[1]);pending.remove(s)
        queries=[s for s in pending if s[0]=='q' and ready(s)]
        for s in queries:known.add(s[1]);pending.remove(s)
        if not copies and not queries:raise ValueError('Cyclic or invalid wiring plan')
        stages.append((copies,queries))
    return plan,stages


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
        # Our own last committed state and the plan fetched alongside that commit.
        # DNS still proves consistency: the UPDATE prerequisite names this exact state.
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


def step_native(bus,sequencer,io=None):
    """Packed block step whose wiring loop runs in the native extension."""
    state=getattr(bus,'cached_state',None)
    if state is None:state=bus.txt('state.reg')
    pc=state[:8]
    raw=getattr(bus,'prefetched_plan',None)
    if raw is None or getattr(bus,'prefetched_pc',None)!=pc:raw=bus.txt(pc+'.code')
    plan,new_state,next_raw,skipped=sequencer.step(state,raw)
    bus.cached_state=new_state;bus.prefetched_plan=next_raw;bus.prefetched_pc=bus.last_pc=new_state[:8]
    wires={s[0]:('1' if hit else '0') for s,hit in zip(plan.get('skips',()),skipped)}
    account(bus,plan,wires)
    if plan['trap']:
        if io is None:raise RuntimeError('ECALL without I/O')
        io.trap(bus)
    return pc,plan['label']


class PackedRegisters:
    """Peripheral/readback adapter; register slicing is fixed physical wiring."""
    def __init__(self,bus):self.bus=bus
    def __getattr__(self,name):return getattr(self.bus,name)
    @staticmethod
    def offset(name):return 0 if name=='pc.reg' else 8+int(name[1:3])*8
    def txt(self,name):
        if name.endswith('.reg') and name!='state.reg':
            value=self.bus.txt('state.reg');i=self.offset(name);return value[i:i+8]
        return self.bus.txt(name)
    def commit(self,changes,previous_pc=None,prerequisite='pc.reg'):
        if any(n.endswith('.reg') and n!='state.reg' for n,v in changes):
            state=self.bus.txt('state.reg');updated=state;other=[]
            if previous_pc is not None and state[:8]!=previous_pc:raise RuntimeError('Stale packed PC')
            for name,value in changes:
                if name.endswith('.reg'):
                    i=self.offset(name);updated=updated[:i]+value+updated[i+8:]
                else:other.append((name,value))
            self.bus.commit(other+[('state.reg',updated)],state,prerequisite='state.reg')
            self.cached_state=updated
        else:self.bus.commit(changes,previous_pc,prerequisite=prerequisite)
