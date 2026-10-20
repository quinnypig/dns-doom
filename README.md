[![Test and deploy](https://github.com/quinnypig/dns-doom/actions/workflows/ci.yml/badge.svg)](https://github.com/quinnypig/dns-doom/actions/workflows/ci.yml)

Live deployment: https://doom.shitposting.cloud (public read-only view).
Pushes to `main` run validation and automatically deploy on success.
See [CI/CD and operations](deploy/README.md).

# Doom on DNS: the domain name computer

**The live demo renders a verified Doom game tick through stock DNS.**
The latest run executed 1,389,327 RISC-V instructions and 6,962,678 DNS
transactions in **55.63 seconds**, about ten times faster than the original.
All 4,608 pixels, 32 registers, the PC, and checked game state match an
independent emulator. This is a checkpoint demo: scene initialization happens
locally; the next game update and fresh rendering happen through DNS.

```sh
./run-demo.sh             # browser demo at http://localhost:8767
./run-rv32.sh fast        # optimized Doom tick from the CLI
./run-rv32.sh test        # differential tests and live DNS fault checks
```

The browser page shows the Doom frame, live progress, and the verified result
of one game tick. An operator with the interactive key can start or stop a run.
Doom takes about a minute plus zone preparation; this is a computation demo, not
interactive play. The Fibonacci program remains available from the CLI.
Requires Docker, Bash, Python 3 for the web server, and several GB of free RAM.

See [DNS32.md](DNS32.md) for the architecture and [PERFORMANCE.md](PERFORMANCE.md)
for measured optimizations and the BIND/Knot comparison.
[Latest verified evidence](artifacts/live/278dc76d75/index.html).

The original four-bit proof of concept is documented below.

## DNS-1

A working four-bit CPU built from DNS truth tables and DNS memory, clocked by a
fixed wiring client. It runs on unmodified BIND 9. Its first program counts down
from three, conditionally branches, and halts.

This is the first experiment toward the question “can DNS compute Doom?”
It is **not Doom**, and it is **not autonomous computation by DNS resolution
alone**. Python supplies external wiring and a clock. Every gate output comes
from a DNS response; instruction decoding, arithmetic, branch selection, and
halting are implemented by that gate circuit.

## Run it

Requires Docker and Bash. From this directory:

```sh
./run.sh
./run.sh test
./run.sh demo --program programs/overflow.asm --cycles 6 --output /output/overflow
```

The first command builds an image, starts an isolated BIND instance, clocks ten
instructions, saves the evidence, and stops the instance. Runtime containers use
`--network none`: all DNS requests stay on the container's loopback interface.
The initial image build downloads BIND and dnspython.

Open `artifacts/countdown/index.html` to replay the actual responses. The page
has clock controls, registers, program ROM, and every DNS transaction. It is an
offline trace viewer; its JavaScript never executes the CPU.

Other evidence:

- `artifacts/countdown/trace.json`: per-gate answers, query names, timing, DNS
  message sizes, wire values, state updates, and independent final readback.
- `artifacts/countdown/netlist.json`: the complete fixed NAND circuit.
- `artifacts/countdown/bind-*/`: initial zone, BIND configuration, query log, and
  dynamic-update journal. Updated memory may be in the journal rather than the
  initial zone text.

Each demo creates a new BIND directory; rerunning replaces the replay/trace with
the latest run and retains the older BIND evidence directories.

## Architecture and what counts as computation

There are 160 NAND gates in the compiled circuit. Every gate sends a direct,
uncached TXT query to BIND for one of these four records:

```dns
0.0.nand.cpu.test.  0 IN TXT "1"
0.1.nand.cpu.test.  0 IN TXT "1"
1.0.nand.cpu.test.  0 IN TXT "1"
1.1.nand.cpu.test.  0 IN TXT "0"
```

NAND gates compose into the opcode decoder, ripple-carry adders, zero detector,
branch multiplexers, and halt latch. Gate outputs remain strings, `"0"` or `"1"`,
in the clock client. It concatenates input bits into query names and copies the
answers along the statically compiled wires.

Every tick executes this same schedule:

1. Read the nine register bits from `state.ram.cpu.test`.
2. Read the zero and one constants from DNS.
3. Copy the four PC bits into a ROM query name and retrieve six instruction bits.
4. Issue all 160 NAND queries in netlist order.
5. Copy the nine output bits into an RFC 2136 UPDATE for `state.ram.cpu.test`.

The update includes the previous TXT value as a prerequisite and atomically
replaces the register state. The intended execution model is one clock writer;
the prerequisite catches a stale value but is not a general multi-writer/ABA
solution. A failed or malformed answer aborts the tick; there is no local CPU
fallback. UPDATE uses TCP; gate and fetch queries use UDP. All queries go
directly to the authoritative BIND server, with recursion disabled.

There are **165 DNS transactions per clock**, even after HALT. The halt bit
causes the DNS circuit to select the existing registers again. Python does not
branch on that bit.

The client does retain temporary wire values during a tick; it implements the
physical wiring analogue. Persistent architectural state is in DNS. Numeric
conversion is used in the offline assembler, test oracle, and display only.

## Instruction set

The machine has a four-bit accumulator A, a four-bit PC, a halt bit, and a
16-word ROM. This prototype has no separate addressable data RAM or load/store
instructions. Its writable DNS memory is the register record.

| Opcode | Instruction | Behavior |
| --- | --- | --- |
| 0 | `LDI n` | Load A with n; increment PC |
| 1 | `ADDI n` | Add n to A modulo 16; increment PC |
| 2 | `JNZ n` | Set PC to n if A is nonzero, otherwise increment PC |
| 3 | `HALT` | Set halt; retain PC and A |

The PC also wraps modulo 16. Once halted, all registers remain fixed regardless
of the fetched instruction.

The assembler accepts labels, semicolon comments, and operands from 0 to 15.
Unused ROM addresses contain HALT. TXT bit encodings are least-significant bit
first: state is `PC[0:4], A[0:4], HALT`; instructions are `opcode[0:2], immediate[0:4]`.

```asm
        LDI 3
again:  ADDI 15
        JNZ again
        HALT
```

Expected states after each clock:

```text
Clock  Instruction   A   PC   Halt
    1  LDI 3         3    1      0
    2  ADDI 15       2    2      0
    3  JNZ 1         2    1      0
    4  ADDI 15       1    2      0
    5  JNZ 1         1    1      0
    6  ADDI 15       0    2      0
    7  JNZ 1         0    3      0
    8  HALT          0    3      1
    9  HALT          0    3      1
   10  HALT          0    3      1
```

## Prove the memory survives the client

After building with `./run.sh`, keep one BIND container running and launch
separate clock processes against it:

```sh
docker run -d --name dns1-lab --network none dns1:local serve --directory /tmp/machine
docker exec dns1-lab python -m dnscpu clock --cycles 2
docker exec dns1-lab dig @127.0.0.1 -p 15353 state.ram.cpu.test TXT +short
docker exec dns1-lab python -m dnscpu clock --cycles 6
docker exec dns1-lab dig @127.0.0.1 -p 15353 state.ram.cpu.test TXT +short
docker stop dns1-lab
docker rm dns1-lab
```

Wait for the server's startup message in `docker logs dns1-lab` before clocking.
The second client resumes from the DNS state left by the first. No state file
or Python process memory is shared between them.

## Validation and measured limits

`./run.sh test` runs ten tests:

- Exhaustive **offline** netlist verification of all 32,768 combinations of
  PC, accumulator, halt, opcode, and operand against an independent ISA model.
- **Live BIND** checks of gate truth values, the countdown, overflow, PC wrap,
  taken/untaken jumps, halted behavior, and continuation across client processes.
- A falsifiability check: change DNS's `NAND(1,1)` record from `0` to `1`; the
  circuit produces incorrect state. Restore the record and normal behavior returns.
- Malformed DNS gate data aborts without committing state; stale UPDATE
  prerequisites are rejected.
- Assembler validation and ROM padding.

An initial local BIND 9.20.29 run completed ten clocks / 1,650 transactions in
about 0.41 seconds (~24 clocks/second). That is a four-bit CPU instruction rate,
**not a Doom frame rate**. Report timing includes client work and state updates,
but excludes BIND startup, assembly, and offline visualization. Network-byte
totals count DNS message payloads, not UDP/TCP/IP overhead or startup/readback.

This construction establishes an externally clocked DNS circuit can execute
programs with writable registers and conditional control flow. It does not
establish practical Doom performance, DNS-only universality, or sufficient
addressable memory for a game engine. Next steps would be data RAM/load-store
instructions, wider words, and gate-depth batching before a Doom target.

## Code map

- `dnscpu/clock.py`: the small, auditable execution path.
- `dnscpu/circuit.py`: compile a fixed circuit from wire-level NAND operations.
- `dnscpu/transport.py`: actual DNS QUERY/UPDATE messages; no cache or truth table.
- `dnscpu/server.py`: ordinary BIND configuration and zone records.
- `dnscpu/assembler.py`: offline assembly and display conversion.
- `tests/test_cpu.py`: independent reference model and live integration checks.
- `dns_compute_probe.py`: earlier static DNAME parity experiment.

Background: [How Complex Is DNS?](https://www.microsoft.com/en-us/research/publication/how-complex-is-dns/),
[Reachability Analysis of the Domain Name System](https://arxiv.org/abs/2411.10188),
[DNS dynamic updates / RFC 2136](https://www.rfc-editor.org/rfc/rfc2136.html).

## Evidence verification without a live DNS run

`make check` builds the existing native transport for the selected Python ABI and
runs the existing offline circuit, RV32 differential, demo and transport tests.
It also checks the retained current tick against independent saved registers,
PC, game state, and all 4,608 raw pixel bytes. No DNS server, Docker benchmark,
full Doom run, or production session is started by this target. The existing
full live-validation and deployment workflow remains separate.

`.fleet/recorded-results.json` pins the exact retained output/oracle bytes and
machine-readable headline claims. Missing or changed artifacts, disagreement
with the oracle, unsupported claims, and README headline drift fail validation.
These integrity checks are not an authenticity signature. The original run's
source revision was not recorded and remains explicitly unknown.

`make evidence` additionally requires a clean checkout and writes ignored
`.fleet-evidence/inputs.json` and `release.json`. The receipt's source revision
identifies the current verifier and CPU checks, not the historical DNS run.
Its hashed input manifest binds every tracked source/artifact file and the
current toolchain; verification rejects changed inputs, missing checks,
unsupported versions, or promotion of CPU success into live-DNS/deployment proof.
The PR evidence workflow retains sanitized hash/reference receipts for 14 days.
`live-dns-rerun`, `full-doom-rerun`, and deployment remain `not-observed`.

The separate Component contract PR job validates declarations with the canonical
fleet JSON Schema pinned at `977e851fbaacabb250b14ed2bb28c6aff9187e85`.
The vendored schema and provenance are checked against the pinned SHA-256 before
installing `jsonschema[format]==4.26.0`; no private repository credentials or
sibling checkout are needed. Negative fixtures cover path traversal, unsupported
versions/profiles, malformed schemas, and schema artifact tampering. These are
real CI checks; repository branch-protection enforcement is not asserted here.
