#!/bin/bash
# Stage 1: load the recovery FPGA bitstream into the Artix-7 fabric (volatile,
# RAM-only — power-cycle reverts).
#
# Tries openFPGALoader first (more reliable), falls back to xc3sprog if not
# available.

set -e

BITSTREAM="${BITSTREAM:-./u64_mk2_artix.bit}"
CABLE_OFL="${CABLE_OFL:-gpiochip}"          # openFPGALoader cable name
CABLE_XC3=${CABLE_XC3:-matrix_creator}      # xc3sprog cable name

if [ ! -f "$BITSTREAM" ]; then
    echo "ERROR: bitstream not found at $BITSTREAM"
    echo "Place u64_mk2_artix.bit in the script dir, or set BITSTREAM env."
    exit 1
fi

echo ">>> Loading $BITSTREAM into FPGA (volatile, no flash writes)…"

if command -v openFPGALoader >/dev/null 2>&1; then
    echo "    Using openFPGALoader (cable=$CABLE_OFL)"
    openFPGALoader --cable "$CABLE_OFL" "$BITSTREAM"
elif command -v xc3sprog >/dev/null 2>&1; then
    echo "    Using xc3sprog (cable=$CABLE_XC3)"
    xc3sprog -c "$CABLE_XC3" -p 0 "$BITSTREAM"
else
    echo "ERROR: neither openFPGALoader nor xc3sprog installed."
    echo "    sudo apt install openfpgaloader xc3sprog"
    exit 1
fi

echo ""
echo "FPGA loaded. The C64U fabric is now running the recovery bitstream."
echo "RISC-V soft-core has nothing to execute yet — DRAM is empty."
echo ""
echo "Proceed to Stage 2: sudo python3 ./recover.py ./ultimate.bin"
