//! The front door's contract, run against the built binary with a
//! fake ladder and a fake interpreter: the binary must exec
//! `python -m ren.cli` with the original argv and the engine's own
//! environment, and refuse - never fall back to a stock interpreter -
//! when the ladder finds nothing.

use std::env;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

struct Sandbox {
    root: PathBuf,
}

impl Sandbox {
    fn new(label: &str) -> Sandbox {
        let root = env::temp_dir().join(format!(
            "ren-cli-{label}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("bin")).unwrap();
        fs::create_dir_all(root.join("engine/library/tools")).unwrap();
        fs::write(
            root.join("engine/library/tools/shared_environment.py"),
            "# faked for this test\n",
        )
        .unwrap();
        Sandbox { root }
    }

    /// A `python3` that answers the ladder query with a fixed report.
    /// Builtins only: the front door's PATH holds this directory alone.
    fn ladder_vehicle(&self, report: &str) {
        let vehicle = self.root.join("bin/python3");
        fs::write(
            &vehicle,
            format!("#!/bin/sh\nprintf '%s\\n' '{report}'\n"),
        )
        .unwrap();
        make_executable(&vehicle);
    }

    /// The interpreter the ladder names: prints its argv and the
    /// environment the front door set, then succeeds.
    fn fake_interpreter(&self) -> PathBuf {
        let interpreter = self.root.join("fake-interpreter");
        fs::write(
            &interpreter,
            "#!/bin/sh\n\
             for arg in \"$@\"; do\n\
             echo \"arg: $arg\"\n\
             done\n\
             echo \"REN_ENGINE_ROOT: $REN_ENGINE_ROOT\"\n\
             echo \"PYTHONPATH: $PYTHONPATH\"\n\
             echo \"PYTHONSAFEPATH: $PYTHONSAFEPATH\"\n\
             exit 0\n",
        )
        .unwrap();
        make_executable(&interpreter);
        interpreter
    }

    fn run_ren(&self, argv: &[&str]) -> std::process::Output {
        Command::new(env!("CARGO_BIN_EXE_ren"))
            .args(argv)
            .env("PATH", self.root.join("bin"))
            .env("REN_ENGINE_ROOT", self.root.join("engine"))
            .env("PYTHONPATH", "/already/there")
            .output()
            .unwrap()
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn make_executable(path: &Path) {
    let mut permissions = fs::metadata(path).unwrap().permissions();
    permissions.set_mode(0o755);
    fs::set_permissions(path, permissions).unwrap();
}

#[test]
fn it_execs_the_python_implementation_with_the_original_argv() {
    let sandbox = Sandbox::new("exec");
    let interpreter = sandbox.fake_interpreter();
    sandbox.ladder_vehicle(&format!(
        "{{\"python\": \"{}\", \"error\": \"\", \"tried\": []}}",
        interpreter.display()
    ));

    let output = sandbox.run_ren(&["status", "some-project", "--verbose"]);

    assert!(output.status.success(), "stderr: {}", String::from_utf8_lossy(&output.stderr));
    let stdout = String::from_utf8_lossy(&output.stdout);
    let lines: Vec<&str> = stdout.lines().collect();
    assert_eq!(
        lines,
        vec![
            "arg: -m",
            "arg: ren.cli",
            "arg: status",
            "arg: some-project",
            "arg: --verbose",
            &format!("REN_ENGINE_ROOT: {}", sandbox.root.join("engine").display()),
            &format!(
                "PYTHONPATH: {}:/already/there",
                sandbox.root.join("engine").display()
            ),
            "PYTHONSAFEPATH: 1",
        ]
    );
}

#[test]
fn a_ladder_that_finds_nothing_is_refused_with_the_ladders_message() {
    let sandbox = Sandbox::new("refuse");
    sandbox.ladder_vehicle(
        "{\"python\": \"\", \"error\": \"No Python interpreter carrying the ML stack was found.\", \"tried\": []}",
    );

    let output = sandbox.run_ren(&["status", "some-project"]);

    assert_eq!(output.status.code(), Some(3));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.starts_with("ren: refusing - no interpreter carrying the ML stack.\n"));
    assert!(stderr.contains("No Python interpreter carrying the ML stack was found."));
}

#[test]
fn an_unparseable_ladder_answer_is_refused_as_such() {
    let sandbox = Sandbox::new("garbled");
    sandbox.ladder_vehicle("not json at all");

    let output = sandbox.run_ren(&["status", "some-project"]);

    assert_eq!(output.status.code(), Some(3));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("(the ladder answered nothing parseable; see docs/ML_ENVIRONMENT.md)"));
}

#[test]
fn no_python3_on_path_is_refused_with_the_fix() {
    let sandbox = Sandbox::new("novehicle");

    let output = Command::new(env!("CARGO_BIN_EXE_ren"))
        .args(["status", "some-project"])
        .env("PATH", &sandbox.root)
        .env("REN_ENGINE_ROOT", sandbox.root.join("engine"))
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(3));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("ren: no python3 on PATH to resolve the interpreter ladder."));
    assert!(stderr.contains("docs/ML_ENVIRONMENT.md"));
}
