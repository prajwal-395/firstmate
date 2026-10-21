# The universal source reference (lane A-04)

A claim about a picture is worth as much as the bytes it was read from.
A claim that cannot say which bytes those were is a claim about
nothing. This is the one way Ren points at the original information,
and the rules that keep the pointer honest.

## The type

`SourceReference` composes what the canonical model already carries:

| kind | names | carries |
|---|---|---|
| `asset` | the original file, whole | the asset id |
| `stream` | one stream inside an asset | the stream id |
| `element` | one native element - a frame or a sample | the stream, the clock that counts it, the index |
| `interval` | a run of a stream's elements | the stream, the interval (clock + ticks) |
| `region` | a place in a coordinate space | the space, the geometry |
| `sub_element` | a named part of another reference | the parent, what it is, which one |

One spelling, in three places: the Rust type in
`native/ren-protocol/src/project_model.rs`, the JSON Schema generated
from it (`project-source-reference.schema.json`), and the Python
enumeration in `library/tools/source_reference.py`.

Two properties are deliberate.

**It names the source, never a derived record.** The observation that
described a frame is not the frame: grounding a claim in the
description would be grounding it in a caption. An `asset` reference
names the file, and a file is not a place in it - so a measurement is
complete only when one of its references reaches a stream, an element,
an interval or a region.

**It names no application and no model provider.** A reference stays
meaningful when the analysis that made it came from one tool and the
edit that uses it is executed by another. The project-model schemas
carry no provider name, and a test keeps it that way.

## What is refused, and where

| the defect | refused by | the refusal |
|---|---|---|
| a measurement with no reference that reaches the source | `evidence_contract.EvidenceRecord` (when the record is written) and `project_model.validate_project_model` (when the model is written) | "a measurement must carry at least one reference that names the source" |
| a mask whose `representation.kind` nobody can read | `project_model.validate_project_model` | the mask registry, listed |
| a singular space-transform matrix | `project_model.validate_project_model`, `transform_graph::validate_graph` | "space transform matrix must be invertible" |
| a space transform whose endpoint is not declared | the same two | "space transform references unknown coordinate space" |
| a derived space with no calibration | the same two | "coordinate space requires a calibration" |
| a reference that joins to nothing | `project_model.validate_project_model` | the unknown asset, stream, clock or space, named |

The rules are versioned by the evidence contract: a record stamped
`contract_version` 2 or later carries the reference; a record stamped
1 predates the contract and keeps reading as it was written. Documents
already in a project store are not rewritten by this change, and the
migration that produces new ones cannot write a measurement without a
reference.

## The spatial tables

`coordinate_spaces` and `space_transforms` were collections nothing
wrote. `library/tools/spatial_tables.py` is their one producer, and it
produces them from a measurement: the frame resolution the source
metadata (or the segmentation lane) read off the source.

Two spaces per source, and the join between them:

- `space-<digest>-pixels` - the source's own pixel grid. It describes
  itself, so it carries no calibration.
- `space-<digest>-normalized` - the same grid divided by that
  resolution. It means nothing without the frame it normalizes, so it
  carries its calibration: the measured resolution.
- `space-transform-<digest>-pixels-to-normalized` - the measured scale
  between them, an invertible matrix.

The tracks that measured geometry in those grids now join to them by
`coordinate_space_id`, so a reader can walk from a normalized box back
to the pixels it was measured on.

## The resolver

`library/tools/reference_resolver.py` is the single way to ask "where
are the bytes this claim was read from?". Given any valid reference it
returns the artefact path and the byte range inside it, or it refuses
with a named reason:

| reason | means |
|---|---|
| `missing` | the thing is not there: an unknown asset, a file that is gone, a mask nobody wrote |
| `stale` | the thing is there, but it is not the thing the model measured: the bytes changed |
| `ambiguous` | the reference names more than one thing, so choosing one would be a guess |
| `incompatible` | the reference cannot be expressed against this source: the model carries no measured mapping from it to bytes |

A refusal is never a guess. A reader that cannot be told where the
bytes are is told that.

## The measurements

- **Reference completeness**: 100% of the measurement rows a migration
  writes carry at least one reference that reaches the source. Before
  this change a measurement with no reference at all was accepted.
- **Resolution**: a resolver that answers with a path and a byte range,
  or refuses with one of the four reasons above. Before this change
  there were four per-domain resolvers and no ambiguity case at all
  outside one policy loader.
- **Spatial validation**: a singular matrix, a dangling space join and
  a null calibration are each refused. All three were accepted.
- **Mask typing**: a mask whose `representation.kind` is not in the
  registry is refused at model validation. It was accepted.
