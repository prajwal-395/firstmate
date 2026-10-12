// vision_helper.swift - Apple Vision person measurement (M3, the punch-in aim).
//
// One image list in, one JSON array out. Six requests per image: face
// rectangles (box, confidence, head-pose yaw/pitch/roll), face capture
// quality, face landmarks (2D, all regions), human body pose, human
// hand pose (up to 2 hands), person segmentation (coverage + bbox only).
//
// Usage: vision_helper --list <path-file>
//   <path-file> holds one image path per line. stdout is ONLY the JSON
//   array (one doc per input line, in order); diagnostics go to stderr.
//   The first image pays model load (cold); later images are warm, so the
//   caller batches many frames per process.
//
// Why a compiled helper instead of pyobjc (PR justification): no new pip
// dependencies, no import-time cost on machines without Apple silicon,
// and the call path is the same harness the eval measured (34 ms/frame
// warm for all five requests). The Python wrapper compiles this once per
// source revision into a temp cache and reports a named absence when the
// compile or the run fails, so non-macOS machines never notice it.
import Foundation
import Vision
import CoreGraphics
import ImageIO

func cgImage(at path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

func pt(_ p: CGPoint) -> [Double] {
    // Vision normalized coords are bottom-left origin; flip y to top-left
    // so boxes/points read like image pixels. Stated once, applied always.
    return [Double(p.x), Double(1.0 - p.y)]
}

func landmarkPoints(_ region: VNFaceLandmarkRegion2D?) -> [[Double]] {
    guard let region = region else { return [] }
    return region.normalizedPoints.map {
        pt(CGPoint(x: Double($0.x), y: Double($0.y)))
    }
}

// VNRecognizedPointKey has no stable string form across SDKs, and body
// joints arrive keyed by their own JointName type; either way the debug
// description wraps the real name as `Name(_rawValue: name)`. Unwrap that
// rather than hardcoding a joint list, so new joints survive SDK updates.
func shortKey(_ key: Any) -> String {
    let desc = String(describing: key)
    if let l = desc.range(of: "_rawValue: "),
       let r = desc.range(of: ")", options: .backwards) {
        return String(desc[l.upperBound..<r.lowerBound])
    }
    return desc
}

// A measured number, or null when Vision did not return one (an older
// OS leaves yaw/pitch/roll nil; a quality observation with no score is
// recorded, not dropped, so the Python pairing can still place its box).
func numOrNull(_ value: Double?) -> Any {
    value ?? NSNull()
}

func analyze(path: String) -> [String: Any] {
    var doc: [String: Any] = ["image": (path as NSString).lastPathComponent]
    guard let img = cgImage(at: path) else {
        doc["error"] = "unreadable"
        return doc
    }
    doc["pixels"] = [img.width, img.height]
    let handler = VNImageRequestHandler(cgImage: img, options: [:])

    do {
        let t0 = Date()
        let req = VNDetectFaceRectanglesRequest()
        try handler.perform([req])
        let faces: [VNFaceObservation] = req.results ?? []
        doc["face_ms"] = Date().timeIntervalSince(t0) * 1000.0
        doc["faces"] = faces.map { o -> [String: Any] in
            ["box": [o.boundingBox.minX, 1.0 - o.boundingBox.maxY,
                     o.boundingBox.maxX, 1.0 - o.boundingBox.minY],
             "confidence": Double(o.confidence),
             "pose": ["yaw": numOrNull(o.yaw?.doubleValue),
                      "pitch": numOrNull(o.pitch?.doubleValue),
                      "roll": numOrNull(o.roll?.doubleValue)]]
        }
    } catch { doc["face_error"] = "\(error)" }

    do {
        let t0 = Date()
        if #available(macOS 13.0, *) {
            let req = VNDetectFaceCaptureQualityRequest()
            try handler.perform([req])
            let faces: [VNFaceObservation] = req.results ?? []
            doc["quality_ms"] = Date().timeIntervalSince(t0) * 1000.0
            // Raw observations, in the request's own order: the caller
            // pairs them to face rectangles by box overlap (the two
            // requests observe independently, so counts can differ -
            // the same reason landmarks pair by overlap below).
            doc["face_quality"] = faces.map { o -> [String: Any] in
                ["box": [o.boundingBox.minX, 1.0 - o.boundingBox.maxY,
                         o.boundingBox.maxX, 1.0 - o.boundingBox.minY],
                 "capture_quality": numOrNull(
                     o.faceCaptureQuality.map(Double.init))]
            }
        } else {
            doc["quality_ms"] = Date().timeIntervalSince(t0) * 1000.0
            doc["face_quality"] = []
        }
    } catch { doc["quality_error"] = "\(error)" }

    do {
        let t0 = Date()
        let req = VNDetectFaceLandmarksRequest()
        try handler.perform([req])
        let faces: [VNFaceObservation] = req.results ?? []
        doc["landmark_ms"] = Date().timeIntervalSince(t0) * 1000.0
        doc["landmarks"] = faces.map { o -> [String: Any] in
            var d: [String: Any] = [
                "box": [o.boundingBox.minX, 1.0 - o.boundingBox.maxY,
                        o.boundingBox.maxX, 1.0 - o.boundingBox.minY],
            ]
            if let lm = o.landmarks {
                d["outerLips"] = landmarkPoints(lm.outerLips)
                d["innerLips"] = landmarkPoints(lm.innerLips)
                d["leftEye"] = landmarkPoints(lm.leftEye)
                d["rightEye"] = landmarkPoints(lm.rightEye)
                d["nose"] = landmarkPoints(lm.nose)
                d["faceContour"] = landmarkPoints(lm.faceContour)
            }
            return d
        }
    } catch { doc["landmark_error"] = "\(error)" }

    do {
        let t0 = Date()
        let req = VNDetectHumanBodyPoseRequest()
        try handler.perform([req])
        let people: [VNHumanBodyPoseObservation] = req.results ?? []
        doc["body_ms"] = Date().timeIntervalSince(t0) * 1000.0
        var out: [[String: Any]] = []
        for p in people {
            do {
                let joints = try p.recognizedPoints(.all)
                var jd: [String: Any] = [:]
                for (name, point) in joints {
                    jd[shortKey(name)] = [Double(point.location.x),
                                          Double(1.0 - point.location.y),
                                          Double(point.confidence)]
                }
                out.append(["joints": jd])
            } catch { out.append(["error": "\(error)"]) }
        }
        doc["bodies"] = out
    } catch { doc["body_error"] = "\(error)" }

    do {
        let t0 = Date()
        let req = VNDetectHumanHandPoseRequest()
        req.maximumHandCount = 2
        try handler.perform([req])
        let hands: [VNHumanHandPoseObservation] = req.results ?? []
        doc["hand_ms"] = Date().timeIntervalSince(t0) * 1000.0
        var out: [[String: Any]] = []
        for h in hands {
            do {
                let joints = try h.recognizedPoints(.all)
                var jd: [String: Any] = [:]
                for (name, point) in joints {
                    jd[shortKey(name)] = [Double(point.location.x),
                                          Double(1.0 - point.location.y),
                                          Double(point.confidence)]
                }
                out.append(["chirality": h.chirality == .left ? "left"
                    : (h.chirality == .right ? "right" : "unknown"),
                            "joints": jd,
                            "confidence": Double(h.confidence)])
            } catch { out.append(["error": "\(error)"]) }
        }
        doc["hands"] = out
    } catch { doc["hand_error"] = "\(error)" }

    do {
        let t0 = Date()
        let req = VNGeneratePersonSegmentationRequest()
        req.qualityLevel = .balanced
        try handler.perform([req])
        doc["seg_ms"] = Date().timeIntervalSince(t0) * 1000.0
        let segResults: [VNPixelBufferObservation] = req.results ?? []
        if let obs = segResults.first {
            let buf = obs.pixelBuffer
            CVPixelBufferLockBaseAddress(buf, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
            let w = CVPixelBufferGetWidth(buf), h = CVPixelBufferGetHeight(buf)
            let stride = CVPixelBufferGetBytesPerRow(buf)
            let base = CVPixelBufferGetBaseAddress(buf)!
                .assumingMemoryBound(to: UInt8.self)
            var count = 0, x0 = w, y0 = h, x1 = -1, y1 = -1
            for y in 0..<h {
                let row = base.advanced(by: y * stride)
                for x in 0..<w {
                    if row[x] > 127 {
                        count += 1
                        if x < x0 { x0 = x }; if x > x1 { x1 = x }
                        if y < y0 { y0 = y }; if y > y1 { y1 = y }
                    }
                }
            }
            let total = w * h
            var d: [String: Any] = ["coverage": Double(count) / Double(total),
                                    "mask_pixels": [w, h]]
            if count > 0 {
                d["bbox"] = [Double(x0) / Double(w), Double(y0) / Double(h),
                             Double(x1 + 1) / Double(w),
                             Double(y1 + 1) / Double(h)]
            }
            doc["segmentation"] = d
        }
    } catch { doc["seg_error"] = "\(error)" }

    return doc
}

var listPath: String?
var args = CommandLine.arguments.dropFirst()
while let arg = args.first {
    if arg == "--list", args.count >= 2 {
        args = args.dropFirst()
        listPath = args.first
    }
    args = args.dropFirst()
}
guard let listPath = listPath,
      let listText = try? String(contentsOfFile: listPath, encoding: .utf8)
else {
    fputs("usage: vision_helper --list <path-file>\n", stderr)
    exit(2)
}
let paths = listText.components(separatedBy: "\n").filter { !$0.isEmpty }
if paths.isEmpty {
    FileHandle.standardOutput.write(Data("[]\n".utf8))
    exit(0)
}
let docs = paths.map(analyze)
let data = try JSONSerialization.data(withJSONObject: docs, options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
