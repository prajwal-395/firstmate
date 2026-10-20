"""A blind head-to-head: Ren's treatment against a professional editor's.

The gap this closes
-------------------
The blind editorial pilot asks the evaluator ONE question about ONE
proposal: is this decision acceptable?  That cannot say whether REN'S
choice was the better one, because it never shows two treatments side
by side.  A comparison of Ren's cut against a professional editor's
selection needs three things the pilot did not have:

* a comparison the evaluator CANNOT SEE THROUGH - the sides in an order
  that says nothing about which side is Ren's, and an evaluator-facing
  view that carries no origin, no treatment name and no treatment id;
* a way to record what the evaluator chose - a SIDE, never an origin,
  because the evaluator never saw one;
* a way to turn that choice back into a statement about the two
  treatments, and to MEASURE how often Ren's was preferred.

What this is not
----------------
It does not decide which treatment is better.  The evaluator's answer is
the answer; this module's job is to make the answer mean something.  It
also does not judge a delivered render against its brief: that measure
lives in `library/tools/editorial_brief_scorer.py`, which keeps the
editorial verdict separate from the technical gate.

The rules
---------
* **The order says nothing about the origin.**  It is a deterministic
  function of the comparison id, so the same comparison always shows the
  same order and no order is systematically Ren's.
* **The evaluator-facing view names nothing.**  A plan summary that
  mentions either treatment is refused: a description that gives the
  answer away is not a blind comparison.
* **An undecided pair is undecided.**  It is never counted as a loss for
  either side, and a preference for a comparison nobody built is
  reported rather than dropped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

COMPARISON_SCHEMA = "ren-editorial-comparison/1"

# ── The preferences an evaluator may record ───────────────────────────

PREFERRED_FIRST = "first"
PREFERRED_SECOND = "second"
PREFERRED_NEITHER = "neither"
PREFERRED_UNSURE = "unsure"

PREFERENCES = (PREFERRED_FIRST, PREFERRED_SECOND, PREFERRED_NEITHER,
               PREFERRED_UNSURE)

#: Which side each preference names once the blinding is lifted.
REN_PREFERRED = "ren"
PROFESSIONAL_PREFERRED = "professional"
NO_PREFERENCE = "neither"
UNDECIDED = "unsure"


# ── A blind comparison of two treatments ──────────────────────────────


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _side_order(comparison_id: str) -> tuple[str, str]:
    """Which origin each side carries: the order the sides are shown in.

    The order is a deterministic function of the comparison id, so the
    same comparison always shows the same order (a reviewer can repeat
    it).  It is NOT a function of the origin alone: Ren's treatment is
    first about half the time, so an evaluator who watched many
    comparisons could not learn "the first side is the one Ren chose".
    """
    flipped = int(_digest(comparison_id)[:8], 16) % 2 == 1
    return ("professional", "ren") if flipped else ("ren", "professional")


def comparison(request_id: str, request: str, ren_treatment: Mapping[str, Any],
               professional_treatment: Mapping[str, Any], *,
               criteria: Sequence[Mapping[str, Any]] = (),
               comparison_id: str = "") -> dict[str, Any]:
    """Construct a blind comparison of Ren's treatment and a professional's.

    Each treatment carries an id, a plan summary the evaluator may read,
    and the criteria it was judged against.  The record holds the
    blinding map beside the sides; `evaluator_view` is what the evaluator
    sees, and it carries neither.
    """
    if not str(request_id or "").strip():
        raise ValueError("request_id is required")
    if not str(request or "").strip():
        raise ValueError("a comparison needs the request it compares against")
    for label, treatment in (("ren", ren_treatment),
                             ("professional", professional_treatment)):
        if not isinstance(treatment, Mapping):
            raise TypeError(f"the {label} treatment must be an object")
        if not str(treatment.get("treatment_id") or "").strip():
            raise ValueError(f"the {label} treatment needs a treatment_id")
        if not str(treatment.get("plan_summary") or "").strip():
            raise ValueError(
                f"the {label} treatment needs a plan summary the evaluator "
                "can read")
    identifier = str(comparison_id or (
        f"{request_id}:{_digest([ren_treatment.get('treatment_id'),
                               professional_treatment.get('treatment_id')])[:12]}"))
    first, second = _side_order(identifier)
    sides = {
        "ren": dict(ren_treatment),
        "professional": dict(professional_treatment),
    }
    return {
        "schema": COMPARISON_SCHEMA,
        "comparison_id": identifier,
        "request_id": str(request_id),
        "request": str(request),
        "criteria": [dict(item) for item in criteria
                     if isinstance(item, Mapping)],
        "sides": [
            {"side": "first", "origin": first, "treatment": sides[first]},
            {"side": "second", "origin": second, "treatment": sides[second]},
        ],
        "blinding": {"first": first, "second": second},
    }


def evaluator_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """What the evaluator is shown.

    The view carries the request, the criteria and each side's plan
    summary and per-criterion verdicts.  It carries no origin, no
    treatment id and no treatment name, so nothing in it says which side
    is Ren's.
    """
    if not isinstance(record, Mapping):
        raise TypeError("a comparison must be an object")
    rows = list(record.get("sides") or ())
    # Nothing in an evaluator-facing view may name EITHER treatment: a
    # summary that mentions the other side's id names both.
    secrets = set()
    for row in rows:
        treatment = row.get("treatment") or {}
        for key in ("treatment_id", "name"):
            value = str(treatment.get(key) or "")
            if value:
                secrets.add(value)
    sides = []
    for row in rows:
        treatment = row.get("treatment") or {}
        summary = str(treatment.get("plan_summary") or "")
        for secret in sorted(secrets):
            if secret in summary:
                raise ValueError(
                    f"the plan summary names {secret!r}, which would tell the "
                    "evaluator which side this is")
        sides.append({
            "side": str(row.get("side") or ""),
            "plan_summary": summary,
            "evaluation": [dict(item)
                           for item in treatment.get("evaluation") or ()
                           if isinstance(item, Mapping)],
        })
    return {
        "schema": COMPARISON_SCHEMA,
        "comparison_id": str(record.get("comparison_id") or ""),
        "request": str(record.get("request") or ""),
        "criteria": [dict(item) for item in record.get("criteria") or ()
                     if isinstance(item, Mapping)],
        "sides": sides,
    }


def unblind(record: Mapping[str, Any], preference: str) -> dict[str, Any]:
    """Turn the evaluator's preference into a statement about the two.

    The preference names the SIDE the evaluator chose, not the origin.
    Unblinding is where the two meet, and it is recorded: which side was
    preferred, which treatment that was, and whether Ren's treatment was
    the one preferred.
    """
    if preference not in PREFERENCES:
        raise ValueError(
            f"unknown preference {preference!r}; known preferences: "
            f"{list(PREFERENCES)}")
    sides = {str(row.get("side")): row
             for row in record.get("sides") or ()}
    if preference in (PREFERRED_FIRST, PREFERRED_SECOND):
        side = sides.get(preference)
        if side is None:
            raise ValueError(f"the comparison has no {preference!r} side")
        origin = str(side.get("origin") or "")
    else:
        origin = ""
    return {
        "comparison_id": str(record.get("comparison_id") or ""),
        "request_id": str(record.get("request_id") or ""),
        "preference": preference,
        "preferred": (REN_PREFERRED if origin == "ren"
                      else PROFESSIONAL_PREFERRED if origin == "professional"
                      else NO_PREFERENCE if preference == PREFERRED_NEITHER
                      else UNDECIDED),
        "ren_chosen": origin == "ren",
    }


def agreement(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How often Ren's treatment was preferred over the professional's.

    This is the editorial utility of a blind comparison, and it is NOT a
    quality score: a pair the evaluator could not decide on is reported
    as undecided, never as a loss for either side.
    """
    counts = {REN_PREFERRED: 0, PROFESSIONAL_PREFERRED: 0, NO_PREFERENCE: 0,
              UNDECIDED: 0}
    for record in records:
        preference = str(record.get("preference") or "")
        if preference not in PREFERENCES:
            continue
        origin = ""
        if preference in (PREFERRED_FIRST, PREFERRED_SECOND):
            for side in record.get("sides") or ():
                if str(side.get("side")) == preference:
                    origin = str(side.get("origin") or "")
        preferred = (REN_PREFERRED if origin == "ren"
                     else PROFESSIONAL_PREFERRED if origin == "professional"
                     else NO_PREFERENCE if preference == PREFERRED_NEITHER
                     else UNDECIDED)
        counts[preferred] += 1
    decided = counts[REN_PREFERRED] + counts[PROFESSIONAL_PREFERRED]
    return {
        "comparison_count": sum(counts.values()),
        "counts": counts,
        "decided_count": decided,
        "ren_preferred_rate": (counts[REN_PREFERRED] / decided
                               if decided else None),
        "undecided_count": counts[UNDECIDED] + counts[NO_PREFERENCE],
        "method": ("a blind head-to-head: the evaluator chose a side "
                   "without knowing which side was Ren's"),
    }


# ── The page the evaluator reads ──────────────────────────────────────

ANSWERS_SCHEMA = "ren-editorial-comparison-answers-v1"

_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Editorial comparison</title>
<style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;
color:#17202a}article{border:1px solid #ccd3da;border-radius:10px;padding:1rem;
margin:1rem 0}.sides{display:flex;gap:1rem;flex-wrap:wrap}.side{flex:1 1 20rem;
min-width:0;border:1px solid #dfe4e9;border-radius:8px;padding:.75rem}
label{display:block;margin:.4rem 0}.muted{color:#52616b}</style>
<h1>Editorial comparison</h1>
<p class="muted">Two treatments of the same request. Judge which one you would
keep. Neither side is labelled and the order is not the order they were made
in. Leave a note if you want to say why.</p>
<label>Evaluator <input id="labeller" required></label>
<section id="items"></section>
<button id="save">Save answers JSON</button>
<script>
const kit=__MANIFEST__,choices={};
const root=document.querySelector('#items');
for(const c of kit.comparisons){
  const a=document.createElement('article');
  a.innerHTML=`<h2></h2><p class="muted request"></p><div class="sides"></div>
  <label>Note <textarea class="note"></textarea></label>`;
  a.querySelector('h2').textContent=c.request;
  a.querySelector('.request').textContent=c.criteria.length
    ? 'Judge against: '+c.criteria.map(x=>x.criterion).join('; ')
    : 'Judge against the request.';
  const box=a.querySelector('.sides');
  for(const s of c.sides){
    const d=document.createElement('div');d.className='side';
    d.innerHTML=`<h3></h3><p class="plan"></p><ul class="verdicts"></ul>`;
    d.querySelector('h3').textContent=s.side==='first'?'First treatment':'Second treatment';
    d.querySelector('.plan').textContent=s.plan_summary;
    const ul=d.querySelector('.verdicts');
    for(const e of s.evaluation){
      const li=document.createElement('li');
      li.textContent=e.criterion+': '+e.verdict+(e.reason?' - '+e.reason:'');
      ul.append(li);
    }
    box.append(d);
  }
  const fs=document.createElement('fieldset');
  fs.innerHTML='<legend>Which treatment would you keep?</legend>';
  for(const v of ['first','second','neither','unsure']){
    const l=document.createElement('label');
    l.innerHTML=`<input type="radio" name="${c.comparison_id}" value="${v}"> ${v}`;
    fs.append(l);
  }
  a.append(fs);
  a.querySelectorAll('input[type=radio]').forEach(r=>r.onchange=()=>{
    choices[c.comparison_id]=r.value;});
  a.querySelector('.note').oninput=()=>{
    choices[c.comparison_id]={preference:choices[c.comparison_id],
      note:a.querySelector('.note').value};};
  root.append(a);
}
document.querySelector('#save').onclick=()=>{
  const labeller=document.querySelector('#labeller').value.trim();
  if(!labeller) return alert('Enter the evaluator name or identifier.');
  const rows=kit.comparisons.map(c=>{
    const v=choices[c.comparison_id];
    const pref=typeof v==='object'?v.preference:v;
    const note=typeof v==='object'?v.note:'';
    return {comparison_id:c.comparison_id,preference:pref||'',note:note||''};
  });
  const blob=new Blob([JSON.stringify({schema:'__SCHEMA__',labeller,choices:rows},
    null,2)],{type:'application/json'});
  const u=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=u;a.download='editorial-comparison-answers.json';a.click();
  URL.revokeObjectURL(u);};
</script></html>"""


def render_evaluator_page(records: Sequence[Mapping[str, Any]]) -> str:
    """The page an evaluator reads, with no origin in it.

    The page is self-contained: no external scripts, stylesheets, fonts
    or network calls.  It shows each request, both sides' plan summaries
    and their per-criterion verdicts, and asks which treatment the
    evaluator would keep.
    """
    comparisons = []
    for record in records:
        view = evaluator_view(record)
        comparisons.append({
            "comparison_id": view["comparison_id"],
            "request": view["request"],
            "criteria": view["criteria"],
            "sides": view["sides"],
        })
    payload = json.dumps({"comparisons": comparisons},
                          ensure_ascii=False).replace("</", "<\\/")
    return (_PAGE.replace("__MANIFEST__", payload)
            .replace("__SCHEMA__", ANSWERS_SCHEMA))


def read_preferences(answers: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The preferences an evaluator recorded, with their notes.

    The preference is a SIDE, never an origin: the answer file the
    evaluator hands back cannot say which side was Ren's, because the
    evaluator never saw it.
    """
    if not isinstance(answers, Mapping):
        raise TypeError("an answer file must be an object")
    if answers.get("schema") != ANSWERS_SCHEMA:
        raise ValueError(f"answers schema must be {ANSWERS_SCHEMA!r}")
    labeller = answers.get("labeller")
    if not isinstance(labeller, str) or not labeller.strip():
        raise ValueError("an answer file needs the evaluator's name")
    rows = answers.get("choices")
    if not isinstance(rows, list):
        raise TypeError("choices must be an array")
    preferences = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TypeError(f"choices[{index}] must be an object")
        preference = str(row.get("preference") or "")
        if preference and preference not in PREFERENCES:
            raise ValueError(
                f"choices[{index}] has an invalid preference {preference!r}")
        preferences.append({
            "comparison_id": str(row.get("comparison_id") or ""),
            "preference": preference,
            "note": str(row.get("note") or ""),
        })
    return preferences


def score_preferences(records: Sequence[Mapping[str, Any]],
                      preferences: Sequence[Mapping[str, Any]]
                      ) -> dict[str, Any]:
    """Unblind every recorded preference and measure the agreement.

    The comparison and the answer file are joined by the comparison id;
    a preference for a comparison nobody constructed is reported, not
    dropped, because a silent drop would understate the measurement.
    """
    by_id = {str(row.get("comparison_id") or ""): row for row in records}
    results = []
    paired = []
    unmatched = []
    for preference in preferences:
        comparison_id = str(preference.get("comparison_id") or "")
        record = by_id.get(comparison_id)
        if record is None:
            unmatched.append(comparison_id)
            continue
        choice = str(preference.get("preference") or "")
        if not choice:
            continue
        unblinded = unblind(record, choice)
        unblinded["note"] = str(preference.get("note") or "")
        results.append(unblinded)
        paired.append({**dict(record), "preference": choice})
    return {
        "agreement": agreement(paired),
        "results": results,
        "unmatched_comparison_ids": unmatched,
    }
