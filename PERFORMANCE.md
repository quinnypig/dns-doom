# DNS Doom performance and live demo

The verified browser run completes the same 1,389,327-instruction Doom tick in
**55.63 seconds**, compared with 146.28 seconds for the previous optimized run
and 576.75 seconds for the original. It issued 6,962,678 DNS transactions in
1,854,410 network round trips over 284,757 block clocks. All 4,608 viewport
pixels, all 32 registers, the PC, and the checked game-state fields match the
independent local emulator. Execution timing includes periodic DNS framebuffer
previews but excludes zone preparation and container startup.

The ceiling is structural. Each dependent DNS lookup costs a few microseconds
inside named plus a round trip, and Doom is a long chain of dependent loads,
compares, and branches. This is a turn-based demo: press a key, wait most of a
minute, see the frame. It is not playable and no amount of batching makes it so.

[Previous report](artifacts/live/278dc76d75/run.json) ·
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

### Round two (55.63 s): fewer DNS transactions, fewer commits, cheaper transport

The second optimization pass started from a wrong model. Profiling suggested
the machine was latency-bound on dependent round trips, so the first changes
converted dependency depth into batch width: carry-lookahead adds (three stages
instead of four), parallel byte addresses for loads and stores, two-stage
comparisons, and predicated short forward branches. Round trips fell by 2.4×
and the time barely moved. A transport microbenchmark (`dns32/probe.py`)
explained why: with a 6.4-million-record zone, named spends about 7 µs per TCP
query and 4 µs per UDP query, each RFC 2136 UPDATE costs 65 µs, and the fixed
cost of a round trip is only about 8 µs. The machine is bound by BIND's
per-message work, so the right trade is fewer messages, not shallower chains.

* **Fewer lookups per instruction.** Carry-lookahead adds were reverted to
  ripple carry (4 lookups instead of 9). The four byte comparisons of a branch
  are followed by one lookup that both combines them and applies the condition.
  Branches against `x0`, the common `beqz`/`bnez` forms, use 16-bit half
  tables: three lookups instead of five. Target bytes shared by both branch
  outcomes are copied rather than selected. The 64 most frequent constant
  offsets in the tick, measured with `dns32/offsets.py`, use the two-lookup
  16-bit add path (78% of constant adds, up from 42%).
* **Fewer commits.** Stores no longer end blocks unless a later instruction in
  the block loads. Short forward branches over straight-line arms are
  predicated: the arm's wiring always runs and each register it writes is
  selected by the `mux` table on the branch condition, so the block continues
  through the join point. Registers may then be carried as wires across up to
  eight blocks before an UPDATE; any block that writes memory or traps commits
  immediately, so DNS memory is always current and host I/O only sees
  committed state. The UPDATE prerequisite names the last state DNS accepted,
  so a diverged commit is still rejected by the server. Block clocks fell from
  507,332 to 284,757 and commits fell further.
* **Cheaper transport.** The next block's plan is fetched in the same round
  trip as the commit, and the state record is not re-read after our own write.
  Table and memory lookups travel over UDP, which costs named about 40% less
  per message than TCP; lost datagrams are re-sent (none were observed on
  loopback). `minimal-responses yes` trims each answer. The wiring loop itself
  (string slicing, DNS framing, batching) runs in the C++ extension; plans are
  decoded once per distinct text and still fetched from DNS at every clock.
* **Not worth it, measured.** Extra named threads with one UDP lane each gained
  under 5% because stages average fewer than four independent lookups. Fetching
  a plan record (median 985 bytes, p90 2,260) costs 1 µs more than a small
  answer, so a compact plan encoding would save about 0.5%. Moving the tables
  into a separate static zone would cut UPDATE cost from 65 µs to 40 µs but
  commits are now rare enough that this is a few percent.

Over the 20,000-instruction prefix the table and memory lookups per instruction
(excluding plan fetches and commits) fell from 6.0 to 4.7, and the commits from one
per block to about one per four blocks.

### Round one (146.28 s): native transport, pipelining, packed state, blocks

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
  fixed shifts plus 16-bit constant-offset truth tables. The current table set
  has 6,358,136 immutable records; round one had 2,884,368 and the baseline
  1,114,896.
* Up to 16 straight-line instructions share a wiring plan. Nonleader addresses
  retain individual plans for branches into a block. An instruction limit can
  consequently overshoot by up to 15 instructions.

Every guest arithmetic result still comes from authoritative DNS TXT answers.
Memory remains DNS TXT records and changes through RFC 2136. Local string slices
and copies connect wires; no local guest ALU was substituted. Registers within a
block, and between blocks until the next commit, are temporary wires.

## Measurements

The prefix benchmark starts from the same snapshot, executes 20,000 instructions,
and checks every register, the PC, and every written RAM byte against an
independent emulator. It restores state between three repeats. Setup, restore,
and verification are excluded from the timer. Intermediate rows record the
implementation at that stage; each row includes all rows above it.

| Optimization stage (BIND) | Median seconds / 20,000 instructions |
| --- | ---: |
| Python baseline | 8.344 |
| Native DNS transport | 4.984 |
| Pipelining | 4.652 |
| Initial wiring optimization | 4.359 |
| Packed registers | 3.959 |
| Final blocks, wider tables, buffered transport (round one) | 2.100 |
| Commit+prefetch, lookahead wiring, stores in blocks, hammocks | 2.023 |
| Native wiring loop | 1.750 |
| Ripple adds restored, folded conditions, shared target bytes | 1.460 |
| `minimal-responses yes` | 1.349 |
| Registers carried across up to 8 blocks | 1.104 |
| UDP lookups | 0.882 |
| Branches against x0 via 16-bit halves (fewer lookups, time within noise) | 0.886 |
| Review fixes: reply question validation, final commit inside the timer | **0.916** |

The final prefix is **9.4× faster than the Python baseline** and 2.4× faster
than round one, using 100,805 transactions versus 120,308. Round-two reports are in
`artifacts/performance/<stage>/benchmark.json` (`t1-5-combined` through `t10-zero`);
round-one artifacts were not kept in the repository. The first of the three repeats
is consistently the slowest because plan decoding is cold; the median reports the
warm steady state. The timer includes the final register commit.

Knot was implemented and passed correctness tests, but was dramatically slower
for this unusually large, continuously updated zone. Its bounded **100-instruction**
benchmark took 24.946 seconds with Python and 32.908 seconds with native packed
state (three-repeat medians). These use a different instruction count from the
BIND table and are not general DNS-server throughput comparisons. The UI therefore
allows Knot for Fibonacci but selects BIND for Doom. The precise cause of Knot's
large-zone UPDATE cost has not been established.

## Validation

The suite passed 36 test methods, including 15,000 randomized instruction cases
across baseline, optimized, and packed execution, 1,000 randomized straight-line
blocks, 300 blocks containing stores, and 400 predicated branch hammocks checked
on both the taken and not-taken paths with per-path instruction accounting.
Both BIND and Knot passed 80 live instruction cases and fault checks, and BIND
passed them again through the native UDP sequencer. Those checks include
corrupt/missing DNS answers, a stale PC (rejected client-side before the commit),
and stale packed registers with an unchanged PC (rejected by the server). The full final Doom run passed the independent pixel and state
comparison. Public HTTPS browser tests exercised fresh Fibonacci and Doom runs,
progress, evidence links, mobile layout, and JavaScript error checks; a separate
immediate-stop test verified container cleanup.

Relevant evidence is under `artifacts/performance/`, especially
`final-tests.log`, `bind-live.json`, `bind-live-native.json`, and `knot-live.json`. The original checkpoint ELF is preserved as
`artifacts/tick-checkpoint/program.elf`; reference generation emits its own expected
state so a rebuilt fixture can also be verified.
