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

**Phase:** Phase 1 — I2C, 8×8, Arduino framework (PlatformIO,
`board = nucleo_h563zi`, `framework = arduino`, `upload_protocol = mbed`)

**Status:** Phase 1 complete and pushed to GitHub (private repo:
https://github.com/ErenErenel/TMF8829-Buildup). Distance-to-color
terminal heatmap done, with low-confidence zones grayed out. Now
starting Phase 2 (I3C) on a dedicated branch, `phase2-i3c`, tracking
`origin/phase2-i3c` — `master` stays on the validated Phase 1 state as
a fallback while I3C work is iterated on.

**Definition of done for this phase:** live 8×8 distance/confidence
frame streaming from the TMF8829 to a PC over serial, via direct
(non-shield) wiring between the LightRanger 14 Click and the
NUCLEO-H563ZI.

**Open items:**
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

---

## Changelog

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
