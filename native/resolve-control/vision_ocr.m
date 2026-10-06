// vision_ocr.m - Vision OCR helper for resolve-control.
// One CGImage in, one JSON array out. Frames are image pixel coordinates,
// top-left origin (Vision's normalized boxes are bottom-left origin).
#import "vision_ocr.h"
#import <objc/runtime.h>

static NSString *json_escape(NSString *s) {
    NSMutableString *out = [s mutableCopy];
    [out replaceOccurrencesOfString:@"\\" withString:@"\\\\" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\"" withString:@"\\\"" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\n" withString:@"\\n" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\r" withString:@"\\r" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\t" withString:@"\\t" options:0 range:NSMakeRange(0, out.length)];
    return out;
}

char *vision_ocr_image(CGImageRef image, double region_x, double region_y,
                       double region_w, double region_h, int has_region) {
    if (!image) { return strdup("[]"); }

    NSError *error = nil;
    VNImageRequestHandler *handler =
        [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
    if (!handler) { return strdup("[]"); }

    VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
    req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    req.usesLanguageCorrection = YES;

    if (![handler performRequests:@[req] error:&error]) {
        return strdup("[]");
    }

    NSArray<VNRecognizedTextObservation *> *observations = req.results;
    if (!observations || observations.count == 0) { return strdup("[]"); }

    size_t width = CGImageGetWidth(image);
    size_t height = CGImageGetHeight(image);

    NSMutableString *json = [NSMutableString stringWithString:@"["];
    BOOL first = YES;

    for (VNRecognizedTextObservation *obs in observations) {
        VNRecognizedText *candidate = [obs topCandidates:1].firstObject;
        if (!candidate) { continue; }

        CGRect box = obs.boundingBox;
        // Vision normalized coords: bottom-left origin, y up.
        // Convert to image pixels: top-left origin, y down.
        double x = box.origin.x * (double)width;
        double y = (1.0 - box.origin.y - box.size.height) * (double)height;
        double w = box.size.width * (double)width;
        double h = box.size.height * (double)height;

        if (has_region) {
            double cx = x + w / 2.0;
            double cy = y + h / 2.0;
            if (cx < region_x || cx > region_x + region_w ||
                cy < region_y || cy > region_y + region_h) {
                continue;
            }
        }

        if (!first) { [json appendString:@","]; }
        first = NO;

        [json appendFormat:@"{\"text\":\"%@\",\"confidence\":%.4f,\"frame\":{\"x\":%.2f,\"y\":%.2f,\"width\":%.2f,\"height\":%.2f}}",
         json_escape(candidate.string), candidate.confidence, x, y, w, h];
    }

    [json appendString:@"]"];
    return strdup([json UTF8String]);
}

void vision_ocr_free(char *s) {
    free(s);
}
