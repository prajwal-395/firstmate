import sys

filepath = "/Users/prajwal/Documents/work_stuff/firstmate/data/fm-curate-backends-to-herdr/handoff.md"
with open(filepath, "r") as f:
    content = f.read()

old_text = "- **Other failures**: All remaining lane failures are due to test suites that require porting to herdr fakes. Per instructions, these are left untouched."

new_text = """- **Other failures**: Most remaining lane failures are due to test suites that require porting to herdr fakes, and are left untouched per instructions. Exceptions:
  - `tests/fm-test-run.test.sh`: Failed because the coverage guard partition broke (my change). Fixed locally by adding the duration hint.
  - `tests/fm-pi-watch-extension.test.sh`: Failed with a Node EPIPE crash. Cause not established; passed on two recent non-branch runs."""

new_content = content.replace(old_text, new_text)

with open(filepath, "w") as f:
    f.write(new_content)
