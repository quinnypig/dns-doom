"""Explicit reference-origin checkpoints for isolated DNS renderer experiments."""
import gzip
import json
from pathlib import Path
import struct


def save(cpu,image,directory,symbol,devices=None):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    metadata={"origin":"LOCAL REFERENCE INITIALIZATION — not DNS", "symbol":symbol,
              "pc":cpu.pc,"registers":cpu.regs,"return_pc":cpu.regs[1],"reference_instructions":cpu.steps,
              "symbols":image["symbols"],"elf_sha256":image["sha256"],
              "engine_before":{name:cpu.read(image["symbols"][name],4)
                               for name in ("gametic","maketic","viewangle")},
              "devices":{"ms":devices.ms,"next_handle":devices.next_handle,
                         "files":{str(k):v.tell() for k,v in devices.files.items()}} if devices else None}
    (root/"checkpoint.json").write_text(json.dumps(metadata,indent=2))
    with gzip.open(root/"memory.bin.gz","wb") as f:
        for address,value in cpu.memory.items():
            if value:f.write(struct.pack("<IB",address,value))
    return metadata


def restore(image,directory):
    root=Path(directory)
    metadata=json.loads((root/"checkpoint.json").read_text())
    if metadata.get("elf_sha256")!=image["sha256"]:
        raise ValueError("Checkpoint and executable hashes differ")
    memory={}
    with gzip.open(root/"memory.bin.gz","rb") as f:
        data=f.read()
        if len(data)%5:raise ValueError("Truncated checkpoint")
        for address,value in struct.iter_unpack("<IB",data):memory[address]=value
    image["memory"]=memory
    image["entry"]=metadata["pc"]
    image["registers"]=metadata["registers"]
    return metadata


def capture_view(bus,symbols,devices,path):
    from .devices import png
    def integer(name):
        return int.from_bytes(devices.read(bus,symbols[name],4),"little")
    framebuffer=integer("screens")
    # PureDOOM's palette is an inline array, not a pointer.
    palette=devices.read(bus,symbols["screen_palette"],768)
    width,height=integer("viewwidth"),integer("viewheight")
    x,y=integer("viewwindowx"),integer("viewwindowy")
    if not(0<width<=320 and 0<height<=200 and x+width<=320 and y+height<=200):
        raise ValueError(f"Invalid view dimensions {(x,y,width,height)}")
    pixels=b"".join(devices.read(bus,framebuffer+(y+row)*320+x,width) for row in range(height))
    Path(str(path)+".indices").write_bytes(pixels)
    png(path,pixels,palette,width,height)
    return {"width":width,"height":height,"x":x,"y":y,"framebuffer":framebuffer}
