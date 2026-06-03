# 标准审核 Deep Agents 最终设计方案

## 1. 设计结论

标准审核能力建议改造成与元数据提取类似的工程框架：由 Deep Agents 负责主编排和子智能体委派，由 `standard-review` skill 约束审核工作方式，由少量高层 tools 暴露业务入口，由内部 LangGraph 子图承载确定性审核流程。

推荐目标结构：

```text
Deep Agents 主 Agent
  -> reviewer subagent
    -> standard-review skill
      -> run_standard_review tool
        -> standard_review LangGraph
          -> ingest
          -> retrieve_rules
          -> content_review
          -> format_review
          -> aggregate
          -> write_report
          -> write_manifest
```

不建议把 `D:\Chinese_national_standards_docs_Review-SKILL` 作为外部黑盒 CLI 直接调用。该原始 skill 应作为审核算法和流程参考实现，其核心模块应迁移到当前项目内部，统一接入当前项目已有的上传、MinerU 解析、虚拟路径、产物 manifest、配置、权限和 trace 追踪体系。

## 2. 参考依据

本设计结合以下能力边界：

- Deep Agents：适合主编排、文件系统、skills、subagents、tools、HITL 和长期任务管理。
- LangGraph：适合固定步骤、条件分支、质量门控、状态聚合和可追溯审核流程。
- 当前项目已有链路：文件上传 -> MinerU 文档解析 -> 标准审核或其他后续标准处理。
- 原始标准审核 skill：提供双轨审核、规则检索、LLM 判定、确定性格式检查和报告输出经验。

相关文档：

- <https://docs.langchain.com/oss/python/deepagents/overview>
- <https://docs.langchain.com/oss/python/deepagents/customization>
- <https://docs.langchain.com/oss/python/deepagents/skills>
- <https://docs.langchain.com/langsmith/trace-deep-agents>

## 3. 原始审核 Skill 分析

`D:\Chinese_national_standards_docs_Review-SKILL` 的核心结构包括：

```text
D:\Chinese_national_standards_docs_Review-SKILL
├── data/rules/
│   ├── rules_test.md
│   ├── rules.faiss
│   ├── rules.faiss.meta.json
│   └── tfidf_vectorizer.pkl
├── document_audit_agent/
│   ├── workflow.py
│   ├── graph.py
│   ├── state.py
│   ├── nodes/
│   │   ├── ingest.py
│   │   ├── retrieve.py
│   │   ├── review.py
│   │   ├── aggregate.py
│   │   └── report.py
│   ├── audit_core/
│   │   ├── doc_parser.py
│   │   ├── format_audit.py
│   │   ├── knowledge_base.py
│   │   ├── llm_client.py
│   │   ├── llm_judge.py
│   │   ├── reporter.py
│   │   ├── rule_models.py
│   │   ├── word_parser.py
│   │   ├── pdf_format_parser.py
│   │   └── retrievers/vector_retriever.py
│   └── utils/
│       ├── scope.py
│       ├── serialization.py
│       └── source_files.py
└── gb-standard-doc-audit/
    ├── SKILL.md
    ├── README.md
    ├── reference.md
    ├── examples.md
    └── scripts/
        ├── check_setup.py
        ├── rebuild_rules_index.py
        └── run_audit.py
```

其主要优点：

- 审核流程已经被抽象为 `ingest -> retrieve -> review -> aggregate -> report`。
- 内容审核与格式审核分轨处理，边界清晰。
- 内容轨基于 MinerU Markdown 和规则检索，适合 LLM 判断软规则。
- 格式轨基于原始 `docx/pdf`，使用确定性逻辑判断章、条、悬置段、列项、目次页码等格式问题。
- 输出包含 Markdown 报告、JSON 结果和 trace，具备追溯能力。

需要改造的地方：

- 原始实现是独立 LangGraph/CLI 应用，不符合当前 Deep Agents 主项目的 tools、skills、subagents 和虚拟路径约束。
- 环境变量、规则路径、索引路径分散，需要统一纳入当前项目配置。
- 原始脚本接收本地路径，当前项目内部应使用 `/workspace/input`、`/workspace/output` 等虚拟路径。
- 原始 skill 的流程过重，不应直接让 Agent 临场调用每个细粒度步骤。
- 规则索引重建不应成为默认审核流程的一部分。

## 4. 目标架构

建议新增如下代码结构：

```text
src/standard_document_assistant/
├── graphs/
│   └── standard_review/
│       ├── __init__.py
│       ├── state.py
│       ├── graph.py
│       ├── nodes_ingest.py
│       ├── nodes_retrieve.py
│       ├── nodes_review.py
│       ├── nodes_aggregate.py
│       └── nodes_report.py
├── review_core/
│   ├── __init__.py
│   ├── models.py
│   ├── doc_parser.py
│   ├── source_pairing.py
│   ├── rules.py
│   ├── retriever.py
│   ├── llm_client.py
│   ├── llm_judge.py
│   ├── format_audit.py
│   ├── word_parser.py
│   ├── pdf_format_parser.py
│   └── reporter.py
├── tools/
│   ├── mineru.py
│   └── review.py
└── resources/
    └── review_rules/
        ├── rules_test.md
        ├── rules.faiss
        ├── rules.faiss.meta.json
        └── tfidf_vectorizer.pkl
```

推荐 skill 结构：

```text
skills/standard-review/
├── SKILL.md
└── references/
    ├── review-workflow.md
    ├── review-inputs-outputs.md
    ├── review-issue-schema.md
    ├── review-rules.md
    ├── format-audit-limits.md
    └── report-interpretation.md
```

`SKILL.md` 只负责指导 reviewer subagent 如何工作，不承载核心实现。

其中 `tools/mineru.py` 中的 MinerU 解析 tool 是独立能力，既可以作为标准审核前置步骤，也可以被用户单独调用用于文档解析。`tools/review.py` 不重复实现 MinerU 解析，只消费 MinerU 产物。

## 5. Deep Agents 编排设计

### 5.1 主 Agent

主 Agent 负责：

- 接收用户任务。
- 确认输入文件是否已上传。
- 调用文件上传保存能力。
- 在用户上传 PDF 或 Word 且尚无 Markdown 时，调用独立 MinerU tool 生成 Markdown、图片、布局结果和 manifest。
- 将标准审核任务委派给 reviewer subagent。
- 最终向用户返回报告路径、问题摘要、风险和下一步建议。

主 Agent 不应直接逐条执行审核规则。

### 5.2 reviewer subagent

`reviewer` subagent 负责标准审核任务。

建议绑定：

- skill：`standard-review`
- tools：
  - `parse_file_with_mineru`
  - `run_standard_review`
  - `run_format_source_review`
  - `inspect_review_rules`
  - `validate_review_result_schema`

reviewer subagent 的职责：

- 判断当前输入适合内容轨、格式轨还是双轨审核。
- 当输入为 PDF 或 Word 且缺少 Markdown 时，先调用 MinerU 解析 tool。
- 调用高层审核 tool。
- 检查 tool 返回的产物路径和 warning。
- 根据报告和 JSON 结果生成用户可读摘要。
- 对依据不足、PDF 限制、LLM 失败等情况明确说明。

## 6. Tools 设计

### 6.1 `parse_file_with_mineru`

MinerU 文档解析应作为独立 tool，可以被单独调用，也可以作为标准审核的前置步骤被 reviewer subagent 调用。

建议签名：

```python
def parse_file_with_mineru(
    file_path: str,
    output_subdir: str | None = None,
    trace_id: str | None = None,
) -> MineruParseToolResult:
    ...
```

职责：

- 接收用户上传的 PDF 或 Word 文件。
- 将输入文档解析为 Markdown。
- 保存图片、布局结果、中间 JSON、Markdown 和 manifest。
- 返回 Markdown 路径、原始文件路径、manifest 路径和 trace 路径。
- 支持作为独立功能被用户直接调用。
- `StructuredTool.from_function` 同时暴露 sync 与 async 实现，统一处理 `/workspace/...` 虚拟路径与宿主真实路径。

输出建议：

```text
/workspace/output/mineru/{job_id}/
├── content.md
├── images/
├── layout/
├── mineru_manifest.json
└── mineru_trace.json
```

约束：

- 标准审核 tool 不负责重复执行 MinerU 解析。
- 若 `run_standard_review` 收到 PDF 或 Word 而没有 Markdown，应返回“需要先调用 MinerU 解析”的结构化提示，或由 reviewer subagent 在调用审核前主动调用 `parse_file_with_mineru`。
- Word 支持应以 MinerU 集成实际能力为准；如果当前 MinerU 集成不支持 Word，应明确返回不支持原因，不应静默改走 LLM 全文读取。
- MinerU manifest 是标准审核读取前序解析产物的主要桥梁。

### 6.2 `run_standard_review`

主审核入口。

建议签名：

```python
def run_standard_review(
    content_path: str | None = None,
    source_path: str | None = None,
    manifest_path: str | None = None,
    target_scopes: list[str] | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    top_k: int | None = None,
    format_only: bool = False,
    output_subdir: str | None = None,
    trace_id: str | None = None,
) -> ReviewToolResult:
    ...
```

职责：

- 校验所有输入路径必须位于允许的虚拟工作区内。
- 解析 MinerU manifest，自动补全 `content_path` 和 `source_path`。
- 调用 `standard_review` LangGraph。
- 写出 Markdown 报告、JSON 结果、trace 和 manifest。
- 返回摘要和产物路径。

不应返回完整报告正文，避免上下文膨胀。

### 6.3 `run_format_source_review`

仅格式审核入口。

建议签名：

```python
def run_format_source_review(
    source_path: str,
    output_subdir: str | None = None,
    trace_id: str | None = None,
) -> ReviewToolResult:
    ...
```

职责：

- 支持 `docx` 和带文字层的 `pdf`。
- 不调用 LLM。
- 只执行确定性格式检查。
- 输出与 `run_standard_review` 一致的产物结构。

### 6.4 `inspect_review_rules`

规则解释和调试入口。

建议签名：

```python
def inspect_review_rules(
    query: str,
    scope: str | None = None,
    top_k: int = 5,
    trace_id: str | None = None,
) -> RuleInspectionResult:
    ...
```

职责：

- 检索规则库。
- 返回匹配规则、scope、规则来源和检索分数。
- 用于解释审核依据，不用于正式判定。

### 6.5 `validate_review_result_schema`

结果自检入口。

建议签名：

```python
def validate_review_result_schema(
    result_path: str,
    trace_id: str | None = None,
) -> ValidationResult:
    ...
```

职责：

- 校验 `*_audit_result.json` 是否满足 schema。
- 校验 issue 是否包含 rule、scope、status、evidence、source_ref 等必要字段。
- 校验产物 manifest 是否完整。

### 6.6 不默认暴露的能力

`rebuild_review_rules_index` 不建议作为普通 Agent tool 默认暴露。

如果后续需要，应作为脚本或管理员工具，并要求人工审批：

```text
scripts/rebuild_review_rules_index.py
```

原因：

- 会写入规则索引文件。
- 可能耗时。
- 索引版本影响审核结果，应受控管理。

## 7. LangGraph 子图设计

推荐节点：

```text
START
  -> ingest
  -> retrieve_rules
  -> content_review
  -> format_review
  -> aggregate
  -> write_report
  -> write_manifest
  -> END
```

P0 阶段可串行执行。P1 阶段可将 `content_review` 和 `format_review` 做成并行分支。

### 7.1 State 设计

建议状态：

```python
class StandardReviewState(TypedDict):
    job_id: str
    trace_id: str
    content_path: str | None
    source_path: str | None
    manifest_path: str | None

    target_scopes: list[str] | None
    line_start: int | None
    line_end: int | None
    top_k: int
    format_only: bool

    parsed_document: dict | None
    scope_text_map: dict[str, str]
    active_scope_keys: list[str]

    format_document: dict | None
    format_facts: dict | None

    section_rules: list[dict]
    full_document_rules: list[dict]
    retrieval_trace: list[dict]

    issues: Annotated[list[dict], operator.add]
    warnings: Annotated[list[str], operator.add]
    events: Annotated[list[dict], operator.add]
    trace_events: Annotated[list[dict], operator.add]

    aggregate_summary: dict | None
    report_markdown: str | None
    result_payload: dict | None
    trace_payload: dict | None
    output_paths: dict[str, str]
    status: str
```

### 7.2 `ingest` 节点

职责：

- 读取 manifest。
- 解析 `content_path` 指向的 Markdown。
- 切分文档 scope。
- 配对原始 `docx/pdf`。
- 对源文件执行格式事实解析。

输入：

- `content_path`
- `source_path`
- `manifest_path`

输出：

- `parsed_document`
- `scope_text_map`
- `active_scope_keys`
- `format_document`
- `format_facts`
- `warnings`

### 7.3 `retrieve_rules` 节点

职责：

- 加载 `rules_test.md`。
- 加载或使用现有 FAISS/tfidf 索引。
- 根据 scope 文本检索候选规则。
- 内容轨排除格式类规则。
- 生成 retrieval trace。

输出：

- `section_rules`
- `full_document_rules`
- `retrieval_trace`

### 7.4 `content_review` 节点

职责：

- 使用 LLM 对内容轨规则进行判定。
- 对局部 scope 和全文规则分别处理。
- 生成结构化 issue。
- 捕获 LLM 异常和解析异常。

输出 issue 必须包含：

- `issue_id`
- `rule_id`
- `rule_name`
- `scope`
- `route`
- `audit_track`
- `severity`
- `status`
- `expected`
- `actual`
- `evidence_text`
- `source_ref`
- `suggestion`
- `confidence`
- `llm_reasoning`

### 7.5 `format_review` 节点

职责：

- 对 `docx/pdf` 源文件执行确定性格式审核。
- 不调用 LLM。
- 生成 `audit_track=format_source` 的 issue。

检查范围：

- 章编号。
- 条编号。
- 悬置段。
- 列项。
- 目次页码。

限制：

- PDF 必须有可复制文字层。
- 扫描件或乱码 PDF 应返回 warning 或 `insufficient_context`。
- 禁止把 PDF 转 Word 后再审核。
- 禁止在 MinerU Markdown 上判断目次页码。

### 7.6 `aggregate` 节点

职责：

- 按 scope、route、audit_track、severity 聚合。
- 统计 pass、fail、warn、insufficient_context。
- 生成摘要。

### 7.7 `write_report` 节点

职责：

- 生成 Markdown 报告。
- 生成 JSON 结果。
- 生成 trace。

### 7.8 `write_manifest` 节点

职责：

- 生成审核 manifest。
- 记录输入、输出、规则版本、模型配置、时间戳和 warning。

## 8. 审核流程拆分

标准审核 skill 中的主流程建议拆成 11 步：

1. 输入登记：确认用户上传文件和已有解析产物。
2. 来源配对：优先使用 MinerU manifest，必要时按同 stem 查找源文件。
3. 内容解析：解析 MinerU Markdown 并切分 scope。
4. 格式解析：解析原始 `docx/pdf`，提取格式事实。
5. 规则加载：加载规则 Markdown 和规则索引。
6. 规则检索：按 scope 检索候选规则。
7. 内容轨 LLM 判定：对软规则执行结构化审核。
8. 格式轨确定性判定：对格式事实执行硬规则检查。
9. 质量门控：必要时扩大上下文或标记依据不足。
10. 结果聚合：按范围和严重级别生成摘要。
11. 产物写入：保存 report、result、trace、manifest。

## 9. 输入文件处理

### 9.1 PDF 标准文档

推荐链路：

```text
/workspace/input/uploads/{job_id}/source.pdf
  -> parse_file_with_mineru
  -> /workspace/output/mineru/{job_id}/content.md
  -> run_standard_review
```

审核输入：

- `source_path`: 原始 PDF。
- `content_path`: MinerU Markdown。
- `manifest_path`: MinerU manifest。

### 9.2 Markdown 文档

支持：

```text
/workspace/input/uploads/{job_id}/content.md
```

可执行内容轨审核。

若无 `source_path`，则不执行格式轨。

### 9.3 Word 文档

支持：

```text
/workspace/input/uploads/{job_id}/source.docx
  -> parse_file_with_mineru
  -> /workspace/output/mineru/{job_id}/content.md
  -> run_standard_review
```

若 MinerU 集成支持 Word，则推荐先解析为 Markdown 后执行内容轨审核；同时可用原始 Word 执行格式轨审核。

若当前 MinerU 集成不支持 Word，应返回明确错误或降级为 `run_format_source_review` 的格式轨审核，不建议临时让 LLM 直接读取 Word 全文后判断。

### 9.4 路径约束

所有 tool 参数必须使用 Deep Agents 虚拟路径：

- `/workspace/input/...`
- `/workspace/output/...`
- `/workspace/skills/...`

不得将 Windows 盘符路径直接传入 Agent 内部文件工具。

原始文件不得覆盖。

## 10. 输出文件处理

每次审核生成独立目录：

```text
/workspace/output/reviews/{job_id}/
├── {stem}_audit_report.md
├── {stem}_audit_result.json
├── {stem}_audit_trace.json
└── {stem}_review_manifest.json
```

`run_standard_review` 返回示例：

```json
{
  "status": "success",
  "job_id": "20260601_xxx",
  "trace_id": "trace_20260601_xxx",
  "trace_path": "/workspace/output/reviews/20260601_xxx/sample_audit_trace.json",
  "summary": {
    "total_issues": 12,
    "failed": 6,
    "warn": 4,
    "insufficient_context": 2
  },
  "artifacts": {
    "report": "/workspace/output/reviews/20260601_xxx/sample_audit_report.md",
    "result": "/workspace/output/reviews/20260601_xxx/sample_audit_result.json",
    "trace": "/workspace/output/reviews/20260601_xxx/sample_audit_trace.json",
    "manifest": "/workspace/output/reviews/20260601_xxx/sample_review_manifest.json"
  },
  "warnings": []
}
```

manifest 应包含：

- 输入文件路径。
- MinerU Markdown 路径。
- 原始源文件路径。
- 规则文件路径。
- 规则索引版本。
- LLM 模型配置。
- trace id。
- 审核开始和结束时间。
- 输出产物路径。
- warnings。

## 11. Trace 追踪设计

所有 skill、tool、subagent 和 LangGraph 子图执行都需要支持 trace 追踪。trace 既用于用户可见的审核追溯，也用于开发调试、LangSmith 观测和后续质量回放。

### 11.1 Trace ID 传递

每次用户任务应生成统一的 `trace_id`。

推荐传递链路：

```text
main agent trace_id
  -> reviewer subagent
    -> parse_file_with_mineru
      -> mineru_trace.json
    -> run_standard_review
      -> standard_review graph
        -> node trace events
      -> *_audit_trace.json
      -> *_review_manifest.json
```

要求：

- 主 Agent 创建或继承 `trace_id`。
- subagent 调用 tool 时必须传入 `trace_id`。
- tool 返回值必须包含 `trace_id` 和本次 tool 的 `trace_path`。
- LangGraph state 必须包含 `trace_id`。
- 每个节点都应向 `trace_events` 追加结构化事件。
- manifest 必须记录 `trace_id`、上游 trace 文件和当前 trace 文件。

### 11.2 本地 Trace 文件

MinerU 解析输出：

```text
/workspace/output/mineru/{job_id}/mineru_trace.json
```

标准审核输出：

```text
/workspace/output/reviews/{job_id}/{stem}_audit_trace.json
```

trace 事件建议结构：

```json
{
  "trace_id": "trace_20260601_xxx",
  "job_id": "20260601_xxx",
  "component": "standard_review_graph",
  "node": "content_review",
  "event": "llm_rule_judged",
  "status": "success",
  "input_refs": {
    "content_path": "/workspace/output/mineru/20260601_xxx/content.md",
    "rule_id": "R-001"
  },
  "output_refs": {
    "issue_id": "ISSUE-001"
  },
  "started_at": "2026-06-01T10:00:00+08:00",
  "ended_at": "2026-06-01T10:00:02+08:00",
  "warnings": []
}
```

### 11.3 LangSmith Trace

如环境已启用 LangSmith，应将同一个 `trace_id` 或 `job_id` 写入 LangChain/LangGraph config metadata：

```python
config = {
    "configurable": {"thread_id": job_id},
    "metadata": {
        "trace_id": trace_id,
        "job_id": job_id,
        "component": "standard_review",
    },
    "tags": ["standard-review", "deep-agents"],
}
```

要求：

- LLM 调用、工具调用和 LangGraph 节点执行都应出现在同一条可关联 trace 下。
- 若 LangSmith 未启用，本地 `*_trace.json` 仍必须完整生成。
- trace 不得包含密钥、`.env` 内容或大段原文全文；只记录路径、hash、规则编号、issue id、状态和必要短证据。

### 11.4 Skill 和 Tool 的 Trace 约束

`standard-review` skill 应要求：

- 调用审核 tool 前检查是否有上游 MinerU trace。
- 调用审核 tool 后检查 `trace_path` 是否存在。
- 向用户汇报报告路径时，同时汇报 trace 路径。

所有 tool 返回值应统一包含：

```json
{
  "trace_id": "...",
  "trace_path": "/workspace/output/.../*_trace.json",
  "artifacts": {}
}
```

## 12. LLM 调用设计

### 12.1 配置来源

建议统一使用项目根目录下的 `config.yaml` 和 `.env`。

`config.yaml` 中新增：

```yaml
review:
  rules_md: "src/standard_document_assistant/resources/review_rules/rules_test.md"
  index_dir: "src/standard_document_assistant/resources/review_rules"
  top_k: 8
  max_review_rounds: 2
  include_full_document_rules: true

  llm:
    provider: "openai_compatible"
    model: "${MODEL_ID}"
    base_url: "${DASHSCOPE_BASE_URL}"
    api_key_env: "DASHSCOPE_API_KEY"
    timeout_sec: 60
    max_retries: 3
    retry_backoff_sec: 2
    max_workers: 4
    disable_response_format: false
```

`.env` 只放密钥和环境差异：

```text
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=...
MODEL_ID=...
```

不得读取或回显 `.env` 内容。

### 12.2 LLM 客户端

建议封装：

```text
review_core/llm_client.py
```

职责：

- 从统一配置构建 ChatModel。
- 支持 OpenAI-compatible API。
- 支持 timeout、retry、max_workers。
- 支持 LangSmith tracing。
- 不在业务节点中散落读取环境变量。

### 12.3 结构化输出

LLM 判定必须输出结构化 JSON：

```json
{
  "rule_id": "R-001",
  "status": "fail",
  "severity": "major",
  "scope": "normative_references",
  "expected": "应列出正文中规范性引用的文件",
  "actual": "正文引用了 GB/T xxx，但规范性引用文件未列出",
  "evidence_text": "正文第 5 章引用 GB/T xxx",
  "source_ref": "rules_test.md#R-001",
  "suggestion": "补充该规范性引用文件或删除正文引用",
  "confidence": 0.82,
  "llm_reasoning": "..."
}
```

约束：

- LLM 只用于内容轨软规则判断。
- 格式轨不调用 LLM。
- LLM 调用失败时，应写入 warning 和 trace。
- JSON 解析失败时，不得默认通过，应标记 `llm_error` 或 `insufficient_context`。
- 所有问题必须尽量包含 evidence 和 source_ref。

### 12.4 报告摘要

报告摘要可选使用 LLM 生成。

如果 LLM 摘要失败，应 fallback 到统计摘要：

- 总问题数。
- 严重问题数。
- 警告数。
- 依据不足项。
- 主要涉及章节。

## 13. 规则库和索引管理

建议将原始 skill 的规则资源迁移到：

```text
src/standard_document_assistant/resources/review_rules/
```

包括：

- `rules_test.md`
- `rules.faiss`
- `rules.faiss.meta.json`
- `tfidf_vectorizer.pkl`

规则索引重建脚本：

```text
scripts/rebuild_review_rules_index.py
```

原则：

- 默认审核不自动重建索引。
- 规则变更后由开发者或管理员显式重建。
- 重建结果应更新索引元数据。
- 审核 manifest 中记录规则版本或规则文件 hash。

## 14. Skill 改造建议

`skills/standard-review/SKILL.md` 建议改为流程型说明：

```markdown
---
name: standard-review
description: 标准文档审核流程。用于调用 run_standard_review，读取审核报告、解释问题、说明风险和依据不足。
---

# 标准审核 Skill

## 适用场景

- 用户要求审核标准文档。
- 用户要求检查标准结构、规范性引用、术语、范围、格式或一致性。
- 已有 MinerU Markdown、原始 PDF/DOCX 或 MinerU manifest。
- 用户上传 PDF 或 Word 但尚未解析为 Markdown。

## 必须遵守

- 优先调用 run_standard_review。
- PDF 或 Word 缺少 Markdown 时，先调用 parse_file_with_mineru。
- 不手工拼接审核流程。
- 不伪造标准条款和来源。
- 不覆盖原始文件。
- 不使用 Windows 盘符路径调用文件工具。
- 所有问题必须保留规则来源和证据。
- 所有 tool 和子图调用必须携带 trace_id，并检查 trace_path。

## 默认流程

1. 确认输入文件和解析产物。
2. 若只有 PDF 或 Word，调用 parse_file_with_mineru。
3. 调用 run_standard_review。
4. 检查返回的 report/result/trace/manifest。
5. 向用户总结主要问题、严重程度、依据不足、产物路径和 trace 路径。
```

详细说明放入 `references/`：

- `review-workflow.md`：审核流程。
- `review-inputs-outputs.md`：输入输出。
- `review-issue-schema.md`：issue schema。
- `review-rules.md`：规则库说明。
- `format-audit-limits.md`：格式审核限制。
- `report-interpretation.md`：报告解读。

## 15. 与现有流水线衔接

推荐完整用户链路：

```text
用户上传 PDF 或 Word
  -> save_uploaded_file
  -> parse_file_with_mineru
  -> run_standard_review
  -> 返回审核报告和问题摘要
```

其中：

- `parse_file_with_mineru` 是独立 tool，负责把 PDF 或 Word 转为 Markdown，并生成图片、布局结果、manifest 和 MinerU trace。
- `run_standard_review` 负责读取 Markdown、源文件和 MinerU manifest，输出审核报告、JSON 结果、审核 trace 和审核 manifest。

标准审核不内置元数据提取步骤，也不依赖元数据提取结果。若其他业务流程需要元数据，可在审核之外单独调用元数据提取 tool，但该步骤不是标准审核的必需前置条件。

当用户已经提供 Markdown 时，可直接调用：

```text
用户上传 Markdown
  -> save_uploaded_file
  -> run_standard_review
  -> 返回审核报告和问题摘要
```

## 16. 依赖管理

不建议把所有审核依赖放入核心依赖。

建议在 `pyproject.toml` 中增加 optional dependency：

```toml
[project.optional-dependencies]
review = [
  "numpy",
  "scikit-learn",
  "python-docx",
  "lxml",
  "pymupdf",
  "faiss-cpu",
  "langchain-openai",
  "openai",
]
```

原因：

- FAISS、PyMuPDF、scikit-learn 等依赖较重。
- 不是所有标准助手能力都需要审核功能。
- 有利于保持基础安装轻量。

## 17. 分阶段实施计划

### P0：审核主链路骨架

- 将 MinerU 解析明确为独立 tool，并保证其返回 manifest 和 trace。
- 新增 `review_core/models.py`。
- 新增 `graphs/standard_review`。
- 新增 `tools/review.py`。
- 实现 `run_standard_review` 主入口。
- 支持读取 MinerU Markdown。
- 支持输出 report/result/trace/manifest。
- 标准审核流程不加入元数据提取步骤。
- 先接通最小 LLM 结构化判定。

### P1：迁移规则和格式轨

- 迁移 `rules_test.md` 和规则索引。
- 迁移规则检索逻辑。
- 迁移 `LLMSoftRuleJudge`。
- 迁移 `format_audit`。
- 支持 `docx/pdf` 格式轨审核。

### P2：质量门控和可追溯性

- 实现局部审核扩大到全文的 quality gate。
- 增加 skill、tool、subagent、LangGraph 节点级 trace 细节。
- 加入 `validate_review_result_schema`。
- 加入 `inspect_review_rules`。
- 接入 LangSmith tracing。

### P3：运行体验优化

- 支持并发审核。
- 支持流式事件。
- 增加规则索引重建脚本。
- 增加报告摘要 fallback。
- 增强 reviewer subagent 的报告解释能力。

## 18. 关键风险和处理方式

### 18.1 PDF 格式审核可靠性

风险：

- 扫描 PDF 或乱码 PDF 无法可靠识别页码、条号和列项。

处理：

- 明确要求 PDF 有可复制文字层。
- 无法解析时标记 `insufficient_context`。
- 建议用户提供 Word 原文。

### 18.2 LLM 泛化判断

风险：

- LLM 可能在证据不足时给出泛化结论。

处理：

- 所有判定必须绑定规则和 evidence。
- 没有证据时只能输出 `insufficient_context`。
- 报告中明确标注依据不足。

### 18.3 规则索引兼容性

风险：

- 原始 FAISS/sklearn 索引可能与当前依赖版本不兼容。

处理：

- 保留规则 Markdown 作为权威来源。
- 必要时重建索引。
- manifest 记录规则 hash 和索引版本。

### 18.4 Agent 跳步

风险：

- 如果暴露太多细粒度 tools，Agent 可能跳过关键步骤或错误组合。

处理：

- 默认只暴露 `run_standard_review` 这种高层业务 tool。
- 小步骤封装在 LangGraph 内部。

## 19. 最终建议

标准审核应采用与元数据提取一致的项目化设计框架，但审核内部需要更完整的 LangGraph 子图。

推荐最终落地方式：

- Deep Agents 主 Agent 负责接收任务和调度。
- reviewer subagent 负责标准审核任务。
- `standard-review` skill 负责流程和安全约束。
- MinerU 解析作为独立 tool，可单独调用，也可作为 PDF/Word 审核前置步骤。
- `run_standard_review` 作为唯一主审核入口。
- LangGraph 子图负责固定审核流程。
- `review_core` 承载规则、解析、LLM 判定、格式审核和报告生成。
- 所有输入输出都通过 `/workspace/input` 和 `/workspace/output` 管理。
- `.env` 只保存密钥，审核参数统一放入 `config.yaml`。
- 标准审核不内置元数据提取步骤。
- 所有 skill、tool、subagent 和子图节点都必须产生可关联的 trace。

这样可以最大程度复用当前项目已有能力，同时避免把复杂审核逻辑暴露给 Agent 临场编排，从而提高稳定性、可追溯性和后续可维护性。
