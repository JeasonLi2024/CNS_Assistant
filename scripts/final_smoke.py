"""标准审核工具链的端到端冒烟测试脚本。

作用
----
不依赖 pytest，单独可跑。依次调用 4 个审核模块的**同步内部入口**（均带
`_sync` 后缀），验证完整审核 pipeline 仍可工作：

1. `_build_review_index_sync(force_rebuild=True)`
   强制重建审核规则的 FAISS / TF-IDF 向量索引，输出 `rules_loaded`、
   `index_source`、耗时（ms）。

2. `_inspect_review_rules_sync(query, scope, top_k=3)`
   对三种 scope 各跑一次规则检索：
   - `"完整性"`         → scope=`"scope"`
   - `"引用文件"`       → scope=`"normative_references"`
   - `"全文结构"`       → scope=`"full_document"`
   打印每个查询命中的 `chunk_id` 列表。

3. `_run_standard_review_sync(content_path, trace_id, output_subdir)`
   对 `/workspace/input/uploads/smoke_standard.md` 跑一次完整审核，
   输出 `status`、`summary`、`artifacts`、`scope_summary`、
   `audit_summary.key_risks`、`retrieval_trace` 中命中的规则总数。

4. `_validate_review_result_schema_sync(result_path)`
   校验审核结果 JSON 是否满足 schema。

运行方式
-------
在项目根目录 `d:\\deep-agents\\` 下执行：

```powershell
python scripts/final_smoke.py
```

前置条件
--------
- 已经准备好 `/workspace/input/uploads/smoke_standard.md` 测试样本。
  可先跑一次 `python scripts/smoke_test.py` 让 `save_uploaded_file` 生成。
- `LANGSMITH_TRACING=false` / `LANGCHAIN_TRACING_V2=false` 已被默认设置。

注意
----
- 步骤 1 会清空 `src/standard_document_assistant/resources/review_rules/`
  下已生成的 `rules.faiss*` / `tfidf_vectorizer.pkl` 并重新生成，
  比 `rebuild_rules_index.py` 更"硬"。
- 步骤 3 会调用 LLM，请确保 `config.yaml` 中 `primary_model` / `judge`
  对应的模型与凭据可用，否则会卡住或失败。
- 该脚本目前**没有** pytest 包装（与 `smoke_test.py` 不同）。如要让
  `pytest tests/` 自动跑到它，可在 `tests/test_final_smoke.py` 加 1 行
  `from scripts.final_smoke import main` 包装，参考 `tests/test_smoke_tools.py`。
"""

import sys
import time

sys.path.insert(0, "src")

from standard_document_assistant.tools.review import (
    _build_review_index_sync,
    _inspect_review_rules_sync,
    _run_standard_review_sync,
    _validate_review_result_schema_sync,
)

print("=== build_review_index (force rebuild) ===")
t = time.time()
r = _build_review_index_sync(force_rebuild=True)
print(
    "rules_loaded:",
    r["rules_loaded"],
    "| index_source:",
    r["index_source"],
    "| ms:",
    int((time.time() - t) * 1000),
)

print()
print("=== inspect_review_rules ===")
for q, s in [("完整性", "scope"), ("引用文件", "normative_references"), ("全文结构", "full_document")]:
    h = _inspect_review_rules_sync(q, scope=s, top_k=3)
    print("  q=", repr(q), "scope=", repr(s), "matches=", [m["chunk_id"] for m in h["matches"]])

print()
print("=== run_standard_review (content) ===")
r = _run_standard_review_sync(
    content_path="/workspace/input/uploads/smoke_standard.md",
    trace_id="final-3",
    output_subdir="final-3",
)
print("status:", r["status"])
print("summary:", r["summary"])
print("artifacts:", r["artifacts"])
print("scope buckets:", list(r["scope_summary"].keys()))
print("audit_summary key_risks:", r["audit_summary"].get("key_risks"))
print(
    "retrieval_trace rules found:",
    sum(len(rt["rule_ids"]) for rt in r["retrieval_trace"]),
)

print()
print("=== validate_review_result_schema ===")
v = _validate_review_result_schema_sync(r["artifacts"]["result"])
print("valid:", v["valid"], "| errors:", v["errors"])
