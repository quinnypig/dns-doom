.section .text.start
.global _start
_start:
    lui sp, 0x8000
    addi sp, sp, -16
    call main
    li a7, 0
    ecall
1:  j 1b
