import xml.etree.ElementTree as ET

from library.tools.junit_lanes import merge_reports


def _write_report(path, cases):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="pytest")
    for classname, name, outcome, message in cases:
        case = ET.SubElement(
            suite, "testcase", classname=classname, name=name, time="0.1")
        if outcome:
            ET.SubElement(case, outcome, message=message).text = message
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_call_failure_and_teardown_error_in_one_report_merge_as_one_test(
        tmp_path):
    report = tmp_path / "lane.xml"
    output = tmp_path / "merged.xml"
    nodeid = ("tests.unit.example", "test_failure")
    _write_report(report, [
        (*nodeid, "failure", "call failed"),
        (*nodeid, "error", "teardown failed"),
    ])

    ok, detail = merge_reports([str(report)], str(output))

    assert ok, detail
    merged = ET.parse(output).getroot()
    suite = merged.find("testsuite")
    assert suite is not None
    assert suite.get("tests") == "1"
    assert suite.get("failures") == "1"
    assert suite.get("errors") == "1"
    cases = suite.findall("testcase")
    assert len(cases) == 1
    assert cases[0].find("failure").text == "call failed"
    assert cases[0].find("error").text == "teardown failed"


def test_same_nodeid_in_different_reports_still_refuses(tmp_path):
    first = tmp_path / "first.xml"
    second = tmp_path / "second.xml"
    output = tmp_path / "merged.xml"
    cases = [("tests.unit.example", "test_overlap", "", "")]
    _write_report(first, cases)
    _write_report(second, cases)

    ok, detail = merge_reports([str(first), str(second)], str(output))

    assert not ok
    assert "duplicate nodeid tests.unit.example::test_overlap" in detail

