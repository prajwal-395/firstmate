use crate::ax::{self, AxElement};
use crate::capture::{self, CapturedImage};
use crate::input;
use crate::ocr;
use crate::protocol::{
    self, DriverError, DriverResult, Frame, Json, Request, Response,
};
use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::time::{Duration, Instant};

/// The one Resolve project an action may touch: `QUALIFICATION_PROJECT` in
/// `library/tools/resolved/jobs.py`. Whatever else is open is the captain's.
const QUALIFICATION_PROJECT: &str = "Ren Qualification";

/// Methods that only look. Every other method changes Resolve, so it refuses
/// unless the open project reads back as `QUALIFICATION_PROJECT` at the
/// moment of the call. A whitelist, so a new action method is gated by default.
const READ_ONLY_METHODS: &[&str] = &[
    "activate_resolve", "get_windows", "get_accessibility_tree", "find_element",
    "list_commands",
    "capture_window", "capture_region", "wait_for", "read_text",
    "get_pointer_state", "get_frontmost_app",
    "debug_window_list", "debug_raw_window_list",
];

/// Methods that post CGEvents. By default each goes to Resolve's process
/// alone (`input::Route::Background`): the captain keeps his cursor and his
/// keyboard. With `"allow_foreground": true` it is posted at the HID tap
/// instead, which moves the real cursor and reaches whatever is frontmost.
const INPUT_EVENT_METHODS: &[&str] = &["click", "drag", "scroll", "key", "type_text"];

/// Methods that take the captain's screen, cursor or keyboard whatever their
/// parameters say: they run only when the caller passes `allow_foreground`.
const FOREGROUND_ONLY_METHODS: &[&str] = &["activate_resolve"];

/// Methods that need the screen's pixels or deliver input through it; a
/// locked screen would answer them with blank images or the lock screen.
const PIXEL_METHODS: &[&str] = &[
    "capture_window", "capture_region", "read_text", "wait_for",
    "click", "drag", "scroll", "key", "type_text",
];

/// Refuse a foreground-stealing call the caller did not explicitly allow, and
/// a foreground input event unless Resolve is frontmost (a HID-tap event
/// reaches the frontmost app and the topmost window under the point).
fn foreground_gate(
    method: &str,
    allow_foreground: bool,
    frontmost_pid: impl FnOnce() -> Option<u32>,
    resolve_pid: u32,
) -> DriverResult<()> {
    if FOREGROUND_ONLY_METHODS.contains(&method) && !allow_foreground {
        return Err(DriverError::ForegroundNotAllowed(method.to_string()));
    }
    if INPUT_EVENT_METHODS.contains(&method) && allow_foreground
        && frontmost_pid() != Some(resolve_pid)
    {
        return Err(DriverError::OutsideResolve);
    }
    Ok(())
}

fn allows_foreground(params: &Json) -> bool {
    params.get("allow_foreground").and_then(|v| v.as_bool()).unwrap_or(false)
}

/// Refuse `method` unless it only looks or the open project is the
/// qualification project. `press_role` is the AX role of a `press` target:
/// pressing a menu-bar item only opens its menu, so it is a read; pressing a
/// menu ITEM runs a command and is an action. `open_project` is called only
/// when the method needs the read-back.
fn action_gate(
    method: &str,
    press_role: Option<&str>,
    open_project: impl FnOnce() -> DriverResult<String>,
) -> DriverResult<()> {
    if READ_ONLY_METHODS.contains(&method)
        || (method == "press" && press_role == Some("AXMenuBarItem"))
    {
        return Ok(());
    }
    let name = open_project()?;
    if name == QUALIFICATION_PROJECT {
        Ok(())
    } else {
        Err(DriverError::ProjectNotQualification(name))
    }
}

pub struct Driver {
    resolve_pid: Option<u32>,
    element_refs: HashMap<String, AxElement>,
    ref_counter: u64,
    main_window_id: Option<u32>,
    main_window_frame: Option<Frame>,
    main_window_scale: f64,
}

impl Driver {
    pub fn new() -> Self {
        Driver {
            resolve_pid: None,
            element_refs: HashMap::new(),
            ref_counter: 0,
            main_window_id: None,
            main_window_frame: None,
            main_window_scale: 1.0,
        }
    }

    pub fn run(&mut self) -> io::Result<()> {
        let stdin = io::stdin();
        let stdout = io::stdout();
        let mut stdout_lock = stdout.lock();

        for line in stdin.lock().lines() {
            let line = line?;
            let trimmed = line.trim();
            if trimmed.is_empty() { continue; }

            let request: Request = match serde_json::from_str(trimmed) {
                Ok(r) => r,
                Err(e) => {
                    let resp = protocol::failure(0, "INVALID_PARAMS", format!("malformed JSON: {}", e));
                    writeln!(stdout_lock, "{}", serde_json::to_string(&resp).unwrap())?;
                    stdout_lock.flush()?;
                    continue;
                }
            };

            let response = self.dispatch(request);
            writeln!(stdout_lock, "{}", serde_json::to_string(&response).unwrap())?;
            stdout_lock.flush()?;
        }
        Ok(())
    }

    fn dispatch(&mut self, req: Request) -> Response {
        let result = self.handle(&req);
        match result {
            Ok(json) => protocol::success(req.id, json),
            Err(e) => protocol::failure(req.id, protocol::err_code(&e), e.message()),
        }
    }

    fn handle(&mut self, req: &Request) -> DriverResult<Json> {
        let press_role = if req.method == "press" {
            protocol::get_param_str(&req.params, "element")
                .ok()
                .and_then(|r| self.element_refs.get(&r))
                .and_then(|el| el.role())
        } else {
            None
        };
        action_gate(&req.method, press_role.as_deref(), || self.current_project_name())?;
        if PIXEL_METHODS.contains(&req.method.as_str()) && capture::screen_locked() {
            return Err(DriverError::ScreenLocked);
        }
        if INPUT_EVENT_METHODS.contains(&req.method.as_str())
            || FOREGROUND_ONLY_METHODS.contains(&req.method.as_str())
        {
            let pid = self.ensure_resolve()?;
            foreground_gate(&req.method, allows_foreground(&req.params), ax::frontmost_pid, pid)?;
        }
        match req.method.as_str() {
            "activate_resolve" => self.activate_resolve(),
            "get_windows" => self.get_windows(),
            "get_accessibility_tree" => self.get_accessibility_tree(&req.params),
            "find_element" => self.find_element(&req.params),
            "list_commands" => self.list_commands(&req.params),
            "run_command" => self.run_command(&req.params),
            "press" => self.press(&req.params),
            "set_value" => self.set_value(&req.params),
            "focus" => self.focus(&req.params),
            "type_text" => self.type_text(&req.params),
            "key" => self.key(&req.params),
            "click" => self.click(&req.params),
            "drag" => self.drag(&req.params),
            "scroll" => self.scroll(&req.params),
            "capture_window" => self.capture_window(&req.params),
            "capture_region" => self.capture_region(&req.params),
            "wait_for" => self.wait_for(&req.params),
            "read_text" => self.read_text(&req.params),
            "get_pointer_state" => self.get_pointer_state(),
            "get_frontmost_app" => self.get_frontmost_app(),
            "debug_window_list" => self.debug_window_list(),
            "debug_raw_window_list" => self.debug_raw_window_list(),
            _ => Err(DriverError::InvalidParams(format!("unknown method: {}", req.method))),
        }
    }

    fn ensure_resolve(&mut self) -> DriverResult<u32> {
        if let Some(pid) = self.resolve_pid { return Ok(pid); }
        let pid = ax::find_resolve_pid().ok_or(DriverError::ResolveNotRunning)?;
        self.resolve_pid = Some(pid);
        Ok(pid)
    }

    fn route(&mut self, params: &Json) -> DriverResult<input::Route> {
        if allows_foreground(params) {
            Ok(input::Route::Foreground)
        } else {
            let pid = self.ensure_resolve()? as i32;
            let (window_id, _, _) = self.main_window()?;
            Ok(input::Route::Background { pid, window_id })
        }
    }

    fn ensure_permissions(&self) -> DriverResult<()> {
        if !ax::ax_process_trusted() {
            return Err(DriverError::PermissionDenied(format!(
                "Accessibility permission not granted for {}",
                std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default(),
            )));
        }
        Ok(())
    }

    /// Window pixels and window titles (the project read-back) both need it.
    fn ensure_screen_recording(&self) -> DriverResult<()> {
        if !capture::screen_recording_granted() {
            return Err(DriverError::PermissionDenied(format!(
                "Screen Recording permission not granted for {}",
                std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_default(),
            )));
        }
        Ok(())
    }

    fn main_window(&mut self) -> DriverResult<(u32, Frame, f64)> {
        if let (Some(id), Some(frame), scale) = (self.main_window_id, self.main_window_frame, self.main_window_scale) {
            return Ok((id, frame, scale));
        }
        let _pid = self.ensure_resolve()?;
        let windows = capture::get_window_list();
        let resolve_windows: Vec<_> = windows
            .into_iter()
            .filter(|(_, _, owner, _, _)| owner.contains("Resolve") || owner.contains("DaVinci"))
            .collect();
        if resolve_windows.is_empty() {
            return Err(DriverError::ResolveNotRunning);
        }
        let (id, _, _, frame, scale) = resolve_windows
            .into_iter()
            .max_by_key(|(_, _, _, f, _)| (f.width * f.height) as i64)
            .unwrap();
        self.main_window_id = Some(id);
        self.main_window_frame = Some(frame);
        self.main_window_scale = scale;
        Ok((id, frame, scale))
    }

    /// The open project's name, read back from the main window's title,
    /// which Resolve sets to the project name. Read fresh on every call; an
    /// unreadable title (no Screen Recording permission) reads as "" and refuses.
    fn current_project_name(&mut self) -> DriverResult<String> {
        self.ensure_screen_recording()?;
        let _pid = self.ensure_resolve()?;
        let windows = capture::get_window_list();
        let resolve_windows: Vec<_> = windows
            .into_iter()
            .filter(|(_, _, owner, _, _)| owner.contains("Resolve") || owner.contains("DaVinci"))
            .collect();
        if resolve_windows.is_empty() {
            return Err(DriverError::ResolveNotRunning);
        }
        let (_, title, _, _, _) = resolve_windows
            .into_iter()
            .max_by_key(|(_, _, _, f, _)| (f.width * f.height) as i64)
            .unwrap();
        Ok(title)
    }

    fn activate_resolve(&mut self) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let pid = self.ensure_resolve()?;
        let activated = unsafe { ax::activate_app_with_pid(pid) };
        if activated {
            Ok(serde_json::json!({"activated": true, "pid": pid}))
        } else {
            Err(DriverError::ResolveNotRunning)
        }
    }

    fn get_windows(&mut self) -> DriverResult<Json> {
        self.ensure_resolve()?;
        let windows = capture::get_window_list();
        let resolve_windows: Vec<_> = windows
            .into_iter()
            .filter(|(_, _, owner, _, _)| owner.contains("Resolve") || owner.contains("DaVinci"))
            .collect();
        let main_id = resolve_windows
            .iter()
            .max_by_key(|(_, _, _, f, _)| (f.width * f.height) as i64)
            .map(|(id, _, _, _, _)| *id);
        let list: Vec<Json> = resolve_windows
            .iter()
            .map(|(id, name, owner, frame, scale)| {
                serde_json::json!({
                    "id": id,
                    "title": name,
                    "owner": owner,
                    "frame": protocol::frame_to_json(frame),
                    "backing_scale": scale,
                    "is_main": Some(*id) == main_id,
                })
            })
            .collect();
        Ok(serde_json::json!({"windows": list}))
    }

    fn get_accessibility_tree(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let pid = self.ensure_resolve()?;
        let max_depth = protocol::get_param_u64(params, "max_depth").unwrap_or(12) as usize;
        let app = AxElement::from_app(pid);
        let tree = self.dump_tree(&app, 0, max_depth)?;
        Ok(serde_json::json!({"tree": tree}))
    }

    fn dump_tree(&self, el: &AxElement, depth: usize, max_depth: usize) -> DriverResult<Json> {
        let mut node = serde_json::json!({});
        if let Some(role) = el.role() { node["role"] = serde_json::json!(role); }
        if let Some(title) = el.title() { node["title"] = serde_json::json!(title); }
        if let Some(desc) = el.description() { node["description"] = serde_json::json!(desc); }
        if let Some(value) = el.value() { node["value"] = serde_json::json!(value); }
        if let Some(frame) = el.frame() { node["frame"] = protocol::frame_to_json(&frame); }
        let actions = el.actions();
        if !actions.is_empty() { node["actions"] = serde_json::json!(actions); }
        if let Some(enabled) = el.enabled() { node["enabled"] = serde_json::json!(enabled); }
        if let Some(focused) = el.focused() { if focused { node["focused"] = serde_json::json!(true); } }

        if depth < max_depth {
            let children = el.children();
            if !children.is_empty() {
                let mut child_json = Vec::new();
                for child in &children {
                    child_json.push(self.dump_tree(child, depth + 1, max_depth)?);
                }
                node["children"] = serde_json::json!(child_json);
            }
        }
        Ok(node)
    }

    fn find_element(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let pid = self.ensure_resolve()?;
        let role_filter = params.get("role").and_then(|v| v.as_str());
        let title_filter = params.get("title").and_then(|v| v.as_str());
        let title_contains = params.get("title_contains").and_then(|v| v.as_str());
        let label_filter = params.get("label").and_then(|v| v.as_str());
        let value_filter = params.get("value").and_then(|v| v.as_str());
        let path_filter: Option<Vec<String>> = params.get("path").and_then(|v| v.as_array()).map(|a| {
            a.iter().filter_map(|t| t.as_str().map(str::to_string)).collect()
        });
        let filter = Filter {
            role: role_filter, title: title_filter, title_contains,
            label: label_filter, value: value_filter, path: path_filter.as_deref(),
        };

        let app = AxElement::from_app(pid);
        let mut results: Vec<(AxElement, Vec<String>)> = Vec::new();
        find_in_tree(&app, &filter, &mut Vec::new(), &mut results);

        let elements: Vec<Json> = results
            .into_iter()
            .map(|(el, path)| {
                self.ref_counter += 1;
                let ref_id = format!("ref-{}", self.ref_counter);
                let role = el.role();
                let title = el.title();
                let frame = el.frame();
                let actions = el.actions();
                let entry = serde_json::json!({
                    "ref": ref_id,
                    "role": role,
                    "title": title,
                    "frame": frame.map(|f| protocol::frame_to_json(&f)),
                    "actions": actions,
                    "path": path,
                });
                self.element_refs.insert(ref_id, el);
                entry
            })
            .collect();

        Ok(serde_json::json!({"elements": elements}))
    }

    fn list_commands(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let pid = self.ensure_resolve()?;
        let menu = params.get("menu").and_then(|v| v.as_str());
        let app = AxElement::from_app(pid);
        let root = ax::AxNode::from_element(&app);
        let mut path = Vec::new();
        let mut commands = Vec::new();
        ax::collect_commands(&root, &mut path, &mut commands);
        let listed: Vec<Json> = commands
            .iter()
            .filter(|c| menu.map_or(true, |m| c.path.first().is_some_and(|first| first == m)))
            .map(|c| {
                serde_json::json!({
                    "path": c.path,
                    "actions": c.actions,
                    "enabled": c.enabled,
                })
            })
            .collect();
        Ok(serde_json::json!({
            "count": listed.len(),
            "commands": listed,
        }))
    }

    fn run_command(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let path = command_path(params)?;
        let (leaf, ancestors) = path.split_last().unwrap();
        let pid = self.ensure_resolve()?;
        let app = AxElement::from_app(pid);
        let filter = Filter {
            role: Some("AXMenuItem"),
            title: Some(leaf),
            title_contains: None,
            label: None,
            value: None,
            path: Some(ancestors),
        };
        let mut results: Vec<(AxElement, Vec<String>)> = Vec::new();
        find_in_tree(&app, &filter, &mut Vec::new(), &mut results);
        match results.len() {
            0 => Err(DriverError::ElementNotFound),
            1 => {
                let (el, _) = results.remove(0);
                el.press()?;
                Ok(serde_json::json!({"pressed": true, "path": path}))
            }
            n => Err(DriverError::AmbiguousCommand(path, n)),
        }
    }

    fn press(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let element_ref = protocol::get_param_str(params, "element")?;
        let el = self.element_refs.get(&element_ref).ok_or(DriverError::ElementStale)?;
        el.press()?;
        Ok(serde_json::json!({"pressed": true}))
    }

    fn set_value(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let element_ref = protocol::get_param_str(params, "element")?;
        let value = protocol::get_param_str(params, "value")?;
        let el = self.element_refs.get(&element_ref).ok_or(DriverError::ElementStale)?;
        el.set_value(&value)?;
        Ok(serde_json::json!({"set": true}))
    }

    fn focus(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let element_ref = protocol::get_param_str(params, "element")?;
        let el = self.element_refs.get(&element_ref).ok_or(DriverError::ElementStale)?;
        el.set_focused()?;
        Ok(serde_json::json!({"focused": true}))
    }

    fn type_text(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let text = protocol::get_param_str(params, "text")?;
        let n = input::type_text(self.route(params)?, &text)?;
        Ok(serde_json::json!({"typed": n}))
    }

    fn key(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let key_name = protocol::get_param_str(params, "key")?;
        let modifiers: Vec<String> = params
            .get("modifiers")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
            .unwrap_or_default();
        input::key(self.route(params)?, &key_name, &modifiers)?;
        Ok(serde_json::json!({"sent": true}))
    }

    fn click(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let x = protocol::get_param_f64(params, "x")?;
        let y = protocol::get_param_f64(params, "y")?;
        let clicks = protocol::get_param_u64(params, "clicks").unwrap_or(1);
        let (_, frame, _) = self.main_window()?;
        if !frame.contains(x, y) { return Err(DriverError::OutsideResolve); }
        input::click(self.route(params)?, x, y, &frame, clicks as u32)?;
        Ok(serde_json::json!({"clicked": true}))
    }

    fn drag(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let sx = protocol::get_param_f64(params, "start_x")?;
        let sy = protocol::get_param_f64(params, "start_y")?;
        let ex = protocol::get_param_f64(params, "end_x")?;
        let ey = protocol::get_param_f64(params, "end_y")?;
        let duration = protocol::get_param_f64(params, "duration").unwrap_or(0.3);
        let (_, frame, _) = self.main_window()?;
        if !frame.contains(sx, sy) || !frame.contains(ex, ey) { return Err(DriverError::OutsideResolve); }
        input::drag(self.route(params)?, sx, sy, ex, ey, &frame, duration)?;
        Ok(serde_json::json!({"dragged": true}))
    }

    fn scroll(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let x = protocol::get_param_f64(params, "x")?;
        let y = protocol::get_param_f64(params, "y")?;
        let dx = protocol::get_param_f64(params, "delta_x").unwrap_or(0.0);
        let dy = protocol::get_param_f64(params, "delta_y").unwrap_or(0.0);
        let (_, frame, _) = self.main_window()?;
        if !frame.contains(x, y) { return Err(DriverError::OutsideResolve); }
        input::scroll(self.route(params)?, x, y, &frame, dx, dy)?;
        Ok(serde_json::json!({"scrolled": true}))
    }

    fn capture_window(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        self.ensure_screen_recording()?;
        let source = capture_source(params)?;
        let (window_id, frame, scale) = self.main_window()?;
        let img = capture::capture_window(window_id, &frame, scale, source)?;
        Ok(captured_to_json(&img))
    }

    fn capture_region(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        self.ensure_screen_recording()?;
        let x = protocol::get_param_f64(params, "x")?;
        let y = protocol::get_param_f64(params, "y")?;
        let w = protocol::get_param_f64(params, "width")?;
        let h = protocol::get_param_f64(params, "height")?;
        let source = capture_source(params)?;
        let (window_id, frame, scale) = self.main_window()?;
        let region = Frame { x, y, width: w, height: h };
        if x < 0.0 || y < 0.0 || w <= 0.0 || h <= 0.0
            || x + w > frame.width || y + h > frame.height
        {
            return Err(DriverError::OutsideResolve);
        }
        let img = capture::capture_region(window_id, &frame, scale, source, &region)?;
        Ok(captured_to_json(&img))
    }

    fn wait_for(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        let timeout = protocol::get_param_f64(params, "timeout").unwrap_or(10.0);
        let poll_interval = protocol::get_param_f64(params, "poll_interval").unwrap_or(0.5);
        let predicate = params.get("predicate").ok_or_else(|| DriverError::InvalidParams("missing predicate".into()))?;
        let start = Instant::now();
        let timeout_dur = Duration::from_secs_f64(timeout);
        let poll_dur = Duration::from_secs_f64(poll_interval);
        loop {
            if self.check_predicate(predicate)? {
                return Ok(serde_json::json!({"met": true, "elapsed": start.elapsed().as_secs_f64()}));
            }
            if start.elapsed() >= timeout_dur {
                return Ok(serde_json::json!({"met": false, "elapsed": start.elapsed().as_secs_f64()}));
            }
            std::thread::sleep(poll_dur);
        }
    }

    fn check_predicate(&mut self, predicate: &Json) -> DriverResult<bool> {
        let pred_type = predicate.get("type").and_then(|v| v.as_str())
            .ok_or_else(|| DriverError::InvalidParams("predicate missing type".into()))?;
        match pred_type {
            "seconds" => {
                let duration = predicate.get("duration").and_then(|v| v.as_f64()).unwrap_or(0.0);
                std::thread::sleep(Duration::from_secs_f64(duration));
                Ok(true)
            }
            "element_visible" => {
                let role = predicate.get("role").and_then(|v| v.as_str());
                let title = predicate.get("title").and_then(|v| v.as_str());
                let title_contains = predicate.get("title_contains").and_then(|v| v.as_str());
                let pid = self.ensure_resolve()?;
                let app = AxElement::from_app(pid);
                let mut found = false;
                self.check_element_visible(&app, role, title, title_contains, &mut found)?;
                Ok(found)
            }
            "text_visible" => {
                let text = predicate.get("text").and_then(|v| v.as_str())
                    .ok_or_else(|| DriverError::InvalidParams("text_visible predicate missing text".into()))?;
                let source = capture_source(predicate)?;
                let (window_id, frame, scale) = self.main_window()?;
                let cg_image = capture::window_image(window_id, scale, source)?;
                let (width, height) = unsafe { (capture::CGImageGetWidth(cg_image), capture::CGImageGetHeight(cg_image)) };
                let region = predicate.get("region").and_then(parse_frame);
                let result = ocr::ocr_image(cg_image, width, height, region.as_ref(), &frame);
                unsafe { capture::CFRelease(cg_image as *const std::ffi::c_void) };
                let result = result?;
                Ok(result.full_text.contains(text))
            }
            _ => Err(DriverError::InvalidParams(format!("unknown predicate type: {}", pred_type))),
        }
    }

    fn check_element_visible(&self, el: &AxElement, role: Option<&str>, title: Option<&str>, title_contains: Option<&str>, found: &mut bool) -> DriverResult<()> {
        if *found { return Ok(()); }
        let matches = (role.is_none() || el.role().as_deref() == role)
            && (title.is_none() || el.title().as_deref() == title)
            && (title_contains.is_none() || el.title().map(|t| t.contains(title_contains.unwrap())).unwrap_or(false));
        if matches { *found = true; return Ok(()); }
        for child in el.children() {
            self.check_element_visible(&child, role, title, title_contains, found)?;
            if *found { return Ok(()); }
        }
        Ok(())
    }

    fn read_text(&mut self, params: &Json) -> DriverResult<Json> {
        self.ensure_permissions()?;
        self.ensure_screen_recording()?;
        let source = capture_source(params)?;
        let (window_id, frame, scale) = self.main_window()?;
        let region = params.get("region").and_then(parse_frame);
        let cg_image = capture::window_image(window_id, scale, source)?;
        let (width, height) = unsafe { (capture::CGImageGetWidth(cg_image), capture::CGImageGetHeight(cg_image)) };
        let result = ocr::ocr_image(cg_image, width, height, region.as_ref(), &frame);
        unsafe { capture::CFRelease(cg_image as *const std::ffi::c_void) };
        let result = result?;
        let elements: Vec<Json> = result.elements.iter().map(|e| {
            serde_json::json!({
                "text": e.text,
                "confidence": e.confidence,
                "frame": protocol::frame_to_json(&e.frame),
            })
        }).collect();
        Ok(serde_json::json!({"text": result.full_text, "elements": elements}))
    }

    fn get_pointer_state(&mut self) -> DriverResult<Json> {
        let (x, y, button) = input::get_pointer_state()?;
        Ok(serde_json::json!({"x": x, "y": y, "button": button}))
    }

    fn get_frontmost_app(&mut self) -> DriverResult<Json> {
        let (bid, name) = unsafe { ax::get_frontmost() }
            .ok_or_else(|| DriverError::Internal("no frontmost application".into()))?;
        Ok(serde_json::json!({"bundle_id": bid, "name": name}))
    }

    fn debug_window_list(&mut self) -> DriverResult< Json> {
        let windows = capture::get_window_list();
        Ok(serde_json::json!({
            "count": windows.len(),
            "windows": windows.iter().map(|(id, title, owner, frame, scale)| {
                serde_json::json!({
                    "id": id,
                    "title": title,
                    "owner": owner,
                    "frame": protocol::frame_to_json(frame),
                    "scale": scale,
                })
            }).collect::<Vec<_>>(),
        }))
    }

    fn debug_raw_window_list(&mut self) -> DriverResult<Json> {
        let json_str = unsafe { capture::window_helper_list_raw() };
        if json_str.is_null() {
            return Ok(serde_json::json!({"raw": null}));
        }
        let cstr = unsafe { std::ffi::CStr::from_ptr(json_str) };
        let s = cstr.to_string_lossy().into_owned();
        unsafe { capture::window_helper_free_raw(json_str) };
        Ok(serde_json::json!({"raw": s}))
    }
}

/// The `source` a capture, OCR or wait names; ScreenCaptureKit by default.
fn capture_source(params: &Json) -> DriverResult<capture::Source> {
    capture::Source::parse(params.get("source").and_then(|v| v.as_str()))
}

/// A `{"x", "y", "width", "height"}` object, or None.
fn parse_frame(r: &Json) -> Option<Frame> {
    Some(Frame {
        x: r.get("x")?.as_f64()?,
        y: r.get("y")?.as_f64()?,
        width: r.get("width")?.as_f64()?,
        height: r.get("height")?.as_f64()?,
    })
}

/// The full menu path `run_command` takes: non-empty titles only.
fn command_path(params: &Json) -> DriverResult<Vec<String>> {
    let path: Vec<String> = params
        .get("path")
        .and_then(|v| v.as_array())
        .ok_or_else(|| DriverError::InvalidParams("run_command needs a path array of menu titles".into()))?
        .iter()
        .filter_map(|t| t.as_str().map(str::to_string))
        .collect();
    if path.is_empty() || path.iter().any(|t| t.is_empty()) {
        return Err(DriverError::InvalidParams("run_command path must be non-empty titles".into()));
    }
    Ok(path)
}

/// What `find_element` matches on. `path` is the titles of the menu bar item
/// and menu items above the element, outermost first: a title alone is
/// ambiguous (Resolve has "Color" under Mark > Keyframe Timeline Mode, View >
/// Bypass Color and Fusion, and Workspace > Switch to Page).
struct Filter<'a> {
    role: Option<&'a str>,
    title: Option<&'a str>,
    title_contains: Option<&'a str>,
    label: Option<&'a str>,
    value: Option<&'a str>,
    path: Option<&'a [String]>,
}

impl Filter<'_> {
    fn matches(&self, el: &AxElement, ancestors: &[String]) -> bool {
        node_matches(el.role().as_deref(), el.title().as_deref(), el.description().as_deref(), el.value().as_deref(), self, ancestors)
    }
}

/// The match itself, apart from where the attributes come from, so the path
/// semantics are testable without a live app.
fn node_matches(
    role: Option<&str>,
    title: Option<&str>,
    description: Option<&str>,
    value: Option<&str>,
    filter: &Filter,
    ancestors: &[String],
) -> bool {
    (filter.role.is_none() || role == filter.role)
        && (filter.title.is_none() || title == filter.title)
        && filter.title_contains.map_or(true, |t| title.is_some_and(|s| s.contains(t)))
        && (filter.label.is_none() || description == filter.label)
        && (filter.value.is_none() || value == filter.value)
        && filter.path.map_or(true, |p| p == ancestors)
}

/// Depth-first; each match comes with its menu path.
fn find_in_tree(el: &AxElement, filter: &Filter, ancestors: &mut Vec<String>, results: &mut Vec<(AxElement, Vec<String>)>) {
    if filter.matches(el, ancestors) {
        results.push((el.owned(), ancestors.clone()));
    }
    let titled = matches!(el.role().as_deref(), Some("AXMenuBarItem") | Some("AXMenuItem"));
    if titled {
        ancestors.push(el.title().unwrap_or_default());
    }
    for child in el.children() {
        find_in_tree(&child, filter, ancestors, results);
    }
    if titled {
        ancestors.pop();
    }
}

fn captured_to_json(img: &CapturedImage) -> Json {
    serde_json::json!({
        "image": img.png_base64,
        "width": img.width,
        "height": img.height,
        "scale": img.scale,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const ACTIONS: &[&str] = &["set_value", "focus", "type_text", "key", "click", "drag", "scroll"];

    #[test]
    fn every_action_refuses_in_the_captains_project() {
        for method in ACTIONS {
            let got = action_gate(method, None, || Ok("Podcast (field test)".to_string()));
            assert!(
                matches!(got, Err(DriverError::ProjectNotQualification(ref n)) if n == "Podcast (field test)"),
                "{method} was not refused"
            );
        }
    }

    #[test]
    fn pressing_a_menu_item_is_an_action_but_opening_a_menu_is_not() {
        let captains = || Ok("Podcast (field test)".to_string());
        assert!(action_gate("press", Some("AXMenuItem"), captains).is_err());
        assert!(action_gate("press", None, captains).is_err());
        assert!(action_gate("press", Some("AXMenuBarItem"), || panic!("read back for a read")).is_ok());
    }

    #[test]
    fn actions_run_in_the_qualification_project() {
        for method in ACTIONS.iter().chain(&["press"]) {
            assert!(action_gate(method, Some("AXMenuItem"), || Ok(QUALIFICATION_PROJECT.to_string())).is_ok());
        }
    }

    #[test]
    fn foreground_input_refuses_unless_resolve_is_frontmost() {
        for method in ["click", "drag", "scroll", "key", "type_text"] {
            assert!(matches!(foreground_gate(method, true, || Some(1234), 571), Err(DriverError::OutsideResolve)), "{method}");
            assert!(foreground_gate(method, true, || None, 571).is_err(), "{method}");
            assert!(foreground_gate(method, true, || Some(571), 571).is_ok(), "{method}");
        }
    }

    #[test]
    fn background_input_runs_while_the_captain_is_in_another_app() {
        for method in ["click", "drag", "scroll", "key", "type_text"] {
            assert!(foreground_gate(method, false, || Some(1234), 571).is_ok(), "{method}");
        }
    }

    #[test]
    fn activating_resolve_needs_explicit_permission() {
        assert!(matches!(
            foreground_gate("activate_resolve", false, || Some(571), 571),
            Err(DriverError::ForegroundNotAllowed(_))
        ));
        assert!(foreground_gate("activate_resolve", true, || Some(1234), 571).is_ok());
    }

    #[test]
    fn an_unreadable_project_name_refuses() {
        assert!(action_gate("click", None, || Ok(String::new())).is_err());
        assert!(matches!(
            action_gate("click", None, || Err(DriverError::ResolveNotRunning)),
            Err(DriverError::ResolveNotRunning)
        ));
    }

    #[test]
    fn list_commands_is_read_only_but_run_command_is_an_action() {
        let read = || Ok("Podcast (field test)".to_string());
        assert!(action_gate("list_commands", None, read).is_ok());
        assert!(action_gate("run_command", None, read).is_err());
        assert!(action_gate("run_command", None, || Ok(QUALIFICATION_PROJECT.to_string())).is_ok());
    }

    #[test]
    fn run_command_refuses_a_missing_or_empty_path() {
        for params in [
            serde_json::json!({}),
            serde_json::json!({"path": []}),
            serde_json::json!({"path": ["Workspace", ""]}),
            serde_json::json!({"path": "Workspace"}),
        ] {
            assert!(
                matches!(command_path(&params), Err(DriverError::InvalidParams(_))),
                "{params}"
            );
        }
        assert_eq!(
            command_path(&serde_json::json!({"path": ["Workspace", "Switch to Page", "Media"]})).unwrap(),
            vec!["Workspace", "Switch to Page", "Media"]
        );
    }

    #[test]
    fn a_menu_path_matches_only_its_own_chain() {
        let ws_switch = vec!["Workspace".to_string(), "Switch to Page".to_string()];
        let filter = Filter {
            role: Some("AXMenuItem"),
            title: Some("Media"),
            title_contains: None,
            label: None,
            value: None,
            path: Some(&ws_switch),
        };
        let media = |ancestors: &[String]| {
            node_matches(Some("AXMenuItem"), Some("Media"), None, None, &filter, ancestors)
        };
        assert!(media(&ws_switch));
        assert!(!media(&["View".to_string()]));
        assert!(!node_matches(Some("AXMenuItem"), Some("Color"), None, None, &filter, &ws_switch));
        assert!(!node_matches(Some("AXButton"), Some("Media"), None, None, &filter, &ws_switch));
    }
}
