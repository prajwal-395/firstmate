// vision_ocr.h - Vision OCR helper for resolve-control.
// Linked into the Rust binary; called via FFI.
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>
#import <CoreGraphics/CoreGraphics.h>

// OCR a CGImage, return a JSON array of {text, confidence, frame:{x,y,width,height}}.
// Frames are in image pixel coordinates, top-left origin.
// Caller frees the returned string with vision_ocr_free.
char *vision_ocr_image(CGImageRef image, double region_x, double region_y,
                       double region_w, double region_h, int has_region);

void vision_ocr_free(char *s);
