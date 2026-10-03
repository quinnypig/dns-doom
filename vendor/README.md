PureDOOM is vendored from https://github.com/Daivuk/PureDOOM at commit
355cfbd16fac119718879239336ee2ea408886bd. Its source license is preserved in
PureDOOM/LICENSE. The local RV32 build includes PureDOOM/PureDOOM.h directly.

PureDOOM/doom1.wad is the Doom shareware data required for the E1M1 demo; it is
explicitly tracked despite the upstream project's WAD ignore rule. It is game
data, not covered by the source-code license. The native extension, compiler
outputs, checkpoints, and DNS zones are regenerated during CI and deployment.
