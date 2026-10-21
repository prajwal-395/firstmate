"""A BLIND review pack: human reviewers watch delivered videos blind.

QA-007 of the captain's punch list (2026-10-20) asks for human editorial
review on a blind evaluation set:

* reviewers inspect delivered videos WITHOUT knowing which candidate or
  model produced them;
* they are given the original brief, the constraints and the relevant
  reference material;
* they use a structured rubric ALONGSIDE an overall acceptance decision;
* disagreement among reviewers is RECORDED, rather than one subjective
  opinion being treated as absolute truth;
* acceptable work is distinguished from work that requires significant
  human repair.

What this module is
-------------------
The SURFACE. A pack is a directory a reviewer can be handed: the delivered
videos under opaque names, the brief and its constraints, the reference
material, and one self-contained form. The form's answers come back as a
file, and the results record is computed from those files.

What this module is NOT
-----------------------
It does not review anything itself, and it does not decide whether a
deliverable is good. It builds the surface a human reads, and it RECORDS
what the humans said - including where they said different things.

The rubric is the one the engine already has. The eight legs and the two
kinds of row (an objective measurement, and a subjective reading) live in
`library.tools.editorial_acceptance.judgement` (QA-006, PR 3088), and this
module does not restate them: a reviewer's rows are validated by the same
`judgement.review` that grades an agent's, so a reviewer cannot write a
judgment with no evidence or no stated uncertainty either.

THE BLINDING
------------
A blind pack is blind in three places, and each is checked rather than
hoped for:

1. the PACK carries no origin in anything a reviewer reads - the entries
   are opaque ids, the media are copies under those ids, and the reviewer
   view is refused if any origin string appears in it (`assert_blind`);
2. the ORDER of the entries says nothing about the origin - it is a
   digest of the pack id and the candidate id, so no position is
   systematically one producer's;
3. the media copies carry no metadata - the container's tags are stripped
   on the way in, because a file whose tags say who made it is not blind
   however carefully the rest is arranged.

What the blinding does NOT cover: the PICTURE. A deliverable that shows
its producer in the frame is not made blind by renaming it, and this
module says so rather than claiming a guarantee it cannot keep.

THE RECORD
----------
`results` keeps every reviewer's rows, every reviewer's score and every
reviewer's decision, and it reports DISAGREEMENT where it happened. A
candidate two reviewers judged differently is `disagreement` - not the
average of the two, and not the more confident of the two. The engine's
own objective measurements are published BESIDE the panel's reading, so a
human's approval cannot quietly clear a measured defect.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools.editorial_acceptance import judgement

PACK_SCHEMA = "ren-blind-review-pack/1"
ANSWERS_SCHEMA = "ren-blind-review-answers/1"
RESULTS_SCHEMA = "ren-blind-review-results/1"

#: The overall acceptance decision a reviewer makes about one deliverable.
#: The third value is the one the punch list asks for: work that is
#: ACCEPTABLE is distinguished from work that needs a human to REPAIR it,
#: and the two are not the same answer.
ACCEPTABLE = "acceptable"
MINOR_REPAIR = "acceptable_with_minor_repair"
SIGNIFICANT_REPAIR = "requires_significant_repair"
DECISIONS: tuple[str, ...] = (ACCEPTABLE, MINOR_REPAIR, SIGNIFICANT_REPAIR)

#: What a candidate's standing is when the reviewers did not agree.
STANDING_DISAGREEMENT = "disagreement"

#: The shortest string that can be checked for a leak. A secret shorter
#: than this matches everywhere, so a candidate that names itself in two
#: letters cannot be blinded and is refused rather than half-checked.
MIN_SECRET_LENGTH = 3


class BlindReviewError(ValueError):
    """The blind review pack does not satisfy its own contract."""


# ── the files a pack is made of ────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_duration(path: Path) -> float | None:
    """The media's own duration, or None when nothing could read it."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, encoding="utf-8", check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
        return float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def _strip_metadata(source: Path, target: Path) -> bool:
    """Copy the media without the container's tags, and say if it worked.

    A file whose metadata names its producer is not blind, however
    carefully the rest of the pack is arranged. The copy is a remux with
    the metadata dropped, which keeps the picture and the sound exactly
    as they were.

    Returns whether the copy is metadata-free. A copy that could not be
    stripped is still made, and the pack says so rather than pretending.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(source),
             "-map_metadata", "-1", "-c", "copy", str(target)],
            capture_output=True, encoding="utf-8", check=False)
    except OSError:
        result = None
    if result is not None and result.returncode == 0 and target.is_file():
        return True
    shutil.copyfile(source, target)
    return False


def _entry_id(pack_id: str, candidate_id: str, content_sha256: str) -> str:
    """The opaque name an entry carries in the pack.

    It is a digest, so it says nothing about the candidate or its
    producer, and it is stable for one pack, so the same deliverable can
    be recognised across a reviewer's two sittings without being named.
    """
    digest = hashlib.sha256(
        f"{pack_id}|{candidate_id}|{content_sha256}".encode("utf-8")).hexdigest()
    return f"entry-{digest[:16]}"


def _order_key(pack_id: str, candidate_id: str) -> str:
    """The order the entries are shown in.

    A digest of the pack id and the candidate id: deterministic, so a
    reviewer who repeats a pack sees the same order, and unrelated to
    the producer, so no position in the pack is systematically one
    producer's.
    """
    return hashlib.sha256(
        f"{pack_id}|order|{candidate_id}".encode("utf-8")).hexdigest()


# ── the pack ───────────────────────────────────────────────────────────


def _candidate_secrets(candidate: Mapping[str, Any]) -> list[str]:
    """The strings that would tell a reviewer who made this candidate."""
    secrets = []
    for key in ("candidate_id", "origin", "label"):
        value = str(candidate.get(key) or "").strip()
        if value:
            if len(value) < MIN_SECRET_LENGTH:
                raise BlindReviewError(
                    f"the candidate's {key} {value!r} is shorter than "
                    f"{MIN_SECRET_LENGTH} characters, so it cannot be "
                    "checked for leaks and the pack would not be blind")
            secrets.append(value)
    source = Path(str(candidate.get("path") or ""))
    if source.name:
        secrets.append(source.name)
        secrets.append(source.stem)
    return sorted(set(secrets))


def _leaks(text: str, secrets: Sequence[str]) -> list[str]:
    """Which of the secrets appear in this text.

    The match is on WHOLE WORDS, case-sensitively. A brand name that the
    brief legitimately uses ("Ren") is not the same string as the origin
    label a caller chose, and a secret that appears inside a longer word
    ("ren" inside "render") is not a leak of the origin.
    """
    found = []
    for secret in secrets:
        pattern = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(secret) + r"(?![A-Za-z0-9])")
        if pattern.search(text):
            found.append(secret)
    return found


def build_pack(*, pack_id: str, brief: Mapping[str, Any],
               candidates: Sequence[Mapping[str, Any]],
               out_dir: str | Path,
               constraints: Sequence[str] = (),
               references: Sequence[Mapping[str, Any]] = (),
               objective: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
               ) -> dict[str, Any]:
    """Build one blind review pack and write its reviewer-facing surface.

    `brief` is the brief's PUBLIC half - the instruction, the project
    state and the acceptance criteria - and the brief's answer key is
    refused here rather than carried into a reviewer's hands.

    `candidates` are the delivered videos under review. Each carries a
    `candidate_id`, the `path` of the delivered file, and the `origin`:
    who produced it. The origin is the thing being hidden, and it is
    recorded in the pack's PRIVATE half only.

    `objective` maps a candidate id to the engine's own measurements of
    that delivered file (QA-002/003/004, as `judgement.objective_legs`
    produces them). They are not shown to the reviewer - a measurement
    would anchor a human's reading - and they are published BESIDE the
    panel's reading in the results.
    """
    if not str(pack_id or "").strip():
        raise BlindReviewError("a pack needs an id")
    judgement.assert_independent(brief)
    if not isinstance(candidates, Sequence) or not candidates:
        raise BlindReviewError("a pack needs at least one candidate")

    directory = Path(out_dir)
    (directory / "entries").mkdir(parents=True, exist_ok=True)
    (directory / "references").mkdir(parents=True, exist_ok=True)

    seen_ids: set[str] = set()
    entries = []
    blinding: dict[str, Any] = {}
    entry_by_candidate: dict[str, str] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise BlindReviewError("a candidate must be an object")
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise BlindReviewError("a candidate needs a candidate_id")
        if candidate_id in seen_ids:
            raise BlindReviewError(
                f"two candidates share the id {candidate_id!r}")
        seen_ids.add(candidate_id)
        source = Path(str(candidate.get("path") or ""))
        if not source.is_file():
            raise BlindReviewError(
                f"the candidate {candidate_id!r} has no delivered file at "
                f"{source}")
        digest = _sha256(source)
        entry = _entry_id(pack_id, candidate_id, digest)
        target = directory / "entries" / f"{entry}.mp4"
        stripped = _strip_metadata(source, target)
        copy_digest = _sha256(target)
        entries.append({
            "entry_id": entry,
            "media": f"entries/{entry}.mp4",
            "duration_seconds": _probe_duration(target),
            "bytes": target.stat().st_size,
            "sha256": copy_digest,
            "metadata_stripped": stripped,
        })
        entry_by_candidate[candidate_id] = entry
        blinding[entry] = {
            "candidate_id": candidate_id,
            "origin": str(candidate.get("origin") or ""),
            "label": str(candidate.get("label") or ""),
            "source_path": str(source),
            "source_sha256": digest,
        }

    entries.sort(
        key=lambda row: _order_key(pack_id, str(
            (blinding.get(row["entry_id"]) or {}).get("candidate_id") or "")))

    packed_references = []
    for index, reference in enumerate(references):
        if not isinstance(reference, Mapping):
            raise BlindReviewError("a reference must be an object")
        label = str(reference.get("label") or "").strip()
        if reference.get("kind") == "file":
            path = Path(str(reference.get("path") or ""))
            if not path.is_file():
                raise BlindReviewError(
                    f"the reference {label or path.name!r} has no file at "
                    f"{path}")
            suffix = path.suffix if len(path.suffix) <= 8 else ""
            copied = directory / "references" / f"reference-{index}{suffix}"
            shutil.copyfile(path, copied)
            packed_references.append({
                "kind": "file",
                "label": label,
                "path": f"references/{copied.name}",
            })
        else:
            text = str(reference.get("text") or "").strip()
            if not text:
                raise BlindReviewError(
                    f"the reference {label or index!r} carries no text")
            packed_references.append({"kind": "text", "label": label,
                                      "text": text})

    pack = {
        "schema": PACK_SCHEMA,
        "pack_id": str(pack_id),
        "brief": {
            "id": str(brief.get("id") or ""),
            "title": str(brief.get("title") or ""),
            "instruction": str(brief.get("instruction") or ""),
            "project_state": brief.get("project_state") or {},
            "acceptance_criteria": [
                {"id": str(row.get("id") or ""),
                 "statement": str(row.get("statement") or "")}
                for row in brief.get("acceptance_criteria") or ()
                if isinstance(row, Mapping)],
        },
        "constraints": [str(item).strip() for item in constraints
                        if str(item).strip()],
        "references": packed_references,
        "rubric": [leg.as_dict() for leg in judgement.REVIEW_LEGS],
        "decisions": list(DECISIONS),
        "entries": entries,
        "blinding": blinding,
        "objective": {
            entry_by_candidate[str(key)]: [dict(row) for row in value
                                           if isinstance(row, Mapping)]
            for key, value in (objective or {}).items()
            if str(key) in entry_by_candidate},
    }
    assert_blind(pack)
    return pack


def reviewer_view(pack: Mapping[str, Any]) -> dict[str, Any]:
    """What a reviewer is shown: the videos, the brief and the rubric.

    No origin, no candidate id, no source path, and no engine measurement:
    a reviewer reads the deliverable and answers the rubric.
    """
    if not isinstance(pack, Mapping):
        raise BlindReviewError("a pack must be an object")
    entries = []
    for row in pack.get("entries") or ():
        if not isinstance(row, Mapping):
            continue
        entries.append({
            "entry_id": str(row.get("entry_id") or ""),
            "media": str(row.get("media") or ""),
            "duration_seconds": row.get("duration_seconds"),
            "bytes": row.get("bytes"),
        })
    return {
        "schema": PACK_SCHEMA,
        "pack_id": str(pack.get("pack_id") or ""),
        "brief": dict(pack.get("brief") or {}),
        "constraints": list(pack.get("constraints") or ()),
        "references": [dict(row) for row in pack.get("references") or ()
                       if isinstance(row, Mapping)],
        "rubric": [dict(leg) for leg in pack.get("rubric") or ()
                   if isinstance(leg, Mapping)],
        "decisions": list(pack.get("decisions") or DECISIONS),
        "entries": entries,
    }


def assert_blind(pack: Mapping[str, Any]) -> None:
    """Refuse a pack whose reviewer-facing view names a producer.

    The check runs over the WHOLE reviewer view - the brief, the
    constraints, the references and the entries - because a leak is a
    leak wherever it sits, and a pack that leaks in its references is no
    more blind than one that leaks in its entries.
    """
    view = reviewer_view(pack)
    text = json.dumps(view, sort_keys=True, ensure_ascii=False)
    blinding = pack.get("blinding") or {}
    if not isinstance(blinding, Mapping):
        return
    problems = []
    for entry_id, secret_row in blinding.items():
        if not isinstance(secret_row, Mapping):
            continue
        candidate = {
            "candidate_id": secret_row.get("candidate_id"),
            "origin": secret_row.get("origin"),
            "label": secret_row.get("label"),
            "path": secret_row.get("source_path"),
        }
        for secret in _candidate_secrets(candidate):
            if _leaks(text, [secret]):
                problems.append(
                    f"the reviewer-facing view names {secret!r}, which "
                    f"would tell the reviewer which candidate {entry_id} is")
    if problems:
        raise BlindReviewError(
            "the pack is not blind:\n  " + "\n  ".join(problems))


# ── the form the reviewer fills ────────────────────────────────────────

_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blind editorial review</title>
<style>body{font:16px system-ui;max-width:960px;margin:2rem auto;padding:0 1rem;
color:#17202a}article{border:1px solid #ccd3da;border-radius:10px;padding:1rem;
margin:1rem 0}video{width:100%;max-height:460px;background:#111}
label{display:block;margin:.45rem 0}textarea{width:100%;min-height:3rem}
fieldset{border:1px solid #dfe4e9;border-radius:8px;margin:.75rem 0}
legend{font-weight:600}input[type=text]{width:22rem;max-width:100%}
.muted{color:#52616b}.leg{border-top:1px solid #eef1f4;padding-top:.5rem}
button{padding:.55rem 1rem;margin:.5rem}</style>
<h1>Blind editorial review</h1>
<p class="muted">You are reviewing delivered videos. Which candidate or model
produced each one is not shown and is not asked about. Watch each video
against the brief, then answer every leg of the rubric. A leg you could not
judge is <code>not_measured</code> - that is a real answer, and leaving it
blank is not.</p>
<label>Reviewer <input id="reviewer" type="text" required></label>
<section id="brief"></section>
<section id="items"></section>
<button id="save">Save answers JSON</button>
<script>
const kit=__MANIFEST__,answers={};
const brief=document.querySelector('#brief');
brief.innerHTML='<h2>The brief</h2><div class="muted"></div>';
brief.querySelector('.muted').textContent=kit.brief.instruction||'';
if((kit.brief.acceptance_criteria||[]).length){
  const ul=document.createElement('ul');
  for(const c of kit.brief.acceptance_criteria){
    const li=document.createElement('li');li.textContent=c.statement;ul.append(li);}
  brief.append(ul);}
if((kit.constraints||[]).length){
  const h=document.createElement('h3');h.textContent='Constraints';brief.append(h);
  const ul=document.createElement('ul');
  for(const c of kit.constraints){const li=document.createElement('li');
  li.textContent=c;ul.append(li);}brief.append(ul);}
if((kit.references||[]).length){
  const h=document.createElement('h3');h.textContent='Reference material';
  brief.append(h);const ul=document.createElement('ul');
  for(const r of kit.references){const li=document.createElement('li');
  li.textContent=(r.label?r.label+': ':'')+(r.kind==='file'?r.path:r.text);
  ul.append(li);}brief.append(ul);}
const root=document.querySelector('#items');
for(const e of kit.entries){
  const a=document.createElement('article');
  a.innerHTML=`<h2>${e.entry_id}</h2><video controls preload="metadata"
  src="${e.media}"></video><div class="legs"></div>`;
  const box=a.querySelector('.legs');
  for(const leg of kit.rubric){
    const d=document.createElement('div');d.className='leg';
    d.innerHTML=`<h3></h3><fieldset><legend>Judgement</legend></fieldset>
    <label>Rationale <textarea class="rationale"></textarea></label>
    <label>Evidence artifact <input type="text" class="artifact"></label>
    <label>Evidence observation <textarea class="observation"></textarea></label>
    <label>Uncertainty
      <select class="level"><option value="none">none</option>
      <option value="low">low</option><option value="medium">medium</option>
      <option value="high">high</option></select></label>
    <label>What could change this <textarea class="unote"></textarea></label>`;
    d.querySelector('h3').textContent=leg.statement||leg.id;
    const fs=d.querySelector('fieldset');
    for(const v of ['pass','fail','not_measured']){
      const l=document.createElement('label');
      l.innerHTML=`<input type="radio" name="${e.entry_id}|${leg.id}"
      value="${v}"> ${v}`;fs.append(l);}
    box.append(d);}
  const fs=document.createElement('fieldset');
  fs.innerHTML='<legend>Overall acceptance decision</legend>';
  for(const v of kit.decisions){
    const l=document.createElement('label');
    l.innerHTML=`<input type="radio" name="${e.entry_id}|decision"
    value="${v}"> ${v}`;fs.append(l);}
  a.append(fs);
  const n=document.createElement('label');
  n.innerHTML='Notes <textarea class="notes"></textarea>';a.append(n);
  root.append(a);
}
document.querySelector('#save').onclick=()=>{
  const reviewer=document.querySelector('#reviewer').value.trim();
  if(!reviewer) return alert('Enter the reviewer name or identifier.');
  const entries=[];
  for(const e of kit.entries){
    const legs=[];
    for(const leg of kit.rubric){
      const picked=document.querySelector(
        `input[name="${e.entry_id}|${leg.id}"]:checked`);
      const box=document.querySelector(`[name="${e.entry_id}|${leg.id}"]`)
        .closest('.leg');
      const observation=box.querySelector('.observation').value.trim();
      const artifact=box.querySelector('.artifact').value.trim();
      legs.push({leg:leg.id,status:picked?picked.value:'',
        rationale:box.querySelector('.rationale').value.trim(),
        evidence:artifact||observation?
          [{artifact:artifact||e.entry_id,observation:observation}]:[],
        uncertainty:{level:box.querySelector('.level').value,
          note:box.querySelector('.unote').value.trim()}});
    }
    const decided=document.querySelector(
      `input[name="${e.entry_id}|decision"]:checked`);
    const article=document.querySelector(`[name="${e.entry_id}|decision"]`)
      .closest('article');
    entries.push({entry_id:e.entry_id,legs:legs,
      decision:decided?decided.value:'',
      notes:article.querySelector('.notes').value.trim()});
  }
  const blob=new Blob([JSON.stringify({schema:'__SCHEMA__',
    pack_id:kit.pack_id,reviewer:reviewer,entries:entries},null,2)],
    {type:'application/json'});
  const u=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=u;a.download='blind-review-answers.json';a.click();
  URL.revokeObjectURL(u);};
</script></html>"""


def render_form(pack: Mapping[str, Any]) -> str:
    """The page a reviewer reads, with no origin in it.

    The page is self-contained: no external scripts, stylesheets, fonts or
    network calls. It shows the brief, the constraints, the reference
    material, each video and the rubric, and it writes the answers file.
    """
    view = reviewer_view(pack)
    payload = json.dumps(view, ensure_ascii=False).replace("</", "<\\/")
    return (_PAGE.replace("__MANIFEST__", payload)
            .replace("__SCHEMA__", ANSWERS_SCHEMA))


# ── the answers a reviewer brings back ─────────────────────────────────


def read_answers(data: Mapping[str, Any]) -> dict[str, Any]:
    """Read one reviewer's answers file, and refuse what is not one.

    The shape is checked here; the REVIEW itself is checked by
    `judgement.review`, which grades a reviewer's rows exactly as it
    grades an agent's, so the two cannot drift apart.
    """
    if not isinstance(data, Mapping):
        raise BlindReviewError("an answers file must be an object")
    if data.get("schema") != ANSWERS_SCHEMA:
        raise BlindReviewError(
            f"the answers schema must be {ANSWERS_SCHEMA!r}, not "
            f"{data.get('schema')!r}")
    reviewer = str(data.get("reviewer") or "").strip()
    if not reviewer:
        raise BlindReviewError("an answers file names no reviewer")
    rows = data.get("entries")
    if not isinstance(rows, list) or not rows:
        raise BlindReviewError("an answers file answers no entry")
    entries = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise BlindReviewError(f"entries[{index}] must be an object")
        entry_id = str(row.get("entry_id") or "").strip()
        if not entry_id:
            raise BlindReviewError(f"entries[{index}] names no entry")
        legs = row.get("legs")
        if not isinstance(legs, list):
            raise BlindReviewError(
                f"entries[{index}] carries no legs: the rubric is answered "
                "leg by leg")
        decision = str(row.get("decision") or "").strip()
        if decision and decision not in DECISIONS:
            raise BlindReviewError(
                f"entries[{index}] has the decision {decision!r}, which is "
                "not one of " + ", ".join(DECISIONS))
        entries.append({
            "entry_id": entry_id,
            "legs": [dict(leg) for leg in legs if isinstance(leg, Mapping)],
            "decision": decision,
            "notes": str(row.get("notes") or ""),
        })
    return {
        "schema": ANSWERS_SCHEMA,
        "pack_id": str(data.get("pack_id") or ""),
        "reviewer": reviewer,
        "entries": entries,
    }


# ── the results record ─────────────────────────────────────────────────


def _panel_rows(reviewers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The reviewers' rows, in the shape `judgement.panel` reads."""
    rows = []
    for reviewer in reviewers:
        legs = [leg for leg in reviewer.get("legs") or ()
                if leg.get("leg")]
        rows.append({
            "judge": reviewer.get("reviewer"),
            "editorial": {"criteria": [
                {"id": leg["leg"], "status": leg.get("status")}
                for leg in legs]},
        })
    return rows


def _standing(decisions: Sequence[str]) -> tuple[str, str]:
    """What the panel's reading of one candidate amounts to.

    Two reviewers who agree give an answer. Two who do not have NOT
    established one, and the record says `disagreement` - the average of
    two opinions is not an opinion anybody held, and the more confident
    of the two is not the truth.
    """
    named = [decision for decision in decisions if decision]
    if not named:
        return STANDING_DISAGREEMENT, (
            "no reviewer made an acceptance decision about this candidate")
    unique = sorted(set(named))
    if len(unique) == 1:
        return unique[0], (
            f"{len(named)} reviewer(s) decided the same way")
    return STANDING_DISAGREEMENT, (
        "the reviewers did not agree: " + ", ".join(
            f"{decision} ({named.count(decision)} reviewer(s))"
            for decision in unique))


def results(pack: Mapping[str, Any],
             answers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The results record: every reviewer's reading, and the disagreement.

    Nothing is averaged into a single number the readers did not write.
    Each reviewer's rows, score and decision are kept, the legs they
    agreed on and the legs they did not are named, and a candidate they
    split on is `disagreement` rather than a verdict.
    """
    if not isinstance(pack, Mapping):
        raise BlindReviewError("a pack must be an object")
    entries = [row for row in pack.get("entries") or ()
               if isinstance(row, Mapping)]
    blinding = pack.get("blinding") or {}
    objective = pack.get("objective") or {}
    brief = pack.get("brief") or {}

    parsed = []
    for data in answers:
        record = read_answers(data)
        pack_id = str(record.get("pack_id") or "")
        if pack_id and pack_id != str(pack.get("pack_id") or ""):
            raise BlindReviewError(
                f"the answers file from {record['reviewer']!r} answers "
                f"pack {pack_id!r}, not {pack.get('pack_id')!r}")
        parsed.append(record)

    candidates = []
    for entry in entries:
        entry_id = str(entry.get("entry_id") or "")
        objective_rows = [dict(row)
                          for row in (objective.get(entry_id) or ())
                          if isinstance(row, Mapping)]
        per_reviewer = []
        for record in parsed:
            answered = [row for row in record["entries"]
                        if row["entry_id"] == entry_id]
            if not answered:
                continue
            row = answered[0]
            render = {
                "sha256": str(entry.get("sha256") or ""),
                "evidence": [{"kind": "video",
                              "artifact": str(entry.get("media") or ""),
                              "observation": "the delivered file the "
                                             "reviewer watched"}],
                "duration_seconds": entry.get("duration_seconds") or 0.0,
            }
            try:
                review = judgement.review(
                    task_id=entry_id, brief=brief, render=render,
                    judgments=row["legs"], objective=objective_rows)
            except judgement.ReviewError as exc:
                # One reviewer's rows that do not satisfy the contract do
                # not erase the other reviewers' readings: the refusal is
                # recorded against that reviewer and the others stand.
                per_reviewer.append({
                    "reviewer": record["reviewer"],
                    "review_error": str(exc),
                    "legs": [dict(leg) for leg in row["legs"]],
                    "subjective_score": None,
                    "review_verdict": "not_measured",
                    "review_verdict_reason": str(exc),
                    "decision": row["decision"],
                    "notes": row["notes"],
                })
                continue
            per_reviewer.append({
                "reviewer": record["reviewer"],
                "review_error": None,
                "legs": review["subjective"],
                "subjective_score": review["subjective_score"],
                "review_verdict": review["verdict"],
                "review_verdict_reason": review["verdict_reason"],
                "decision": row["decision"],
                "notes": row["notes"],
            })
        panel = judgement.panel(_panel_rows(per_reviewer))
        standing, reason = _standing(
            [row["decision"] for row in per_reviewer])
        decisions = {row["reviewer"]: row["decision"]
                     for row in per_reviewer}
        candidates.append({
            "entry_id": entry_id,
            "candidate_id": str((blinding.get(entry_id) or {}).get(
                "candidate_id") or ""),
            "origin": str((blinding.get(entry_id) or {}).get("origin") or ""),
            "objective_rows": objective_rows,
            "objective_measured": sum(
                1 for row in objective_rows
                if row.get("status") != "not_measured"),
            "objective_failures": [row["leg"] for row in objective_rows
                                   if row.get("status") == "fail"],
            "reviewers": per_reviewer,
            "reviewer_count": len(per_reviewer),
            "panel": panel,
            "leg_disagreements": panel["disagreements"],
            "decisions": decisions,
            "standing": standing,
            "standing_reason": reason,
        })

    answered_entries = sum(
        candidate["reviewer_count"] for candidate in candidates)
    possible_entries = len(candidates) * len(parsed)
    decision_counts = {decision: 0 for decision in DECISIONS}
    for candidate in candidates:
        for decision in candidate["decisions"].values():
            if decision in decision_counts:
                decision_counts[decision] += 1
    leg_rows = sum(len(leg)
                   for candidate in candidates
                   for row in candidate["reviewers"]
                   for leg in row["legs"])
    disagreed_legs = sum(len(candidate["leg_disagreements"])
                         for candidate in candidates)
    return {
        "schema": RESULTS_SCHEMA,
        "pack_id": str(pack.get("pack_id") or ""),
        "rubric": [dict(leg) for leg in pack.get("rubric") or ()],
        "reviewers": [record["reviewer"] for record in parsed],
        "candidates": candidates,
        "counts": {
            "candidate_count": len(candidates),
            "reviewer_count": len(parsed),
            "answers_count": len(parsed),
            "reviews_read": answered_entries,
            "reviews_possible": possible_entries,
            "entries_unanswered": possible_entries - answered_entries,
            "reviews_refused": sum(
                1 for candidate in candidates
                for row in candidate["reviewers"]
                if row.get("review_error")),
            "leg_rows_read": leg_rows,
            "legs_disagreed": disagreed_legs,
            "candidates_disagreed": sum(
                1 for candidate in candidates
                if candidate["standing"] == STANDING_DISAGREEMENT),
        },
        "decision_counts": decision_counts,
        "denominators": {
            "leg_agreement": (
                f"{leg_rows - disagreed_legs}/{leg_rows} leg readings agreed"
                if leg_rows else "0/0 leg readings agreed"),
            "candidate_decision": (
                f"{len(candidates) - sum(1 for candidate in candidates if candidate['standing'] == STANDING_DISAGREEMENT)}"
                f"/{len(candidates)} candidates reached one standing"),
        },
    }
