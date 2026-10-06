use crate::protocol::{DriverError, DriverResult, Frame};
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
}

/// Where an event goes. `Background` delivers it to Resolve's process alone:
/// the real cursor does not move and keyboard focus stays with the captain's
/// app. `Foreground` posts it at the HID tap, as if typed or clicked by hand -
/// it moves the cursor and reaches whatever is frontmost.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route { Background { pid: i32, window_id: u32 }, Foreground }

/// kCGMouseEventWindowUnderMousePointer and ...ThatCanHandleThisEvent: a
/// background mouse event names the window it is for, since the window server
/// cannot find it under a cursor that is elsewhere.
const MOUSE_WINDOW_FIELDS: [u32; 2] = [91, 92];

unsafe fn post(route: Route, event: *mut c_void) {
    match route {
        Route::Background { pid, window_id } => {
            for field in MOUSE_WINDOW_FIELDS {
                CGEventSetIntegerValueField(event, field, window_id as i64);
            }
            CGEventPostToPid(pid, event)
        }
        Route::Foreground => post(route, event),
    }
}

// CGEventField constants
const kCGMouseEventClickState: u32 = 1;
const kCGMouseEventButtonNumber: u32 = 3;
const kCGKeyboardEventKeycode: u32 = 9;

pub fn click(route: Route, x: f64, y: f64, window_frame: &Frame, clicks: u32) -> DriverResult<()> {
    let sx = window_frame.x + x;
    let sy = window_frame.y + y;
    unsafe {
        for click_num in 0..clicks {
            let down = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseDown, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if down.is_null() { return Err(DriverError::Internal("failed to create mouse down".into())); }
            if click_num > 0 {
                CGEventSetIntegerValueField(down, kCGMouseEventClickState, click_num as i64 + 1);
            }
            post(route, down);
            CFRelease(down as *const c_void);

            let up = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseUp, CGPoint { x: sx, y: sy }, CGMouseButton::Left);
            if up.is_null() { return Err(DriverError::Internal("failed to create mouse up".into())); }
            if click_num > 0 {
                CGEventSetIntegerValueField(up, kCGMouseEventClickState, click_num as i64 + 1);
            }
            post(route, up);
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
        post(route, down);
        CFRelease(down as *const c_void);

        let move_ = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseDragged, CGPoint { x: ex, y: ey }, CGMouseButton::Left);
        if move_.is_null() { return Err(DriverError::Internal("failed to create drag move".into())); }
        post(route, move_);
        CFRelease(move_ as *const c_void);

        let up = CGEventCreateMouseEvent(std::ptr::null(), CGEventType::LeftMouseUp, CGPoint { x: ex, y: ey }, CGMouseButton::Left);
        if up.is_null() { return Err(DriverError::Internal("failed to create drag up".into())); }
        post(route, up);
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
        post(route, scroll);
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
                post(route, event);
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
            post(route, event);
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
