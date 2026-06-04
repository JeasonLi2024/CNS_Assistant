---
name: standard-review
description: |
  标准文档审核流程（FAISS RAG + LLM Judge 多策略 + 确定性 DOCX/PDF + 质量门控 + 范围扩大回环 + LLM 报告摘要 + scope_summary 聚合）。用于调用 run_standard_review，读取报告、解释问题、说明风险和依据不足。
  强约束：输入必须为 /workspace/output/mineru/**/*.md 或 /workspace/input/uploads/**/*.md；PDF/Word 必须先委派 parser 调用 parse_file_with_mineru；审核本身**不要**先调用元数据抽取。
  流式事件：state["trace_events"] 与 get_stream_writer 双通道推送 review.* 事件（review.ingest.* / review.retrieve.* / review.judge.* / review.quality_gate.* / review.widen.* / review.format.* / review.aggregate.* / review.report.* / review.manifest.*），与 MinerU mineru.*、langextract meta.* 形成统一 <domain>.<stage> 命名空间。
---

# Standard Review

## Overview

标准审核子图执行**双轨**审核：

- **内容轨（content）**：FAISS 向量检索 + LLM Judge 多策略（single / window / cross_section / full_document），异步并发，置信度降级，scope 进度流式推送。
- **格式轨（format_source）**：对源 DOCX/PDF 做确定性检查（章编号、条层次、悬置段、列项、目次页码），无 LLM。

**子图拓扑**：

```
ingest → retrieve_rules → judge_rules
                                │
                                ▼
                          quality_gate (Command[Literal[...]])
                          │           │
                       (widen)        (ok)
                          ▼             ▼
            widen_review_scope → reload_review_rules   format_review
                          └─── loop back to judge_rules ──┘        │
                                                                    ▼
                                                              aggregate (scope_summary)
                                                                    │
                                                                    ▼
                                                           write_outputs (audit_summary)
                                                                    │
                                                                    ▼
                                                            write_manifest → END
```

`quality_gate` 检测到 `insufficient_context` 且 `review_round < max_review_rounds` 时进入 `widen_review_scope` → `reload_review_rules` → `judge_rules` 回环；否则进入 `format_review` → `aggregate`。

## Instructions

1. **优先调用 `run_standard_review`**，不要手工拼接流程。
2. PDF/Word 输入且缺少 Markdown / manifest 时，**先调用 `parse_file_with_mineru`**。
3. 修改 `rules_test.md`、切换嵌入模型、改变 `embedding_dim` 后，**先调用 `build_review_index`**，否则仍会命中旧索引。
4. 只想预览会命中哪些规则时，**调用 `inspect_review_rules(query=..., scope=...)`**。
5. 标准审核不需要先做元数据抽取；**不要把 `extract_standard_metadata` 作为固定前置步骤**。
6. 所有路径必须使用 `/workspace/` 虚拟路径。
7. 调用审核工具时传入同一个 `trace_id`；结束后核对 `trace_path` 是否存在。
8. 每条发现必须保留规则来源（chunk_id、source_ref）、证据、严重级别、状态（pass/fail/insufficient_context）、策略（single/window/...）、建议。
9. **不得伪造**标准条款或来源；无法定位证据时标记"依据不足"。
10. 最终只摘要关键问题和风险，不粘贴完整报告正文。

## Tool Set

| 工具 | 用途 | HITL |
|---|---|---|
| `parse_file_with_mineru` | PDF/DOCX → Markdown + manifest | ✅ |
| `run_standard_review` | 执行完整审核（content + format + scope_summary + audit_summary） | ✅ |
| `run_format_source_review` | 仅执行格式轨 | ✅ |
| `inspect_review_rules` | FAISS 检索规则 | — |
| `build_review_index` | 构建/重建 FAISS + TF-IDF 索引 | ✅ |
| `validate_review_result_schema` | 校验产物 schema 与 `/workspace/` 路径前缀 | — |

## Default Workflow

1. 确认输入文件、Markdown 和 manifest。
2. PDF/Word 且无 Markdown 时调用 `parse_file_with_mineru`。
3. （可选）`inspect_review_rules` 预览命中规则。
4. 调用 `run_standard_review`，传入 `trace_id`、`content_path`、`source_path`（DOCX/PDF）、`target_scopes`（可选）、`partial_mode`（`sectional`/`full_document`/`format_only`）。
5. 调用 `validate_review_result_schema` 自检 `*_audit_result.json`。
6. 向用户返回：四个产物路径、scope_summary 关键桶、audit_summary、retrieval_trace 命中规则数与策略分布、错误/警告/重试轮次。

## Artifact Layout

所有产物写入 `/workspace/output/reviews/<job_id>/`（由 `pathing.review_output_root` 分配）：

- `<stem>_audit_report.md`（人类可读）
- `<stem>_audit_result.json`（结构化 + scope_summary + audit_summary）
- `<stem>_audit_trace.json`（节点级 trace 事件）
- `<stem>_review_manifest.json`（ArtifactManifest）

## Knowledge Base

- 规则源：`src/standard_document_assistant/resources/review_rules/rules_test.md`
- 索引：`src/standard_document_assistant/resources/review_rules/rules.faiss.json`（自动从 markdown 构建；首次或 `force_rebuild_index=True` 时重建）
- 回退：当 `faiss-cpu` 与 `langchain_community.vectorstores.FAISS` 不可用时，自动使用纯 Python TF-IDF（`retriever.build_tfidf_index`），保证离线仍可运行。

## Trace & Resumption

- 子图运行在 `build_subgraph_runnable_config` 包装的 `RunnableConfig` 中，parent callbacks / tags / metadata 全部透传。
- 在 `langgraph dev`/Studio 中，子图以 `standard_review` 节点呈现。
- 多轮回环：`review_round` 写入 trace，`widened=True` 标记是否触发过 `widen_review_scope`。
- 检查点：使用 Deep Agents 的 `MemorySaver`（默认）或 LangGraph Server 的托管 PostgresSaver。
