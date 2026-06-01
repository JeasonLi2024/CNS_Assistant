from standard_document_assistant.agent import build_subagents, hitl_enabled
from standard_document_assistant.tools import STANDARD_DOCUMENT_TOOLS


def test_agent_registration_uses_new_tools_only() -> None:
    subagents = build_subagents()
    names = {item["name"] for item in subagents}
    assert "vision_parser" not in names
    parser = next(item for item in subagents if item["name"] == "parser")
    extractor = next(item for item in subagents if item["name"] == "extractor")
    assert [tool.__name__ for tool in parser["tools"]] == ["parse_pdf_with_mineru"]
    def tool_name(tool: object) -> str:
        return getattr(tool, "name", None) or getattr(tool, "__name__", "")

    assert "extract_standard_metadata" in [tool_name(tool) for tool in extractor["tools"]]
    assert {tool.__name__ for tool in STANDARD_DOCUMENT_TOOLS} == {
        "validate_output_schema",
        "propose_memory_update",
    }


def test_hitl_disabled_for_langgraph_server_by_default(monkeypatch) -> None:
    monkeypatch.delenv("STANDARD_DOC_ENABLE_HITL", raising=False)
    monkeypatch.delenv("STANDARD_DOC_DISABLE_HITL", raising=False)
    assert hitl_enabled(langgraph_server=True) is False
    assert hitl_enabled(langgraph_server=False) is True


def test_subagents_omit_interrupt_on_when_hitl_disabled(monkeypatch) -> None:
    monkeypatch.delenv("STANDARD_DOC_ENABLE_HITL", raising=False)
    monkeypatch.delenv("STANDARD_DOC_DISABLE_HITL", raising=False)
    subagents = build_subagents(langgraph_server=True)
    extractor = next(item for item in subagents if item["name"] == "extractor")
    assert "interrupt_on" not in extractor

