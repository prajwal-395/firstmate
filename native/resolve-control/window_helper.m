// window_helper.m - Window listing helper for resolve-control.
#import "window_helper.h"
#import <CoreGraphics/CoreGraphics.h>
#import <ImageIO/ImageIO.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <objc/runtime.h>

static NSString *json_escape(NSString *s) {
    NSMutableString *out = [s mutableCopy];
    [out replaceOccurrencesOfString:@"\\" withString:@"\\\\" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\"" withString:@"\\\"" options:0 range:NSMakeRange(0, out.length)];
    [out replaceOccurrencesOfString:@"\n" withString:@"\\n" options:0 range:NSMakeRange(0, out.length)];
    return out;
}

static NSString *dict_string(NSDictionary *dict, NSString *key) {
    id val = dict[key];
    return [val isKindOfClass:[NSString class]] ? val : @"";
}

static CGFloat dict_cgfloat(NSDictionary *dict, NSString *key) {
    id val = dict[key];
    if ([val isKindOfClass:[NSNumber class]]) return [val doubleValue];
    return 0.0;
}

// CGWindow info carries no backing scale, so read it off the screen that
// holds the window's centre (CG bounds are top-left from the primary screen,
// NSScreen frames bottom-left).
static CGFloat screen_scale_for(CGFloat x, CGFloat y, CGFloat w, CGFloat h) {
    NSArray<NSScreen *> *screens = [NSScreen screens];
    if (screens.count == 0) return 1.0;
    CGFloat primary_top = NSMaxY(screens[0].frame);
    NSPoint centre = NSMakePoint(x + w / 2.0, primary_top - (y + h / 2.0));
    for (NSScreen *screen in screens) {
        if (NSPointInRect(centre, screen.frame)) return screen.backingScaleFactor;
    }
    return screens[0].backingScaleFactor;
}

static NSString *fmt(CGFloat v) {
    if (!isfinite(v)) return @"0.00";
    return [NSString stringWithFormat:@"%.2f", v];
}

char *window_helper_list(void) {
    @autoreleasepool {
        CFArrayRef windowList = CGWindowListCopyWindowInfo(
            kCGWindowListOptionAll, kCGNullWindowID);
        if (!windowList) return strdup("[]");

        NSArray *windows = (__bridge NSArray *)windowList;
        NSMutableString *json = [NSMutableString stringWithString:@"["];
        BOOL first = YES;

        for (NSDictionary *info in windows) {
            NSNumber *wid = info[@"kCGWindowNumber"];
            NSString *name = dict_string(info, @"kCGWindowName");
            NSString *owner = dict_string(info, @"kCGWindowOwnerName");
            NSNumber *ownerPid = info[@"kCGWindowOwnerPID"];
            NSDictionary *bounds = info[@"kCGWindowBounds"];

            CGFloat x = dict_cgfloat(bounds, @"X");
            CGFloat y = dict_cgfloat(bounds, @"Y");
            CGFloat w = dict_cgfloat(bounds, @"Width");
            CGFloat h = dict_cgfloat(bounds, @"Height");
            CGFloat s = screen_scale_for(x, y, w, h);

            if (!first) { [json appendString:@","]; }
            first = NO;

            [json appendFormat:
                @"{\"id\":%@,\"title\":\"%@\",\"owner\":\"%@\",\"owner_pid\":%@,\"frame\":{\"x\":%@,\"y\":%@,\"width\":%@,\"height\":%@},\"backing_scale\":%@}",
                wid, json_escape(name), json_escape(owner), ownerPid ?: @0,
                fmt(x), fmt(y), fmt(w), fmt(h), fmt(s)];
        }

        [json appendString:@"]"];
        CFRelease(windowList);
        return strdup([json UTF8String]);
    }
}

char *window_helper_png_base64(CGImageRef image) {
    @autoreleasepool {
        NSMutableData *data = [NSMutableData data];
        CGImageDestinationRef dest = CGImageDestinationCreateWithData(
            (__bridge CFMutableDataRef)data, CFSTR("public.png"), 1, NULL);
        if (!dest) return NULL;
        CGImageDestinationAddImage(dest, image, NULL);
        BOOL ok = CGImageDestinationFinalize(dest);
        CFRelease(dest);
        if (!ok) return NULL;
        return strdup([[data base64EncodedStringWithOptions:0] UTF8String]);
    }
}

CGImageRef window_helper_sck_capture(uint32_t window_id, double scale) {
    @autoreleasepool {
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block SCWindow *target = nil;
        [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                                   onScreenWindowsOnly:NO
                                                     completionHandler:^(SCShareableContent *content, NSError *error) {
            for (SCWindow *w in content.windows) {
                if (w.windowID == window_id) { target = w; break; }
            }
            dispatch_semaphore_signal(done);
        }];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) || !target) {
            return NULL;
        }
        SCContentFilter *filter = [[SCContentFilter alloc] initWithDesktopIndependentWindow:target];
        SCStreamConfiguration *config = [[SCStreamConfiguration alloc] init];
        config.width = (size_t)(target.frame.size.width * scale);
        config.height = (size_t)(target.frame.size.height * scale);
        config.showsCursor = NO;
        config.ignoreShadowsSingleWindow = YES;
        __block CGImageRef image = NULL;
        [SCScreenshotManager captureImageWithFilter:filter
                                      configuration:config
                                  completionHandler:^(CGImageRef captured, NSError *error) {
            if (captured) image = CGImageRetain(captured);
            dispatch_semaphore_signal(done);
        }];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC))) {
            return NULL;
        }
        return image;
    }
}

int window_helper_screen_locked(void) {
    CFDictionaryRef session = CGSessionCopyCurrentDictionary();
    if (!session) return 0;
    NSDictionary *d = (__bridge NSDictionary *)session;
    int locked = [d[@"CGSSessionScreenIsLocked"] boolValue] ? 1 : 0;
    CFRelease(session);
    return locked;
}

void window_helper_free(char *s) {
    free(s);
}
