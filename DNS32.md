# DNS-32: compiled Doom instructions through DNS

This extends the four-bit DNS-1 experiment into a 32-bit RISC-V machine with
addressable RAM. It runs an ordinary RV32I binary compiled from PureDOOM's C
engine. Unmodified BIND stores the registers, RAM, instruction wiring, and
byte-wide arithmetic truth tables. An external Python sequencer connects them.

**Original verified result:** BIND executed **1,389,327 instructions** and **16,173,013 DNS
transactions** in **576.75 seconds** to advance one game tick and render a
96×48 E1M1 view. All 4,608 pixel indices, all 32 registers, the final PC, and the
checked game-state fields match the independent emulator. Doom's game tick
advanced from 1 to 2 and its view angle changed in response to Right.

Open [the standalone evidence page](artifacts/dns32-tick/index.html),
[the DNS run report](artifacts/dns32-tick/run.json), or
[the comparison results](artifacts/dns32-tick/verification.json).

**Optimized result:** the same tick now takes **55.63 seconds**, with
6,962,678 DNS transactions, 1,854,410 round trips, and 284,757 block clocks. The
same pixel and machine state checks pass.
See [PERFORMANCE.md](PERFORMANCE.md) for controlled benchmarks and tradeoffs.
The instruction-by-instruction architecture below describes the baseline;
optimized mode batches wiring and packs architectural state as described there.

The experiment has two distinct execution modes:

* **Cold boot:** starts at the ELF entry point. Doom initialization itself runs
  through DNS. The recorded three-minute run reaches the original startup
  banner and screen allocation; it does not reach a frame.
* **Checkpoint:** an explicitly labeled, independent local emulator initializes
  E1M1. It clears the viewport, saves the machine immediately before the second
  `D_DoomLoop` call, then computes a reference result. BIND loads that initial
  state and executes the game-loop call. The resulting view is read back from
  DNS and compared byte for byte with the independent result.

The checkpoint is a way to test real game logic and rendering without waiting
hours for startup. It is not evidence of an end-to-end DNS boot, and the
reference image alone is not a DNS result. Check `reference_pixels_match`,
`status`, `engine_before`, and `engine_after` in the DNS run report.

## Reproduce

Requires Docker, Bash, several GB of available RAM, and patience. The vendored
PureDOOM source and shareware WAD are already present. Image builds download
Debian packages; execution uses `--network none` and container-local DNS.

```sh
./run-demo.sh             # live browser UI, localhost:8767
./run-rv32.sh fast        # optimized checkpoint execution
./run-rv32.sh fibonacci   # C Fibonacci program, entirely through DNS
./run-rv32.sh test        # independent differential tests + live BIND tests
./run-rv32.sh tick        # prepare reference checkpoint, then DNS game update
./run-rv32.sh cold        # three-minute bounded cold boot
```

Runtime arguments may follow the mode, for example:

```sh
./run-rv32.sh cold --seconds 14400 --steps 100000000
```

`tick` defaults to a 30-minute execution bound. A limit is not a successful
frame: inspect `artifacts/dns32-tick/run.json`. Rebuilding the binary invalidates
old checkpoints if its SHA-256 changes. The script regenerates them.

## What actually computes?

The offline compiler translates each static RV32I instruction into a schedule
of string copies, slices, DNS queries, and state writes. That wiring plan is
itself stored in a TXT record. At runtime the sequencer reads the PC, fetches
its plan through DNS, follows its wires, and commits register/memory changes
using an RFC 2136 UPDATE with the previous PC as a prerequisite.

Example byte addition:

```text
ff.01.0.add.rv.test.   TXT "001"
```

The response means result byte `00`, carry `1`. Four such DNS lookups compose a
32-bit addition. Further tables implement subtraction, bit operations,
comparisons, multiplexers, and shifts. There are 1,114,896 immutable ALU
records. Branch selection, variable addresses, and variable arithmetic depend
on their answers. No local ALU or query cache exists in `runtime.py` or
`bus.py`. Removing or corrupting the answers breaks execution.

Memory consists of byte-valued TXT records, with a wildcard supplying zero for
unwritten addresses. For example, `0070d43c.mem.rv.test` is a RAM byte;
`r10.reg.rv.test` is a register and `pc.reg.rv.test` is the program counter.
All requests go directly to authoritative BIND over persistent TCP. This is
not recursive DNS computation: Python supplies sequencing, temporary wires,
JSON-plan interpretation, and the external clock.

The offline compiler folds constants such as immediates and sequential PCs.
Instruction decoding is compiled in advance; this is a static executable
machine, not an implementation of instruction decoding by DNS gates. Writable
code and self-modifying programs are unsupported. The supported guest target
is bare-metal RV32I; it has no OS, interrupts, privilege model, atomics, or
compressed instructions. Multiply/divide use compiler-supplied RV32I routines.

Host ECALL devices supply console output, read-only WAD file I/O, deterministic
time, exit, and framebuffer export. File DMA copies bytes into DNS records;
PNG output expands palette indices after reading them back. Allocation, game
logic, texture lookup, software rendering, and compiler-generated arithmetic
remain guest instructions. The I/O layer does not render a Doom scene.

## Doom adapter

`programs/rv32/doom.c` includes the unmodified PureDOOM header, supplies device
callbacks and a 16 MB bump allocator, and launches E1M1 without sound/music.
The checkpoint variant uses Doom's `screenblocks=3` setting for a 96×48 game
view inside the normal 320×200 framebuffer. It skips the cosmetic screen-melt
transition and presses Right before the measured second game update. The
viewport is explicitly zeroed before saving the initial checkpoint, preventing
an old reference-rendered image from satisfying the pixel comparison.

## Evidence and limits

* `artifacts/dns32-fibonacci/run.json`: 132 instructions, 1,620 DNS transactions,
  and the twelve expected Fibonacci numbers, ending at 89.
* `artifacts/dns32-doom/run.json`: bounded cold boot, 512,410 instructions and
  5,125,077 transactions in 180 seconds.
* `artifacts/tick-checkpoint/`: **local reference** initialization, saved memory,
  and expected view. Its metadata binds the snapshot to the executable hash.
* `artifacts/dns32-tick/`: DNS execution report, progress, BIND log, final view,
  raw palette indices, and comparison result. Its
  `index.html` embeds the report and image for offline viewing.
  The measured run's verbose update log is compressed as `named.log.gz`.
* `artifacts/all-tests.log`: 12 test methods, including exhaustive DNS-1
  verification and 5,000 randomized RV32I differential cases.
* `artifacts/dns32-live-tests.json`: 80 live BIND instruction cases and a
  deliberately corrupted AND table that changes the computed result.

Timing excludes compilation, table generation, zone loading, and final image
export. Transaction counts include final readback. Payload bytes count DNS
messages, excluding TCP/IP framing. Reports retain a bounded sample of request
names and periodic counters, not a full packet transcript of millions of
queries. Dynamic BIND zones/journals live in container shared memory and are
removed with the container; the saved checkpoint is the initial reference
state, not a saved final DNS machine.

The run wrapper allocates up to 4 GB of container shared memory for BIND's zone
and update journal. Successful-update chatter is suppressed in new runs;
warnings/errors are retained in `named.log` on the host. An earlier attempt
with a 1 GB allowance failed during UPDATE after about 1.15 million
instructions. Its progress/log are retained separately, and it is not counted
as a completed game update. Each run now also preserves its exact `program.elf`.

This is an extremely slow computation experiment, not an interactive game or
a public DNS service. It demonstrates an externally clocked machine whose
variable arithmetic and persistent state are provided by DNS responses.

## Source map and attribution

* `dns32/compile.py`, `tables.py`: offline instruction wiring and truth tables.
* `dns32/runtime.py`, `bus.py`: runtime wiring and DNS transport.
* `dns32/server.py`: stock BIND configuration and initial zone generation.
* `dns32/devices.py`: host peripherals only.
* `dns32/reference.py`: separate local RV32I emulator used for validation and
  checkpoint preparation; never imported by the DNS runtime.
* `dns32/checkpoint.py`: explicit snapshot loading and image readback.
* `dns32/report.py`: offline evidence-page export after execution.

Engine: [PureDOOM](https://github.com/Daivuk/PureDOOM), vendored at commit
`355cfbd16fac119718879239336ee2ea408886bd`. Its GPLv2 license and source remain
in `vendor/PureDOOM/`. The bundled `doom1.wad` is the upstream shareware game
data; the engine's source license does not relicense those assets.
