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
