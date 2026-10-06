// window_helper.h - Window listing helper for resolve-control.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>

// Returns a JSON array of {id, title, frame:{x,y,width,height}, backing_scale}
// for all on-screen windows. Caller frees with window_helper_free.
char *window_helper_list(void);
void window_helper_free(char *s);

// Base64 of `image` encoded as PNG by ImageIO, or NULL. Caller frees with
// window_helper_free.
char *window_helper_png_base64(CGImageRef image);

// 1 when the login session's screen is locked: the window server then hands
// back blank captures and routes input to the lock screen.
int window_helper_screen_locked(void);

// The window `window_id` alone, captured by ScreenCaptureKit at `scale`
// pixels per point, or NULL. The window server composites it, so a window
// covered by other apps still comes back. Caller releases with CFRelease.
CGImageRef window_helper_sck_capture(uint32_t window_id, double scale);
