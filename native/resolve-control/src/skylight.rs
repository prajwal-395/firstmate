//! SkyLight background input: focus-without-raise plus per-pid event post.
//!
//! The recipe is cua-driver's (`SkyLightEventPost.swift` in trycua/cua,
//! itself ported from yabai's `window_manager_focus_window_without_raise`
//! and `window_manager_make_key_window`): `SLPSPostEventRecordTo` flips a
//! window's AppKit-active state without raising it, and `SLEventPostToPid`
//! posts a synthesized event to one process through an auth-signed
//! WindowServer channel that bypasses `IOHIDPostEvent`. The captain's
//! cursor never moves and nothing is raised.
//!
//! Every symbol is resolved at run time with `dlopen` + `dlsym` against
//! `/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight` -
//! nothing links at build time, so a missing symbol is a refusal naming
//! what is missing, never a launch crash. `available()` reports whether
//! the post path resolves; `focus_available()` whether the whole
//! focus-without-raise chain does.
//!
//! The focus record is the 248-byte buffer cua-driver documents: length
//! at 0x04, marker 0x0d at 0x08, the focus/defocus marker at 0x8a (0x01
//! focus, 0x02 defocus), the target window id little-endian at 0x3c.
//! The location is left blank, which is what makes the record a focus
//! signal rather than a click (yabai). Effect is verified at the
//! application level - frontmost unchanged, click lands - never assumed
//! from a zero return code.

use crate::protocol::{DriverError, DriverResult};
use std::ffi::{c_char, c_void};

const SKYLIGHT_PATH: &[u8] = b"/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight\0";
const RTLD_LAZY: i32 = 0x1;
const RTLD_DEFAULT: *mut c_void = -2isize as *mut c_void;

extern "C" {
    fn dlopen(path: *const c_char, mode: i32) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}

/// A `ProcessSerialNumber`: two longs identifying a process to the
/// WindowServer-era SPIs.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct Psn {
    pub hi: u32,
    pub lo: u32,
}

type PostToPidFn = unsafe extern "C" fn(pid: i32, event: *mut c_void);
type PostRecordFn = unsafe extern "C" fn(psn: *const Psn, bytes: *const u8) -> i32;
type GetFrontPsnFn = unsafe extern "C" fn(psn: *mut Psn) -> i32;
type GetWindowOwnerFn = unsafe extern "C" fn(cid: u32, wid: u32, owner: *mut u32) -> i32;
type GetConnectionPsnFn = unsafe extern "C" fn(cid: u32, psn: *mut Psn) -> i32;
type MainConnectionFn = unsafe extern "C" fn() -> u32;
type GetProcessPsnFn = unsafe extern "C" fn(pid: i32, psn: *mut Psn) -> i32;

unsafe fn resolve<T>(name: &[u8]) -> Option<T> {
    let handle = dlopen(SKYLIGHT_PATH.as_ptr() as *const c_char, RTLD_LAZY);
    if handle.is_null() {
        return None;
    }
    let ptr = dlsym(RTLD_DEFAULT, name.as_ptr() as *const c_char);
    if ptr.is_null() {
        return None;
    }
    Some(std::mem::transmute_copy::<*mut c_void, T>(&ptr))
}

macro_rules! cstr {
    ($name:expr) => {
        concat!($name, "\0").as_bytes()
    };
}

/// The post path resolves: `SLEventPostToPid` is present.
pub fn available() -> bool {
    unsafe {
        resolve::<PostToPidFn>(cstr!("SLEventPostToPid")).is_some()
    }
}

/// The whole focus chain resolves: front-PSN read, a PSN lookup, and the
/// record post. PSN lookup prefers the modern
/// `SLSGetWindowOwner` + `SLSGetConnectionPSN` pair (the deprecated
/// `GetProcessForPID` no longer exports on macOS 15+); either pair
/// suffices.
pub fn focus_available() -> bool {
    unsafe {
        let front = resolve::<GetFrontPsnFn>(cstr!("_SLPSGetFrontProcess")).is_some();
        let modern = resolve::<GetWindowOwnerFn>(cstr!("SLSGetWindowOwner")).is_some()
            && resolve::<GetConnectionPsnFn>(cstr!("SLSGetConnectionPSN")).is_some()
            && resolve::<MainConnectionFn>(cstr!("CGSMainConnectionID")).is_some();
        let legacy = resolve::<GetProcessPsnFn>(cstr!("GetProcessForPID")).is_some();
        let post = resolve::<PostRecordFn>(cstr!("SLPSPostEventRecordTo")).is_some();
        front && (modern || legacy) && post
    }
}

pub const RECORD_LEN: usize = 0xf8;
const RECORD_FOCUS: u8 = 0x01;
const RECORD_DEFOCUS: u8 = 0x02;

/// Build one focus record: a blanked location (a focus signal, not a
/// click) for `window_id`, focus or defocus per `focus`.
pub fn focus_record(window_id: u32, focus: bool) -> [u8; RECORD_LEN] {
    let mut buf = [0u8; RECORD_LEN];
    buf[0x04] = 0xf8;
    buf[0x08] = 0x0d;
    buf[0x8a] = if focus { RECORD_FOCUS } else { RECORD_DEFOCUS };
    buf[0x3c..0x40].copy_from_slice(&window_id.to_le_bytes());
    buf
}

unsafe fn target_psn(pid: i32, window_id: u32) -> DriverResult<Psn> {
    if let (Some(owner), Some(to_psn), Some(main_cid)) = (
        resolve::<GetWindowOwnerFn>(cstr!("SLSGetWindowOwner")),
        resolve::<GetConnectionPsnFn>(cstr!("SLSGetConnectionPSN")),
        resolve::<MainConnectionFn>(cstr!("CGSMainConnectionID")),
    ) {
        let cid = main_cid();
        let mut owner_cid: u32 = 0;
        if owner(cid, window_id, &mut owner_cid) == 0 {
            let mut psn = Psn::default();
            if to_psn(owner_cid, &mut psn) == 0 {
                return Ok(psn);
            }
        }
        return Err(DriverError::SkyLightUnavailable(
            "SLSGetWindowOwner/SLSGetConnectionPSN would not resolve the target PSN".into(),
        ));
    }
    if let Some(for_pid) = resolve::<GetProcessPsnFn>(cstr!("GetProcessForPID")) {
        let mut psn = Psn::default();
        if for_pid(pid, &mut psn) == 0 {
            return Ok(psn);
        }
    }
    Err(DriverError::SkyLightUnavailable(
        "no PSN lookup resolves (SLSGetWindowOwner pair and GetProcessForPID both missing or failed)".into(),
    ))
}

/// Focus-without-raise: defocus whoever is frontmost, focus Resolve's
/// window, raising nothing. Returns the frontmost pid before and after
/// so the caller verifies nothing was stolen.
pub fn focus_without_raise(
    pid: i32,
    window_id: u32,
    frontmost_pid: impl Fn() -> Option<u32>,
) -> DriverResult<(Option<u32>, Option<u32>)> {
    if !focus_available() {
        return Err(DriverError::SkyLightUnavailable(
            "focus chain incomplete (_SLPSGetFrontProcess, a PSN lookup and SLPSPostEventRecordTo all required)".into(),
        ));
    }
    let before = frontmost_pid();
    unsafe {
        let post: PostRecordFn = resolve(cstr!("SLPSPostEventRecordTo"))
            .ok_or_else(|| DriverError::SkyLightUnavailable("SLPSPostEventRecordTo vanished".into()))?;
        let front_fn: GetFrontPsnFn = resolve(cstr!("_SLPSGetFrontProcess"))
            .ok_or_else(|| DriverError::SkyLightUnavailable("_SLPSGetFrontProcess vanished".into()))?;
        let mut front = Psn::default();
        if front_fn(&mut front) != 0 {
            return Err(DriverError::SkyLightUnavailable(
                "could not read the front process PSN".into(),
            ));
        }
        let defocus = focus_record(window_id, false);
        if post(&front, defocus.as_ptr()) != 0 {
            return Err(DriverError::SkyLightUnavailable(
                "defocus record post failed".into(),
            ));
        }
        let target = target_psn(pid, window_id)?;
        let focus = focus_record(window_id, true);
        if post(&target, focus.as_ptr()) != 0 {
            return Err(DriverError::SkyLightUnavailable(
                "focus record post failed".into(),
            ));
        }
    }
    Ok((before, frontmost_pid()))
}

/// Post one built event to `pid` through the SkyLight channel.
pub unsafe fn post_to_pid(pid: i32, event: *mut c_void) -> DriverResult<()> {
    let post: PostToPidFn = resolve(cstr!("SLEventPostToPid")).ok_or_else(|| {
        DriverError::SkyLightUnavailable("SLEventPostToPid does not resolve".into())
    })?;
    post(pid, event);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn focus_record_layout() {
        let focus = focus_record(0x1234_5678, true);
        assert_eq!(focus.len(), 0xf8);
        assert_eq!(focus[0x04], 0xf8);
        assert_eq!(focus[0x08], 0x0d);
        assert_eq!(focus[0x8a], 0x01);
        assert_eq!(&focus[0x3c..0x40], &[0x78, 0x56, 0x34, 0x12]);
        // A focus signal carries no location: every other byte is blank.
        assert!(focus.iter().enumerate().all(|(i, b)| {
            matches!(i, 0x04 | 0x08 | 0x8a | 0x3c..=0x3f) || *b == 0
        }));
        let defocus = focus_record(1, false);
        assert_eq!(defocus[0x8a], 0x02);
        assert_eq!(&defocus[0x3c..0x40], &[1, 0, 0, 0]);
    }

    #[test]
    fn skylight_symbols_resolve_on_this_mac() {
        assert!(available(), "SLEventPostToPid must resolve on macOS 26");
        assert!(
            focus_available(),
            "the focus chain must resolve on macOS 26"
        );
    }
}
