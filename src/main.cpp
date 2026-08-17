#include <Arduino.h>
#include "tmf8829_app.h"

// Full ams driver integration -- thin wrapper around the vendored
// ams-OSRAM tmf8829_driver_arduino application layer
// (lib/tmf8829_driver_arduino) -- see its shim (tmf8829_shim.h/.cpp) for
// the NUCLEO-H563ZI-specific pin/transport adaptation. As of Phase 2/3
// Stage 2, the shim's transport is I3C (was I2C in Phase 1) -- see
// CLAUDE.md changelog.
//
// Interactive over serial at 4,000,000 baud. Send single characters to
// control the device, e.g.:
//   e = enable + download firmware (watch appid 0x80 -> 0x01)
//   c = select next config (defaults to 8x8 first)
//   m = start measuring, s = stop, h = help, + / - = log verbosity
//   b = binary streaming, f = full-res dump, t = timing report (all shim)
//
// Raised from 1,000,000: tmf8829ReadResults() reads, decodes and emits a
// sub-frame inline, so all of it has to finish inside the sensor's 33ms
// sub-frame cadence. At 1 Mbaud the emit alone was ~16ms of that at 32x32
// (~23ms at 48x32), enough to overshoot the deadline -- and because a full
// image needs both sub-frames, losing halves costs whole images: 8.1fps
// measured at 32x32 against a nominal 15.2. ams' README warns that their
// 2,000,000 default "could show communication errors", but that was on an
// Arduino Uno; here the ceiling is the ST-LINK V3E's VCP, not the MCU. If
// frames stop arriving or CRC errors appear, step down to 2000000 -- and
// keep monitor_speed and the tools' --baud defaults in step (see MANUAL.md).

#define UART_BAUD_RATE 2000000
#define I2C_CLK_SPEED  400000 // vestigial for I3C -- kept for setupFn()'s signature, see tmf8829_shim.cpp i2cOpen()

void setup() {
  setupFn(2 /* log-level idx */, UART_BAUD_RATE, I2C_CLK_SPEED);
}

void loop() {
  loopFn();
}
