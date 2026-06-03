"""实际跑一次标准审核，验证 qwen3.7-max 切回后 LLM Judge 不再被 length 截断。

约定：
- 启动前手动 export DASHSCOPE_API_KEY；脚本内部仍走 load_config -> load_dotenv。
- 输入：/workspace/output/mineru/md/国家标准/GB-T-15034-2009.md（已解析产物）
- 输出：/workspace/output/reviews/gbt15034-2009-qwen37-*/...
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC))

# 关闭 LangSmith 噪音，保持本次测试输出干净
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from standard_document_assistant.config import load_config  # noqa: E402
from standard_document_assistant.tools import (  # noqa: E402
    parse_file_with_mineru,
    run_standard_review,
    validate_review_result_schema,
)


def main() -> int:
    cfg = load_config()
    print("== loaded config ==")
    print(
        "judge.model =",
        cfg.standard_review.judge_model,
        "| judge.max_tokens =",
        cfg.standard_review.judge_max_tokens,
        "| judge.temperature =",
        cfg.standard_review.judge_temperature,
    )
    print(
        "primary.model =",
        cfg.primary_model.model,
        "| metadata.model =",
        cfg.metadata_extraction.model,
    )

    pdf_virtual = (
        "/workspace/input/uploads/019e8ca7-5df2-7f81-832a-fc6ab2927d78/"
        "GBT15034-2009.pdf"
    )
    md_virtual = "/workspace/output/mineru/md/国家标准/GB-T-15034-2009.md"
    manifest_virtual = (
        "/workspace/output/mineru/manifests/GB-T-15034-2009_parse_manifest.json"
    )

    print("\n== step 1/3: parse_file_with_mineru (复用 zip 缓存) ==")
    t0 = time.time()
    parsed = parse_file_with_mineru.invoke({"file_path": pdf_virtual})
    print(f"  status={parsed.get('status')}  took={time.time()-t0:.1f}s")
    if parsed.get("status") != "ok":
        print("  parse error:", json.dumps(parsed, ensure_ascii=False, indent=2)[:2000])
        return 2
    print("  virtual_md_path =", parsed.get("virtual_md_path"))
    print("  virtual_manifest_path =", parsed.get("virtual_manifest_path"))
    print("  cover_metadata =", json.dumps(parsed.get("cover_metadata") or {}, ensure_ascii=False))

    print("\n== step 2/3: run_standard_review (full pipeline) ==")
    output_subdir = f"gbt15034-2009-qwen37-{int(time.time())}"
    t0 = time.time()
    try:
        result = run_standard_review.invoke(
            {
                "content_path": md_virtual,
                "source_path": pdf_virtual,
                "manifest_path": manifest_virtual,
                "output_subdir": output_subdir,
                "trace_id": f"trace-gbt15034-2009-{int(time.time())}",
            }
        )
    except Exception as exc:
        print("  review raised exception:")
        traceback.print_exc()
        return 3
    print(f"  status={result.get('status')}  took={time.time()-t0:.1f}s")
    summary = result.get("summary") or {}
    print("  summary =", json.dumps(summary, ensure_ascii=False))

    artifacts = result.get("artifacts") or {}
    print("  artifacts:")
    for key, path in artifacts.items():
        print(f"    - {key}: {path}")

    scope_summary = result.get("scope_summary") or {}
    print("  scope_summary =", json.dumps(scope_summary, ensure_ascii=False)[:1500])
    audit_summary = result.get("audit_summary") or {}
    print("  audit_summary =", json.dumps(audit_summary, ensure_ascii=False)[:1500])
    retrieval_trace = result.get("retrieval_trace") or []
    print(f"  retrieval_trace.count = {len(retrieval_trace)}")
    if retrieval_trace:
        strategies: dict[str, int] = {}
        for hit in retrieval_trace:
            s = hit.get("strategy") or hit.get("analysis_mode") or "unknown"
            strategies[s] = strategies.get(s, 0) + 1
        print("  retrieval strategies =", strategies)
        sample = retrieval_trace[0]
        print("  sample hit =", json.dumps(sample, ensure_ascii=False)[:600])

    if result.get("errors"):
        print("  errors =", result["errors"])
    if result.get("warnings"):
        print("  warnings =", result["warnings"][:3])

    print("\n== step 3/3: validate_review_result_schema ==")
    validation = validate_review_result_schema.invoke(
        {"result_path": artifacts.get("result", "")}
    )
    print("  valid =", validation.get("valid"))
    if not validation.get("valid"):
        print("  validation_errors =", json.dumps(validation, ensure_ascii=False, indent=2)[:1500])

    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
