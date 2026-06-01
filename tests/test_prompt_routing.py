from standard_document_assistant.prompts import EXTRACTOR_PROMPT, MAIN_SYSTEM_PROMPT


def test_metadata_extraction_routing_avoids_prefetch() -> None:
    assert "不要先 read_file 源文档" in MAIN_SYSTEM_PROMPT
    assert "不要读取 extraction skill" in MAIN_SYSTEM_PROMPT
    assert "不要 write_todos" in MAIN_SYSTEM_PROMPT
    assert "直接用 task 委派 extractor" in MAIN_SYSTEM_PROMPT
    assert "不要先 read_file 全文" in EXTRACTOR_PROMPT
    assert "直接调用 extract_standard_metadata" in EXTRACTOR_PROMPT
