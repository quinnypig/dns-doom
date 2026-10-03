"""Export an offline evidence page after execution; never part of the CPU."""
import base64
import json
from pathlib import Path


def write(directory):
    out=Path(directory)
    report=json.loads((out/"run.json").read_text())
    template=Path(__file__).with_name("report.html").read_text()
    template=template.replace("../dns32-tick/","./")
    # Embed evidence so opening the HTML directly needs no fetch or local server.
    saved=json.dumps(report).replace("<","\\u003c")
    template=template.replace("let report;",f"let report; const saved={saved};")
    template=template.replace("async function read(path){", "async function read(path){if(path.endsWith('/run.json'))return saved;")
    if report.get("view"):
        uri="data:image/png;base64,"+base64.b64encode((out/"dns-view.png").read_bytes()).decode()
        template=template.replace("$('frame').src='./dns-view.png'",f"$('frame').src={json.dumps(uri)}")
    (out/"index.html").write_text(template)
