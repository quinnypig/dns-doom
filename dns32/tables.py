"""Generate immutable byte truth tables offline. Runtime has no import of this module."""

def lookup(name):
    """Truth-table definition, also used by the offline test oracle only."""
    parts = name.split(".")
    op = parts[-1]
    if op == "cond":
        value, cond = parts[:2]
        return str(int({"eq":value == "eq", "ne":value != "eq",
                        "lt":value == "lt", "ge":value != "lt"}[cond]))
    if op == "cond4":
        return lookup(next((v for v in parts[:4] if v != "eq"), "eq")+"."+parts[4]+".cond")
    if op == "cond2":
        return lookup(next((v for v in parts[:2] if v != "eq"), "eq")+"."+parts[2]+".cond")
    if op in ("cmp16","scmp16"):
        a,b=int(parts[0],16),int(parts[1],16)
        if op == "scmp16":
            a = a-65536 if a & 32768 else a
            b = b-65536 if b & 32768 else b
        return "lt" if a < b else "gt" if a > b else "eq"
    a = int(parts[0],16)
    if op in ('inc16','dec16'):
        return f"{(a+int(parts[1])*(1 if op=='inc16' else -1))&65535:04x}"
    if op=='add16':
        b=int(parts[1],16);b=b-(1<<32) if b&(1<<31) else b
        result=a+b;carry=result<0 if b<0 else result>65535
        return f'{result&65535:04x}{int(carry)}'
    if op == "sign": return "ff" if a & 128 else "00"
    if op == "clearlsb": return f"{a & 254:02x}"
    if op == "shiftbits": return f"{a & 31:05b}"[::-1]
    b = int(parts[1],16)
    if op in ("add","sub"):
        c = int(parts[2])
        result = a+b+c if op == "add" else a-b-c
        carry = result > 255 if op == "add" else result < 0
        return f"{result & 255:02x}{int(carry)}"
    if op == "and": return f"{a & b:02x}"
    if op == "or": return f"{a | b:02x}"
    if op == "xor": return f"{a ^ b:02x}"
    if op == "mux": return f"{b if parts[2] == '1' else a:02x}"
    if op in ("cmp","scmp"):
        if parts[2] != "eq": return parts[2]
        if op == "scmp":
            a = a-256 if a & 128 else a
            b = b-256 if b & 128 else b
        return "lt" if a < b else "gt" if a > b else "eq"
    if op == "shl":
        shift = int(parts[2])
        return f"{((a << shift) | (b >> (8-shift))) & 255:02x}"
    if op == "shr":
        shift = int(parts[2])
        return f"{((a >> shift) | (b << (8-shift))) & 255:02x}"
    raise ValueError(name)


def records():
    from .compile import FAST_OFFSETS,word
    for a in range(65536):
        for offset in FAST_OFFSETS:
            name=f'{a:04x}.{word(offset)}.add16';yield name,lookup(name)
        for op in ('inc16','dec16'):
            for c in (0,1):
                name=f'{a:04x}.{c}.{op}';yield name,lookup(name)
    for m in ("lt","eq","gt"):
        for x in ("lt","eq","gt"):
            for y in ("lt","eq","gt"):
                for z in ("lt","eq","gt"):
                    for cond in ("eq","ne","lt","ge"):
                        name=f"{m}.{x}.{y}.{z}.{cond}.cond4";yield name,lookup(name)
    for m in ("lt","eq","gt"):
        for x in ("lt","eq","gt"):
            for cond in ("eq","ne","lt","ge"):
                name=f"{m}.{x}.{cond}.cond2";yield name,lookup(name)
    for a in range(65536):
        for op in ("cmp16","scmp16"):
            for name in (f"{a:04x}.0000.{op}",f"0000.{a:04x}.{op}"):yield name,lookup(name)
    for value in ("lt","eq","gt"):
        for cond in ("eq","ne","lt","ge"):
            name = f"{value}.{cond}.cond"
            yield name, lookup(name)
    for a in range(256):
        for op in ("sign","clearlsb","shiftbits"):
            name = f"{a:02x}.{op}"
            yield name,lookup(name)
    for op in ("cmp","scmp"):
        yield f"*.lt.{op}","lt"
        yield f"*.gt.{op}","gt"
    for a in range(256):
        for b in range(256):
            base = f"{a:02x}.{b:02x}"
            for op in ("and","or","xor"):
                name = f"{base}.{op}"
                yield name,lookup(name)
            for op in ("add","sub","mux"):
                for carry in (0,1):
                    name = f"{base}.{carry}.{op}"
                    yield name,lookup(name)
            for op in ("cmp","scmp"):
                name = f"{base}.eq.{op}"
                yield name,lookup(name)
            for shift in range(1,8):
                for op in ("shl","shr"):
                    name = f"{base}.{shift}.{op}"
                    yield name,lookup(name)
