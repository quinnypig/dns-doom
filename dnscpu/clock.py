"""The complete CPU runtime: fixed wiring and clock, without an opcode interpreter.

Bit strings remain opaque symbols. This module does not convert them to numbers,
calculate arithmetic, implement NAND, or branch on an opcode/ALU result/halt bit.
The same gates and queries execute on every clock, including after HALT.
"""

import time


def check_bits(value, length):
    # Protocol validation only; invalid data aborts instead of changing CPU behavior.
    if len(value) != length or set(value) - {"0", "1"}:
        raise ValueError(f"Invalid {length}-bit DNS value: {value!r}")
    return value


def tick(dns, netlist):
    start = time.perf_counter()
    first_event = len(dns.events)
    old = check_bits(dns.txt("state.ram", stage="memory"), 9)
    wires = dict(zip(netlist["state_inputs"], old))
    wires["zero"] = check_bits(dns.txt("zero.const", stage="constant"), 1)
    wires["one"] = check_bits(dns.txt("one.const", stage="constant"), 1)
    address = ".".join(wires[p] for p in netlist["state_inputs"][:4])
    instruction = check_bits(dns.txt(address + ".rom", stage="fetch"), 6)
    wires.update(zip(netlist["instruction_inputs"], instruction))

    for gate in netlist["gates"]:
        name = wires[gate["a"]] + "." + wires[gate["b"]] + ".nand"
        wires[gate["out"]] = check_bits(dns.txt(name, gate=gate["out"],
                                               stage=gate["stage"]), 1)

    new = "".join(wires[w] for w in netlist["state_outputs"])
    dns.replace("state.ram", new, previous=old)
    return {"before": old, "after": new, "instruction": instruction,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            "wires": wires, "events": dns.events[first_event:]}
