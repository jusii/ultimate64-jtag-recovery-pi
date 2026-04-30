#!/usr/bin/env python3
"""
Stage-2 recovery for the Commodore 64 Ultimate (original) and the
Ultimate 64 Elite II / Elite Mark II — Gideon Zweijtzer's U64-II
Artix-7 hardware family, driven by Raspberry Pi GPIO bit-banging.

Functional port of the user-side BSCANE2 protocol in
    GideonZ/1541ultimate:recovery/u64ii/recover.py
which targets an FT232H USB MPSSE bridge via pyftdi. This version targets
Pi GPIO directly via RPi.GPIO, suitable for any Pi already wired GPIO ->
JTAG (the standard "Pi as Xilinx programmer" setup that works with
xc3sprog's `matrix_creator` cable).

ATTRIBUTION:
  - Original recovery script and BSCANE2 protocol design:
        Gideon Zweijtzer <https://github.com/GideonZ/1541ultimate>
  - Pi-as-Xilinx-programmer 2025 setup:
        LinuxJedi <https://linuxjedi.co.uk/raspberry-pi-jtag-programming-2025-edition/>
  - MATRIX Labs xc3sprog fork (sysfscreator pinout):
        <https://github.com/matrix-io/xc3sprog>

USAGE (on the Pi, with GPIO wired to the C64U P5 JTAG header):
    sudo python3 recover.py /path/to/ultimate.bin            # full Stage 2
    sudo python3 recover.py /path/to/ultimate.bin --quick    # smoke test only

ASSUMPTION: u64_mk2_artix.bit has already been loaded into the FPGA fabric
via xc3sprog or openFPGALoader (Stage 1). This script is Stage 2: it uploads
ultimate.bin into DRAM via the recovery FPGA's USER4-BSCANE2 bridge, sets
the boot-magic word at 0xFFF8, and unresets the RISC-V soft-core so it
boots from DRAM.

After Stage 2 the C64U comes up with a working recovery menu. From that
menu you run a normal update.ue2 from a USB stick to permanently restore
flash.

WIRING (Pi 40-pin GPIO -> C64U P5 JTAG header):
    Pi BCM 17 (board pin 11)  TCK  ->  P5 pin 1
    Pi BCM 4  (board pin 7)   TMS  ->  P5 pin 5
    Pi BCM 22 (board pin 15)  TDI  ->  P5 pin 9
    Pi BCM 27 (board pin 13)  TDO  ->  P5 pin 3
    Pi GND   (board pin 6/9/etc)   ->  P5 pin 2 or 10
    DO NOT connect 3.3V — the C64U powers its JTAG side from its own PSU.

These BCM pin numbers match xc3sprog's "matrix_creator" cable layout, the
"gpiod_creator" cable in newer xc3sprog builds, and the LinuxJedi 2025
guide. If your wiring uses different pins, adjust TCK_PIN/TMS_PIN/TDI_PIN/
TDO_PIN below.

SAFETY: Stage 2 writes only to DRAM. No flash operations. Worst-case from
a wrong sequence is "device doesn't recover this attempt"; power-cycle
reverts to whatever was in flash before (typically the broken state you
were trying to recover from). Re-running is safe.

License: GPLv3 (matches upstream GideonZ/1541ultimate).
"""

import sys
import struct
import time
import argparse
import logging

try:
    import RPi.GPIO as GPIO
except ImportError:
    sys.stderr.write("ERROR: RPi.GPIO not available. Try: sudo apt install python3-rpi.gpio\n")
    sys.exit(1)

# ---------- Pin assignments (BCM numbers, change to match your wiring) ----------
# Defaults match xc3sprog's "matrix_creator" cable layout — confirmed working on
# this hardware via `xc3sprog -c matrix_creator -j` returning IDCODE 0x0362c093.
# Source: matrix-io/xc3sprog sysfscreator.cpp -> IOSysFsGPIO(4, 17, 22, 27)
# (constructor arg order: TMS, TCK, TDI, TDO).
TMS_PIN = 4
TCK_PIN = 17
TDI_PIN = 22
TDO_PIN = 27

# ---------- Xilinx and bridge constants (from upstream recover.py) ----------
XILINX_USER4 = 0x23   # 6-bit IR opcode that selects USER4 BSCANE2
IRLEN = 6             # XC7A50T instruction register length

# ---------- Logging ----------
log = logging.getLogger("recover")
log.setLevel(logging.INFO)
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)


class JtagBitbang:
    """Minimal JTAG state machine + IR/DR scan over Pi GPIO via RPi.GPIO."""

    # JTAG TAP states we use
    TLR, RTI, SDR, EX1DR, PDR, SIR, EX1IR, PIR = range(8)

    def __init__(self):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TCK_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(TMS_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(TDI_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(TDO_PIN, GPIO.IN)
        self.state = self.TLR

    def close(self):
        GPIO.cleanup([TCK_PIN, TMS_PIN, TDI_PIN, TDO_PIN])

    # ----- low-level bit operations -----

    def _set(self, pin, value):
        GPIO.output(pin, GPIO.HIGH if value else GPIO.LOW)

    def _get_tdo(self):
        return 1 if GPIO.input(TDO_PIN) else 0

    def _clock(self, tms, tdi):
        """One TCK pulse with given TMS+TDI; samples TDO on rising edge."""
        self._set(TMS_PIN, tms)
        self._set(TDI_PIN, tdi)
        # TCK low->high, sample TDO on the rising edge
        self._set(TCK_PIN, 1)
        bit = self._get_tdo()
        self._set(TCK_PIN, 0)
        return bit

    # ----- state transitions -----

    def reset(self):
        """Drive 5x TMS=1 to force TLR, then to RTI."""
        for _ in range(5):
            self._clock(1, 0)
        self._clock(0, 0)  # TLR -> RTI
        self.state = self.RTI

    def _to_shift_dr(self):
        """RTI -> Select-DR -> Capture-DR -> Shift-DR."""
        self._clock(1, 0)  # RTI -> Select-DR-Scan
        self._clock(0, 0)  # -> Capture-DR
        self._clock(0, 0)  # -> Shift-DR
        self.state = self.SDR

    def _to_shift_ir(self):
        """RTI -> Select-DR -> Select-IR -> Capture-IR -> Shift-IR."""
        self._clock(1, 0)  # RTI -> Select-DR
        self._clock(1, 0)  # -> Select-IR
        self._clock(0, 0)  # -> Capture-IR
        self._clock(0, 0)  # -> Shift-IR
        self.state = self.SIR

    def _exit_to_rti(self, last_state):
        """Exit Shift-DR or Shift-IR back to RTI (Update + RTI)."""
        # last bit was already shifted with TMS=1 to enter Exit1
        self._clock(1, 0)  # Exit1 -> Update
        self._clock(0, 0)  # Update -> RTI
        self.state = self.RTI

    def _shift_bits(self, value_int, n_bits, exit_at_end):
        """Shift n_bits LSB-first from value_int. If exit_at_end, last bit pulses TMS=1."""
        out = 0
        for i in range(n_bits):
            tdi = (value_int >> i) & 1
            tms = 1 if (exit_at_end and i == n_bits - 1) else 0
            tdo = self._clock(tms, tdi)
            out |= (tdo & 1) << i
        return out

    def _shift_bytes(self, data_bytes, exit_at_end):
        """Shift a bytes object LSB-first byte-wise. Returns received bytes."""
        recv = bytearray(len(data_bytes))
        total = len(data_bytes)
        for byte_idx, b in enumerate(data_bytes):
            for bit in range(8):
                tdi = (b >> bit) & 1
                last = (byte_idx == total - 1) and (bit == 7)
                tms = 1 if (exit_at_end and last) else 0
                tdo = self._clock(tms, tdi)
                recv[byte_idx] |= (tdo & 1) << bit
        return bytes(recv)

    # ----- high-level scans -----

    def irscan(self, ir_value, ir_len=IRLEN):
        """Shift ir_len bits of ir_value into IR, return to RTI."""
        if self.state == self.TLR:
            self._clock(0, 0)  # -> RTI
            self.state = self.RTI
        self._to_shift_ir()
        self._shift_bits(ir_value, ir_len, exit_at_end=True)
        self._exit_to_rti(self.EX1IR)

    def drscan_int(self, value, n_bits, exit_to_rti=True):
        """Shift n_bits of value into DR, return TDO. Optionally stay in Shift-DR."""
        if self.state != self.SDR:
            self._to_shift_dr()
        if exit_to_rti:
            tdo = self._shift_bits(value, n_bits, exit_at_end=True)
            self._exit_to_rti(self.EX1DR)
        else:
            tdo = self._shift_bits(value, n_bits, exit_at_end=False)
        return tdo

    def drscan_bytes(self, data_bytes, exit_to_rti=True):
        """Shift bytes into DR (LSB first within each byte). Returns TDO bytes."""
        if self.state != self.SDR:
            self._to_shift_dr()
        recv = self._shift_bytes(data_bytes, exit_at_end=exit_to_rti)
        if exit_to_rti:
            self._exit_to_rti(self.EX1DR)
        return recv


class U64iiRecovery:
    """Stage-2 protocol layer matching upstream recover.py user_* methods."""

    def __init__(self, jtag):
        self.j = jtag

    def read_idcode(self):
        self.j.reset()
        idcode = self.j.drscan_int(0, 32, exit_to_rti=True)
        log.info(f"IDCODE: 0x{idcode:08x}")
        return idcode

    def set_user_ir(self, ir):
        """Mimic recover.py:set_user_ir.

        Two USER4 IRSCANs around a 5-bit DR write select the inner-bridge IR.
        Then USER4 again, enter SHIFT-DR, and shift '0' to mark "what follows
        is data" — but DO NOT exit Shift-DR. The next drscan must continue
        shifting in Shift-DR so the bridge sees mode-bit + payload as ONE
        continuous DR chain that gets committed at a single final Update-DR.

        Exiting Update-DR after the mode bit alone (which my earlier port
        did) causes the bridge to commit "1-bit data" then interpret the
        next drscan's payload as bridge-IR — wrong protocol, garbage results.
        """
        self.j.irscan(XILINX_USER4)
        self.j.drscan_int((ir << 1) | 1, 5, exit_to_rti=True)
        self.j.irscan(XILINX_USER4)
        # Enter Shift-DR, shift the '0' mode bit, STAY in Shift-DR.
        self.j.drscan_int(0, 1, exit_to_rti=False)

    def read_user_dr(self, n_bits):
        """After set_user_ir, read n_bits from the bridge data register.

        Continues from Shift-DR (set_user_ir leaves us there). One final
        Update-DR commits the full mode-bit + n_bits to the bridge.
        """
        return self.j.drscan_int(0, n_bits, exit_to_rti=True)

    def user_read_id(self):
        self.set_user_ir(0)
        uid = self.read_user_dr(32)
        log.info(f"User-side IDCODE: 0x{uid:08x}")
        return uid

    def user_set_outputs(self, value):
        """Set 8-bit GPIO outputs on the bridge — used to assert/release CPU reset.
           value=0x80 -> reset asserted; value=0x00 -> released (boot)."""
        self.set_user_ir(2)
        self.j.drscan_int(value & 0xFF, 8, exit_to_rti=True)

    def user_write_memory(self, addr, buffer):
        """Write a buffer of bytes to DRAM at addr via the bridge.
        Command format (10 bytes): {addr_LE_byte_0, 4, addr_LE_byte_1, 5,
                                    addr_LE_byte_2, 6, addr_LE_byte_3, 7,
                                    0x80, 0x01}
        Then bridge IR=6 streams data bytes."""
        addrbytes = struct.pack("<L", addr)
        command = bytes([
            addrbytes[0], 4,
            addrbytes[1], 5,
            addrbytes[2], 6,
            addrbytes[3], 7,
            0x80, 0x01,
        ])
        self.set_user_ir(5)
        self.j.drscan_bytes(command, exit_to_rti=True)
        self.set_user_ir(6)
        self.j.drscan_bytes(buffer, exit_to_rti=True)

    def user_upload(self, filename, addr):
        """Upload file to DRAM in 16K chunks (matches upstream recover.py)."""
        total = 0
        chunk_size = 16384
        with open(filename, "rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                total += len(buf)
                # upstream pads each chunk with 8 zero bytes
                self.user_write_memory(addr, buf + b"\x00" * 8)
                addr += chunk_size
                log.info(f"Uploaded chunk -> next addr 0x{addr:08x}, total {total} bytes")
        if total == 0:
            raise RuntimeError(f"Read of {filename} returned 0 bytes")
        log.info(f"Upload complete: {total} bytes")


def main():
    ap = argparse.ArgumentParser(description="Commodore 64 Ultimate / Ultimate 64 Elite II stage-2 recovery via Pi GPIO")
    ap.add_argument("ultimate_bin", help="Path to ultimate.bin (recovery RISC-V image)")
    ap.add_argument("--addr", type=lambda x: int(x, 0), default=0x30000,
                    help="DRAM upload address (default 0x30000)")
    ap.add_argument("--magic-addr", type=lambda x: int(x, 0), default=0xFFF8,
                    help="Boot magic write address (default 0xFFF8)")
    ap.add_argument("--magic-sig", type=lambda x: int(x, 0), default=0x1571BABE,
                    help="Boot magic signature (default 0x1571BABE)")
    ap.add_argument("--skip-fpga-check", action="store_true",
                    help="Skip the user-side IDCODE check (use if FPGA not yet loaded)")
    ap.add_argument("--quick", action="store_true",
                    help="Just read IDCODE and exit (chain smoke test)")
    args = ap.parse_args()

    jtag = JtagBitbang()
    rec = U64iiRecovery(jtag)
    try:
        idcode = rec.read_idcode()
        if (idcode & 0x0FFFFFFF) != 0x0362C093:
            log.warning(f"Unexpected IDCODE 0x{idcode:08x} (expected XC7A50T 0x0362c093)")
        if args.quick:
            log.info("Quick smoke test done.")
            return

        if not args.skip_fpga_check:
            try:
                rec.user_read_id()
            except Exception as e:
                log.error(f"User-side IDCODE read failed: {e}")
                log.error("FPGA may not be loaded with the recovery bitstream. "
                          "Run xc3sprog/openFPGALoader for u64_mk2_artix.bit first.")
                return

        log.info("Asserting CPU reset (output 0x80)…")
        rec.user_set_outputs(0x80)
        time.sleep(0.05)

        log.info(f"Uploading {args.ultimate_bin} to DRAM at 0x{args.addr:08x}…")
        rec.user_upload(args.ultimate_bin, args.addr)

        log.info(f"Writing boot magic at 0x{args.magic_addr:08x}: addr=0x{args.addr:08x} sig=0x{args.magic_sig:08x}")
        magic = struct.pack("<LL", args.addr, args.magic_sig)
        rec.user_write_memory(args.magic_addr, magic)

        log.info("Releasing CPU reset (output 0x00) — RISC-V should now boot from DRAM…")
        rec.user_set_outputs(0x00)
        time.sleep(0.5)

        try:
            rec.user_read_id()
        except Exception as e:
            log.warning(f"Post-boot user IDCODE read raised {e} — board may have started running ultimate.bin (which would re-init the bridge).")

        log.info("Stage 2 done. Watch the C64U: HDMI/menu should come up shortly.")
        log.info("Once it boots, run an update.ue2 from a USB stick via the menu to permanently restore flash.")
    finally:
        jtag.close()


if __name__ == "__main__":
    main()
