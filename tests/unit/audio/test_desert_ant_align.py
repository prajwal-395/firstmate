"""Unit tests for `library/tools/desert_ant_align.py`.

The Node driver is faked at `_run_driver`: no node, no package, no
model. What is exercised is the wrapper's contract - support reporting,
passthrough visibility, refusals, and PCM handling.
"""
import json
import struct
import wave

import pytest

from library.tools import desert_ant_align


def _words():
    return [{"text": "hello", "start": 0.40, "end": 0.71, "speaker": "a"},
            {"text": "world", "start": 0.80, "end": 1.30}]


def _wav(path, seconds=1.0, channels=1):
    frames = int(16000 * seconds)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(struct.pack("<" + "h" * frames, *([100] * frames)))


def test_refine_reports_refined_words_and_support(
        monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    _wav(wav)
    monkeypatch.setattr(desert_ant_align, "_node_binary", lambda node: "node")
    monkeypatch.setattr(desert_ant_align, "_resolve_package_dir",
                        lambda package_dir: "/pkg")

    def _fake(command, timeout):
        assert command[1].endswith(
            desert_ant_align.DRIVER_FILENAME)
        words_path = command[command.index("--words") + 1]
        out_path = command[command.index("--out") + 1]
        sent = json.loads(open(words_path, encoding="utf-8").read())
        assert [w["text"] for w in sent] == ["hello", "world"]
        with open(out_path, "w", encoding="utf-8") as stream:
            json.dump({"language": "en", "supported": True,
                       "sdkVersion": "3.6.0", "downloaded": True,
                       "words": [{**w, "start": w["start"] + 0.012,
                                  "end": w["end"] - 0.022, "refined": True}
                                 for w in sent]}, stream)
        # The refine command leaves its result in the --out file and
        # prints nothing: the driver seam returns stdout verbatim.
        return ""

    monkeypatch.setattr(desert_ant_align, "_run_driver", _fake)
    result = desert_ant_align.refine_words(_words(), str(wav), language="en")
    assert result["support"] == {"language": "en", "supported": True,
                                 "passthrough": False}
    assert result["words"][0]["speaker"] == "a"
    assert result["words"][0]["refined"] is True
    assert result["sdk"]["sdk_version"] == "3.6.0"


def test_refine_reports_passthrough_instead_of_silence(
        monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    _wav(wav)
    monkeypatch.setattr(desert_ant_align, "_node_binary", lambda node: "node")
    monkeypatch.setattr(desert_ant_align, "_resolve_package_dir",
                        lambda package_dir: "/pkg")

    def _fake(command, timeout):
        out_path = command[command.index("--out") + 1]
        with open(out_path, "w", encoding="utf-8") as stream:
            json.dump({"language": "sv", "supported": False,
                       "sdkVersion": "3.6.0", "downloaded": True,
                       "words": [{**w, "refined": False}
                                 for w in _words()]}, stream)
        return ""

    monkeypatch.setattr(desert_ant_align, "_run_driver", _fake)
    result = desert_ant_align.refine_words(_words(), str(wav), language="sv")
    assert result["support"]["supported"] is False
    assert result["support"]["passthrough"] is True
    assert all(w["refined"] is False for w in result["words"])


def test_refine_refuses_without_a_language(tmp_path):
    wav = tmp_path / "window.wav"
    _wav(wav)
    with pytest.raises(desert_ant_align.AlignUnavailable):
        desert_ant_align.refine_words(_words(), str(wav), language="  ")


def test_refine_refuses_an_absent_window(tmp_path):
    with pytest.raises(desert_ant_align.AlignUnavailable):
        desert_ant_align.refine_words(_words(), str(tmp_path / "gone.wav"),
                                      language="en")


def test_refine_refuses_without_a_package(monkeypatch, tmp_path):
    wav = tmp_path / "window.wav"
    _wav(wav)
    monkeypatch.delenv("DESERT_ANT_ALIGN_PACKAGE", raising=False)
    with pytest.raises(desert_ant_align.AlignUnavailable):
        desert_ant_align.refine_words(_words(), str(wav), language="en",
                                      package_dir=str(tmp_path / "no-pkg"))


def test_supported_languages_requires_a_non_empty_list(monkeypatch):
    monkeypatch.setattr(desert_ant_align, "_node_binary", lambda node: "node")
    monkeypatch.setattr(desert_ant_align, "_resolve_package_dir",
                        lambda package_dir: "/pkg")
    monkeypatch.setattr(desert_ant_align, "_run_driver",
                        lambda command, timeout: json.dumps({"languages": []}))
    with pytest.raises(desert_ant_align.AlignUnavailable):
        desert_ant_align.supported_languages()


def test_supported_languages_refuses_non_json_stdout(monkeypatch):
    monkeypatch.setattr(desert_ant_align, "_node_binary", lambda node: "node")
    monkeypatch.setattr(desert_ant_align, "_resolve_package_dir",
                        lambda package_dir: "/pkg")
    monkeypatch.setattr(desert_ant_align, "_run_driver",
                        lambda command, timeout: "not json")
    with pytest.raises(desert_ant_align.AlignUnavailable):
        desert_ant_align.supported_languages()


def test_stereo_input_is_mixed_down_and_recorded(tmp_path):
    wav = tmp_path / "stereo.wav"
    _wav(wav, channels=2)
    samples, rate = desert_ant_align._read_mono_f32(str(wav))
    assert rate == 16000
    assert len(samples) == 8000
