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

**Phase:** Phase 3 (starting 2026-09-28) — **port to an ESP32-S3 over SPI,
with Wi-Fi streaming for commissioning.** Phase 2 (STM32H5 + I3C) is complete
and committed as the fallback; see the 09-28 changelog entry for why the
platform changes. Work continues on the ESP32-S3 dev board before any custom
PCB exists.

**Phase 2 (superseded, preserved):** I3C transport, Arduino framework
(PlatformIO, `board = nucleo_h563zi`, `framework = arduino`,
`upload_protocol = mbed`), branch `phase2-i3c` tracking `origin/phase2-i3c` —
`master` stays on the validated Phase 1 (I2C) state as a fallback.

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

**Operating mode, decided 2026-08-25: 16×16 (preconfig 67), not 48×32.**
Measured on one scene, same sensor position: 48×32 gave 20% no-return at
5.4fps and could not see a 3-4m room at all; 16×16 gave **4% no-return at
30.3fps** with the walls clearly resolved. This is the datasheet's own
Table 5 (p.16) appearing on the bench — 48×32 is specified to 2700mm
against a white card at centre, 16×16 to 5900mm. Full resolution was the
*phase* goal; it is not the right operating point for presence work. See
the 08-25 entry.

> **REOPENED 2026-09-28.** That decision was measured against a 3-4 m room.
> The application's working distance is **8 inches (~203 mm)**, where every
> mode is spec'd 13×+ beyond what is needed and range carries no weight at
> all. The frame-rate half of the argument also weakens on SPI. Reselect on
> **coverage and frame rate**, not reach — see the 09-28 entry.

**Day-to-day operation is documented separately in `MANUAL.md`** — keys,
the nine Preconfig values, and the commands. The Python viewer
(`tools/tmf8829_viewer.py`) can drive the board by itself and shows the
board's own replies, so a separate serial monitor is not needed.

**Working tree is clean as of 2026-09-28.** The ~1100 lines of 08-19/08-25
work (renderer, soak tooling, viewer fixes) are committed, `pio run` verified
clean at 11556 B RAM / 65916 B flash. This commit is the checkpoint before the
ESP32 pivot and the rollback point the 08-25 note said was missing.

**Definition of done for this phase:** live distance/confidence frame
streaming from the TMF8829 to a PC over serial via I3C, at a resolution
higher than Phase 1's 8×8, at a reasonable frame rate, direct (non-shield)
wiring between the LightRanger 14 Click and the NUCLEO-H563ZI.

**Open items — Phase 3 (ESP32-S3 / SPI / placement), in the order that wastes
least effort:**
- [ ] **Settle the 8-inch question: sensor standoff, or trigger threshold with
      the sensor further back?** Blocks sensor count, mounting and mode. Nothing
      downstream should be designed before this is answered.
- [ ] **Build the placement/coverage visualisation** — project zones to 3D
      using FOVX 67.9° / FOVY 52.8°, render top-down and side views in room
      coordinates with the frustum drawn against the bore. Pure host-side
      Python on the existing serial stream; no hardware change. Design it for
      *N* posed sensors from the start.
- [ ] **Prove the SPI transport on the existing Click + H5 rig** before any new
      silicon — `tmf8829BootloaderCmdI2cOff()` selects SPI, the Click already
      breaks SPI out to mikroBUS (R6/R7 on SDO/SCK'), and the result is a
      working SPI shim to port. Confirm the 0.93 ms figure.
- [ ] **Port to the ESP32-S3 dev board** (SPI + SoftAP + UDP), then the PCB.
- [ ] **Reselect the operating mode** on coverage and frame rate at the real
      working distance, not on reach.
- [ ] **Persist coverage maps** (frames + preset + config page + sensor pose +
      timestamp). This is the existing reproducible-capture item, now with a
      concrete reason: it is the record that the hazard volume is covered.

**Open items — Phase 2 (STM32/I3C, now the fallback path):**
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
- [x] Flash and confirm binary streaming (then `b`, now **`v`**) +
      `tools/tmf8829_viewer.py` against hardware — ~1400 frames, 30.3 fps, zero
      CRC errors; re-confirmed on the `v` build at 29.9 fps, zero CRC errors.
- [x] Stage 3b: sub-frame interleave is **alternating rows** — sub_result
      clear = even rows, set = odd rows. Confirmed at 32×32 and 48×32;
      full 1536-zone frames now assemble live in the viewer.
- [x] Free up `b` for the vendor's binary config protocol (stream toggle moved
      to `v`, intercept made mode-aware) — confirmed on hardware 2026-08-19.
      This is what unblocks Stage 4.
- [~] Stage 4: frame-rate validation/tuning at 32×32/48×32. **Largely
      superseded 2026-09-28 by the SPI finding** — the deficit was transport,
      and SPI at 20 MHz moves a sub-frame in 0.93 ms versus the 75 ms measured
      on I3C, which leaves the sensor's own cadence as the only limit. The
      config-page path still stands if iteration tuning is ever wanted: read
      with `u`, change `TMF8829_CFG_PERIOD_MS` (0x22/0x23) and
      `TMF8829_CFG_KILO_ITERATIONS` (0x24/0x25), write back with `0x31`.
- [x] **Soak-test the DISEC/recovery build** — done 2026-08-19. Wedge
      reproduced at 16.5 min / ~29,600 frames; `i3cRecoverBus()` fired and
      genuinely restored the bus. DISEC did **not** prevent the wedge.
- [x] **Host-side wedge watchdog** — `tools/tmf8829_soak.py`, built and
      self-tested on hardware 2026-08-19. Detects the wedge (or an unexplained
      stall), replays `s`→`d`→`e`→`m`→`v`, and continues; reports the clean
      interval before each wedge so time-to-failure can be characterised.
- [ ] **Run a long soak to characterise time-to-failure.** Two samples so far
      (23+ and 16.5 min) — not enough to tell random from periodic. Now
      cheap to gather: `tools/tmf8829_soak.py --minutes 480`.
- [x] Per-scene colour range + confidence handling — done 2026-08-19, see the
      renderer entry. Recovered 18% of zones that were being masked away.
- [x] Explore whether 16×16 beats 48×32 for the actual goal — **answered
      2026-08-25, decisively: yes.** See the operating-mode note above and the
      08-25 entry. **Detection belongs in `tools/`, not in ams's GUI** — see
      the 08-19 entry for why that path was evaluated and declined.
- [x] Root-cause the frame-rate deficit — **done 2026-08-25 by measurement,
      not inference.** `t` reports bus=75.0ms, emit=11.7ms against a
      period=93.7ms at 48×32, i.e. 92% of every cycle is spent in our code and
      **the I3C bus is 6× the cost of the UART**. The UART is innocent: 2328
      bytes at 2Mbaud is 11.6ms, matching `emit` almost exactly.
- [~] **Raise the I3C push-pull clock — DROPPED 2026-09-28.** Superseded by
      SPI, which is both faster than I3C's ceiling and unaffected by the
      jumper-wire edge degradation that killed 4 MHz. Kept below only as the
      record of what was tried, should the I3C path ever be revisited.
      **ATTEMPTED 2026-08-25, 4 MHz FAILED,
      reverted to the working 1 MHz.** `i3c1PeripheralInit()` is back at
      `SCLPPLowDuration`/`SCLI3CHighDuration` = 500ns each. 170/80 (4 MHz) left
      the device unable to boot (`pwup ENABLE=0x4`, `#Err,CPU not ready`, chip
      version 0.0). **SETDASA still succeeded**, so open-drain addressing was
      fine and the push-pull private transfers were what broke — most likely
      the jumper-wire interconnect rather than the silicon. Step ladder for a
      bisection is in the source above the constants; each step costs a
      flash-and-test because the failure mode is a dead device, not a degraded
      one. **Low priority now** — the bus only binds at 32×32/48×32, and the
      operating mode is 16×16. Still uncaptured: the `I3C kclk=… -> SCL~…kHz`
      boot banner (added 08-25), which would confirm the actual kernel clock
      and whether the ns→MHz arithmetic in that ladder is even right.
- [ ] **No detection exists yet — this is the missing half of the project.**
      Everything built so far is a viewer: it renders depth for a human. The
      goal is presence and overhang sensing, and nothing currently classifies,
      decides, or emits a signal. Start at D1 (background subtraction) in the
      punch list.
- [ ] **No test suite, and the changelog overstates this.** `test/` holds only
      PlatformIO's README. The 08-19 entry says offline tests "cover the
      adaptive floor… SpanTracker… the blend maths, and no-return
      distinctness" — those were inline scripts that were never committed, as
      were the 08-25 verifications. Matters because the B1 defect was a single
      `|` that should have been `&`, which silently misrepresented the
      hardware's dropout rate for weeks. That class of bug is exactly what a
      suite catches.
- [ ] **Measurements are not reproducible.** `.captures/` holds three files.
      The 48% → 20% → 4% progression that changed the operating mode exists
      only as screenshots. A capture format storing frames + preset + config
      page + timestamp would turn every future comparison into an offline diff
      instead of a bench session.
- [ ] **Host-side config-page writer** (`b` → `0x31`, 190-byte payload). Gates
      ~14 of the punch-list items, Stage 4 included. Highest-leverage tooling
      task outstanding.
- [ ] **Evaluate dual mode** (`dual_mode=1`, datasheet §7.3.1 p.26) — alternates
      a default preset with its high-accuracy sibling and reports one combined
      result, covering **10mm–11000mm in a single configuration**. Available at
      16×16/32×32/48×32 per Table 7, not just 8×8. Largest unexploited
      capability in the part.
- [ ] **Evaluate on-chip motion detection** (§7.3.2 p.27) — `post_processing`,
      `motion_distance`, `detect_snr`/`release_snr`, `motion_adjacent`,
      `int_zone_mask`, plus ROI cropping via `mp_top_x/y`/`mp_bottom_x/y` and
      `spad_cropping`. This is the phase's actual goal implemented in hardware,
      and `int_threshold_low/high` (0x68-0x6B) are already declared in
      `tmf8829_shim.h` but never written.
- [ ] **Test the 7.6m aliasing case** (§7.7.2 p.32). Untestable indoors — needs
      a corridor or outdoors. The viewer now colours `dist==0 & conf>0` magenta
      specifically to make it visible when it occurs.
- [ ] Enable spread-spectrum EMC settings (§7.7.1 p.32) — datasheet states "no
      side effects other than reducing EMC noise". Cheap now, expensive after
      an enclosure exists.

**Full findings list (26 items, grouped and ordered):** published artifact,
<https://claude.ai/code/artifact/07ffcc94-a785-48db-a47b-1338a21dcf87>

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
PDFs, and pyocd is the fallback flasher — see below).

```powershell
pio run                          # build only (~3.1% flash, ~11.5 kB RAM at present)
pio run --target upload          # build + flash to the mbed disk (see below)
pio device monitor -p COM5 -b 2000000 -f direct   # match UART_BAUD_RATE
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py            # live GUI
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py --no-gui   # link check: want err hdr=0 pay=0
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py capture --seconds 20
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py solve    # re-verify the interleave
.\.venv\Scripts\python.exe tools\tmf8829_soak.py --self-test  # prove auto-recovery works
.\.venv\Scripts\python.exe tools\tmf8829_soak.py --minutes 120
```

Two flags are load-bearing, each for a reason found the hard way: `-f direct`
(miniterm's default filter eats the ESC bytes of the ANSI grid), and a `-b` that
matches `monitor_speed`/`UART_BAUD_RATE`.

**Upload goes to the ST-LINK's mbed virtual disk** (`upload_protocol = mbed`,
auto-detected as `D:` / `NOD_H563ZI` — pass **no** `--upload-port`). If it fails
with "Please specify `upload_port`" and no mbed drive is mounted, that is the
`WdDevFlt` USB mass-storage driver fault documented in the 08-17/08-18 entries;
it is a driver-state problem on this machine, **the user has a known fix for it**
— ask rather than working around it.

The pyocd/SWD fallback (`upload_protocol = custom`, commented in
`platformio.ini`) bypasses the mass-storage stack entirely and stays available.
Its one-time setup is already done here; on a fresh checkout:

```powershell
.\.venv\Scripts\python.exe -m pyocd pack update
.\.venv\Scripts\python.exe -m pyocd pack install stm32h563zitx
```

pyocd is also how you reset the board without reflashing (useful for capturing
the boot banner while the monitor holds COM5):

```powershell
.\.venv\Scripts\python.exe -m pyocd reset -t stm32h563zitx
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
   (capture + interleave solver), `tmf8829_soak.py` (unattended soak with
   automatic wedge recovery).

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
- **Extra keys are intercepted in the shim's `inputGetKey()`.** `v` (binary
  stream), `f` (full-resolution dump) and `t` (frame timing) are consumed there
  and never reach `tmf8829_app.cpp`, which is why they are absent from the app's
  own `h` help and are announced by a startup hint instead. Two rules constrain
  what may be intercepted, both learned by breaking them:
  - **`b` is reserved by the vendor app** — it enters binary input mode, the
    only channel for the `0x31`/`0x32` config commands, i.e. the only way to set
    measurement period and iteration count. Intercepting it (as this shim did
    until 2026-08-19) makes that entire protocol unreachable. Don't take it back.
  - **The intercept is skipped while `isInBinaryInputMode()`.** In that mode
    every byte is payload, so an unguarded intercept eats any payload byte equal
    to `f`/`v`/`t` and leaves the app waiting forever for a byte already
    consumed. `isInBinaryInputMode()` is declared `extern` in the shim; it is in
    no header.
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

### 2026-09-28 — SPI found at 20 MHz (unused all along); application geometry pinned at 8 inches; scope pivots to an ESP32-S3 wireless node
- **The TMF8829 has a 20 MHz SPI interface and this project has never used
  it.** Datasheet §7.11 (p.40) plus the timing table on p.14. It has
  **dedicated pins** — MOSI/GPIO0, MISO/GPIO3, SCLK/GPIO2, CSN/GPIO1 —
  separate from SCL/SDA, and **INT/GPIO6 stays free**, so it is a clean 4-wire
  SPI + INT + EN with no pin multiplexing cost. Both interfaces are live at
  boot (§7.9, p.37) and the *first* bootloader command chooses one;
  `tmf8829BootloaderCmdI2cOff()` already exists in the vendored driver
  (`tmf8829.h:310`) and is what selects SPI.

  | interface | max clock | 2316 B sub-frame |
  |---|---|---|
  | I²C | 1 MHz | 20.8 ms |
  | I3C | 12.5 MHz | 1.7 ms |
  | **SPI** | **20 MHz** | **0.93 ms** |
  | *as built — I3C @ 1 MHz* | | *75 ms measured* |

  **SPI is the fastest interface on the part.** This dissolves **Stage 4** and
  the **I3C clock ladder** outright: at 48×32 transport drops from 92% of the
  cycle to ~3%, and the 08-25 push-pull failure at 4 MHz stops mattering
  because the bus is being left behind rather than tuned.
- **FoV confirmed from the datasheet (p.15): FOVX 67.9°, FOVY 52.8°, 80°
  diagonal.** The 08-13 ceiling run derived 67° × 51° independently from
  slant-range geometry — **agreement to ~1%**, an unplanned end-to-end
  validation of the decode chain, the 0.25mm LSB and the zone ordering all at
  once. Rule of thumb worth carrying: **coverage width ≈ 1.35 × distance,
  height ≈ 1.0 × distance.**
- **Application geometry stated for the first time, and it is close range:
  feet must be detected at 8 inches (~203 mm).** Three consequences, all of
  which move previously-settled decisions:
  - Coverage at 203 mm is **274 × 202 mm** — roughly one foot. A single sensor
    **cannot span a ~70 cm bore** from that standoff; that needs ~700 mm of
    standoff, or several sensors.
  - **Range stops being a selection criterion.** Every mode is spec'd 13×+
    beyond 203 mm, so the 08-25 choice of 16×16 (decided on reach in a 3-4 m
    room) does not bind here. See the REOPENED note in Current status.
  - A foot spans ~15×7 zones at 16×16 and ~40×17 at 48×32 at this range. The
    system is **not resolution-starved, it is coverage-starved** — which is a
    different problem with different answers (standoff, sensor count, pose).
  - **Unresolved and blocking:** whether 8 inches is the *sensor standoff* or
    the *trigger threshold* with the sensor mounted further back. This decides
    sensor count, mounting and mode. Settle it first.
- **Product shape clarified.** In the installed system the interface is a
  **3-pin connector (power/ground/data)** carrying one digital signal that
  tells the SPECT detectors to move. The radio is **commissioning-only and off
  during imaging** — its job is proving the concept and, more importantly,
  **finding the right sensor placement**, since coverage cannot be confirmed by
  hoping it maps the right area.
- **Pivot: Phase 3 targets an ESP32-S3.** The reasoning chain, in order:
  SPI removes I3C as a selection constraint (ESP32 and nRF have none), so the
  wireless field opens; detection compute is trivial (256-1536 zones at 30 Hz
  is a fraction of any M4, ~1-2 kB for a background model), so MHz is not a
  criterion; what remains is radio, certification and ecosystem.
  **`ESP32-S3-WROOM-1`** is a pre-certified module with integrated antenna —
  no RF layout, no radio certification, which is the dominant cost term for a
  Siemens product. Streaming shape: **SoftAP + UDP**, so the laptop joins the
  sensor's own AP (no hospital network, no IT involvement) and frames are
  dropped rather than retransmitted — the same latency-over-completeness
  choice the viewer's single-slot frame buffer already makes. Bandwidth is a
  non-issue: 192 kbit/s at 16×16, 559 kbit/s at full 48×32.
  **One MCU can drive several TMF8829s on one SPI bus with separate
  chip-selects**, fused into a common coordinate frame — the strongest argument
  for a custom PCB over the Click, and a reason to design the host tooling for
  *N* posed sensors from the start.
- **What ports and what does not.** The transport shim is rewritten either way
  (I3C → SPI), so "preserve the STM32 work" is a weaker argument than it looks.
  Portable: the zone decoder, the binary frame format and both its CRCs, and
  all of `tools/`. Not portable: `i3c*` in `tmf8829_shim.cpp`, the
  `HAL_I3C_MODULE_ENABLED` build flag, pyocd/mbed flashing.
- **Ordering that wastes least effort:** (1) build the placement/coverage
  visualisation host-side — pure Python against the existing serial stream, no
  hardware change, and it is what answers the 8-inch geometry question;
  (2) prove the SPI transport on the existing Click + H5 rig, which yields a
  working SPI shim to port; (3) move to the ESP32-S3 dev board; (4) PCB last,
  since working distance, sensor count and mode are its inputs.
- **The real risk in the custom-PCB plan is optical, not electrical** —
  cover-glass standoff, aperture geometry, and the crosstalk barrier between
  VCSEL and receiver. That failure mode looks exactly like the Phase 1
  high-confidence 0 mm corner block. Use ams's reference layout rather than
  deriving the optical stack.

### 2026-08-25 — 16×16 chosen over 48×32 on measured evidence; frame-rate deficit root-caused to the I3C clock; viewer no-return logic corrected
- **Operating mode settled by experiment, and it reverses the phase's working
  assumption.** Same scene, same sensor position, one keypress apart:

  | | 48×32 (preconfig 71) | 16×16 (preconfig 67) |
  |---|---|---|
  | no-return | 20% | **4%** |
  | frame rate | 5.4 fps | **30.3 fps** |
  | 3-4m walls | invisible | resolved, mid-ramp |

  48×32 reported the room's walls as empty space. Not a defect —
  **datasheet Table 5 (p.16) specifies 48×32 to 2700mm** against a 90% white
  card at centre pixel (1900mm at a corner, 1200mm for an 18% grey card),
  versus **5900mm for 16×16**. The headline 11000mm belongs to 8×8 long range
  at 2000k iterations *only*. Full resolution was the phase goal; 16×16 is the
  operating point.
- **Iteration tuning cannot fix this, and the datasheet supplies the numbers to
  prove it.** The 8×8 long-range rows give two points on one curve: 300k
  iterations → 8000mm, 2000k → 11000mm. That is range ∝ iterations^**0.17** —
  ambient-limited, far weaker than the shot-noise-limited ^0.25 one would
  assume. Lifting 48×32 from 2700mm to 5000mm would need ~**39×** the
  iterations. No frame-time budget permits it. **Mode selection is the range
  lever; `KILO_ITERATIONS` is not.**
- **Frame-rate deficit root-caused by measurement.** `t` at 48×32 reports
  `bus=75028 emit=11710 period=93815` µs/sub-frame — bus+emit is **92% of every
  cycle**, so we are the bottleneck. But **the UART is innocent**: a 48×32
  sub-frame is 2328 bytes, which at 2 Mbaud is 11.6ms, matching `emit` almost
  exactly. The cost is **I3C: 75ms to move 2316 bytes = 31 kB/s**, on a bus
  specified to 12.5 MHz. Cause is in `i3c1PeripheralInit()` —
  `SCLPPLowDuration`/`SCLI3CHighDuration` at 500ns each is a **1 MHz** clock,
  the conservative Stage 1 bring-up value, never revisited. (The prior
  hypothesis in the shim's own comment — that emit was the bottleneck and DMA
  TX was the fix — is now disproven.)
- **Three viewer defects fixed, all of which were inflating the no-return
  statistic that decisions were being made on. 48% → 20% on the same scene, at
  48×32, with no sensor change whatsoever.**
  - **`no_return` used OR where it needs AND.** `(dist==0)|(conf==0)` painted
    as absent any zone reporting 0mm with a *real* confidence — which is not a
    no-return but either an object at the 10mm minimum range or a **distant
    object aliased onto 0mm** (§7.7.2: at the default VCSEL clock an object at
    7.6m aliases to 0m). Now AND, with `dist==0 & conf>0` given its own
    magenta colour and a `zero-dist %` counter.
  - **`no_return` was evaluated on the *smoothed* array.** A zone returning
    real data in 2 of 5 frames has a plain median of 0 and was recorded as
    permanently dead — a filter tuned to delete exactly the intermittent weak
    far returns worth seeing. At 5.4fps that window spans nearly a second.
  - **The median ran across no-return zeros**, dragging flickering zones toward
    0 even when most frames carried data. New `smooth_valid()` medians over
    valid samples only; a zone with no valid sample anywhere stays 0/0 and is
    honestly reported as a no-return. Verified offline: a zone reading 2500mm
    in 2 of 5 frames now returns 2500mm where the old path returned 0.
- **Colour ramp now follows the active preset** (`PRESET_MAX_MM`), because a
  fixed 11000mm ramp is actively misleading outside 8×8 long range — at 48×32
  three quarters of the palette is unreachable and it implies reach the mode
  does not have. Two caveats worth keeping: the datasheet has **two different
  "maximum" numbers** (Table 5 = detection distance, where returns stop;
  Table 6 = the configured unambiguous *window* set by `histogram_bins`, past
  which objects alias back down), so the table cites its source per entry and
  uses Table 6 for the high-accuracy presets, where 64 bins bind at 1.4m well
  before detection does. And **`fp_mode` cannot identify the preset** —
  64/65/66 are all FP_8x8A yet span 5700/11000/5700mm — so the preset is
  tracked from the board's own `Preconfig NN` reply **via `on_text`**, never by
  scanning the rolling `text_lines` buffer (the trap recorded on 08-19).
- **New I3C error signature, distinct from the wedge:** `I3C-TX failed
  regAddr=0xE1 status=1 hi3c1.ErrorCode=0x100`. `0xE1` is INT_STATUS
  (datasheet p.49); `status=1` is `HAL_ERROR` and `0x100` is
  **`I3C_SER_ANACK`** — an address NACK, not the wedge's `0xF8`/`HAL_TIMEOUT`/
  `0x20000`. Single occurrence at a preset change, recovered on its own. Recorded
  because an ANACK is an **addressing-phase** failure, i.e. exactly the class
  that worsens with faster bus timing — so this is the baseline to compare
  against after the clock change above.
- Datasheet sections read properly for the first time and worth knowing:
  **§7.1** (how the 0.25mm resolution is achieved from 200ps/~30mm bins — peak
  centroid over 600k pulses, plus an internal reference SPAD cancelling
  VCSEL/TDC/temperature drift); **§7.5.1** (factory calibration covers 8×8 and
  16×16 only, using 24 and 6 SPADs/zone; 32×32 and 48×32 use **1-2 SPADs/zone**
  and *reuse* the 16×16 calibration, hence more offset error — this is the
  quantified version of the optical-budget argument); **p.17** (precision is
  **2mm + 0.5% of distance at 2σ**, accuracy ±3% beyond 300mm, and a footnote
  warning that **SPAD "screamers"** give a few pixels permanently degraded
  range in low ambient light).
- Also: footer bands re-spaced (`FOOTER_H` 92→116 — the colour bar ended at
  +28 and the first board-reply baseline was +36, so board text was drawn
  through the ramp), and canvas padding darkened from (24,24,24) with an
  explicit border, since it was indistinguishable from `NO_RETURN_COLOUR`
  (28,28,28) and made a quarter of a 16×16 window look like dead zones.
- **I3C clock raised to 4 MHz and reverted the same session — the interconnect,
  not the silicon, is the limit.** 170ns/80ns (4 MHz) built and flashed clean
  but left the device unable to boot: `pwup ENABLE=0x4`, `#Err,CPU not ready`,
  chip version 0.0, serial 0x0. **Diagnostic split worth keeping: SETDASA
  succeeded** (no `I3C-SETDASA failed` line), so the open-drain addressing
  phase tolerated 4 MHz and the *push-pull private transfers* were what broke.
  That is consistent with the Click being on jumper wires rather than a shield
  — unshielded flying leads degrade edges well before the part's 12.5 MHz
  ceiling matters. Reverted to 500/500; a step ladder (500/250, 330/330,
  250/250, 170/170) is recorded above the constants in `tmf8829_shim.cpp`.
  Method note: **4× was too large a first step.** The failure mode is a dead
  device rather than a degraded one, so there is no host-side sweep — every
  step is a flash-and-test, and a bisection should have started at 500/250.
- A `I3C kclk=…MHz ppLow=… high=… -> SCL~…kHz` banner was added to
  `i3c1PeripheralInit()` and prints at open and on every bus recovery, because
  `i3cNsToCycles()` truncates and the I3C1 kernel clock is whatever the Arduino
  core's clock tree gives. **Not yet captured on hardware** — it needs the
  monitor open *before* an upload, since it only prints at startup. Until it is,
  the MHz figures in the step ladder are arithmetic, not measurement.

### 2026-08-19 — Renderer fixed: the "deadzones" were the viewer deleting real data
- **Complaint was dropouts and poor far-field detail. Measured the scene before
  touching anything, and the sensor was not at fault — the viewer was.** With
  the old fixed `conf_min=40` mask, **18.4% of zones held real measurements and
  were being painted flat gray**:

  | distance | share of scene | median conf | masked at 40 |
  |---|---|---|---|
  | 0-500 mm | 52.1% | 128 | 1.4% |
  | 500-1000 | 11.3% | 42 | **41.8%** |
  | 1000-1500 | 12.4% | 40 | **50.0%** |
  | 1500-2000 | 15.9% | 54 | 26.9% |
  | 4000-6000 | 2.5% | 7 | **100%** |

- **Root cause: confidence falls as ~1/distance, so no fixed threshold can
  work.** Measured medians 128 @250mm, 42 @750mm, 7 @5000mm — 3× and 20× in
  range give 3.0× and 18× in confidence, i.e. inverse-linear, *not* the
  inverse-square an optical-power argument would predict. Tune the threshold
  for the near field and it deletes the far field; tune it for the far field
  and it accepts noise up close.
- Fixes in `tools/tmf8829_viewer.py`, all measured rather than guessed:
  - **Adaptive confidence floor** `conf_k / distance` (default k=15000, chosen
    to track the measured 10th percentile of genuine returns, so ~90% of real
    data stays fully coloured at every range). `--conf-mode fixed|off` keeps
    the old behaviours.
  - **Shading instead of a hard cutoff.** Marginal zones now fade toward gray
    in proportion to confidence rather than vanishing. The binary cutoff is
    what made uncertain data look like holes in the scene.
  - **No-return zones are their own colour.** A zone with nothing to report
    always comes back distance 0 *and* confidence 0 (verified: min=max=0 across
    every such sample) — and distance 0 lands at the **near** end of the ramp,
    i.e. bright red, reading as "object touching the sensor". The old fixed
    mask happened to hide these, so the bug only appeared on pressing `M`.
    They now render near-black and stay distinct with shading off.
  - **Colour range: fixed at 100-11000mm**, the datasheet's maximum detection
    range (Table 5, p.16). Auto-fitting to the scene was tried and made the
    default, then **reverted on user direction** — the point of this work is
    characterising what the sensor can do, and a range that follows the scene
    destroys cross-session comparability and hides the sensor's real reach.
    `--dist-auto` remains available, off by default.
  - **`--dist-gamma`** (default 1.0 = linear) exists because of the tension a
    full-scale ramp creates: a linear 0-11m map puts a 0-2m indoor scene in the
    bottom fifth. A gamma below 1 gives the near field more of the ramp while
    still spanning the full range. The colour bar inverts the gamma when
    labelling, so the midpoint tick stays truthful.
  - **Colour bar with mm labels**, in a new footer strip. Status text used to be
    drawn straight onto the depth image, covering the bottom rows of zones.
- **Verified against live data, before/after on the same frame**: auto range
  chose 74..1677mm, 18.0% of zones recovered from gray into real structure, and
  the 5.5% that genuinely have no data stayed visibly distinct.
- `colourise()` was extracted so the GUI and offline tooling share one
  implementation — a rendering question can now be answered without opening a
  window. Offline tests cover the adaptive floor against the measured
  percentiles, SpanTracker settling/exclusion behaviour, the blend maths, and
  no-return distinctness.
- Trap worth keeping: **`cv2` is imported lazily inside `run_gui()`** so
  `--no-gui` works on a host without OpenCV. A module-level helper that uses
  `cv2` breaks that; import it inside the function.

### 2026-08-19 — `tools/tmf8829_soak.py`: unattended soak with automatic wedge recovery
- Closes the gap the soak above exposed. The firmware recovers the *bus*; this
  recovers the *device*, by replaying `s`→`d`→`e`→`m`→`v` when it sees
  `I3C bus wedged` — so a run now survives an arbitrary number of wedges. It
  logs the clean interval before each one, which is what turns "it wedges
  sometimes" into a distribution worth reasoning about.
- **`FrameParser` gained an `on_text` callback** (and a configurable
  `text_history`). This is the structural fix for the bug that made the first
  soak misreport: `text_lines` is a rolling buffer sized for the viewer's
  2-line display, so **polling it — or its length — silently drops lines the
  moment the board repeats an error fast enough to saturate it**. `on_text`
  fires once per line as parsed. Anything that must not miss a line must use
  it. Viewer defaults are unchanged (`on_text=None`, history 12).
- **`--self-test` exercises the restore path once and exits.** The restore code
  is by definition the code that runs when nobody is watching, so it should not
  first execute eight hours into an unattended run. Confirmed on hardware:
  90 frames before, restore, 90 frames after — 30 fps either side.
- Robustness choices, each from something actually observed today: streaming is
  turned on by **reading back** `Binary streaming ON` rather than pressing once
  (the toggle survives disable/enable, and a blind press turns it off); the
  preconfig is reselected after every restore by **reading back** `Preconfig NN`
  (`configNr` is not reset by `e`); the stall threshold is 20 s because a
  legitimate `e` stops frames for ~4 s; and a `recovery failed` line stops the
  run rather than looping, since that state needs a board reset.

### 2026-08-19 — WEDGE REPRODUCED AND RECOVERED IN SOFTWARE; DISEC did not prevent it
- **The soak finally caught it, and the recovery path works.** 16×16 binary
  streaming ran **16.5 minutes / ~29,600 frames at 29.9 fps with zero CRC
  errors**, then wedged with the exact 08-13 signature:
  `I3C-TX failed regAddr=0xF8 status=3 hi3c1.ErrorCode=0x20000` (`HAL_TIMEOUT`,
  on the routine per-frame status poll). `i3cRecoverBus()` tripped on its 3rd
  consecutive failure and power-cycled the device.
- **Two conclusions, one negative and one positive:**
  - **The DISEC IBI-disable did NOT prevent the wedge.** That hypothesis, open
    since 08-13, is now answered: disabling target-initiated events is not the
    fix. The underlying cause remains unknown.
  - **The recovery does work, end to end, with nobody touching the board.**
    Confirmed by hardware probe, not by log text: after the power-cycle,
    `Chip Version 158.1` read back correctly — which requires working I3C
    transactions, so the bus is genuinely restored.
- **Restoring the bus is not enough to resume measuring** — exactly as the
  recovery message says. The power-cycle resets the sensor, so it comes back in
  the **bootloader state** (`Firmware Application Version 128.33.0.0`, serial
  `0x0`) with a stale `state=measure`. That is the same signature recorded on
  08-17, and the same fix applies: **`s` → `d` → `e`**, which restored
  `1.2.200.0` / serial `0x51067D`, then `m` + stream toggle brought back
  **30 fps streaming**. No power cycle, no reflash, no physical access.
- **So the gap is now precisely defined, and it is small**: recovery is
  automatic up to the bus, and manual from there. A host-side watchdog that
  sees the `I3C bus recovered` line and replays `s`→`d`→`e`→`m` would close it
  and make long unattended runs viable. That is the natural next task.
- Time-to-failure now has two samples — **23+ min and 16.5 min** — both after a
  long clean stretch at full rate. Consistent with random onset rather than a
  periodic trigger, and not obviously load-related. Still too few samples to
  characterise; more soaks would need the watchdog above to be worth running.
- **Two host-script traps hit while measuring this, both worth avoiding again:**
  - `FrameParser.text_lines` is a **rolling 12-entry buffer**, so
    `len(lines) != last_len` as a change detector **silently stops working**
    once it saturates — which is exactly what happens when an error line
    repeats. That is why the soak log captured the wedge but missed the
    `recovered` verdict. Scan buffer contents, never its length.
  - The stream toggle is **stateful across sensor disable/enable** (it lives in
    MCU RAM). A script that blindly sends it can turn streaming *off* right
    before its own capture window — which happened here and produced a
    misleading "not streaming" result. Always read back the
    `Binary streaming ON/OFF` reply and re-toggle if needed.

### 2026-08-19 — `b` returned to the vendor app; stream toggle moved to `v`; ams GUI path evaluated and declined
- **Found a real defect while scoping the ams EVM GUI: our shim was shadowing
  `b`.** The vendor app uses `b` to enter binary input mode
  (`tmf8829_app.cpp:743` → `enterBinaryInputMode()`), which is the only channel
  for the `0x31` set-config and `0x32` set-preconfig commands. Our
  `inputGetKey()` consumed `b` for the binary-stream toggle, so that protocol
  was unreachable — and the comment above the intercept asserted the opposite
  ("Neither key is used by the vendor app, so nothing is shadowed"), which is
  how it survived. `f` and `t` were and remain genuinely free.
- **This mattered more than it looked.** The config page carries
  `TMF8829_CFG_PERIOD_MS` (0x22/0x23) and `TMF8829_CFG_KILO_ITERATIONS`
  (0x24/0x25) — measurement period and iteration count, i.e. exactly the knobs
  Stage 4 needs. We had no way to set them; `c` only cycles the nine fixed
  preconfigs. So this was blocking Stage 4 without anyone noticing.
- Fixes, both in `tmf8829_shim.cpp`: stream toggle moved `b` → **`v`**, and the
  intercept is now **skipped entirely while `isInBinaryInputMode()`**. The
  second half is the subtler bug: without it, a payload byte equal to
  `f`/`v`/`t` (0x66/0x76/0x74 — all plausible inside a 190-byte config page)
  would be eaten by the shim and the app would wait forever for a byte that had
  already been consumed. `isInBinaryInputMode()` is in no header, so it is
  declared `extern` in the shim; both files are C++ so the mangled names match.
- Host side: `tools/tmf8829_viewer.py` forwards `v` instead of `b`, and **`b` is
  deliberately excluded from the forwarded set** — forwarding it would drop the
  board into binary input mode and swallow subsequent keys.
- **Verified on hardware, three assertions, all passing**: (1) `b` now reaches
  the app and prints `Binary input mode active`; (2) a following `0x66` is
  reported as `#Err,BinaryCmd,66` — proving `f` was passed through as payload
  rather than intercepted, which is the mode-guard test; (3) `v` still streams —
  **449 frames, 29.9 fps, 0 header and 0 payload CRC errors** in steady state,
  matching the pre-change 30.3 fps baseline.
- Measurement note worth reusing: `FrameParser.text_lines` is a **rolling
  12-entry buffer**, so index-based marking into it is invalid once the ASCII
  preview floods it — scan the whole buffer. And the ASCII→binary transition
  costs exactly one header-CRC resync (a false sync inside the trailing ANSI
  preview); measure steady state with a fresh parser or that lone error looks
  like a link fault.
- **ams EVM GUI: evaluated, declined as infrastructure.** Findings, since this
  will come up again:
  - The GUI never touches hardware. It is a ZeroMQ client; ams ships three
    server backends (Raspberry Pi Zero W = host type 3, Arduino over UART = 2,
    an **STM32H503 USB-to-I²C bridge** on the shield = 4). Loggers are separate
    clients on the same bus, so the GUI would *not* block a detection client.
  - But we have no shield and no EVM, so the entry price is writing a zmq server
    adapter — and what it buys is a config panel plus a viewer we already have.
    At 32×32/48×32 the GUI shows z **as colour only, no numbers** (UG §3.2,
    p.14), which is worse than our `f` dump.
  - Decisive: everything we need from it is reachable directly via `u` +
    `0x31`. The GUI is a UI over a protocol we can now drive ourselves.
  - Frame-rate reality check, relevant to Stage 4: **~15 fps is the sensor's own
    ceiling at 48×32** (66 ms cycle, datasheet Table 5; ams marketing
    independently states "up to 15 fps"). And ams's own Arduino README warns
    that at 32×32/48×32 the Uno path loses frames unless the measurement period
    is raised — that is an Uno I²C/UART limit we already engineered past with
    I3C and `DATA_BUFFER_SIZE = 2400`, not something to inherit.
  - No community evidence exists either way — no forum threads, no issues, no
    write-ups. All findings above are vendor-authored. Third-party hardware
    running this sensor does exist (ProtoCentral's breakout, ESP32/RP2040/SAMD),
    but it ships its own visualiser, not the ams GUI.
- Keep the GUI installed: it remains the one **independent implementation** to
  cross-check our empirically-derived sub-frame interleave and `fliplr`
  orientation against, if a shield or EVM ever turns up. Not worth writing an
  adapter to obtain.

### 2026-08-19 — USB/mbed flashing restored as the primary path; pyocd demoted to fallback
- **The USB mass-storage fault is resolved as an operational blocker.** The
  user reports it was USB driver weirdness specific to these work machines,
  and now knows how to fix it if it recurs — so the two prior entries' framing
  ("recurring fault, default to pyocd until IT resolves it") is superseded:
  a wedged mbed disk is now a known-fixable speed bump, not a reason to route
  around the mass-storage stack permanently.
- `platformio.ini` back to `upload_protocol = mbed` as primary, with the pyocd
  `custom`/`upload_command` pair kept commented directly beneath it. **Verified
  by actually flashing**, not by seeing a drive letter: `D:` mounts as
  `NOD_H563ZI`, and `pio run --target upload` reported `Auto-detected: D:\` →
  `Firmware has been successfully uploaded.` (65752 B flash, 11556 B RAM —
  unchanged from the pyocd builds, so the image is the same one).
- Commands section updated to match, and the pyocd **reset** invocation
  (`pyocd reset -t stm32h563zitx`) recorded explicitly — that one is still
  worth keeping regardless of flash path, since it resets the board without
  reflashing while the monitor holds COM5.
- Nothing about the firmware changed here. The open items below are untouched:
  Stage 4 frame-rate validation, and the DISEC/recovery build still un-soak-tested.

### 2026-08-18 (later same day) — WdDevFlt re-wedged hours after the reboot; back to pyocd, and this is now confirmed recurring
- **The fault came back the same day.** `Get-WinEvent` on `Kernel-PnP` event
  **219** shows fresh `WdDevFlt failed to load, status 0xC000038E` entries
  starting ~11:13am, for the mbed drive *and* two different USB sticks
  (`General USB_Flash_Disk`, `Verbatim STORE_N_GO`) — same signature as every
  prior occurrence, just hours after the reboot below had cleared it.
- **Conclusion: this is a recurring fault on this machine, not a one-time
  glitch a single reboot permanently fixes.** A reboot is a temporary
  workaround, not a resolution — expect it to resurface again within a
  session. Worth reporting to IT with the diagnostic specifics already on
  record (`WdDevFlt` boot-start service, `Kernel-PnP` 219, `0xC000038E`,
  reproducible on any USB mass-storage device) rather than rebooting through
  it indefinitely.
- `platformio.ini` switched back to `upload_protocol = custom` (pyocd/SWD),
  re-verified working (`Erased 8192 bytes ... programmed 1024 bytes ...
  identical 65536 bytes`) — unaffected by the driver fault, as expected. The
  `mbed` block is kept commented, to swap back in only after a reboot when
  the drive is confirmed mounted again.
- **Practical guidance going forward**: default to pyocd/SWD for flashing on
  this machine rather than treating `mbed` as the steady-state primary — flip
  to `mbed` opportunistically, not as a fix to leave in place.

### 2026-08-18 — WdDevFlt USB mass-storage fault cleared by a reboot; `upload_protocol` back to `mbed`
- **The Defender Device Control fault from the 08-17 entry below is gone.**
  A reboot brought back the ST-LINK's mbed virtual disk (`D:`) *and* an
  unrelated personal USB flash drive that had been failing to mount on this
  machine since at least 08-13 — confirming the fault was general to USB mass
  storage, not mbed-specific, and that a reboot (not admin rights, not a
  policy change) is what clears a wedged `WdDevFlt` load.
- Verified by actually flashing, not just by seeing the drive letter:
  `upload_protocol = mbed` build+upload succeeded (`Auto-detected: D:\`,
  `Firmware has been successfully uploaded.`), and a subsequent SWD reset
  captured the boot banner (the shim's `f`/`b`/`t` lines), confirming the
  image written via mbed is what's actually running.
- `platformio.ini` reverted to `upload_protocol = mbed` as primary; the
  pyocd/SWD command from 08-17 is kept commented in place as the fallback
  if this driver wedges again (it doesn't depend on the mass-storage stack
  at all, so it's the more robust of the two long-term).
- **Not yet known: whether/when this recurs.** No root cause for why
  `WdDevFlt` got stuck in the first place was found (only that a reboot
  fixes it), so this may resurface after a future update or sleep/wake
  cycle. If `mbed` upload fails again with a missing upload disk, that's
  the signal to flip back to the commented pyocd lines.

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
