"""An offline evidence replay. It never emulates or executes the CPU."""

import json
from pathlib import Path


def render(report):
    payload = json.dumps(report).replace("<", "\\u003c")
    return Path(__file__).with_name("replay.html").read_text().replace("__TRACE_DATA__", payload)
