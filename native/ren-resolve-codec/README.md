# ren-resolve-codec

`ren-resolve-codec` inspects exported DaVinci Resolve `.drp` and `.drt` ZIP
archives and can return a no-change copy of the exact input bytes. It is a
serialization interoperability crate; it does not render video, control
Resolve, or open live project databases. `.drx` and interchange XML are not
supported.

## Capability boundary

The initial inspection supports only the measured `DbAppVer=21.1.0.0014`,
`DbPrjVer=17` pair. Unknown or missing app/project versions refuse; sharing
only a project format version is not treated as compatibility evidence.
Inspection reports project/version identity,
archive member hashes and sizes, timeline IDs, track/item observations, XML
source byte spans, and opaque `*BA`/`*Blob` content lengths and hashes. These
observations are not a complete Resolve schema or reference-graph validator.
For observation only, XML-invalid control bytes are stripped and C++-qualified
tag names containing `::` are normalized in a temporary view; reported source
spans map back to the original bytes. The temporary view is never written.

`read_preserved(...).write_unchanged()` returns the original archive bytes
verbatim. This is stronger than member-payload preservation: ZIP directory
records, compression streams, ordering, timestamps, comments, extra fields,
and unknown members remain byte-exact because no archive serialization takes
place. The inspection model cannot be passed to a writer. There is no semantic
mutation API. XML spans are observations only; no normalized XML is serialized
back into an archive.

The protobuf helper is schema-neutral and retains raw field encodings and
order. No `drp-format` implementation code or Resolve-captured template is
included.

Inspection bounds member count to 20,000, a member's decompressed size to
128 MiB, and total decompressed content to 1 GiB. It rejects duplicate member
names, absolute/traversal/backslash paths, unsupported extensions, malformed
ZIP/XML, multi-disk and ZIP64-unreadable directories that cannot be walked
exactly, and unsupported project versions with typed errors. Duplicate
detection walks the raw central directory, not just the reader inventory, so
a collapsed duplicate entry refuses as ambiguity instead of shadowing another
member.

## Fixtures and evidence

`fixtures/manifest.json` records hashes and aggregate counts for two permitted
external exports. The exports themselves are not included. Tests construct
small synthetic archives independently and contain no Resolve assets.

To check the external exports against their exact identities and semantic
counts, run the `verify-fixtures` binary with the manifest and explicit paths
to the `.drp` and `.drt`. It is a manual corpus check and is not invoked by the
test suite.

The field-test exports were reported as Resolve 21.1.0.0014 / DbPrjVer 17:
the `.drp` has 23 sequences, 174 tracks and 1,421 items; the `.drt` has one
sequence, five tracks and 339 items. These are saved exports, not live project
state, and establish no import, editability, or render capability.
