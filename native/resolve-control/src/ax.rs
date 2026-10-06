use crate::protocol::{DriverError, DriverResult, Frame};
use std::ffi::c_void;

pub type AXUIElementRef = *mut c_void;
pub type AXValueRef = *mut c_void;
pub type CFTypeRef = *const c_void;
pub type CFArrayRef = *const c_void;
pub type CFStringRef = *const c_void;
pub type CFIndex = isize;
pub type CFTypeID = u64;
pub type SEL = *const c_void;

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AXError(pub i32);

pub const kAXErrorSuccess: AXError = AXError(0);
pub const kAXErrorActionUnsupported: AXError = AXError(-25206);
pub const kAXErrorAttributeUnsupported: AXError = AXError(-25205);

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGPoint { pub x: f64, pub y: f64 }
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGSize { pub width: f64, pub height: f64 }

extern "C" {
    // Accessibility
    static kCFBooleanTrue: CFTypeRef;
    pub fn AXUIElementCreateApplication(pid: u32) -> AXUIElementRef;
    pub fn AXUIElementCopyAttributeValue(element: AXUIElementRef, attribute: CFStringRef, value: *mut CFTypeRef) -> AXError;
    pub fn AXUIElementCopyActionNames(element: AXUIElementRef, names: *mut CFArrayRef) -> AXError;
    pub fn AXUIElementPerformAction(element: AXUIElementRef, action: CFStringRef) -> AXError;
    pub fn AXUIElementSetAttributeValue(element: AXUIElementRef, attribute: CFStringRef, value: CFTypeRef) -> AXError;
    pub fn AXValueGetValue(value: AXValueRef, value_type: u32, out: *mut c_void) -> bool;
    pub fn AXValueGetTypeID() -> CFTypeID;
    pub fn AXIsProcessTrusted() -> bool;
    // CoreFoundation
    pub fn CFRelease(cf: CFTypeRef);
    pub fn CFRetain(cf: CFTypeRef) -> CFTypeRef;
    pub fn CFArrayGetCount(array: CFArrayRef) -> CFIndex;
    pub fn CFArrayGetValueAtIndex(array: CFArrayRef, index: CFIndex) -> *const c_void;
    pub fn CFStringGetLength(string: CFStringRef) -> CFIndex;
    pub fn CFStringGetCString(string: CFStringRef, buffer: *mut u8, buffer_size: isize, encoding: u32) -> bool;
    pub fn CFStringCreateWithCString(alloc: *const c_void, c_str: *const u8, encoding: u32) -> CFStringRef;
    pub fn CFGetTypeID(cf: CFTypeRef) -> CFTypeID;
    pub fn CFArrayGetTypeID() -> CFTypeID;
    pub fn CFStringGetTypeID() -> CFTypeID;
    pub fn CFNumberGetTypeID() -> CFTypeID;
    pub fn CFBooleanGetTypeID() -> CFTypeID;
    pub fn CFNumberGetValue(number: *const c_void, value_type: i32, out: *mut c_void) -> bool;
    // Objective-C runtime
    pub fn objc_getClass(name: *const u8) -> *const c_void;
    pub fn sel_registerName(name: *const u8) -> SEL;
    pub fn objc_msgSend(obj: *mut c_void, sel: SEL) -> *mut c_void;
}

pub const kCFStringEncodingUTF8: u32 = 0x08000100;
pub const kAXValueCGPointType: u32 = 1;
pub const kAXValueCGSizeType: u32 = 2;
pub const kCFNumberSInt32Type: i32 = 3;

fn cfstr(s: &str) -> CFStringRef {
    unsafe { CFStringCreateWithCString(std::ptr::null(), format!("{}\0", s).as_ptr(), kCFStringEncodingUTF8) }
}

fn sel_cstr(s: &str) -> SEL {
    unsafe { sel_registerName(format!("{}\0", s).as_ptr()) }
}

fn cf_string_to_string(cf: CFStringRef) -> Option<String> {
    if cf.is_null() { return None; }
    unsafe {
        let len = CFStringGetLength(cf);
        if len == 0 { return Some(String::new()); }
        let capacity = len * 4 + 1;
        let mut buf = vec![0u8; capacity as usize];
        if !CFStringGetCString(cf, buf.as_mut_ptr(), capacity, kCFStringEncodingUTF8) { return None; }
        let cstr = std::ffi::CStr::from_bytes_until_nul(&buf).ok()?;
        Some(cstr.to_string_lossy().into_owned())
    }
}

fn cf_array_to_vec(array: CFArrayRef) -> Vec<CFTypeRef> {
    if array.is_null() { return vec![]; }
    unsafe {
        let count = CFArrayGetCount(array);
        (0..count).map(|i| CFArrayGetValueAtIndex(array, i) as CFTypeRef).collect()
    }
}

fn get_attr(element: AXUIElementRef, attr: &str) -> Option<CFTypeRef> {
    unsafe {
        let attr_cf = cfstr(attr);
        let mut value: CFTypeRef = std::ptr::null();
        let err = AXUIElementCopyAttributeValue(element, attr_cf, &mut value);
        CFRelease(attr_cf);
        if err == kAXErrorSuccess && !value.is_null() { Some(value) } else { None }
    }
}

fn get_attr_string(element: AXUIElementRef, attr: &str) -> Option<String> {
    let value = get_attr(element, attr)?;
    unsafe {
        if CFGetTypeID(value) == CFStringGetTypeID() {
            let s = cf_string_to_string(value as CFStringRef);
            CFRelease(value);
            s
        } else { CFRelease(value); None }
    }
}

fn get_attr_bool(element: AXUIElementRef, attr: &str) -> Option<bool> {
    let value = get_attr(element, attr)?;
    unsafe {
        if CFGetTypeID(value) == CFBooleanGetTypeID() {
            let b = *(value as *const u8);
            CFRelease(value);
            Some(b != 0)
        } else { CFRelease(value); None }
    }
}

fn get_attr_frame(element: AXUIElementRef) -> Option<Frame> {
    let pos = get_attr(element, "AXPosition")?;
    let size = get_attr(element, "AXSize")?;
    unsafe {
        if CFGetTypeID(pos) != AXValueGetTypeID() || CFGetTypeID(size) != AXValueGetTypeID() {
            CFRelease(pos); CFRelease(size); return None;
        }
        let mut pt = CGPoint { x: 0.0, y: 0.0 };
        let mut sz = CGSize { width: 0.0, height: 0.0 };
        let ok1 = AXValueGetValue(pos as AXValueRef, kAXValueCGPointType, &mut pt as *mut CGPoint as *mut c_void);
        let ok2 = AXValueGetValue(size as AXValueRef, kAXValueCGSizeType, &mut sz as *mut CGSize as *mut c_void);
        CFRelease(pos); CFRelease(size);
        if ok1 && ok2 { Some(Frame { x: pt.x, y: pt.y, width: sz.width, height: sz.height }) } else { None }
    }
}

fn get_attr_actions(element: AXUIElementRef) -> Vec<String> {
    unsafe {
        let mut names: CFArrayRef = std::ptr::null();
        if AXUIElementCopyActionNames(element, &mut names) != kAXErrorSuccess || names.is_null() { return vec![]; }
        let items = cf_array_to_vec(names);
        let result = items.into_iter().filter_map(|item| {
            if CFGetTypeID(item) == CFStringGetTypeID() { cf_string_to_string(item as CFStringRef) } else { None }
        }).collect();
        CFRelease(names);
        result
    }
}

pub struct AxElement { pub inner: AXUIElementRef }

impl Drop for AxElement {
    fn drop(&mut self) {
        if !self.inner.is_null() {
            unsafe { CFRelease(self.inner as CFTypeRef) };
        }
    }
}

impl AxElement {
    pub fn from_app(pid: u32) -> Self {
        AxElement { inner: unsafe { AXUIElementCreateApplication(pid) } }
    }

    pub fn owned(&self) -> AxElement {
        unsafe { CFRetain(self.inner as CFTypeRef) };
        AxElement { inner: self.inner }
    }
    pub fn role(&self) -> Option<String> { get_attr_string(self.inner, "AXRole") }
    pub fn title(&self) -> Option<String> { get_attr_string(self.inner, "AXTitle") }
    pub fn description(&self) -> Option<String> { get_attr_string(self.inner, "AXDescription") }
    pub fn value(&self) -> Option<String> { get_attr_string(self.inner, "AXValue") }
    pub fn frame(&self) -> Option<Frame> { get_attr_frame(self.inner) }
    pub fn actions(&self) -> Vec<String> { get_attr_actions(self.inner) }
    pub fn enabled(&self) -> Option<bool> { get_attr_bool(self.inner, "AXEnabled") }
    pub fn focused(&self) -> Option<bool> { get_attr_bool(self.inner, "AXFocused") }
    pub fn children(&self) -> Vec<AxElement> {
        let value = match get_attr(self.inner, "AXChildren") { Some(v) => v, None => return vec![] };
        unsafe {
            if CFGetTypeID(value) != CFArrayGetTypeID() {
                CFRelease(value);
                return vec![];
            }
            let items = cf_array_to_vec(value as CFArrayRef);
            let owned: Vec<AXUIElementRef> = items
                .into_iter()
                .map(|c| { CFRetain(c); c as AXUIElementRef })
                .collect();
            CFRelease(value);
            owned.into_iter().map(|inner| AxElement { inner }).collect()
        }
    }
    pub fn press(&self) -> DriverResult<()> {
        unsafe {
            let action = cfstr("AXPress");
            let err = AXUIElementPerformAction(self.inner, action);
            CFRelease(action);
            if err == kAXErrorSuccess { Ok(()) }
            else if err == kAXErrorActionUnsupported { Err(DriverError::ActionNotSupported("AXPress".to_string())) }
            else { Err(DriverError::Internal(format!("AXPress failed: AXError({})", err.0))) }
        }
    }
    pub fn set_value(&self, value: &str) -> DriverResult<()> {
        unsafe {
            let attr = cfstr("AXValue");
            let val = cfstr(value);
            let err = AXUIElementSetAttributeValue(self.inner, attr, val as CFTypeRef);
            CFRelease(attr); CFRelease(val);
            if err == kAXErrorSuccess { Ok(()) }
            else if err == kAXErrorAttributeUnsupported { Err(DriverError::ActionNotSupported("AXValue".to_string())) }
            else { Err(DriverError::Internal(format!("set value failed: AXError({})", err.0))) }
        }
    }
    pub fn set_focused(&self) -> DriverResult<()> {
        unsafe {
            let attr = cfstr("AXFocused");
            let err = AXUIElementSetAttributeValue(self.inner, attr, kCFBooleanTrue);
            CFRelease(attr);
            if err == kAXErrorSuccess { Ok(()) }
            else if err == kAXErrorAttributeUnsupported { Err(DriverError::ActionNotSupported("AXFocused".to_string())) }
            else { Err(DriverError::Internal(format!("set focused failed: AXError({})", err.0))) }
        }
    }
}

pub fn ax_process_trusted() -> bool { unsafe { AXIsProcessTrusted() } }

/// A plain-data view of an AX node. The command inventory is computed from
/// this so the collection logic is testable without a live app.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct AxNode {
    pub role: Option<String>,
    pub title: Option<String>,
    pub actions: Vec<String>,
    pub enabled: Option<bool>,
    pub children: Vec<AxNode>,
}

impl AxNode {
    pub fn from_element(el: &AxElement) -> Self {
        AxNode {
            role: el.role(),
            title: el.title(),
            actions: el.actions(),
            enabled: el.enabled(),
            children: el.children().iter().map(AxNode::from_element).collect(),
        }
    }
}

/// One command the AX route can run: the full menu path from a menu bar item
/// to the addressable item itself, outermost first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Command {
    pub path: Vec<String>,
    pub actions: Vec<String>,
    pub enabled: Option<bool>,
}

/// Depth-first walk collecting every addressable menu item. Only menu bar
/// items and menu items form the path (the same rule `find_element`'s path
/// uses), so a collected path is directly usable with `find_element` as
/// `path` (all but the last title) plus `title` (the last one), and with
/// `run_command` as the whole path.
pub fn collect_commands(node: &AxNode, path: &mut Vec<String>, out: &mut Vec<Command>) {
    let mut pushed = false;
    if matches!(node.role.as_deref(), Some("AXMenuBarItem") | Some("AXMenuItem")) {
        if let Some(title) = &node.title {
            if !title.is_empty() {
                path.push(title.clone());
                pushed = true;
            }
        }
    }
    if node.role.as_deref() == Some("AXMenuItem") && pushed {
        out.push(Command {
            path: path.clone(),
            actions: node.actions.clone(),
            enabled: node.enabled,
        });
    }
    for child in &node.children {
        collect_commands(child, path, out);
    }
    if pushed {
        path.pop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn item(title: &str, children: Vec<AxNode>) -> AxNode {
        AxNode {
            role: Some("AXMenuItem".into()),
            title: Some(title.into()),
            actions: vec!["AXPress".into()],
            enabled: Some(true),
            children,
        }
    }

    fn bar_item(title: &str, children: Vec<AxNode>) -> AxNode {
        AxNode {
            role: Some("AXMenuBarItem".into()),
            title: Some(title.into()),
            children,
            ..Default::default()
        }
    }

    fn menu(children: Vec<AxNode>) -> AxNode {
        AxNode {
            role: Some("AXMenu".into()),
            children,
            ..Default::default()
        }
    }

    fn collect(root: &AxNode) -> Vec<Command> {
        let mut path = Vec::new();
        let mut out = Vec::new();
        collect_commands(root, &mut path, &mut out);
        out
    }

    #[test]
    fn a_nested_menu_yields_the_full_path_to_each_item() {
        let root = AxNode {
            role: Some("AXApplication".into()),
            title: Some("DaVinci Resolve".into()),
            children: vec![AxNode {
                role: Some("AXMenuBar".into()),
                children: vec![bar_item("Workspace", vec![menu(vec![
                    item("Switch to Page", vec![menu(vec![
                        item("Media", vec![]),
                        item("Edit", vec![]),
                    ])]),
                    item("About DaVinci Resolve", vec![]),
                ])])],
                ..Default::default()
            }],
            ..Default::default()
        };
        let commands = collect(&root);
        let paths: Vec<Vec<String>> = commands.iter().map(|c| c.path.clone()).collect();
        assert!(paths.contains(&vec!["Workspace".into(), "Switch to Page".into(), "Media".into()]));
        assert!(paths.contains(&vec!["Workspace".into(), "Switch to Page".into(), "Edit".into()]));
        assert!(paths.contains(&vec!["Workspace".into(), "About DaVinci Resolve".into()]));
        assert!(paths.contains(&vec!["Workspace".into(), "Switch to Page".into()]));
        assert_eq!(commands.len(), 4);
    }

    #[test]
    fn non_menu_titles_do_not_pollute_the_path() {
        let root = AxNode {
            role: Some("AXApplication".into()),
            title: Some("DaVinci Resolve".into()),
            children: vec![bar_item("Workspace", vec![AxNode {
                role: Some("AXWindow".into()),
                title: Some("Ren Qualification".into()),
                children: vec![item("Media", vec![])],
                ..Default::default()
            }])],
            ..Default::default()
        };
        let commands = collect(&root);
        assert_eq!(commands.len(), 1);
        assert_eq!(commands[0].path, vec![String::from("Workspace"), String::from("Media")]);
    }

    #[test]
    fn empty_titles_are_skipped_and_enabled_is_reported_not_defaulted() {
        let root = bar_item("Workspace", vec![
            AxNode {
                role: Some("AXMenuItem".into()),
                title: Some(String::new()),
                children: vec![item("Media", vec![])],
                ..Default::default()
            },
            AxNode {
                role: Some("AXMenuItem".into()),
                title: Some("Greyed Out".into()),
                enabled: Some(false),
                ..Default::default()
            },
            AxNode {
                role: Some("AXMenuItem".into()),
                title: Some("No Enabled Attr".into()),
                enabled: None,
                ..Default::default()
            },
        ]);
        let commands = collect(&root);
        assert_eq!(commands.len(), 3);
        assert_eq!(commands[0].path, vec![String::from("Workspace"), String::from("Media")]);
        assert_eq!(commands[1].enabled, Some(false));
        assert_eq!(commands[2].enabled, None);
    }
}

// Objective-C helpers via objc_msgSend.
// On arm64, objc_msgSend must be cast to the correct function pointer type
// before calling - the variadic extern declaration generates the wrong
// calling convention.
unsafe fn nsclass(name: &str) -> *const c_void {
    objc_getClass(format!("{}\0", name).as_ptr())
}

unsafe fn send(obj: *const c_void, sel: SEL) -> *mut c_void {
    objc_msgSend(obj as *mut c_void, sel)
}
unsafe fn send1(obj: *const c_void, sel: SEL, a: *const c_void) -> *mut c_void {
    let f: unsafe extern "C" fn(*mut c_void, SEL, *const c_void) -> *mut c_void =
        std::mem::transmute(objc_msgSend as *const ());
    f(obj as *mut c_void, sel, a)
}
unsafe fn send_i(obj: *const c_void, sel: SEL, a: isize) -> *mut c_void {
    let f: unsafe extern "C" fn(*mut c_void, SEL, isize) -> *mut c_void =
        std::mem::transmute(objc_msgSend as *const ());
    f(obj as *mut c_void, sel, a)
}

unsafe fn get_str_property(obj: *const c_void, key: &str) -> Option<String> {
    let key_cf = cfstr(key);
    let sel = sel_cstr("valueForKey:");
    let result = send1(obj, sel, key_cf);
    CFRelease(key_cf);
    if result.is_null() { return None; }
    if CFGetTypeID(result as CFTypeRef) == CFStringGetTypeID() {
        cf_string_to_string(result as CFStringRef)
    } else { None }
}

unsafe fn get_int_property(obj: *const c_void, key: &str) -> Option<i64> {
    let key_cf = cfstr(key);
    let sel = sel_cstr("valueForKey:");
    let result = send1(obj, sel, key_cf);
    CFRelease(key_cf);
    if result.is_null() { return None; }
    if CFGetTypeID(result as CFTypeRef) == CFNumberGetTypeID() {
        let mut val: i64 = 0;
        if CFNumberGetValue(result as CFTypeRef, 9, &mut val as *mut i64 as *mut c_void) { // kCFNumberSInt64Type = 9
            return Some(val);
        }
    }
    None
}

extern "C" {
    fn nsworkspace_find_pid(bundle_id: *const u8) -> i64;
    fn nsworkspace_activate_pid(pid: i64) -> i32;
    fn nsworkspace_frontmost() -> *mut i8;
    fn nsworkspace_free(s: *mut i8);
}

pub fn find_resolve_pid() -> Option<u32> {
    unsafe {
        let bid = b"com.blackmagic-design.DaVinciResolve\0";
        let pid = nsworkspace_find_pid(bid.as_ptr());
        if pid > 0 {
            return Some(pid as u32);
        }
    }
    None
}

pub unsafe fn activate_app_with_pid(pid: u32) -> bool {
    nsworkspace_activate_pid(pid as i64) != 0
}

/// The frontmost application's pid.
pub fn frontmost_pid() -> Option<u32> {
    unsafe { get_frontmost_json() }?.get("pid")?.as_u64().map(|p| p as u32)
}

unsafe fn get_frontmost_json() -> Option<serde_json::Value> {
    let json_str = nsworkspace_frontmost();
    if json_str.is_null() { return None; }
    let json_string = std::ffi::CStr::from_ptr(json_str).to_string_lossy().into_owned();
    nsworkspace_free(json_str);
    serde_json::from_str(&json_string).ok()
}

pub unsafe fn get_frontmost() -> Option<(String, String)> {
    let json_str = nsworkspace_frontmost();
    if json_str.is_null() { return None; }
    let cstr = std::ffi::CStr::from_ptr(json_str);
    let json_string = cstr.to_string_lossy().into_owned();
    nsworkspace_free(json_str);
    let json: serde_json::Value = serde_json::from_str(&json_string).ok()?;
    let bid = json.get("bundle_id")?.as_str()?.to_string();
    let name = json.get("name")?.as_str()?.to_string();
    Some((bid, name))
}
