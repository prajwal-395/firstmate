use crate::protocol::{DriverError, DriverResult, Frame};
use crate::skylight;
use std::ffi::c_void;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGPoint { pub x: f64, pub y: f64 }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum CGEventType {
    LeftMouseDown = 1,
    LeftMouseUp = 2,
    RightMouseDown = 3,
    RightMouseUp = 4,
    MouseMoved = 5,
    LeftMouseDragged = 6,
    RightMouseDragged = 7,
    ScrollWheel = 22,
    KeyDown = 10,
    KeyUp = 11,
    FlagsChanged = 12,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum CGMouseButton { Left = 0, Right = 1, Center = 2 }

#[derive(Debug, Clone, Copy)]
pub struct CGEventFlags(pub u64);
impl CGEventFlags {
    pub const fn empty() -> Self { CGEventFlags(0) }
    pub const fn contains(&self, other: Self) -> bool { self.0 & other.0 != 0 }
    pub const COMMAND: CGEventFlags = CGEventFlags(0x0010);
    pub const SHIFT: CGEventFlags = CGEventFlags(0x0002);
    pub const ALTERNATE: CGEventFlags = CGEventFlags(0x0008);
    pub const CONTROL: CGEventFlags = CGEventFlags(0x0004);
    pub const SECONDARY_FN: CGEventFlags = CGEventFlags(0x0080);
}
impl std::ops::BitOr for CGEventFlags {
    type Output = Self;
    fn bitor(self, rhs: Self) -> Self { CGEventFlags(self.0 | rhs.0) }
}

#[derive(Debug, Clone, Copy)]
#[repr(u32)]
pub enum CGEventTapLocation { HID = 0, Session = 1, AnnotatedSession = 2 }

extern "C" {
    pub fn CGEventCreateMouseEvent(
        source: *const c_void,
        event_type: CGEventType,
        point: CGPoint,
        button: CGMouseButton,
    ) -> *mut c_void;
    pub fn CGEventCreateKeyboardEvent(
        source: *const c_void,
        key_code: u16,
        key_down: bool,
    ) -> *mut c_void;
    pub fn CGEventPost(tap: CGEventTapLocation, event: *mut c_void);
    pub fn CGEventPostToPid(pid: i32, event: *mut c_void);
    pub fn CGEventKeyboardSetUnicodeString(event: *mut c_void, length: u64, string: *const u16);
    pub fn CGEventSetIntegerValueField(event: *mut c_void, field: u32, value: i64);
    pub fn CGEventSetFlags(event: *mut c_void, flags: u64);
    pub fn CGEventGetFlags(event: *mut c_void) -> u64;
    pub fn CGEventGetLocation(event: *mut c_void) -> CGPoint;
    pub fn CGEventCreate(source: *const c_void) -> *mut c_void;
    pub fn CGEventSetType(event: *mut c_void, event_type: CGEventType);
    pub fn CFRelease(cf: *const c_void);
    pub fn CGWarpMouseCursorPosition(point: CGPoint) -> i32;
    pub fn CGAssociateMouseAndMouseCursorPosition(connected: u8) -> i32;
}

/// Where an event goes. `Background` delivers it to Resolve's process alone:
/// the real cursor does not move and keyboard focus stays with the captain's
/// app. `Foreground` posts it at the HID tap, as if typed or clicked by hand -
/// it moves the cursor and reaches whatever is frontmost. `SkyLight` posts it
/// to Resolve's process through the SkyLight channel (focus-without-raise is
/// a separate explicit step, never implicit): like `Background` the cursor
/// does not move, but Qt-drawn controls may accept what they filter on the
/// `CGEventPostToPid` path.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route { Background { pid: i32, window_id: u32 }, Foreground, SkyLight { pid: i32, window_id: u32, chromium: bool } }

/// kCGMouseEventWindowUnderMousePointer and ...ThatCanHandleThisEvent: a
/// background mouse event names the window it is for, since the window server
/// cannot find it under a cursor that is elsewhere.
const MOUSE_WINDOW_FIELDS: [u32; 2] = [91, 92];

unsafe fn post(route: Route, event: *mut c_void) -> DriverResult<()> {
    match post_target(route) {
        PostTarget::HidTap => CGEventPost(CGEventTapLocation::HID, event),
        PostTarget::Pid(pid) => {
            if let Route::Background { window_id, .. } = route {
                for field in MOUSE_WINDOW_FIELDS {
                    CGEventSetIntegerValueField(event, field, window_id as i64);
                }
            }
            CGEventPostToPid(pid, event)
        }
        PostTarget::SkyLightPid(pid) => skylight::post_to_pid(pid, event)?,
    }
    Ok(())
}

/// The delivery channel for a route, resolved without acting: HID tap for
/// foreground, the pid for background, the SkyLight channel for SkyLight.
/// `Background` stamps the window fields onto the event; the other two
/// post it as built.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PostTarget { HidTap, Pid(i32), SkyLightPid(i32) }

pub fn post_target(route: Route) -> PostTarget {
    match route {
        Route::Foreground => PostTarget::HidTap,
        Route::Background { pid, .. } => PostTarget::Pid(pid),
        Route::SkyLight { pid, .. } => PostTarget::SkyLightPid(pid),
    }
}

// CGEventField constants
const kCGMouseEventClickState: u32 = 1;
const kCGMouseEventButtonNumber: u32 = 3;
const kCGKeyboardEventKeycode: u32 = 9;

// Foreground click gaps: the moved primer settles cursor-tracking
// state, down/up spaces match the tracking loop, and the beat between
// clicks keeps multi-clicks distinct. Values mirror the SkyLight
// path's measured 12/28 plus cua-driver's 40 ms primer - the
// reference the live drops were diffed against, not guesses.
const FG_PRIMER_MS: u64 = 40;
const FG_DOWN_MS: u64 = 12;
const FG_UP_MS: u64 = 28;

/// A SkyLight click: a leading mouse-moved at the point (canvas-class
/// event loops only accept per-pid routes behind a leading `mouseMoved`,
/// per cua-driver's canvas-app finding), then down/up, all through the
/// SkyLight channel. Focus-without-raise stays a separate explicit step.
/// A SkyLight click, cua-style: a HIDSystemState event source, a leading
/// `mouseMoved` priming cursor-tracking state, 12ms, down, 28ms (an
/// NSButton's tracking loop polls for the up), up - every event stamped
/// with the window-local point and f1/f3/f7/f40/f51/f58/f91/f92 under one
/// click-group id. Chromium targets additionally get the off-screen
/// primer down/up at (-1,-1) with f0 gesture phases (cua's
/// `chromium_click_route_plan`): it ticks Chromium's user-activation gate
/// so the real click lands as a trusted continuation.
fn skylight_click(
    pid: i32,
    window_id: u32,
    rel_x: f64,
    rel_y: f64,
    frame: &Frame,
    clicks: u32,
    chromium: bool,
) -> DriverResult<()> {
    let sx = frame.x + rel_x;
    let sy = frame.y + rel_y;
    // Chromium takes both locations in screen space; AppKit takes the
    // window-local point (cua's two paths differ here - verified against
    // chromium_click_route_plan vs click_at_xy_inner).
    let (win_x, win_y) = if chromium { (sx, sy) } else { (rel_x, rel_y) };
    // Chromium timing is its own plan (15/1/100/1/0); AppKit leans on the
    // tracking-loop gaps (12/28).
    let (move_ms, down_ms) = if chromium { (15, 1) } else { (12, 28) };
    let group_id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos() as i64)
        .unwrap_or(0);
    unsafe {
        let source = skylight::event_source().ok_or_else(|| {
            DriverError::SkyLightUnavailable("no HIDSystemState event source".into())
        })?;
        let moved = CGEventCreateMouseEvent(
            source, CGEventType::MouseMoved, CGPoint { x: sx, y: sy }, CGMouseButton::Left,
        );
        if moved.is_null() {
            return Err(DriverError::Internal("failed to create skylight move".into()));
        }
        skylight::stamp_event(moved, pid, window_id, win_x, win_y, 0, 0, 3, group_id, None)?;
        skylight::post_to_pid(pid, moved)?;
        CFRelease(moved as *const c_void);
        std::thread::sleep(std::time::Duration::from_millis(move_ms));

        // Chromium primer: an off-screen down/up ticks the user-activation
        // gate so the real click lands as a trusted continuation. The
        // (-1,-1) point hits no window; without it, activation-gated
        // controls swallow the click silently.
        if chromium {
            let prime_down = CGEventCreateMouseEvent(source, CGEventType::LeftMouseDown, CGPoint { x: -1.0, y: -1.0 }, CGMouseButton::Left);
            if prime_down.is_null() { return Err(DriverError::Internal("failed to create primer down".into())); }
            skylight::stamp_event(prime_down, pid, window_id, -1.0, -1.0, 1, 0, 3, group_id, chromium.then_some(1))?;
            skylight::post_to_pid(pid, prime_down)?;
            CFRelease(prime_down as *const c_void);
            std::thread::sleep(std::time::Duration::from_millis(1));

            let prime_up = CGEventCreateMouseEvent(source, CGEventType::LeftMouseUp, CGPoint { x: -1.0, y: -1.0 }, CGMouseButton::Left);
            if prime_up.is_null() { return Err(DriverError::Internal("failed to create primer up".into())); }
            skylight::stamp_event(prime_up, pid, window_id, -1.0, -1.0, 1, 0, 3, group_id, chromium.then_some(2))?;
            skylight::post_to_pid(pid, prime_up)?;
            CFRelease(prime_up as *const c_void);
            std::thread::sleep(std::time::Duration::from_millis(100));
        }

        for click_num in 0..clicks {
            let state = click_num as i64 + 1;
            let phase: Option<i64> = chromium.then_some(3);
            let down = CGEventCreateMouseEvent(source, CGEventType::LeftMouseDown, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if down.is_null() { return Err(DriverError::Internal("failed to create skylight down".into())); }
            skylight::stamp_event(down, pid, window_id, win_x, win_y, state, 0, 3, group_id, phase)?;
            skylight::post_to_pid(pid, down)?;
            CFRelease(down as *const c_void);
            std::thread::sleep(std::time::Duration::from_millis(down_ms));

            let up = CGEventCreateMouseEvent(source, CGEventType::LeftMouseUp, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if up.is_null() { return Err(DriverError::Internal("failed to create skylight up".into())); }
            skylight::stamp_event(up, pid, window_id, win_x, win_y, state, 0, 3, group_id, phase)?;
            skylight::post_to_pid(pid, up)?;
            CFRelease(up as *const c_void);
        }
        CFRelease(source);
    }
    Ok(())
}

/// One foreground click event from an explicit HID-system source.
/// Factored so tests can read the source state back without posting:
/// a NULL-sourced event and an HID-sourced one must differ here, and
/// every event this path builds must match the HID reference.
fn fg_click_event(
    source: *const c_void,
    event_type: CGEventType,
    point: CGPoint,
) -> *mut c_void {
    unsafe { CGEventCreateMouseEvent(source, event_type, point, CGMouseButton::Left) }
}

/// Re-associate the real cursor after foreground input. Idempotent
/// and side-effect free when already coupled (the common case), so it
/// is safe to call unconditionally - and unsafe to skip, because a
/// warp that outlives its take leaves the captain's cursor decoupled
/// from the mouse.
pub fn recouple_cursor() {
    unsafe {
        CGAssociateMouseAndMouseCursorPosition(1);
    }
}

/// Runs a cleanup on every exit - normal return, error return, or
/// panic unwind. Foreground input holds one around its whole sequence
/// so the cursor is recoupled however the sequence ends; a signal
/// handler does the same for kills (see main).
pub struct CleanupGuard<F: Fn()> {
    cleanup: F,
}

impl<F: Fn()> CleanupGuard<F> {
    pub fn new(cleanup: F) -> Self {
        CleanupGuard { cleanup }
    }
}

impl<F: Fn()> Drop for CleanupGuard<F> {
    fn drop(&mut self) {
        (self.cleanup)();
    }
}

/// A foreground click, cua-style: HIDSystemState source, real cursor
/// warped to the point and re-coupled (AppKit hit-tests some clicks
/// against the actual cursor), a moved primer settling tracking state,
/// then down/up with tracking-loop gaps. The old path posted
/// NULL-sourced down/up back-to-back with no move and no timing, which
/// Qt drops while reporting success (measured live 2026-10-07:
/// clicked:true, 0 pixels). Foreground takes the screen by definition,
/// so the visible cursor jump is expected, never silent.
fn foreground_click(x: f64, y: f64, clicks: u32) -> DriverResult<()> {
    let source = skylight::event_source()
        .ok_or_else(|| DriverError::Internal("no HIDSystemState event source".into()))?;
    let point = CGPoint { x, y };
    // However this sequence ends - posted, refused, or panicked - the
    // cursor is recoupled: a warp that outlives its take decouples the
    // captain's mouse from the cursor with no visible cause.
    let _recoupled = CleanupGuard::new(recouple_cursor);
    unsafe {
        CGWarpMouseCursorPosition(point);
        CGAssociateMouseAndMouseCursorPosition(1);
        let moved = fg_click_event(source, CGEventType::MouseMoved, point);
        if moved.is_null() {
            return Err(DriverError::Internal("failed to create foreground move".into()));
        }
        CGEventPost(CGEventTapLocation::HID, moved);
        CFRelease(moved as *const c_void);
        std::thread::sleep(std::time::Duration::from_millis(FG_PRIMER_MS));
        for click_num in 0..clicks {
            let down = fg_click_event(source, CGEventType::LeftMouseDown, point);
            if down.is_null() {
                return Err(DriverError::Internal("failed to create foreground down".into()));
            }
            if click_num > 0 {
                CGEventSetIntegerValueField(down, kCGMouseEventClickState, click_num as i64 + 1);
            }
            CGEventPost(CGEventTapLocation::HID, down);
            CFRelease(down as *const c_void);
            std::thread::sleep(std::time::Duration::from_millis(FG_DOWN_MS));

            let up = fg_click_event(source, CGEventType::LeftMouseUp, point);
            if up.is_null() {
                return Err(DriverError::Internal("failed to create foreground up".into()));
            }
            if click_num > 0 {
                CGEventSetIntegerValueField(up, kCGMouseEventClickState, click_num as i64 + 1);
            }
            CGEventPost(CGEventTapLocation::HID, up);
            CFRelease(up as *const c_void);
            std::thread::sleep(std::time::Duration::from_millis(FG_UP_MS));
        }
        CFRelease(source);
    }
    Ok(())
}

pub fn click(route: Route, x: f64, y: f64, window_frame: &Frame, clicks: u32) -> DriverResult<()> {
    if let Route::SkyLight { pid, window_id, chromium } = route {
        return skylight_click(pid, window_id, x, y, window_frame, clicks, chromium);
    }
    let sx = window_frame.x + x;
    let sy = window_frame.y + y;
    if route == Route::Foreground {
        return foreground_click(sx, sy, clicks);
    }
    unsafe {
        for click_num in 0..clicks {
            let down = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseDown, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if down.is_null() { return Err(DriverError::Internal("failed to create mouse down".into())); }
            if click_num > 0 {
                CGEventSetIntegerValueField(down, kCGMouseEventClickState, click_num as i64 + 1);
            }
            post(route, down)?;
            CFRelease(down as *const c_void);

            let up = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseUp, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if up.is_null() { return Err(DriverError::Internal("failed to create mouse up".into())); }
            if click_num > 0 {
                CGEventSetIntegerValueField(up, kCGMouseEventClickState, click_num as i64 + 1);
            }
            post(route, up)?;
            CFRelease(up as *const c_void);
        }
    }
    Ok(())
}

pub fn drag(route: Route, start_x: f64, start_y: f64, end_x: f64, end_y: f64, window_frame: &Frame, _duration: f64) -> DriverResult<()> {
    let sx = window_frame.x + start_x;
    let sy = window_frame.y + start_y;
    let ex = window_frame.x + end_x;
    let ey = window_frame.y + end_y;
    unsafe {
        let down = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseDown, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
        if down.is_null() { return Err(DriverError::Internal("failed to create drag down".into())); }
        post(route, down)?;
        CFRelease(down as *const c_void);

        let move_ = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseDragged, CGPoint { x: ex, y: ey }, CGMouseButton::Left);
        if move_.is_null() { return Err(DriverError::Internal("failed to create drag move".into())); }
        post(route, move_)?;
        CFRelease(move_ as *const c_void);

        let up = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseUp, CGPoint { x: ex, y: ey }, CGMouseButton::Left);
        if up.is_null() { return Err(DriverError::Internal("failed to create drag up".into())); }
        post(route, up)?;
        CFRelease(up as *const c_void);
    }
    Ok(())
}

pub fn scroll(route: Route, x: f64, y: f64, window_frame: &Frame, delta_x: f64, delta_y: f64) -> DriverResult<()> {
    let sx = window_frame.x + x;
    let sy = window_frame.y + y;
    unsafe {
        let scroll = CGEventCreate(std::ptr::null());
        if scroll.is_null() { return Err(DriverError::Internal("failed to create scroll event".into())); }
        CGEventSetType(scroll, CGEventType::ScrollWheel);
        let loc = CGPoint { x: sx, y: sy };
        CGEventSetLocation(scroll, &loc as *const CGPoint as *const c_void);
        // kCGScrollWheelEventDeltaAxis1 = 11 (vertical), kCGScrollWheelEventDeltaAxis2 = 12 (horizontal)
        CGEventSetIntegerValueField(scroll, 11, delta_y as i64);
        CGEventSetIntegerValueField(scroll, 12, delta_x as i64);
        post(route, scroll)?;
        CFRelease(scroll as *const c_void);
    }
    Ok(())
}

extern "C" {
    pub fn CGEventSetLocation(event: *mut c_void, location: *const c_void);
}

/// Types `text` as Unicode: each character is one key event carrying its
/// string, so the result does not depend on the keyboard layout.
pub fn type_text(route: Route, text: &str) -> DriverResult<usize> {
    unsafe {
        for ch in text.chars() {
            let mut units = [0u16; 2];
            let units = ch.encode_utf16(&mut units);
            for key_down in [true, false] {
                let event = CGEventCreateKeyboardEvent(std::ptr::null(), 0, key_down);
                if event.is_null() { return Err(DriverError::Internal("failed to create key event".into())); }
                CGEventKeyboardSetUnicodeString(event, units.len() as u64, units.as_ptr());
                post(route, event)?;
                CFRelease(event as *const c_void);
            }
        }
    }
    Ok(text.chars().count())
}

/// Presses one named key with `modifiers`, as a shortcut: the virtual key
/// code of a US ANSI keyboard, which is what Resolve's shortcuts bind.
pub fn key(route: Route, key_name: &str, modifiers: &[String]) -> DriverResult<()> {
    let key_code = key_code_for(key_name)
        .ok_or_else(|| DriverError::InvalidParams(format!("unknown key: {}", key_name)))?;
    let flags = modifier_flags(modifiers);
    unsafe {
        for key_down in [true, false] {
            let event = CGEventCreateKeyboardEvent(std::ptr::null(), key_code, key_down);
            if event.is_null() { return Err(DriverError::Internal("failed to create key event".into())); }
            CGEventSetFlags(event, flags.0);
            post(route, event)?;
            CFRelease(event as *const c_void);
        }
    }
    Ok(())
}

fn key_code_for(name: &str) -> Option<u16> {
    let code = match name {
        "return" => 36, "tab" => 48, "escape" => 53, "space" => 49, "delete" => 51,
        "arrow_up" => 126, "arrow_down" => 125, "arrow_left" => 123, "arrow_right" => 124,
        "home" => 115, "end" => 119, "page_up" => 116, "page_down" => 121,
        "f1" => 122, "f2" => 120, "f3" => 99, "f4" => 118, "f5" => 96, "f6" => 97,
        "f7" => 98, "f8" => 100, "f9" => 101, "f10" => 109, "f11" => 103, "f12" => 111,
        "a" => 0, "s" => 1, "d" => 2, "f" => 3, "h" => 4, "g" => 5, "z" => 6, "x" => 7,
        "c" => 8, "v" => 9, "b" => 11, "q" => 12, "w" => 13, "e" => 14, "r" => 15,
        "y" => 16, "t" => 17, "1" => 18, "2" => 19, "3" => 20, "4" => 21, "6" => 22,
        "5" => 23, "=" => 24, "9" => 25, "7" => 26, "-" => 27, "8" => 28, "0" => 29,
        "]" => 30, "o" => 31, "u" => 32, "[" => 33, "i" => 34, "p" => 35, "l" => 37,
        "j" => 38, "'" => 39, "k" => 40, ";" => 41, "\\" => 42, "," => 43, "/" => 44,
        "n" => 45, "m" => 46, "." => 47, "`" => 50,
        _ => return None,
    };
    Some(code)
}

fn modifier_flags(modifiers: &[String]) -> CGEventFlags {
    let mut flags = CGEventFlags::empty();
    for m in modifiers {
        match m.as_str() {
            "cmd" | "command" => flags = flags | CGEventFlags::COMMAND,
            "shift" => flags = flags | CGEventFlags::SHIFT,
            "option" | "alt" => flags = flags | CGEventFlags::ALTERNATE,
            "ctrl" | "control" => flags = flags | CGEventFlags::CONTROL,
            "fn" | "function" => flags = flags | CGEventFlags::SECONDARY_FN,
            _ => {}
        }
    }
    flags
}

pub fn get_pointer_state() -> DriverResult<(f64, f64, String)> {
    unsafe {
        let event = CGEventCreate(std::ptr::null());
        if event.is_null() { return Err(DriverError::Internal("failed to create event".into())); }
        let loc = CGEventGetLocation(event);
        let button_state = CGEventGetIntegerValueField(event, kCGMouseEventButtonNumber);
        CFRelease(event as *const c_void);
        let button = if button_state != 0 { "left" } else { "none" };
        Ok((loc.x, loc.y, button.to_string()))
    }
}

extern "C" {
    pub fn CGEventGetIntegerValueField(event: *mut c_void, field: u32) -> i64;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn foreground_resolves_to_the_hid_tap() {
        // The old `post` called itself for this arm - unbounded recursion
        // that stack-overflowed the driver on any foreground input. The
        // tap mapping pins the fix without posting anything.
        assert_eq!(post_target(Route::Foreground), PostTarget::HidTap);
    }

    #[test]
    fn routes_resolve_to_their_channels() {
        assert_eq!(
            post_target(Route::Background { pid: 11, window_id: 22 }),
            PostTarget::Pid(11)
        );
        assert_eq!(
            post_target(Route::SkyLight { pid: 11, window_id: 22, chromium: true }),
            PostTarget::SkyLightPid(11)
        );
    }

    #[test]
    fn foreground_click_events_carry_the_hid_source() {
        // From CGEventTypes.h (MacOSX SDK): the field that names an
        // event's source state, and the HID-system value a hand click
        // carries.
        const kCGEventSourceStateID: u32 = 45;
        const kCGEventSourceStateHIDSystemState: i64 = 1;
        // The defect, measured live: the old path built events with a
        // NULL source, Qt dropped them while reporting clicked:true,
        // and nothing in the repo could tell. Every event the
        // foreground path builds must read the same source state as an
        // explicit HIDSystemState reference - and not the same as a
        // NULL-sourced one. Builds only; nothing is posted.
        unsafe {
            let source = skylight::event_source()
                .expect("HIDSystemState source must exist");
            let point = CGPoint { x: 10.0, y: 20.0 };
            let reference = CGEventCreateMouseEvent(
                source, CGEventType::LeftMouseDown, point, CGMouseButton::Left,
            );
            assert!(!reference.is_null());
            let ours = fg_click_event(source, CGEventType::LeftMouseDown, point);
            assert!(!ours.is_null());
            let nulled = CGEventCreateMouseEvent(
                std::ptr::null(), CGEventType::LeftMouseDown, point, CGMouseButton::Left,
            );
            assert!(!nulled.is_null());
            let field = |event| CGEventGetIntegerValueField(event, kCGEventSourceStateID);
            assert_eq!(field(reference), kCGEventSourceStateHIDSystemState);
            assert_eq!(field(ours), kCGEventSourceStateHIDSystemState);
            assert_ne!(field(ours), field(nulled));
            CFRelease(reference as *const c_void);
            CFRelease(ours as *const c_void);
            CFRelease(nulled as *const c_void);
            CFRelease(source);
        }
    }

    #[test]
    fn cleanup_guard_runs_on_panic_unwind() {
        // The guarantee behind the cursor recouple: the cleanup runs
        // however the scope ends. A flag records it; catch_unwind
        // proves the panic still propagates after cleanup ran.
        use std::cell::Cell;
        use std::panic::{catch_unwind, AssertUnwindSafe};
        let cleaned = Cell::new(false);
        let caught = catch_unwind(AssertUnwindSafe(|| {
            let _guard = CleanupGuard::new(|| cleaned.set(true));
            panic!("simulated mid-sequence failure");
        }));
        assert!(caught.is_err());
        assert!(cleaned.get());
    }

    #[test]
    fn cleanup_guard_runs_on_normal_exit() {
        use std::cell::Cell;
        let cleaned = Cell::new(false);
        {
            let _guard = CleanupGuard::new(|| cleaned.set(true));
        }
        assert!(cleaned.get());
    }

    #[test]
    fn recouple_cursor_is_safe_to_call_headless() {
        // Re-associating an already-coupled cursor is specified
        // side-effect free: calling it proves the symbol resolves
        // without moving anything (no display attached in tests).
        recouple_cursor();
    }

    #[test]
    fn stamps_land_on_the_event() {
        // Build, stamp, read back through the PUBLIC getter (f1 is a
        // public field): proves stamp_event writes what it claims without
        // posting anything. The private indexes (f40/f51/f58/f91/f92) go
        // through the same call, which would refuse on missing symbols.
        unsafe {
            let event = CGEventCreateMouseEvent(
                std::ptr::null(),
                CGEventType::LeftMouseDown,
                CGPoint { x: 10.0, y: 20.0 },
                CGMouseButton::Left,
            );
            assert!(!event.is_null());
            skylight::stamp_event(event, 1234, 99, 10.0, 20.0, 2, 0, 3, 777, None)
                .expect("stamps must apply");
            assert_eq!(CGEventGetIntegerValueField(event, kCGMouseEventClickState), 2);
            CFRelease(event as *const c_void);
        }
    }
}
