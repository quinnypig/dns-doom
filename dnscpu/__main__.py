import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from .assembler import assemble, decode, describe
from .circuit import compile_cpu
from .clock import tick
from .server import Server
from .transport import DNS


def run_clocks(dns, netlist, cycles):
    rows = []
    print("\n  DNS-1 / THE DOMAIN NAME COMPUTER\n", flush=True)
    print(f"  {len(netlist['gates'])} NAND gates · 4-bit accumulator · 16-word ROM")
    print("  Arithmetic, instruction decoding, branches, and HALT: DNS lookups.")
    print("  Registers: a TXT record, latched with an atomic DNS UPDATE.\n")
    print("  CLOCK   PC  INSTRUCTION   ACC BEFORE → AFTER   NEXT PC   HALT      TIME")
    for index in range(cycles):
        row = tick(dns, netlist)
        rows.append(row)
        # Display only: decoded numbers never feed back into tick().
        old, new = describe(row["before"]), describe(row["after"])
        print(f"  {index + 1:5}   {old['pc']:2}  {decode(row['instruction']):11}"
              f"       {old['acc']:2} → {new['acc']:<2}          {new['pc']:2}"
              f"      {new['halted']}     {row['elapsed_ms']:6.1f}ms", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description="DNS-1: clock a CPU made of DNS records")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Start BIND, run a program, save evidence, stop BIND")
    demo.add_argument("--program", default="programs/countdown.asm")
    demo.add_argument("--cycles", type=int, default=10)
    demo.add_argument("--output", default=os.environ.get("DNSCPU_OUTPUT", "artifacts/countdown"))
    serve = commands.add_parser("serve", help="Start a machine for separate clock clients")
    serve.add_argument("--program", default="programs/countdown.asm")
    serve.add_argument("--directory", required=True)
    clock = commands.add_parser("clock", help="Clock an already-running local machine")
    clock.add_argument("--cycles", type=int, default=1)
    clock.add_argument("--port", type=int, default=15353)
    clock.add_argument("--trace")
    commands.add_parser("test", help="Exhaustive circuit checks and live BIND integration tests")
    args = parser.parse_args()
    if hasattr(args, "cycles") and args.cycles < 1:
        parser.error("--cycles must be positive")
    if args.command == "test":
        import unittest
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests"))
        raise SystemExit(not result.wasSuccessful())
    netlist = compile_cpu()
    if args.command == "clock":
        rows = run_clocks(DNS(port=args.port), netlist, args.cycles)
        if args.trace:
            Path(args.trace).write_text(json.dumps(rows, indent=2) + "\n")
        return
    program = assemble(Path(args.program).read_text())
    if args.command == "serve":
        with Server(program, directory=args.directory):
            print("BIND is serving DNS-1 at 127.0.0.1:15353. Ctrl-C to stop.", flush=True)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    # Each run gets its own zone/journal; repeat runs do not reuse stale DNS state.
    import tempfile
    directory = tempfile.mkdtemp(prefix="bind-", dir=out.resolve())
    version = subprocess.check_output(["named", "-v"], text=True).strip()
    (out / "netlist.json").write_text(json.dumps(netlist, indent=2) + "\n")
    with Server(program, directory=directory):
        rows = run_clocks(DNS(), netlist, args.cycles)
        final = DNS().txt("state.ram", stage="independent readback")
    events = [e for row in rows for e in row["events"]]
    report = {"name": "DNS-1", "bind_version": version,
              "program": Path(args.program).read_text(), "rom": program,
              "gates": len(netlist["gates"]), "ticks": rows,
              "final_readback": final, "final": describe(final),
              "transactions": len(events),
              "wire_bytes": sum(e["request_bytes"] + e["response_bytes"] for e in events),
              "elapsed_ms": round(sum(row["elapsed_ms"] for row in rows), 3)}
    (out / "trace.json").write_text(json.dumps(report, indent=2) + "\n")
    from .report import render
    (out / "index.html").write_text(render(report))
    print(f"\n  {len(events):,} real DNS transactions; {report['elapsed_ms'] / 1000:.2f}s.")
    print(f"  Independent DNS readback: {report['final']}")
    print(f"  Replay: {out / 'index.html'}")
    print(f"  Raw traffic: {out / 'trace.json'}\n  BIND evidence: {directory}")


if __name__ == "__main__":
    main()
