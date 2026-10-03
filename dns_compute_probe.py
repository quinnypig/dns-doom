"""Run inside a disposable container with Python, BIND 9, and dig installed.

Tests computation performed by stock BIND using static DNAME records.
Python generates inputs and checks answers; it does not serve DNS.
Reference: https://www.microsoft.com/en-us/research/publication/how-complex-is-dns/
"""

import itertools
import pathlib
import subprocess
import tempfile
import time


with tempfile.TemporaryDirectory(prefix="dns-compute-") as directory:
    root = pathlib.Path(directory)
    records = [
        "$ORIGIN compute.test.",
        "$TTL 60",
        "@ IN SOA ns.compute.test. hostmaster.compute.test. (1 60 60 60 60)",
        "@ IN NS ns.compute.test.",
        "ns IN A 127.0.0.1",
        'end.even IN TXT "0"',
        'end.odd IN TXT "1"',
        "0.even IN DNAME even.compute.test.",
        "1.even IN DNAME odd.compute.test.",
        "0.odd IN DNAME odd.compute.test.",
        "1.odd IN DNAME even.compute.test.",
    ]
    (root / "zone").write_text("\n".join(records) + "\n")
    (root / "named.conf").write_text(
        'options { directory "' + directory + '"; '
        'listen-on port 15353 { 127.0.0.1; }; listen-on-v6 { none; }; '
        'recursion no; dnssec-validation no; pid-file "' + directory + '/named.pid"; } ;\n'
        'controls {};\n'
        'zone "compute.test" { type primary; file "zone"; };\n'
    )
    subprocess.run(["named-checkconf", "-z", str(root / "named.conf")], check=True)
    print(subprocess.check_output(["named", "-v"], text=True).strip(), flush=True)
    with (root / "named.log").open("w") as log:
        server = subprocess.Popen(
            ["named", "-g", "-n", "1", "-c", str(root / "named.conf")],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            def query(bits, short=True):
                name = ".".join(["end", *bits, "even", "compute", "test", ""])
                command = ["dig", "@127.0.0.1", "-p", "15353", name, "TXT",
                           "+tcp", "+time=1", "+tries=1", "+norecurse"]
                command += ["+short"] if short else ["+noall", "+comments", "+answer"]
                return subprocess.run(command, text=True, capture_output=True, check=True).stdout

            for attempt in range(50):
                try:
                    if '"0"' in query(""):
                        break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError((root / "named.log").read_text())

            count = 0
            for length in range(9):
                for bits in itertools.product("01", repeat=length):
                    result = query(bits)
                    expected = str(sum(map(int, bits)) % 2)
                    assert result.splitlines()[-1] == '"' + expected + '"', (bits, result)
                    count += 1
            print(f"PASS: {count} inputs of length 0..8, parity computed by BIND", flush=True)
            print("Example: parity(1011)")
            print(query("1011", short=False))
            for length in [12, 16, 20, 32, 64]:
                result = query("1" * length, short=False)
                status = next(line for line in result.splitlines() if "status:" in line)
                print(f"Rewrite limit probe, {length} input bits: {status}")
        finally:
            server.terminate()
            server.wait(timeout=10)
