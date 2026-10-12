// person_regions_helper.swift - Apple Vision person instance masks for M3d.
//
// One image list in, one JSON array out. One request per image:
// VNGeneratePersonInstanceMaskRequest (macOS 14+), each instance's mask
// scaled to the input image (`generateScaledMaskForImage`) and then
// resampled onto the caller's `--grid` - M3d stores every mask on the
// source's M2 thumbnail grid, whatever resolution the model was fed
// (`library/tools/person_regions.py` says why the two differ).
//
// Usage: person_regions_helper --list <path-file> --grid <w> <h>
//   <path-file> holds one image path per line. stdout is ONLY the JSON
//   array (one doc per input line, in order); diagnostics go to stderr.
//   The first image pays model load (cold); later images are warm, so the
//   caller batches many frames per process.
//
// Per image: `pixels` [w, h], `ms`, `confidence` (the observation's), and
// `instances`: one entry per person instance with `index` (Vision's own
// per-frame label, NOT an identity) and `runs` - alternating run lengths
// over the row-major grid, background first (codec `runs-rowmajor-v1`,
// `library/tools/person_regions.py`). A frame with no person has
// `instances: []`, never an absent key.
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

// The mask resampled onto a `gw` x `gh` grid - each cell the mean of the
// input pixels it covers, "in" when that mean exceeds 0.5 - as alternating
// run lengths over the row-major grid, background first. Two instances
// are disjoint in Vision's label map, so no cell can exceed 0.5 for both.
// The scaled mask is one-component Float32 or 8-bit depending on the SDK,
// so the format is read rather than assumed; either way, 0..1.
func gridRuns(_ buf: CVPixelBuffer, _ gw: Int, _ gh: Int) -> [Int] {
    CVPixelBufferLockBaseAddress(buf, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buf, .readOnly) }
    let w = CVPixelBufferGetWidth(buf), h = CVPixelBufferGetHeight(buf)
    let stride = CVPixelBufferGetBytesPerRow(buf)
    let base = CVPixelBufferGetBaseAddress(buf)!
    let isFloat = CVPixelBufferGetPixelFormatType(buf)
        == kCVPixelFormatType_OneComponent32Float
    let column = (0..<w).map { min(gw - 1, $0 * gw / w) }
    var sums = [Float](repeating: 0, count: gw * gh)
    var counts = [Int32](repeating: 0, count: gw * gh)
    for y in 0..<h {
        let row = base.advanced(by: y * stride)
        let first = min(gh - 1, y * gh / h) * gw
        if isFloat {
            let px = row.assumingMemoryBound(to: Float32.self)
            for x in 0..<w {
                sums[first + column[x]] += px[x]
                counts[first + column[x]] += 1
            }
        } else {
            let px = row.assumingMemoryBound(to: UInt8.self)
            for x in 0..<w {
                sums[first + column[x]] += Float(px[x]) / 255.0
                counts[first + column[x]] += 1
            }
        }
    }
    var out: [Int] = []
    var current = false
    var length = 0
    for cell in 0..<(gw * gh) {
        let on = counts[cell] > 0 && sums[cell] > 0.5 * Float(counts[cell])
        if on == current {
            length += 1
        } else {
            out.append(length)
            current = on
            length = 1
        }
    }
    out.append(length)
    return out
}

func analyze(path: String, grid: (Int, Int)) -> [String: Any] {
    var doc: [String: Any] = ["image": (path as NSString).lastPathComponent]
    guard let img = cgImage(at: path) else {
        doc["error"] = "unreadable"
        return doc
    }
    doc["pixels"] = [img.width, img.height]
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    do {
        let t0 = Date()
        let req = VNGeneratePersonInstanceMaskRequest()
        try handler.perform([req])
        var instances: [[String: Any]] = []
        if let obs = req.results?.first {
            doc["confidence"] = Double(obs.confidence)
            for index in obs.allInstances {
                let mask = try obs.generateScaledMaskForImage(
                    forInstances: IndexSet(integer: index), from: handler)
                instances.append(["index": index,
                                  "runs": gridRuns(mask, grid.0, grid.1)])
            }
        }
        doc["instances"] = instances
        doc["ms"] = Date().timeIntervalSince(t0) * 1000.0
    } catch { doc["error"] = "\(error)" }
    return doc
}

var listPath: String?
var gridW = 0, gridH = 0
var args = Array(CommandLine.arguments.dropFirst())
var i = 0
while i < args.count {
    if args[i] == "--list", i + 1 < args.count {
        listPath = args[i + 1]; i += 1
    } else if args[i] == "--grid", i + 2 < args.count {
        gridW = Int(args[i + 1]) ?? 0; gridH = Int(args[i + 2]) ?? 0; i += 2
    }
    i += 1
}
guard let listPath = listPath, gridW > 0, gridH > 0,
      let listText = try? String(contentsOfFile: listPath, encoding: .utf8)
else {
    fputs("usage: person_regions_helper --list <path-file> --grid <w> <h>\n",
          stderr)
    exit(2)
}
let paths = listText.components(separatedBy: "\n").filter { !$0.isEmpty }
let docs = paths.map { analyze(path: $0, grid: (gridW, gridH)) }
let data = try JSONSerialization.data(withJSONObject: docs, options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
