# Changelog

## 2026-04-29 — first successful recovery

- Initial release.
- ✅ Hardware-tested on a Commodore 64 Ultimate (the original — Mass
  Production / V2.5; same Artix-7 + BSCANE2 bridge as the Ultimate 64
  Elite II / Elite Mark II) bricked by an incompatible firmware flash. Pi-GPIO recovery
  successfully reloaded the FPGA fabric, uploaded the recovery RISC-V
  app to DRAM, and booted into a working recovery menu. From there, a
  normal `update.ue2` from a USB stick permanently restored flash.
- Bug fixed during initial bring-up: the `set_user_ir` JTAG TAP state
  transitions had to stay in Shift-DR between the bridge mode bit and
  the payload, so they get committed to the bridge as ONE continuous
  Update-DR transaction. An earlier draft exited to RTI between the
  mode bit and the payload, which produced two separate Update-DRs and
  desynced the bridge protocol — uploads completed silently with no
  error but the post-boot bridge IDCODE came back as garbage
  (`0xbd5a2a83`) instead of Gideon's deliberate
  `0xdead1541` "recovery firmware booted" signature, and HDMI never
  came up. The fix preserves the single-Update-DR semantic across calls
  by making `set_user_ir` leave the JTAG state in Shift-DR and having
  the next drscan continue from there. See `docs/PROTOCOL.md` for
  detail.
- Default GPIO pin assignments match xc3sprog's `matrix_creator` cable
  and LinuxJedi's 2025 Pi-JTAG layout: TMS=4, TCK=17, TDI=22, TDO=27.
- GPIO library: `RPi.GPIO` via Raspberry Pi OS's apt package
  `python3-rpi.gpio` (Bookworm-patched 0.7.1~a4 build). Initial attempt
  was libgpiod via `python-gpiod` — that apt package doesn't exist on
  Bookworm under that name, and the PyPI `gpiod` v1→v2 API change made
  drop-in use awkward, so we fell back to RPi.GPIO. Tested on Raspberry
  Pi OS Bookworm (Debian 12) / kernel 6.6.31 / Python 3.11.2.
