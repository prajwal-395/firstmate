# The Resolve panel, as it looks

Six screenshots of `resolve_scripts/VEP Pipeline Panel.py` running against a real
DaVinci Resolve on 2026-08-30, on `Pipeline_Edit_2` - a real build of project 001
- with the playhead at 00:00:13:04.

Committed rather than linked, because a screenshot of a UI the captain judges by
looking should not depend on a host that can go away. They are the honest
baseline for what Qt 5.15 rich text inside Resolve's own `UIManager` can do:
headings, tables, colour, monospace and `<img>`, with no CSS grid and no
browser.

| file | tab | what it shows |
|---|---|---|
| `1_trace_top.png` | Trace | 28 steps in run order; 001's 2.89 MB `temporal_index` as seven readable lines |
| `2_trace_drilldown.png` | Trace | two levels down - `full_indices / 10` - each key with its kind, size and a preview that never serialises the subtree |
| `3_this_clip.png` | This clip | the clip under the playhead joined to the catalog, the vision pass, the transcript of the seconds that PLAY, and the step that chose it |
| `4_run_configuration.png` | Run | the `podcast` profile: 19 steps in, 6 out with the reason for each, and where the run stops |
| `5_gates.png` | Gates | the review gate the run configuration left on 001, its snapshot drilled down, and approve / reject / revise |
| `6_timeline.png` | Timeline | the plan as a picture the panel encodes itself, with Resolve's live playhead drawn on it |

Every one is the panel's own window at its declared geometry, 1180x884 points.

## How these are captured, and the mistake that is worth not repeating

The first version of `1_trace_top.png` in this directory was **not the panel**. It
was a macOS "Python quit unexpectedly" dialog over Resolve's media pool, captured
from the screen region the panel window occupies, and it was committed with a
caption describing a view it did not contain.

Two things went wrong and both are procedural:

1. The capture wrote a screen RECTANGLE without first confirming the panel window
   was really there and frontmost. It now confirms both and prints what it found
   before writing the file.
2. **Six files were captured and two were looked at.** The rest were copied in on
   the strength of an earlier round that had been checked. Every file in this
   directory must be opened and read before it is committed - that is the only
   check that distinguishes a picture of the panel from a picture of something
   else, and it is the check the captain ran.

Regenerate them by installing the panel (`scripts/install_resolve_scripts.sh`) and
opening Workspace > Scripts > VEP Pipeline Panel.
