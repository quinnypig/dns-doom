"""Host I/O only: console, read-only WAD, clock and framebuffer output.

DMA address iteration and pixel palette expansion are I/O serialization. No
game logic, ALU, allocator, libc implementation, or renderer lives here.
"""

from pathlib import Path
import struct
import zlib


def png(path,pixels,palette,width,height):
    rows=[]
    for y in range(height):
        row=b"".join(palette[p*3:p*3+3] for p in pixels[y*width:(y+1)*width])
        rows.append(b"\0"+row)
    def chunk(kind,data):
        return struct.pack("!I",len(data))+kind+data+struct.pack("!I",zlib.crc32(kind+data)&0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack("!IIBBBBB",width,height,8,2,0,0,0))+
                          chunk(b"IDAT",zlib.compress(b"".join(rows)))+chunk(b"IEND",b""))


class Devices:
    def __init__(self,out,wad="vendor/PureDOOM/doom1.wad"):
        self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        self.wad=Path(wad)
        self.files={}
        self.next_handle=1
        self.ms=0
        self.exit_code=None
        self.frames=0
        self.messages=[]

    def read(self,bus,address,size):
        if not 0<=size<=16*1024*1024:
            raise ValueError("Invalid I/O byte count")
        return bytes(int(bus.txt(f"{address+i:08x}.mem"),16) for i in range(size))

    def string(self,bus,address):
        data=[]
        for i in range(8192):
            b=int(bus.txt(f"{address+i:08x}.mem"),16)
            if b==0:return bytes(data).decode("utf8",errors="replace")
            data.append(b)
        raise ValueError("Unterminated I/O string")

    def write(self,bus,address,data):
        for start in range(0,len(data),200):
            bus.commit([(f"{address+i:08x}.mem",f"{data[i]:02x}")
                        for i in range(start,min(start+200,len(data)))])

    def trap(self,bus):
        n=int(bus.txt("r17.reg"),16)
        a,b,c,d=[int(bus.txt(f"r{i:02}.reg"),16) for i in range(10,14)]
        result=0
        if n==0:
            self.exit_code=a
        elif n==1:
            text=self.string(bus,a)
            self.messages.append(text)
            print(text,end="",flush=True)
            with (self.out/"console.txt").open("a") as f:f.write(text)
        elif n==2:
            if not 0<c<=1024 or not 0<d<=1024:raise ValueError("Invalid framebuffer dimensions")
            pixels=self.read(bus,a,c*d);palette=self.read(bus,b,768)
            self.frames+=1
            png(self.out/f"frame-{self.frames:03}.png",pixels,palette,c,d)
        elif n==3:
            name=self.string(bus,a);mode=self.string(bus,b)
            if name.replace("\\","/").split("/")[-1].lower()=="doom1.wad" and mode.startswith("r"):
                result=self.next_handle;self.next_handle+=1
                self.files[result]=self.wad.open("rb")
        elif n==4:
            data=self.files[a].read(c)
            self.write(bus,b,data);result=len(data)
        elif n==5:
            offset=b if b<0x80000000 else b-0x100000000
            self.files[a].seek(offset,c)
        elif n==6:result=self.files[a].tell()
        elif n==7:self.files.pop(a).close()
        elif n==8:self.ms+=1;result=self.ms
        elif n==9:
            f=self.files[a];pos=f.tell();result=int(not f.read(1));f.seek(pos)
        else:raise ValueError(f"Unsupported host I/O call {n}")
        bus.commit([("r10.reg",f"{result & 0xffffffff:08x}")])

    def close(self):
        for f in self.files.values():f.close()
