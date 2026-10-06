fn main() {
    println!("cargo:rerun-if-changed=vision_ocr.m");
    println!("cargo:rerun-if-changed=vision_ocr.h");
    println!("cargo:rerun-if-changed=nsworkspace_helper.m");
    println!("cargo:rerun-if-changed=nsworkspace_helper.h");
    println!("cargo:rerun-if-changed=window_helper.m");
    println!("cargo:rerun-if-changed=window_helper.h");

    cc::Build::new()
        .file("vision_ocr.m")
        .file("nsworkspace_helper.m")
        .file("window_helper.m")
        .flag("-fobjc-arc")
        .flag("-framework")
        .flag("Foundation")
        .flag("-framework")
        .flag("Vision")
        .flag("-framework")
        .flag("CoreGraphics")
        .flag("-framework")
        .flag("AppKit")
        .compile("objc_helpers");

    println!("cargo:rustc-link-lib=framework=Foundation");
    println!("cargo:rustc-link-lib=framework=Vision");
    println!("cargo:rustc-link-lib=framework=CoreGraphics");
    println!("cargo:rustc-link-lib=framework=AppKit");
    println!("cargo:rustc-link-lib=framework=ApplicationServices");
    println!("cargo:rustc-link-lib=framework=CoreServices");
    println!("cargo:rustc-link-lib=framework=ImageIO");
    println!("cargo:rustc-link-lib=framework=ScreenCaptureKit");
    println!("cargo:rustc-link-lib=framework=UniformTypeIdentifiers");
}
