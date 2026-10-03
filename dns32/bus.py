"""Persistent TCP DNS transport. Parsing/serialization only; no guest arithmetic."""

import socket
import struct
import time

import dns.message
import dns.rcode
import dns.update

ZONE = "rv.test."


class Bus:
    def __init__(self,port=15354,host="127.0.0.1",sample_limit=200):
        self.socket = socket.create_connection((host,port),timeout=10)
        self.sequence = 0
        self.transactions = self.payload_bytes = 0
        self.samples = []
        self.sample_limit = sample_limit

    def close(self):
        self.socket.close()

    def _read(self,count):
        parts=[]
        while count:
            part=self.socket.recv(count)
            if not part:
                raise ConnectionError("DNS connection closed")
            parts.append(part)
            count-=len(part)
        return b"".join(parts)

    def exchange(self,wire,name):
        self.socket.sendall(struct.pack("!H",len(wire))+wire)
        size=struct.unpack("!H",self._read(2))[0]
        reply=self._read(size)
        self.transactions+=1
        self.payload_bytes+=len(wire)+size
        ident,flags,qd,an,ns,ar=struct.unpack_from("!6H",reply)
        if reply[:2]!=wire[:2] or not flags & 0x8000 or flags & 15:
            raise RuntimeError(f"DNS {dns.rcode.to_text(flags & 15)} for {name}")
        if len(self.samples)<self.sample_limit:
            self.samples.append({"name":name,"request_bytes":len(wire),"response_bytes":size})
        return reply

    @staticmethod
    def skip_name(data,offset):
        while data[offset]:
            length=data[offset]
            if length & 0xc0 == 0xc0:
                return offset+2
            if length & 0xc0:
                raise ValueError("Unsupported DNS label")
            offset+=length+1
        return offset+1

    def txt(self,name):
        self.sequence=(self.sequence+1)&65535
        fqdn=name+"."+ZONE
        encoded=b"".join(bytes([len(label)])+label.encode("ascii") for label in fqdn.rstrip(".").split("."))+b"\0"
        wire=struct.pack("!6H",self.sequence,0,1,0,0,0)+encoded+b"\0\x10\0\x01"
        data=self.exchange(wire,name)
        _,flags,questions,answers,_,_=struct.unpack_from("!6H",data)
        if not flags & 0x0400 or questions!=1:
            raise ValueError("Expected an authoritative DNS response")
        offset=self.skip_name(data,12)+4
        values=[]
        for _ in range(answers):
            offset=self.skip_name(data,offset)
            typ,cls,ttl,size=struct.unpack_from("!HHIH",data,offset)
            offset+=10
            end=offset+size
            if typ==16:
                chunks=[]
                while offset<end:
                    length=data[offset]
                    chunks.append(data[offset+1:offset+1+length])
                    offset+=length+1
                values.append(b"".join(chunks).decode("ascii"))
            offset=end
        if len(values)!=1:
            raise ValueError(f"Expected one TXT response: {name}")
        return values[0]

    def commit(self,changes,previous_pc=None):
        message=dns.update.Update(ZONE)
        if previous_pc is not None:
            message.present("pc.reg","TXT",'"'+previous_pc+'"')
        for name,value in changes:
            message.replace(name,0,"TXT",'"'+value+'"')
        self.exchange(message.to_wire(),"UPDATE")
