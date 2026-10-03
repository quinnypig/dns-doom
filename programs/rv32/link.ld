ENTRY(_start)
SECTIONS {
  . = 0x10000;
  .text : { *(.text.start) *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) *(.srodata .srodata.*) }
  . = ALIGN(16);
  .data : { *(.data .data.*) *(.sdata .sdata.*) }
  .bss : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) }
}
