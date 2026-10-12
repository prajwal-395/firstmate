// attention_saliency_helper.swift - Apple Vision attention saliency for M3e.
//
// One image list in, one JSON array out. One request per image:
// VNGenerateAttentionBasedSaliencyImageRequest - where a viewer's eye
// goes in the frame. Ren keeps the salient-object BOXES the request
// returns beside its heatmap; the 68x68 heatmap itself is not emitted
// (no reader needs it yet - `library/tools/attention_saliency.py`).
//
// Usage: attention_saliency_helper --list <path-file>
//   <path-file> holds one image path per line. stdout is ONLY the JSON
//   array (one doc per input line, in order); diagnostics go to stderr.
//   The first image pays model load (cold); later images are warm, so the
//   caller batches many frames per process.
//
// Per image: `pixels` [w, h], `ms`, and `boxes`: one entry per salient
// object with `bbox` [x0, y0, x1, y1] image-normalised with a TOP-LEFT
// origin (Vision's own boxes are bottom-left; flipped here, once) and
// `confidence`. A frame with nothing salient has `boxes: []`, never an
// absent key.
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

func round4(_ v: CGFloat) -> Double {
    return (Double(v) * 10000.0).rounded() / 10000.0
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
        let req = VNGenerateAttentionBasedSaliencyImageRequest()
        try handler.perform([req])
        var boxes: [[String: Any]] = []
        if let obs = req.results?.first {
            for object in obs.salientObjects ?? [] {
                let b = object.boundingBox
                boxes.append([
                    "bbox": [round4(b.minX), round4(1.0 - b.maxY),
                             round4(b.maxX), round4(1.0 - b.minY)],
                    "confidence": (Double(object.confidence) * 1000.0)
                        .rounded() / 1000.0,
                ])
            }
        }
        doc["boxes"] = boxes
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
    fputs("usage: attention_saliency_helper --list <path-file>\n", stderr)
    exit(2)
}
let paths = listText.components(separatedBy: "\n").filter { !$0.isEmpty }
let docs = paths.map { analyze(path: $0) }
let data = try JSONSerialization.data(withJSONObject: docs, options: [])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
