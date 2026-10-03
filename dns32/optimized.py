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


def step(bus,io=None,pipeline=True,packed=False):
    state=bus.txt('state.reg') if packed else None
    pc=state[:8] if packed else bus.txt('pc.reg')
    plan,stages=schedule(bus.txt(pc+'.code'))
    wires={'_state':state} if packed else {}
    for copies,queries in stages:
        for _,out,parts in copies:wires[out]=expand(parts,wires)
        if queries:
            names=[expand(parts,wires) for _,_,parts in queries]
            values=bus.txt_many(names) if pipeline else [bus.txt(n) for n in names]
            for (_,out,_),value in zip(queries,values):wires[out]=value
    changes=[(expand(n,wires),expand(v,wires)) for n,v in plan['writes']]
    if packed:
        bus.commit(changes,state,prerequisite='state.reg')
        bus.last_pc=next(v[:8] for n,v in changes if n=='state.reg')
    else:
        bus.commit(changes,pc)
        bus.last_pc=next(v for n,v in changes if n=='pc.reg')
    if plan['trap']:
        if io is None:raise RuntimeError('ECALL without I/O')
        io.trap(bus)
    bus.last_instructions=plan.get('instructions',1)
    bus.last_labels=plan.get('labels',[plan['label']])
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
        else:self.bus.commit(changes,previous_pc,prerequisite=prerequisite)
