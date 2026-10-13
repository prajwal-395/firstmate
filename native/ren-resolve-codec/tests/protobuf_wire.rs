use ren_resolve_codec::blob::protobuf_wire::{WireError, decode, encode_unchanged};

#[test]
fn unknown_fields_keep_order_and_original_encodings() {
    let input = [
        0x98, 0x06, 0x81, 0x00, 0x12, 0x02, 0xaa, 0xbb, 0x19, 1, 2, 3, 4, 5, 6, 7, 8,
    ];
    let fields = decode(&input).unwrap();
    assert_eq!(
        fields.iter().map(|f| f.number).collect::<Vec<_>>(),
        [99, 2, 3]
    );
    assert_eq!(encode_unchanged(&fields), input);
}

#[test]
fn malformed_varints_lengths_and_wire_types_are_errors() {
    assert_eq!(decode(&[0x80]), Err(WireError::Truncated));
    assert_eq!(
        decode(&[
            0x08, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x02
        ]),
        Err(WireError::VarintOverflow)
    );
    assert_eq!(decode(&[0x0a, 0x05, 0x01]), Err(WireError::Truncated));
    assert_eq!(decode(&[0x0b]), Err(WireError::UnsupportedWireType(3)));
    assert_eq!(decode(&[0x0c]), Err(WireError::UnsupportedWireType(4)));
    assert_eq!(decode(&[0x0e]), Err(WireError::UnsupportedWireType(6)));
}

#[test]
fn deterministic_wire_corpus_never_panics() {
    for len in 0..256 {
        let bytes: Vec<u8> = (0..len)
            .map(|n| ((n * 29 + len * 11) & 0xff) as u8)
            .collect();
        assert!(std::panic::catch_unwind(|| decode(&bytes)).is_ok());
    }
}
