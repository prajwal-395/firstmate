//! Schema-neutral protobuf wire inventory preserving each original field's
//! bytes and order. No Resolve field numbers are assigned meaning here.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireField {
    pub number: u32,
    pub wire_type: u8,
    pub encoded: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WireError {
    Truncated,
    VarintOverflow,
    InvalidFieldNumber,
    UnsupportedWireType(u8),
    LengthOverflow,
}

pub fn decode(data: &[u8]) -> Result<Vec<WireField>, WireError> {
    let mut offset = 0;
    let mut fields = Vec::new();
    while offset < data.len() {
        let start = offset;
        let key = read_varint(data, &mut offset)?;
        let raw_number = key >> 3;
        if raw_number == 0 || raw_number > (1 << 29) - 1 {
            return Err(WireError::InvalidFieldNumber);
        }
        let number = raw_number as u32;
        let wire_type = (key & 7) as u8;
        match wire_type {
            0 => {
                read_varint(data, &mut offset)?;
            }
            1 => skip(data, &mut offset, 8)?,
            2 => {
                let len = usize::try_from(read_varint(data, &mut offset)?)
                    .map_err(|_| WireError::LengthOverflow)?;
                skip(data, &mut offset, len)?;
            }
            5 => skip(data, &mut offset, 4)?,
            3 | 4 => return Err(WireError::UnsupportedWireType(wire_type)),
            other => return Err(WireError::UnsupportedWireType(other)),
        }
        fields.push(WireField {
            number,
            wire_type,
            encoded: data[start..offset].to_vec(),
        });
    }
    Ok(fields)
}

/// Reassembles fields in their input order without changing any field bytes.
pub fn encode_unchanged(fields: &[WireField]) -> Vec<u8> {
    fields
        .iter()
        .flat_map(|field| field.encoded.iter().copied())
        .collect()
}

fn read_varint(data: &[u8], offset: &mut usize) -> Result<u64, WireError> {
    let mut value = 0u64;
    for index in 0..10 {
        let byte = *data.get(*offset).ok_or(WireError::Truncated)?;
        *offset += 1;
        if index == 9 && byte > 1 {
            return Err(WireError::VarintOverflow);
        }
        value |= u64::from(byte & 0x7f) << (index * 7);
        if byte & 0x80 == 0 {
            return Ok(value);
        }
    }
    Err(WireError::VarintOverflow)
}

fn skip(data: &[u8], offset: &mut usize, len: usize) -> Result<(), WireError> {
    let end = offset.checked_add(len).ok_or(WireError::LengthOverflow)?;
    if end > data.len() {
        return Err(WireError::Truncated);
    }
    *offset = end;
    Ok(())
}
