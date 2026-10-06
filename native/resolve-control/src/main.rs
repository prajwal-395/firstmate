mod ax;
mod capture;
mod driver;
mod input;
mod ocr;
mod protocol;

use driver::Driver;
use std::io;

fn main() -> io::Result<()> {
    let mut driver = Driver::new();
    driver.run()
}
