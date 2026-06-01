---
name: standard-review
description: 标准文档审核流程。用于调用 run_standard_review，读取审核报告、解释问题、说明风险和依据不足。
---

# Standard Review

## Instructions

1. 优先调用 `run_standard_review`，不要手工拼接审核流程。
2. 如果输入是 PDF 或 Word 且缺少 Markdown 或 MinerU manifest，先调用 `parse_document_with_mineru`。
3. 标准审核不需要先做元数据提取；不要把 `extract_standard_metadata` 作为固定前置步骤。
4. 所有工具参数必须使用 `/workspace/` 虚拟路径，禁止 Windows 盘符路径。
5. 调用审核工具时传入同一个 `trace_id`；工具返回后检查 `trace_path` 是否存在。
6. 每条发现必须保留规则来源、证据、严重级别、状态和建议。
7. 不能从模型常识伪造标准条款；无法定位证据时标记“依据不足”。
8. 最终只摘要关键问题和风险，不粘贴完整报告正文。

## Default Workflow

1. 确认输入文件、Markdown 和 manifest。
2. 只有 PDF 或 Word 时，先调用 `parse_document_with_mineru`。
3. 调用 `run_standard_review`。
4. 必要时调用 `validate_review_result_schema` 自检结果。
5. 向用户返回 report、result、trace、manifest 路径，以及主要问题摘要。
