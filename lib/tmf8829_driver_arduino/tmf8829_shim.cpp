/**************************************************************************************************
* Copyright © 2024 ams-OSRAM AG                                                                   *
* All rights are reserved.                                                                        *
*                                                                                                 *
* FOR FULL LICENSE TEXT SEE LICENSES-MIT.TXT                                                      *
*                                                                                                 *
**************************************************************************************************/

#include "tmf8829_shim.h"
#include "tmf8829.h"
#include "Arduino.h"
#include "stm32h5xx_hal_i3c.h" // not pulled in by Arduino.h by default (STM32duino core has no I3C wrapper)
#include <string.h>

// ============================================================================
// I3C transport (Phase 2/3, Stage 2). Replaces the original I2C/Wire-based
// transport below -- ported from the standalone Stage 1 test that proved
// this sequence on hardware (src/main.cpp history, see CLAUDE.md changelog
// 2026-08-12 "Stage 1 confirmed on hardware"). tmf8829.c/tmf8829_app.cpp are
// unmodified; this file is still the only place that knows the transport
// changed, per the shim's whole purpose.
// ============================================================================

#define TMF8829_STATIC_ADDR       0x41   // TMF8829_SLAVE_ADDR (tmf8829.h); legacy address until DAA
#define TMF8829_DYNAMIC_ADDR      0x50   // chosen; avoids 0x00-0x07, 0x78-0x7F and broadcast 0x7E

// SETDASA direct CCC code (0x87, MIPI I3C v1.0) -- not exposed as a HAL
// macro, only broadcast RSTDAA/ENTDAA are predefined in the HAL source.
#define I3C_CCC_SETDASA            0x87U

// Broadcast DISEC (Disable Events Command, 0x01) and its event bits. Used to
// stop the target raising In-Band Interrupts -- see i3cDisableTargetEvents().
#define I3C_CCC_DISEC_BROADCAST    0x01U
#define I3C_CCC_EVENT_ENINT        0x01U  /* IBI / target interrupt requests */
#define I3C_CCC_EVENT_ENCR         0x02U  /* controller-role requests */
#define I3C_CCC_EVENT_ENHJ         0x08U  /* hot-join requests */

// Consecutive transfer failures before the bus is treated as wedged and the
// device is power-cycled. >1 so a single glitch doesn't reset a running
// measurement, small enough that a real wedge is caught within a frame or two.
#define TMF8829_I3C_FAILURES_BEFORE_RECOVERY  3

static I3C_HandleTypeDef hi3c1;
static uint8_t  s_i3cDynamicAddr  = 0;
static bool     s_i3cAddrValid    = false;
static uint8_t  s_i3cFailStreak   = 0;
static bool     s_i3cRecovering   = false;

// ---- Frame-timing instrumentation ('t') ------------------------------------
// Splits the per-sub-frame budget into the two things that are serialized
// against it: time spent on the I3C bus, and time spent pushing bytes out the
// UART. Both matter because tmf8829ReadResults() does header-read, payload-read,
// decode and emit inline before returning, so all of it has to fit inside the
// device's sub-frame cadence (33ms; two sub-frames per image in the 32x32/48x32
// modes). Overshoot by even a millisecond and the next sub-frame is lost
// outright -- and since a full image needs both halves, losing halves costs
// whole images. That is the suspected cause of 8.1fps at 32x32 against a
// nominal 15.2, and this exists to replace the estimate with a measurement.
//
// 'period' is the device's own frame-to-frame delivery interval as seen by the
// host, so bus+emit < period means we are keeping up and bus+emit ~= period
// means we are the bottleneck.
static bool     s_timingOn           = false;
static uint32_t s_timingBusUs        = 0;   /* inside HAL_I3C_Ctrl_Transmit/Receive */
static uint32_t s_timingEmitUs       = 0;   /* inside streamZoneFrameBinary */
static uint32_t s_timingPeriodUs     = 0;   /* header-to-header, summed */
static uint32_t s_timingPeriodCount  = 0;
static uint32_t s_timingLastHeaderUs = 0;
static uint32_t s_timingFrames       = 0;

/* One short line per ~1s at 30fps -- ~50 bytes, i.e. 0.0125ms of wire time at
 * 4 Mbaud, small enough not to perturb what it measures. */
#define TMF8829_TIMING_REPORT_FRAMES  30

static void timingReset ( )
{
  s_timingBusUs = s_timingEmitUs = s_timingPeriodUs = 0;
  s_timingPeriodCount = s_timingFrames = s_timingLastHeaderUs = 0;
}

// MIPI I3C addresses (as transmitted on the wire, e.g. SETDASA's data byte)
// carry the 7-bit address in bits[7:1] with an odd-parity bit in bit[0].
static uint8_t i3cWithOddParityBit ( uint8_t sevenBitAddr )
{
  uint8_t v = sevenBitAddr & 0x7F;
  uint8_t ones = 0;
  for ( uint8_t i = 0; i < 7; i++ )
  {
    if ( v & ( 1 << i ) ) ones++;
  }
  uint8_t parity = ( ( ones % 2U ) == 0U ) ? 1U : 0U; // total set bits must be odd
  return (uint8_t)( ( v << 1 ) | parity );
}

static void i3c1GpioInit ( )
{
  __HAL_RCC_GPIOB_CLK_ENABLE( );

  GPIO_InitTypeDef gpio = {0};
  gpio.Pin       = GPIO_PIN_8 | GPIO_PIN_9; // PB8=I3C1_SCL, PB9=I3C1_SDA (AF3, per PeripheralPins.c)
  gpio.Mode      = GPIO_MODE_AF_OD;
  gpio.Pull      = GPIO_PULLUP; // belt-and-braces only: the Click already has R8/R9 (2.2k,
                                // populated) on the mikroBUS SDA/SCL we drive. R16/R17 being DNP
                                // is a redundant sensor-side pair -- see the 2026-08-13
                                // correction in CLAUDE.md; earlier notes here had this wrong.
  gpio.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF3_I3C1;
  HAL_GPIO_Init( GPIOB, &gpio );
}

// Datasheet-derived (DS001140 v2-00 p.14) but deliberately conservative
// (correctness over speed) -- see Stage 1 notes in CLAUDE.md. Not yet tuned
// for the full 12.5MHz SDR ceiling.
static uint8_t i3cNsToCycles ( uint32_t kernelClkHz, uint32_t ns )
{
  uint64_t cycles = ( (uint64_t)ns * kernelClkHz ) / 1000000000ULL;
  if ( cycles > 0xFF ) cycles = 0xFF;
  if ( cycles < 1 )    cycles = 1;
  return (uint8_t)cycles;
}

static HAL_StatusTypeDef i3c1PeripheralInit ( )
{
  __HAL_RCC_I3C1_CLK_ENABLE( );

  uint32_t kernelClkHz = HAL_RCCEx_GetPeriphCLKFreq( RCC_PERIPHCLK_I3C1 );

  hi3c1.Instance = I3C1;
  hi3c1.Mode     = HAL_I3C_MODE_CONTROLLER;

  LL_I3C_CtrlBusConfTypeDef * bus = &hi3c1.Init.CtrlBusCharacteristic;
  bus->SDAHoldTime         = HAL_I3C_SDA_HOLD_TIME_0_5;
  bus->WaitTime             = HAL_I3C_OWN_ACTIVITY_STATE_0;
  bus->SCLPPLowDuration     = i3cNsToCycles( kernelClkHz, 500 );
  bus->SCLI3CHighDuration   = i3cNsToCycles( kernelClkHz, 500 );
  bus->SCLODLowDuration     = i3cNsToCycles( kernelClkHz, 1000 );
  bus->SCLI2CHighDuration   = i3cNsToCycles( kernelClkHz, 1000 );
  bus->BusFreeDuration      = i3cNsToCycles( kernelClkHz, 1000 );
  bus->BusIdleDuration      = 0xFF; // hot-join timing; non-critical, HotJoinAllowed is DISABLE below

  HAL_StatusTypeDef status = HAL_I3C_Init( &hi3c1 );
  if ( status != HAL_OK ) return status;

  I3C_CtrlConfTypeDef ctrlConf = {0};
  ctrlConf.DynamicAddr      = 0x00; // we don't act as a target ourselves
  ctrlConf.StallTime        = 0;
  ctrlConf.HotJoinAllowed   = DISABLE;
  ctrlConf.ACKStallState    = DISABLE;
  ctrlConf.CCCStallState    = DISABLE;
  ctrlConf.TxStallState     = DISABLE;
  ctrlConf.RxStallState     = DISABLE;
  ctrlConf.HighKeeperSDA    = DISABLE;

  return HAL_I3C_Ctrl_Config( &hi3c1, &ctrlConf );
}

// SETDASA: assign TMF8829_DYNAMIC_ADDR to the device currently sitting at
// its static/legacy address. Must run after EN has been driven high (device
// resets to its default/static address on EN low->high) and before any
// txReg/rxReg call. NOT I3C_DIRECT_WITH_DEFBYTE_STOP -- that's for MIPI's
// formal "Defining Byte" CCCs (ENEC/DISEC/RSTACT); using it here zeroed the
// HAL's programmed data-phase byte count and silently dropped the new-
// address byte (Stage 1 bug, confirmed on hardware -- see CLAUDE.md).
static bool i3cAssignDynamicAddress ( )
{
  uint8_t setdasaByte = i3cWithOddParityBit( TMF8829_DYNAMIC_ADDR );
  uint8_t cccDataBuf[1] = { setdasaByte };
  uint8_t txDataBuf[1]  = {0};

  I3C_CCCTypeDef setdasaDesc = {0};
  setdasaDesc.TargetAddr     = TMF8829_STATIC_ADDR;
  setdasaDesc.CCC            = I3C_CCC_SETDASA;
  setdasaDesc.CCCBuf.pBuffer = cccDataBuf;
  setdasaDesc.CCCBuf.Size    = 1;
  setdasaDesc.Direction      = HAL_I3C_DIRECTION_WRITE;

  uint32_t ctrlWords[2]; // Direct CCC needs 2 control words per frame
  I3C_XferTypeDef xfer = {0};
  xfer.CtrlBuf.pBuffer = ctrlWords;
  xfer.CtrlBuf.Size    = 2;
  xfer.TxBuf.pBuffer   = txDataBuf;
  xfer.TxBuf.Size      = 1;

  HAL_StatusTypeDef status = HAL_I3C_AddDescToFrame( &hi3c1, &setdasaDesc, NULL, &xfer, 1, I3C_DIRECT_WITHOUT_DEFBYTE_STOP );
  if ( status != HAL_OK )
  {
    PRINT_STR( "AddDescToFrame(SETDASA) failed, status=" );
    PRINT_INT( status );
    PRINT_LN( );
    return false;
  }
  status = HAL_I3C_Ctrl_TransmitCCC( &hi3c1, &xfer, 100 );
  if ( status != HAL_OK )
  {
    PRINT_STR( "TransmitCCC(SETDASA) failed, status=" );
    PRINT_INT( status );
    PRINT_STR( " hi3c1.ErrorCode=0x" );
    PRINT_UINT_HEX( hi3c1.ErrorCode );
    PRINT_LN( );
    return false;
  }
  return true;
}

// Broadcast DISEC, disabling target-initiated events -- primarily ENINT, i.e.
// In-Band Interrupts.
//
// Rationale: an I3C target requests an IBI by pulling SDA low during the bus
// available condition, and expects the controller to service it. This
// controller never does -- HAL_I3C_ActivateNotification() is not called and
// HotJoinAllowed is DISABLE (see i3c1PeripheralInit) -- so an unserviced
// request leaves SDA held low and every subsequent transfer fails with
// HAL_TIMEOUT, permanently, after an arbitrary period of correct operation.
// That matches the observed "streams fine for a while, then every transfer
// times out" failure, and has no I2C analogue, which is consistent with the
// bus having been solid throughout Phase 1. The datasheet (DS001140 v2-00
// section 7.10, page 37) confirms the device implements IBI but does not say
// whether it raises them unprompted -- so this is a hypothesis under test, not
// a known fix. Deliberately non-fatal: if it is wrong, nothing is lost.
//
// Unlike SETDASA (see the Stage 1 bug above), the event byte here IS a true
// MIPI Defining Byte -- it follows the CCC code in the same phase rather than
// being per-target write data -- hence WITH_DEFBYTE, the opposite choice.
static bool i3cDisableTargetEvents ( )
{
  uint8_t events[1] = { I3C_CCC_EVENT_ENINT | I3C_CCC_EVENT_ENCR | I3C_CCC_EVENT_ENHJ };

  I3C_CCCTypeDef disecDesc = {0};
  disecDesc.TargetAddr     = 0x00;  // broadcast; a single device, so no need to target it
  disecDesc.CCC            = I3C_CCC_DISEC_BROADCAST;
  disecDesc.CCCBuf.pBuffer = events;
  disecDesc.CCCBuf.Size    = 1;
  disecDesc.Direction      = HAL_I3C_DIRECTION_WRITE;

  uint32_t ctrlWords[1];   // broadcast frame needs one control word
  I3C_XferTypeDef xfer = {0};
  xfer.CtrlBuf.pBuffer = ctrlWords;
  xfer.CtrlBuf.Size    = 1;
  xfer.TxBuf.pBuffer   = events;
  xfer.TxBuf.Size      = 1;

  if ( HAL_I3C_AddDescToFrame( &hi3c1, &disecDesc, NULL, &xfer, 1, I3C_BROADCAST_WITH_DEFBYTE_STOP ) != HAL_OK )
  {
    PRINT_STR( "I3C-DISEC AddDescToFrame failed" );
    PRINT_LN( );
    return false;
  }
  HAL_StatusTypeDef status = HAL_I3C_Ctrl_TransmitCCC( &hi3c1, &xfer, 100 );
  if ( status != HAL_OK )
  {
    PRINT_STR( "I3C-DISEC failed status=" );
    PRINT_INT( status );
    PRINT_STR( " hi3c1.ErrorCode=0x" );
    PRINT_UINT_HEX( hi3c1.ErrorCode );
    PRINT_LN( );
    return false;
  }
  return true;
}

// Power-cycles the device and rebuilds the I3C link after the bus has wedged.
//
// A wedged bus can't be talked out of it: re-running SETDASA alone would fail,
// because the device still holds its dynamic address and no longer answers at
// the static one. Driving EN low is what actually resets it back to an
// addressable state -- which also means recovery is NOT transparent, since the
// device loses its downloaded firmware. Hence the explicit instruction to
// press 'e'; silently pretending to recover would be worse than saying so.
static void i3cRecoverBus ( )
{
  s_i3cRecovering = true;
  s_i3cAddrValid  = false;

  PRINT_STR( "I3C bus wedged (" );
  PRINT_INT( s_i3cFailStreak );
  PRINT_STR( " consecutive failures) -- power-cycling device" );
  PRINT_LN( );

  HAL_I3C_DeInit( &hi3c1 );
  digitalWrite( ENABLE_PIN, LOW );
  delay( 20 );                      // let the rail and any held-low line settle
  i3c1GpioInit( );
  i3c1PeripheralInit( );
  digitalWrite( ENABLE_PIN, HIGH );
  delay( 10 );                      // datasheet tPOR max 2ms

  if ( i3cAssignDynamicAddress( ) )
  {
    s_i3cDynamicAddr = TMF8829_DYNAMIC_ADDR;
    s_i3cAddrValid   = true;
    i3cDisableTargetEvents( );
    PRINT_STR( "I3C bus recovered -- device was reset, press 'e' to re-download firmware" );
  }
  else
  {
    PRINT_STR( "I3C recovery failed -- check wiring, then reset the board" );
  }
  PRINT_LN( );

  s_i3cFailStreak = 0;
  s_i3cRecovering = false;
}

// Called from the transport on every transfer. A wedge shows up as an
// unbroken run of failures, so a single glitch must not trigger a reset.
static void i3cNoteTransferResult ( bool ok )
{
  if ( ok )
  {
    s_i3cFailStreak = 0;
    return;
  }
  if ( s_i3cRecovering ) return;   // failures during recovery must not re-enter
  if ( ++s_i3cFailStreak >= TMF8829_I3C_FAILURES_BEFORE_RECOVERY )
  {
    i3cRecoverBus( );
  }
}

// ----------------------------------------- I3C private read/write ---------------------------------------
// Single private WRITE transfer containing [regAddr][txData...] concatenated
// -- mirrors the original single-I2C-transaction semantics (regAddr and
// data went out together, see i2cTxOnly below in git history). Needs one
// contiguous TxBuf for HAL_I3C_Ctrl_Transmit, hence the scratch buffer.
static int8_t i3cTxRegOnly ( uint8_t slaveAddr, uint8_t regAddr, uint16_t toTx, const uint8_t * txData )
{
  (void)slaveAddr; // ignored -- always TMF8829_STATIC_ADDR here (driver->i2cSlaveAddress never
                    // changes, see enablePinHigh below), real bus address is s_i3cDynamicAddr
  if ( !s_i3cAddrValid ) return I2C_ERR_OTHER;

  static uint8_t scratch[ DATA_BUFFER_SIZE + 1 ];
  if ( toTx > DATA_BUFFER_SIZE ) return I2C_ERR_DATA_TOO_LONG;
  scratch[0] = regAddr;
  if ( toTx )
  {
    memcpy( scratch + 1, txData, toTx );
  }

  I3C_PrivateTypeDef writeDesc = {0};
  writeDesc.TargetAddr    = s_i3cDynamicAddr;
  writeDesc.TxBuf.pBuffer = scratch;
  writeDesc.TxBuf.Size    = (uint32_t)toTx + 1U;
  writeDesc.Direction     = HAL_I3C_DIRECTION_WRITE;

  uint32_t ctrlWords[1];
  I3C_XferTypeDef xfer = {0};
  xfer.CtrlBuf.pBuffer = ctrlWords;
  xfer.CtrlBuf.Size    = 1;
  xfer.TxBuf.pBuffer   = scratch;
  xfer.TxBuf.Size      = (uint32_t)toTx + 1U;

  if ( HAL_I3C_AddDescToFrame( &hi3c1, NULL, &writeDesc, &xfer, 1, I3C_PRIVATE_WITHOUT_ARB_STOP ) != HAL_OK )
  {
    return I2C_ERR_OTHER;
  }
  /* Timed here rather than around i3cTxRegOnly/i3cRxRegOnly as a whole: those
   * nest (a register read is a write followed by a read), so wrapping both
   * would double-count the write. The two HAL calls are disjoint. */
  uint32_t timingStartUs = s_timingOn ? micros( ) : 0;
  HAL_StatusTypeDef status = HAL_I3C_Ctrl_Transmit( &hi3c1, &xfer, 100 );
  if ( s_timingOn )
  {
    s_timingBusUs += micros( ) - timingStartUs;
  }
  if ( status != HAL_OK )
  {
    PRINT_STR( "I3C-TX failed regAddr=0x" );
    PRINT_UINT_HEX( regAddr );
    PRINT_STR( " status=" );
    PRINT_INT( status );
    PRINT_STR( " hi3c1.ErrorCode=0x" );
    PRINT_UINT_HEX( hi3c1.ErrorCode );
    PRINT_LN( );
    i3cNoteTransferResult( false );
    return I2C_ERR_TIMEOUT;
  }
  i3cNoteTransferResult( true );
  return I2C_SUCCESS;
}

// Two private transfers: write the 1-byte regAddr (STOP), then read toRx
// bytes (STOP) -- proven pattern from Stage 1's i3cReadReg(), same shape as
// the original I2C shim's write-then-read (which also used STOP-then-START
// rather than a true repeated-START; the TMF8829 already tolerates that).
static int8_t i3cRxRegOnly ( uint8_t slaveAddr, uint8_t regAddr, uint16_t toRx, uint8_t * rxData )
{
  (void)slaveAddr;
  if ( !s_i3cAddrValid ) return I2C_ERR_OTHER;

  int8_t writeStat = i3cTxRegOnly( slaveAddr, regAddr, 0, NULL );
  if ( writeStat != I2C_SUCCESS ) return writeStat;

  if ( toRx == 0 ) return I2C_SUCCESS;
  if ( toRx > DATA_BUFFER_SIZE ) return I2C_ERR_DATA_TOO_LONG;

  I3C_PrivateTypeDef readDesc = {0};
  readDesc.TargetAddr = s_i3cDynamicAddr;
  readDesc.RxBuf.Size = toRx;
  readDesc.Direction  = HAL_I3C_DIRECTION_READ;

  uint32_t ctrlWords[1];
  I3C_XferTypeDef xfer = {0};
  xfer.CtrlBuf.pBuffer = ctrlWords;
  xfer.CtrlBuf.Size    = 1;
  xfer.RxBuf.pBuffer   = rxData; // received bytes land in the XferData's own RxBuf, not readDesc.RxBuf
  xfer.RxBuf.Size      = toRx;

  if ( HAL_I3C_AddDescToFrame( &hi3c1, NULL, &readDesc, &xfer, 1, I3C_PRIVATE_WITHOUT_ARB_STOP ) != HAL_OK )
  {
    return I2C_ERR_OTHER;
  }
  uint32_t timingStartUs = s_timingOn ? micros( ) : 0;
  bool ok = ( HAL_I3C_Ctrl_Receive( &hi3c1, &xfer, 100 ) == HAL_OK );
  if ( s_timingOn )
  {
    s_timingBusUs += micros( ) - timingStartUs;
  }
  i3cNoteTransferResult( ok );
  return ok ? I2C_SUCCESS : I2C_ERR_TIMEOUT;
}
// ----------------------------------------------------------------------------------------------------------

void delayInMicroseconds ( uint32_t wait )
{
  delayMicroseconds( wait );
}

uint32_t getSysTick ( )
{
  return micros( );
}

uint8_t readProgramMemoryByte ( uint32_t address )
{
  return pgm_read_byte( address );
}

void enablePinHigh ( void * dptr )
{
  (void)dptr; // not used here
  digitalWrite( ENABLE_PIN, HIGH );
  delay( 10 ); // let the device boot/settle before DAA (datasheet tPOR max 2ms; Stage 1-proven value)

  // Device resets to its default/static address on EN low->high (see
  // tmf8829Enable() in tmf8829.c) -- this is the one point where dynamic
  // address assignment can happen. Can't store the result on
  // driver->i2cSlaveAddress: tmf8829Initialise(), called immediately after
  // this by tmf8829Enable(), unconditionally resets that field back to
  // TMF8829_SLAVE_ADDR. Tracked locally instead; i3cTxRegOnly/i3cRxRegOnly
  // below ignore whatever slaveAddr they're passed and use this instead.
  s_i3cAddrValid  = i3cAssignDynamicAddress( );
  s_i3cFailStreak = 0;
  if ( s_i3cAddrValid )
  {
    s_i3cDynamicAddr = TMF8829_DYNAMIC_ADDR;
    // Stop the target raising IBIs before any private transfer runs -- see
    // i3cDisableTargetEvents() for why an unserviced IBI wedges the bus.
    i3cDisableTargetEvents( );
  }
  else
  {
    // NB: not a pull-up problem. R8/R9 (2.2k, populated) pull up the mikroBUS
    // SDA/SCL we actually drive; R16/R17 being DNP is a redundant sensor-side
    // pair and irrelevant. Look at wiring/power instead.
    PRINT_STR( "I3C-SETDASA failed -- check wiring and that EN reached the device" );
    PRINT_LN( );
  }
}

void enablePinLow ( void * dptr )
{
  (void)dptr; // not used here
  digitalWrite( ENABLE_PIN, LOW );
  s_i3cAddrValid = false; // device will reset to static addressing on next enable; forces DAA to redo
}

void configurePins ( void * dptr )
{
  (void)dptr; // not used here
  // configure ENABLE pin and interupt pin
  pinOutput( ENABLE_PIN );
  pinInput( INTERRUPT_PIN );
  pinInput( TRIGGER_INTERRUPT_PIN );                 // if interrupt PIN is used
}

void i2cOpen ( void * dptr, uint32_t i2cClockSpeedInHz )
{
  (void)dptr; // not used here
  (void)i2cClockSpeedInHz; // I3C timing is fixed/datasheet-derived (see i3c1PeripheralInit), not
                            // derived from this legacy I2C-clock arg -- kept only for interface compatibility
  i3c1GpioInit( );
  i3c1PeripheralInit( );

  // 'f' and 'b' are implemented in this shim, so they aren't listed by the
  // app's own 'h' help text -- mention them here instead.
  PRINT_STR( "f ... dump last frame at full resolution (shim)" );
  PRINT_LN( );
  PRINT_STR( "b ... toggle binary frame streaming (shim)" );
  PRINT_LN( );
  PRINT_STR( "t ... toggle per-sub-frame timing report (shim)" );
  PRINT_LN( );
}

void i2cClose ( void * dptr )
{
  (void)dptr; // not used here
  HAL_I3C_DeInit( &hi3c1 );
}

void printChar ( char c ) 
{
  Serial.print( c );
}

void printInt ( int32_t i )
{
  Serial.print( i, DEC );
}

void printUint ( uint32_t i )
{
  Serial.print( i, DEC );
}

void printUintHex ( uint32_t i )
{
  Serial.print( i, HEX );
}

void printStr ( char * str )
{
  Serial.print( str );    // use only for printing zero-terminated strings: (const char *)
}

void printLn ( void )
{
  Serial.print( '\n' );
}

void inputOpen ( uint32_t baudrate )
{
  Serial.end( );                                     // this clears any old pending data 
  Serial.begin( baudrate );
}

void inputClose ( )
{
  Serial.end( );
}

static void printZoneFullDump ( void );   /* defined with the zone decoder below */
extern bool zoneBinaryStreamGet ( void );
extern void zoneBinaryStreamSet ( bool on );

/* 'f' (full-resolution dump) and 'b' (binary stream toggle) are handled here in
 * the shim rather than in tmf8829_app.cpp's key handler, so the vendored
 * app/driver sources stay unmodified -- the same reason every other platform
 * difference lives in this file. The key is consumed (return 0 = "no key"), so
 * the app never sees it and keeps reporting genuinely unknown keys as #Err,Cmd.
 * Neither key is used by the vendor app, so nothing is shadowed; they also
 * won't appear in the app's own 'h' help text, hence the startup hint printed
 * from i2cOpen(). */
int8_t inputGetKey ( char *c )
{
  *c = 0;
  if ( Serial.available() )
  {
    char key = Serial.read();
    if ( key == 'f' )
    {
      printZoneFullDump( );
      return 0;
    }
    if ( key == 'b' )
    {
      bool on = !zoneBinaryStreamGet( );
      zoneBinaryStreamSet( on );
      /* Printed before the stream starts / after it stops, so it never lands
       * mid-record. The host tolerates it either way via resync.
       * Two statements rather than a ternary: PRINT_CONST_STR casts its
       * argument, and the cast binds tighter than ?:, so a ternary would be
       * cast on its condition instead of its result. */
      if ( on ) { PRINT_CONST_STR( F( "Binary streaming ON" ) ); }
      else      { PRINT_CONST_STR( F( "Binary streaming OFF" ) ); }
      PRINT_LN( );
      return 0;
    }
    if ( key == 't' )
    {
      s_timingOn = !s_timingOn;
      timingReset( );
      if ( s_timingOn ) { PRINT_CONST_STR( F( "Frame timing ON" ) ); }
      else              { PRINT_CONST_STR( F( "Frame timing OFF" ) ); }
      PRINT_LN( );
      return 0;
    }
    *c = key;
    return 1;
  }
  return 0;
}

void printConstStr ( const char * str )
{
  /* casting back to Arduino specific memory */
  Serial.print( reinterpret_cast<const __FlashStringHelper *>( str ) );
}

void pinOutput ( uint8_t pin )
{
  pinMode( pin, OUTPUT );      /* define a pin as output */
}

void pinInput ( uint8_t pin )
{ 
  pinMode( pin, INPUT );      /* define a pin as input */
}

void setInterruptHandler( void (* handler)( void ) )
{
  attachInterrupt( digitalPinToInterrupt( INTERRUPT_PIN ), handler, FALLING );
}

void disableInterruptHandler( uint8_t pin )
{
  detachInterrupt( digitalPinToInterrupt( pin ) );
}

void disableInterrupts ( void )
{
  noInterrupts( );
}

void enableInterrupts ( void )
{
  interrupts( );
}

char inputGetKey ( )
{
  if ( Serial.available() )
  {
    char c = Serial.read();
    return c;
  }
  return 0;
}

// ----------------------------------------- rx/tx ---------------------------------------
int8_t txReg ( void *dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toTx, const uint8_t *txData )
{
    return i2cTxReg(dptr, slaveAddr, regAddr, toTx, txData);
}

int8_t rxReg ( void *dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toRx, uint8_t *rxData )
{
    return i2cRxReg(dptr, slaveAddr, regAddr, toRx, rxData);
}

// ----------------------------------------- i2c (now I3C-backed, see top of file) -----------------------

// Names kept as i2cTxReg/i2cRxReg/i2cTxRx since txReg/rxReg call them by
// exact name (tmf8829.c never changed) -- bodies below are I3C, not I2C.
// No chunking needed: I3C private transfers aren't bound by Arduino Wire's
// 32-byte buffer, and DATA_BUFFER_SIZE (500B, the real ceiling -- the
// driver itself already chunks the firmware image to fit) is well within
// the HAL control word's 16-bit byte-count field (max 65535).

int8_t i2cTxReg ( void * dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toTx, const uint8_t * txData )
{
  (void)dptr;
  return i3cTxRegOnly( slaveAddr, regAddr, toTx, txData );
}

int8_t i2cRxReg ( void * dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toRx, uint8_t * rxData )
{
  (void)dptr;
  return i3cRxRegOnly( slaveAddr, regAddr, toRx, rxData );
}

int8_t i2cTxRx ( void * dptr, uint8_t slaveAddr, uint16_t toTx, const uint8_t * txData, uint16_t toRx, uint8_t * rxData )
{
  // Not called anywhere in tmf8829.c today (only txReg/rxReg are) -- kept
  // for interface completeness. toTx/txData follow the same convention as
  // the original I2C implementation: txData[0] is the register address,
  // the rest is payload, sent as one write transfer; toRx is a separate
  // plain read transfer (no reg-address phase of its own, matching the
  // original i2cRxOnly's direct Wire.requestFrom()).
  (void)dptr;
  int8_t res = I2C_SUCCESS;
  if ( toTx )
  {
    res = i3cTxRegOnly( slaveAddr, *txData, toTx - 1, txData + 1 );
  }
  if ( toRx && res == I2C_SUCCESS )
  {
    if ( !s_i3cAddrValid ) return I2C_ERR_OTHER;
    if ( toRx > DATA_BUFFER_SIZE ) return I2C_ERR_DATA_TOO_LONG;

    I3C_PrivateTypeDef readDesc = {0};
    readDesc.TargetAddr = s_i3cDynamicAddr;
    readDesc.RxBuf.Size = toRx;
    readDesc.Direction  = HAL_I3C_DIRECTION_READ;

    uint32_t ctrlWords[1];
    I3C_XferTypeDef xfer = {0};
    xfer.CtrlBuf.pBuffer = ctrlWords;
    xfer.CtrlBuf.Size    = 1;
    xfer.RxBuf.pBuffer   = rxData;
    xfer.RxBuf.Size      = toRx;

    if ( HAL_I3C_AddDescToFrame( &hi3c1, NULL, &readDesc, &xfer, 1, I3C_PRIVATE_WITHOUT_ARB_STOP ) != HAL_OK )
    {
      res = I2C_ERR_OTHER;
    }
    else
    {
      res = ( HAL_I3C_Ctrl_Receive( &hi3c1, &xfer, 100 ) == HAL_OK ) ? I2C_SUCCESS : I2C_ERR_TIMEOUT;
    }
  }
  return res;
}

// ------------------------------------------ 8x8 zone decoder --------------------------------
// Bring-up step 3 sanity check: decode raw result bytes into a readable
// 8x8 distance(mm)/confidence grid, instead of relying on the driver's
// generic raw-integer dump (which -- at our serial baud rate -- proved
// large enough per frame to delay the loop and trip measurement's
// auto-stop-on-error path).
//
// Pixel layout is derived from tmf8829GetPixelSize()/
// tmf8829CorrectDistanceDataSegment() in tmf8829.c: for layout=1 (the
// default 8x8 config: 1 peak, no signal/noise/xtalk extras) each pixel
// is 3 bytes = [distance_LSB, distance_MSB, confidence]. Zone raster
// order (top-left to bottom-right) is an assumption, not confirmed
// against the datasheet -- fine for a "does the distance look right"
// check, since a flat target should read similarly across zones
// regardless of exact ordering.
//
// Units: raw distance is in 0.25mm steps, per the TMF8829 datasheet
// (DS001140 v2-00, section 8.2.37 TMF8829_CFG_ALG_DISTANCE, page 71:
// "Distance is reported in 0.25 mm steps"). Divide by 4 for mm -- an
// earlier version of this code assumed 1 LSB = 1mm, which was off by
// 4x (confirmed by a real-world ceiling-height test that only made
// sense after applying this conversion).
// Resolution is derived per-frame from the frame header rather than
// hardcoded, so every preset (8x8 through 48x32) decodes correctly:
//
//  - Zone count comes from the header's `payload` field. That field counts
//    12 bytes of already-read frame header plus a 12-byte trailing footer,
//    neither of which is zone data -- see tmf8829ReadResults() in tmf8829.c
//    (sizeToRead = payload - (FRAME_HEADER_SIZE - FRAME_HEADER_OFFSET), and
//    TMF8829_FRAME_FOOTER_SIZE consumed at the end). Validated against the
//    known-good 8x8 case: (216-24)/3 = 64 zones exactly.
//  - Grid dimensions come from the low nibble of the frame ID, which is the
//    device's FP_MODE code (datasheet DS001140 v2-00 section 8.2.5, page 59).
//    Confirmed on hardware: ID 0x10 -> FP_8x8A, 0x13 -> FP_32x32,
//    0x15 -> FP_48x32.
//  - At 32x32 and 48x32 the device splits each measurement across two
//    sub-frames (measured 512 of 1024, and 768 of 1536, per frame), flagged
//    by bit 6 of `layout` (sub_result, section 8.2.9). Assembling those two
//    halves into one image needs the interleaving pattern, which is not yet
//    known -- so for now each sub-frame is decoded and displayed on its own
//    and labelled as such.
#define TMF8829_MAX_ZONES        1536   /* 48x32, the device's full SPAD count */

/* Frame header field offsets, relative to the start of the pre-header block. */
#define TMF8829_HDR_ID_OFFSET       ( TMF8829_PRE_HEADER_SIZE + 0 )
#define TMF8829_HDR_LAYOUT_OFFSET   ( TMF8829_PRE_HEADER_SIZE + 1 )
#define TMF8829_HDR_PAYLOAD_OFFSET  ( TMF8829_PRE_HEADER_SIZE + 2 )

/* Bytes inside `payload` that are not zone data (see comment above). */
#define TMF8829_PAYLOAD_NON_ZONE_BYTES \
  ( ( TMF8829_FRAME_HEADER_SIZE - TMF8829_FRAME_HEADER_OFFSET ) + TMF8829_FRAME_FOOTER_SIZE )

#define TMF8829_FID_FP_MODE_MASK              0x0F  /* low nibble of frame ID = FP_MODE */
#define TMF8829_CFG_RESULT_FORMAT_SUB_RESULT_MASK 0x40  /* layout bit 6, datasheet 8.2.9 */

// Live preview is subsampled to at most this many cells per axis. Sized by
// serial bandwidth, not taste: a colored cell costs ~27-31 bytes of ANSI, so
// at 1Mbaud (~100kB/s) 64 cells is ~2kB ~= 20ms -- comfortably inside the
// device's fastest 33ms measurement cadence. A full 48x32 grid would be
// ~41kB ~= 0.4s per frame, which would not just look slow but would delay
// the loop past the next measurement and trip the driver's
// auto-stop-on-error path (exactly what happened at 115200 baud in Phase 1).
// Press 'f' for a full-resolution dump instead.
#define TMF8829_PREVIEW_MAX_SIDE 8

// Distance-to-color range for the terminal heatmap. Fixed (not
// per-frame min/max) so a color always means the same distance across
// frames -- otherwise the same physical scene would repaint itself in
// different colors every time something in view changed slightly.
// Tune to whatever range is actually relevant for the target scene.
#define TMF8829_COLOR_DIST_MIN_MM   100
#define TMF8829_COLOR_DIST_MAX_MM   3000

// Below this confidence, a cell is shown flat gray instead of
// distance-colored, regardless of its distance value -- e.g. the
// ~350mm wall test's bottom-left column (confidence 36-61, distance
// ~2200mm while its neighbors read ~300mm) was almost certainly a weak/
// unreliable return, not a real object. This is a rough fixed cutoff,
// not derived from any spec -- the device's own on-chip filter
// (TMF8829_CFG_ALG_CONFIDENCE_THRESHOLD, default 6, datasheet §8.2.38)
// is much more permissive and already applied before we ever see the
// data. Confidence also falls off with real distance (the ~2m ceiling
// test's legitimately clean data ran as low as ~18-50), so a single
// fixed threshold isn't perfectly scene-independent -- tune per target
// scene/range if this over- or under-flags.
#define TMF8829_CONFIDENCE_DISPLAY_THRESHOLD   40

static uint8_t  zonePixelSize  = 3;
static uint16_t zoneDistanceMm[ TMF8829_MAX_ZONES ];
static uint8_t  zoneConfidence[ TMF8829_MAX_ZONES ];
static uint16_t zoneFillCount  = 0;   /* zones decoded so far this frame (accumulates across chunks) */
static uint16_t zoneExpected   = 0;   /* zones this frame should carry, from the header */
static uint16_t zoneGridW      = 0;   /* full-resolution grid width for the active FP_MODE */
static uint16_t zoneGridH      = 0;   /* full-resolution grid height for the active FP_MODE */
static bool     zoneIsSubFrame = false;
static uint8_t  zoneFpMode     = 0;   /* low nibble of the frame ID, carried to the host as-is */
static uint32_t zoneFrameNum   = 0;   /* device frame counter, frame header PRE+4 */
static uint32_t zoneSysTick    = 0;   /* device 125kHz tick, pre-header +1 -- host-side frame timing */

// ---- Binary streaming ('b') --------------------------------------------------
// ASCII costs ~28 bytes/zone (a colored cell is ~25, confidence ~3), which at
// 48x32 is ~43kB/frame against a 6600-byte budget (66ms cadence at 1Mbaud,
// 8N1 -> 100kB/s). Packed binary is 3 bytes/zone + 24 of framing: 4632 bytes,
// ~70% of budget, so every mode fits at its native cadence. See the changelog
// entry for the full before/after table.
//
// The stream deliberately shares the port with the existing ASCII output
// rather than replacing it: the driver's own '#Err' text and any startup
// messages just look like noise the host's resync scanner steps over. ASCII
// per-frame output is suppressed while streaming (see the log-level gates in
// handleReceived*) purely to stop it eating the bandwidth this exists to free.
//
// Zone order is emitted exactly as the device delivers it -- no sub-frame
// assembly, no orientation correction. Both of those are still unknown
// (Stage 3b), and putting them in Python means testing a hypothesis is an
// edit-and-rerun instead of an edit-and-reflash.
#define TMF8829_BIN_SYNC_0        0x54  /* 'T' */
#define TMF8829_BIN_SYNC_1        0x4D  /* 'M' */
#define TMF8829_BIN_SYNC_2        0x46  /* 'F' */
#define TMF8829_BIN_SYNC_3        0x38  /* '8' */
#define TMF8829_BIN_VERSION       0x01
#define TMF8829_BIN_HEADER_SIZE   22    /* incl. the 2-byte header CRC at [20..21] */
#define TMF8829_BIN_FLAG_SUBFRAME 0x01
#define TMF8829_BIN_CHUNK_ZONES   32    /* zones staged per Serial.write -- 96B, keeps
                                         * the call count down without a frame-sized buffer */

static bool zoneBinaryStream = false;

/* Accessors so the 'b' intercept in inputGetKey() -- which sits above this
 * file's zone-decoder section -- can reach the flag without hoisting the
 * decoder's state up with it. */
bool zoneBinaryStreamGet ( void )      { return zoneBinaryStream; }
void zoneBinaryStreamSet ( bool on )   { zoneBinaryStream = on; }

/* FP_MODE code (low nibble of the frame ID) -> full-resolution grid size,
 * datasheet DS001140 v2-00 section 8.2.5, page 59. FP_8x8B (1) is listed as
 * "do not use" but is mapped anyway so an unexpected frame still decodes. */
static void zoneGridForFpMode ( uint8_t fpMode, uint16_t * width, uint16_t * height )
{
  switch ( fpMode )
  {
    case 0: case 1: *width =  8; *height =  8; break;  /* FP_8x8A / FP_8x8B */
    case 2:         *width = 16; *height = 16; break;  /* FP_16x16 */
    case 3: case 4: *width = 32; *height = 32; break;  /* FP_32x32 / FP_32x32s */
    case 5:         *width = 48; *height = 32; break;  /* FP_48x32 */
    default:        *width =  0; *height =  0; break;  /* unknown -- fall back below */
  }
}

/* Columns to lay the decoded zones out in. For a full frame this is the real
 * grid width; for a sub-frame (whose true shape is not yet known) it is still
 * the best guess, with the row count derived from how many zones arrived. */
static uint16_t zoneDisplayWidth ( )
{
  if ( zoneGridW > 0 && zoneGridW <= zoneExpected ) return zoneGridW;
  return ( zoneExpected < 8 ) ? zoneExpected : 8;
}

static uint16_t zoneDisplayHeight ( )
{
  uint16_t w = zoneDisplayWidth( );
  if ( w == 0 ) return 0;
  return (uint16_t)( ( zoneExpected + w - 1 ) / w );
}

// Prints one grid cell with an ANSI 24-bit-color background: red = near
// (TMF8829_COLOR_DIST_MIN_MM), blue = far (TMF8829_COLOR_DIST_MAX_MM),
// linearly interpolated in between, clamped at the ends. Below
// TMF8829_CONFIDENCE_DISPLAY_THRESHOLD, the cell is flat gray instead --
// flags a marginal/unreliable reading regardless of what distance value
// it happens to carry. Requires a VT100/ANSI-capable terminal
// (PowerShell/Windows Terminal both are; `pio device monitor` needs
// `-f direct` or it'll show literal escape-code text instead).
static void printColoredDistanceCell ( uint16_t distanceMm, uint8_t confidence )
{
  uint8_t red, green, blue;

  if ( confidence < TMF8829_CONFIDENCE_DISPLAY_THRESHOLD )
  {
    red = 80;
    green = 80;
    blue = 80;
  }
  else
  {
    uint16_t d = distanceMm;
    if ( d < TMF8829_COLOR_DIST_MIN_MM ) d = TMF8829_COLOR_DIST_MIN_MM;
    if ( d > TMF8829_COLOR_DIST_MAX_MM ) d = TMF8829_COLOR_DIST_MAX_MM;

    uint32_t t1000 = ( (uint32_t)( d - TMF8829_COLOR_DIST_MIN_MM ) * 1000 )
                      / ( TMF8829_COLOR_DIST_MAX_MM - TMF8829_COLOR_DIST_MIN_MM );
    red   = (uint8_t)( 255 - ( 255 * t1000 ) / 1000 );
    green = 0;
    blue  = (uint8_t)( ( 255 * t1000 ) / 1000 );
  }

  PRINT_STR( "\x1b[48;2;" );
  PRINT_UINT( red );
  PRINT_CHAR( ';' );
  PRINT_UINT( green );
  PRINT_CHAR( ';' );
  PRINT_UINT( blue );
  PRINT_STR( "m" );
  if ( distanceMm < 1000 ) PRINT_CHAR( ' ' );
  if ( distanceMm < 100 )  PRINT_CHAR( ' ' );
  if ( distanceMm < 10 )   PRINT_CHAR( ' ' );
  PRINT_UINT( distanceMm );
  PRINT_STR( "\x1b[0m " );
}

/* One shared stride on both axes so the preview keeps the sensor's aspect
 * ratio (e.g. 48x32 -> 8x6 rather than being squashed to a square). */
static uint16_t zonePreviewStride ( uint16_t w, uint16_t h )
{
  uint16_t maxSide = ( w > h ) ? w : h;
  if ( maxSide == 0 ) return 1;
  uint16_t stride = (uint16_t)( ( maxSide + TMF8829_PREVIEW_MAX_SIDE - 1 ) / TMF8829_PREVIEW_MAX_SIDE );
  return ( stride > 0 ) ? stride : 1;
}

/* Prints a "<w>x<h>" size, plus a sub-frame warning when the zone shape is
 * a guess rather than something we've confirmed. */
static void printZoneShapeInfo ( uint16_t w, uint16_t h )
{
  PRINT_UINT( w );
  PRINT_CHAR( 'x' );
  PRINT_UINT( h );
  if ( zoneIsSubFrame || ( zoneGridW && zoneExpected != (uint16_t)( zoneGridW * zoneGridH ) ) )
  {
    PRINT_CONST_STR( F( " SUB-FRAME of " ) );
    PRINT_UINT( zoneGridW );
    PRINT_CHAR( 'x' );
    PRINT_UINT( zoneGridH );
    PRINT_CONST_STR( F( ", zone order unverified" ) );
  }
}

/* Subsampled live view -- see TMF8829_PREVIEW_MAX_SIDE for why this is
 * subsampled rather than complete. At 8x8 the stride is 1, so this is
 * identical to the pre-Stage-3 output. */
static void printZonePreview ( )
{
  uint16_t w = zoneDisplayWidth( );
  uint16_t h = zoneDisplayHeight( );
  if ( w == 0 || h == 0 ) return;
  uint16_t stride = zonePreviewStride( w, h );

  PRINT_CONST_STR( F( "Distance (mm, red=near/" ) );
  PRINT_UINT( TMF8829_COLOR_DIST_MIN_MM );
  PRINT_CONST_STR( F( " blue=far/" ) );
  PRINT_UINT( TMF8829_COLOR_DIST_MAX_MM );
  PRINT_CONST_STR( F( ") " ) );
  printZoneShapeInfo( w, h );
  if ( stride > 1 )
  {
    PRINT_CONST_STR( F( ", preview every " ) );
    PRINT_UINT( stride );
    PRINT_CONST_STR( F( " zones -- 'f' for full res" ) );
  }
  PRINT_CHAR( ':' );
  PRINT_LN( );

  for ( uint16_t row = 0; row < h; row += stride )
  {
    for ( uint16_t col = 0; col < w; col += stride )
    {
      uint16_t idx = (uint16_t)( row * w + col );
      if ( idx < zoneFillCount )
      {
        printColoredDistanceCell( zoneDistanceMm[ idx ], zoneConfidence[ idx ] );
      }
    }
    PRINT_LN( );
  }

  PRINT_CONST_STR( F( "Confidence:" ) );
  PRINT_LN( );
  for ( uint16_t row = 0; row < h; row += stride )
  {
    for ( uint16_t col = 0; col < w; col += stride )
    {
      uint16_t idx = (uint16_t)( row * w + col );
      if ( idx < zoneFillCount )
      {
        PRINT_UINT( zoneConfidence[ idx ] );
        PRINT_CHAR( ' ' );
      }
    }
    PRINT_LN( );
  }
}

/* Every zone of the last decoded frame, plain numbers (no ANSI) -- roughly
 * 5 bytes per cell instead of ~27, which keeps even a 48x32 dump to ~8kB.
 * Triggered by 'f'; see the intercept in inputGetKey(). */
static void printZoneFullDump ( )
{
  if ( zoneFillCount == 0 )
  {
    PRINT_CONST_STR( F( "No frame captured yet." ) );
    PRINT_LN( );
    return;
  }

  uint16_t w = zoneDisplayWidth( );
  uint16_t h = zoneDisplayHeight( );

  PRINT_CONST_STR( F( "--- Full resolution dump, " ) );
  PRINT_UINT( zoneFillCount );
  PRINT_CONST_STR( F( " zones, " ) );
  printZoneShapeInfo( w, h );
  PRINT_CONST_STR( F( " ---" ) );
  PRINT_LN( );

  PRINT_CONST_STR( F( "Distance (mm):" ) );
  PRINT_LN( );
  for ( uint16_t idx = 0; idx < zoneFillCount; idx++ )
  {
    PRINT_UINT( zoneDistanceMm[ idx ] );
    PRINT_CHAR( ( ( idx % w ) == ( w - 1U ) ) ? '\n' : ' ' );
  }
  PRINT_LN( );

  PRINT_CONST_STR( F( "Confidence:" ) );
  PRINT_LN( );
  for ( uint16_t idx = 0; idx < zoneFillCount; idx++ )
  {
    PRINT_UINT( zoneConfidence[ idx ] );
    PRINT_CHAR( ( ( idx % w ) == ( w - 1U ) ) ? '\n' : ' ' );
  }
  PRINT_LN( );
}

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no final xor).
 * Bitwise rather than table-driven: at 48x32 the payload pass is ~37k
 * iterations, well under a millisecond on a 250MHz H5 against a 66ms cadence,
 * and it costs no flash for a table. */
static uint16_t crc16Update ( uint16_t crc, uint8_t byte )
{
  crc ^= (uint16_t)byte << 8;
  for ( uint8_t bit = 0; bit < 8; bit++ )
  {
    crc = ( crc & 0x8000 ) ? (uint16_t)( ( crc << 1 ) ^ 0x1021 ) : (uint16_t)( crc << 1 );
  }
  return crc;
}

static uint16_t crc16Buffer ( const uint8_t * data, uint16_t len )
{
  uint16_t crc = 0xFFFF;
  for ( uint16_t i = 0; i < len; i++ )
  {
    crc = crc16Update( crc, data[ i ] );
  }
  return crc;
}

static void binPutU16 ( uint8_t * p, uint16_t v )
{
  p[ 0 ] = (uint8_t)( v & 0xFF );
  p[ 1 ] = (uint8_t)( v >> 8 );
}

static void binPutU32 ( uint8_t * p, uint32_t v )
{
  p[ 0 ] = (uint8_t)( v & 0xFF );
  p[ 1 ] = (uint8_t)( ( v >> 8 ) & 0xFF );
  p[ 2 ] = (uint8_t)( ( v >> 16 ) & 0xFF );
  p[ 3 ] = (uint8_t)( ( v >> 24 ) & 0xFF );
}

/* Emits the last decoded frame as one binary record. Layout:
 *
 *   off  size  field
 *    0     4   sync "TMF8"
 *    4     1   version
 *    5     1   flags       bit0 = sub_result
 *    6     1   fp_mode     low nibble of the device frame ID
 *    7     1   stride      bytes per zone in the payload (3)
 *    8     2   zone_count  N, uint16 LE
 *   10     1   grid_w      full-resolution grid width for this fp_mode
 *   11     1   grid_h      full-resolution grid height
 *   12     4   frame_num   device frame counter, uint32 LE
 *   16     4   systick     device 125kHz tick, uint32 LE
 *   20     2   header_crc  CRC-16/CCITT-FALSE over bytes 0..19
 *   22   3*N   payload     per zone: uint16 LE distance_mm, uint8 confidence
 * 22+3N   2    payload_crc CRC-16/CCITT-FALSE over the payload
 *
 * The header carries its own CRC, separate from the payload's, because the
 * host has to trust zone_count *before* it can know where the frame ends. A
 * chance 4-byte sync match inside pixel data would otherwise yield a garbage
 * length and leave the parser blocking on bytes that never arrive -- a hang
 * rather than a dropped frame. Validating the header on its own rejects false
 * syncs at ~1/65536 and bounds recovery to a single frame.
 *
 * Distances are the same mm values the ASCII path prints (raw 0.25mm steps
 * already divided down in handleReceivedResultData), so both outputs can be
 * compared directly when the parser is misbehaving. */
static void streamZoneFrameBinary ( )
{
  uint32_t timingStartUs = s_timingOn ? micros( ) : 0;
  uint8_t header[ TMF8829_BIN_HEADER_SIZE ];

  header[ 0 ] = TMF8829_BIN_SYNC_0;
  header[ 1 ] = TMF8829_BIN_SYNC_1;
  header[ 2 ] = TMF8829_BIN_SYNC_2;
  header[ 3 ] = TMF8829_BIN_SYNC_3;
  header[ 4 ] = TMF8829_BIN_VERSION;
  header[ 5 ] = zoneIsSubFrame ? TMF8829_BIN_FLAG_SUBFRAME : 0x00;
  header[ 6 ] = zoneFpMode;
  header[ 7 ] = 3;                                   /* payload stride, see note above */
  binPutU16( header + 8, zoneFillCount );
  header[ 10 ] = (uint8_t)zoneGridW;
  header[ 11 ] = (uint8_t)zoneGridH;
  binPutU32( header + 12, zoneFrameNum );
  binPutU32( header + 16, zoneSysTick );
  binPutU16( header + 20, crc16Buffer( header, 20 ) );

  Serial.write( header, sizeof( header ) );

  uint16_t crc = 0xFFFF;
  uint8_t  chunk[ TMF8829_BIN_CHUNK_ZONES * 3 ];
  uint16_t used = 0;

  for ( uint16_t idx = 0; idx < zoneFillCount; idx++ )
  {
    chunk[ used     ] = (uint8_t)( zoneDistanceMm[ idx ] & 0xFF );
    chunk[ used + 1 ] = (uint8_t)( zoneDistanceMm[ idx ] >> 8 );
    chunk[ used + 2 ] = zoneConfidence[ idx ];
    crc = crc16Update( crc, chunk[ used ] );
    crc = crc16Update( crc, chunk[ used + 1 ] );
    crc = crc16Update( crc, chunk[ used + 2 ] );
    used += 3;

    if ( used == sizeof( chunk ) )
    {
      Serial.write( chunk, used );
      used = 0;
    }
  }
  if ( used > 0 )
  {
    Serial.write( chunk, used );
  }

  uint8_t trailer[ 2 ];
  binPutU16( trailer, crc );
  Serial.write( trailer, sizeof( trailer ) );

  if ( s_timingOn )
  {
    /* Measures how long the emit *blocks* for, which is the number that
     * matters here -- HardwareSerial's TX buffer is 64 bytes and
     * interrupt-driven, so past that, write() waits on the wire. Enlarging
     * that buffer would make this number shrink without the bytes moving any
     * faster, which is exactly why it is left at the default while measuring. */
    s_timingEmitUs += micros( ) - timingStartUs;
  }
}
// ----------------------------------------------------------------------------------------------

void handleReceivedFrameHeaderData ( void * dptr, uint8_t * data )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;

  /* Sampled at the frame header rather than at the end of the emit, so the
   * period covers one whole delivery cycle including everything the loop does
   * between frames. Skips the first sample, which has no predecessor. */
  if ( s_timingOn )
  {
    uint32_t nowUs = micros( );
    if ( s_timingLastHeaderUs != 0 )
    {
      s_timingPeriodUs += nowUs - s_timingLastHeaderUs;
      s_timingPeriodCount++;
    }
    s_timingLastHeaderUs = nowUs;
  }

  /* Per-frame ASCII is suppressed while binary streaming, so it doesn't eat
   * the bandwidth the binary format exists to free. One-shot output ('f',
   * errors, startup text) still goes out -- the host resyncs past it. */
  if ( !zoneBinaryStream && driver->logLevel == TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    printResultHeader( driver, data, TMF8829_PRE_HEADER_SIZE + TMF8829_FRAME_HEADER_SIZE );
  }
  if ( !zoneBinaryStream && driver->logLevel > TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    PRINT_STR( "#Obj " );
    printResults( driver, data, TMF8829_PRE_HEADER_SIZE + TMF8829_FRAME_HEADER_SIZE );

  }

  uint8_t  frameId = data[ TMF8829_HDR_ID_OFFSET ];
  uint8_t  layout  = data[ TMF8829_HDR_LAYOUT_OFFSET ];
  uint16_t payload = tmf8829GetUint16( data + TMF8829_HDR_PAYLOAD_OFFSET );

  zonePixelSize  = tmf8829GetPixelSize( layout );
  zoneIsSubFrame = ( layout & TMF8829_CFG_RESULT_FORMAT_SUB_RESULT_MASK ) != 0;
  zoneFpMode     = (uint8_t)( frameId & TMF8829_FID_FP_MODE_MASK );
  zoneFrameNum   = tmf8829GetUint32( data + TMF8829_PRE_HEADER_SIZE + 4 );
  zoneSysTick    = tmf8829GetUint32( data + 1 );
  zoneGridForFpMode( zoneFpMode, &zoneGridW, &zoneGridH );

  zoneExpected = 0;
  if ( zonePixelSize > 0 && payload > TMF8829_PAYLOAD_NON_ZONE_BYTES )
  {
    uint16_t zoneBytes = (uint16_t)( payload - TMF8829_PAYLOAD_NON_ZONE_BYTES );
    zoneExpected = (uint16_t)( zoneBytes / zonePixelSize );
    if ( zoneExpected > TMF8829_MAX_ZONES )
    {
      zoneExpected = TMF8829_MAX_ZONES;   // clamp; never overrun the arrays
    }
  }
  zoneFillCount = 0;
}

void handleReceivedResultData ( void * dptr, uint8_t * data, uint16_t size )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  if ( !zoneBinaryStream && driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS )
  {
    printResults( driver, data, size );
  }
  // Note: data size ==1 Error for EOF check, change buffer size !

  // Called once per chunk; zoneFillCount persists across calls so frames
  // larger than DATA_BUFFER_SIZE still assemble correctly. Bounding by
  // zoneExpected is also what stops decoding before the 12-byte frame
  // footer, which trails the zone data in the final chunk.
  if ( zonePixelSize >= 3 )
  {
    for ( uint16_t offset = 0;
          zoneFillCount < zoneExpected && ( offset + zonePixelSize ) <= size;
          offset += zonePixelSize )
    {
      zoneDistanceMm[ zoneFillCount ] = tmf8829GetUint16( data + offset ) / 4; // 0.25mm steps -> mm
      zoneConfidence[ zoneFillCount ] = data[ offset + 2 ];
      zoneFillCount++;
    }
  }
}

void handleReceivedHistogramData( void * dptr, uint8_t * data, uint16_t size )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  (void)size;
  if ( driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS )
  {
    printHistogram( driver, data, size );
  }
  // Note: data size ==1 Error for EOF check, change buffer size !
}

void handleReceivedResultDataEnd( void * dptr )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;

  if ( !zoneBinaryStream && driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    PRINT_LN( );
  }

  if ( zoneFillCount > 0 && zoneFillCount == zoneExpected )
  {
    if ( zoneBinaryStream )
    {
      streamZoneFrameBinary( );
    }
    else
    {
      printZonePreview( );
    }
  }

  /* Reported after the emit so the emit it describes is already accounted for.
   * Per sub-frame averages, in microseconds. Deliberately not prefixed with the
   * binary sync word, and short enough that the host steps over it as ASCII. */
  if ( s_timingOn && ++s_timingFrames >= TMF8829_TIMING_REPORT_FRAMES )
  {
    PRINT_STR( "#T,n=" );
    PRINT_UINT( s_timingFrames );
    PRINT_STR( ",bus=" );
    PRINT_UINT( s_timingBusUs / s_timingFrames );
    PRINT_STR( ",emit=" );
    PRINT_UINT( s_timingEmitUs / s_timingFrames );
    PRINT_STR( ",period=" );
    PRINT_UINT( s_timingPeriodCount ? ( s_timingPeriodUs / s_timingPeriodCount ) : 0 );
    PRINT_STR( ",us/subframe" );
    PRINT_LN( );
    timingReset( );
  }
}

void handleReceivedHistogramDataEnd( void * dptr )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;

  if (driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    PRINT_LN( );
  }
}

// Result Header printing:
// #Obj,<i2c_slave_address>,<fifostatus>,<systick>,
//      <frame_identifier>,<result_layout>,<payload>,<frameNumber>,
//      <temperature0>,<temperature1>,<temperature2>,<bdv_value>,<ref_peak_position1>, <ref_peak_position2>
void printResultHeader ( void * dptr, uint8_t * data, uint8_t len )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;  
  if ( len == (TMF8829_PRE_HEADER_SIZE + TMF8829_FRAME_HEADER_SIZE) )
  {
    uint32_t sysTick = tmf8829GetUint32( data + 1 );
    uint16_t payload = tmf8829GetUint16( data + TMF8829_PRE_HEADER_SIZE + 2 );
    uint32_t frameNum = tmf8829GetUint32( data + TMF8829_PRE_HEADER_SIZE + 4 );
    uint16_t refPos1 = tmf8829GetUint16( data + TMF8829_PRE_HEADER_SIZE + 12 );
    uint16_t refPos2 = tmf8829GetUint16( data + TMF8829_PRE_HEADER_SIZE + 14 );
    PRINT_STR( "#Obj" );
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( driver->i2cSlaveAddress );
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ 0 ] );
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( sysTick );
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE ] ); //ID
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE + 1 ] ); // Layout
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( payload ); // Payload
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( frameNum ); // Frame Number
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE + 8 ] ); // Temp0
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE + 9 ] ); // Temp1
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE + 10 ] ); // Temp2
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( data[ TMF8829_PRE_HEADER_SIZE + 11 ] ); // BDV
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( refPos1 ); // Ref Pos 1
    PRINT_CHAR( SEPARATOR );
    PRINT_UINT( refPos2 ); // Ref Pos 2
  }
  else // result structure too short
  {
    PRINT_STR( "#Err" );
    PRINT_CHAR( SEPARATOR );
    PRINT_STR( "header size length wrong" );
    PRINT_CHAR( SEPARATOR );
    PRINT_INT( len );
    PRINT_LN( );
  }
}

void printResults ( void * dptr, uint8_t * data, uint16_t len )
{
  (void) dptr; // not used for this platform
  uint16_t cnt;
  
  for ( cnt = 0 ; cnt < len ; cnt ++ )
  {
    PRINT_INT( data[cnt] );
    PRINT_CHAR( SEPARATOR );
  }
}

void printHistogram ( void * dptr, uint8_t * data, uint16_t len )
{
  (void) dptr; // not used for this platform
  uint16_t cnt;

  for ( cnt = 0 ; cnt < len ; cnt ++ )
  {
    PRINT_INT( data[cnt] );
    PRINT_CHAR( SEPARATOR );
  }

}
