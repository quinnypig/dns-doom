"""Offline straight-line instruction fusion. Stores and control flow end blocks.

Intermediate register values are opaque wires; all variable arithmetic still
queries DNS. Architectural state is committed at each block boundary.
"""
from .compile import sx

END={'JAL','JALR','BEQ','BNE','BLT','BGE','BLTU','BGEU','SB','SH','SW','ECALL','FENCE'}

def fuse(plans):
    steps=[];registers={};memory=[];pc_value=None;labels=[]
    for index,p in enumerate(plans):
        prefix=f'i{index}_'
        def rename(part):return part if isinstance(part,str) else [prefix+part[0],*part[1:]]
        for kind,out,parts in p['steps']:
            if kind=='q' and len(parts)==1 and isinstance(parts[0],str) and parts[0] in registers:
                steps.append(['c',prefix+out,registers[parts[0]]])
            else:steps.append([kind,prefix+out,[rename(v) for v in parts]])
        for name,value in p['writes']:
            n=[rename(v) for v in name];v=[rename(x) for x in value]
            if n==['pc.reg']:pc_value=v
            elif len(n)==1 and isinstance(n[0],str) and n[0].endswith('.reg'):registers[n[0]]=v
            else:memory.append([n,v])
        labels.append(p['label'])
    writes=memory+[[[n],v] for n,v in registers.items()]+[[['pc.reg'],pc_value]]
    return {'label':'BLOCK','steps':steps,'writes':writes,'trap':plans[-1]['trap'],
            'instructions':len(plans),'labels':labels}

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
            group.append(p);covered.add(pc)
            if p['label'] in END:break
            pc+=4
        if len(group)>1:result[start]=fuse(group)
    # Every non-leader address retains its single-instruction plan. Indirect
    # branches into a block's middle therefore remain correct.
    image['code']=result
