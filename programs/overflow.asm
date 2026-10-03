; Overflow happens in the DNS gates, not in the clock client.
        LDI 15
        ADDI 1
        JNZ failure
        LDI 10
        HALT
failure: LDI 13
        HALT
