"""Offline zone generation, then an unmodified BIND server."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .tables import records
from .bus import Bus
from .compile import word


def txt_record(name,value):
    # BIND concatenates these <=255-byte strings into one TXT record.
    chunks=[value[i:i+200] for i in range(0,len(value),200)] or [""]
    return name+' IN TXT '+" ".join(json.dumps(chunk) for chunk in chunks)+"\n"


def prepare(root,image,tables_path=None):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    tablefile=Path(tables_path) if tables_path else root/"tables.zone"
    if not tablefile.exists():
        print("Generating byte ALU truth tables (offline)...",flush=True)
        with tablefile.open("w") as f:
            count=0
            for name,value in records():
                f.write(txt_record(name,value))
                count+=1
        print(f"Generated {count:,} immutable DNS records",flush=True)
    with (root/"machine.zone").open("w") as f:
        f.write('$ORIGIN rv.test.\n$TTL 0\n@ IN SOA ns.rv.test. hostmaster.rv.test. (1 60 60 60 0)\n@ IN NS ns.rv.test.\nns IN A 127.0.0.1\n')
        f.write(f'$INCLUDE "{tablefile.resolve()}"\n')
        f.write(txt_record("*.mem","00"))
        if image.get("packed"):
            f.write(txt_record("state.reg",word(image["entry"])+''.join(word(v) for v in image.get("registers",[0]*32))))
        else:
            f.write(txt_record("pc.reg",word(image["entry"])))
            for i in range(32):
                f.write(txt_record(f"r{i:02}.reg",word(image.get("registers",[0]*32)[i])))
        for address,value in image["memory"].items():
            f.write(txt_record(word(address)+".mem",f"{value:02x}"))
        for address,plan in image["code"].items():
            f.write(txt_record(word(address)+".code",json.dumps(plan,separators=(",",":"))))
    (root/"named.conf").write_text('''options {
 directory "%s";
 listen-on port 15354 { 127.0.0.1; }; listen-on-v6 { none; };
 recursion no; dnssec-validation no; empty-zones-enable no;
 allow-query { 127.0.0.1; }; allow-transfer { none; };
 pid-file "%s/named.pid"; session-keyfile "%s/session.key";
 tcp-idle-timeout 1200; tcp-keepalive-timeout 1200;
 %s
};
controls {};
logging {
 channel diagnostics { stderr; severity info; print-time yes; };
 channel update_warnings { stderr; severity warning; print-time yes; };
 category default { diagnostics; };
 category update { update_warnings; };
 category update-security { update_warnings; };
};
zone "rv.test" { type primary; file "machine.zone";
 allow-update { 127.0.0.1; }; notify no; };
'''%(root.resolve(),root.resolve(),root.resolve(),os.environ.get('DNS32_NAMED_OPTIONS','')))


class Server:
    def __init__(self,image,root=None,tables_path=None,log_path=None,backend="bind"):
        self.root=Path(root or tempfile.mkdtemp(prefix="dns32-",dir="/dev/shm"))
        self.log_path=Path(log_path) if log_path else self.root/"named.log"
        prepare(self.root,image,tables_path)
        self.backend=backend
        self.ready_name='state.reg' if image.get('packed') else 'pc.reg'
        if backend=="knot":
            (self.root/"db").mkdir(exist_ok=True)
            (self.root/"knot.conf").write_text(f'''server:
    listen: 127.0.0.1@15354
    rundir: "{self.root.resolve()}"
    udp-workers: 1
    tcp-workers: 1
    background-workers: 1
    tcp-idle-timeout: 1200
database:
    storage: "{self.root.resolve()}/db"
    journal-db-max-size: 3G
log:
  - target: stderr
    any: warning
acl:
  - id: local_update
    address: 127.0.0.1
    action: update
zone:
  - domain: rv.test
    file: "{self.root.resolve()}/machine.zone"
    storage: "{self.root.resolve()}"
    acl: local_update
    zonefile-sync: -1
    journal-max-usage: 1G
    journal-max-depth: 10000000
''')
        self.process=None
        self.log=None

    def __enter__(self):
        self.log=self.log_path.open("w")
        # -g overrides configured logging; -f stays foreground and honors it.
        command=(["knotd","-c",str(self.root/"knot.conf")] if self.backend=="knot" else
                 ["named","-f","-n",os.environ.get("DNS32_NAMED_THREADS","1"),"-c",str(self.root/"named.conf")])
        self.process=subprocess.Popen(command,
                                      stdout=self.log,stderr=subprocess.STDOUT)
        for _ in range(600):
            if self.process.poll() is not None:
                raise RuntimeError(f"{self.backend} exited ({self.process.returncode}): {self.log_path.read_text()}")
            try:
                b=Bus()
                b.txt(self.ready_name)
                b.close()
                print(f"{self.backend} ready: 127.0.0.1:15354",flush=True)
                return self
            except (OSError,RuntimeError):
                time.sleep(.1)
        raise RuntimeError("BIND startup timed out")

    def __exit__(self,*exc):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait()
        if self.log:self.log.close()
