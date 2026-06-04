"""从 rules_test.md 重建标准审核规则的 FAISS / TF-IDF 向量索引（CLI）。

作用
----
- 与早期 Skill `D:/Chinese_national_standards_docs_Review-SKILL/scripts/
  rebuild_rules_index.py` 行为对齐。
- 默认从 `src/standard_document_assistant/resources/review_rules/rules_test.md`
  读规则文本，向**同一目录**写出索引产物：
  - FAISS 后端：`rules.faiss` + `rules.faiss.meta.json` + `tfidf_vectorizer.pkl`
  - JSON 回退：`rules.faiss.json`（纯 Python TF-IDF，不依赖 `faiss-cpu` /
    `scikit-learn`，保证 CI 在最小依赖下仍可跑）。
- 当 `faiss-cpu` / `scikit-learn` 不可用时自动回退到 JSON 后端。
- 退出码：`0` = 成功；`2` = 重建失败。

参数
----
- `--rules-md <path>`：规则 markdown 路径；缺省沿用配置中
  `standard_review.rules_md`。
- `--index-dir <path>`：索引输出目录；缺省沿用配置中
  `standard_review.index_dir`。
- `--backend {auto,faiss,tfidf_json}`：
  - `auto`（默认）：FAISS 优先，失败回退到 `tfidf_json`；
  - `faiss`：仅 FAISS，不允许回退；
  - `tfidf_json`：仅 JSON 后端。
- `--force-rebuild`：忽略已有索引，强制重建。

使用方式
-------
在项目根目录 `d:\\deep-agents\\` 下执行：

```powershell
# 用配置默认值，auto 后端
python scripts/rebuild_rules_index.py

# 强制重建，FAISS 后端（不允许回退）
python scripts/rebuild_rules_index.py --backend faiss --force-rebuild

# 仅跑纯 Python TF-IDF 后端
python scripts/rebuild_rules_index.py --backend tfidf_json

# 自定义规则 / 输出目录
python scripts/rebuild_rules_index.py --rules-md path/to/rules.md --index-dir path/to/index
```

环境与副作用
------------
- 自动设置 `LANGSMITH_TRACING=false` / `LANGCHAIN_TRACING_V2=false`，
  避免 CLI 触发 LangSmith 网络调用。
- 会调用 `standard_document_assistant.config.load_config()` 读取
  `config.yaml`，再用 `dataclasses.replace` 临时覆盖 `rules_md` /
  `index_dir` 后喂给 `load_knowledge_base`。
- 如果 `index_dir` 与 `constants.REVIEW_RULES_DIR` 解析到同一路径，
  会打印一行确认信息，便于人工核对。

输出
----
成功时打印形如：
```
[rebuild] rules:    ...\resources\review_rules\rules_test.md
[rebuild] index:    ...\resources\review_rules
[rebuild] backend:  auto
[rebuild] force:    False
[rebuild] done: rules=NN backend=faiss source=rebuilt
[rebuild] file: ... exists=True size=NNNNN
[rebuild] index_dir 与常量 REVIEW_RULES_DIR 一致。
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
