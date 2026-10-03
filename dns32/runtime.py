"""Generic wiring sequencer. No ISA decoding, integer ALU, or table generator.

All CPU bit strings remain opaque. The only variable decisions here select
q/c wiring primitives supplied by DNS, or hand explicit ECALLs to host I/O.
"""

import json
import time


def expand(parts, wires):
    result = []
    for part in parts:
        if isinstance(part,str):
            result.append(part)
        elif len(part) == 1:
            result.append(wires[part[0]])
        else:
            result.append(wires[part[0]][part[1]:part[2]])
    return "".join(result)


def step(bus, io=None):
    pc = bus.txt("pc.reg")
    plan = json.loads(bus.txt(pc + ".code"))
    if "invalid" in plan:
        raise RuntimeError(plan["invalid"])
    wires = {}
    for kind,out,parts in plan["steps"]:
        value = expand(parts,wires)
        if kind == "q":
            value = bus.txt(value)
        elif kind != "c":
            raise ValueError(f"Unknown wiring primitive {kind}")
        wires[out] = value
    changes = [(expand(name,wires),expand(value,wires)) for name,value in plan["writes"]]
    bus.commit(changes,pc)
    if plan["trap"]:
        if io is None:
            raise RuntimeError("ECALL without a host I/O device")
        io.trap(bus)
    return pc,plan["label"]
