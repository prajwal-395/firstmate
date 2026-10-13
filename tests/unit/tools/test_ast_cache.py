from library.tools import ast_cache


def test_file_parse_is_reused_and_content_changes_invalidate(tmp_path):
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = ast_cache.parse_count()

    first = ast_cache.parse_file(source)
    assert ast_cache.parse_file(source) is first
    assert ast_cache.parse_count() == before + 1

    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = ast_cache.parse_file(source)
    assert second is not first
    assert ast_cache.parse_count() == before + 2
