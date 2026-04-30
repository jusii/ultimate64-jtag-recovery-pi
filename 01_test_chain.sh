#!/bin/bash
# Stage 0: smoke-test the JTAG chain to the C64 Ultimate via Pi GPIO.
# Uses xc3sprog to read the IDCODE off the Artix-7. Expected: 0x0362c093 (XC7A50T).
#
# This is read-only — no writes to FPGA fabric, flash, or DRAM. Safe to run
# anytime to verify wiring before doing the real recovery.
#
# Adjust CABLE if your Pi xc3sprog setup uses a different cable name.
# Common defaults: matrix_voyager, sysfsgpio, gpio
CABLE=${CABLE:-matrix_creator}

echo ">>> Reading IDCODE via xc3sprog (cable: $CABLE)…"
echo "    Expected: 0x0362c093 (XC7A50T-FGG484)"
echo ""

xc3sprog -c "$CABLE" -j

echo ""
echo "If you see IDCODE 0x0362c093 (or 0x1362c093 / 0x2362c093 — version field varies),"
echo "the wiring is good and you can proceed to Stage 1 (./02_load_fpga.sh)."
echo ""
echo "If you see 'no chain found' / 0x00000000 / 0xFFFFFFFF: check wiring,"
echo "ground continuity, and that the C64U is powered on."
