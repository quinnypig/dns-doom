# Doom on DNS: feature outline

## DNS records as a computer

DNS records are a key-value store: ask for a name, receive its value. A truth table is key-value data too: the name encodes inputs, the value supplies their result. The useful analogy is a logic circuit whose gates are lookup tables and whose wires carry strings. A client copies an answer into the next query's name, connecting those gates into arithmetic and conditional control flow. RFC 2136 dynamic updates make records writable memory. Stock BIND serves the gates and holds committed state; an external sequencer supplies the wiring and clock. Together, they execute Doom.

- The demonstrated result: one game tick and a fresh E1M1 viewport after pressing Right, executing 1,389,327 RISC-V instructions in 55.63 seconds. The 96×48 view matches an independent emulator pixel for pixel.
- Concrete gate: `ff.01.0.add.rv.test. TXT "001"`. The inputs are hexadecimal bytes `ff`, `01`, and carry-in `0`; the answer contains result byte `00` and carry-out `1`. Copy that carry into the next byte's query. Four chained byte additions implement a general 32-bit addition.

Sources: `DNS32.md`, `dns32/tables.py`, `artifacts/dns32-tick/run.json`.

## From a countdown to Doom

- DNS-1, the earlier four-bit machine, runs a countdown with a conditional jump and halt. Its fixed circuit executes 160 NAND lookups each clock. `1.1.nand.cpu.test. TXT "0"` is an actual gate; `state.ram.cpu.test` holds its registers.
- That circuit includes instruction decoding. DNS-32 changes the tradeoff: decode instructions offline, replace individual NAND gates with wider truth tables, and add addressable RAM so an ordinary compiled C engine can run.

Sources: `README.md`, `dnscpu/clock.py`, `dnscpu/circuit.py`.

## From C source to DNS execution

- GCC compiles the unmodified PureDOOM C engine for bare-metal RV32I. `programs/rv32/doom.c` supplies the platform adapter: allocation, file callbacks, time, console and display export. The adapter selects a small viewport, disables sound/music, skips the cosmetic screen melt and presses Right; engine logic and rendering remain unchanged.
- `dns32/compile.py` decodes the executable offline. Each instruction becomes a JSON wiring plan containing literal strings, copies, slices, DNS queries and writes. Immediate constants and fixed next addresses are resolved here. Optimized plans combine instructions into blocks.
- Zone generation stores plans under instruction addresses. The verified run fetches `00048ddc.code.rv.test.`. Baseline registers include `r10.reg.rv.test.` and `pc.reg.rv.test.`; optimized execution packs the PC and registers into `state.reg.rv.test.`. A byte read uses a name such as `00070e74.mem.rv.test.`. `*.mem.rv.test. TXT "00"` supplies unwritten memory's default value.
- The loop obtains the PC, fetches its plan from DNS, connects the specified strings, queries arithmetic tables and RAM, then commits resulting state. Independent queries can travel together; dependent ones consume earlier answers. Every block's plan is fetched, although decoding identical plan text is cached. Arithmetic and RAM answers are not cached.
- Each commit uses an RFC 2136 prerequisite: replace state only if DNS still contains the expected previous value. Baseline execution checks the old PC; packed execution checks the complete previous register/PC value. This is a server-enforced stale-state check, including register changes that leave the PC unchanged.

Sources: `build-rv32.sh`, `dns32/compile.py`, `runtime.py`, `optimized.py`, `server.py`.

## Where the work happens

- Stock BIND 9.20 supplies authoritative TXT answers and dynamic updates. Server behavior is configured through `named.conf`; generated zone files supply the data. No server patches or computation plugins. Queries go directly to container-local BIND with recursion disabled and external networking disabled.
- Python supplies the outer clock and peripherals; the optimized C++ extension handles string wiring, DNS framing and batching. DNS answers determine variable arithmetic, comparisons, branch selection and computed addresses. Offline constant folding and runtime string copies do not require arithmetic lookups.
- Intermediate registers live as client-side strings within blocks and across up to eight blocks between commits. Memory writes and traps force commits. Persistent committed state lives in DNS; claiming that every intermediate value always resides there would misdescribe the optimization.
- Host devices provide WAD file bytes, deterministic time, console output and framebuffer export. PNG generation expands palette indices read from DNS. Guest instructions perform allocation, game logic, texture lookup and software rendering. Instruction decoding is offline; self-modifying code is unsupported.

Sources: `DNS32.md`, `dns32/server.py`, `optimized.py`, `devices.py`.

## The checkpoint and the evidence

- A local independent emulator initializes E1M1, clears the viewport and saves state immediately before the second `D_DoomLoop` call. DNS loads that checkpoint and executes the call. Clearing the viewport prevents an already rendered reference image from passing the comparison.
- Cold boot through DNS remains unfinished: the bounded 180-second run executed 512,410 instructions, reaching the startup banner and screen allocation, but no frame. The checkpoint avoids waiting hours for initialization while exercising actual game logic and rendering.
- **Fault injection:** `verify_live.py` changes `ff.ff.and` to `"00"`. The all-ones AND instruction then produces `00000000`. Changing DNS changes the computation; a local correct-answer fallback would conceal this fault.
- **Stale-state rejection:** tests submit an obsolete PC and, separately, obsolete packed registers with an unchanged PC. The latter explicitly verifies rejection by DNS.
- **Independent emulator:** separate instruction decoding and native arithmetic produce the expected result. The verified run reports all four match flags true: pixels, registers, PC and checked game-state fields. All 4,608 pixel indices and 32 registers match; `gametic` advances from 1 to 2 and `viewangle` changes.
- The report's `frames: 0` counts host export calls. Execution stops at the checkpoint return; the rendered viewport is then read directly from DNS memory.

Sources: `dns32/reference.py`, `checkpoint.py`, `verify_live.py`, `__main__.py`, `artifacts/dns32-tick/run.json`.

## Making the same tick faster

- **576.75 → 146.28 → 55.63 seconds.** The original tick used 16,173,013 DNS transactions; the latest uses 6,962,678, with 1,854,410 round trips and 284,757 block clocks. Timing excludes preparation; the latest execution includes periodic framebuffer previews.
- First round: replace expensive Python DNS serialization with native transport, pipeline independent lookups, pack registers into one TXT value, optimize wiring and combine up to 16 instructions per block. The controlled 20,000-instruction prefix fell from 8.344 to 2.100 seconds.
- Second round began with the wrong diagnosis: dependency latency. Carry-lookahead addition and wider batches cut round trips by 2.4×; elapsed time barely moved.
- `probe.py` corrected the model. With a roughly 6.4-million-record zone, BIND spent about 7 µs per TCP query, 4 µs per UDP query and 65 µs per UPDATE; fixed round-trip overhead was about 8 µs. Per-message work dominated.
- Winners: restore ripple addition's four lookups instead of lookahead's nine; fold branch conditions; widen tables for frequent constant offsets; reduce commits. Short forward branches execute the arm's wiring and use DNS multiplexers to select its effects. Stores stay inside blocks unless a later load requires a boundary.
- Native wiring, commit-plus-plan prefetch, carried registers, UDP lookups and `minimal-responses yes` complete the gains. Final prefix median: 0.886 seconds, 9.4× faster than baseline.
- Measured alternatives: extra server threads gained under 5%; compact plans promised about 0.5%; separating static tables would save only a few percent. Knot passed correctness checks but performed poorly on this large updated zone: 24.946 seconds for just 100 instructions with Python. That is not a general DNS-server ranking.

Source: `PERFORMANCE.md`.

## Angles and closers

- **Turn-based Doom:** dependent address calculations, loads and branches still require successive DNS answers, each costing microseconds even locally. Batching independent work cannot remove those dependencies. Interactive play is beyond this design; the honest experience is press Right, wait most of a minute, see the result.
- **DNS is a database:** Corey's longstanding thesis becomes executable: names are keys, TXT answers are values, updates provide writes, and prerequisites protect commits. The engineering story is how composing those operations turns stored data into computation.
- **The hypothetical Route 53 bill:** the calculator survives in `dns32/demo.html` at commit `6c7521f`; the current page removed it. Its formula is `(min(q, 1e9) × $0.40 + max(q − 1e9, 0) × $0.20) / 1e6`. Starting at zero monthly usage, `6,962,678 / 1,000,000 × $0.40 = $2.7850712`, approximately **$2.79 per rendered tick**. This reproduces the repo's estimate, counting every transaction as a billable query; it is neither an actual bill nor evidence that this machine runs on Route 53.
