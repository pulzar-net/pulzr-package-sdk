//! Matching an answer to its command without remembering anything.
//!
//! The platform names each command with an `id`. A vendor protocol whose
//! correlation field is an integer (a `seq`) is sent the id as that integer,
//! and its answer carries it home: an adapter keeps nothing about what is in
//! flight, and a restart between a command and its answer loses nothing.

/// The vendor's integer for a command id, when the id is decimal digits that
/// fit; None means the command cannot be carried by this vendor, and is
/// refused `invalid`.
pub fn seq_for_id(id: &str) -> Option<u64> {
    (!id.is_empty() && id.len() <= 19 && id.bytes().all(|b| b.is_ascii_digit()))
        .then(|| id.parse().ok())
        .flatten()
}

/// The command id an answer's integer names, if it can be one of the
/// platform's (8 to 64 characters): an answer with a small integer is another
/// sender's, not ours to answer.
pub fn id_for_seq(seq: u64) -> Option<String> {
    let id = seq.to_string();
    (id.len() >= 8).then_some(id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        assert_eq!(seq_for_id("10000042"), Some(10000042));
        assert_eq!(seq_for_id("c7e1a2b3-0001"), None);
        assert_eq!(seq_for_id(""), None);
        assert_eq!(id_for_seq(10000042).as_deref(), Some("10000042"));
        assert_eq!(id_for_seq(17), None);
    }
}
