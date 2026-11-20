//! The ABI's plumbing (docs/abi.md), which [`crate::export!`] wires up: the
//! host writes an event into room `pz_alloc` made, `pz_handle` reads it,
//! frees it, and returns the answer's place in memory, kept until the next
//! call. Other languages implement the same three exports.

use std::cell::RefCell;

use crate::{Answer, Event};

thread_local! {
    // Buffers the host was given room in, by address, until taken back.
    static ROOM: RefCell<Vec<Vec<u8>>> = const { RefCell::new(Vec::new()) };
    // The last answer, readable until the next call.
    static ANSWER: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}

/// `pz_alloc`: room for `len` octets, kept until [`take`] gives it back.
pub fn alloc(len: i32) -> i32 {
    let mut buf = vec![0u8; len.max(0) as usize];
    let ptr = buf.as_mut_ptr() as usize as i32;
    ROOM.with(|r| r.borrow_mut().push(buf));
    ptr
}

/// The octets the host wrote at `ptr`, the room they were in freed.
pub fn take(ptr: i32, len: i32) -> Vec<u8> {
    ROOM.with(|r| {
        let mut room = r.borrow_mut();
        match room.iter().position(|b| b.as_ptr() as usize as i32 == ptr) {
            Some(i) => {
                let mut b = room.swap_remove(i);
                b.truncate(len.max(0) as usize);
                b
            }
            None => Vec::new(),
        }
    })
}

/// Packs an answer's place as the ABI returns it.
pub fn pack(ptr: *const u8, len: usize) -> i64 {
    (((ptr as usize as u64) << 32) | (len as u64 & 0xffff_ffff)) as i64
}

/// `pz_handle`: one event to `handle`, its answer kept and returned.
pub fn handle(ptr: i32, len: i32, handle: impl FnOnce(Event) -> Answer) -> i64 {
    let input = take(ptr, len);
    let answer = match serde_json::from_slice(&input) {
        Ok(v) => handle(Event::from_value(v)),
        Err(e) => {
            let mut a = Answer::default();
            a.log_error(format!("the host sent an event that is not JSON: {e}"));
            a
        }
    };
    let bytes = serde_json::to_vec(&answer).unwrap_or_else(|_| b"{}".to_vec());
    ANSWER.with(|a| {
        *a.borrow_mut() = bytes;
        let a = a.borrow();
        pack(a.as_ptr(), a.len())
    })
}
