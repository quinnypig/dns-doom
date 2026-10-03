"""Offline assembler and display helpers. Never used to choose runtime behavior."""

OPS = {"LDI": 0, "ADDI": 1, "JNZ": 2, "HALT": 3}


def bits(value, width):
    return format(value, f"0{width}b")[::-1]


def number(value):
    return int(value[::-1], 2)


def assemble(source):
    labels, lines = {}, []
    for line in source.splitlines():
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        if ":" in line:
            label, line = line.split(":", 1)
            if label.strip() in labels:
                raise ValueError(f"Duplicate label: {label}")
            labels[label.strip()] = len(lines)
            line = line.strip()
        if line:
            lines.append(line.split())
    if len(lines) > 16:
        raise ValueError("The ROM has 16 addresses")
    result = []
    for words in lines:
        op = words[0].upper()
        if op not in OPS or len(words) != (1 if op == "HALT" else 2):
            raise ValueError(f"Invalid instruction: {' '.join(words)}")
        token = words[1] if len(words) == 2 else "0"
        operand = labels[token] if token in labels else int(token, 0)
        if not 0 <= operand < 16:
            raise ValueError("Operands must fit in four bits (0..15)")
        result.append(bits(OPS[op], 2) + bits(operand, 4))
    return result + [bits(3, 2) + bits(0, 4)] * (16 - len(result))


def decode(instruction):
    op = list(OPS)[number(instruction[:2])]
    return op if op == "HALT" else f"{op} {number(instruction[2:])}"


def describe(state):
    return {"pc": number(state[:4]), "acc": number(state[4:8]), "halted": number(state[8:])}
