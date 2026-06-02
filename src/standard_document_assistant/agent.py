"""Deep Agents factory for the standard document assistant."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from standard_document_assistant.config import build_qwen_model, load_config
from standard_document_assistant.constants import (
    AGENT_NAME,
    DRAFTS_OUTPUT_DIR,
    MEMORIES_DIR,
    MEMORY_NAMESPACE,
    METADATA_OUTPUT_DIR,
    MINERU_OUTPUT_DIR,
    REPORTS_OUTPUT_DIR,
    PROJECT_ROOT,
    SAMPLES_DIR,
    SKILLS_DIR,
    UPLOADS_DIR,
    WORKSPACE_ROOT,
)
from standard_document_assistant.prompts import (
    EXTRACTOR_PROMPT,
    MAIN_SYSTEM_PROMPT,
    PARSER_PROMPT,
    RESEARCH_PROMPT,
    REVIEWER_PROMPT,
    WRITER_PROMPT,
)
from standard_document_assistant.schemas import AgentResult
from standard_document_assistant.tools import (
    build_review_index,
    extract_standard_metadata,
    inspect_review_rules,
    parse_document_with_mineru,
    parse_file_with_mineru,
    run_format_source_review,
    run_standard_review,
    validate_output_schema,
    validate_review_result_schema,
    STANDARD_DOCUMENT_TOOLS,
)


def ensure_project_directories() -> None:
    """Create the official project directories used by the assistant."""

    for path in [
        WORKSPACE_ROOT / "input",
        UPLOADS_DIR,
        SAMPLES_DIR,
        WORKSPACE_ROOT / "output",
        MINERU_OUTPUT_DIR,
        METADATA_OUTPUT_DIR,
        REPORTS_OUTPUT_DIR,
        DRAFTS_OUTPUT_DIR,
        WORKSPACE_ROOT / "tmp",
        WORKSPACE_ROOT / "templates",
        MEMORIES_DIR,
        SKILLS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def _read_memory_seed(relative_path: str) -> str:
    path = MEMORIES_DIR / relative_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def seed_memory_store(store: Any) -> None:
    """Seed StoreBackend memory files from the repository's memories directory."""

    from deepagents.backends.utils import create_file_data

    for name in ["AGENTS.md", "preferences.md", "project-notes.md"]:
        content = _read_memory_seed(name)
        if content:
            store.put(MEMORY_NAMESPACE, f"/memories/{name}", create_file_data(content))


def _memory_namespace_factory(*, langgraph_server: bool = False):
    """Return a StoreBackend namespace factory for long-term memory routing."""

    def _namespace(rt: Any) -> tuple[str, ...]:
        if langgraph_server:
            server_info = getattr(rt, "server_info", None)
            if server_info is not None:
                assistant_id = getattr(server_info, "assistant_id", AGENT_NAME)
                user = getattr(server_info, "user", None)
                user_id = getattr(user, "identity", None) if user is not None else None
                if user_id:
                    return (assistant_id, user_id)
                return (assistant_id,)
        return MEMORY_NAMESPACE

    return _namespace


def _agent_namespace_factory(*, langgraph_server: bool = False):
    """Return an assistant-scoped namespace factory for shared agent assets."""

    def _namespace(rt: Any) -> tuple[str, ...]:
        if langgraph_server:
            server_info = getattr(rt, "server_info", None)
            if server_info is not None:
                return (getattr(server_info, "assistant_id", AGENT_NAME),)
        return (AGENT_NAME,)

    return _namespace


def build_backend(*, langgraph_server: bool = False, store: Any | None = None):
    """Build the CompositeBackend recommended by Deep Agents docs.

    When ``langgraph_server`` is true, ``/memories/`` uses deployment-safe
    namespace scoping and omits host ``FilesystemBackend`` unless explicitly
    enabled for local debugging.
    """

    import os

    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
    
    """
    1. StoreBackend不会自动写回本地 memories/ 目录。
    2. 本地 memories/ 只是 seed/source-of-initial-data（启动时初始化）。
    3. 运行时 /memories/ 是虚拟路径。
    4. 生产持久化靠 StoreBackend + 持久 Store，例如 Postgres。
    5. 是否允许 Agent 直接写 /memories/，由 permissions 和业务审批流程控制；当前设计是“Agent 不直写，只提案，应用层批准后写数据库”。
    6. Memory namespace 在部署时按用户隔离
    """
    routes: dict[str, Any] = {
        "/memories/": StoreBackend(namespace=_memory_namespace_factory(langgraph_server=langgraph_server)),
    } 
    enable_local_skills_backend = (
        not langgraph_server or os.getenv("STANDARD_DOC_ENABLE_LOCAL_SKILLS_BACKEND", "1") == "1"
    )
    if enable_local_skills_backend:
        routes["/skills/"] = FilesystemBackend(
            root_dir=str(SKILLS_DIR.resolve()),
            virtual_mode=True,
        )
    else:
        routes["/skills/"] = StoreBackend(namespace=_agent_namespace_factory(langgraph_server=langgraph_server))

    """
    1. FilesystemBackend让 Agent 能处理真实工作区文件。
    2. 本地运行时直接启用 /workspace/;
    3. 在 langgraph_server=True 的部署模式下，默认关闭，只有显式设置为 1 才启用
    """
    enable_workspace_backend = (
        not langgraph_server or os.getenv("STANDARD_DOC_ENABLE_WORKSPACE_BACKEND", "0") == "1"
    )
    if enable_workspace_backend:
        routes["/workspace/"] = FilesystemBackend(
            root_dir=str(WORKSPACE_ROOT.resolve()),
            virtual_mode=True,
        )

    _ = store  # Reserved for future store-aware backend wiring.
    """
    默认路径走 StateBackend,这类文件存在 graph state / thread state 里。
    1. 作为短期 scratch 空间。
    2. 只在当前 thread 中存在。
    3. 可用于 Deep Agents 自动 offload 大工具结果。
    4. 不污染长期记忆，也不直接写真实磁盘。
    """
    return CompositeBackend(default=StateBackend(), routes=routes)


def build_permissions():
    """
    Build path-level filesystem permissions for built-in Deep Agents file tools.
    1. 原始输入不可被 Agent 覆盖
    2. 产物写入 output/或者tmp/
    3. /memories/ 只读；更新走 propose_memory_update + HITL
    """

    from deepagents import FilesystemPermission

    return [
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/.env*", "/workspace/**/.env*", "/workspace/**/*secret*"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/workspace/input/**", "/workspace/templates/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/workspace/input/**", "/workspace/templates/**"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/output/**", "/workspace/tmp/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/skills/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/skills/**"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/memories/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/memories/**"],
            mode="deny",
        ),
    ]


def hitl_enabled(*, langgraph_server: bool = False) -> bool:
    """Whether HumanInTheLoopMiddleware is enabled for main and sub agents.

  LangGraph Studio 对子图 HITL 的 resume 需传入 ``{"decisions": [...]}`` 或按
  interrupt id 映射；空字符串或裸 ``"approve"`` 会触发
  ``TypeError: string indices must be integers, not 'str'``。本地 ``langgraph dev``
  默认关闭 HITL；生产/API 可设置 ``STANDARD_DOC_ENABLE_HITL=1``。
    """

    disable = os.getenv("STANDARD_DOC_DISABLE_HITL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if disable:
        return False
    enable = os.getenv("STANDARD_DOC_ENABLE_HITL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if enable:
        return True
    return not langgraph_server


def build_subagents(*, langgraph_server: bool = False) -> list[dict[str, Any]]:
    """Build specialized subagent specs."""

    review_skill = "/skills/standard-review"
    drafting_skill = "/skills/standard-drafting"
    extraction_skill = "/skills/standard-extraction"
    parsing_skill = "/skills/standard-parsing"
    use_hitl = hitl_enabled(langgraph_server=langgraph_server)
    parser_spec: dict[str, Any] = {
        "name": "parser",
        "description": "解析用户上传的 PDF 或 Word 标准文档，调用 MinerU 生成 Markdown、图片、JSON 和 manifest。",
        "system_prompt": PARSER_PROMPT,
        "tools": [parse_file_with_mineru],
        "skills": [parsing_skill],
    }
    if use_hitl:
        parser_spec["interrupt_on"] = {"parse_file_with_mineru": True}

    extractor_spec: dict[str, Any] = {
        "name": "extractor",
        "description": "从 Markdown 标准文档中抽取国标元数据字段，生成结构化 JSON 和 manifest。",
        "system_prompt": EXTRACTOR_PROMPT,
        "tools": [extract_standard_metadata, validate_output_schema],
        "skills": [extraction_skill],
    }
    if use_hitl:
        extractor_spec["interrupt_on"] = {"extract_standard_metadata": True}

    reviewer_spec: dict[str, Any] = {
        "name": "reviewer",
        "description": "执行标准文档内容轨和格式轨审核，调用标准审核工具写入报告、结果、trace 和 manifest。",
        "system_prompt": REVIEWER_PROMPT,
        "tools": [
            parse_document_with_mineru,
            run_standard_review,
            run_format_source_review,
            inspect_review_rules,
            build_review_index,
            validate_review_result_schema,
        ],
        "skills": [review_skill],
    }
    if use_hitl:
        reviewer_spec["interrupt_on"] = {
            "parse_document_with_mineru": True,
            "run_standard_review": True,
            "run_format_source_review": True,
            "build_review_index": True,
        }

    return [
        parser_spec,
        extractor_spec,
        reviewer_spec,
        {
            "name": "research",
            "description": "基于已提供材料和模板做参考资料梳理；正式检索工具后续单独接入。",
            "system_prompt": RESEARCH_PROMPT,
            "tools": [],
        },
        {
            "name": "writer",
            "description": "根据用户需求、参考资料和模板生成标准文档 Markdown 草稿；使用内置文件工具写入草稿。",
            "system_prompt": WRITER_PROMPT,
            "tools": [validate_output_schema],
            "skills": [drafting_skill],
        },
    ]


def build_standard_document_agent(*, strict_model: bool = False, langgraph_server: bool = False):
    """Create the Deep Agent graph for the standard document assistant.

    Args:
        strict_model: When true, fail if ChatQwen or DASHSCOPE_API_KEY is missing.
        langgraph_server: When true, omit local checkpointer/store so LangGraph Server
            can inject platform persistence for ``langgraph dev`` and deployment.

    Returns:
        A compiled Deep Agents graph.
    """

    from deepagents import create_deep_agent

    ensure_project_directories() # 创建 workspace 子目录与 memories、skills 目录
    config = load_config() # 读 config.yaml 和 .env 文件
    model = build_qwen_model(config.primary_model, strict=strict_model) # 构建 Qwen 模型

    """
    持久化配置，如果 langgraph_server 为 false，则使用 MemorySaver 和 InMemoryStore 持久化：
        store 是 LangGraph Store，Deep Agents 的 StoreBackend 会用它承载 /memories/ 这类长期记忆
    
        checkpointer 保存的是 LangGraph graph state，通常包括：
            1. messages：同一 thread_id 的对话历史
            2. todos / 中间状态
            3. HITL interrupt 的暂停点
            4. StateBackend 中 thread-scoped VFS 文件状态的关联状态
        作为短期记忆，围绕一个 thread / conversation 持续存在
    注意：MemorySaver 和 InMemoryStore 本质上都是内存存储，生产应换成 Postgres/Redis/平台托管的持久化后端
    如果langgraph_server 为 true，则使用平台托管的持久化后端
    """
    store = None
    checkpointer = None 
    if not langgraph_server:
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.store.memory import InMemoryStore

        store = InMemoryStore()
        seed_memory_store(store)
        checkpointer = MemorySaver()

    agent_kwargs: dict[str, Any] = {
        "name": AGENT_NAME,
        "model": model,
        "system_prompt": MAIN_SYSTEM_PROMPT,
        "tools": STANDARD_DOCUMENT_TOOLS,
        "memory": ["/memories/AGENTS.md", "/memories/preferences.md"],
        "skills": ["/skills/"],  # 按需加载；Deep Agents 运行时必须使用虚拟路径
        "subagents": build_subagents(langgraph_server=langgraph_server),
        "backend": build_backend(langgraph_server=langgraph_server, store=store),
        "permissions": build_permissions(),
        "response_format": AgentResult,  # 结构化最终输出
    }
    if hitl_enabled(langgraph_server=langgraph_server):
        agent_kwargs["interrupt_on"] = {
            "write_file": True,
            "edit_file": True,
            "parse_file_with_mineru": True,
            "parse_document_with_mineru": True,
            "extract_standard_metadata": True,
            "run_standard_review": True,
            "run_format_source_review": True,
            "build_review_index": True,
            "propose_memory_update": True,
            "execute": True,
        }
    if checkpointer is not None:
        agent_kwargs["checkpointer"] = checkpointer
    if store is not None:
        agent_kwargs["store"] = store

    return create_deep_agent(**agent_kwargs)


def build_thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """Build a stable LangGraph thread config used for invoke, stream, and resume."""

    return {"configurable": {"thread_id": thread_id}}


def managed_project_shape() -> dict[str, str]:
    """Return the source files matching the Managed Deep Agents project shape."""

    return {
        "instructions": str((PROJECT_ROOT / "AGENTS.md").resolve()),
        "skills": str(SKILLS_DIR.resolve()),
        "subagents": str((PROJECT_ROOT / "subagents").resolve()),
        "tools": str((PROJECT_ROOT / "tools.json").resolve()),
        "workspace": str(WORKSPACE_ROOT.resolve()),
    }


def relative_to_project(path: str | Path) -> str:
    """Return a readable project-relative path when possible."""

    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)
