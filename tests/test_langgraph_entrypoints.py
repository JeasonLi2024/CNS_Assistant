"""Verify that every graph declared in ``langgraph.json`` can be imported and compiled.

These checks mirror what ``langgraph dev`` and LangGraph Server do at boot:
parse ``langgraph.json``, import the listed modules, and pull the named
symbols. If any of the three graphs (agent / metadata_extraction /
standard_review) is missing or broken, the suite fails loudly.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest


LANGGRAPH_JSON = Path(__file__).resolve().parents[1] / "langgraph.json"


def _load_langgraph_config() -> dict[str, Any]:
    return json.loads(LANGGRAPH_JSON.read_text(encoding="utf-8"))


def _split_target(target: str) -> tuple[str, str]:
    # langgraph.json 形如 "./agent.py:agent" / "./metadata_extraction_graph.py:metadata_extraction"
    cleaned = target.lstrip("./")
    module_path, _, attr = cleaned.partition(":")
    if module_path.endswith(".py"):
        module_path = module_path[: -len(".py")]
    return module_path, attr


@pytest.mark.parametrize("graph_id", ["agent", "metadata_extraction", "standard_review"])
def test_langgraph_json_declares_all_three_graphs(graph_id: str) -> None:
    config = _load_langgraph_config()
    assert graph_id in config["graphs"], f"langgraph.json 缺少 {graph_id} 图"


@pytest.mark.parametrize("graph_id", ["agent", "metadata_extraction", "standard_review"])
def test_graph_target_is_importable_and_runnable(graph_id: str) -> None:
    config = _load_langgraph_config()
    module_path, attr = _split_target(config["graphs"][graph_id])
    module = importlib.import_module(module_path)
    graph = getattr(module, attr)
    assert graph is not None, f"{module_path}:{attr} 为空"
    # 兼容两种导出形式：StateGraph（callable）或预编译的 Pregel。
    invoke = getattr(graph, "invoke", None) or getattr(graph, "ainvoke", None)
    assert invoke is not None, f"{graph_id} 缺少 invoke/ainvoke，类型={type(graph).__name__}"


def test_standard_review_graph_has_expected_nodes() -> None:
    """子图必须包含完整 9 节点拓扑，供 Studio 渲染。"""
    import os
    import sys

    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    project_root = Path(__file__).resolve().parents[1]
    src = project_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from standard_review_graph import standard_review  # noqa: E402

    expected = {
        "ingest",
        "retrieve_rules",
        "judge_rules",
        "quality_gate",
        "widen_review_scope",
        "reload_review_rules",
        "format_review",
        "aggregate",
        "write_outputs",
        "write_manifest",
    }
    nodes = set(standard_review.get_graph().nodes.keys())
    missing = expected - nodes
    assert not missing, f"standard_review 子图缺失节点: {missing}"


def test_metadata_extraction_graph_has_expected_nodes() -> None:
    import os
    import sys

    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    project_root = Path(__file__).resolve().parents[1]
    src = project_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from metadata_extraction_graph import metadata_extraction  # noqa: E402

    nodes = set(metadata_extraction.get_graph().nodes.keys())
    assert len(nodes) >= 3, f"metadata_extraction 子图节点数过少: {nodes}"


def test_config_yaml_covers_standard_review_fields() -> None:
    """配置不能丢字段，否则会回退到 dataclass 默认值，掩盖真实使用。"""
    from dataclasses import fields

    project_root = Path(__file__).resolve().parents[1]
    src = project_root / "src"
    import sys

    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    import yaml  # noqa: E402

    from standard_document_assistant.config import StandardReviewConfig  # noqa: E402

    cfg = yaml.safe_load((project_root / "config.yaml").read_text(encoding="utf-8"))
    review = cfg.get("standard_review") or {}
    declared = set(review.keys())
    expected = {f.name for f in fields(StandardReviewConfig)}
    missing = expected - declared
    # 允许在 config.yaml 中省略 judge_api_key_env / embedding_api_key_env 这类
    # 仅作默认值的派生字段，但剩余字段必须显式出现以便运维审计。
    optional = {"judge_api_key_env", "embedding_api_key_env"}
    must_have = expected - optional
    still_missing = must_have - declared
    assert not still_missing, f"config.yaml 缺少 standard_review 字段: {sorted(still_missing)}"
