mod ax;
mod capture;
mod driver;
mod input;
mod ocr;
mod protocol;
mod skylight;

use driver::Driver;
use std::io;

extern "C" {
    fn signal(signum: i32, handler: usize) -> usize;
    fn _exit(status: i32) -> !;
}

const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

/// A kill arrives mid-sequence: recouple the cursor best-effort, then
/// die for real. Async-signal-safe by necessity rather than by proof:
/// the alternative (a dead driver with the captain's cursor decoupled)
/// is worse than a best-effort call. The exit code follows the shell
/// convention, 128 plus the signal.
extern "C" fn on_kill(signum: i32) {
    input::recouple_cursor();
    unsafe { _exit(128 + signum) };
}

fn main() -> io::Result<()> {
    unsafe {
        signal(SIGINT, on_kill as usize);
        signal(SIGTERM, on_kill as usize);
    }
    let mut driver = Driver::new();
    driver.run()
}
