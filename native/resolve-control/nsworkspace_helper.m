// nsworkspace_helper.m - NSWorkspace helper for resolve-control.
#import "nsworkspace_helper.h"
#import <objc/runtime.h>

int64_t nsworkspace_find_pid(const char *bundle_id) {
    if (!bundle_id) return 0;
    @autoreleasepool {
        NSWorkspace *ws = [NSWorkspace sharedWorkspace];
        for (NSRunningApplication *app in ws.runningApplications) {
            if ([app.bundleIdentifier isEqualToString:@(bundle_id)]) {
                return app.processIdentifier;
            }
        }
    }
    return 0;
}

int nsworkspace_activate_pid(int64_t pid) {
    @autoreleasepool {
        NSWorkspace *ws = [NSWorkspace sharedWorkspace];
        for (NSRunningApplication *app in ws.runningApplications) {
            if (app.processIdentifier == pid) {
                [app activateWithOptions:NSApplicationActivateAllWindows
                    | NSApplicationActivateIgnoringOtherApps];
                return 1;
            }
        }
    }
    return 0;
}

char *nsworkspace_frontmost(void) {
    @autoreleasepool {
        NSRunningApplication *app = [[NSWorkspace sharedWorkspace] frontmostApplication];
        if (!app) return NULL;
        NSString *bid = app.bundleIdentifier ?: @"";
        NSString *name = app.localizedName ?: @"";
        NSString *json = [NSString stringWithFormat:
            @"{\"bundle_id\":\"%@\",\"name\":\"%@\",\"pid\":%d}", bid, name,
            app.processIdentifier];
        return strdup([json UTF8String]);
    }
}

void nsworkspace_free(char *s) {
    free(s);
}
