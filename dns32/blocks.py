"""Offline straight-line instruction fusion. Stores and control flow end blocks.

Intermediate register values are opaque wires; all variable arithmetic still
queries DNS. Architectural state is committed at each block boundary.
"""
from .compile import sx

END={'JAL','JALR','BEQ','BNE','BLT','BGE','BLTU','BGEU','ECALL','FENCE'}
STORES={'SB','SH','SW'};LOADS={'LB','LH','LW','LBU','LHU'}

def cut_store_before_load(group):
    """A store may stay inside a block only if no later instruction in it loads."""
    for i,p in enumerate(group):
        if p['label'] in STORES and any(q['label'] in LOADS for q in group[i+1:]):return group[:i+1]
    return group

BRANCHES={'BEQ','BNE','BLT','BGE','BLTU','BGEU'}

def branch_target(pc,p):
    ins=int(p['bits'],16)
    imm=sx(((ins>>31)<<12)|(((ins>>7)&1)<<11)|(((ins>>25)&63)<<5)|(((ins>>8)&15)<<1),13)
    return (pc+imm)&0xffffffff

def prune(steps,writes,keep=()):
    """Drop wiring whose output nothing reads, such as a fused branch's own PC selection."""
    used=set(keep)
    for name,value in writes:
        for part in name+value:
            if not isinstance(part,str):used.add(part[0])
    kept=[]
    for kind,out,parts in reversed(steps):
        if out in used:
            kept.append([kind,out,parts])
            for part in parts:
                if not isinstance(part,str):used.add(part[0])
    return kept[::-1]

def fuse(items):
    """Fuse plans into one block. Items are plans or {'branch':plan,'arm':[plans]} hammocks.

    A hammock is a forward conditional branch over a short straight-line arm. The
    arm's wiring always runs; each register it writes is then selected with the
    DNS mux table between the arm's value and the prior value, keyed on the branch
    condition. No local decision is made: the condition answer and the selection
    answers both come from DNS.
    """
    steps=[];registers={};memory=[];pc_value=None;labels=[];skips=[];counter=0
    def emit(p):
        nonlocal counter
        prefix=f'i{counter}_';counter+=1
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
    def wire(name,parts):
        steps.append(['c',name,parts]);return name
    for item in items:
        if isinstance(item,dict) and 'branch' in item:
            prefix,_=emit(item['branch'])
            cond=next(prefix+out for kind,out,parts in item['branch']['steps'] if kind=='q' and parts[-1] in ('.cond','.cond2','.cond4'))
            before=dict(registers);first=len(labels);changed=[]
            for p in item['arm']:
                _,written=emit(p)
                for reg,value in written.items():
                    if reg!='pc.reg':
                        registers[reg]=value
                        if reg not in changed:changed.append(reg)
            for reg in changed:
                tag=f'{cond}_{reg[:3]}'
                arm=wire(tag+'_arm',registers[reg])
                if reg in before:prior=wire(tag+'_prior',before[reg])
                else:prior=tag+'_prior';steps.append(['q',prior,[reg]])
                selected=[]
                for i in (0,2,4,6):
                    steps.append(['q',f'{tag}_{i}',[[arm,i,i+2],'.',[prior,i,i+2],'.',[cond],'.mux']])
                    selected.append([f'{tag}_{i}'])
                registers[reg]=[[wire(tag,selected)]]
            skips.append([cond,first,len(item['arm'])])
        else:
            _,written=emit(item)
            for reg,value in written.items():
                if reg=='pc.reg':pc_value=value
                else:registers[reg]=value
    writes=memory+[[[n],v] for n,v in registers.items()]+[[['pc.reg'],pc_value]]
    last=items[-1]
    trap=False if isinstance(last,dict) and 'branch' in last else last['trap']
    plan={'label':'BLOCK','steps':prune(steps,writes,keep=[s[0] for s in skips]),'writes':writes,'trap':trap,
          'instructions':len(labels),'labels':labels}
    if skips:plan['skips']=skips
    return plan

def flatten(group):
    return [x for g in group for x in ([g['branch'],*g['arm']] if isinstance(g,dict) and 'branch' in g else [g])]

def compile_blocks(image,maximum=16,extra_leaders=(),arm_limit=6):
    code=image['code']
    hard={image['entry']}|{v for v in image.get('symbols',{}).values() if v in code}|set(extra_leaders)
    leaders=set(hard)
    for pc,p in code.items():
        if 'invalid' in p:continue
        if p['label'] in END:leaders.add(pc+4)
        ins=int(p['bits'],16);op=ins&127
        if op==0x6f:
            imm=sx(((ins>>31)<<20)|(((ins>>12)&255)<<12)|(((ins>>20)&1)<<11)|(((ins>>21)&1023)<<1),21)
            leaders.add((pc+imm)&0xffffffff)
        elif op==0x63:
            leaders.add(branch_target(pc,p))
    def straight(start):
        group=[];pc=start
        while pc in code and len(group)<maximum:
            if group and pc in leaders:break
            p=code[pc]
            if 'invalid' in p:break
            group.append(p)
            if p['label'] in END:break
            pc+=4
        return cut_store_before_load(group)
    def predicated(start):
        group=[];pc=start;count=0;joined=False
        while pc in code and count<maximum:
            if group and pc in leaders and not joined:break
            joined=False
            p=code[pc]
            if 'invalid' in p:break
            if p['label'] in BRANCHES:
                target=branch_target(pc,p)
                arm=[code.get(a) for a in range(pc+4,target,4)] if pc<target<=pc+4*(arm_limit+1) else []
                if arm and count+1+len(arm)<maximum and target in code and target not in hard and not any(
                        a in hard for a in range(pc+4,target,4)) and all(
                        a and 'invalid' not in a and a['label'] not in END and a['label'] not in STORES for a in arm):
                    group.append({'branch':p,'arm':arm});count+=1+len(arm);pc=target;joined=True;continue
            group.append(p);count+=1
            if p['label'] in END:break
            pc+=4
        return group
    result=dict(code);covered=set()
    for start in sorted(code):
        if start in covered and start not in leaders:continue
        group=predicated(start);flat=flatten(group)
        if not any(isinstance(g,dict) for g in group) or cut_store_before_load(flat) is not flat:
            group=straight(start);flat=group
        covered.update(start+4*i for i in range(len(flat)))
        if len(flat)>1:result[start]=fuse(group)
    # Every non-leader address retains its single-instruction plan. Indirect
    # branches into a block's middle therefore remain correct.
    image['code']=result
