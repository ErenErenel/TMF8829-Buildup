# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# TMF8829-Buildup

Firmware bring-up for the ams OSRAM TMF8829 dToF sensor. Broader
project context, hardware/wiring reference, and datasheets live in the
sibling folder `..\tmf8829\PROJECT (1).md` — that file is the
authoritative source of truth for hardware/goal/wiring. This file
tracks progress specific to the firmware in this repo and is loaded
automatically at the start of every session here.

> Update this file's Changelog on sizeable advancements only (hardware
> milestone confirmed, checklist item resolved, pivot in approach,
> scope change) — not routine debugging. Surface the edit rather than
> making it silently.

## Current status

**Phase:** Phase 2 — I3C transport, Arduino framework (PlatformIO,
`board = nucleo_h563zi`, `framework = arduino`, `upload_protocol = mbed`),
branch `phase2-i3c` tracking `origin/phase2-i3c` — `master` stays on the
validated Phase 1 (I2C) state as a fallback.

**Status:** Phase 1 (I2C, 8×8) is complete and pushed to GitHub (private
repo: https://github.com/ErenErenel/TMF8829-Buildup). Phase 2 (I3C)
Stages 1 and 2 are both done and confirmed on hardware: the driver shim's
transport is now fully I3C-backed (`tmf8829_shim.cpp`), with
`tmf8829.c`/`tmf8829_app.cpp` unmodified. Device info and 8×8
distance/confidence streaming over I3C match Phase 1's I2C results
exactly. Stages 3a and 3b are both done: resolution scales to the
sensor's full native **48×32 (1536 zones)**, with sub-frames assembled
live in the Python viewer. The phase's definition of done is met.
Remaining: Stage 4 (frame-rate validation at 32×32/48×32) and the
unresolved I3C bus wedge (see changelog — the DISEC/recovery build is
now flashed and confirmed resident, but has not been soak-tested).

**Day-to-day operation is documented separately in `MANUAL.md`** — keys,
the nine Preconfig values, and the commands. The Python viewer
(`tools/tmf8829_viewer.py`) can drive the board by itself and shows the
board's own replies, so a separate serial monitor is not needed.

**Uncommitted as of this writing:** the shim changes, `tools/`,
`MANUAL.md`, and `.captures/` are all untracked or modified — nothing has
been committed since the initial Phase 2 work.

**Definition of done for this phase:** live distance/confidence frame
streaming from the TMF8829 to a PC over serial via I3C, at a resolution
higher than Phase 1's 8×8, at a reasonable frame rate, direct (non-shield)
wiring between the LightRanger 14 Click and the NUCLEO-H563ZI.

**Open items:**
- [x] Stage 1: standalone I3C1 controller init + SETDASA dynamic address
      assignment + ID-register readback (0xE3 → 0x9E) — confirmed on
      hardware.
- [x] Stage 2: port that transport into `tmf8829_shim.cpp`, replacing
      I2C — confirmed on hardware (firmware download + device info +
      live 8×8 streaming, matching Phase 1's I2C results).
- [x] Stage 3a: generalize the zone decoder (resolution + zone count read
      from each frame header), grow `DATA_BUFFER_SIZE`, subsampled live
      preview + `f` full-res dump — confirmed at **16×16, ~28-32 fps**
      (the sensor's native 33ms cadence), 256 zones, no errors.
- [x] Physically validate 16×16 distances against a measured target —
      confirmed against the ~2m ceiling: perpendicular minimum 1947mm,
      matching Phase 1's 8×8 result on the same ceiling to ~0.7%.
- [x] Resolve grid orientation — **horizontal mirror** (`np.fliplr`), rows
      correct. Confirmed with a hand at two known positions.
- [x] Flash and confirm binary streaming (`b`) + `tools/tmf8829_viewer.py`
      against hardware — ~1400 frames, 30.3 fps, zero CRC errors.
- [x] Stage 3b: sub-frame interleave is **alternating rows** — sub_result
      clear = even rows, set = odd rows. Confirmed at 32×32 and 48×32;
      full 1536-zone frames now assemble live in the viewer.
- [ ] Stage 4: frame-rate validation/tuning at 32×32/48×32 against the
      datasheet's 66ms cadence for those modes.
- [ ] **Soak-test the DISEC/recovery build.** It is flashed and confirmed
      resident (boot banner shows the `b ...` shim line), but has never
      run long enough to hit the wedge. Unattended job — needs nobody
      watching. This gates everything else; see the 08-13 wedge entry.
- [ ] Explore, once the bus is trusted: higher frame rate, per-scene
      colour range, a real GUI, and the actual goal — presence and
      overhang detection. 16×16 at 30fps may beat 48×32 at 7fps for
      detecting a moving arm; full resolution was the phase goal, not
      necessarily the right operating point.

<details>
<summary>Phase 1 (I2C, 8×8) — completed checklist</summary>

- [x] Bring-up step 1: PlatformIO build + flash + serial monitor
      confirmed working (`src/main.cpp` blink/hello-world sketch).
- [x] Confirm COMM SEL jumpers on the Click are set to I2C (user
      confirmed wired/jumpered before step 2 testing).
- [x] Verify I2C scan ACKs at 0x41 with EN high — confirmed via direct
      targeted read at 0x41 (see changelog: a full 1-127 address scan
      is NOT safe on this hardware, destabilizes the next transaction).
- [x] Verify ID register 0xE3 reads 0x9E — confirmed reliably, 8/8
      consecutive reads.
- [x] Confirm firmware download completes — confirmed via device info
      printout (chip version 158.1, app version 1.2.200.0, serial
      0x51067D) after sending `e`, no `#Err`.
- [x] Confirm 8×8 frame output (64 zones, distance + confidence) looks
      sane against a known physical distance — confirmed against a real
      ~350mm wall measurement: most zones (esp. highest-confidence ones)
      read ~300-370mm, matching well. See changelog for the two corner
      anomalies found and explained during this test.

</details>

---

## Commands

`pio` is **not on PATH** — use the PlatformIO Core CLI terminal, or
`~/.platformio/penv/Scripts/pio.exe` directly. Python tooling runs from the
repo-root `.venv` (gitignored; numpy, pyserial, opencv-python, pymupdf, pypdf,
pyocd — pymupdf/pypdf are there for reading the datasheet and Click schematic
PDFs, and pyocd is what flashes the board — see below).

```powershell
pio run                          # build only (~3.1% flash, ~11.5 kB RAM at present)
pio run --target upload          # build + flash over SWD via pyocd (see below)
pio device monitor -p COM5 -b 2000000 -f direct   # match UART_BAUD_RATE
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py            # live GUI
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py --no-gui   # link check: want err hdr=0 pay=0
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py capture --seconds 20
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py solve    # re-verify the interleave
```

Two flags are load-bearing, each for a reason found the hard way: `-f direct`
(miniterm's default filter eats the ESC bytes of the ANSI grid), and a `-b` that
matches `monitor_speed`/`UART_BAUD_RATE`.

**Upload does not use the ST-LINK's mbed virtual disk on this machine** — see
the 08-17 changelog entry. `upload_protocol = custom` drives pyocd over SWD
instead. One-time setup (already done here; needed on a fresh checkout):

```powershell
.\.venv\Scripts\python.exe -m pyocd pack update
.\.venv\Scripts\python.exe -m pyocd pack install stm32h563zitx
```

**Only one process can hold COM5.** Close the monitor before running a Python
tool and vice versa. The viewer forwards keys to the board and surfaces the
board's ASCII replies in-window precisely so a second process isn't needed.

**There is no automated test suite** — `test/` is PlatformIO's unused
placeholder. Verification is: `pio run` for compile, then hardware. The
regression check that catches transport/protocol breakage is `--no-gui` (frame
rate plus `hdr_err`/`pay_err` counters); `subframe.py solve` re-derives the
interleave from a capture. Per-key operating detail (the nine Preconfigs, their
frame IDs and cadences, viewer flags) lives in `MANUAL.md`.

---

## Architecture

Signal chain: TMF8829 → I3C1 (PB8/PB9, AF3) → shim → vendored ams driver →
UART 1 Mbaud → Python. Four layers, in dependency order:

1. **`src/main.cpp`** — ~15 lines. `setup()`/`loop()` call the vendor app's
   `setupFn(logLevel, baud, i2cClock)`/`loopFn()`. The `i2cClock` argument is
   vestigial under I3C.
2. **`lib/tmf8829_driver_arduino/`** — vendored ams-OSRAM driver.
   `tmf8829.c/.h` (chip protocol, firmware download, result parsing),
   `tmf8829_app.cpp` (the single-character serial REPL), `tmf8829_image.c`
   (firmware blob). **These are unmodified and must stay that way** — the shim
   exists so they can be. Every project-specific change goes in the shim.
3. **`tmf8829_shim.cpp/.h`** — the only adapted file, and where nearly all of
   this project's own code lives (~1300 lines): I3C transport, pins, buffer
   sizing, zone decoding, ASCII rendering, binary streaming, key interception.
4. **`tools/`** — host side. `tmf8829_viewer.py` (threaded reader → resyncing
   `FrameParser` → `SubFrameAssembler` → OpenCV), `tmf8829_subframe.py`
   (capture + interleave solver).

Structural facts that span files, and are easy to break:

- **The transport is I3C but keeps the I2C names.** `i2cTxReg`/`i2cRxReg`/
  `i2cTxRx`/`i2cOpen` are the vendor driver's function-pointer contract; they
  now wrap `i3cTxRegOnly`/`i3cRxRegOnly`. Don't rename them.
- **The dynamic address cannot live on the driver struct.** `tmf8829Enable()`
  calls `enablePinHigh()` and then `tmf8829Initialise()`, which unconditionally
  resets `driver->i2cSlaveAddress` to the static `0x41`. So SETDASA runs *inside*
  `enablePinHigh()` (the one point the device is powered and freshly reset), the
  assigned address is held in a shim static, and the transports **ignore the
  `slaveAddr` they are passed** — it is always stale.
- **Extra keys are intercepted in the shim's `inputGetKey()`.** `b` (binary
  stream) and `f` (full-resolution dump) are consumed there and never reach
  `tmf8829_app.cpp`, which is why they are absent from the app's own `h` help
  and are announced by a startup hint instead.
- **Frame decode path**, all in the shim: `handleReceivedFrameHeaderData()`
  derives geometry per frame (fp_mode = low nibble of the frame ID;
  `zones = (payload - 24) / pixelSize`, the 24 being 12 bytes of already-read
  header plus a 12-byte footer) → `handleReceivedResultData()` accumulates zones
  across chunks and divides by 4 (**distance LSB is 0.25 mm**) →
  `handleReceivedResultDataEnd()` renders the subsampled ASCII preview or calls
  `streamZoneFrameBinary()`. Resolution is read from each frame, never from a
  per-preset table.
- **The binary frame layout is a contract between two files**:
  `streamZoneFrameBinary()` in the shim and `FrameParser` in
  `tools/tmf8829_viewer.py`. 22-byte header with **its own CRC** (the host must
  trust `zone_count` before it knows where the frame ends), 3 bytes/zone, then a
  payload CRC; both CRC-16/CCITT-FALSE. Change one side and you must change the
  other.
- **Geometry stays in Python, deliberately.** The firmware emits raw device zone
  order; the horizontal mirror (`fliplr`) and the alternating-row sub-frame
  assembly are applied host-side so a hypothesis can be tested by editing and
  rerunning rather than reflashing. `--raw-order` disables both. Sub-frame
  assembly must stay in the *reader thread* — the single-slot frame buffer drops
  frames when rendering lags, and dropping half a pair breaks assembly.
- **`DATA_BUFFER_SIZE = 2400`** so the largest frame (48×32, 2316 bytes) moves in
  one I3C transaction. This is the payoff for leaving I2C, where `Wire` capped
  transfers at 32 bytes.
- **`build_flags = -D HAL_I3C_MODULE_ENABLED` must stay global.** STM32duino's
  `stm32yyxx_hal_conf.h` omits I3C entirely, and a project-level
  `include/hal_conf_extra.h` does *not* reach the framework's `SrcWrapper`
  library (built with its own narrower `CPPPATH`) — it silently compiles
  `stm32h5xx_hal_i3c.c` to an empty translation unit and every `HAL_I3C_*` call
  fails at link.
- **Never run a full 1–127 I2C/I3C address scan on this hardware** — it
  reproducibly destabilizes the next transaction. Probe `0x41` directly.

---

## Changelog

### 2026-08-17 — COM5 contention incident; viewer/tools hardened; link re-verified end to end
- **"Board stopped responding" after the upload fix was three processes
  fighting over COM5**: two viewer instances (one launched by a **system
  Python 3.12 that now exists on this machine** — the environment notes from
  08-13 predate it) plus a `pio device monitor`. The window the user typed
  into had lost the port: it echoed `sent 'e'` while nothing reached the
  board, because `send_key()` swallowed write errors and a dead reader thread
  was never surfaced. The firmware was fine throughout.
- Hardened `SerialReader` (shared by viewer and subframe tool), each fix
  aimed at a behaviour observed that day:
  - Port-open failure now exits with a named-PID listing of likely holders
    (other viewers, monitors) instead of a bare traceback. Excludes itself
    *and its parent* — the venv `python.exe` is a redirector that spawns the
    real interpreter, so WMI lists the same invocation under two PIDs.
  - `link_error` flag set on any read/write failure: the GUI draws a red
    **SERIAL LINK LOST** banner and stops echoing `sent 'x'`; `--no-gui`
    exits loudly. A dead link can no longer impersonate a healthy idle one.
- MANUAL.md gained an "If the board seems deaf" section (duplicate-process
  check first, venv-only Python, SWD reset command, the `s`→`d`→`e` stall
  recovery) and its flash section was rewritten for the pyocd path.
- **Full chain re-verified on hardware afterwards**: `e` → correct device
  info (1.2.200.0, serial 0x51067D) → Preconfig 64 → `m`/`b` → **241 binary
  frames, 0 CRC errors, 30.3 fps at the native 33.0ms cadence**, parsed by
  the production `FrameParser`. Device left stopped + disabled, streaming off.
- Two operational traps hit while verifying, worth knowing:
  - The first `e` after the session's many resets produced the **bootloader
    signature** (app version 128.x = appid 0x80, serial 0x0, `#Err,timeout
    reg=0x8`, `#Err,Config`) — the documented `s`→`d`→`e` recovery cleared
    it on the first try. Chip version still read 158.1, so this state can
    look "half-working".
  - The shim's `b` is a **toggle with state that survives sensor
    disable/enable** (MCU RAM): a script that blindly sends `b` can turn
    streaming *off* right before its own capture window. Read back the
    `Binary streaming ON/OFF` reply instead of assuming.

### 2026-08-17 — Flashing unblocked: upload moved off mbed/MSD onto pyocd over SWD
- **Root-caused the "ST-LINK mass-storage drive is not mounted" blocker from the
  08-13 entry. It is not the board, the cable, or ST-LINK firmware — it is this
  machine's endpoint security.** Microsoft Defender **Device Control**'s filter
  driver `WdDevFlt` fails to attach to USB mass-storage volumes:
  `Kernel-PnP` event **219**, status **0xC000038E** (`STATUS_FAILED_DRIVER_ENTRY`),
  naming the MBED volume by name, once per attach. Because the filter is a
  mandatory upper filter on the storage-volume stack, its `DriverEntry` failure
  aborts the whole stack build — so the disk and its FAT16 partition enumerate
  (`Get-Disk`/`Get-Partition` show them) but **no volume object is ever created**,
  hence no drive letter. `Set-Partition -NewDriveLetter` returns "Not Supported"
  because there is nothing to assign a letter *to*. The service is boot-start yet
  `Stopped`, and `WddState = 1` with an **empty** policy package.
- Consequences worth knowing: this affects **all** USB mass storage on this
  machine, not just the ST-LINK (test with any USB stick). Replugging, a
  different port/cable, an elevated shell, or an ST-LINK firmware upgrade will
  **not** fix it — it is policy/driver state, not a device fault. Legacy GPO keys
  (`StorageDevicePolicies`, `RemovableStorageDevices`, `NoDrives`) are all absent,
  which is why the earlier "is it corporate lockdown?" checks came back clean;
  MDE Device Control uses its own channel.
- **The ST-LINK debug interface is completely unaffected**, which is the way out.
  Verified independently with OpenOCD: `STLINK V3J10M3 (API v3)`, target voltage
  **3.276 V**, `SWD DPIDR 0x6ba02477`, **Cortex-M33 r0p4 detected, examination
  succeeded**. So the solid-red **LD4** simply means PC↔ST-LINK is up and no
  target communication has happened *yet* — it is not an error indication.
- **Mainline OpenOCD cannot flash this part** — PlatformIO's `tool-openocd`
  (xPack 0.12.0+dev) ships no `target/stm32h5x.cfg`, and forcing the `stm32l4x`
  driver fails with `can't get the device id` (it reads DBGMCU at the L4/U5
  address; the H5's is elsewhere). STM32H5 flash support lives only in **ST's
  OpenOCD fork**, i.e. CubeIDE/CubeCLT — not installed, not on winget, and
  gated behind an ST account.
- **Solution: pyocd**, which was already in `.venv`. It auto-identifies the probe
  as `STLINK-V3 / NUCLEO-H563ZI / stm32h563zitx`; it only needed the CMSIS device
  pack (`pyocd pack update` then `pyocd pack install stm32h563zitx` →
  `Keil.STM32H5xx_DFP 2.3.1`). No admin rights and no ST account required.
  Wired into `platformio.ini` as `upload_protocol = custom` + `upload_command`,
  so **`pio run --target upload` works normally again**.
- **Verified properly, not just by exit code**: the first flash reported
  `identical 66560 bytes`, which only proves *verification*, so the target was
  chip-erased and reflashed — `Erased 73728 bytes (9 sectors), programmed 66560
  bytes (65 pages), identical 0 bytes`. Then reset over SWD while holding COM5
  open, and captured the boot banner including the shim's own `f`/`b`/`t` lines,
  confirming the freshly-written image is what is running.
- Also corrected `upload_protocol`, which was sitting at **`jlink`** — that
  cannot work here regardless of the disk, since the probe is a stock ST-LINK
  V3E (USB `0483:374E`), not J-Link OB. The board declares only `{jlink, mbed}`,
  which is precisely why a third path was needed.
- Method note: the decisive clue was in the **System event log**, not in any
  storage tooling. `Get-Disk`/`Get-Partition`/`Get-Volume` describe the *symptom*
  (partition present, volume absent) but never say who vetoed the mount; the
  Kernel-PnP 219 record names both the driver and the exact device.

### 2026-08-14 — Viewer made self-sufficient; MANUAL.md added; operational findings
- **`MANUAL.md`** added at the repo root — keys, the nine Preconfig values with
  their frame IDs and cadences, and the commands. Deliberately short; `CLAUDE.md`
  keeps the reasoning, `MANUAL.md` keeps the how.
- **The viewer can now drive the board on its own.** Previously the legend,
  status and key feedback were drawn only inside the "we have a frame" branch,
  so straight after a flash — which resets the board and leaves the device
  disabled — the window was a grey void with no hint. It now always draws, with
  an instruction panel when no frames are arriving.
- **Board replies are surfaced in the window.** The frame scanner already steps
  over the board's ASCII to find sync markers; those skipped bytes are now
  captured and the last two lines drawn in green. So `Preconfig 71`,
  `state=measure` and `#Err,...` are visible **without a second process on the
  port** — which matters because only one process can hold COM5, and cycling
  configs blind was otherwise impossible.
- Also: `d` added to the forwarded key set (was missing), a `sent 'x'`
  confirmation so a keypress that went to the terminal instead of the focused
  window is distinguishable, and `tools/tmf8829_subframe.py capture` now saves
  in any mode (it previously returned without writing unless sub-frames were
  present, so recording at 8×8/16×16 silently produced nothing).
- **Temporal median smoothing** (`--smooth`, default 5) cut per-zone noise
  **80%: 33.7mm → 6.7mm**. Median rather than mean because noisy zones flip
  *bimodally* between two surfaces — mean-of-5 left 19.7mm, three times worse,
  since averaging a 200mm and a 2500mm reading lands at 1350mm where no surface
  exists. Also added `--colormap`. A wider palette does *not* help: measured
  p5..p95 already occupies the full ramp.
- **New failure mode, distinct from the wedge:** measurements stop while control
  transactions keep working, reporting `#Err,timeout ... reg=0x8` with
  `state=measure`. **Recovered in software with `s` → `d` → `e`** — no need to
  touch the board. Cause unknown; found by accident.
- **Upload gotcha:** `upload_protocol = mbed` copies to the ST-Link's *virtual
  disk*, not a COM port. Passing `--upload-port COM5` makes it try to write
  `COM5\firmware.bin`. Auto-detection works fine when the disk is mounted
  (`Auto-detected: D:\`), so pass **no** `--upload-port` at all; fall back to
  `--upload-port D:` only if detection fails, and replug USB if the disk is
  absent entirely.
- Two reference pages published as artifacts (private): a repo/signal-chain
  architecture map, and a retrospective covering goals, work done, method, open
  items and next directions.

### 2026-08-14 — Stage 3b solved: sub-frames are alternating rows; full 48×32 live
- **The interleave is `rows_alternating`**: the sub-frame with `sub_result`
  **clear carries the even rows**, the one with it **set carries the odd rows**.
  Confirmed independently at 32×32 (512 zones/half) and 48×32 (768/half).
- Method — `tools/tmf8829_subframe.py`, scoring candidate assemblies by
  **spatial roughness** (mean |difference| between 4-neighbours, reported per
  axis). A correct assembly of a real scene is smooth; a wrong interleave puts
  unrelated rows next to each other and spikes total variation.

  | candidate | 32×32 total | 48×32 total |
  |---|---|---|
  | **rows_alternating** | **200.5** | **359.8** |
  | rows_alternating_swapped | 232.1 | 421.3 |
  | rows_blocked | 263.2 | 428.1 |
  | cols_* / checkerboard | 619-627 | 804-826 |
  | *(one sub-frame alone)* | *237.3* | *445.6* |

- **The decisive test was the solo baseline, not the ranking.** Only
  `rows_alternating` scores *better than a single sub-frame on its own* — i.e.
  assembly adds real spatial information rather than shuffling data. Every
  wrong candidate is at or worse than that baseline, because inserting
  unrelated rows between correct ones cannot beat just having fewer rows.
- Two further checks, both consistent: all four `rows_*` candidates share an
  identical *horizontal* roughness (any row split leaves horizontal neighbours
  intact — a sanity check that the metric measures what was intended), and the
  observed 16-17% margin matches the ~15% the synthetic validation predicted
  for a *true* `rows_alternating` (the thinnest margin of the four patterns,
  since adjacent rows are inherently similar). A correct answer here looks
  narrow; that is expected, not marginal.
- Validated the solver against synthetic scenes with known interleaves before
  spending hardware time — it recovered all four patterns (margins 15-35%).
  Worth keeping: a method that cannot be shown to work on a known answer should
  not be trusted on an unknown one.
- Assembly implemented in `tools/tmf8829_viewer.py` (`SubFrameAssembler`), in
  the **reader thread**, not the render loop — the viewer's single-slot buffer
  intentionally drops frames when rendering lags, and dropping half a pair would
  make assembly impossible. Only joins halves with consecutive `frame_num` and
  opposite `sub_result`, so a dropped frame can't silently fuse two scenes.
  `--raw-order` still shows unassembled halves.
- **Confirmed live: 1536 zones at 48×32, zero CRC errors, row-to-row jaggedness
  47.3mm over a 25-2841mm scene** — smooth and physically coherent. Full native
  resolution now works end to end.
- Scene note for repeating this: a flat ceiling alone cannot discriminate (it is
  smooth under every candidate). Needs a sharp near/far edge in frame — a hand
  at ~150-300mm over a ~2m ceiling worked. The solver warns below a 10% margin.

### 2026-08-13 — I3C wedge characterised (~23+ min); DISEC/recovery written but NOT yet flashed
- **Reproduced the failure with a timed soak**: 16×16 binary streaming ran
  **23+ minutes at 29.9 fps with zero CRC errors**, then the bus wedged. Same
  signature every time: `I3C-TX failed regAddr=0xF8 status=3
  hi3c1.ErrorCode=0x20000` = `HAL_TIMEOUT` / `HAL_I3C_ERROR_TIMEOUT`, repeating
  forever. `regAddr=0xF8` is the driver's status/interrupt poll, so it wedges
  on the routine per-frame poll, not on anything exotic.
- So the failure is **not** ~10 minutes and not obviously load-related — it ran
  clean for a long time first. Time-to-failure is still only bounded on one
  sample; more soaks needed before calling it periodic vs. random.
- **Important caveat on today's results: the board was running the PRE-DISEC
  firmware the whole time.** The new build was compiled but never uploaded, so
  neither the DISEC IBI-disable nor `i3cRecoverBus()` was under test. An earlier
  note in this session read "no DISEC error printed" as success — that was
  wrong; the code simply wasn't on the device. The IBI hypothesis remains
  entirely untested.
- Confirmed the recovery path did not fire (zero occurrences of `wedged` /
  `power-cycling` / `recovered` in the stream), which is consistent with the
  old firmware being resident rather than with a bug in the new code.
- **Blocker: `pio run --target upload` fails with "Please specify
  `upload_port`" because the ST-Link mass-storage drive is not mounted** — only
  `C:` and Recovery are present. `upload_protocol = mbed` needs that virtual
  disk. COM5/VCP still enumerates fine, so it is the storage interface
  specifically. Unplug/replug the USB cable to re-enumerate (which also clears
  the wedged bus); if it still doesn't appear, that is worth investigating on
  its own since it blocks all flashing.
  **RESOLVED 2026-08-17 — see that entry.** The replug advice here is wrong: the
  cause is Defender Device Control's `WdDevFlt` failing to attach to the volume
  stack, so the disk can never mount no matter how often it re-enumerates.
  Flashing now goes over SWD via pyocd and does not touch the disk at all.
- Method note: driving the board over serial from Python works well for this
  (`s`/`c`/`m`/`b` + a background soak logging frames and scanning for board
  error text). Watch two traps hit today: gating a progress log on
  `n % 900 == 0` never fires reliably because frames arrive in bursts; and
  `threading.Thread` already defines `_stop()`, so naming an Event that
  shadows it breaks `join()`.

### 2026-08-13 — CORRECTION: the bus *does* have pull-ups (R8/R9, 2.2k) — earlier entries were wrong
- **Every prior entry claiming "the bus genuinely has no pull-up anywhere" is
  incorrect.** Traced the Click schematic properly (rendered the PDF and read
  the junctions, rather than relying on flattened text):
  - **R8 = 2.2k to 3V3 on mikroBUS SCL; R9 = 2.2k to 3V3 on mikroBUS SDA. Both
    populated.** These are the lines our jumper wires attach to, so the bus we
    drive has had proper pull-ups all along.
  - R16/R17 (DNP) sit on the *sensor* side (`SCL'`/`SDA'`, TMF8829 pins 10/11)
    — a second, redundant pair. R6/R7 (10k, populated) are on SDO/SCK', i.e.
    the SPI lines, not I2C.
- The original Phase 1 finding looked at R16/R17, saw DNP, and generalised to
  "no pull-up anywhere" without checking the mikroBUS side. That error then
  propagated into the Stage 1 risk assessment ("plausible hard blocker"), the
  note about the internal weak pull-up "turning out to be sufficient" (it was
  never load-bearing — R8/R9 were doing the work), and the Click Shield
  evaluation.
- Practical consequences: rise time is ~100ns against our 1µs open-drain high
  period, roughly 10× margin — **the bus is not timing-marginal, and populating
  R16/R17 would achieve nothing.** Do not solder them.
- Method note for next time: `pypdf` text extraction flattens schematics and
  loses which net a component actually connects to. Render the page
  (`pymupdf` at 600dpi, cropped to the designator via `page.search_for()`) and
  read the junction dots.

### 2026-08-13 — Binary streaming confirmed on hardware; grid orientation resolved
- **Binary streaming works end-to-end**: ~1400 frames at 16×16, **30.3 fps**
  (the sensor's native 33ms cadence), `hdr_err=0 pay_err=0` throughout. The
  framing, both CRCs, and the C↔Python struct layout all agree on real data.
- One real bug, found on `Ctrl+C`: `SerialReader` assigned
  `self._stop = threading.Event()`, shadowing `Thread._stop()` — which
  `join()` calls internally, so shutdown raised `TypeError: 'Event' object is
  not callable`. Renamed to `_stopping`. Also moved `daemon` into
  `Thread.__init__` (a class attribute shadows the `daemon` property while
  leaving `_daemonic` unset).
- **Grid orientation resolved: the device's zone order is a horizontal mirror
  of the scene — columns invert, rows are correct.** Measured with a hand at
  two known positions, in a behind-the-sensor ("camera") view:

  | Hand position | Grid centroid | Region |
  |---|---|---|
  | scene top-right | row 0.3, col 3.6 | top-left |
  | scene bottom-right | row 13.0, col 2.3 | bottom-left |

  This uniquely determines `fliplr`: transpose predicts (0,15) for the first,
  rot90 predicts top-right for the second, rot180 predicts bottom-left for the
  first. Two supporting signals point the same way — the first blob is 8 cols
  wide × 2 rows tall (a hand across the top edge, not a rotated one), and grid
  columns measure ~4.2°/zone against rows at ~3.2°/zone, matching the native
  array being 48 wide × 32 tall. The single-corner test warned about in the
  previous entry really was ambiguous; the second position is what closed it.
- Applied as a **display-time** flip in `tools/tmf8829_viewer.py` (`--raw-order`
  disables it). Deliberately not applied in `as_grid()` or the firmware, so
  Stage 3b's sub-frame work sees the raw device order rather than reasoning
  through a transform.
- Also re-measured the ceiling baseline with the board lying flat: a clean,
  symmetric dome, perpendicular distance **1944mm** at row 8/col 7, corners at
  2497mm, confidence 8-60 (median 44). The vertical angular pitch now agrees to
  **1.5%** across a wide angle span (3.25 vs 3.20 °/zone) — much tighter than
  the handheld run, and a stronger reconfirmation of the 0.25mm/LSB scaling.
- Operational notes for driving the board over serial (no monitor needed):
  `configNr` is **not** reset by `e`, so `c` cycles from wherever it was —
  always read back the `Preconfig NN` line rather than counting presses
  (64=8×8 … 67=16×16 … 71=48×32). `c` is ignored unless stopped, so send `s`
  first. Frame ID confirms the mode independently (18=16×16, 21=48×32).

### 2026-08-13 — Binary frame streaming + Python/OpenCV viewer (built, not yet flashed)
- **Measured the actual bandwidth position first, and it corrected the working
  assumption**: at 16×16 we are *not* UART-bound. Per-zone ASCII costs, derived
  from real dumps rather than estimated — an ANSI colored cell is 25 B
  (7 prefix + 8 color + 1 + 4 padded digits + 5 reset), a plain distance 4.72 B
  (avg 3.72 digits), a confidence 3.09 B (avg 2.09 digits, counted across a real
  256-zone grid). The current subsampled preview is ~1953 B/frame × 30.3 fps =
  **59 kB/s of the 100 kB/s link (59%)**, i.e. running at the sensor's own
  cadence with ~40% spare. The wall is real but lives at 32×32/48×32:

  | Mode | Zones | Budget/frame | ANSI full | Plain ASCII | Binary |
  |---|---|---|---|---|---|
  | 16×16 | 256 | 3300 | 7191 ❌ | 2000 | 784 |
  | 32×32 | 1024 | 6600 | 28,764 ❌ | 7997 ❌ | 3088 |
  | 48×32 | 1536 | 6600 | 43,146 ❌ | 11,996 ❌ | 4632 |

  (1 Mbaud, 8N1 → 100 kB/s; 33/66 ms cadences.) Two conclusions: the Stage 3a
  subsampling was necessary rather than cosmetic, and **plain ASCII is not a
  fix** — only binary clears 48×32.
- Added `b` (binary stream toggle) to `tmf8829_shim.cpp`, intercepted in
  `inputGetKey()` exactly like `f`, so `tmf8829.c`/`tmf8829_app.cpp` stay
  unmodified. Per-frame ASCII is suppressed while streaming; one-shot output
  (errors, `f`, banners) still goes out and the host resyncs past it.
- Frame format (see `streamZoneFrameBinary()` for the authoritative layout):
  22-byte header — sync `"TMF8"`, version, flags(sub_result), fp_mode, stride,
  zone_count, grid_w/h, frame_num, systick, **header CRC** — then 3 B/zone
  (uint16 LE mm + uint8 confidence), then a payload CRC. Both CRC-16/CCITT-FALSE.
- **Design point worth keeping**: the header carries its own CRC, separate from
  the payload's, because the host must trust `zone_count` *before* it knows
  where the frame ends. With a single whole-frame CRC, a chance 4-byte sync
  match inside pixel data yields a garbage length and the parser blocks on
  bytes that never arrive — a hang, not a dropped frame. Separate header
  validation rejects false syncs at ~1/65536 and bounds recovery to one frame.
- **Zone order is emitted raw** — no sub-frame assembly, no orientation fix.
  Both are still unknown, and putting them in Python makes testing a Stage 3b
  hypothesis an edit-and-rerun instead of an edit-and-reflash. That is the main
  reason this was built before Stage 3b rather than after.
- `tools/tmf8829_viewer.py`: threaded pyserial reader → resyncing parser →
  OpenCV render. Notable choices, each for a specific failure it avoids:
  single-slot frame buffer (not a queue — a queue accumulates latency instead
  of dropping frames); fixed colour normalisation (auto-scaling makes the image
  breathe and misleads while debugging geometry); `INTER_NEAREST` upscaling
  (linear would blur away the per-zone structure); `set_buffer_size` on Windows
  (default RX buffers drop bytes at 70% utilisation if the thread hiccups).
  Viewer keys are forwarded to the board, since only one process can hold the
  COM port. `--no-gui` prints statistics without needing OpenCV.
- Verified offline: firmware builds (RAM unchanged at 11.5 kB, flash 3.1%), and
  the parser round-trips synthetic frames at all four modes, recovers after a
  dropped byte, steps over interleaved ASCII and a bare false sync, and does not
  emit or wedge on a truncated trailing frame. The Python CRC matches an
  independent bitwise implementation *and* the standard 0x29B1 check value, so
  both ends are confirmed CCITT-FALSE rather than merely self-consistent.
- **Not yet flashed or run against hardware** — that is the next checkpoint.
- Environment note: no system Python on this machine, so `numpy`/`pyserial` were
  installed into PlatformIO's bundled venv
  (`~/.platformio/penv/Scripts/python.exe -m pip install ...`). `opencv-python`
  is still needed for the GUI path. A dedicated venv would be cleaner if this
  tooling grows.

### 2026-08-13 — 16×16 distances physically validated against the ~2m ceiling
- Closes the open item left by the Stage 3a entry below. The earlier
  ~0mm/confidence-184 frames were exactly what they looked like — nothing
  in range — not a decode fault. With a real target the signature is gone
  entirely (no 0mm zones; confidence 11-71, in line with Phase 1's ceiling
  run at 18-50).
- **Absolute scale confirmed three ways**, pointing at the ceiling ~2m
  above the sensor:
  - Perpendicular minimum **1947mm** (grid row 11, col 9; 1948 at two
    neighbours) vs. the ~2m measurement — ~2.7%.
  - Phase 1's 8×8 run on the same ceiling computed ~1950-1960mm center.
    The 16×16 decode reproduces that to **~0.7%** at 4× the zone count —
    the apples-to-apples cross-check this item was really asking for.
  - The slant-range dome is internally self-consistent: solving `d/cos θ`
    for the per-zone angular pitch at two widely separated angles per axis
    gives 4.29 vs 4.09 °/zone horizontally (col 0 and col 15 of row 11)
    and 3.26 vs 3.14 °/zone vertically (row 0 and row 15 of col 9) —
    flat to within ~5% across a 2× range of angles. A wrong distance LSB
    would make that implied pitch drift systematically edge-to-edge
    instead, so this independently re-confirms the 0.25mm/LSB scaling.
- **All 256 zones land in physically coherent positions** — every row of
  the `f` dump falls monotonically to a minimum and rises again, with no
  zone out of sequence. Ordering/stride bugs in the generalized decoder
  would scramble this.
- Two observations recorded, neither a defect:
  - The dome's foot sits at ~(row 11, col 8.5) rather than the grid
    center — the sensor was tilted a few degrees, not aimed straight up.
  - A low-confidence patch at cols 12-14 / rows 0-10 (11-41, vs 50-67 for
    neighbours) with distances still smooth and on the dome — most likely
    a lower-reflectance ceiling feature.
- **New open item found: grid orientation is not yet pinned down.** A
  transposed or mirrored grid would produce an equally smooth dome, so
  this test can't distinguish it. Weak evidence against a transpose: the
  printed width axis carries the larger angular pitch (~4.2° vs ~3.2°),
  as expected if it maps to the wide axis of the native 48×32 array — but
  that's inference. Resolve with an object at a *known* FoV edge; the same
  physically-structured scene is what Stage 3b needs anyway.
- Housekeeping: the "~350mm wall test" and the ceiling test in the Phase 1
  entries below are two genuinely separate tests (the wall was a cubicle
  wall), both records correct as written.

### 2026-08-13 — Stage 3a confirmed: 16×16 streaming at native frame rate; result-frame format decoded
- **Reverse-engineered the result frame format from hardware, cross-validated
  three ways** (this replaces the earlier guesswork about per-preset payload
  sizes):
  - **Zone count**: `payload` in the frame header counts 12 bytes of
    already-read frame header *plus* a 12-byte trailing footer, neither of
    which is zone data — derived by tracing `tmf8829ReadResults()` in
    `tmf8829.c`. So `zones = (payload - 24) / tmf8829GetPixelSize(layout)`.
    Checks out against Phase 1's known-good 8×8: `(216-24)/3 = 64` exactly.
  - **Grid size**: the low nibble of the frame ID is the device's FP_MODE
    code (datasheet §8.2.5, p.59). Confirmed on hardware: ID `0x10`→8×8,
    `0x12`→16×16, `0x13`→32×32, `0x15`→48×32. Resolution is now read from
    the frame itself — no per-preset lookup table needed.
  - **Sub-frames**: 32×32 and 48×32 split each measurement across *two*
    frames — measured 512 zones/frame (of 1024) and 768 (of 1536), with
    `layout` alternating `0x41`/`0x01`, where bit 6 is `sub_result`
    (§8.2.9). 8×8 and 16×16 are single-frame.
- Generalized the decoder in `tmf8829_shim.cpp` accordingly (resolution and
  zone count derived per frame; accumulation bounded by the computed count,
  which is also what correctly stops before the footer). Grew
  `DATA_BUFFER_SIZE` 500→2400 so the largest frame (48×32, 2316 bytes) moves
  in a single I3C transaction — cashing in the removed 32-byte `Wire` cap
  that motivated I3C in the first place. RAM 3.3kB→11.5kB (1.8% of 640kB).
- **Display had to change for bandwidth reasons, not cosmetic ones**: a
  colored cell costs ~27-31 bytes of ANSI, so a full 48×32 grid is ~41kB
  ≈0.4s/frame at 1Mbaud vs. the sensor's 66ms cadence — that would delay the
  loop and trip the driver's auto-stop-on-error path (the same failure seen
  at 115200 baud in Phase 1). Live view is now subsampled to ≤8 cells/axis
  (stride 1 at 8×8, so that case is byte-identical to before); `f` dumps the
  last frame at full resolution as plain numbers (~5 bytes/cell).
  `f` is intercepted in the shim's `inputGetKey()` and consumed there, so
  `tmf8829_app.cpp`/`tmf8829.c` remain unmodified — hence the startup hint,
  since `f` can't appear in the vendor app's own `h` help text.
- **Result, confirmed live at 16×16**: ID 18, payload 792, all 256 zones
  decoded, no sub-frame, no `#Err`. Frame deltas from the device's own 125kHz
  tick were 3930/4454/3930/3952 ticks = **31.4/35.6/31.4/31.6 ms ≈ 28-32 fps**,
  i.e. the datasheet's native 33ms 16×16 cadence — 4× Phase 1's zone count at
  no frame-rate cost.
- **Not yet validated: the 16×16 distance values themselves.** The test frames
  read ~0-59mm across nearly the whole grid at uniformly high confidence
  (~184), which matches the "no valid target, crosstalk peak dominating"
  signature rather than real returns (Phase 1's genuine far readings had
  *low* confidence, 18-50; and its 0mm/high-confidence corner block was
  explained the same way). Structural decode is confirmed; a wall test at a
  measured distance, like Phase 1's ~350mm check, is still owed.
- Next: Stage 3b — determine the sub-frame interleaving pattern empirically
  and assemble both halves into one image, unlocking full 32×32/48×32.

### 2026-08-12 — Phase 2 (I3C) Stage 2 confirmed on hardware: full driver shim now I3C-backed
- Ported Stage 1's proven I3C logic (GPIO/peripheral init, SETDASA, private
  read/write) into `lib/tmf8829_driver_arduino/tmf8829_shim.cpp`, replacing
  the I2C/`Wire` transport. `tmf8829.c`/`tmf8829_app.cpp` unmodified, per
  the shim's whole purpose. `src/main.cpp` reverted to the plain
  `setupFn()`/`loopFn()` wrapper (Stage 1's standalone test is recoverable
  via git history).
- **Key design finding, from tracing `tmf8829Enable()` in `tmf8829.c`**:
  `enablePinHigh()` (EN low->high) is immediately followed by
  `tmf8829Initialise()`, which unconditionally resets
  `driver->i2cSlaveAddress` back to the static address `0x41` -- both calls
  inside the unmodified vendor driver. So the dynamic address assigned by
  SETDASA can't be stored on the driver struct (it'd be stomped
  immediately); it's tracked in a shim-local static instead, and the
  transport functions ignore whatever `slaveAddr` they're passed (always
  stale) in favor of that tracked value. SETDASA itself runs inside
  `enablePinHigh()` -- the only point where the device is guaranteed
  powered and freshly reset to addressable state.
- Removed `ARDUINO_MAX_I2C_TRANSFER` (32-byte chunking) -- confirmed via
  the HAL source that `HAL_I3C_Ctrl_Transmit`'s polling loop and the
  private-transfer control word's byte-count field (16-bit, max 65535)
  both comfortably exceed `DATA_BUFFER_SIZE` (500B, the real ceiling), so
  no shim-level re-chunking is needed -- one I3C call per `txReg`/`rxReg`.
- **First flash after the port failed** (`I3C-SETDASA failed`, followed by
  `#Err,CPU not ready` and all-zero device info) -- a regression versus
  Stage 1, which had succeeded on its very first attempt. Root cause not
  fully isolated (added back the detailed `HAL_I3C_Ctrl_TransmitCCC`
  status/`ErrorCode` printing that Stage 1 had, which Stage 2's initial
  port had dropped down to a generic failure message -- diagnostics fix
  went out, but the *next* flash succeeded outright, so the specific
  failure mode wasn't captured this time). Flagged here since the
  underlying cause is still technically unconfirmed -- if SETDASA
  intermittently fails again, the added `AddDescToFrame`/`TransmitCCC`
  status and `hi3c1.ErrorCode` prints (search `tmf8829_shim.cpp` for
  `SETDASA failed`) are the first thing to check.
- **Result on the next flash, confirmed live**: `e` -> firmware download
  succeeds, device info matches Phase 1's I2C result exactly (chip version
  158.1, app version 1.2.200.0, serial `0x51067D`) -> `c`/`m` -> continuous
  8x8 distance/confidence streaming with plausible, stable values. This is
  the apples-to-apples proof the transport swap is transparent to the
  driver -- Stage 2 is done.
- Next: per the saved plan (`floating-dazzling-lagoon.md`), scale
  resolution (16x16 up to 48x32 via the existing `c` cycling), which needs
  `DATA_BUFFER_SIZE` grown and the currently-hardcoded 8x8 zone-grid
  decoder in `tmf8829_shim.cpp` generalized -- not started yet.

### 2026-08-12 — Phase 2 (I3C) Stage 1 confirmed on hardware: SETDASA + ID readback working
- Flashed the Stage 1 test (see entry below) and hit an immediate real bug:
  `TransmitCCC(SETDASA)` reported `HAL_OK`, but every subsequent transaction
  at the new dynamic address (0x50) came back `ANACK` (`hi3c1.ErrorCode =
  0x100` = `I3C_SER_ANACK`, confirmed via the CMSIS register bit defines).
  Crucially, SETDASA's own address phase (targeting the static address
  0x41) had NOT NACKed — only 0x50 was unreachable — which pointed away
  from the pull-up risk (a real pull-up problem should have broken 0x41
  addressing too) and at a protocol framing bug instead.
- Root cause: `i3c1AssignDynamicAddress()` used
  `I3C_DIRECT_WITH_DEFBYTE_STOP` for the SETDASA CCC. Traced through the
  HAL source (`I3C_ControlBuffer_PriorPreparation` in
  `stm32h5xx_hal_i3c.c`): the `WITH_DEFBYTE` option subtracts 1 from the
  per-target data-phase byte count programmed into the control word --
  correct for CCCs with a true MIPI "Defining Byte" (ENEC/DISEC/RSTACT
  etc.), but SETDASA's one data byte (the new address + parity) is
  ordinary per-target write data, not a defining byte. With `CCCBuf.Size
  == 1`, this zeroed the programmed data-phase byte count, so the
  new-address byte most likely never reached the wire even though the
  HAL call itself completed without error. Fixed by switching to
  `I3C_DIRECT_WITHOUT_DEFBYTE_STOP`.
- **Result after the fix, confirmed live**: full init -> SETDASA -> device
  ready -> register write -> register read chain all report `HAL_OK`, and
  the ID register (0xE3) reads back `0x9E` -- exactly the value Phase 1's
  I2C bring-up confirmed, now reproduced over I3C. This closes Stage 1 of
  the saved plan (`floating-dazzling-lagoon.md`).
- The STM32's internal weak pull-up (`GPIO_PULLUP` on PB8/PB9) turned out
  to be sufficient for this bring-up at the conservative timing config
  used -- the R16/R17 DNP pull-up gap flagged from the schematic did
  **not** end up blocking anything here. Worth re-testing once timing is
  tuned toward the full 12.5MHz SDR ceiling (a later stage), since a
  faster bus is less forgiving of weak pull-ups.
- Practical note on iterating with `pio device monitor`: `setup()` runs
  once and `loop()` is currently empty, so if the monitor is opened after
  the board already booted, nothing appears (not a hang/failure) --
  re-running `pio run --target upload` while the monitor is already open
  forces a fresh reset and reliably reproduces the boot-time output.
- Next: per the saved plan, Stage 2 (wiring the proven I3C calls into
  `tmf8829_shim.cpp`, replacing the I2C/`Wire`-based transport, dropping
  the 32-byte transfer cap) -- not started yet.

### 2026-08-12 — Phase 2 (I3C) Stage 1 written and build-verified (not yet flashed)
- Picked up the I3C research from the prior session and pinned down the
  remaining unknowns directly from the datasheet and the Click's schematic
  before writing any code:
  - TMF8829 datasheet §7.10 (p.37) confirms the device supports **both**
    SETDASA and ENTDAA for dynamic address assignment — chose **SETDASA**
    (single known device at its known static address 0x41), consistent
    with this project's established preference for direct/targeted bus
    operations over broadcast ones (see the earlier finding that a full
    I2C address scan destabilizes this hardware).
  - Datasheet p.14 ("Bus timing characteristics for I3C communications")
    gives concrete SDR timing numbers (open-drain tLOW_OD≥200ns,
    push-pull fSCL up to 12.5MHz, etc.) — used to derive a conservative,
    correctness-over-speed timing config for this first bring-up.
  - Datasheet Table 5 (p.16) shows the sensor's own measurement cadence
    (33ms/cycle at 8x8/16x16, 66ms/cycle at 32x32/48x32) is the real
    frame-rate ceiling, not the bus — relevant context for later
    resolution-scaling stages, not for Stage 1.
  - **New risk found**: `../tmf8829/lightranger-14-click-schematic.pdf`
    shows the Click's SDA/SCL pull-up resistors, **R16 and R17, marked
    DNP** — confirms and pinpoints the "no pull-up anywhere" gap already
    on record from I2C bring-up. I3C's timing margins are tighter than
    I2C's, and the DAA handshake still runs in open-drain mode, so this
    is a plausible hard blocker for Stage 1, not just a nice-to-have.
- Wrote `src/main.cpp` as a standalone Stage 1 test (I3C1 controller init
  + SETDASA + ID-register readback, 0xE3 → expect 0x9E) — not yet wired
  into the tmf8829 driver/shim, mirrors how Phase 1 step 2 first proved
  I2C directly before the full driver was integrated. GPIO is configured
  with the STM32's internal weak pull-up (`GPIO_PULLUP`) as a
  software-only first attempt at the pull-up risk above.
- Full plan (this stage plus the follow-on shim-integration, resolution-
  scaling, and frame-rate stages) saved at
  `C:\Users\z005a6sd\.claude\plans\floating-dazzling-lagoon.md`.
- **Build environment finding, not obvious from the framework docs**:
  STM32duino's `stm32yyxx_hal_conf.h` doesn't enable I3C by default (it's
  absent from that file's module list entirely, not just disabled via a
  `HAL_I3C_MODULE_DISABLED` guard like other optional modules). A
  project-level `include/hal_conf_extra.h` override — the officially
  documented mechanism for adding HAL modules — compiles fine for
  `src/main.cpp` itself, but the framework's own `SrcWrapper` library
  (which actually compiles `stm32h5xx_hal_i3c.c`) is built with its own
  narrower `CPPPATH` that doesn't include the project's `include/` dir, so
  it silently produced an empty translation unit (0 defined symbols,
  confirmed via `nm`) and every `HAL_I3C_*` call failed at link time
  despite compiling fine. Fixed by defining the module globally instead:
  `build_flags = -D HAL_I3C_MODULE_ENABLED` in `platformio.ini`, which
  applies at the compiler command-line level for every translation unit
  regardless of include-path scoping.
- Full project build succeeds (`pio run`): 39300 bytes flash / 1436 bytes
  RAM, only a harmless upstream `-Wregister` warning from ST's own
  `stm32h5xx_ll_i3c.h`.
- **Not yet flashed to hardware** — this is the hardware checkpoint per
  the saved plan. Next: user flashes and reports back what
  `pio device monitor -p COM5 -b 1000000 -f direct` shows through
  init/SETDASA/ID-readback; a failure at the SETDASA step points at the
  R16/R17 pull-up gap above as the prime suspect.

### 2026-08-12 — Pushed to GitHub; started Phase 2 (I3C) on its own branch
- Repo pushed to GitHub for the first time: private repo at
  https://github.com/ErenErenel/TMF8829-Buildup. Neither `git` nor `gh`
  CLI were installed on this machine beforehand -- both installed via
  `winget` (`Git.Git`, `GitHub.cli`), then `gh auth login --web`
  (browser device-code flow) to authenticate as GitHub user
  `ErenErenel`. `.claude/settings.local.json` excluded via `.gitignore`
  (Claude Code's own convention: "local" settings files stay untracked).
- Created and pushed a `phase2-i3c` branch off `master` (which stays on
  the validated Phase 1 state as a fallback) to do the I3C work on.
- **Phase 2 (I3C) research/scoping done, no code written yet.**
  Findings from the datasheet (§7.10, pages 37-38) and the STM32H5 HAL
  headers actually present in this PlatformIO toolchain
  (`framework-arduinoststm32/system/Drivers/STM32H5xx_HAL_Driver`):
  - PB8/PB9 (already wired, no rewiring needed) support I3C1 natively
    via `GPIO_AF3_I3C1` -- confirmed in this variant's `PeripheralPins.c`.
  - The full H5 I3C controller-role HAL API is already in the
    toolchain (`HAL_I3C_Init`, `HAL_I3C_Ctrl_Config`,
    `HAL_I3C_Ctrl_DynAddrAssign`, `HAL_I3C_Ctrl_TransmitCCC`,
    `HAL_I3C_Ctrl_Transmit`/`Receive`, etc.) -- nothing extra to
    install for the HAL side.
  - **Key protocol fact that shapes the implementation:** the TMF8829
    is a real MIPI I3C v1.0 device (up to 12.5MHz) but boots up in
    legacy I2C mode and stays there until the host performs an explicit
    **dynamic address assignment** (`SETDASA` or `ENTDAA` CCC command).
    Only after that does it switch into I3C SDR mode and unlock the
    real throughput gain -- just wiring/talking to it isn't enough,
    there's a required handshake first.
  - Required implementation steps identified: (1) I3C1 peripheral init
    as controller (clock, GPIO AF3 remap, `HAL_I3C_Init` +
    `Ctrl_Config` + bus characteristic/timing config -- I3C has its own
    distinct SDR timing model per datasheet Figure 19, separate from
    I2C's Figure 18); (2) the dynamic address assignment sequence;
    (3) new transport functions in `tmf8829_shim.cpp` replacing the
    `Wire`-based `i2cTxReg`/`i2cRxReg`/`i2cTxRx` with
    `HAL_I3C_Ctrl_Transmit`/`Receive` equivalents -- this is the bulk
    of the new code needed.
  - **Good news:** `tmf8829.c` (chip protocol/firmware-download/result
    parsing logic) never touches the bus directly, only calls the
    shim's transport functions -- so none of that logic needs to
    change, only the transport layer underneath it.
  - Not yet consulted: ST app note AN5879 ("Introduction to I3C for
    STM32H5 series MCU") -- no local copy, wasn't fetched (no URL was
    given and one wasn't guessed). Worth pulling in before actually
    writing the peripheral init code, for the exact register-sequence
    details CubeMX would normally generate.
  - Separately (independent of I3C): our zone decoder currently
    hardcodes 8x8/single-I2C-chunk assumptions; higher resolutions will
    need chunk-spanning logic regardless of I2C vs I3C transport.
- Next: either pull in AN5879, or start sketching the I3C1 peripheral
  init sequence directly against the HAL headers already in the
  toolchain.

### 2026-08-12 — Low-confidence zones grayed out in the color grid
- Added `TMF8829_CONFIDENCE_DISPLAY_THRESHOLD` (40, a rough fixed
  cutoff, not spec-derived) in `tmf8829_shim.cpp`. `printColoredDistanceCell()`
  now takes both distance and confidence; cells below the threshold
  render flat gray (80,80,80) instead of distance-colored, regardless
  of their distance value.
- Directly motivated by the ~350mm wall test's bottom-left column
  (confidence 36-61, reporting ~2200mm while neighbors read ~300mm) --
  that's exactly the kind of reading this should now visually flag as
  marginal instead of looking like a normal graded value.
- Caveat noted in the code comment: confidence naturally falls with
  real distance (the ~2m ceiling test's legitimately clean data ran as
  low as ~18-50), so a single fixed threshold isn't perfectly
  scene-independent -- may need tuning per target range if it over- or
  under-flags in practice.

### 2026-08-12 — Color grid confirmed rendering; `pio device monitor` needs `-f direct`
- The ANSI color grid initially showed raw escape-code text
  (`␛[48;2;55;0;200m2377␛[0m`) instead of actual colored cells, live in
  the terminal, not just when copy-pasted. Root cause: `pio device
  monitor`'s default filter (pyserial miniterm) substitutes
  non-printable control characters -- including the ESC byte our color
  codes start with -- with a visible placeholder glyph, so the terminal
  (which does support ANSI/24-bit color fine) never receives a real
  escape sequence to interpret.
- Fix: add `-f direct` (the "direct" filter passes bytes through
  unmodified) to the monitor command:
  `pio device monitor -p COM5 -b 1000000 -f direct`
- Confirmed working after this -- grid now renders with real colored
  (red=near/blue=far) cell backgrounds.

### 2026-08-12 — Distance validated against real ~350mm wall; color grid re-added
- User measured the sensor at ~350mm from a flat wall. Result: most
  zones, especially the highest-confidence ones (140-194), read
  ~300-370mm — matches well, closing the last Phase 1 checklist item.
- Two corner anomalies found and explained by cross-referencing against
  the earlier ceiling test (which had zero anomalies across all 64
  zones):
  - Bottom-left column read ~2200-2266mm with the *lowest* confidence
    in the grid (36-61) — a weak/unreliable return, plausibly the
    sensor seeing past the wall's edge into open room space at this
    close range/wide FoV combination.
  - Top-left 2x3 block read exactly 0mm but with the *highest*
    confidence in the grid (180-194) — a strong, confident return
    reporting near-zero distance, meaning it's very likely seeing
    something real and close (not "no signal"). At only ~350mm range
    this is plausibly something near the sensor itself (hand, wire,
    Click board edge) clipping into that corner of the FoV.
  - Confirmed environmental/setup-related rather than a decode bug:
    user confirmed the ceiling test (nothing near the sensor, single
    flat target filling the whole FoV) was clean with no such
    anomalies. Not investigated further; no code changes made for this.
  - Follow-up idea not yet implemented: filter/flag zones below
    `TMF8829_CFG_ALG_CONFIDENCE_THRESHOLD` (device default 6, datasheet
    §8.2.38) distinctly, so low-confidence outliers like the
    bottom-left one are visually distinguished from real readings.
- Re-added the ANSI color grid (previously reverted, see entry below)
  now that the underlying distance data is validated. Same
  implementation as before: `printColoredDistanceCell()` in
  `tmf8829_shim.cpp`, fixed 100-3000mm red(near)->blue(far) range.

### 2026-08-12 — Reverted ANSI color grid, back to plain numeric grid
- User asked to go back to the plain `Distance grid (mm):` numeric
  output. Removed `printColoredDistanceCell()` and the ANSI-color path
  in `printZoneGrid()` in `tmf8829_shim.cpp`; reason for reverting
  wasn't stated. If color grading is revisited, see the entry below for
  what was tried (fixed 100-3000mm red->blue range, 24-bit ANSI
  background codes) before re-implementing from scratch.

### 2026-08-12 — Distance-to-color grading added (ANSI terminal heatmap)
- Added `printColoredDistanceCell()` in `tmf8829_shim.cpp`: each zone in
  `printZoneGrid()`'s distance grid now prints with a 24-bit-color ANSI
  background (`\x1b[48;2;R;G;Bm`) instead of plain numbers — red = near
  (100mm), blue = far (3000mm), linear interpolation between, clamped
  at both ends. Requires a VT100/ANSI-capable terminal (PowerShell and
  Windows Terminal both qualify).
- Chose on-device ANSI coloring over a host-side Python heatmap script:
  zero new tooling, works directly in the existing `pio device monitor`
  workflow already in use for bring-up.
- Range (100-3000mm) is a fixed bring-up default, not derived from any
  spec — tune `TMF8829_COLOR_DIST_MIN_MM`/`_MAX_MM` in `tmf8829_shim.cpp`
  to whatever range matters for the actual target scene later.
- Caveat found while reviewing this: the 3-byte-per-zone payload size
  (and the payload-size table used earlier to reason about I2C
  throughput at higher resolutions) is only *confirmed* correct for the
  specific config actually loaded and tested (default 8x8, layout=1,
  1 peak/no extras) — matches the datasheet's `TMF8829_CFG_RESULT_FORMAT`
  reset default (§8.2.9, page 61), but that register is configurable per
  preset, and the other 8 presets (16x16/32x32/48x32 and their
  HIGH_ACCURACY/LONG_RANGE variants) haven't been checked and could use
  more peaks or extra signal/noise/xtalk bytes, making their real
  payload size larger than a naive linear zone-count scaling would
  suggest. Not an issue for current 8x8-only scope; flag before trusting
  any resolution-scaling throughput estimate later.

### 2026-08-12 — Custom zone decoder added; found and fixed a 4x distance scale bug
- The driver's built-in raw-integer dump (`printResults`, log level
  `TMF8829_LOG_LEVEL_RESULTS`) prints ~250+ comma-separated numbers per
  frame with no field labels. At our original 115200 baud, printing one
  of these lines took long enough to delay the loop past the sensor's
  next measurement, which triggered the driver's error-recovery path
  and auto-stopped measurement after ~8 frames.
- Fix 1: bumped serial to **1,000,000 baud** in both `src/main.cpp`
  (`UART_BAUD_RATE`) and `platformio.ini` (`monitor_speed`) — matches
  the ams README's own guidance (their 2,000,000 default "could show
  communication errors", recommends >=1,000,000). Must reconnect
  `pio device monitor` at `-b 1000000` now, not 115200.
- Fix 2: added a real decoder in `tmf8829_shim.cpp`
  (`handleReceivedFrameHeaderData`/`handleReceivedResultData`/
  `handleReceivedResultDataEnd`) that parses the raw bytes into a
  labeled 8x8 grid (`Distance grid (mm):` / `Confidence grid:`) instead
  of the raw dump. Byte layout (3 bytes/pixel = 2-byte LE distance + 1
  confidence byte, for layout=1) was derived directly from the driver's
  own `tmf8829GetPixelSize()`/`tmf8829CorrectDistanceDataSegment()` in
  `tmf8829.c`, not guessed.
- **Found a real bug via a physical test.** User aimed the sensor at a
  ceiling estimated ~3.5m away; decoded output showed a smooth,
  physically-coherent "dome" pattern (center zones ~7805-7830, edges up
  to ~10150 -- ratio ~1.30 matches the expected 1/cos(40°) slant-distance
  falloff for an 80° FoV almost exactly), proving the byte layout/order
  was right, but the absolute number was ~2.2x too high vs. the ceiling
  estimate.
  - Checked the TMF8829 datasheet (`../tmf8829/TMF8829_datasheet.pdf`,
    DS001140 v2-00) directly using `pypdf` (installed into PlatformIO's
    bundled Python venv, since no system Python/poppler was available).
    §8.2.37 `TMF8829_CFG_ALG_DISTANCE` (page 71) states plainly:
    "Distance is reported in 0.25 mm steps." The decoder had assumed
    1 LSB = 1mm; actual is 1 LSB = 0.25mm, a 4x error.
  - Fixed: `zoneDistanceMm[...] = tmf8829GetUint16(...) / 4`. Center
    reading now computes to ~1950-1960mm, which is plausible for a
    ceiling estimated at ~3.5m if the sensor was held up rather than at
    floor level (imprecise on both ends, but the right order of
    magnitude, unlike the old 7.8m result).
- **Not yet independently confirmed** — the ceiling test was a rough
  eyeballed distance, useful for catching the 4x bug via its physically
  coherent spatial pattern, but not precise enough to fully close the
  checklist item. Next: repeat with the sensor square to a flat target
  at a distance measured with a tape measure.

### 2026-08-12 — Bring-up step 3: ams driver integrated, measuring on first flash
- Vendored `ams-OSRAM/tmf8829_driver_arduino` (tag/branch `main`, fetched
  via GitHub raw URLs since no git CLI available) into
  `lib/tmf8829_driver_arduino/`. Per-repo layout:
  - Unmodified: `tmf8829.c/h` (chip driver), `tmf8829_app.cpp/h`
    (console app layer — enable/config/measure/print, driven by single-
    character serial commands), `tmf8829_image.c/h` (firmware blob).
  - Adapted: `tmf8829_shim.h` — `ENABLE_PIN` 6→`PE9`, `INTERRUPT_PIN`/
    `TRIGGER_INTERRUPT_PIN` 2→`PE11` (single sensor, same physical INT
    pin for both). `tmf8829_shim.cpp` — `i2cOpen()` now calls
    `Wire.setSCL(PB8)`/`Wire.setSDA(PB9)` before `Wire.begin()`, matching
    the confirmed-working step 2 pin routing.
  - Reference-only `tmf8829.ino` moved to `examples/tmf8829/` inside the
    lib folder so PlatformIO doesn't try to compile its own
    `setup()`/`loop()` as a second entry point (conflicts with
    `src/main.cpp`).
  - Turned out **no AVR-specific code needed touching** —
    STM32duino ships its own `avr/pgmspace.h` compatibility shim
    (`PROGMEM` is empty, `pgm_read_byte` is a direct memory read,
    `PSTR`/`F()`/`__FlashStringHelper` all work), so `tmf8829.c`,
    `tmf8829_image.c/h`, and the `PRINT_CONST_STR`/`F()` calls throughout
    the app layer compiled unmodified.
- `src/main.cpp` is now a ~15-line wrapper: `setup()` calls
  `setupFn(2, 115200, 400000)` (log level, baud, I2C clock), `loop()`
  calls `loopFn()`. Interactive over serial: `e`=enable+download,
  `c`=next config, `m`=measure, `s`=stop, `+`/`-`=log level, `h`=help.
- **First flash worked end-to-end, no fixes needed.** User sent `e` →
  firmware downloaded, app started, printed chip version 158.1, app
  version 1.2.200.0, serial 0x51067D, no errors. Sent `c` → 8×8 config
  loaded (config 64). Sent `m` → measurement streaming continuously,
  frame counter incrementing steadily (`#Obj,65,0,<systick>,16,1,216,
  <frameNum>,...`).
- Note: default log level (2 = `TMF8829_LOG_LEVEL_RESULTS_HEADER`) only
  prints the frame header line, not the 64-zone distance/confidence
  payload. Need level 3 (`TMF8829_LOG_LEVEL_RESULTS`, one `+` keypress)
  to see per-zone data for the final open checklist item.
- Next: bump log level and visually sanity-check zone distances against
  a known physical target distance — last item for Phase 1's definition
  of done.

### 2026-08-12 — Bring-up step 2 confirmed: TMF8829 responds on I2C
- `src/main.cpp` now does a direct targeted read of ID register 0xE3 at
  address 0x41 (EN=PE9 high first). Confirmed 0x9E (correct TMF8829 ID)
  reliably across repeated reads.
- Along the way, found and ruled out red herrings:
  - A full 1-127 address bus scan (classic I2C scanner pattern)
    reproducibly destabilized the very next transaction on this
    hardware (a fail/fail/success 3-cycle repeated exactly). Root cause
    not fully explained, but confirmed reproducible — **do not use a
    full-range scanner on this bus**; probe 0x41 directly instead.
  - Investigated whether missing I2C pull-ups were the cause: confirmed
    (via `PeripheralPins.c`) that the Nucleo's I2C1 pins are
    `GPIO_NOPULL` by default, and the LightRanger 14 Click's own
    pull-up resistor footprint is DNP (unpopulated) — so the bus
    genuinely has no pull-up anywhere. Tried enabling the STM32's
    internal weak pull-up as a software workaround; confirmed (via
    direct `GPIOB->PUPDR` register reads) that the override was really
    taking effect, but it did not fix the symptom on its own — removing
    the full scan is what fixed it. The missing-pull-up gap is real but
    was not the cause of this particular symptom; left unresolved for
    now (tracked in Claude's internal memory as a known follow-up if
    bus issues recur, e.g. populating the Click's DNP resistor).
  - User confirmed physical wiring/jumpers were already correct per the
    pinout table before this testing began.
- Final `src/main.cpp` for this step: plain `Wire`, no internal
  pull-up override, no full scan — just a direct 0x41 read every 1s.
- Next: integrate the ported `ams-OSRAM/tmf8829_driver_arduino` driver
  (step 3) — enable, firmware download (watch `appid` 0x80 → 0x01),
  load 8×8 config, start measurement, read frames on INT.

### 2026-08-12 — Bring-up step 1 confirmed: blink + serial hello-world
- Flashed `src/main.cpp` (blinks LED_BUILTIN, prints over serial) to the
  NUCLEO-H563ZI via `pio run --target upload`. Board enumerates as
  ST-Link VCP on COM5 (VID:PID 0483:374E).
- `upload_protocol = stlink` from the original plan doesn't exist for
  this platform version — valid options are `jlink` or `mbed` (mass-
  storage drag-and-drop over the ST-Link's virtual disk). Set
  `upload_protocol = mbed` explicitly in `platformio.ini`; this is what
  PlatformIO already defaulted to and it works reliably.
- User confirmed serial output ("TMF8829 bring-up: hello world") visible
  in `pio device monitor -p COM5 -b 115200` and LED blinking.
- Next: I2C-scanner sketch (step 2) — EN (PE9) high, scan bus for ACK at
  0x41, read ID register 0xE3 for 0x9E.

### 2026-08-12 — Switched from workspace MEMORY.md to CLAUDE.md
- Replaced the workspace-root `MEMORY.md` with this `CLAUDE.md` so
  progress context loads automatically every session (Claude Code
  convention) instead of relying on an internal memory pointer.
  No hardware/software progress yet.
