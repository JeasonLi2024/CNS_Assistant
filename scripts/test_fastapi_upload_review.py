"""Test FastAPI upload + direct standard review endpoints.

Run from the project root after starting both services:

    langgraph dev --host 127.0.0.1 --port 2024 --no-browser
    uvicorn standard_document_assistant.api.app:app --host 0.0.0.0 --port 8080

Example:

    python scripts/test_fastapi_upload_review.py ^
      --file "C:\\Users\\32084\\Desktop\\GB-T-15034-2009_2.md" ^
      --mode scoped_content ^
      --target-scopes foreword ^
      --disable-widen ^
      --force-rebuild-index ^
      --save-response workspace\\tmp\\fastapi_review_response.json

This script calls:
1. GET  /health
2. POST /api/threads/{thread_id}/uploads
3. POST /api/review-jobs/standard-review
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int,
) -> dict[str, Any]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {text}") from exc
    except Exception as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc


def _upload_file(base_url: str, thread_id: str, file_path: Path, *, timeout: int) -> dict[str, Any]:
    boundary = f"----standard-doc-boundary-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    data = header + file_bytes + footer
    url = f"{base_url}/api/threads/{urllib.parse.quote(thread_id)}/uploads"
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {url} failed: HTTP {exc.code}: {text}") from exc
    except Exception as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc


def _scope_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    scopes = [item.strip() for item in value.split(",") if item.strip()]
    return scopes or None


def _review_payload(args: argparse.Namespace, virtual_path: str) -> dict[str, Any]:
    review_options: dict[str, Any] = {
        "mode": args.mode,
        "disable_widen": args.disable_widen,
    }
    scopes = _scope_list(args.target_scopes)
    if scopes:
        review_options["target_scopes"] = scopes
    if args.line_start is not None:
        review_options["line_start"] = args.line_start
    if args.line_end is not None:
        review_options["line_end"] = args.line_end
    if args.top_k is not None:
        review_options["top_k"] = args.top_k
    if args.max_review_rounds is not None:
        review_options["max_review_rounds"] = args.max_review_rounds
    if args.force_rebuild_index:
        review_options["force_rebuild_index"] = True

    payload: dict[str, Any] = {
        "thread_id": args.thread_id,
        "file_path": virtual_path,
        "review_options": review_options,
        "return_report_content": args.return_report_content,
        "return_result_json": args.return_result_json,
    }
    if args.source_path:
        payload["source_path"] = args.source_path
    if args.output_subdir:
        payload["output_subdir"] = args.output_subdir
    if args.trace_id:
        payload["trace_id"] = args.trace_id
    if args.instruction:
        payload["instruction"] = args.instruction
    return payload


def _print_summary(upload: dict[str, Any], review: dict[str, Any], elapsed: float) -> None:
    public = review.get("review") or {}
    summary = public.get("summary") or {}
    result = review.get("review_result") or {}
    issues = result.get("issues") if isinstance(result, dict) else None
    artifacts = review.get("artifacts") or {}
    output_paths = public.get("artifacts") or {}

    print("=== FastAPI upload + standard review result ===")
    print(f"elapsed_seconds: {elapsed:.1f}")
    print(f"uploaded_virtual_path: {upload.get('virtual_path')}")
    print(f"status: {review.get('status')}")
    print(f"passed: {review.get('passed')}")
    print(f"review_options: {json.dumps(review.get('review_options'), ensure_ascii=False)}")
    print(f"summary: {json.dumps(summary, ensure_ascii=False)}")
    if isinstance(issues, list):
        print(f"issues_count: {len(issues)}")
        scope_counts: dict[str, int] = {}
        track_counts: dict[str, int] = {}
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            scope = str(issue.get("scope") or "")
            track = str(issue.get("track") or "")
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
            track_counts[track] = track_counts.get(track, 0) + 1
        print(f"issues_by_scope: {json.dumps(scope_counts, ensure_ascii=False)}")
        print(f"issues_by_track: {json.dumps(track_counts, ensure_ascii=False)}")
        for issue in issues[:3]:
            if isinstance(issue, dict):
                print(
                    "issue_preview:",
                    json.dumps(
                        {
                            "scope": issue.get("scope"),
                            "rule_name": issue.get("rule_name"),
                            "status": issue.get("status"),
                            "actual": issue.get("actual"),
                        },
                        ensure_ascii=False,
                    ),
                )
    print(f"output_paths: {json.dumps(output_paths, ensure_ascii=False)}")
    print(f"registered_artifacts: {json.dumps(artifacts, ensure_ascii=False)}")
    report = review.get("review_report_markdown") or ""
    if report:
        compact = " ".join(str(report).split())
        print(f"report_preview: {compact[:500]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test FastAPI upload and standard review endpoints.")
    parser.add_argument("--file", required=True, help="Local file to upload.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="FastAPI base URL.")
    parser.add_argument("--thread-id", default=None, help="Thread ID. Defaults to a generated UUID.")
    parser.add_argument(
        "--mode",
        default="scoped_content",
        choices=[
            "content_and_format",
            "content_only",
            "format_only",
            "full_document_content",
            "scoped_content",
            "line_range_content",
        ],
    )
    parser.add_argument(
        "--target-scopes",
        default="foreword",
        help="Comma-separated scopes, e.g. foreword or scope,normative_references.",
    )
    parser.add_argument("--line-start", type=int, default=None)
    parser.add_argument("--line-end", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--max-review-rounds", type=int, default=None)
    parser.add_argument("--disable-widen", action="store_true")
    parser.add_argument("--force-rebuild-index", action="store_true")
    parser.add_argument("--source-path", default=None)
    parser.add_argument("--output-subdir", default=None)
    parser.add_argument("--trace-id", default=None)
    parser.add_argument("--instruction", default=None)
    parser.add_argument("--return-report-content", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--return-result-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--save-response", default=None, help="Optional path to save full review JSON.")
    parser.add_argument("--timeout", type=int, default=900, help="HTTP timeout seconds for review call.")
    args = parser.parse_args()

    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    args.thread_id = args.thread_id or str(uuid.uuid4())

    start = time.time()
    health = _request_json("GET", f"{base_url}/health", timeout=30)
    print(f"health: {json.dumps(health, ensure_ascii=False)}")

    upload = _upload_file(base_url, args.thread_id, file_path, timeout=120)
    virtual_path = str(upload.get("virtual_path") or "")
    if not virtual_path.startswith("/workspace/"):
        raise RuntimeError(f"Upload response did not contain a /workspace/ virtual_path: {upload}")

    payload = _review_payload(args, virtual_path)
    review = _request_json(
        "POST",
        f"{base_url}/api/review-jobs/standard-review",
        body=payload,
        timeout=args.timeout,
    )
    elapsed = time.time() - start
    _print_summary(upload, review, elapsed)

    if args.save_response:
        save_path = Path(args.save_response).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved_response: {save_path}")

    return 0 if review.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
