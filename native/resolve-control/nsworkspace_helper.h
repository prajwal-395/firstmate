// nsworkspace_helper.h - NSWorkspace helper for resolve-control.
// Linked into the Rust binary; called via FFI.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>

// Returns the PID of the running app with the given bundle ID, or 0.
int64_t nsworkspace_find_pid(const char *bundle_id);

// Activates the app with the given PID. Returns 1 on success.
int nsworkspace_activate_pid(int64_t pid);

// Gets the frontmost app. Returns a JSON object {bundle_id, name}.
// Caller frees the returned string with nsworkspace_free.
char *nsworkspace_frontmost(void);

void nsworkspace_free(char *s);
