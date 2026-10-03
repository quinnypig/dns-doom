"""Real DNS transport; no cache, local gate evaluation, or fallback engine."""

import time

import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.rdatatype
import dns.update

ZONE = "cpu.test."


class DNS:
    def __init__(self, host="127.0.0.1", port=15353):
        self.host, self.port = host, port
        self.events = []

    def _exchange(self, message, event, tcp=False):
        start = time.perf_counter()
        exchange = dns.query.tcp if tcp else dns.query.udp
        response = exchange(message, self.host, port=self.port, timeout=3)
        event.update(
            rcode=dns.rcode.to_text(response.rcode()),
            ms=round((time.perf_counter() - start) * 1000, 3),
            request_bytes=len(message.to_wire()), response_bytes=len(response.to_wire()),
        )
        self.events.append(event)
        if response.rcode() != dns.rcode.NOERROR:
            raise RuntimeError(f"DNS {event['rcode']}: {event}")
        return response

    def txt(self, name, **metadata):
        fqdn = name + "." + ZONE
        message = dns.message.make_query(fqdn, "TXT")
        message.flags &= ~dns.flags.RD
        event = {"kind": "QUERY", "name": fqdn, **metadata}
        response = self._exchange(message, event)
        if not response.flags & dns.flags.AA:
            raise RuntimeError("Expected a direct authoritative answer")
        values = [b"".join(rr.strings).decode("ascii")
                  for rrset in response.answer if rrset.rdtype == dns.rdatatype.TXT
                  for rr in rrset]
        if len(values) != 1:
            raise RuntimeError(f"Expected one TXT value for {fqdn}: {values}")
        event["value"] = values[0]
        return values[0]

    def replace(self, name, value, previous=None):
        message = dns.update.Update(ZONE)
        if previous is not None:
            message.present(name, "TXT", '"' + previous + '"')
        message.replace(name, 0, "TXT", '"' + value + '"')
        self._exchange(message, {"kind": "UPDATE", "name": name + "." + ZONE,
                                 "value": value, "previous": previous}, tcp=True)
