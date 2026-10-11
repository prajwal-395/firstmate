use serde::{Deserialize, Serialize};

pub type Json = serde_json::Value;

#[derive(Debug, Serialize, Deserialize)]
pub struct Request {
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub params: Json,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Response {
    pub id: u64,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Json>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
}

impl ErrorBody {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        ErrorBody {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

pub fn success(id: u64, result: Json) -> Response {
    Response {
        id,
        ok: true,
        result: Some(result),
        error: None,
    }
}

pub fn failure(id: u64, code: &str, message: impl Into<String>) -> Response {
    Response {
        id,
        ok: false,
        result: None,
        error: Some(ErrorBody::new(code, message)),
    }
}

pub fn err_code(e: &DriverError) -> &'static str {
    match e {
        DriverError::ResolveNotRunning => "RESOLVE_NOT_RUNNING",
        DriverError::PermissionDenied(_) => "PERMISSION_DENIED",
        DriverError::ElementNotFound => "ELEMENT_NOT_FOUND",
        DriverError::ElementStale => "ELEMENT_STALE",
        DriverError::ActionNotSupported(_) => "ACTION_NOT_SUPPORTED",
        DriverError::InvalidParams(_) => "INVALID_PARAMS",
        DriverError::CaptureFailed(_) => "CAPTURE_FAILED",
        DriverError::OcrFailed(_) => "OCR_FAILED",
        DriverError::OutsideResolve => "OUTSIDE_RESOLVE",
        DriverError::ProjectNotQualification(_) => "PROJECT_NOT_QUALIFICATION",
        DriverError::ScreenLocked => "SCREEN_LOCKED",
        DriverError::ForegroundNotAllowed(_) => "FOREGROUND_NOT_ALLOWED",
        DriverError::TargetNotRunning(_) => "TARGET_NOT_RUNNING",
        DriverError::SkyLightUnavailable(_) => "SKYLIGHT_UNAVAILABLE",
        DriverError::AmbiguousCommand(_, _) => "AMBIGUOUS_COMMAND",
        DriverError::ActivationFailed(_) => "ACTIVATION_FAILED",
        DriverError::Internal(_) => "INTERNAL",
    }
}

#[derive(Debug)]
pub enum DriverError {
    ResolveNotRunning,
    PermissionDenied(String),
    ElementNotFound,
    ElementStale,
    ActionNotSupported(String),
    InvalidParams(String),
    CaptureFailed(String),
    OcrFailed(String),
    OutsideResolve,
    ProjectNotQualification(String),
    ScreenLocked,
    ForegroundNotAllowed(String),
    TargetNotRunning(String),
    SkyLightUnavailable(String),
    AmbiguousCommand(Vec<String>, usize),
    ActivationFailed(String),
    Internal(String),
}

impl DriverError {
    pub fn message(&self) -> String {
        match self {
            DriverError::ResolveNotRunning => "Resolve is not running".to_string(),
            DriverError::PermissionDenied(what) => {
                format!("permission denied: {}. Grant Accessibility and Screen Recording permission for this binary in System Settings > Privacy & Security.", what)
            }
            DriverError::ElementNotFound => "no element matched the search criteria".to_string(),
            DriverError::ElementStale => "element reference is no longer valid".to_string(),
            DriverError::ActionNotSupported(a) => format!("element does not support action: {}", a),
            DriverError::InvalidParams(m) => format!("invalid parameters: {}", m),
            DriverError::CaptureFailed(m) => format!("capture failed: {}", m),
            DriverError::OcrFailed(m) => format!("OCR failed: {}", m),
            DriverError::OutsideResolve => "refusing to act outside Resolve's windows".to_string(),
            DriverError::ProjectNotQualification(name) => format!(
                "refusing to act: the open Resolve project is {:?}, not the Ren Qualification project",
                name),
            DriverError::ScreenLocked => "the screen is locked: captures would be blank and input would reach the lock screen".to_string(),
            DriverError::ForegroundNotAllowed(m) => format!(
                "{} would take the captain's screen, cursor or keyboard; pass \"allow_foreground\": true to allow it", m),
            DriverError::TargetNotRunning(t) => format!(
                "the target app is not running: {}", t),
            DriverError::SkyLightUnavailable(m) => format!(
                "SkyLight background input unavailable: {}; pass \"allow_skylight\": true only when the driver reports it available", m),
        DriverError::AmbiguousCommand(path, n) => format!(
            "{} elements match the menu path {:?}; refine the path until it names one item", n, path),
        DriverError::ActivationFailed(m) => m.clone(),
            DriverError::Internal(m) => format!("internal error: {}", m),
        }
    }
}

pub type DriverResult<T> = Result<T, DriverError>;

pub fn get_param_str(params: &Json, key: &str) -> DriverResult<String> {
    params
        .get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            DriverError::InvalidParams(format!("missing or non-string parameter: {}", key))
        })
}

pub fn get_param_f64(params: &Json, key: &str) -> DriverResult<f64> {
    params.get(key).and_then(|v| v.as_f64()).ok_or_else(|| {
        DriverError::InvalidParams(format!("missing or non-numeric parameter: {}", key))
    })
}

pub fn get_param_u64(params: &Json, key: &str) -> DriverResult<u64> {
    params.get(key).and_then(|v| v.as_u64()).ok_or_else(|| {
        DriverError::InvalidParams(format!("missing or non-integer parameter: {}", key))
    })
}

pub fn frame_to_json(frame: &Frame) -> Json {
    serde_json::json!({
        "x": frame.x,
        "y": frame.y,
        "width": frame.width,
        "height": frame.height,
    })
}

#[derive(Debug, Clone, Copy)]
pub struct Frame {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Frame {
    /// Window-relative containment: input coordinates arrive relative
    /// to the window's own origin (the driver adds the frame offset
    /// when posting), so the check runs against (0,0)-(width,height).
    /// The old callers passed relative points to `contains` and only
    /// passed because Resolve's window sits at x=0 - a Chrome window at
    /// x=400 refused every click (measured 2026-10-06).
    pub fn contains_rel(&self, x: f64, y: f64) -> bool {
        x >= 0.0 && x <= self.width && y >= 0.0 && y <= self.height
    }
}
