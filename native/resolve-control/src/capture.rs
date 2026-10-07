use crate::protocol::{DriverError, DriverResult, Frame};
use std::ffi::c_void;

pub type CGWindowID = u32;
pub type CGImageRef = *mut c_void;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGRect {
    pub origin: CGPoint,
    pub size: CGSize,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGPoint { pub x: f64, pub y: f64 }
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGSize { pub width: f64, pub height: f64 }

/// CGWindowListOption and CGWindowImageOption are C bitmasks, so they are
/// plain u32 flags: a Rust enum cannot hold an OR of two variants.
pub type CGWindowListOption = u32;
pub const LIST_OPTION_ALL: CGWindowListOption = 0;
pub const LIST_ON_SCREEN_ONLY: CGWindowListOption = 1 << 0;
pub const LIST_INCLUDING_WINDOW: CGWindowListOption = 1 << 3;

pub type CGWindowImageOption = u32;
pub const IMAGE_BOUNDS_IGNORE_FRAMING: CGWindowImageOption = 1 << 0;
pub const IMAGE_BEST_RESOLUTION: CGWindowImageOption = 1 << 3;

extern "C" {
    pub fn CGWindowListCreateImage(
        rect: CGRect,
        list_option: CGWindowListOption,
        window_id: CGWindowID,
        image_option: CGWindowImageOption,
    ) -> CGImageRef;
    pub fn CGWindowListCopyWindowInfo(list_option: CGWindowListOption, relative_to_window: CGWindowID) -> *const c_void;
    pub fn CFArrayGetCount(array: *const c_void) -> isize;
    pub fn CFArrayGetValueAtIndex(array: *const c_void, index: isize) -> *const c_void;
    pub fn CFGetTypeID(cf: *const c_void) -> u64;
    pub fn CFArrayGetTypeID() -> u64;
    pub fn CFStringGetTypeID() -> u64;
    pub fn CFNumberGetTypeID() -> u64;
    pub fn CFStringGetLength(string: *const c_void) -> isize;
    pub fn CFStringGetCString(string: *const c_void, buffer: *mut u8, buffer_size: isize, encoding: u32) -> bool;
    pub fn CFNumberGetValue(number: *const c_void, value_type: i32, out: *mut c_void) -> bool;
    pub fn CFRelease(cf: *const c_void);
    fn CGPreflightScreenCaptureAccess() -> bool;
    // ImageIO
    pub fn CGImageGetWidth(image: CGImageRef) -> usize;
    pub fn CGImageGetHeight(image: CGImageRef) -> usize;
    pub fn CGImageCreateWithImageInRect(image: CGImageRef, rect: CGRect) -> CGImageRef;
    pub fn CGImageDestinationCreateWithData(data: *mut c_void, type_id: *const c_void, count: usize, options: *const c_void) -> *mut c_void;
    pub fn CGImageDestinationAddImage(dest: *mut c_void, image: CGImageRef, options: *const c_void) -> bool;
    pub fn CGImageDestinationFinalize(dest: *mut c_void) -> bool;
    pub fn UTTypeCreatePreferredIdentifierForTag(tag_class: *const c_void, tag: *const c_void, conforming_to: *const c_void) -> *const c_void;
    // For PNG data
    pub fn CFDataCreate(alloc: *const c_void, bytes: *const u8, length: isize) -> *const c_void;
    pub fn CFDataGetLength(data: *const c_void) -> isize;
    pub fn CFDataGetBytePtr(data: *const c_void) -> *const u8;
    pub fn CGImageGetDataProvider(image: CGImageRef) -> *const c_void;
    pub fn CGDataProviderCopyData(provider: *const c_void) -> *const c_void;
}

pub const kCFStringEncodingUTF8: u32 = 0x08000100;
pub const kCFNumberSInt32Type: i32 = 3;
pub const kUTTypePNG: *const c_void = b"public.png\0" as *const u8 as *const c_void;

/// Whether this process may read other apps' window pixels and titles.
pub fn screen_recording_granted() -> bool {
    unsafe { CGPreflightScreenCaptureAccess() }
}

pub struct CapturedImage {
    pub width: u32,
    pub height: u32,
    pub scale: f64,
    pub png_base64: String,
}

/// The window alone, at its backing resolution. `scale` is pixels per point,
/// measured from the image against `window_frame`.
pub fn capture_window(window_id: CGWindowID, window_frame: &Frame, scale: f64, source: Source) -> DriverResult<CapturedImage> {
    let image_ref = window_image(window_id, scale, source)?;
    unsafe { encode(image_ref, window_frame.width) }
}

/// A window-relative region (points) of the window alone, cropped from the
/// window's own capture: nothing outside the window can reach the image.
pub fn capture_region(window_id: CGWindowID, window_frame: &Frame, scale: f64, source: Source, region: &Frame) -> DriverResult<CapturedImage> {
    let image_ref = window_image(window_id, scale, source)?;
    unsafe {
        let scale = crate::ocr::pixels_per_point(CGImageGetWidth(image_ref), window_frame);
        let rect = CGRect {
            origin: CGPoint { x: region.x * scale, y: region.y * scale },
            size: CGSize { width: region.width * scale, height: region.height * scale },
        };
        let cropped = CGImageCreateWithImageInRect(image_ref, rect);
        CFRelease(image_ref as *const c_void);
        if cropped.is_null() {
            return Err(DriverError::CaptureFailed("region does not intersect the window image".into()));
        }
        encode(cropped, region.width)
    }
}

/// PNG-encode and release `image_ref`, which shows `points_wide` points.
unsafe fn encode(image_ref: CGImageRef, points_wide: f64) -> DriverResult<CapturedImage> {
    let width = CGImageGetWidth(image_ref) as u32;
    let height = CGImageGetHeight(image_ref) as u32;
    let png = png_base64(image_ref);
    CFRelease(image_ref as *const c_void);
    let scale = if points_wide > 0.0 { width as f64 / points_wide } else { 1.0 };
    Ok(CapturedImage { width, height, scale, png_base64: png? })
}

/// How a window's pixels are read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    /// ScreenCaptureKit: the window server composites the window alone,
    /// so a window covered by other apps still comes back.
    ScreenCaptureKit,
    /// CGWindowListCreateImage: the window's last backing store.
    WindowList,
}

impl Source {
    pub fn parse(name: Option<&str>) -> DriverResult<Source> {
        match name {
            None | Some("screencapturekit") => Ok(Source::ScreenCaptureKit),
            Some("window_list") => Ok(Source::WindowList),
            Some(other) => Err(DriverError::InvalidParams(format!("unknown capture source: {}", other))),
        }
    }
}

/// The window alone as a retained CGImage, read through `source`.
pub fn window_image(window_id: CGWindowID, scale: f64, source: Source) -> DriverResult<CGImageRef> {
    match source {
        Source::WindowList => capture_window_to_cg_image(window_id),
        Source::ScreenCaptureKit => {
            let image = unsafe { window_helper_sck_capture(window_id, scale) };
            if image.is_null() {
                return Err(DriverError::CaptureFailed("ScreenCaptureKit returned no image".into()));
            }
            Ok(image)
        }
    }
}

pub fn capture_window_to_cg_image(window_id: CGWindowID) -> DriverResult<CGImageRef> {
    unsafe {
        let rect = CGRect { origin: CGPoint { x: 0.0, y: 0.0 }, size: CGSize { width: 0.0, height: 0.0 } };
        let image_ref = CGWindowListCreateImage(
            rect,
            LIST_INCLUDING_WINDOW,
            window_id,
            IMAGE_BOUNDS_IGNORE_FRAMING | IMAGE_BEST_RESOLUTION,
        );
        if image_ref.is_null() {
            return Err(DriverError::CaptureFailed("CGWindowListCreateImage returned null".into()));
        }
        Ok(image_ref)
    }
}

extern "C" {
    fn window_helper_list() -> *mut i8;
    fn window_helper_free(s: *mut i8);
    fn window_helper_png_base64(image: CGImageRef) -> *mut i8;
    fn window_helper_screen_locked() -> i32;
    fn window_helper_sck_capture(window_id: u32, scale: f64) -> CGImageRef;
}

/// Whether the screen is locked (captures come back blank, input goes to the lock screen).
pub fn screen_locked() -> bool {
    unsafe { window_helper_screen_locked() != 0 }
}

/// `image` as base64 PNG, encoded by ImageIO.
unsafe fn png_base64(image: CGImageRef) -> DriverResult<String> {
    let raw = window_helper_png_base64(image);
    if raw.is_null() {
        return Err(DriverError::CaptureFailed("PNG encoding failed".into()));
    }
    let out = std::ffi::CStr::from_ptr(raw).to_string_lossy().into_owned();
    window_helper_free(raw);
    Ok(out)
}

pub unsafe fn window_helper_list_raw() -> *mut i8 {
    window_helper_list()
}

pub unsafe fn window_helper_free_raw(s: *mut i8) {
    window_helper_free(s)
}

pub fn get_window_list() -> Vec<(CGWindowID, String, String, Frame, f64)> {
    unsafe {
        let json_str = window_helper_list();
        if json_str.is_null() { return vec![]; }
        let cstr = std::ffi::CStr::from_ptr(json_str);
        let json_string = cstr.to_string_lossy().into_owned();

        let json: serde_json::Value = match serde_json::from_str(&json_string) {
            Ok(v) => v,
            Err(_) => {
                window_helper_free(json_str);
                return vec![];
            }
        };
        window_helper_free(json_str);

        let mut out = Vec::new();
        if let Some(arr) = json.as_array() {
            for item in arr {
                let id = item.get("id").and_then(|v| v.as_u64()).unwrap_or(0) as CGWindowID;
                let title = item.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let owner = item.get("owner").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let frame = item.get("frame").and_then(|f| {
                    Some(Frame {
                        x: f.get("x")?.as_f64()?,
                        y: f.get("y")?.as_f64()?,
                        width: f.get("width")?.as_f64()?,
                        height: f.get("height")?.as_f64()?,
                    })
                }).unwrap_or(Frame { x: 0.0, y: 0.0, width: 0.0, height: 0.0 });
                let scale = item.get("backing_scale").and_then(|v| v.as_f64()).unwrap_or(1.0);
                out.push((id, title, owner, frame, scale));
            }
        }
        out
    }
}

/// Every window owned by `pid`: id, title, frame. The focus restore
/// needs the previous app's window; the listing the protocol exposes
/// (`get_windows`) is Resolve-filtered, so this reads the helper JSON
/// directly (`owner_pid` rides the helper output).
pub fn windows_for_pid(pid: u32) -> Vec<(CGWindowID, String, Frame)> {
    all_windows_with_pids()
        .into_iter()
        .filter(|(p, _, _, _, _, _)| *p == pid)
        .map(|(_, id, _, title, frame, _)| (id, title, frame))
        .collect()
}

/// Every window on the session: owner pid, id, owner name, title, frame.
/// Chrome targeting filters this by owner name plus pid pin.
pub fn all_windows_with_pids() -> Vec<(u32, CGWindowID, String, String, Frame, f64)> {
    unsafe {
        let json_str = window_helper_list();
        if json_str.is_null() { return vec![]; }
        let cstr = std::ffi::CStr::from_ptr(json_str);
        let json_string = cstr.to_string_lossy().into_owned();
        let json: serde_json::Value = match serde_json::from_str(&json_string) {
            Ok(v) => v,
            Err(_) => {
                window_helper_free(json_str);
                return vec![];
            }
        };
        window_helper_free(json_str);
        let mut out = Vec::new();
        if let Some(arr) = json.as_array() {
            for item in arr {
                let frame = item.get("frame").and_then(|f| {
                    Some(Frame {
                        x: f.get("x")?.as_f64()?,
                        y: f.get("y")?.as_f64()?,
                        width: f.get("width")?.as_f64()?,
                        height: f.get("height")?.as_f64()?,
                    })
                }).unwrap_or(Frame { x: 0.0, y: 0.0, width: 0.0, height: 0.0 });
                out.push((
                    item.get("owner_pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                    item.get("id").and_then(|v| v.as_u64()).unwrap_or(0) as CGWindowID,
                    item.get("owner").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    item.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    frame,
                    item.get("backing_scale").and_then(|v| v.as_f64()).unwrap_or(1.0),
                ));
            }
        }
        out
    }
}

extern "C" {
    pub fn CFDictionaryGetTypeID() -> u64;
    pub fn CFDictionaryGetValue(dict: *const c_void, key: *const c_void) -> *const c_void;
}

unsafe fn cf_string_to_string(cf: *const c_void) -> Option<String> {
    if cf.is_null() { return None; }
    let len = CFStringGetLength(cf);
    if len == 0 { return Some(String::new()); }
    let capacity = len * 4 + 1;
    let mut buf = vec![0u8; capacity as usize];
    if !CFStringGetCString(cf, buf.as_mut_ptr(), capacity, kCFStringEncodingUTF8) { return None; }
    let cstr = std::ffi::CStr::from_bytes_until_nul(&buf).ok()?;
    Some(cstr.to_string_lossy().into_owned())
}

unsafe fn get_dict_string(dict: *const c_void, key: &str) -> String {
    let key_cf = CFStringCreateWithCString(std::ptr::null(), format!("{}\0", key).as_ptr(), kCFStringEncodingUTF8);
    let val = CFDictionaryGetValue(dict, key_cf);
    CFRelease(key_cf);
    if val.is_null() { return String::new(); }
    cf_string_to_string(val).unwrap_or_default()
}

unsafe fn get_dict_u32(dict: *const c_void, key: &str) -> u32 {
    let key_cf = CFStringCreateWithCString(std::ptr::null(), format!("{}\0", key).as_ptr(), kCFStringEncodingUTF8);
    let val = CFDictionaryGetValue(dict, key_cf);
    CFRelease(key_cf);
    if val.is_null() { return 0; }
    if CFGetTypeID(val) == CFNumberGetTypeID() {
        let mut n: i64 = 0;
        if CFNumberGetValue(val, 9, &mut n as *mut i64 as *mut c_void) { return n as u32; }
    }
    0
}

unsafe fn get_dict_f64(dict: *const c_void, key: &str) -> Option<f64> {
    let key_cf = CFStringCreateWithCString(std::ptr::null(), format!("{}\0", key).as_ptr(), kCFStringEncodingUTF8);
    let val = CFDictionaryGetValue(dict, key_cf);
    CFRelease(key_cf);
    if val.is_null() { return None; }
    if CFGetTypeID(val) == CFNumberGetTypeID() {
        let mut n: f64 = 0.0;
        if CFNumberGetValue(val, 6, &mut n as *mut f64 as *mut c_void) { return Some(n); }
    }
    None
}

unsafe fn get_dict_value(dict: *const c_void, key: &str) -> *const c_void {
    let key_cf = CFStringCreateWithCString(std::ptr::null(), format!("{}\0", key).as_ptr(), kCFStringEncodingUTF8);
    let val = CFDictionaryGetValue(dict, key_cf);
    CFRelease(key_cf);
    val
}

extern "C" {
    pub fn CFStringCreateWithCString(alloc: *const c_void, c_str: *const u8, encoding: u32) -> *const c_void;
}
