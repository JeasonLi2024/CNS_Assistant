"""从 rules_test.md 重建 FAISS / TF-IDF 规则索引（CLI）。

与原 Skill
``D:/Chinese_national_standards_docs_Review-SKILL/scripts/rebuild_rules_index.py``
行为对齐：默认读取 ``src/standard_document_assistant/resources/review_rules/rules_test.md``，
向同目录写出 ``rules.faiss`` + ``rules.faiss.meta.json`` + ``tfidf_vectorizer.pkl``。

在 ``faiss-cpu`` / ``scikit-learn`` 不可用的环境下，自动退到
``rules.faiss.json``（纯 Python TF-IDF），保证 CI 仍然可跑。

Windows 用法（在项目根目录）：

```powershell
python scripts/rebuild_rules_index.py
python scripts/rebuild_rules_index.py --backend faiss --force-rebuild
python scripts/rebuild_rules_index.py --backend tfidf_json
```
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 关闭 LangSmith 追踪，避免 CLI 触发网络调用。
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from standard_document_assistant.config import load_config  # noqa: E402
from standard_document_assistant.constants import REVIEW_RULES_DIR  # noqa: E402
from standard_document_assistant.review_core.knowledge_base import (  # noqa: E402
    load_knowledge_base,
)


def main() -> None:
    config = load_config().standard_review
    parser = argparse.ArgumentParser(
        description="Rebuild the standard review rules vector index (FAISS or TF-IDF).",
    )
    parser.add_argument(
        "--rules-md",
        type=Path,
        default=None,
        help="规则 markdown 路径；缺省沿用配置中的 standard_review.rules_md。",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help="索引输出目录；缺省沿用配置中的 standard_review.index_dir。",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "faiss", "tfidf_json"],
        default="auto",
        help="索引后端：auto=FAISS 优先失败退 JSON；faiss=仅 FAISS；tfidf_json=仅 JSON 回退。",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="忽略已有索引，强制重建。",
    )
    args = parser.parse_args()

    rules_path = args.rules_md or Path(config.rules_md)
    if not rules_path.is_absolute():
        rules_path = PROJECT_ROOT / rules_path

    index_dir = args.index_dir or Path(config.index_dir)
    if not index_dir.is_absolute():
        index_dir = PROJECT_ROOT / index_dir
    index_dir.mkdir(parents=True, exist_ok=True)

    print(f"[rebuild] rules:    {rules_path}")
    print(f"[rebuild] index:    {index_dir}")
    print(f"[rebuild] backend:  {args.backend}")
    print(f"[rebuild] force:    {args.force_rebuild}")

    # 用临时覆盖 rules_md / index_dir 的 config 喂给 load_knowledge_base
    from dataclasses import replace

    patched = replace(config, rules_md=str(rules_path), index_dir=str(index_dir))
    try:
        kb, meta = load_knowledge_base(
            patched,
            force_rebuild=args.force_rebuild,
            backend=args.backend,
        )
    except Exception as exc:
        print(f"[rebuild] FAILED: {exc}", file=sys.stderr)
        sys.exit(2)

    backend_actual = meta.get("index_backend", "unknown")
    rules_loaded = meta.get("rules_loaded", len(kb.rules))
    index_source = meta.get("index_source", "rebuilt")
    print(
        f"[rebuild] done: rules={rules_loaded} backend={backend_actual} source={index_source}"
    )
    for warning in meta.get("warnings") or []:
        print(f"[rebuild] warning: {warning}")

    # 文件清单自检
    expected = {
        "faiss": [
            index_dir / "rules.faiss",
            index_dir / "rules.faiss.meta.json",
            index_dir / "tfidf_vectorizer.pkl",
        ],
        "tfidf_json": [index_dir / "rules.faiss.json"],
    }
    files = expected.get(backend_actual, [])
    for f in files:
        exists = f.exists()
        size = f.stat().st_size if exists else 0
        print(f"[rebuild] file: {f} exists={exists} size={size}")

    # 同步旁路检查 REVIEW_RULES_DIR
    if str(index_dir.resolve()) == str(REVIEW_RULES_DIR.resolve()):
        print("[rebuild] index_dir 与常量 REVIEW_RULES_DIR 一致。")

    sys.exit(0)


if __name__ == "__main__":
    main()
