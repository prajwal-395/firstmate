"""Recallable skills: reusable checks a step can call, that the model is told about.

A skill is one directory under `library/skills/`, with a `SKILL.md`
written for a model deciding whether to call it (what it is, WHEN to
reach for it, what it costs, what it returns) plus an entry point the
model - or the pipeline on its behalf - can invoke.

`library/tools/pipeline_skills.py` is the single owner of what skills
exist and how they are rendered into a prompt. A directory here with no
row there reaches no prompt, and a row there with no directory here is
refused - either direction fails loudly rather than going quiet.
"""
