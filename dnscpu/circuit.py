"""Compile wiring, never execute CPU arithmetic here at runtime.

All functions below manipulate *wire names*. The output is a fixed NAND
netlist: no Python callback or expression is embedded in that netlist.
"""

import json
from pathlib import Path


class Circuit:
    def __init__(self):
        self.gates = []
        self.memo = {}
        self.stage = "decode"

    def nand(self, a, b):
        key = tuple(sorted((a, b)))
        if key not in self.memo:
            name = f"g{len(self.gates):03d}"
            self.gates.append({"out": name, "a": a, "b": b, "stage": self.stage})
            self.memo[key] = name
        return self.memo[key]

    def inv(self, a):
        return self.nand(a, a)

    def both(self, a, b):
        return self.inv(self.nand(a, b))

    def either(self, a, b):
        return self.nand(self.inv(a), self.inv(b))

    def xor(self, a, b):
        t = self.nand(a, b)
        return self.nand(self.nand(a, t), self.nand(b, t))

    def mux(self, selector, off, on):
        return self.nand(self.nand(off, self.inv(selector)), self.nand(on, selector))

    def add(self, left, right):
        carry = "zero"
        result = []
        for a, b in zip(left, right):
            x = self.xor(a, b)
            result.append(self.xor(x, carry))
            carry = self.nand(self.nand(a, b), self.nand(x, carry))
        return result


def compile_cpu():
    c = Circuit()
    pc = [f"p{i}" for i in range(4)]
    acc = [f"a{i}" for i in range(4)]
    imm = [f"i{i}" for i in range(4)]
    load = c.both(c.inv("op1"), c.inv("op0"))
    add = c.both(c.inv("op1"), "op0")
    jump = c.both("op1", c.inv("op0"))
    halt = c.both("op1", "op0")

    c.stage = "ALU"
    summed = c.add(acc, imm)
    loaded = [c.mux(load, a, i) for a, i in zip(acc, imm)]
    next_acc = [c.mux(add, a, s) for a, s in zip(loaded, summed)]

    c.stage = "branch"
    nonzero = c.either(c.either(acc[0], acc[1]), c.either(acc[2], acc[3]))
    take_jump = c.both(jump, nonzero)
    incremented = c.add(pc, ["one", "zero", "zero", "zero"])
    next_pc = [c.mux(take_jump, p, i) for p, i in zip(incremented, imm)]

    c.stage = "latch"
    stopped = c.either("halted", halt)
    next_pc = [c.mux(stopped, p, old) for p, old in zip(next_pc, pc)]
    next_acc = [c.mux("halted", a, old) for a, old in zip(next_acc, acc)]
    return {
        "name": "DNS-1",
        "state_inputs": pc + acc + ["halted"],
        "instruction_inputs": ["op0", "op1"] + imm,
        "gates": c.gates,
        "state_outputs": next_pc + next_acc + [stopped],
    }


def write_netlist(path):
    Path(path).write_text(json.dumps(compile_cpu(), indent=2) + "\n")
