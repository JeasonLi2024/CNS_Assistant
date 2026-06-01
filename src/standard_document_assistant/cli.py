"""Small CLI entrypoint for local development."""

from __future__ import annotations

import argparse
import json

from standard_document_assistant.agent import build_standard_document_agent, build_thread_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the standard document Deep Agent.")
    parser.add_argument("message", nargs="?", help="User message to send to the agent.")
    parser.add_argument("--thread-id", default="standard-doc-session-001")
    parser.add_argument("--strict-model", action="store_true", help="Require ChatQwen and DASHSCOPE_API_KEY.")
    parser.add_argument("--print-config-only", action="store_true", help="Only build and print basic graph info.")
    args = parser.parse_args()

    agent = build_standard_document_agent(strict_model=args.strict_model)
    if args.print_config_only:
        print(json.dumps({"status": "ok", "thread_id": args.thread_id}, ensure_ascii=False))
        return
    if not args.message:
        parser.error("缺少 message；或使用 --print-config-only 只检查配置。")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": args.message}]},
        config=build_thread_config(args.thread_id),
    )
    print(result)


if __name__ == "__main__":
    main()

