# A step's result must survive being bigger than a pipe buffer

Moved from `tests/unit/context/test_step_subprocess_large_output.py`.

The runner deadlocked on project 001 exactly here. `semantic_analysis`
finished all 17 clips, printed "Collected 17 clip profiles", and stopped.
`sample` showed the child parked in `write()` and BOTH parent threads in
`read()`: stderr was being echoed from a pump thread while
`communicate()` - which reads stdout AND stderr - ran on the same Popen.
Two readers on one pipe, so the main thread blocked on stderr bytes the
pump had already taken, nothing drained stdout, and the child wedged the
moment its JSON exceeded the 64KB pipe buffer.

The tell is that it is invisible on small steps. `scan` and `catalog`
emit a few KB and pass; the deadlock only appears once a step has real
work to report, which is why it survived every test and every small
project. So these tests use an output deliberately far past one buffer.

Which test actually reproduces it, measured against the broken revision:
**`test_large_stderr_and_large_stdout_together`, and only that one.** A
large stdout alone passes even on the deadlocking version, because with
stderr quiet the pump simply blocks and the selector still services
stdout. The race needs traffic on BOTH pipes - the selector reports
stderr readable, the pump has already taken those bytes, and the main
thread parks in a read() that will never return, after which stdout is
never serviced again. That is the analysis phase exactly: a step that
logs steadily for an hour and then emits a large result.

The five sibling tests that pinned surrounding behaviour (exit codes, stderr on
failure, a large stdin, a fast step) were removed earlier; this one is the guard.
