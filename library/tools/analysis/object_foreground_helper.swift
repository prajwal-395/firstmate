// object_foreground_helper.swift - Apple Vision class-agnostic foreground
// instances, the MU-15 detector bakeoff's baseline.
//
// One image list in, one JSON array out. One request per image:
// VNGenerateForegroundInstanceMaskRequest (macOS 14+). The bakeoff
// scores boxes, not masks, so each instance answers its normalised
// top-left bounding box plus its area share - the same box vocabulary
// the open-vocabulary detectors speak, with no phrase attached. The
// box is read off the instance's own scaled mask (min/max of the
// foreground pixels), never assumed from an SDK geometry API.
//
// Usage: object_foreground_helper --list <path-file>
//   <path-file> holds one image path per line. stdout is ONLY the JSON
//   array (one doc per input line, in order); diagnostics go to stderr.
//   The first image pays model load (cold); later images are warm, so the
//   caller batches many frames per process.
//
// Per image: `pixels` [w, h], `ms`, and `instances`: one entry per
// foreground instance with `index` (Vision's own per-frame label, NOT
// an identity), `box` [x0, y0, x1, y1] normalised top-left, and `area`
// (share of the frame). A frame with no foreground instance has
// `instances: [], never an absent key.
import Foundation
import Vision
import CoreGraphics
import CoreVideo
import ImageIO

func cgImage(at path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

// Pixel "in" test over the scaled mask's own format: the buffer is
// one-component Float32 or 8-bit depending on the SDK, so the format is
// read rather than assumed (the same read the person-regions helper
// does); either way the value is 0..1 and 0.5 decides.
func foregroundBox(_ buf: CVPixelBuffer) -> (box: [Double], area: Double)? {
    CVPixelBufferLockBaseAddress(buf, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
    let w = CVPixelBufferGetWidth(buf), h = CVPixelBufferGetHeight(buf)
    let stride = CVPixelBufferGetBytesPerRow(buf)
    let base = CVPixelBufferGetBaseAddress(buf)!
    let isFloat = CVPixelBufferGetPixelFormatType(buf)
        == kCVPixelFormatType_OneComponent32Float
    var x0 = w, y0 = h, x1 = -1, y1 = -1, count = 0
    for y in 0..<h {
        let row = base.advanced(by: y * stride)
        if isFloat {
            let px = row.assumingMemoryBound(to: Float32.self)
            for x in 0..<w where px[x] > 0.5 {
                if x < x0 { x0 = x }
                if x > x1 { x1 = x }
                if y < y0 { y0 = y }
                if y > y1 { y1 = y }
                count += 1
            }
        } else {
            let px = row.assumingMemoryBound(to: UInt8.self)
            for x in 0..<w where px[x] > 127 {
                if x < x0 { x0 = x }
                if x > x1 { x1 = x }
                if y < y0 { y0 = y }
                if y > y1 { y1 = y }
                count += 1
            }
        }
    }
    guard count > 0 else { return nil }
    let dw = Double(w), dh = Double(h)
    return ([Double(x0) / dw, Double(y0) / dh,
             Double(x1 + 1) / dw, Double(y1 + 1) / dh],
            Double(count) / Double(w * h))
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
        let req = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([req])
        var instances: [[String: Any]] = []
        if let obs = req.results?.first {
            for index in obs.allInstances {
                let mask = try obs.generateScaledMaskForImage(
                    forInstances: IndexSet(integer: index), from: handler)
                if let found = foregroundBox(mask) {
                    instances.append(["index": index,
                                      "box": found.box,
                                      "area": found.area])
                }
            }
        }
        doc["instances"] = instances
        doc["ms"] = Date().timeIntervalSince(t0) * 1000.0
    } catch { doc["error"] = "\(error)" }
    return doc
}

var listPath: String?
var args = Array(CommandLine.arguments.dropFirst())
var i = 0
while i < args.count {
    if args[i] == "--list", i + 1 < args.count {
        listPath = args[i + 1]; i += 1
    }
    i += 1
}
guard let listPath = listPath,
      let listText = try? String(contentsOfFile: listPath, encoding: .utf8)
else {
    fputs("usage: object_foreground_helper --list <path-file>\n", stderr)
    exit(2)
}
let paths = listText.components(separatedBy: "\n").filter { !$0.isEmpty }
let docs = paths.map { analyze(path: $0) }
let data = try JSONSerialization.data(withJSONObject: docs, options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
