# DNS Doom performance and live demo

The final browser run completed the same 1,389,327-instruction Doom tick in
**146.28 seconds**, compared with the original 576.75 seconds. It issued
8,206,070 DNS transactions, 5,055,888 network batches, and 507,332 block clocks.
All 4,608 viewport pixels, all 32 registers, the PC, and checked game-state
fields match the independent local emulator. Execution timing includes periodic
DNS framebuffer previews but excludes zone preparation and container startup.
Allow a few minutes for a fresh browser run.

[Final report](artifacts/live/278dc76d75/run.json) ·
[Evidence page](artifacts/live/278dc76d75/index.html) ·
[Browser screenshot](artifacts/performance/public-complete.png)

## Try it

```sh
./run-demo.sh                   # open http://localhost:8767
./run-rv32.sh fast              # rebuild fixture and execute optimized Doom tick
./run-rv32.sh fibonacci         # small CPU demonstration
./run-rv32.sh test              # unit, differential, and live server tests
./benchmark-dns.sh              # three-repeat performance matrix
```

The UI starts fresh isolated containers, shows actual instruction progress and
DNS framebuffer previews, and offers Stop and downloadable evidence. The quick
Fibonacci run demonstrates a complete small program. Doom advances an initialized
E1M1 checkpoint by one game tick and renders a 96×48 view. A complete Doom cold
boot through DNS remains unfinished; this is not a playable streaming game.

The persistent deployment is https://doom.shitposting.cloud through a named
Cloudflare Tunnel. Browser access uses a private cookie; credentials stay on the
production host. See [deployment operations](deploy/README.md).

## What changed

Profiling 50,000 baseline instructions attributed 36.24 of 56.11 profiled seconds
to state commits, largely Python DNS UPDATE construction/serialization; socket
receive accounted for 9.38 seconds. Profiling adds substantial overhead, so these
are diagnostic figures rather than benchmark timings.

* A C++ extension encodes DNS messages and batches buffered TCP transport.
  Python still supplies the external clock and host peripherals.
* Dependency-independent DNS lookups are pipelined. Arithmetic and RAM answers
  are never cached. Only decoding of identical static wiring-plan text is cached;
  the plan itself is fetched from DNS at every clock.
* All registers and the PC occupy one 264-character TXT value. RFC 2136 updates
  require the previous complete value, catching stale state even at the same PC.
* Offline wiring optimization removes duplicate expressions and uses direct
  fixed shifts plus 16-bit constant-offset truth tables. Optimized mode has
  2,884,368 immutable arithmetic records; the baseline has 1,114,896.
* Up to 16 straight-line instructions share a wiring plan. State commits at
  block boundaries; stores, control flow, and host calls terminate blocks.
  Nonleader addresses retain individual plans for branches into a block.
  An instruction limit can consequently overshoot by up to 15 instructions.

Every guest arithmetic result still comes from authoritative DNS TXT answers.
Memory remains DNS TXT records and changes through RFC 2136. Local string slices
and copies connect wires; no local guest ALU was substituted. Registers within a
block are temporary wires until the atomic architectural-state commit.

## Measurements

The prefix benchmark starts from the same snapshot, executes 20,000 instructions,
and checks every register, the PC, and every written RAM byte against an
independent emulator. It restores state between three repeats. Setup, restore,
and verification are excluded from the timer. Intermediate rows record the
implementation at that stage; the final row includes all subsequent changes.

| Optimization stage (BIND) | Median seconds / 20,000 instructions |
| --- | ---: |
| Python baseline | 8.344 |
| Native DNS transport | 4.984 |
| Pipelining | 4.652 |
| Initial wiring optimization | 4.359 |
| Packed registers | 3.959 |
| Final blocks, wider tables, buffered transport | **2.100** |

The final controlled prefix is **3.97× faster** and uses 120,308 transactions
versus 235,538. Reports are in `artifacts/performance/<stage>/benchmark.json`,
with the last result under `final-blocks`.

Knot was implemented and passed correctness tests, but was dramatically slower
for this unusually large, continuously updated zone. Its bounded **100-instruction**
benchmark took 24.946 seconds with Python and 32.908 seconds with native packed
state (three-repeat medians). These use a different instruction count from the
BIND table and are not general DNS-server throughput comparisons. The UI therefore
allows Knot for Fibonacci but selects BIND for Doom. The precise cause of Knot's
large-zone UPDATE cost has not been established.

## Validation

The suite passed 17 test methods, including 15,000 randomized instruction cases
across baseline, optimized, and packed execution, plus 1,000 randomized blocks.
Both BIND and Knot passed 80 live instruction cases and fault checks. Those checks
include corrupt/missing DNS answers, stale PC, and stale packed registers with an
unchanged PC. The full final Doom run passed the independent pixel and state
comparison. Public HTTPS browser tests exercised fresh Fibonacci and Doom runs,
progress, evidence links, mobile layout, and JavaScript error checks; a separate
immediate-stop test verified container cleanup.

Relevant evidence is under `artifacts/performance/`, especially
`v3-unit-tests.log`, `v3-bind-live.json`, `v3-knot-live.json`, and
`public-final-test.log`. The original checkpoint ELF is preserved as
`artifacts/tick-checkpoint/program.elf`; reference generation emits its own expected
state so a rebuilt fixture can also be verified.
