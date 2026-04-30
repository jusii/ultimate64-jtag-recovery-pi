#!/bin/bash
# Master end-to-end recovery: stage 0 (chain test) + stage 1 (FPGA) + stage 2 (DRAM).
#
# Run on the Pi with GPIO already wired to the C64U's P5 JTAG header.
# C64U should be powered on but not booted (it's bricked anyway).

set -e

cd "$(dirname "$0")"

echo "============================================================"
echo " Commodore 64 Ultimate / Ultimate 64 Elite II — JTAG recovery via Pi GPIO"
echo "============================================================"
echo ""

echo "[0/3] Smoke-testing JTAG chain…"
./01_test_chain.sh
read -rp "Did you see the expected IDCODE (0x?362c093 etc)? [y/N] " ok
[[ "$ok" =~ ^[yY]$ ]] || { echo "Aborting. Check wiring."; exit 1; }

echo ""
echo "[1/3] Loading FPGA recovery bitstream…"
./02_load_fpga.sh

echo ""
echo "[2/3] Stage 2: uploading ultimate.bin to DRAM and booting…"
sudo python3 ./recover.py ./ultimate.bin

echo ""
echo "============================================================"
echo " Recovery flow done."
echo " Now check the C64U — HDMI menu should be up."
echo " From the menu, run a known-good update.ue2 from a USB stick"
echo " to PERMANENTLY restore flash (e.g. /SD/Firmware/c64u_v1.1.0.ue2)."
echo "============================================================"
