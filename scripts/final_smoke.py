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
