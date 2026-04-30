#!/usr/bin/env python3
"""Soft-reset the Commodore 64 Ultimate / Ultimate 64 Elite II RISC-V soft-core via the JTAG bridge.

Asserts the bridge GPIO output bit 7 (= reset), holds, then releases. DRAM
contents are preserved, so the RISC-V re-boots from whatever was last loaded
via recover.py.

Useful when:
  - HDMI didn't quite come up after recovery; nudge the CPU to retry.
  - You changed something via JTAG-write-memory and want it to take effect.
  - You want to re-enter the recovery menu without redoing Stage 1+2.

Run on the Pi with the Pi's GPIO already wired to the C64U P5 JTAG header
and a recovery FPGA bitstream loaded into the FPGA fabric.

Usage: sudo python3 soft_reset.py
"""

import sys
import time

# Same module as the main recovery tool; we reuse JtagBitbang + U64iiRecovery.
import recover

def main():
    j = recover.JtagBitbang()
    r = recover.U64iiRecovery(j)
    try:
        chip = r.read_idcode()
        if (chip & 0x0FFFFFFF) != 0x0362C093:
            print(f"WARNING: chip IDCODE 0x{chip:08x} is not XC7A50T 0x0362c093 — wiring or chain issue?")
        try:
            pre = r.user_read_id()
            print(f"pre-reset bridge ID: 0x{pre:08x}")
        except Exception as e:
            print(f"pre-reset user_read_id raised {e}; the bridge may not be loaded.")
        r.user_set_outputs(0x80)
        time.sleep(0.2)
        r.user_set_outputs(0x00)
        time.sleep(0.5)
        try:
            post = r.user_read_id()
            print(f"post-reset bridge ID: 0x{post:08x}")
            if post == 0xdead1541:
                print("→ recovery firmware booted (signature 0xdead1541 confirmed).")
        except Exception as e:
            print(f"post-reset user_read_id raised {e}.")
    finally:
        j.close()

if __name__ == "__main__":
    main()
