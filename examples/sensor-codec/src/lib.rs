//! A made-up sensor's 5-byte frame on port 1: temperature (0.1 °C, signed,
//! big-endian), humidity (0.5 %RH), then battery (mV / 20), and a CRC-8 of
//! the four bytes before it.

use pulzr_codec_sdk::{crc, emit_number};

fn decode(b: &[u8], port: u8) -> Result<(), String> {
    if port != 1 {
        return Err(format!("no frame on port {port}"));
    }
    let [t0, t1, rh, mv, check] = b
        .try_into()
        .map_err(|_| format!("5 bytes, not {}", b.len()))?;
    if crc("CRC-8/SMBUS", &b[..4]) != Some(check as u32) {
        return Err("CRC mismatch".into());
    }
    emit_number("temperature", i16::from_be_bytes([t0, t1]) as f64 / 10.0);
    emit_number("humidity", rh as f64 / 2.0);
    emit_number("battery_mv", mv as f64 * 20.0);
    Ok(())
}

pulzr_codec_sdk::codec!(decode);
