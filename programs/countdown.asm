; DNS-1: count down to zero, branch in DNS, then halt.
        LDI 3
again:  ADDI 15       ; Four-bit addition: adding 15 subtracts one, modulo 16.
        JNZ again
        HALT
