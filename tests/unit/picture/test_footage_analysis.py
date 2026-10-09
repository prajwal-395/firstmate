import json

from library.tools import footage_analysis


def test_json_mode_sends_progress_to_stderr(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(footage_analysis, "resolve_target",
                        lambda *_args: (str(project), False))

    def analyze(*_args, **_kwargs):
        print("analysis progress")
        return {"status": "complete"}

    monkeypatch.setattr(footage_analysis, "analyze", analyze)

    assert footage_analysis.main([str(project), "--json"]) == 0
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {"status": "complete"}
    assert captured.err == "analysis progress\n"


def test_old_expression_classifier_output_is_stale(tmp_path):
    from library.tools import source_memory

    digest = "expression-method-version"
    sdir = source_memory.source_dir(digest, tmp_path)
    sdir.mkdir(parents=True)
    source_memory.write_json(
        sdir / source_memory.SLOT_PERSONS,
        {"content_digest": digest})
    source_memory.write_json(
        sdir / source_memory.SLOT_FRAMES_INDEX,
        {"content_digest": digest})
    source_memory.write_json(
        sdir / source_memory.SLOT_EXPRESSIONS,
        {"content_digest": digest,
         "instrument": {"method": "expression-classifier-v1"}})
    lane = next(item for item in footage_analysis.LANES
                if item.name == "expressions")

    assert not footage_analysis.slot_is_fresh(lane, digest, tmp_path)
