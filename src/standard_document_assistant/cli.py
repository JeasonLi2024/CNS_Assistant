"""Small CLI entrypoint for local development."""

from __future__ import annotations

import argparse
import json

from standard_document_assistant.agent import build_standard_document_agent, build_thread_config
from standard_document_assistant.artifacts import (
    copy_artifact_to_destination,
    describe_downloadable_artifact,
    list_thread_artifacts,
    public_artifact_record,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standard document Deep Agent.")
    parser.add_argument("message", nargs="?", help="User message to send to the agent.")
    parser.add_argument("--thread-id", default="standard-doc-session-001")
    parser.add_argument("--strict-model", action="store_true", help="Require ChatQwen and DASHSCOPE_API_KEY.")
    parser.add_argument("--print-config-only", action="store_true", help="Only build and print basic graph info.")
    parser.add_argument(
        "--download-artifact",
        metavar="VIRTUAL_PATH",
        help="Copy a /workspace artifact to --download-to destination.",
    )
    parser.add_argument(
        "--download-to",
        metavar="DEST",
        help="Destination path used with --download-artifact.",
    )
    parser.add_argument(
        "--describe-artifact",
        metavar="VIRTUAL_PATH",
        help="Print download/open info for a workspace artifact.",
    )
    parser.add_argument(
        "--list-artifacts",
        metavar="THREAD_ID",
        help="List application-layer artifact records registered for a thread.",
    )
    args = parser.parse_args()

    if args.list_artifacts:
        records = list_thread_artifacts(args.list_artifacts)
        print(
            json.dumps(
                [public_artifact_record(record) for record in records],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.describe_artifact:
        print(
            json.dumps(
                describe_downloadable_artifact(args.describe_artifact),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.download_artifact:
        if not args.download_to:
            parser.error("--download-artifact 需要同时提供 --download-to。")
        target = copy_artifact_to_destination(args.download_artifact, args.download_to)
        print(json.dumps({"status": "ok", "copied_to": str(target)}, ensure_ascii=False))
        return

    agent = build_standard_document_agent(strict_model=args.strict_model)
    if args.print_config_only:
        print(json.dumps({"status": "ok", "thread_id": args.thread_id}, ensure_ascii=False))
        return
    if not args.message:
        parser.error("缺少 message；或使用 --print-config-only / --describe-artifact / --download-artifact / --list-artifacts。")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.message}]},
        config=build_thread_config(args.thread_id),
    )
    print(result)


if __name__ == "__main__":
    main()
