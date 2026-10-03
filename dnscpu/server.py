"""Create an isolated zone and run an unmodified BIND process."""

import pathlib
import subprocess
import tempfile
import time

import dns.exception

from .assembler import bits
from .transport import DNS


def configure(directory, program, port=15353, state="000000000", nand=None):
    root = pathlib.Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "named.conf").exists():
        raise ValueError(f"Refusing to overwrite an existing machine: {root}")
    table = nand or {"00": "1", "01": "1", "10": "1", "11": "0"}
    records = [
        "$ORIGIN cpu.test.", "$TTL 0",
        "@ IN SOA ns.cpu.test. hostmaster.cpu.test. (1 60 60 60 0)",
        "@ IN NS ns.cpu.test.", "ns IN A 127.0.0.1",
        'zero.const IN TXT "0"', 'one.const IN TXT "1"',
        f'state.ram IN TXT "{state}"',
    ]
    for inputs, output in table.items():
        records.append(f'{".".join(inputs)}.nand IN TXT "{output}"')
    for address, instruction in enumerate(program):
        records.append(f'{".".join(bits(address, 4))}.rom IN TXT "{instruction}"')
    (root / "cpu.zone").write_text("\n".join(records) + "\n")
    (root / "named.conf").write_text('''options {
    directory "%s";
    listen-on port %d { 127.0.0.1; };
    listen-on-v6 { none; };
    recursion no;
    dnssec-validation no;
    empty-zones-enable no;
    allow-query { 127.0.0.1; };
    allow-transfer { none; };
    pid-file "%s/named.pid";
    session-keyfile "%s/session.key";
    querylog yes;
};
controls {};
zone "cpu.test" {
    type primary;
    file "cpu.zone";
    allow-update { 127.0.0.1; };
    notify no;
};
''' % (root, port, root, root))
    return root


class Server:
    def __init__(self, program, directory=None, port=15353, state="000000000", nand=None):
        self.temporary = tempfile.TemporaryDirectory(prefix="dns1-") if directory is None else None
        self.root = configure(directory or self.temporary.name, program, port, state, nand)
        self.port = port
        self.process = None
        self.log = None

    def __enter__(self):
        subprocess.run(["named-checkconf", "-z", str(self.root / "named.conf")],
                       check=True, capture_output=True, text=True)
        self.log = (self.root / "named.log").open("w")
        self.process = subprocess.Popen(
            ["named", "-g", "-n", "1", "-c", str(self.root / "named.conf")],
            stdout=self.log, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                DNS(port=self.port).txt("state.ram")
                return self
            except (OSError, RuntimeError, dns.exception.DNSException):
                time.sleep(0.05)
        self.close()
        raise RuntimeError(f"BIND did not start:\n{(self.root / 'named.log').read_text()}")

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()

    def __exit__(self, *exc):
        self.close()
        if self.temporary:
            self.temporary.cleanup()
