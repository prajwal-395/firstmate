import AppKit
import Foundation
import Vision

struct Detection: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}

struct Frame: Codable {
    let id: String
    let predictions: [Detection]
    let latencySeconds: Double
}

let paths = CommandLine.arguments.dropFirst()
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]
let coldStart = Date()
var frames: [Frame] = []
var firstRequestSeconds: Double?

for path in paths {
    let image = NSImage(contentsOfFile: path)!
    var rect = CGRect(origin: .zero, size: image.size)
    let cgImage = image.cgImage(forProposedRect: &rect, context: nil,
                                hints: nil)!
    let start = Date()
    try VNImageRequestHandler(cgImage: cgImage).perform([request])
    let elapsed = Date().timeIntervalSince(start)
    if firstRequestSeconds == nil { firstRequestSeconds = elapsed }
    let predictions = (request.results ?? []).compactMap { observation -> Detection? in
        guard let text = observation.topCandidates(1).first?.string else {
            return nil
        }
        let box = observation.boundingBox
        return Detection(text: text,
                         confidence: observation.topCandidates(1).first!.confidence,
                         bbox: [Double(box.minX), Double(1 - box.maxY),
                                Double(box.width), Double(box.height)])
    }
    frames.append(Frame(id: URL(fileURLWithPath: path).lastPathComponent,
                        predictions: predictions,
                        latencySeconds: elapsed))
}

let result: [String: Any] = [
    "candidate": "Apple Vision OCR",
    "revision": ProcessInfo.processInfo.operatingSystemVersionString,
    "precision": "Apple Vision framework default",
    "license_class": "OS-provided framework",
    "cold_inference_seconds": firstRequestSeconds ?? 0,
    "warm_inference_seconds_by_frame": frames.dropFirst().map(\.latencySeconds),
    "total_inference_seconds": Date().timeIntervalSince(coldStart),
    "frames": try JSONSerialization.jsonObject(
        with: JSONEncoder().encode(frames)),
]
let output = try JSONSerialization.data(withJSONObject: result,
                                        options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(output)
FileHandle.standardOutput.write(Data([0x0a]))
