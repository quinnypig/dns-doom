"""Offline RV32I decoder and wiring compiler. No guest arithmetic runs here at execution time."""

import struct
import hashlib

# Chosen from the measured constant-add histogram of the verified Doom tick
# (artifacts/performance/offsets.txt): these 64 offsets cover about 84% of the
# constant additions the tick performs. Each costs 65,536 add16 records.
FAST_OFFSETS=(-1236,-1235,-1234,-1233,-1232,-1231,-1230,-1229,-1228,-1227,-1226,-1225,-1216,-1215,-1214,-1213,
              -320,-1,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,
              33,36,37,38,39,40,41,42,43,44,45,46,47,320)


def sx(n, width):
    return n - (1 << width) if n & (1 << (width - 1)) else n


def word(n):
    return f"{n & 0xffffffff:08x}"


def ref(name, start=None, end=None):
    return [name] if start is None else [name, start, end]


class Wiring:
    def __init__(self,optimize=False):
        self.steps = []
        self.serial = 0
        self.writes = []
        self.optimize=optimize
        self.queries={}

    def query(self, *parts):
        key=repr(parts)
        if self.optimize and key in self.queries:return self.queries[key]
        out = f"v{self.serial}"
        self.serial += 1
        self.steps.append(["q", out, list(parts)])
        result=ref(out)
        self.queries[key]=result
        return result

    def copy(self, *parts):
        out = f"v{self.serial}"
        self.serial += 1
        self.steps.append(["c", out, list(parts)])
        return ref(out)

    def cut(self, source, start, end):
        if isinstance(source, str):
            return source[start:end]
        if len(source) == 1:
            return [source[0], start, end]
        return [source[0], source[1] + start, source[1] + end]

    def reg(self, index):
        return "00000000" if index == 0 else self.query(f"r{index:02}.reg")

    def alu(self, op, a, b):
        if self.optimize:
            if b=="00000000" and op in ("add","sub","or","xor"):return a
            if b=="ffffffff" and op=="and":return a
            if b=="00000000" and op=="and":return b
            if a==b and op in ("sub","xor"):return "00000000"
            if op in ('add','sub') and isinstance(b,str):
                offset=sx(int(b,16),32)*(1 if op=='add' else -1)
                if offset in FAST_OFFSETS:
                    low=self.query(self.cut(a,4,8),'.',word(offset),'.add16')
                    high=self.query(self.cut(a,0,4),'.',self.cut(low,4,5),'.inc16' if offset>0 else '.dec16')
                    return self.copy(high,self.cut(low,0,4))
        if self.optimize and op in ("add","sub"):
            # Carry lookahead: one stage of byte generate/propagate lookups, one carry-chain
            # lookup, one stage of byte selection. Three dependent trips instead of four.
            gp=[self.query(self.cut(a,o,o+2),".",self.cut(b,o,o+2),f".{op}gp") for o in (0,2,4,6)]   # byte3..byte0
            carries=self.query(*[self.cut(x,4,6) for x in gp],".cla")                                  # g3p3..g0p0 -> c3c2c1c0
            return self.copy(*[self.query(self.cut(x,0,2),".",self.cut(x,2,4),".",self.cut(carries,i,i+1),".mux")
                               for i,x in enumerate(gp)])
        result = []
        carry = "0"
        for offset in (6, 4, 2, 0):
            name = [self.cut(a, offset, offset+2), ".", self.cut(b, offset, offset+2), "."]
            if op in ("add", "sub"):
                out = self.query(*name, carry, ".", op)
                carry = self.cut(out, 2, 3)
                result.append(self.cut(out, 0, 2))
            else:
                byte=self.cut(b,offset,offset+2)
                if self.optimize and op=="and" and byte in ("00","ff"):
                    result.append("00" if byte=="00" else self.cut(a,offset,offset+2))
                elif self.optimize and op in ("or","xor") and byte=="00":
                    result.append(self.cut(a,offset,offset+2))
                else:result.append(self.query(*name, op))
        return self.copy(*reversed(result))

    def compare(self, a, b, signed=False):
        if not self.optimize:
            state = "eq"
            for offset in (0, 2, 4, 6):
                state = self.query(self.cut(a,offset,offset+2), ".", self.cut(b,offset,offset+2),
                                   ".", state, ".", "scmp" if signed and offset == 0 else "cmp")
            return state
        # All four byte comparisons in one stage, then one lookup picks the most significant difference.
        bytes_=[self.query(self.cut(a,o,o+2),".",self.cut(b,o,o+2),".eq.","scmp" if signed and o==0 else "cmp") for o in (0,2,4,6)]
        return self.query(bytes_[0],".",bytes_[1],".",bytes_[2],".",bytes_[3],".cmp4")

    def condition(self, comparison, condition):
        return self.query(comparison, ".", condition, ".cond")

    def mux(self, cond, a, b):
        return self.copy(*[self.query(self.cut(a,i,i+2), ".", self.cut(b,i,i+2), ".", cond, ".mux")
                           for i in (0,2,4,6)])

    def shift_fixed(self, a, amount, kind):
        if amount == 0:
            return a
        if self.optimize:
            whole,bits=divmod(amount,8)
            fill=self.query(self.cut(a,0,2),".sign") if kind=="sra" else "00"
            def source(i):return self.cut(a,i*2,i*2+2) if 0<=i<4 else ("00" if kind=="sll" else fill)
            values=[]
            for i in range(4):
                j=i+whole if kind=="sll" else i-whole
                if not bits or not 0<=j<4:values.append(source(j))
                else:values.append(self.query(source(j),".",source(j+1 if kind=="sll" else j-1),f".{bits}.","shl" if kind=="sll" else "shr"))
            return self.copy(*values)
        fill = self.query(self.cut(a,0,2), ".sign") if kind == "sra" else "00"
        if amount >= 8:
            size = amount // 8
            if kind == "sll":
                return self.copy(self.cut(a,size*2,8), "00" * size)
            return self.copy(*([fill]*size), self.cut(a,0,8-size*2))
        result = []
        for i in (0,2,4,6):
            if kind == "sll":
                neighbor = self.cut(a,i+2,i+4) if i < 6 else "00"
            else:
                neighbor = self.cut(a,i-2,i) if i > 0 else fill
            result.append(self.query(self.cut(a,i,i+2), ".", neighbor, f".{amount}.",
                                     "shl" if kind == "sll" else "shr"))
        return self.copy(*result)

    def shift(self, a, b, kind):
        controls = self.query(self.cut(b,6,8), ".shiftbits")
        for index, amount in enumerate((1,2,4,8,16)):
            a = self.mux(self.cut(controls,index,index+1), a, self.shift_fixed(a,amount,kind))
        return a

    def address(self, base, offset, index):
        # Every byte address is base plus one constant, so all of them share the same stages.
        return self.alu("add", base, word(offset + index))

    def load(self, base, offset, size, signed):
        values = [self.query(self.address(base, offset, index), ".mem") for index in range(size)]
        fill = self.query(values[-1], ".sign") if signed and size < 4 else "00"
        return self.copy(*([fill] * (4-size)), *reversed(values))

    def store(self, base, offset, value, size):
        for index in range(size):
            self.writes.append([[self.address(base, offset, index), ".mem"], [self.cut(value,6-index*2,8-index*2)]])


def compile_instruction(pc, ins,optimize=False):
    w = Wiring(optimize)
    opcode, rd, f3 = ins & 127, (ins >> 7) & 31, (ins >> 12) & 7
    rs1, rs2, f7 = (ins >> 15) & 31, (ins >> 20) & 31, ins >> 25
    imm_i = sx(ins >> 20,12)
    imm_s = sx(((ins >> 25) << 5) | ((ins >> 7) & 31),12)
    imm_b = sx(((ins >> 31) << 12) | (((ins >> 7) & 1) << 11) |
               (((ins >> 25) & 63) << 5) | (((ins >> 8) & 15) << 1),13)
    imm_j = sx(((ins >> 31) << 20) | (((ins >> 12) & 255) << 12) |
               (((ins >> 20) & 1) << 11) | (((ins >> 21) & 1023) << 1),21)
    next_pc, value, label, trap = word(pc+4), None, "", False
    if opcode == 0x37:
        value, label = word(ins & 0xfffff000), "LUI"
    elif opcode == 0x17:
        # AUIPC is constant at this linked address; resolve during compilation.
        value, label = word(pc + (ins & 0xfffff000)), "AUIPC"
    elif opcode == 0x6f:
        value, next_pc, label = word(pc+4), word(pc+imm_j), "JAL"
    elif opcode == 0x67 and f3 == 0:
        value = word(pc+4)
        target = w.alu("add", w.reg(rs1), word(imm_i))
        low = w.query(w.cut(target,6,8), ".clearlsb")
        next_pc, label = w.copy(w.cut(target,0,6),low), "JALR"
    elif opcode == 0x63 and f3 in (0,1,4,5,6,7):
        a,b = w.reg(rs1),w.reg(rs2)
        condition = {0:"eq",1:"ne",4:"lt",5:"ge",6:"lt",7:"ge"}[f3]
        compared = w.compare(a,b,signed=f3 in (4,5))
        next_pc = w.mux(w.condition(compared,condition), word(pc+4), word(pc+imm_b))
        label = {0:"BEQ",1:"BNE",4:"BLT",5:"BGE",6:"BLTU",7:"BGEU"}[f3]
    elif opcode == 0x03 and f3 in (0,1,2,4,5):
        value = w.load(w.reg(rs1), imm_i, {0:1,1:2,2:4,4:1,5:2}[f3], signed=f3 in (0,1))
        label = {0:"LB",1:"LH",2:"LW",4:"LBU",5:"LHU"}[f3]
    elif opcode == 0x23 and f3 in (0,1,2):
        w.store(w.reg(rs1),imm_s,w.reg(rs2),1 << f3)
        label = {0:"SB",1:"SH",2:"SW"}[f3]
    elif opcode in (0x13,0x33):
        a = w.reg(rs1)
        immediate = opcode == 0x13
        b = word(imm_i) if immediate else w.reg(rs2)
        if f3 == 0 and (immediate or f7 in (0,32)):
            op = "sub" if not immediate and f7 == 32 else "add"
            value, label = w.alu(op,a,b), op.upper()
        elif f3 in (4,6,7) and (immediate or f7 == 0):
            op = {4:"xor",6:"or",7:"and"}[f3]
            value,label = w.alu(op,a,b),op.upper()
        elif f3 in (2,3) and (immediate or f7 == 0):
            condition = w.condition(w.compare(a,b,signed=f3 == 2),"lt")
            value,label = w.copy("0000000",condition),"SLT" if f3 == 2 else "SLTU"
        elif (f3 == 1 and f7 == 0) or (f3 == 5 and f7 in (0,32)):
            kind = "sll" if f3 == 1 else ("sra" if f7 == 32 else "srl")
            if immediate:
                if optimize:value=w.shift_fixed(a,rs2,kind)
                else:
                    value = a
                    for amount in (1,2,4,8,16):
                        if rs2 & amount:
                            value = w.shift_fixed(value,amount,kind)
            else:
                value = w.shift(a,b,kind)
            label = kind.upper()
        else:
            raise ValueError(f"Unsupported ALU instruction {ins:08x} at {pc:08x}")
        if immediate:
            label += "I"
    elif ins == 0x00000073:
        label, trap = "ECALL", True
    elif opcode == 0x0f:
        label = "FENCE"
    else:
        raise ValueError(f"Unsupported instruction {ins:08x} at {pc:08x}")
    if value is not None and rd:
        w.writes.append([[f"r{rd:02}.reg"],[value]])
    w.writes.append([["pc.reg"],[next_pc]])
    return {"label":label,"bits":f"{ins:08x}","steps":w.steps,"writes":w.writes,"trap":trap}


def load_elf(path, compile_code=True,optimize=False):
    """ELF32 little-endian RV32 executable, PT_LOAD memory and executable sections."""
    blob = open(path,"rb").read()
    if blob[:6] != b"\x7fELF\x01\x01":
        raise ValueError("Expected little-endian ELF32")
    fields = struct.unpack_from("<HHIIIIIHHHHHH",blob,16)
    _,machine,_,entry,phoff,shoff,_,_,phsize,phnum,shsize,shnum,_ = fields
    if machine != 243:
        raise ValueError("Expected a RISC-V ELF")
    memory, instructions, symbols = {},{},{}
    for index in range(phnum):
        typ,offset,vaddr,_,filesz,memsz,flags,align = struct.unpack_from("<IIIIIIII",blob,phoff+index*phsize)
        if typ == 1:
            for i,b in enumerate(blob[offset:offset+filesz]):
                if b:
                    memory[vaddr+i] = b
    sections = [struct.unpack_from("<IIIIIIIIII",blob,shoff+i*shsize) for i in range(shnum)]
    for sec in sections:
        _,typ,flags,addr,offset,size,link,_,_,entsize = sec
        if compile_code and typ == 1 and flags & 4:
            for i in range(0,size-3,4):
                ins = struct.unpack_from("<I",blob,offset+i)[0]
                try:
                    instructions[addr+i] = compile_instruction(addr+i,ins,optimize)
                except ValueError as error:
                    # Padding / inline data can occur in executable sections. Fetching
                    # it traps explicitly; it is never silently treated as a NOP.
                    instructions[addr+i] = {"invalid":str(error)}
        if typ == 2:
            strings = sections[link]
            table = blob[strings[4]:strings[4]+strings[5]]
            for pos in range(offset,offset+size,entsize):
                name,value,_,info,_,_ = struct.unpack_from("<IIIBBH",blob,pos)
                if name:
                    label = table[name:table.find(b"\0",name)].decode()
                    symbols[label] = value
    return {"entry":entry,"memory":memory,"code":instructions,"symbols":symbols,
            "sha256":hashlib.sha256(blob).hexdigest()}
