import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import threading
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import langextract as lx
from dotenv import load_dotenv
from langextract.providers.openai import OpenAILanguageModel
import os

input_dir = r"D:\standards\output\mdOutput\行业标准"
output_dir = r"D:\standards\output\extractOutput"

# 提供给LLM的信息提取提示词，规定了需要提取的信息字段和要求
PROMPT = textwrap.dedent(
    """
    你是一个专业的文档关键信息提取师，请提取用户上传的国家标准文档中的核心实体信息。请严格按照要求规则提取，没有则留空：
    1) ICS：当前国家标准的国际标准分类号。
    2) CCS：当前国家标准的中国标准文献分类号。
    3) 标准层级：当前国家标准的层次或类别（如“中华人民共和国国家标准”“中华人民共和国XX行业标准”“XX省/市地方标准”“中华人民共和国国家标准化指导性技术文件”等字样）。
    4) 标准号：当前国家标准的正式编号。
    5) 代替标准号：被代替的旧版标准编号（包含“代替”“部分代替”）。
    6) 发布日期：标准的官方发布时间。
    7) 实施日期：标准的实施或执行日期。
    8) 标准中文名称：标准的中文标题（可能出现换行，需要拼接）。
    9) 标准英文名称：标准中文标题下方对应的英文全称，要注意英文格式正确，单词之间要有空格（可能出现换行，需要拼接）。
    10) 采标信息：标准英文名称后方被括号包裹的信息（如 ISO 6660:1993，MOD)
    11) 提出单位：负责提出该标准的机构名称（多个请拆分）。
    12) 归口单位：负责归口该标准的机构名称（多个请拆分）。
    13) 起草单位：负责起草该标准的机构名称（多个请拆分）。
    14) 起草人：起草该标准的具体人员姓名（多个请拆分）。
    15) 引用文件：“规范性引用文件”章节中列出的标准编号及其名称，要确保提取信息完整【不包含“GB/T 1.1—2020《标准化工作导则第1部分：标准化文件的结构和起草规则》”】。
    16) 专业术语：“术语和定义”章节中列出的专业词汇，必须同时包含中文和英文，要注意英文格式正确，单词之间要有空格。

    【必须遵循】以下规则:
    - extraction_text 必须尽量与原文一致，不可捏造内容。仅允许对英文断词（若原文中出现了连续的单词需要正确处理断词）、标准号格式、ICS 格式和 CCS 格式做必要规范化：
     - 标准号由文件代号、顺序号及发布年份号构成。文件代号由大写拉丁字母和/或符号“/”组成，顺序号由阿拉伯数字组成，发布年份号由四位阿拉伯数字组成，顺序号和年份号之间使用一字线形式的连接号。例如：GB/T XXXXX-XXXX。
     - CCS 信息在原文中正确格式应为"CCS X xx"、"X xx"或"Xxx",你只需要提取为"Xxx"（X 为大写拉丁字母字母，x 为阿拉伯数字）。若在原文中出现非正确格式内容，请检查是否被写入原文中的 ICS 信息中，确保正确提取 CCS。
     - ICS 信息在原文中正确格式应为"ICS xx.xxx.xx"或"ICS xx.xxx"，你只需要提取为"xx.xxx.xx"或"xx.xxx"（x 为阿拉伯数字）。若在原文中出现非正确格式内容，请检查是否是混入了 CCS 内容并做必要的修改。
    - 若某字段未找到，extraction_text 输出空字符串 ""，【不要】输出 null。
    - attributes 【必须】是 null，不要输出字符串或数组。
    - 按出现顺序抽取，不要重叠文本片段。
    
    """
).strip()

# 给模型一个示例输入/输出，提高抽取稳定性和准确率。示例中包含了所有16个字段的正确抽取结果，且在文本中模拟了常见的格式和排版特点。
EXAMPLES = [
    lx.data.ExampleData(
        text=textwrap.dedent(
            """
            标准正式编号: GB/T 12345-2026
            代替GB/T 12345-2020
            ICS 12.300
            A 12
            文件代号: GB
            文件的层次或类别: 中华人民共和国国家标准
            发布机构: 中国国家标准化管理委员会

            # 智能体与检索增强生成系统规范
            # SpecificationforAgentandRetrieval-AugmentedGenerationSystem
            (ISO 6660:1993，MOD)
            2026-03-01 发布
            2026-10-01 实施
            
            
            # 前言
            本标准由中华人民共和国工业和信息化部提出。
            本标准由中国人工智能标准化协会归口。
            本标准起草单位: AI大模型工程院、某某科技。
            本标准主要起草人: 张三、李四。
            
            # 2 规范性引用文件
            下列文件中的内容通过文中的规范性引用而构成本文件必不可少的条款。
            GB/T11111 向量检索基础
            
            # 3 术语和定义
            下列术语和定义适用于本文件。
            混合检索 hybrid search
            """
        ).strip(),
        extractions=[
            lx.data.Extraction(extraction_class="ICS", extraction_text="12.300", attributes=None),
            lx.data.Extraction(extraction_class="CCS", extraction_text="A12", attributes=None),
            lx.data.Extraction(extraction_class="标准层级", extraction_text="中华人民共和国国家标准", attributes=None),
            lx.data.Extraction(extraction_class="标准号", extraction_text="GB/T 12345-2026", attributes=None),
            lx.data.Extraction(extraction_class="代替标准号", extraction_text="GB/T 12345-2020", attributes=None),
            lx.data.Extraction(extraction_class="发布日期", extraction_text="2026-03-01", attributes=None),
            lx.data.Extraction(extraction_class="实施日期", extraction_text="2026-10-01", attributes=None),
            lx.data.Extraction(extraction_class="标准中文名称", extraction_text="智能体与检索增强生成系统规范", attributes=None),
            lx.data.Extraction(extraction_class="标准英文名称", extraction_text="Specification for Agent and Retrieval-Augmented Generation System", attributes=None),
            lx.data.Extraction(extraction_class="采标信息", extraction_text="ISO 6660:1993，MOD", attributes=None),
            lx.data.Extraction(extraction_class="提出单位", extraction_text="中华人民共和国工业和信息化部", attributes=None),
            lx.data.Extraction(extraction_class="归口单位", extraction_text="中国人工智能标准化协会", attributes=None),
            lx.data.Extraction(extraction_class="起草单位", extraction_text="AI大模型工程院", attributes=None),
            lx.data.Extraction(extraction_class="起草单位", extraction_text="某某科技", attributes=None),
            lx.data.Extraction(extraction_class="起草人", extraction_text="张三", attributes=None),
            lx.data.Extraction(extraction_class="起草人", extraction_text="李四", attributes=None),
            lx.data.Extraction(extraction_class="引用文件", extraction_text="GB/T 11111 向量检索基础", attributes=None),
            lx.data.Extraction(extraction_class="专业术语", extraction_text="混合检索 hybrid search", attributes=None),
        ],
    )
]

# 所有提取字段信息
TARGET_CLASSES = [
    "ICS",
    "CCS",
    "标准层级",
    "标准号",
    "代替标准号",
    "发布日期",
    "实施日期",
    "标准中文名称",
    "标准英文名称",
    "采标信息",
    "提出单位",
    "归口单位",
    "起草单位",
    "起草人",
    "引用文件",
    "专业术语",
]

# 单值字段
SINGLE_VALUE_CLASSES = [
    "ICS",
    "CCS",
    "标准层级",
    "标准号",
    "代替标准号",
    "发布日期",
    "实施日期",
    "标准中文名称",
    "标准英文名称",
    "采标信息",
]

# 多值字段（数组形式）
MULTI_VALUE_CLASSES = [
    "提出单位",
    "归口单位",
    "起草单位",
    "起草人",
    "引用文件",
    "专业术语",
]

PRINT_LOCK = threading.Lock()

def safe_print(message: str) -> None:
    """多线程下串行输出，避免日志互相穿插"""
    with PRINT_LOCK:
        print(message, flush=True)

def safe_print_block(lines: List[str]) -> None:
    """将同一任务的多行日志一次性输出，保持可读性"""
    with PRINT_LOCK:
        print("\n".join(lines), flush=True)


def list_markdown_files(md_dir: Path) -> List[Path]:
    """列出目录下待处理的 Markdown 文件（仅顶层，不递归）。"""
    files = sorted(md_dir.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"No Markdown files found under: {md_dir}")
    return files


def get_output_paths(md_path: Path, output_dir: Path) -> Dict[str, Path]:
    """根据输入 md 文件名，统一生成三类输出文件路径。"""
    output_base = f"{md_path.stem}_extraction"
    return {
        "annotated": output_dir / f"{output_base}.jsonl",
        "normalized": output_dir / f"{output_base}.json",
        "aggregated": output_dir / f"{output_base}_result.json",
    }


def has_existing_outputs(md_path: Path, output_dir: Path, selected_outputs: List[str]) -> bool:
    """判断该 md 是否已经生成本次所选输出。"""
    paths = get_output_paths(md_path=md_path, output_dir=output_dir)
    return all(paths[name].exists() for name in selected_outputs)


def slice_metadata_scope(text: str, scope_mode: str) -> str:
    """按 scope_mode 截取文本范围。

    - full: 使用全文(默认方式)，适合小文件或需要全局上下文的情况。
    - metadata: 截取到“4 范围”之前，聚焦首页、前言和正文第2、3章中的元信息，无需全文处理
    """
    if scope_mode == "full":
        return text

    # Metadata fields are expected before chapter "1 范围".
    m = re.search(r"(?im)^\s*#{1,6}\s*(?:第\s*4\s*章|4)(?!\s*[\.．]\s*\d)\s*(?:[、.．:：]\s*)?\S.*$", text)
    if not m:
        return text
    scoped = text[: m.start()].strip()
    return scoped or text


def jsonl_to_structured_json(jsonl_path: Path) -> Any:
    """把 jsonl 内容转为标准 JSON 结构（仅变格式，不改内容）。"""
    records: List[Any] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records[0] if len(records) == 1 else records


def resolve_selected_outputs(raw_outputs: List[str]) -> List[str]:
    """把 --outputs 参数解析为最终输出集合。"""
    if "all" in raw_outputs:
        return ["annotated", "normalized", "aggregated"]
    return list(dict.fromkeys(raw_outputs))


def infer_standard_nature(file_number: str) -> str:
    code = (file_number or "").strip().upper()
    if not code:
        return ""

    compact = re.sub(r"\s+", " ", code)

    if re.match(r"^[A-Z]{2}/Z", compact):
        return "指导性技术文件"
    if re.match(r"^[A-Z]{2}/T", compact):
        return "推荐性"
    if compact.startswith("GA"):
        return "推荐性"
    if compact.startswith("GB"):
        return "强制性"
    return ""


def build_extraction_result(result: Any, source_name: str) -> Dict[str, Any]:
    """ 固定输出 schema：单值字段用字符串，多值字段用去重列表。"""
    aggregated: Dict[str, Any] = {k: "" for k in SINGLE_VALUE_CLASSES}
    for k in MULTI_VALUE_CLASSES:
        aggregated[k] = []
    aggregated["标准性质"] = ""
    aggregated["制/修订"] = ""

    # 先做去空、按类别整合，并按 (class, text) 去重。
    # 对重复 text 保留最早出现位置，供单值字段“最先出现”决策使用。
    integrated: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in TARGET_CLASSES}

    for item in result.extractions:
        cls = getattr(item, "extraction_class", None)
        txt = (getattr(item, "extraction_text", "") or "").strip()
        # 过滤空文本与非目标字段。
        if not isinstance(cls, str):
            continue
        if not txt or cls not in integrated:
            continue

        char_interval = getattr(item, "char_interval", None)
        start_pos = getattr(char_interval, "start_pos", None) if char_interval else None

        existing = integrated[cls].get(txt)
        if not existing:
            integrated[cls][txt] = {
                "text": txt,
                "start_pos": start_pos,
            }
        else:
            old_start = existing["start_pos"]
            if old_start is None or (start_pos is not None and start_pos < old_start):
                existing["start_pos"] = start_pos

    # 单值字段：保留 char_interval 最小（最先出现）的那个。
    
    for cls in SINGLE_VALUE_CLASSES:
        candidates = list(integrated[cls].values())
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x["start_pos"] is None, x["start_pos"] if x["start_pos"] is not None else float("inf")))
        aggregated[cls] = candidates[0]["text"]

    # 多值字段：保留全部去重后的 text。
    for cls in MULTI_VALUE_CLASSES:
        candidates = list(integrated[cls].values())
        candidates.sort(key=lambda x: (x["start_pos"] is None, x["start_pos"] if x["start_pos"] is not None else float("inf"), x["text"]))
        aggregated[cls] = [c["text"] for c in candidates]

    aggregated["标准性质"] = infer_standard_nature(aggregated["标准号"])
    aggregated["制/修订"] = "修订" if aggregated["代替标准号"].strip() else "制订"

    aggregated["源文件"] = source_name
    return aggregated


def build_model(api_key: str, model_id: str, base_url: str | None = None) -> OpenAILanguageModel:
    """构建 DashScope OpenAI 兼容模型客户端。"""
    return OpenAILanguageModel(
        model_id=model_id,
        base_url=base_url,
        api_key=api_key,
    )


def run_extraction(text: str, model: OpenAILanguageModel) -> Any:
    """执行一次主抽取流程"""
    common_kwargs = {
        "text_or_documents": text,
        "model": model,
        "batch_length": 40,
        "max_workers": 20,
        "max_char_buffer": 1000,
    }

    return lx.extract(
        prompt_description=PROMPT,
        examples=EXAMPLES,
        extraction_passes=1,
        **common_kwargs,
    )


def process_markdown_file(
    md_path: Path,
    output_dir: Path,
    selected_outputs: List[str],
    scope_mode: str,
    api_key: str,
    model_id: str,
    base_url: str | None,
) -> Dict[str, Any]:
    """处理单个 Markdown 文件并写入所选输出。"""
    text = md_path.read_text(encoding="utf-8")
    text = slice_metadata_scope(text, scope_mode)
    # print(f"Using Markdown: {md_path}")
    # print(f"Input chars after scope={scope_mode}: {len(text)}")
    log_lines = [
        f"[{md_path.name}] Using Markdown: {md_path}",
        f"[{md_path.name}] Input chars after scope={scope_mode}: {len(text)}",
    ]

    # 每个任务独立构建模型，避免共享客户端在线程间潜在状态冲突。
    model = build_model(api_key=api_key, model_id=model_id, base_url=base_url)
    result = run_extraction(text=text, model=model)

    output_paths = get_output_paths(md_path=md_path, output_dir=output_dir)
    if "annotated" in selected_outputs:
        lx.io.save_annotated_documents(
            iter([result]),
            output_name=output_paths["annotated"].name,
            output_dir=str(output_dir),
        )

    if "normalized" in selected_outputs:
        if output_paths["annotated"].exists():
            normalized = jsonl_to_structured_json(output_paths["annotated"])
        else:
            temp_annotated = output_dir / f".__tmp_{md_path.stem}_extraction.jsonl"
            lx.io.save_annotated_documents(
                iter([result]),
                output_name=temp_annotated.name,
                output_dir=str(output_dir),
            )
            try:
                normalized = jsonl_to_structured_json(temp_annotated)
            finally:
                if temp_annotated.exists():
                    temp_annotated.unlink()

        normalized_path = output_paths["normalized"]
        with normalized_path.open("w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)

    if "aggregated" in selected_outputs:
        extraction_result = build_extraction_result(result=result, source_name=md_path.name)
        extraction_result_path = output_paths["aggregated"]
        with extraction_result_path.open("w", encoding="utf-8") as f:
            json.dump(extraction_result, f, ensure_ascii=False, indent=2)

    if "annotated" in selected_outputs:
        # print(f"Annotated output: {output_paths['annotated']}")
        log_lines.append(f"[{md_path.name}] Annotated output: {output_paths['annotated']}")
    if "normalized" in selected_outputs:
        # print(f"Normalized output: {output_paths['normalized']}")
        log_lines.append(f"[{md_path.name}] Normalized output: {output_paths['normalized']}")
    if "aggregated" in selected_outputs:
        # print(f"Aggregated output: {output_paths['aggregated']}")
        log_lines.append(f"[{md_path.name}] Aggregated output: {output_paths['aggregated']}")
    # print(f"Total extracted items: {len(result.extractions)}")
    log_lines.append(f"[{md_path.name}] Total extracted items: {len(result.extractions)}")
    safe_print_block(log_lines)

    return {
        "md_path": str(md_path),
        "extracted_items": len(result.extractions),
    }


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Extract 12 metadata fields from Markdown standard files.")
    parser.add_argument("--md-dir", default=input_dir, help="Directory that contains Markdown files.")
    parser.add_argument("--output-dir", default=output_dir, help="Directory to store extraction outputs.")
    parser.add_argument("--scope-mode", choices=["metadata", "full"], default="metadata", help="Text scope for extraction.")
    parser.add_argument("--doc-concurrency", type=int, default=1, help="Number of Markdown files to process concurrently.")
    parser.add_argument(
        "--outputs",
        nargs="+",
        choices=["all", "annotated", "normalized", "aggregated"],
        default=["aggregated"],
        help="Choose output files to generate. Default: all.",
    )
    return parser.parse_args()


def main() -> None:
    # 1) 解析参数与环境变量
    args = parse_args()
    load_dotenv()
    base_dir = Path(__file__).resolve().parent
    selected_outputs = resolve_selected_outputs(args.outputs)

    # 2) 规范化输入/输出目录
    md_dir = (base_dir / args.md_dir).resolve() if not Path(args.md_dir).is_absolute() else Path(args.md_dir)
    output_dir = (base_dir / args.output_dir).resolve() if not Path(args.output_dir).is_absolute() else Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 3) 收集输入文件，并在多文件模式下跳过已有完整输出的任务
    md_files = list_markdown_files(md_dir)
    to_process: List[Path] = []
    skipped_files: List[Path] = []

    # 跳过已有结果的md文件解析任务
    for md_path in md_files:
        if has_existing_outputs(md_path=md_path, output_dir=output_dir, selected_outputs=selected_outputs):
            skipped_files.append(md_path)
            continue
        to_process.append(md_path)

    # print(f"Found {len(md_files)} Markdown files under: {md_dir}")
    safe_print(f"Found {len(md_files)} Markdown files under: {md_dir}")
    if skipped_files:
        safe_print(f"Skip existing results: {len(skipped_files)}")
        for skipped in skipped_files:
            safe_print(f"- skipped: {skipped.name}")
    safe_print(f"Selected outputs: {', '.join(selected_outputs)}")

    # 若全部已完成，直接退出，避免无意义调用模型。
    if not to_process:
        safe_print("All Markdown files already have extraction outputs. Nothing to process.")
        return

    # 4) 仅在有待处理任务时再校验环境变量，并初始化模型
    api_key = os.getenv("DASHSCOPE_API_KEY")
    model_id = os.getenv("MODEL_ID", "qwen3.5-flash")
    base_url = os.getenv("DASHSCOPE_BASE_URL")
    if not api_key:
        raise RuntimeError("API key not found. Set DASHSCOPE_API_KEY in .env.")

    model = build_model(api_key=api_key, model_id=model_id, base_url=base_url)
    safe_print(f"Pending extraction tasks: {len(to_process)}")
    safe_print(f"Doc concurrency: {max(1, args.doc_concurrency)}")

    # 5) 逐文件执行抽取与落盘
    success_count = 0
    failed_files: List[str] = []

    # 预构建一次模型做启动校验，避免全部任务提交后才发现配置问题。
    _ = model

    with ThreadPoolExecutor(max_workers=max(1, args.doc_concurrency)) as executor:
        futures = {
            executor.submit(
                process_markdown_file,
                md_path=md_path,
                output_dir=output_dir,
                selected_outputs=selected_outputs,
                scope_mode=args.scope_mode,
                api_key=api_key,
                model_id=model_id,
                base_url=base_url,
            ): md_path
            for md_path in to_process
        }

        for future in as_completed(futures):
            md_path = futures[future]
            try:
                future.result()
                success_count += 1
            except Exception as exc:
                failed_files.append(f"{md_path}: {exc}")
                safe_print(f"Error processing {md_path}: {exc}")

    # 6) 打印任务汇总
    safe_print(
        f"Finished. Success: {success_count}, "
        f"Skipped: {len(skipped_files)}, Failed: {len(failed_files)}"
    )
    if failed_files:
        safe_print("Failed files:")
        for item in failed_files:
            safe_print(f"- {item}")


if __name__ == "__main__":
    main()


