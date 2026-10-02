# clip ref check

Test: `tests/test_clip_ref_check.py`.

D7: the clip-reference check fires on a step's own legend text.

Step 5.01's hybrid output carries `grade_terms_legend` - a dict whose
`clip_id` key maps to the legend's prose, not to a clip - and
`grade_pipeline`, whose `source` values name brand-template slots
("brand template style.series_look.cdl"), not files. The check walked the
whole step output with bare-key matching, so both read as unrecognized
references on every run.

The fix is in what counts as a reference: a `clip_id` value that is a
sentence is a definition of the term, not a use of it, and a `source`
value with no directory separator is a slot name or an enum, not a file.
A warning that fires on correct output is noise, and noise is how a real
one gets scrolled past.
