// caption_ocr_helper.swift - Apple Vision text reading for DELIVERED captions.
//
// One image list in, one JSON array out: one doc per input line, in order.
// A doc carries every text observation Vision found, with its box in
// NORMALISED frame coordinates (x, y, width, height, top-left origin).
//
// Why a compiled helper instead of pyobjc: no new pip dependencies, no
// import-time cost on machines without Apple silicon, and the same call
// path the OCR benchmark measured (docs/benchmarks/apple_vision_ocr.swift).
// The Python wrapper compiles this once per source revision into a temp
// cache (library/tools/vision_measure.py) and reports a named absence
// when the compile or the run fails, so non-macOS machines never notice it.
//
// LANGUAGE CORRECTION IS OFF, ON PURPOSE. Vision's corrector rewrites
// what it read into what it thinks was meant, and a caption check that
// compares the delivered pixels against the intended WORDS must read
// the pixels as drawn. A corrector turns a delivered typo into a
// passing check, which is the one thing this helper must never do.
//
// Usage: caption_ocr_helper --list <path-file>
//   stdout is ONLY the JSON array; diagnostics go to stderr.
import Foundation
import Vision
import CoreGraphics
import ImageIO

struct Observation: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct Doc: Codable {
    let id: String
    let texts: [Observation]
}

func cgImage(at path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

var listPath: String?
var args = CommandLine.arguments.dropFirst()
while let arg = args.first {
    args = args.dropFirst()
    if arg == "--list", let next = args.first {
        listPath = next
        args = args.dropFirst()
    }
}
guard let listPath = listPath,
      let listText = try? String(contentsOfFile: listPath, encoding: .utf8)
else {
    fputs("usage: caption_ocr_helper --list <path-file>\n", stderr)
    exit(2)
}
let paths = listText.components(separatedBy: "\n").filter { !$0.isEmpty }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]

var docs: [Doc] = []
for path in paths {
    var texts: [Observation] = []
    if let image = cgImage(at: path) {
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try? handler.perform([request])
        for observation in (request.results ?? []) {
            guard let candidate = observation.topCandidates(1).first else {
                continue
            }
            let box = observation.boundingBox
            texts.append(Observation(
                text: candidate.string,
                confidence: candidate.confidence,
                // Vision's origin is bottom-left; flip to top-left so the
                // box reads like image pixels.
                bbox: [Double(box.minX), Double(1 - box.maxY),
                       Double(box.width), Double(box.height)]))
        }
    }
    docs.append(Doc(id: URL(fileURLWithPath: path).lastPathComponent,
                    texts: texts))
}

let output = try JSONEncoder().encode(docs)
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data([0x0a]))
