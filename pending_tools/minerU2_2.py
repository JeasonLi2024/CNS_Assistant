import requests
import json
import os
import glob
import argparse
import re
import threading
import time
import zipfile
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

"""
minerU2.py
===========

主要功能
--------
1. 批量读取 `data` 目录中的 PDF，调用 MinerU 接口进行解析（请求 ZIP 返回）。
2. 自动保存服务端返回的 ZIP 到 `output/zipOutput`，用于留档与断点续跑判定。
3. 在客户端解析 ZIP：提取 Markdown、middle_json、content_list、图片。
4. Markdown 以标准号优先命名，并按标准层级归档到：
   - `output/mdOutput/国家标准`
   - `output/mdOutput/行业标准`
   - `output/mdOutput/地方标准`
   - `output/mdOutput/其他`
5. 图片按 content_list 规则重命名：
   - 优先使用 `图x` / `表x` 标题；
   - 支持 `a）` 这类子图编号回溯前一 text 拼接；
   - 无可用标题时回退 `001.jpg`、`002.jpg`。
6. 支持并发处理、最大处理数量限制、实时写入批处理结果日志。

实现原理
--------
1. 参数集中：`MINERU_REQUEST_OPTIONS` 统一维护请求参数；`_build_request_data()` 负责归一化与联动。
2. 解析链路：`process_pdf()` 负责网络请求和 ZIP 落盘，`_parse_result_zip()` 负责本地产物解析/重命名/引用替换。
3. 命名与归档：
   - `_extract_cover_metadata()` 从 middle_json 抽取标准号等封面信息；
   - `_build_md_base_name()` 生成 md 主文件名；
   - `_resolve_md_category()` 根据标准号前缀决定子目录。
4. 断点续跑：默认检测 `zipOutput/{pdf_stem}.zip` 是否存在，存在则跳过。

常用命令
--------
在仓库根目录执行（Windows PowerShell）：

```powershell
python .\code\minerU2_2.py
python .\code\minerU2_2.py --max-pdfs 10 --max-workers 3
```

使用说明
--------
1. 如需关闭图片解析，将 `MINERU_REQUEST_OPTIONS['return_images']` 改为 `'false'`。
2. 如需另存 middle_json/content_list，将 `JOSN_SAVE_OPYIONS` 对应开关改为 `'true'`。
3. 需要迁移接口地址时，优先修改主入口中 `api_base_url` 传参。
"""

# docker compose   -p  mineru  --profile api   up  -d

MAX_PDF_SIZE_KB = 100000
MAX_PDF_SIZE_BYTES = MAX_PDF_SIZE_KB * 1024

# MinerU 请求参数集中配置：后续手动调整只需改这一处。
MINERU_REQUEST_OPTIONS = {
    'backend': 'vlm-auto-engine',
    'parse_method': 'auto',
    'lang_list': ['ch'],
    'return_md': 'true',
    'return_images': 'true',
    'return_middle_json': 'true',
    'return_content_list': 'true',
    'response_format_zip': 'true',
}

# 解析结果另存开关：控制是否把 middle_json/content_list 单独落盘。
JOSN_SAVE_OPYIONS = {
    'save_middle_json': 'false',
    'save_content_list': 'false',
}


def _to_bool(value, default=False) -> bool:
    """将配置中的字符串/布尔值统一转为 Python bool。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _build_request_data() -> dict:
    """基于集中配置构建最终请求参数，并处理联动规则。"""
    data = dict(MINERU_REQUEST_OPTIONS)
    return_images = _to_bool(data.get('return_images', False), default=False)
    data['return_images'] = str(return_images).lower()
    data['response_format_zip'] = 'true'

    # 图片下载依赖 zip 和 content_list，开启图片时强制打开相关返回项。
    if return_images:
        data['response_format_zip'] = 'true'
        data['return_content_list'] = 'true'

    return data


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数（与原始代码保持一致，未做改动）
# ─────────────────────────────────────────────────────────────────────────────

def _strip_pdf_suffix(filename: str) -> str:
    return filename[:-4] if filename.lower().endswith('.pdf') else filename


def _normalize_middle_json(middle_json):
    """兼容多种 middle_json 形态：bytes / str / dict / list。"""
    if isinstance(middle_json, (bytes, bytearray)):
        middle_json = middle_json.decode('utf-8', errors='ignore')
    if isinstance(middle_json, str):
        payload = middle_json.strip()
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {'raw_middle_json': middle_json}
    if isinstance(middle_json, (dict, list)):
        return middle_json
    if middle_json is None:
        return {}
    return middle_json


def _extract_header_text(block: dict) -> str:
    """从 middle_json 的 header/footer block 中提取纯文本。"""
    parts = []
    for line in block.get('lines', []) or []:
        if not isinstance(line, dict):
            continue
        for span in line.get('spans', []) or []:
            if not isinstance(span, dict):
                continue
            content = span.get('content')
            if content:
                if isinstance(content, str):
                    parts.append(content.strip())
                elif isinstance(content, dict):
                    text_value = content.get('text') or content.get('content') or ''
                    if text_value:
                        parts.append(str(text_value).strip())
                else:
                    parts.append(str(content).strip())
    return ''.join(parts).strip()


def _extract_cover_metadata(middle_json: dict):
    """提取封面元信息，用于命名、归档和前置元信息补写。"""
    metadata = {
        'standard_number': '',
        'replaced_standard_number': '',
        'ics': '',
        'ccs': '',
        'file_code': '',
        'hierarchy_or_category': '',
        'issuing_organizations': '',
    }

    if not isinstance(middle_json, dict):
        return metadata

    for page in middle_json.get('pdf_info', []) or []:
        if page.get('page_idx') != 0:
            continue

        issuing_orgs = []
        header_candidates = []

        def _clean(text: str) -> str:
            return re.sub(r'\s+', ' ', (text or '')).strip()

        def _is_ics(text: str) -> bool:
            t = _clean(text).upper()
            return t.startswith('ICS') or '国际标准分类' in text

        def _is_ccs(text: str) -> bool:
            t = _clean(text).upper()
            if t.startswith('CCS') or '中国标准文献分类' in text:
                return True
            compact = t.replace(' ', '')
            return bool(re.fullmatch(r'[A-Z]{1,3}\d{1,3}(?:\.\d+)?', compact))

        def _is_replaced_standard(text: str) -> bool:
            return '代替' in text or '替代' in text

        def _extract_standard_number_by_format(text: str) -> str:
            original = _clean(text)
            if not original:
                return ''
            check_text = original.upper()
            prefix = r'(?:[A-Z]{1,6}\d{0,3}(?:/[A-Z]{1,8}\d{0,3})?)\s+\d+(?:\.\d+)*'
            year = r'(?:\d{4}|\d{2})'
            suffix = r'(?:.*)?'
            if re.fullmatch(rf'{prefix}—{year}{suffix}', check_text):
                return original
            if re.fullmatch(rf'{prefix}-{year}{suffix}', check_text):
                return original
            return ''

        def _is_hierarchy_or_category(text: str) -> bool:
            keywords = ['中华人民共和国国家标准', '国家标准化指导性技术文件', '行业标准', '地方标准']
            return any(k in text for k in keywords)

        def _is_standard_number(text: str) -> bool:
            return bool(_extract_standard_number_by_format(text))

        def _is_file_code(text: str) -> bool:
            t = _clean(text).upper().replace(' ', '')
            if not t or _is_ics(text) or _is_ccs(text):
                return False
            return bool(re.fullmatch(r'[A-Z]{1,6}(?:/[A-Z]{1,6})?', t))

        for block in page.get('discarded_blocks', []) or []:
            block_type = block.get('type')
            block_text = _extract_header_text(block)
            if not block_text:
                continue
            if block_type == 'header':
                header_candidates.append({'index': block.get('index'), 'text': _clean(block_text)})
            elif block_type == 'footer':
                normalized_footer = block_text.strip()
                if normalized_footer == '发布':
                    continue
                if normalized_footer.endswith('发布'):
                    normalized_footer = normalized_footer[:-2].strip()
                if normalized_footer:
                    issuing_orgs.append(normalized_footer)

                    # Phase 1: 语义匹配
        for item in header_candidates:
            text = item['text']
            if not text:
                continue
            if _is_ics(text) and not metadata['ics']:
                metadata['ics'] = text
                continue
            if _is_ccs(text) and not metadata['ccs']:
                metadata['ccs'] = text
                continue
            if _is_replaced_standard(text) and not metadata['replaced_standard_number']:
                extracted = _extract_standard_number_by_format(text)
                metadata['replaced_standard_number'] = extracted if extracted else text
                continue
            if _is_standard_number(text) and not metadata['standard_number']:
                metadata['standard_number'] = _extract_standard_number_by_format(text)
                continue
            if _is_hierarchy_or_category(text) and not metadata['hierarchy_or_category']:
                metadata['hierarchy_or_category'] = text
                continue
            if _is_file_code(text) and not metadata['file_code']:
                metadata['file_code'] = text

                # Phase 1.5: 格式补偿
        if not metadata['standard_number'] or not metadata['replaced_standard_number']:
            for item in header_candidates:
                text = item['text']
                if not text:
                    continue
                extracted = _extract_standard_number_by_format(text)
                if not extracted:
                    continue
                if _is_replaced_standard(text) and not metadata['replaced_standard_number']:
                    metadata['replaced_standard_number'] = extracted
                    continue
                if not metadata['standard_number'] and not _is_replaced_standard(text):
                    metadata['standard_number'] = extracted

                    # Phase 2: index 兜底
        index_to_field = {
            0: 'ics', 1: 'ccs', 2: 'file_code',
            3: 'hierarchy_or_category', 4: 'standard_number', 5: 'replaced_standard_number',
        }
        header_text_by_index = {}
        for candidate in header_candidates:
            candidate_idx = candidate.get('index')
            if candidate_idx not in header_text_by_index and isinstance(candidate_idx, int):
                header_text_by_index[candidate_idx] = candidate.get('text', '')

        for item in header_candidates:
            idx = item.get('index')
            text = item['text']
            if idx not in index_to_field:
                continue
            field_name = index_to_field[idx]
            if metadata.get(field_name):
                continue
            extracted_standard_number = _extract_standard_number_by_format(text)
            if field_name == 'standard_number':
                if extracted_standard_number and not _is_replaced_standard(text):
                    metadata[field_name] = extracted_standard_number
                elif idx == 4:
                    text_idx3 = header_text_by_index.get(3, '')
                    extracted_idx3 = _extract_standard_number_by_format(text_idx3)
                    if extracted_idx3 and not _is_replaced_standard(text_idx3):
                        metadata[field_name] = extracted_idx3
                continue
            if field_name == 'replaced_standard_number':
                if _is_replaced_standard(text):
                    metadata[field_name] = extracted_standard_number if extracted_standard_number else text
                continue
            if field_name == 'file_code' and (_is_ics(text) or _is_ccs(text) or _is_standard_number(text)):
                continue
            if field_name == 'ics' and _is_ccs(text):
                continue
            if field_name == 'ccs' and _is_ics(text):
                continue
            metadata[field_name] = text

        if issuing_orgs:
            metadata['issuing_organizations'] = ' '.join(issuing_orgs)
        break

    return metadata


def _prepend_cover_info_to_md(md_content: str, cover_metadata: dict) -> str:
    """将封面元信息追加到 Markdown 顶部，便于后续检索。"""
    if not isinstance(cover_metadata, dict):
        cover_metadata = {}
    if not isinstance(md_content, str):
        md_content = '' if md_content is None else str(md_content)

    prefix_lines = []
    standard_number = cover_metadata.get('standard_number', '')
    replaced_standard_number = cover_metadata.get('replaced_standard_number', '')

    if standard_number:
        prefix_lines.append(f"标准正式编号：{standard_number}")
    if replaced_standard_number:
        if str(replaced_standard_number).startswith('代替'):
            prefix_lines.append(str(replaced_standard_number))
        else:
            prefix_lines.append(f"代替{replaced_standard_number}")

    ics = cover_metadata.get('ics', '')
    ccs = cover_metadata.get('ccs', '')
    file_code = cover_metadata.get('file_code', '')
    hierarchy_or_category = cover_metadata.get('hierarchy_or_category', '')
    issuing_organizations = cover_metadata.get('issuing_organizations', '')

    if ics:
        prefix_lines.append(f"ICS：{ics}")
    if ccs:
        prefix_lines.append(f"CCS：{ccs}")
    if file_code:
        prefix_lines.append(f"文件代号：{file_code}")
    if hierarchy_or_category:
        prefix_lines.append(f"文件的层次或类别：{hierarchy_or_category}")
    if issuing_organizations:
        prefix_lines.append(f"发布机构：{issuing_organizations}")

    if not prefix_lines:
        return md_content

    return '\n\n'.join(prefix_lines) + '\n\n' + md_content


_MD_NAME_LOCK = threading.Lock()
_RESERVED_MD_PATHS = set()


def _sanitize_filename_component(name: str) -> str:
    """清洗 Windows 文件名非法字符，避免保存失败。"""
    text = '' if name is None else str(name)
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    text = re.sub(r'\s+', ' ', text).strip().rstrip('. ')
    return text


def _build_md_base_name(source_stem: str, cover_metadata: dict) -> str:
    """优先使用标准号命名，缺失时回退原始文件名。"""
    standard_number = ''
    if isinstance(cover_metadata, dict):
        standard_number = str(cover_metadata.get('standard_number', '') or '').strip()
    if standard_number:
        standard_number = re.sub(r'^\s*标准正式编号\s*[:：]\s*', '', standard_number)
        standard_number = _sanitize_filename_component(standard_number)
        if standard_number:
            return standard_number
    fallback = _sanitize_filename_component(source_stem)
    return fallback if fallback else 'output'


def _resolve_md_category(cover_metadata: dict) -> str:
    """根据标准号前缀确定 Markdown 分类目录。"""
    standard_number = ''
    if isinstance(cover_metadata, dict):
        standard_number = str(cover_metadata.get('standard_number', '') or '').strip()

    if not standard_number:
        return '其他'

    normalized = standard_number.upper().replace(' ', '')
    if normalized.startswith('GB'):
        return '国家标准'
    if normalized.startswith('DB'):
        return '地方标准'
    return '行业标准'


_FIG_PREFIX_RE = re.compile(r'^\s*图\s*[^\s].*')
_TABLE_PREFIX_RE = re.compile(r'^\s*表\s*[^\s].*')
_LETTER_LABEL_RE = re.compile(r'^\s*[a-z]\s*[)）]\s*$')
_TRAILING_PUNCT_RE = re.compile(r'[。！？；：,.;!?]+$')


def _strip_trailing_punct(text: str) -> str:
    value = '' if text is None else str(text).rstrip()
    return _TRAILING_PUNCT_RE.sub('', value).rstrip()


def _extract_first_prefixed_caption(captions, prefix: str) -> str:
    """提取 caption 数组中第一个以“图/表”开头的条目。"""
    if not isinstance(captions, list):
        return ''

    prefix_re = _FIG_PREFIX_RE if prefix == '图' else _TABLE_PREFIX_RE
    for item in captions:
        text = '' if item is None else str(item).strip()
        if text and prefix_re.match(text):
            return text
    return ''


def _find_last_table_caption_before_md_anchor(md_content: str, anchor: str) -> str:
    """当表标题不规范时，按 table_body 在 MD 中回溯最近“表x”文本。"""
    if not md_content or not anchor:
        return ''

    pos = md_content.find(anchor)
    if pos < 0:
        return ''

    prefix = md_content[:pos]
    matches = list(re.finditer(r'^\s*表[^\n]*', prefix, flags=re.MULTILINE))
    if not matches:
        return ''
    return matches[-1].group(0).strip()


def _iter_content_list_blocks(content_list):
    """兼容不同 content_list 结构，统一返回可遍历 block 列表。"""
    if isinstance(content_list, list):
        return content_list
    if isinstance(content_list, dict):
        for key in ('content_list', 'items', 'blocks', 'data'):
            value = content_list.get(key)
            if isinstance(value, list):
                return value
    return []


def _build_content_list_name_suggestions(content_list, md_content: str) -> dict:
    """Return {original_img_file_name: suggested_base_name} from content_list rules."""
    suggestions = {}
    blocks = _iter_content_list_blocks(content_list)
    last_text = ''

    for block in blocks:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get('type', '') or '').strip().lower()
        # text block 作为后续子图编号（如 a））的语义前文。
        if block_type == 'text':
            text_value = str(block.get('text', '') or '').strip()
            if text_value:
                last_text = text_value
            continue

        if block_type not in {'image', 'table'}:
            continue

        img_path = str(block.get('img_path', '') or '').strip()
        if not img_path:
            continue
        old_name = os.path.basename(img_path)
        if not old_name:
            continue

        suggested = ''
        # image/table 的命名规则不同：图优先图题，表优先表题。
        if block_type == 'image':
            captions = block.get('image_caption')
            suggested = _extract_first_prefixed_caption(captions, '图')
            if not suggested and isinstance(captions, list):
                lettered = next((str(c).strip() for c in captions if _LETTER_LABEL_RE.match(str(c).strip())), '')
                if lettered and last_text:
                    suggested = f"{_strip_trailing_punct(last_text)}{lettered}"
        else:
            captions = block.get('table_caption')
            suggested = _extract_first_prefixed_caption(captions, '表')
            if (not suggested) and isinstance(captions, list) and captions:
                table_body = str(block.get('table_body', '') or '').strip()
                suggested = _find_last_table_caption_before_md_anchor(md_content, table_body)

        if suggested:
            suggestions[old_name] = suggested

    return suggestions


def _allocate_unique_md_path(md_output_dir: str, base_name: str) -> str:
    """在并发场景下分配唯一 MD 路径，避免同名冲突。"""
    os.makedirs(md_output_dir, exist_ok=True)
    stem = _sanitize_filename_component(base_name) or 'output'
    with _MD_NAME_LOCK:
        idx = 0
        while True:
            suffix = '' if idx == 0 else f'（{idx}）'
            candidate_name = f"{stem}{suffix}.md"
            candidate_path = os.path.join(md_output_dir, candidate_name)
            normalized_path = os.path.normcase(os.path.abspath(candidate_path))
            if normalized_path in _RESERVED_MD_PATHS or os.path.exists(candidate_path):
                idx += 1
                continue
            _RESERVED_MD_PATHS.add(normalized_path)
            return candidate_path

        # ─────────────────────────────────────────────────────────────────────────────


# ZIP 解析：提取 MD / middle_json / 图片
#
# ZIP 内部结构（服务端 create_result_zip 生成）：
#   {pdf_name}/{parse_dir_basename}/{pdf_name}.md
#   {pdf_name}/{parse_dir_basename}/{pdf_name}_middle.json
#   {pdf_name}/{parse_dir_basename}/images/{sha256}.jpg
#
# 处理顺序：
#   1. 读取 ZIP 内容（MD 原文、middle_json、图片字节）
#   2. 解析 middle_json，提取封面元信息
#   3. 处理图片：重命名为自增序号，保存到 <img_output_dir>/<标准号>/ 子目录
#   4. 更新 MD 中的图片引用路径
#   5. 拼接封面信息到 MD 头部
#   6. 保存 MD 和 middle_json
# ─────────────────────────────────────────────────────────────────────────────

def _parse_result_zip(
        zip_bytes: bytes,
        source_stem: str,
        md_output_dir: str,
        img_output_dir: str,
        json_output_dir: str,
        return_images=None,
) -> dict:
    """
    解析服务端 ZIP 结果并落盘：
    - 可选另存 middle_json/content_list；
    - 图片按 content_list 重命名；
    - 同步更新 MD 中图片引用；
    - MD 按标准层级分目录保存。
    """
    result = {'result_md_path': '', 'success': False, 'error': ''}

    # 默认跟随统一配置，不强制要求函数默认值为 True。
    if return_images is None:
        return_images = _to_bool(MINERU_REQUEST_OPTIONS.get('return_images', False), default=False)
    else:
        return_images = _to_bool(return_images, default=False)

    save_middle_json = _to_bool(JOSN_SAVE_OPYIONS.get('save_middle_json', False), default=False)
    save_content_list = _to_bool(JOSN_SAVE_OPYIONS.get('save_content_list', False), default=False)

    # ── 步骤 1：读取 ZIP 内容（一次解包，后续统一在内存中处理）─────────────
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

        json_entries = [n for n in names if n.endswith('_middle.json')]
        content_list_entries = [
            n for n in names
            if n.lower().endswith('.json') and 'content_list' in os.path.basename(n).lower()
        ]
        md_entries = [n for n in names if n.endswith('.md') and not n.endswith('_middle.json')]
        image_entries = sorted([n for n in names if '/images/' in n and not n.endswith('/')])

        middle_json_raw = zf.read(json_entries[0]).decode('utf-8') if json_entries else ''
        content_list_raw = zf.read(content_list_entries[0]).decode('utf-8') if content_list_entries else ''
        raw_md_content = zf.read(md_entries[0]).decode('utf-8') if md_entries else ''

        image_data: dict[str, bytes] = {}
        if return_images:
            for img_entry in image_entries:
                image_data[os.path.basename(img_entry)] = zf.read(img_entry)

                # ── 步骤 2：解析 middle_json，提取封面元信息 ──────────────────────────
    middle_json = _normalize_middle_json(middle_json_raw) if middle_json_raw else {}
    content_list = _normalize_middle_json(content_list_raw) if content_list_raw else {}
    cover_metadata = _extract_cover_metadata(middle_json) if isinstance(middle_json, dict) else {}

    # 可选另存 middle_json/content_list 到 json_output_dir（默认关闭）。
    if middle_json and save_middle_json:
        os.makedirs(json_output_dir, exist_ok=True)
        output_json = os.path.join(json_output_dir, f"middle_{source_stem}.json")
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(middle_json, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        print(f"  ✅ middle_json 已保存至: {output_json}")

    if content_list and save_content_list:
        os.makedirs(json_output_dir, exist_ok=True)
        output_content_list_json = os.path.join(json_output_dir, f"content_list_{source_stem}.json")
        with open(output_content_list_json, 'w', encoding='utf-8') as f:
            json.dump(content_list, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        print(f"  ✅ content_list 已保存至: {output_content_list_json}")

        # ── 步骤 3：处理图片（重命名为自增序号，保存到标准号子目录）────────────
    # 图片目录与 MD 命名都依赖标准号，保证跨文件可追溯。
    img_base_name = _build_md_base_name(source_stem, cover_metadata)
    img_subdir = os.path.join(img_output_dir, img_base_name)
    md_category = _resolve_md_category(cover_metadata)
    target_md_output_dir = os.path.join(md_output_dir, md_category)

    name_suggestions = _build_content_list_name_suggestions(content_list, raw_md_content)

    # rename_map: 记录原始 sha256 文件名 -> 新文件名，供 MD 引用替换。
    rename_map: dict[str, str] = {}
    if return_images and image_data:
        os.makedirs(img_subdir, exist_ok=True)
        used_names = set()
        seq_counter = 1

        def _next_seq_name(ext: str) -> str:
            # 当无可用标题时，回退稳定的 3 位序号命名。
            nonlocal seq_counter
            while True:
                candidate = f"{seq_counter:03d}{ext}"
                seq_counter += 1
                if candidate in used_names:
                    continue
                if os.path.exists(os.path.join(img_subdir, candidate)):
                    continue
                return candidate

        for old_name in sorted(image_data.keys()):
            ext = os.path.splitext(old_name)[1] or '.jpg'
            suggested_base = _sanitize_filename_component(name_suggestions.get(old_name, ''))

            if suggested_base:
                idx = 0
                while True:
                    suffix = '' if idx == 0 else f'（{idx}）'
                    candidate = f"{suggested_base}{suffix}{ext}"
                    if candidate in used_names or os.path.exists(os.path.join(img_subdir, candidate)):
                        idx += 1
                        continue
                    new_name = candidate
                    break
            else:
                new_name = _next_seq_name(ext)

            used_names.add(new_name)
            rename_map[old_name] = new_name
            img_path = os.path.join(img_subdir, new_name)
            with open(img_path, 'wb') as f:
                f.write(image_data[old_name])
            size = os.path.getsize(img_path)
            if size > 100:
                print(f"  图片已保存: {img_base_name}/{new_name}")
            else:
                print(f"  警告: 图片文件 {img_base_name}/{new_name} 几乎为空 ({size} bytes)")

                # ── 步骤 4：更新 MD 中的图片引用 ─────────────────────────────────────
    # 原始引用格式: images/{sha}.jpg
    # 新引用格式:   相对 target_md_output_dir 的 images 子路径
    md_content = raw_md_content
    if rename_map and md_content:
        rel_images_root = os.path.relpath(img_output_dir, start=target_md_output_dir).replace('\\', '/')
        for old_name, new_name in rename_map.items():
            old_ref = f"images/{old_name}"
            new_ref = f"{rel_images_root}/{img_base_name}/{new_name}"
            md_content = md_content.replace(old_ref, new_ref)

            # ── 步骤 5：拼接封面信息到 MD 头部 ───────────────────────────────────
    md_content = _prepend_cover_info_to_md(md_content, cover_metadata)

    # ── 步骤 6：保存 MD ───────────────────────────────────────────────────
    if raw_md_content:
        md_base_name = _build_md_base_name(source_stem, cover_metadata)
        output_md = _allocate_unique_md_path(target_md_output_dir, md_base_name)
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(md_content)
        result['result_md_path'] = os.path.abspath(output_md)
        print(f"  ✅ Markdown 已保存至: {output_md}")

    result['success'] = True
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 核心处理函数：同步请求 ZIP → 保存 ZIP → 解析产物
# ─────────────────────────────────────────────────────────────────────────────

def process_pdf(
        file_path: str,
        api_base_url: str = "http://192.168.104.117:18001",
        md_output_dir: str = "./output/mdOutput",
        img_output_dir: str = "./output/images",
        json_output_dir: str = "./output/jsonOutput",
        zip_output_dir: str = "./output/zipOutput",
):
    """单个 PDF 的处理入口：请求解析、保存 ZIP、解析 ZIP 产物。"""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}")
        return {'file_path': file_path, 'result_md_path': '', 'success': False, 'error': 'file_not_found'}

    source_stem = _strip_pdf_suffix(path.name)

    # 所有请求参数集中由 _build_request_data 生成，避免多处散改。
    data = _build_request_data()
    return_images = _to_bool(data.get('return_images', False), default=False)

    print(f"▶️ 正在处理: {path.name}...")

    try:
        # ── 步骤 1：请求服务端解析，预期返回 ZIP 二进制。───────────────
        with open(file_path, 'rb') as fp:
            files = [('files', (path.name, fp, 'application/pdf'))]
            parse_resp = requests.post(
                f"{api_base_url}/file_parse",
                files=files,
                data=data,
                timeout=10000,
            )

        if parse_resp.status_code != 200:
            print(f"  解析失败: {parse_resp.status_code}, {parse_resp.text}")
            return {'file_path': file_path, 'result_md_path': '', 'success': False,
                    'error': f'http_{parse_resp.status_code}'}

        content_type = parse_resp.headers.get('content-type', '')
        if 'application/zip' not in content_type:
            return {'file_path': file_path, 'result_md_path': '', 'success': False,
                    'error': f'unexpected_content_type: {content_type}'}

        zip_bytes = parse_resp.content

        # ── 步骤 2：先保存 ZIP，再做本地解析，便于复盘与断点续跑。─────────
        os.makedirs(zip_output_dir, exist_ok=True)
        zip_save_path = os.path.join(zip_output_dir, f"{source_stem}.zip")
        with open(zip_save_path, 'wb') as f:
            f.write(zip_bytes)
        print(f"  ✅ ZIP 已保存至: {zip_save_path}")

        # ── 步骤 3：解析 ZIP，提取 MD / middle_json / 图片 ───────────────
        parse_result = _parse_result_zip(
            zip_bytes=zip_bytes,
            source_stem=source_stem,
            md_output_dir=md_output_dir,
            img_output_dir=img_output_dir,
            json_output_dir=json_output_dir,
            return_images=return_images,
        )

        return {
            'file_path': file_path,
            'result_md_path': parse_result.get('result_md_path', ''),
            'success': parse_result.get('success', False),
            'error': parse_result.get('error', ''),
        }

    except Exception as exc:
        print(f"An error occurred: {exc}")
        return {'file_path': file_path, 'result_md_path': '', 'success': False, 'error': str(exc)}

    # ─────────────────────────────────────────────────────────────────────────────


# CLI 参数 & 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(description="MinerU PDF batch parser")
    parser.add_argument('--max-workers', type=int, default=3, help='并发线程数，默认 3')
    parser.add_argument('--max-pdfs', type=int, default=0, help='最多处理的 PDF 数量，默认 0 表示不限制')
    return parser.parse_args()


def _resolve_max_workers(total_tasks: int, requested_workers: int) -> int:
    try:
        max_workers = int(requested_workers)
    except (TypeError, ValueError):
        max_workers = 3
    max_workers = max(1, max_workers)
    return min(max_workers, max(1, total_tasks))


def _resolve_max_pdfs(requested_max_pdfs: int) -> int:
    try:
        max_pdfs = int(requested_max_pdfs)
    except (TypeError, ValueError):
        return 0
    return max(0, max_pdfs)


def _append_result_jsonl(log_file_path: str, result: dict):
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    with open(log_file_path, 'a', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False)
        f.write('\n')
        f.flush()


def _split_oversize_pdf_files(pdf_files: list[str], max_size_bytes: int) -> tuple[list[str], list[str]]:
    eligible: list[str] = []
    oversized: list[str] = []
    for pdf_file in pdf_files:
        try:
            size = os.path.getsize(pdf_file)
        except OSError:
            eligible.append(pdf_file)
            continue
        if size > max_size_bytes:
            oversized.append(pdf_file)
        else:
            eligible.append(pdf_file)
    return eligible, oversized

# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = _parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # data_dir = os.path.join(base_dir, "data")
    # md_output_dir = os.path.join(base_dir, "output", "mdOutput")
    # img_output_dir = os.path.join(base_dir, "output", "images")
    # json_output_dir = os.path.join(base_dir, "output", "jsonOutput")
    # zip_output_dir = os.path.join(base_dir, "output", "zipOutput")
    # batch_result_log_path = os.path.join(json_output_dir, "batch_results.jsonl")

    data_dir = r"D:\standards\first_process_pdf\其他"
    md_output_dir = r"D:\standards\output\mdOutput"
    img_output_dir = r"D:\standards\output\images"
    json_output_dir = r"D:\standards\output\jsonOutput"
    zip_output_dir = r"D:\standards\output\zipOutput"
    batch_result_log_path = os.path.join(json_output_dir, "batch_results.jsonl")

    pdf_files = sorted(glob.glob(os.path.join(data_dir, "*.pdf")))

    if not pdf_files:
        print(f"❌ 在 {data_dir} 目录下没有找到任何 PDF 文件，请检查路径。")
    else:
        print(f"共找到 {len(pdf_files)} 个 PDF 文件，准备开始批量解析...\n" + "-" * 40)

        pdf_files, oversized_files = _split_oversize_pdf_files(pdf_files, MAX_PDF_SIZE_BYTES)
        if oversized_files:
            print(f"⏩ [大小限制] 跳过 {len(oversized_files)} 个大文件 （>{MAX_PDF_SIZE_KB}KB）")
            for oversized_file in oversized_files:
                print(f"   - {os.path.basename(oversized_file)}")
        else:
            print(f"[大小限制] 未发现超过 {MAX_PDF_SIZE_KB}KB 的 PDF。")

        # ── 断点续跑过滤：以 zipOutput 中同名 zip 是否存在为准。───────────
        pending_files = []
        skipped_count = 0
        for pdf_file in pdf_files:
            file_name = os.path.basename(pdf_file)
            file_stem = _strip_pdf_suffix(file_name)
            expected_zip_path = os.path.join(zip_output_dir, f"{file_stem}.zip")
            if os.path.exists(expected_zip_path):
                print(f"⏩ [断点续跑] 跳过已解析文件: {file_name} (检测到 ZIP 已存在)")
                skipped_count += 1
                continue
            pending_files.append(pdf_file)

        max_pdfs = _resolve_max_pdfs(args.max_pdfs)
        if max_pdfs > 0 and len(pending_files) > max_pdfs:
            pending_files = pending_files[:max_pdfs]
            print(f"\n已启用限量处理：仅处理前 {max_pdfs} 个待处理 PDF。")

        if not pending_files:
            print("\n没有需要处理的新文件，任务结束。")
        else:
            max_workers = _resolve_max_workers(len(pending_files), args.max_workers)
            print(f"\n并行处理文件数: {len(pending_files)}，线程数: {max_workers}")
            print(f"结果实时写入: {batch_result_log_path}")

            success_count = 0
            failed_results = []

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_pdf = {
                    executor.submit(
                        process_pdf,
                        file_path=pdf_file,
                        api_base_url="http://192.168.104.117:18000",
                        md_output_dir=md_output_dir,
                        img_output_dir=img_output_dir,
                        json_output_dir=json_output_dir,
                        zip_output_dir=zip_output_dir,
                    ): pdf_file
                    for pdf_file in pending_files
                }

                for future in as_completed(future_to_pdf):
                    pdf_file = future_to_pdf[future]
                    print("-" * 40)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            'file_path': pdf_file,
                            'result_md_path': '',
                            'success': False,
                            'error': f'future_exception: {exc}',
                        }
                        failed_results.append(result)
                        _append_result_jsonl(batch_result_log_path, result)
                        print(f"❌ 任务异常: {os.path.basename(pdf_file)} -> {exc}")
                        continue

                    _append_result_jsonl(batch_result_log_path, result)

                    if result.get('success'):
                        success_count += 1
                    else:
                        failed_results.append(result)
                        print(
                            f"❌ 处理失败: {os.path.basename(result.get('file_path', pdf_file))} "
                            f"原因: {result.get('error', 'unknown_error')}"
                        )

            print("\n" + "=" * 40)
            print("批量任务汇总")
            print(f"  总文件数: {len(pdf_files) + len(oversized_files)}")
            print(f"  大文件跳过: {len(oversized_files)}")
            print(f"  跳过数:   {skipped_count}")
            print(f"  成功数:   {success_count}")
            print(f"  失败数:   {len(failed_results)}")
            if failed_results:
                print("\n失败文件列表:")
                for r in failed_results:
                    print(f"  - {os.path.basename(r.get('file_path', '?'))}  原因: {r.get('error', '?')}")

        print("\n所有 PDF 文件批量解析处理完毕！")