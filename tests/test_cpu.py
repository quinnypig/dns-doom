import itertools
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dnscpu.assembler import assemble, bits, describe
from dnscpu.circuit import compile_cpu
from dnscpu.clock import tick
from dnscpu.server import Server
from dnscpu.transport import DNS


def reference(pc, acc, halted, opcode, immediate):
    """Independent ISA specification, used only as a test oracle."""
    if halted:
        return pc, acc, halted
    if opcode == 0:
        return (pc + 1) % 16, immediate, 0
    if opcode == 1:
        return (pc + 1) % 16, (acc + immediate) % 16, 0
    if opcode == 2:
        return immediate if acc else (pc + 1) % 16, acc, 0
    return pc, acc, 1


class CircuitTests(unittest.TestCase):
    def test_all_32768_input_combinations(self):
        circuit = compile_cpu()
        # Offline verification only. Production tick() has no truth table.
        truth = {("0", "0"): "1", ("0", "1"): "1",
                 ("1", "0"): "1", ("1", "1"): "0"}
        for pc, acc, halt, op, imm in itertools.product(range(16), range(16), range(2), range(4), range(16)):
            state = bits(pc, 4) + bits(acc, 4) + bits(halt, 1)
            instruction = bits(op, 2) + bits(imm, 4)
            wires = dict(zip(circuit["state_inputs"], state))
            wires.update(zip(circuit["instruction_inputs"], instruction))
            wires.update(zero="0", one="1")
            for gate in circuit["gates"]:
                wires[gate["out"]] = truth[wires[gate["a"]], wires[gate["b"]]]
            result = describe("".join(wires[w] for w in circuit["state_outputs"]))
            expected = reference(pc, acc, halt, op, imm)
            self.assertEqual(tuple(result.values()), expected, (pc, acc, halt, op, imm))

    def test_assembler_rejects_invalid_programs(self):
        for source in ["LDI 16", "ADDI -1", "NOPE", "HALT 2", "LDI", "x: HALT\nx: HALT", "HALT\n" * 17]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                assemble(source)

    def test_labels_and_rom_padding(self):
        rom = assemble("LDI 3\nloop: ADDI 15\nJNZ loop\nHALT")
        self.assertEqual(rom[:4], ["001100", "101111", "011000", "110000"])
        self.assertEqual(len(rom), 16)
        self.assertTrue(all(word == "110000" for word in rom[4:]))


class LiveDNSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = assemble(Path("programs/countdown.asm").read_text())
        cls.server = Server(cls.program, port=16353)
        cls.server.__enter__()
        cls.netlist = compile_cpu()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def setUp(self):
        self.dns = DNS(port=16353)
        self.dns.replace("state.ram", "000000000")
        for address, instruction in enumerate(self.program):
            self.dns.replace(".".join(bits(address, 4)) + ".rom", instruction)
        self.dns.events.clear()

    def test_truth_table_is_served_by_bind(self):
        for inputs, expected in [("00", "1"), ("01", "1"), ("10", "1"), ("11", "0")]:
            self.assertEqual(self.dns.txt(".".join(inputs) + ".nand"), expected)

    def test_countdown_and_halt_have_identical_wiring(self):
        states = []
        for _ in range(10):
            row = tick(self.dns, self.netlist)
            states.append(tuple(describe(row["after"]).values()))
            self.assertEqual(len(row["events"]), len(self.netlist["gates"]) + 5)
            self.assertEqual([e["gate"] for e in row["events"] if "gate" in e],
                             [g["out"] for g in self.netlist["gates"]])
        self.assertEqual(states, [(1,3,0), (2,2,0), (1,2,0), (2,1,0), (1,1,0),
                                  (2,0,0), (3,0,0), (3,0,1), (3,0,1), (3,0,1)])
        self.assertEqual(DNS(port=16353).txt("state.ram"), "110000001")

    def test_live_boundaries_overflow_branches_and_pc_wrap(self):
        for pc, acc, halted, op, imm in [
            (15,15,0,1,1), (15,0,0,0,15), (2,0,0,2,9), (2,1,0,2,9),
            (7,6,0,3,0), (7,6,1,0,15), (7,6,1,1,15), (7,6,1,2,15),
            (0,7,0,1,9), (0,8,0,1,8), (0,0,0,1,15), (15,0,0,2,0),
        ]:
            with self.subTest(pc=pc, acc=acc, halted=halted, op=op, imm=imm):
                self.dns.replace("state.ram", bits(pc,4) + bits(acc,4) + str(halted))
                self.dns.replace(".".join(bits(pc,4)) + ".rom", bits(op,2) + bits(imm,4))
                row = tick(self.dns, self.netlist)
                self.assertEqual(tuple(describe(row["after"]).values()), reference(pc,acc,halted,op,imm))

    def test_separate_clock_processes_resume_dns_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in range(2):
                path = Path(directory) / f"clock{index}.json"
                subprocess.run([sys.executable, "-m", "dnscpu", "clock", "--port", "16353",
                                "--cycles", "1", "--trace", str(path)], check=True, capture_output=True)
                rows = json.loads(path.read_text())
                self.assertEqual(len(rows), 1)
            self.assertEqual(describe(self.dns.txt("state.ram")), {"pc":2,"acc":2,"halted":0})

    def test_corrupt_dns_gate_changes_the_result(self):
        # Falsifiability: a hidden local NAND implementation would ignore this change.
        self.dns.replace("1.1.nand", "1")
        try:
            row = tick(self.dns, self.netlist)
            self.assertEqual(row["after"], "111111111")
            self.assertNotEqual(row["after"], "100011000")
        finally:
            self.dns.replace("1.1.nand", "0")

    def test_bad_dns_data_aborts_without_committing(self):
        self.dns.replace("1.1.nand", "broken")
        try:
            with self.assertRaises(ValueError):
                tick(self.dns, self.netlist)
            self.assertEqual(self.dns.txt("state.ram"), "000000000")
        finally:
            self.dns.replace("1.1.nand", "0")

    def test_atomic_update_rejects_stale_state(self):
        self.dns.replace("state.ram", "100000000", previous="000000000")
        with self.assertRaisesRegex(RuntimeError, "NXRRSET"):
            self.dns.replace("state.ram", "010000000", previous="000000000")
        self.assertEqual(self.dns.txt("state.ram"), "100000000")


if __name__ == "__main__":
    unittest.main()
