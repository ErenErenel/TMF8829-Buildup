#!/usr/bin/env python3
"""Unattended soak of the TMF8829 I3C link, with automatic wedge recovery.

The bus wedges after a variable clean stretch (observed: 16.5 min and 23+ min,
both at 16x16/30fps with zero CRC errors up to the failure). The firmware
handles half of it: after 3 consecutive transfer failures i3cRecoverBus()
power-cycles the sensor and prints "I3C bus recovered". But the power-cycle
wipes the sensor's downloaded firmware, so it returns in the bootloader state
and does NOT resume measuring.

This tool closes that gap. It watches for the wedge, replays the documented
restore sequence (s -> d -> e -> m -> stream on), and keeps going -- so a run
survives an arbitrary number of wedges and can gather the time-to-failure
samples needed to characterise them.

    python tools/tmf8829_soak.py --minutes 120
    python tools/tmf8829_soak.py --minutes 480 --preconfig 71   # 48x32

Only one process may hold the COM port: close the viewer and any monitor first.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys
import time

import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tmf8829_viewer import FrameParser          # noqa: E402

# Board text that drives the state machine. These are emitted by
# i3cRecoverBus() and the transport in lib/tmf8829_driver_arduino/
# tmf8829_shim.cpp -- keep in step with the strings there.
TXT_WEDGED = "bus wedged"
TXT_RECOVERED = "bus recovered"
TXT_RECOVERY_FAILED = "recovery failed"
TXT_TRANSFER_FAIL = "I3C-TX failed"
TXT_STREAM_ON = "Binary streaming ON"
TXT_STREAM_OFF = "Binary streaming OFF"

# Restoring after a power-cycle takes an 'e' (firmware download, ~4s). Frames
# legitimately stop for that long, so the stall detector must sit well above it.
STALL_SECONDS = 20.0


class Soak:
    def __init__(self, ser: serial.Serial, log_path: pathlib.Path, preconfig: int):
        self.ser = ser
        self.log_path = log_path
        self.preconfig = preconfig
        # on_text, not polling: a repeating error saturates the rolling
        # text buffer in well under a second and silently drops lines.
        self.parser = FrameParser(on_text=self._on_text)
        self.frames = 0
        self.last_frame_at = time.monotonic()
        self.events: list[str] = []
        self.wedges: list[float] = []          # seconds into the run
        self.restores_ok = 0
        self.restores_failed = 0
        self._pending_wedge = False
        self._pending_recovered = False
        self._recovery_failed = False
        self._stream_state: bool | None = None
        self._t0 = time.monotonic()
        log_path.write_text("", encoding="utf-8")

    # ---------------------------------------------------------------- logging
    def log(self, msg: str) -> None:
        line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    # ------------------------------------------------------------ board input
    def _on_text(self, line: str) -> None:
        if TXT_WEDGED in line:
            self._pending_wedge = True
            self.log(f"WEDGE  {line}")
        elif TXT_RECOVERED in line:
            self._pending_recovered = True
            self.log(f"RECOV  {line}")
        elif TXT_RECOVERY_FAILED in line:
            self._recovery_failed = True
            self.log(f"RECOV-FAIL  {line}")
        elif TXT_STREAM_ON in line:
            self._stream_state = True
        elif TXT_STREAM_OFF in line:
            self._stream_state = False
        elif "#Err" in line or TXT_TRANSFER_FAIL in line:
            # These repeat forever while wedged; log the first of each run only.
            if not self.events or self.events[-1] != line:
                self.events.append(line)
                self.log(f"ERR    {line}")

    def pump(self, seconds: float, count_frames: bool = True) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            n = self.ser.in_waiting
            if n:
                for _frame in self.parser.feed(self.ser.read(n)):
                    if count_frames:
                        self.frames += 1
                        self.last_frame_at = time.monotonic()
            else:
                time.sleep(0.004)

    def send(self, key: str, wait: float) -> None:
        self.ser.write(key.encode())
        self.ser.flush()
        self.pump(wait)

    # --------------------------------------------------------------- sequences
    def ensure_streaming(self) -> bool:
        """Turn the binary stream on, reading back rather than assuming.

        The toggle lives in MCU RAM and survives sensor disable/enable, so a
        blind press can turn streaming *off* -- which is how the 2026-08-19
        probe produced a false "not streaming" result.
        """
        for _ in range(3):
            self._stream_state = None
            self.send("v", 1.2)
            if self._stream_state is True:
                return True
            if self._stream_state is None:      # no reply seen; try once more
                continue
        return self._stream_state is True

    def select_preconfig(self) -> bool:
        """Cycle 'c' until the readback names the target. configNr is not reset
        by 'e', so counting keypresses is unreliable."""
        self.send("s", 1.0)
        for _ in range(12):
            self.parser.text_lines.clear()
            self.send("c", 1.0)
            got = [l for l in self.parser.text_lines if "Preconfig" in l]
            if got and str(self.preconfig) in got[-1]:
                self.log(f"config: {got[-1]}")
                return True
        return False

    def start(self) -> bool:
        self.send("e", 6.0)
        info = " ".join(self.parser.text_lines)
        if "1.2.200.0" not in info:
            self.log("start: firmware download did not report 1.2.200.0")
            return False
        if not self.select_preconfig():
            self.log(f"start: could not reach Preconfig {self.preconfig}")
            return False
        self.send("m", 1.5)
        if not self.ensure_streaming():
            self.log("start: could not turn binary streaming on")
            return False
        self.last_frame_at = time.monotonic()
        return True

    def restore(self, why: str) -> bool:
        """Bring the device back after a power-cycle (or an unexplained stall).

        s -> d -> e is the documented fix for both the bootloader state that
        follows i3cRecoverBus() and the standalone measurement stall.
        """
        self.log(f"restoring ({why}) -- s -> d -> e -> m -> v")
        self.send("s", 1.2)
        self.send("d", 1.2)
        self.parser.text_lines.clear()
        self.send("e", 7.0)
        info = " ".join(self.parser.text_lines)
        if "1.2.200.0" not in info or "0x51067D" not in info:
            self.log(f"restore FAILED -- device info was: {self.parser.text_lines[-4:]}")
            self.restores_failed += 1
            return False
        if not self.select_preconfig():
            self.log("restore FAILED -- could not reselect preconfig")
            self.restores_failed += 1
            return False
        self.send("m", 1.5)
        if not self.ensure_streaming():
            self.log("restore FAILED -- streaming would not turn on")
            self.restores_failed += 1
            return False
        self.restores_ok += 1
        self.last_frame_at = time.monotonic()
        self.log(f"restore OK -- streaming again at t+{self.elapsed/60:.1f} min")
        return True

    # -------------------------------------------------------------- main loop
    def run(self, duration_s: float, report_every: float = 300.0) -> None:
        next_report = time.monotonic() + report_every
        last_wedge_end = self._t0
        frames_at_report = 0

        while self.elapsed < duration_s:
            self.pump(1.0)

            wedged = self._pending_wedge
            stalled = (time.monotonic() - self.last_frame_at) > STALL_SECONDS

            if wedged or stalled:
                at = self.elapsed
                clean_for = time.monotonic() - last_wedge_end
                self.wedges.append(at)
                self.log("=" * 58)
                self.log(f"{'WEDGE' if wedged else 'STALL'} #{len(self.wedges)} "
                         f"at t+{at/60:.1f} min after {clean_for/60:.1f} min clean, "
                         f"{self.frames} frames total")

                if wedged:
                    # Give the firmware's own recovery time to finish and print.
                    self.pump(3.0, count_frames=False)
                    if self._recovery_failed:
                        self.log("firmware recovery FAILED -- bus is not usable; "
                                 "stopping (needs a board reset)")
                        break
                    if not self._pending_recovered:
                        self.log("firmware did not report recovery within 3s -- "
                                 "attempting restore anyway")

                self._pending_wedge = False
                self._pending_recovered = False

                if not self.restore("wedge" if wedged else "stall"):
                    self.log("could not restore -- stopping")
                    break
                last_wedge_end = time.monotonic()
                self.log("=" * 58)
                next_report = time.monotonic() + report_every

            if time.monotonic() > next_report:
                el = self.elapsed
                delta = self.frames - frames_at_report
                self.log(f"t+{el/60:5.1f} min  frames={self.frames:8d}  "
                         f"fps={delta/report_every:5.1f}  "
                         f"hdr_err={self.parser.header_crc_errors} "
                         f"pay_err={self.parser.payload_crc_errors}  "
                         f"wedges={len(self.wedges)}")
                frames_at_report = self.frames
                next_report += report_every

        self.summary()

    def summary(self) -> None:
        el = self.elapsed
        self.log("=" * 58)
        self.log(f"soak ended after {el/60:.1f} min")
        self.log(f"frames={self.frames}  avg_fps={self.frames/el:.2f}")
        self.log(f"hdr_err={self.parser.header_crc_errors}  "
                 f"pay_err={self.parser.payload_crc_errors}")
        self.log(f"wedges={len(self.wedges)}  auto-restored={self.restores_ok}  "
                 f"restore-failures={self.restores_failed}")
        if self.wedges:
            gaps, prev = [], 0.0
            for w in self.wedges:
                gaps.append(w - prev)
                prev = w
            pretty = ", ".join(f"{g/60:.1f}" for g in gaps)
            self.log(f"clean intervals before each wedge (min): {pretty}")
            self.log(f"mean time-to-wedge: {sum(gaps)/len(gaps)/60:.1f} min")
        else:
            self.log("no wedge in this run")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=2000000)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--preconfig", type=int, default=67,
                    help="67=16x16 (default), 69=32x32, 71=48x32; see MANUAL.md")
    ap.add_argument("--log", default=None, help="log file (default .captures/soak-<ts>.txt)")
    ap.add_argument("--self-test", action="store_true",
                    help="exercise the restore path once at startup and exit. "
                         "Run this before any long unattended soak -- it is the "
                         "code that must work while nobody is watching.")
    args = ap.parse_args()

    if args.log:
        log_path = pathlib.Path(args.log)
    else:
        d = pathlib.Path(__file__).resolve().parent.parent / ".captures"
        d.mkdir(exist_ok=True)
        log_path = d / f"soak-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as exc:
        print(f"could not open {args.port}: {exc}\n"
              f"Close the viewer / serial monitor first -- only one process "
              f"may hold the port.", file=sys.stderr)
        return 2

    soak = Soak(ser, log_path, args.preconfig)
    soak.log(f"soak start -- {args.minutes:.0f} min, Preconfig {args.preconfig}, "
             f"log {log_path}")
    try:
        soak.pump(1.0)
        soak.parser.text_lines.clear()
        if not soak.start():
            soak.log("could not start -- aborting")
            return 1

        if args.self_test:
            soak.log("self-test: streaming confirmed, now exercising restore()")
            before = soak.frames
            soak.pump(3.0)
            streamed_first = soak.frames - before
            ok = soak.restore("self-test")
            after = soak.frames
            soak.pump(3.0)
            streamed_again = soak.frames - after
            soak.log(f"self-test: {streamed_first} frames before, "
                     f"{streamed_again} frames after restore")
            passed = ok and streamed_first > 30 and streamed_again > 30
            soak.log(f"SELF-TEST {'PASS' if passed else 'FAIL'} -- the restore "
                     f"sequence {'works' if passed else 'does NOT work'} unattended")
            return 0 if passed else 1

        soak.log(f"streaming; watching for wedges (stall threshold {STALL_SECONDS:.0f}s)")
        soak.run(args.minutes * 60.0)
    except KeyboardInterrupt:
        soak.log("interrupted by user")
        soak.summary()
    finally:
        try:
            ser.write(b"s"); ser.flush(); time.sleep(0.3)
            ser.write(b"d"); ser.flush(); time.sleep(0.3)
        except Exception:
            pass
        ser.close()
        soak.log("port closed; device stopped + disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
