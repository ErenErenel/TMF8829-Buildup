#include <Arduino.h>
#include "tmf8829_app.h"

// Bring-up step 3: full ams driver integration.
// This is a thin wrapper around the vendored ams-OSRAM tmf8829_driver_arduino
// application layer (lib/tmf8829_driver_arduino) -- see its shim
// (tmf8829_shim.h/.cpp) for the NUCLEO-H563ZI-specific pin/I2C adaptation.
//
// Interactive over serial at 1,000,000 baud (ams' own README notes
// their default of 2,000,000 can show comms errors and recommends at
// least 1,000,000 -- we saw exactly this at 115200: printing a full
// result/zone-grid frame took long enough to delay the loop and trip
// the driver's auto-stop-on-error path). Send single characters to
// control the device, e.g.:
//   e = enable + download firmware (watch appid 0x80 -> 0x01)
//   c = select next config (defaults to 8x8 first)
//   m = start measuring, s = stop, h = help, + / - = log verbosity
#define UART_BAUD_RATE 1000000
#define I2C_CLK_SPEED  400000

void setup() {
  setupFn(2 /* log-level idx */, UART_BAUD_RATE, I2C_CLK_SPEED);
}

void loop() {
  loopFn();
}
