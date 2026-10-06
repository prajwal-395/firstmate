use crate::protocol::{DriverError, DriverResult, Frame};
use std::ffi::c_void;

extern "C" {
    fn vision_ocr_image(
        image: *mut c_void,
        region_x: f64,
        region_y: f64,
        region_w: f64,
        region_h: f64,
        has_region: i32,
    ) -> *mut i8;
    fn vision_ocr_free(s: *mut i8);
}

pub struct OcrElement {
    pub text: String,
    pub confidence: f64,
    pub frame: Frame,
}

pub struct OcrResult {
    pub full_text: String,
    pub elements: Vec<OcrElement>,
}

pub fn ocr_image(
    cg_image: *mut c_void,
    width: usize,
    height: usize,
    region: Option<&Frame>,
    window_frame: &Frame,
) -> DriverResult<OcrResult> {
    // Vision answers in image pixels; the protocol speaks window points.
    let scale = pixels_per_point(width, window_frame);
    let (rx, ry, rw, rh, has_region) = match region {
        Some(r) => (r.x * scale, r.y * scale, r.width * scale, r.height * scale, 1),
        None => (0.0, 0.0, 0.0, 0.0, 0),
    };

    unsafe {
        let json_str = vision_ocr_image(cg_image, rx, ry, rw, rh, has_region);
        if json_str.is_null() {
            return Err(DriverError::OcrFailed("vision_ocr_image returned null".into()));
        }
        let cstr = std::ffi::CStr::from_ptr(json_str);
        let json_string = cstr.to_string_lossy().into_owned();
        vision_ocr_free(json_str);

        let json: serde_json::Value = serde_json::from_str(&json_string)
            .map_err(|e| DriverError::OcrFailed(format!("failed to parse OCR JSON: {}", e)))?;

        let mut elements = Vec::new();
        let mut full_text = String::new();

        if let Some(arr) = json.as_array() {
            for item in arr {
                let text = item.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let confidence = item.get("confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let frame = item.get("frame").and_then(|f| {
                    Some(Frame {
                        x: f.get("x")?.as_f64()? / scale,
                        y: f.get("y")?.as_f64()? / scale,
                        width: f.get("width")?.as_f64()? / scale,
                        height: f.get("height")?.as_f64()? / scale,
                    })
                }).unwrap_or(Frame { x: 0.0, y: 0.0, width: 0.0, height: 0.0 });

                if !full_text.is_empty() {
                    full_text.push('\n');
                }
                full_text.push_str(&text);

                elements.push(OcrElement { text, confidence, frame });
            }
        }

        Ok(OcrResult { full_text, elements })
    }
}

/// Pixels per point of a capture of `window_frame` that came back `width`
/// pixels wide: the backing scale the capture was actually taken at.
pub fn pixels_per_point(width: usize, window_frame: &Frame) -> f64 {
    if window_frame.width > 0.0 && width > 0 {
        width as f64 / window_frame.width
    } else {
        1.0
    }
}
