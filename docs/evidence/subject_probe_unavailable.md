# A subject probe with no detector

Tests: `tests/test_subject_probe_unavailable.py`.

Reel 09, 2026-09-09: a full `build-reels --only-reel 9` failed the reel
gate on 6x F12, every item delivering the IDENTICAL rect
(0, 656, 1080, 1264) - the identity transform's fitted strip.  The aim
never ran: system python's cv2 5.0.0 ships no `CascadeClassifier`, so
`measure_subject_in_window` answered None for every shot without
decoding a frame, `punch_in_properties` refused every shot, and the
placer left all six items unpunched.  The gate named the symptom; the
cause was an incapacitated probe the build never mentioned.

None from this function means "frames were read and no face was
measured" - genuine absence, which leaves the shot uncropped by the
captain's refuse-rather-than-guess ruling.  A detector that could not
even be loaded is a different fact and raises `SubjectProbeUnavailable`
naming the interpreter's cv2, before any frame is decoded, so the
caller refuses the build with the cause instead of shipping staging
the gate deletes.  The same line `render_qa.measure_face_intact`
draws with its "No Haar cascade available" warning: a measurement
that could not be taken must say so.
