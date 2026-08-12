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
#include <Wire.h>


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
}

void enablePinLow ( void * dptr )
{
  (void)dptr; // not used here
  digitalWrite( ENABLE_PIN, LOW );   
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
  // Explicit pin routing, matching the confirmed-working bring-up step 2
  // sketch: SCL=PB8 (D15), SDA=PB9 (D14). See ../tmf8829/PROJECT (1).md.
  Wire.setSCL( PB8 );
  Wire.setSDA( PB9 );
  Wire.begin( );
  Wire.setClock( i2cClockSpeedInHz );
}

void i2cClose ( void * dptr )
{
  (void)dptr; // not used here
  Wire.end( );
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

int8_t inputGetKey ( char *c )
{
  *c = 0;
  if ( Serial.available() )
  {
    *c = Serial.read();
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

// ----------------------------------------- i2c ---------------------------------------

static int8_t i2cTxOnly ( uint8_t logLevel, uint8_t slaveAddr, uint8_t regAddr, uint16_t toTx, const uint8_t * txData )
{  // split long transfers into max of 32-bytes: 1 byte is register address, up to 31 are payload.
  int8_t res = I2C_SUCCESS;
  uint16_t addr = regAddr;
  do 
  {
    uint8_t tx;
    if ( toTx > ARDUINO_MAX_I2C_TRANSFER - 1) 
    {
      tx = ARDUINO_MAX_I2C_TRANSFER - 1;
    }
    else 
    {
      tx = toTx; // less than 31 bytes 
    }
    if ( logLevel & TMF8829_LOG_LEVEL_I2C ) 
    {
      PRINT_STR( "I2C-TX (0x" );
      PRINT_UINT_HEX( slaveAddr );
      PRINT_STR( ")" );
      PRINT_STR( " tx=" );
      PRINT_INT( tx+1 );          // +1 for regAddr
      PRINT_STR( " 0x" );
      PRINT_UINT_HEX( regAddr );
      if ( logLevel >= TMF8829_LOG_LEVEL_DEBUG ) 
      {
        uint8_t dumpTx = tx;
        const uint8_t * dump = txData;
        while ( dumpTx-- )
        {
          PRINT_STR( " 0x" );
          PRINT_UINT_HEX( *dump );
          dump++;
        }
      }
      PRINT_LN( );
    }

    Wire.beginTransmission( slaveAddr );
    Wire.write( regAddr );
    if ( tx )
    {
      Wire.write( txData, tx );
    }
    toTx -= tx;
    txData += tx;
    if ((addr <= 0xFF) && (addr+tx >=0xFF  ))
    {
      regAddr = 0xFF;
    }
    else
    {
      regAddr += tx;
    }
    addr = regAddr;
    res = Wire.endTransmission( );
  } while ( toTx && res == I2C_SUCCESS );
  return I2C_SUCCESS;
}

static int8_t i2cRxOnly ( uint8_t logLevel, uint8_t slaveAddr, uint16_t toRx, uint8_t * rxData )
{   // split long transfers into max of 32-bytes
  uint8_t expected = 0;
  uint8_t rx = 0;
  int8_t res = I2C_SUCCESS;
  do 
  {
    uint8_t * dump = rxData; // in case we dump on uart, we need the pointer
    if ( toRx > ARDUINO_MAX_I2C_TRANSFER ) 
    {
      expected = ARDUINO_MAX_I2C_TRANSFER;
    }
    else 
    {
      expected = toRx; // less than 32 bytes 
    }
    Wire.requestFrom( slaveAddr, expected );
    rx = 0;
    while ( Wire.available() ) 
    {  // read in all available bytes
      *rxData = Wire.read();
      rxData++;
      toRx--;
      rx++;
    }
    if ( logLevel & TMF8829_LOG_LEVEL_I2C ) 
    {
      PRINT_STR( "I2C-RX (0x" );
      PRINT_UINT_HEX( slaveAddr );
      PRINT_STR( ")" );
      PRINT_STR( " toRx=" );
      PRINT_INT( rx );
      if ( logLevel >= TMF8829_LOG_LEVEL_DEBUG ) 
      {
        uint8_t dumpRx = rx;
        while ( dumpRx-- )
        {
          PRINT_STR( " 0x" );
          PRINT_UINT_HEX( *dump );
          dump++;
        }
      }
      PRINT_LN( );
    }
  } while ( toRx && expected == rx );
  if ( toRx || expected != rx )
  {
    res = I2C_ERR_TIMEOUT;
  }
  return res;
}

int8_t i2cTxReg ( void * dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toTx, const uint8_t * txData )
{  // split long transfers into max of 32-bytes
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  return i2cTxOnly( driver->logLevel, slaveAddr, regAddr, toTx, txData ); 
}

int8_t i2cRxReg ( void * dptr, uint8_t slaveAddr, uint8_t regAddr, uint16_t toRx, uint8_t * rxData )
{   // split long transfers into max of 32-bytes
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  int8_t res = i2cTxOnly( driver->logLevel, slaveAddr, regAddr, 0, 0 ); 
  if ( res == I2C_SUCCESS )
  {
    res = i2cRxOnly( driver->logLevel, slaveAddr, toRx, rxData );
  }
  return res;
}

int8_t i2cTxRx ( void * dptr, uint8_t slaveAddr, uint16_t toTx, const uint8_t * txData, uint16_t toRx, uint8_t * rxData )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  int8_t res = I2C_SUCCESS;
  if ( toTx )
  {
    res = i2cTxOnly( driver->logLevel, slaveAddr, *txData, toTx-1, txData+1 );
  }
  if ( toRx && res == I2C_SUCCESS )
  {
    res = i2cRxOnly( driver->logLevel, slaveAddr, toRx, rxData );
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
#define TMF8829_ZONE_GRID_SIDE   8
#define TMF8829_ZONE_COUNT       ( TMF8829_ZONE_GRID_SIDE * TMF8829_ZONE_GRID_SIDE )

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
static uint16_t zoneDistanceMm[ TMF8829_ZONE_COUNT ];
static uint8_t  zoneConfidence[ TMF8829_ZONE_COUNT ];
static uint8_t  zoneFillCount  = 0;

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

static void printZoneGrid ( )
{
  PRINT_CONST_STR( F( "Distance grid (mm, red=near/" ) );
  PRINT_UINT( TMF8829_COLOR_DIST_MIN_MM );
  PRINT_CONST_STR( F( " blue=far/" ) );
  PRINT_UINT( TMF8829_COLOR_DIST_MAX_MM );
  PRINT_CONST_STR( F( "):" ) );
  PRINT_LN( );
  for ( uint8_t row = 0; row < TMF8829_ZONE_GRID_SIDE; row++ )
  {
    for ( uint8_t col = 0; col < TMF8829_ZONE_GRID_SIDE; col++ )
    {
      uint8_t idx = row * TMF8829_ZONE_GRID_SIDE + col;
      printColoredDistanceCell( zoneDistanceMm[ idx ], zoneConfidence[ idx ] );
    }
    PRINT_LN( );
  }
  PRINT_CONST_STR( F( "Confidence grid:" ) );
  PRINT_LN( );
  for ( uint8_t row = 0; row < TMF8829_ZONE_GRID_SIDE; row++ )
  {
    for ( uint8_t col = 0; col < TMF8829_ZONE_GRID_SIDE; col++ )
    {
      PRINT_UINT( zoneConfidence[ row * TMF8829_ZONE_GRID_SIDE + col ] );
      PRINT_CHAR( ' ' );
    }
    PRINT_LN( );
  }
}
// ----------------------------------------------------------------------------------------------

void handleReceivedFrameHeaderData ( void * dptr, uint8_t * data )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  if ( driver->logLevel == TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    printResultHeader( driver, data, TMF8829_PRE_HEADER_SIZE + TMF8829_FRAME_HEADER_SIZE );
  }
  if ( driver->logLevel > TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    PRINT_STR( "#Obj " );
    printResults( driver, data, TMF8829_PRE_HEADER_SIZE + TMF8829_FRAME_HEADER_SIZE );

  }

  uint8_t layout = data[ TMF8829_PRE_HEADER_SIZE + 1 ];
  zonePixelSize = tmf8829GetPixelSize( layout );
  zoneFillCount = 0;
}

void handleReceivedResultData ( void * dptr, uint8_t * data, uint16_t size )
{
  tmf8829Driver * driver = (tmf8829Driver *)dptr;
  if ( driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS )
  {
    printResults( driver, data, size );
  }
  // Note: data size ==1 Error for EOF check, change buffer size !

  // Assumes the whole 8x8 payload arrives in this single call, true
  // today since DATA_BUFFER_SIZE (500) exceeds the 8x8 payload (~216
  // bytes) -- would need chunk-spanning logic for larger resolutions.
  if ( zonePixelSize >= 3 )
  {
    for ( uint16_t offset = 0;
          zoneFillCount < TMF8829_ZONE_COUNT && ( offset + zonePixelSize ) <= size;
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

  if (driver->logLevel >= TMF8829_LOG_LEVEL_RESULTS_HEADER )
  {
    PRINT_LN( );
  }

  if ( zoneFillCount == TMF8829_ZONE_COUNT )
  {
    printZoneGrid( );
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
