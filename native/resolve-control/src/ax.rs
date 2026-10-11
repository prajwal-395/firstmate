use crate::protocol::{DriverError, DriverResult, Frame};
use std::ffi::c_void;

pub type AXUIElementRef = *mut c_void;
pub type AXValueRef = *mut c_void;
pub type CFTypeRef = *const c_void;
pub type CFArrayRef = *const c_void;
pub type CFStringRef = *const c_void;
pub type CFIndex = isize;
pub type CFTypeID = u64;

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AXError(pub i32);

// Apple's AXError names keep their k-prefix; renaming would diverge from the Accessibility headers.
#[allow(non_upper_case_globals)]
pub const kAXErrorSuccess: AXError = AXError(0);
// Apple's AXError names keep their k-prefix; renaming would diverge from the Accessibility headers.
#[allow(non_upper_case_globals)]
pub const kAXErrorActionUnsupported: AXError = AXError(-25206);
// Apple's AXError names keep their k-prefix; renaming would diverge from the Accessibility headers.
#[allow(non_upper_case_globals)]
pub const kAXErrorAttributeUnsupported: AXError = AXError(-25205);

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct CGSize {
    pub width: f64,
    pub height: f64,
}

extern "C" {
    // Accessibility
    static kCFBooleanTrue: CFTypeRef;
    pub fn AXUIElementCreateApplication(pid: u32) -> AXUIElementRef;
    pub fn AXUIElementSetMessagingTimeout(element: AXUIElementRef, timeout: f32) -> AXError;
    pub fn AXUIElementCopyAttributeValue(
        element: AXUIElementRef,
        attribute: CFStringRef,
        value: *mut CFTypeRef,
    ) -> AXError;
    pub fn AXUIElementCopyActionNames(element: AXUIElementRef, names: *mut CFArrayRef) -> AXError;
    pub fn AXUIElementPerformAction(element: AXUIElementRef, action: CFStringRef) -> AXError;
    pub fn AXUIElementSetAttributeValue(
        element: AXUIElementRef,
        attribute: CFStringRef,
        value: CFTypeRef,
    ) -> AXError;
    pub fn AXValueGetValue(value: AXValueRef, value_type: u32, out: *mut c_void) -> bool;
    pub fn AXValueGetTypeID() -> CFTypeID;
    pub fn AXIsProcessTrusted() -> bool;
    // CoreFoundation
    pub fn CFRelease(cf: CFTypeRef);
    pub fn CFRetain(cf: CFTypeRef) -> CFTypeRef;
    pub fn CFArrayGetCount(array: CFArrayRef) -> CFIndex;
    pub fn CFArrayGetValueAtIndex(array: CFArrayRef, index: CFIndex) -> *const c_void;
    pub fn CFStringGetLength(string: CFStringRef) -> CFIndex;
    pub fn CFStringGetCString(
        string: CFStringRef,
        buffer: *mut u8,
        buffer_size: isize,
        encoding: u32,
    ) -> bool;
    pub fn CFStringCreateWithCString(
        alloc: *const c_void,
        c_str: *const u8,
        encoding: u32,
    ) -> CFStringRef;
    pub fn CFGetTypeID(cf: CFTypeRef) -> CFTypeID;
    pub fn CFArrayGetTypeID() -> CFTypeID;
    pub fn CFStringGetTypeID() -> CFTypeID;
    pub fn CFBooleanGetTypeID() -> CFTypeID;
}

// Apple's CoreFoundation/AXValue names keep their k-prefix; renaming would diverge from the headers.
#[allow(non_upper_case_globals)]
pub const kCFStringEncodingUTF8: u32 = 0x08000100;
// Apple's CoreFoundation/AXValue names keep their k-prefix; renaming would diverge from the headers.
#[allow(non_upper_case_globals)]
pub const kAXValueCGPointType: u32 = 1;
// Apple's CoreFoundation/AXValue names keep their k-prefix; renaming would diverge from the headers.
#[allow(non_upper_case_globals)]
pub const kAXValueCGSizeType: u32 = 2;

fn cfstr(s: &str) -> CFStringRef {
    unsafe {
        CFStringCreateWithCString(
            std::ptr::null(),
            format!("{}\0", s).as_ptr(),
            kCFStringEncodingUTF8,
        )
    }
}

fn cf_string_to_string(cf: CFStringRef) -> Option<String> {
    if cf.is_null() {
        return None;
    }
    unsafe {
        let len = CFStringGetLength(cf);
        if len == 0 {
            return Some(String::new());
        }
        let capacity = len * 4 + 1;
        let mut buf = vec![0u8; capacity as usize];
        if !CFStringGetCString(cf, buf.as_mut_ptr(), capacity, kCFStringEncodingUTF8) {
            return None;
        }
        let cstr = std::ffi::CStr::from_bytes_until_nul(&buf).ok()?;
        Some(cstr.to_string_lossy().into_owned())
    }
}

fn cf_array_to_vec(array: CFArrayRef) -> Vec<CFTypeRef> {
    if array.is_null() {
        return vec![];
    }
    unsafe {
        let count = CFArrayGetCount(array);
        (0..count)
            .map(|i| CFArrayGetValueAtIndex(array, i) as CFTypeRef)
            .collect()
    }
}

fn get_attr(element: AXUIElementRef, attr: &str) -> Option<CFTypeRef> {
    unsafe {
        let attr_cf = cfstr(attr);
        let mut value: CFTypeRef = std::ptr::null();
        let err = AXUIElementCopyAttributeValue(element, attr_cf, &mut value);
        CFRelease(attr_cf);
        if err == kAXErrorSuccess && !value.is_null() {
            Some(value)
        } else {
            None
        }
    }
}

fn get_attr_string(element: AXUIElementRef, attr: &str) -> Option<String> {
    let value = get_attr(element, attr)?;
    unsafe {
        if CFGetTypeID(value) == CFStringGetTypeID() {
            let s = cf_string_to_string(value as CFStringRef);
            CFRelease(value);
            s
        } else {
            CFRelease(value);
            None
        }
    }
}

fn get_attr_bool(element: AXUIElementRef, attr: &str) -> Option<bool> {
    let value = get_attr(element, attr)?;
    unsafe {
        if CFGetTypeID(value) == CFBooleanGetTypeID() {
            let b = *(value as *const u8);
            CFRelease(value);
            Some(b != 0)
        } else {
            CFRelease(value);
            None
        }
    }
}

fn get_attr_frame(element: AXUIElementRef) -> Option<Frame> {
    let pos = get_attr(element, "AXPosition")?;
    let size = get_attr(element, "AXSize")?;
    unsafe {
        if CFGetTypeID(pos) != AXValueGetTypeID() || CFGetTypeID(size) != AXValueGetTypeID() {
            CFRelease(pos);
            CFRelease(size);
            return None;
        }
        let mut pt = CGPoint { x: 0.0, y: 0.0 };
        let mut sz = CGSize {
            width: 0.0,
            height: 0.0,
        };
        let ok1 = AXValueGetValue(
            pos as AXValueRef,
            kAXValueCGPointType,
            &mut pt as *mut CGPoint as *mut c_void,
        );
        let ok2 = AXValueGetValue(
            size as AXValueRef,
            kAXValueCGSizeType,
            &mut sz as *mut CGSize as *mut c_void,
        );
        CFRelease(pos);
        CFRelease(size);
        if ok1 && ok2 {
            Some(Frame {
                x: pt.x,
                y: pt.y,
                width: sz.width,
                height: sz.height,
            })
        } else {
            None
        }
    }
}

fn get_attr_actions(element: AXUIElementRef) -> Vec<String> {
    unsafe {
        let mut names: CFArrayRef = std::ptr::null();
        if AXUIElementCopyActionNames(element, &mut names) != kAXErrorSuccess || names.is_null() {
            return vec![];
        }
        let items = cf_array_to_vec(names);
        let result = items
            .into_iter()
            .filter_map(|item| {
                if CFGetTypeID(item) == CFStringGetTypeID() {
                    cf_string_to_string(item as CFStringRef)
                } else {
                    None
                }
            })
            .collect();
        CFRelease(names);
        result
    }
}

pub struct AxElement {
    pub inner: AXUIElementRef,
}

impl Drop for AxElement {
    fn drop(&mut self) {
        if !self.inner.is_null() {
            unsafe { CFRelease(self.inner as CFTypeRef) };
        }
    }
}

impl AxElement {
    pub fn from_app(pid: u32) -> Self {
        AxElement {
            inner: unsafe { AXUIElementCreateApplication(pid) },
        }
    }

    /// AX requests can block on a complex Qt accessibility tree. Set the
    /// per-application timeout explicitly instead of relying on the short
    /// system default.
    pub fn set_messaging_timeout(&self, seconds: f32) -> DriverResult<()> {
        let err = unsafe { AXUIElementSetMessagingTimeout(self.inner, seconds) };
        if err == kAXErrorSuccess {
            Ok(())
        } else {
            Err(DriverError::Internal(format!(
                "setting AX messaging timeout failed: AXError({})",
                err.0
            )))
        }
    }

    pub fn owned(&self) -> AxElement {
        unsafe { CFRetain(self.inner as CFTypeRef) };
        AxElement { inner: self.inner }
    }
    pub fn role(&self) -> Option<String> {
        get_attr_string(self.inner, "AXRole")
    }
    pub fn title(&self) -> Option<String> {
        get_attr_string(self.inner, "AXTitle")
    }
    pub fn description(&self) -> Option<String> {
        get_attr_string(self.inner, "AXDescription")
    }
    pub fn value(&self) -> Option<String> {
        get_attr_string(self.inner, "AXValue")
    }
    pub fn frame(&self) -> Option<Frame> {
        get_attr_frame(self.inner)
    }
    pub fn actions(&self) -> Vec<String> {
        get_attr_actions(self.inner)
    }
    pub fn enabled(&self) -> Option<bool> {
        get_attr_bool(self.inner, "AXEnabled")
    }
    /// Whether this menu-bar item's menu is open. Absent where the
    /// element is not a menu-bar item or the app does not report it -
    /// callers treat absence as unknown, never as closed.
    pub fn expanded(&self) -> Option<bool> {
        get_attr_bool(self.inner, "AXExpanded")
    }
    pub fn focused(&self) -> Option<bool> {
        get_attr_bool(self.inner, "AXFocused")
    }
    pub fn children(&self) -> Vec<AxElement> {
        let value = match get_attr(self.inner, "AXChildren") {
            Some(v) => v,
            None => return vec![],
        };
        unsafe {
            if CFGetTypeID(value) != CFArrayGetTypeID() {
                CFRelease(value);
                return vec![];
            }
            let items = cf_array_to_vec(value as CFArrayRef);
            let owned: Vec<AXUIElementRef> = items
                .into_iter()
                .map(|c| {
                    CFRetain(c);
                    c as AXUIElementRef
                })
                .collect();
            CFRelease(value);
            owned.into_iter().map(|inner| AxElement { inner }).collect()
        }
    }
    pub fn main_window(&self) -> Option<AxElement> {
        let value = get_attr(self.inner, "AXMainWindow")?;
        let window = AxElement {
            inner: value as AXUIElementRef,
        };
        let _ = window.set_messaging_timeout(5.0);
        matches!(
            window.role().as_deref(),
            Some("AXWindow") | Some("AXStandardWindow")
        )
        .then_some(window)
    }
    pub fn press(&self) -> DriverResult<()> {
        self.act("AXPress")
    }
    /// Perform one named native action. `press` is this with AXPress;
    /// menu invocation picks per hop (openers for hops, a runner for
    /// the leaf) via `choose_menu_action`.
    pub fn act(&self, action: &str) -> DriverResult<()> {
        unsafe {
            let act = cfstr(action);
            let err = AXUIElementPerformAction(self.inner, act);
            CFRelease(act);
            if err == kAXErrorSuccess {
                Ok(())
            } else if err == kAXErrorActionUnsupported {
                Err(DriverError::ActionNotSupported(action.to_string()))
            } else {
                Err(DriverError::Internal(format!(
                    "{action} failed: AXError({})",
                    err.0
                )))
            }
        }
    }
    pub fn set_value(&self, value: &str) -> DriverResult<()> {
        unsafe {
            let attr = cfstr("AXValue");
            let val = cfstr(value);
            let err = AXUIElementSetAttributeValue(self.inner, attr, val as CFTypeRef);
            CFRelease(attr);
            CFRelease(val);
            if err == kAXErrorSuccess {
                Ok(())
            } else if err == kAXErrorAttributeUnsupported {
                Err(DriverError::ActionNotSupported("AXValue".to_string()))
            } else {
                Err(DriverError::Internal(format!(
                    "set value failed: AXError({})",
                    err.0
                )))
            }
        }
    }
    pub fn set_focused(&self) -> DriverResult<()> {
        unsafe {
            let attr = cfstr("AXFocused");
            let err = AXUIElementSetAttributeValue(self.inner, attr, kCFBooleanTrue);
            CFRelease(attr);
            if err == kAXErrorSuccess {
                Ok(())
            } else if err == kAXErrorAttributeUnsupported {
                Err(DriverError::ActionNotSupported("AXFocused".to_string()))
            } else {
                Err(DriverError::Internal(format!(
                    "set focused failed: AXError({})",
                    err.0
                )))
            }
        }
    }
}

pub fn ax_process_trusted() -> bool {
    unsafe { AXIsProcessTrusted() }
}

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
    if matches!(
        node.role.as_deref(),
        Some("AXMenuBarItem") | Some("AXMenuItem")
    ) {
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
                children: vec![bar_item(
                    "Workspace",
                    vec![menu(vec![
                        item(
                            "Switch to Page",
                            vec![menu(vec![item("Media", vec![]), item("Edit", vec![])])],
                        ),
                        item("About DaVinci Resolve", vec![]),
                    ])],
                )],
                ..Default::default()
            }],
            ..Default::default()
        };
        let commands = collect(&root);
        let paths: Vec<Vec<String>> = commands.iter().map(|c| c.path.clone()).collect();
        assert!(paths.contains(&vec![
            "Workspace".into(),
            "Switch to Page".into(),
            "Media".into()
        ]));
        assert!(paths.contains(&vec![
            "Workspace".into(),
            "Switch to Page".into(),
            "Edit".into()
        ]));
        assert!(paths.contains(&vec!["Workspace".into(), "About DaVinci Resolve".into()]));
        assert!(paths.contains(&vec!["Workspace".into(), "Switch to Page".into()]));
        assert_eq!(commands.len(), 4);
    }

    #[test]
    fn non_menu_titles_do_not_pollute_the_path() {
        let root = AxNode {
            role: Some("AXApplication".into()),
            title: Some("DaVinci Resolve".into()),
            children: vec![bar_item(
                "Workspace",
                vec![AxNode {
                    role: Some("AXWindow".into()),
                    title: Some("Ren Qualification".into()),
                    children: vec![item("Media", vec![])],
                    ..Default::default()
                }],
            )],
            ..Default::default()
        };
        let commands = collect(&root);
        assert_eq!(commands.len(), 1);
        assert_eq!(
            commands[0].path,
            vec![String::from("Workspace"), String::from("Media")]
        );
    }

    #[test]
    fn empty_titles_are_skipped_and_enabled_is_reported_not_defaulted() {
        let root = bar_item(
            "Workspace",
            vec![
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
            ],
        );
        let commands = collect(&root);
        assert_eq!(commands.len(), 3);
        assert_eq!(
            commands[0].path,
            vec![String::from("Workspace"), String::from("Media")]
        );
        assert_eq!(commands[1].enabled, Some(false));
        assert_eq!(commands[2].enabled, None);
    }

    #[test]
    fn open_fallback_argv_is_open_dash_b_bundle() {
        // Pins the route the hands lane verified live 2026-10-07: the
        // driver must spawn exactly this, never a bare `open` (which
        // reopens the bundle's default document instead of activating).
        assert_eq!(
            open_activate_argv("com.blackmagic-design.DaVinciResolve"),
            vec!["open", "-b", "com.blackmagic-design.DaVinciResolve"]
        );
    }

    #[test]
    fn verified_activation_trusts_frontmost_not_the_request() {
        // The defect: NSWorkspace answers pid-found while macOS 26
        // ignores the request, and the old caller reported
        // activated:true on that answer alone. A pid that never comes
        // forward must be an error naming both routes, never success -
        // and the `open -b` fallback fires once, not once per poll.
        use std::cell::Cell;
        let opens = Cell::new(0u32);
        let sleeps = Cell::new(0u32);
        let out = activate_verified(
            1234,
            RESOLVE_BUNDLE_ID,
            |_| {},
            || Some(4321),
            |_| {
                opens.set(opens.get() + 1);
                false
            },
            |_| {
                sleeps.set(sleeps.get() + 1);
            },
        );
        assert_eq!(opens.get(), 1);
        assert_eq!(sleeps.get(), ACTIVATION_TOTAL_ROUNDS);
        let err = out.unwrap_err();
        assert!(
            err.contains("1234") && err.contains("open -b"),
            "got: {err}"
        );
    }

    #[test]
    fn nsworkspace_path_wins_when_frontmost_already() {
        // No fallback, no settle sleep: the request landed, the read-back
        // says so, and nothing else runs.
        use std::cell::Cell;
        let opened = Cell::new(false);
        let sleeps = Cell::new(0u32);
        let out = activate_verified(
            1234,
            RESOLVE_BUNDLE_ID,
            |_| {},
            || Some(1234),
            |_| {
                opened.set(true);
                true
            },
            |_| {
                sleeps.set(sleeps.get() + 1);
            },
        );
        assert_eq!(out, Ok(Activation::ViaNsWorkspace));
        assert!(!opened.get());
        assert_eq!(sleeps.get(), 0);
    }

    #[test]
    fn open_fallback_fires_once_then_verifies() {
        // The request alone did not move focus within its head start;
        // `open -b` ran once with the pinned argv and the pid came
        // forward during its settle - credit ViaOpen.
        use std::cell::Cell;
        let polls = Cell::new(0u32);
        let opens = Cell::new(0u32);
        let seen_argv = Cell::new(Vec::new());
        let out = activate_verified(
            1234,
            RESOLVE_BUNDLE_ID,
            |_| {},
            || {
                polls.set(polls.get() + 1);
                if opens.get() > 0 && polls.get() >= ACTIVATION_NS_ROUNDS + 3 {
                    Some(1234)
                } else {
                    Some(4321)
                }
            },
            |argv: &[String]| {
                opens.set(opens.get() + 1);
                seen_argv.set(argv.to_vec());
                true
            },
            |_| {},
        );
        assert_eq!(out, Ok(Activation::ViaOpen));
        assert_eq!(opens.get(), 1);
        assert_eq!(
            seen_argv.take(),
            vec!["open", "-b", "com.blackmagic-design.DaVinciResolve"]
        );
    }

    #[test]
    fn a_late_nsworkspace_landing_credits_nsworkspace() {
        // The request needed a beat but no fallback: frontmost flips
        // inside the head start, `open -b` never runs, and a few
        // settle sleeps happened - the racy arrival the old
        // instant-check missed.
        use std::cell::Cell;
        let polls = Cell::new(0u32);
        let opens = Cell::new(0u32);
        let sleeps = Cell::new(0u32);
        let out = activate_verified(
            1234,
            RESOLVE_BUNDLE_ID,
            |_| {},
            || {
                polls.set(polls.get() + 1);
                if polls.get() >= 4 {
                    Some(1234)
                } else {
                    Some(4321)
                }
            },
            |_: &[String]| {
                opens.set(opens.get() + 1);
                true
            },
            |_| {
                sleeps.set(sleeps.get() + 1);
            },
        );
        assert_eq!(out, Ok(Activation::ViaNsWorkspace));
        assert_eq!(opens.get(), 0);
        assert_eq!(sleeps.get(), 3);
    }
    #[test]
    fn menu_hop_prefers_openers_and_leaf_prefers_runners() {
        // The action table the hop-walk runs: intermediates open
        // (AXPress, then AXPICK, then an opener), the leaf runs
        // (AXPress, then AXPICK, then AXConfirm). Priority follows
        // cua-driver's invoke_menu - the order the reference measures
        // as working. A leaf offering only AXPress still presses.
        let acts = |names: &[&str]| names.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            choose_menu_action(&acts(&["AXPress", "AXShowMenu"]), false),
            Some("AXPress")
        );
        assert_eq!(
            choose_menu_action(&acts(&["AXShowMenu", "AXOpen"]), false),
            Some("AXShowMenu")
        );
        assert_eq!(
            choose_menu_action(&acts(&["AXPress"]), true),
            Some("AXPress")
        );
        assert_eq!(
            choose_menu_action(&acts(&["AXPick", "AXConfirm"]), true),
            Some("AXPick")
        );
        assert_eq!(
            choose_menu_action(&acts(&["AXConfirm"]), true),
            Some("AXConfirm")
        );
    }

    #[test]
    fn menu_hop_with_no_usable_action_is_none() {
        // Neither hop nor leaf invents an action: an element offering
        // only AXCancel (or nothing) refuses as ActionNotSupported
        // instead of pressing blindly.
        let acts = |names: &[&str]| names.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(choose_menu_action(&acts(&["AXCancel"]), false), None);
        assert_eq!(choose_menu_action(&[], true), None);
        // AXConfirm runs a leaf but never opens a hop.
        assert_eq!(choose_menu_action(&acts(&["AXConfirm"]), false), None);
    }
}

extern "C" {
    fn nsworkspace_find_pid(bundle_id: *const u8) -> i64;
    fn nsworkspace_activate_pid(pid: i64) -> i32;
    fn nsworkspace_frontmost() -> *mut i8;
    fn nsworkspace_free(s: *mut i8);
}

pub fn find_resolve_pid() -> Option<u32> {
    find_pid(RESOLVE_BUNDLE_ID)
}

pub fn find_pid(bundle_id: &str) -> Option<u32> {
    unsafe {
        let bid = format!("{bundle_id}\0");
        let pid = nsworkspace_find_pid(bid.as_ptr());
        if pid > 0 {
            return Some(pid as u32);
        }
    }
    None
}

/// The pid of one running app by bundle id, or nothing when it is not
/// running. Measured on this Mac 2026-10-06: Resolve answers
/// `com.blackmagic-design.DaVinciResolve`, Chrome `com.google.Chrome`
/// (`defaults read ... CFBundleIdentifier`).
/// Resolve's bundle id: `find_resolve_pid` matches it, and the
/// `open -b` activation fallback targets it.
pub const RESOLVE_BUNDLE_ID: &str = "com.blackmagic-design.DaVinciResolve";

/// Frontmost poll steps: 250 ms apart. Finer than the old 500 ms so a
/// pid arriving just after a check is seen half as late.
pub const ACTIVATION_POLL_STEP_MS: u64 = 250;
/// NSWorkspace head start: 8 rounds (2 s) for the request to land
/// before escalating. The racy arrival measured live comes forward
/// seconds after the request, not minutes.
pub const ACTIVATION_NS_ROUNDS: u32 = 8;
/// Total poll budget: 40 rounds (10 s), the lane-measured settle
/// window. `open -b` fires once when the head start expires and the
/// same bound covers its settle - one bound for the whole attempt,
/// never an unbounded wait for focus.
pub const ACTIVATION_TOTAL_ROUNDS: u32 = 40;

/// How one activation reached the foreground.
#[derive(Debug, PartialEq)]
pub enum Activation {
    /// The NSWorkspace request landed: the pid was frontmost after it.
    ViaNsWorkspace,
    /// The `open -b` fallback brought it forward, verified by poll.
    ViaOpen,
}

/// argv for the `open -b` activation fallback: exactly what the hands
/// lane verified live 2026-10-07 after the NSWorkspace path proved
/// inert. Pinned by test, consumed by the driver verbatim.
pub fn open_activate_argv(bundle_id: &str) -> Vec<String> {
    vec!["open".to_string(), "-b".to_string(), bundle_id.to_string()]
}

/// Activate `pid` and verify the foreground instead of trusting the
/// request.
///
/// `nsworkspace_activate_pid` answers pid-found, not activated: on
/// macOS 26 the `activateWithOptions:` call is inert from a faceless
/// binary, so the old caller reported `{"activated": true}` while
/// focus never moved (measured live 2026-10-07 - four refused takes).
/// This asks, polls frontmost (the request gets a head start, then
/// `open -b` fires once and the same bound covers its settle), and a
/// pid that never comes forward is an error naming both routes, never
/// a success.
pub fn activate_verified(
    pid: u32,
    bundle_id: &str,
    request: impl Fn(u32),
    frontmost: impl Fn() -> Option<u32>,
    open: impl Fn(&[String]) -> bool,
    sleep_ms: impl Fn(u64),
) -> Result<Activation, String> {
    request(pid);
    let mut opened = false;
    for round in 0..ACTIVATION_TOTAL_ROUNDS {
        if frontmost() == Some(pid) {
            return Ok(if opened {
                Activation::ViaOpen
            } else {
                Activation::ViaNsWorkspace
            });
        }
        if round == ACTIVATION_NS_ROUNDS {
            let argv = open_activate_argv(bundle_id);
            opened = open(&argv);
        }
        sleep_ms(ACTIVATION_POLL_STEP_MS);
    }
    if frontmost() == Some(pid) {
        return Ok(if opened {
            Activation::ViaOpen
        } else {
            Activation::ViaNsWorkspace
        });
    }
    Err(format!(
        "activation failed: pid {pid} never became frontmost - neither the NSWorkspace request nor `open -b {bundle_id}` brought it forward"
    ))
}

pub unsafe fn activate_app_with_pid(pid: u32) -> bool {
    nsworkspace_activate_pid(pid as i64) != 0
}

/// One native menu action for a hop: intermediates open, the leaf runs.
/// Priority follows cua-driver's invoke_menu - the order the reference
/// measures as working, not a guess.
pub fn choose_menu_action(actions: &[String], final_hop: bool) -> Option<&'static str> {
    const OPENERS: &[&str] = &["AXPress", "AXPick", "AXShowMenu", "AXOpen"];
    const RUNNERS: &[&str] = &["AXPress", "AXPick", "AXConfirm"];
    let order = if final_hop { RUNNERS } else { OPENERS };
    order
        .iter()
        .copied()
        .find(|action| actions.iter().any(|have| have == action))
}

/// The frontmost application's pid.
pub fn frontmost_pid() -> Option<u32> {
    unsafe { get_frontmost_json() }?
        .get("pid")?
        .as_u64()
        .map(|p| p as u32)
}

unsafe fn get_frontmost_json() -> Option<serde_json::Value> {
    let json_str = nsworkspace_frontmost();
    if json_str.is_null() {
        return None;
    }
    let json_string = std::ffi::CStr::from_ptr(json_str)
        .to_string_lossy()
        .into_owned();
    nsworkspace_free(json_str);
    serde_json::from_str(&json_string).ok()
}

pub unsafe fn get_frontmost() -> Option<(String, String)> {
    let json_str = nsworkspace_frontmost();
    if json_str.is_null() {
        return None;
    }
    let cstr = std::ffi::CStr::from_ptr(json_str);
    let json_string = cstr.to_string_lossy().into_owned();
    nsworkspace_free(json_str);
    let json: serde_json::Value = serde_json::from_str(&json_string).ok()?;
    let bid = json.get("bundle_id")?.as_str()?.to_string();
    let name = json.get("name")?.as_str()?.to_string();
    Some((bid, name))
}
