#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职业教育专业教学标准 PDF 的下载与解析。

对外接口：
    fetch_and_parse_pdf(url, major_code, cache_dir=None, use_cache=True) -> TeachingStandard | None
    parse_pdf_file(path, major_code) -> TeachingStandard | None
    parse_pdf_bytes(raw, major_code) -> TeachingStandard | None
    to_dict(standard) -> dict
"""

import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from netutil import download_bytes

PDF_MAX_BYTES = 50 * 1024 * 1024
FIELD_MAX_CHARS = 2000

_TOP_HEADING = re.compile(r'(?m)^(\d{1,2})\s+(\S[^\n]{1,30})$')
_ANY_HEADING = re.compile(r'(?m)^\d{1,2}(?:\.\d{1,2})*\s+\S')
_SENTENCE_PUNCT = re.compile(r'[，。；、：,.;:]')

CAREER_LABELS = (
    '所属专业大类（代码）', '所属专业类（代码）', '对应行业（代码）',
    '主要职业类别（代码）', '主要岗位（群）或技术领域', '职业类证书',
)

_EDUCATION_LEVEL_BY_PREFIX = {
    '7': '中等职业教育',
    '5': '高等职业教育',
    '2': '职业教育本科',
}


@dataclass
class TeachingStandard:
    major_code: str
    major_name: str = ''
    education_level: str = ''
    basic_years: str = ''
    admission_requirement: str = ''
    major_group: str = ''
    category_name: str = ''
    category_code: str = ''
    industry: str = ''
    career_categories: str = ''
    jobs: str = ''
    certificates: str = ''
    career_orientation: str = ''
    training_goal: str = ''
    training_spec: str = ''
    courses_section: str = ''
    hours_section: str = ''
    total_hours: int = 0
    credit_conversion: str = ''
    raw_text: str = ''
    source_url: str = ''
    source_file: str = ''
    sections: List[Tuple[str, str]] = field(default_factory=list)


def to_dict(standard: TeachingStandard) -> Dict:
    """转为普通字典，供检索结果合并使用。"""
    return asdict(standard)


def _clean_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r'\d{1,2}', stripped):
            continue
        lines.append(stripped)
    return '\n'.join(lines)


def _join_paragraph(body: str) -> str:
    joined = re.sub(r'\s*\n\s*', '', body)
    joined = re.sub(r'[ \t\u3000]+', ' ', joined)
    return joined.strip()[:FIELD_MAX_CHARS]


def _top_sections(text: str) -> List[Tuple[str, str, int, int]]:
    """按 1,2,3… 递增规则识别顶层章节，规避表格中「序号 + 名称」的伪标题。"""
    accepted: List[Tuple[str, str, int, int]] = []
    expected = 1
    for match in _TOP_HEADING.finditer(text):
        number, title = match.group(1), match.group(2).strip()
        if int(number) != expected:
            continue
        if _SENTENCE_PUNCT.search(title):
            continue
        accepted.append((number, title, match.start(), match.end()))
        expected += 1

    sections = []
    for idx, (number, title, _start, end) in enumerate(accepted):
        body_end = accepted[idx + 1][2] if idx + 1 < len(accepted) else len(text)
        sections.append((number, title, text[end:body_end]))
    return sections


def _find_section(sections: List[Tuple[str, str, str]], keyword: str) -> str:
    for _number, title, body in sections:
        if keyword in title:
            return body
    return ''


def _search_group(pattern: str, text: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _body_after_heading(text: str, keyword: str) -> str:
    """定位标题为 keyword 的章节（优先精确匹配，避免命中包含该词的上级标题）。"""
    candidates = [
        (title.strip(), match)
        for match in re.finditer(r'(?m)^\d{1,2}(?:\.\d{1,2})*\s+([^\n]+)$', text)
        for title in [match.group(1)]
        if keyword in title
    ]
    if not candidates:
        return ''
    exact = [match for title, match in candidates if title == keyword]
    target = exact[0] if exact else candidates[0][1]
    rest = text[target.end():]
    nxt = _ANY_HEADING.search(rest)
    return rest[:nxt.start()] if nxt else rest


def _parse_labeled_block(body: str) -> Dict[str, str]:
    """解析「标签 + 值」块；兼容表格重排导致值被拆到标签前后的情况。"""
    lines = [line.strip() for line in body.split('\n') if line.strip()]
    values: Dict[str, str] = {}

    for idx, line in enumerate(lines):
        for label in CAREER_LABELS:
            if label not in line:
                continue
            remainder = line.split(label, 1)[1].strip()
            if not remainder:
                parts = []
                if idx > 0 and not any(l in lines[idx - 1] for l in CAREER_LABELS):
                    parts.append(lines[idx - 1])
                for nxt in lines[idx + 1:]:
                    if any(l in nxt for l in CAREER_LABELS):
                        break
                    parts.append(nxt)
                remainder = ''.join(parts)
            values[label] = remainder.strip()
            break
    return values


def _parse_career_fields(body: str) -> Dict[str, str]:
    values = _parse_labeled_block(body)
    category = values.get('所属专业类（代码）', '')
    category_code = _search_group(r'[（(](\d{4})[）)]', category) or ''
    category_name = re.sub(r'[（(]\d{4}[）)]', '', category).strip()
    return {
        'major_group': values.get('所属专业大类（代码）', ''),
        'category_name': category_name,
        'category_code': category_code,
        'industry': values.get('对应行业（代码）', ''),
        'career_categories': values.get('主要职业类别（代码）', ''),
        'jobs': values.get('主要岗位（群）或技术领域', '')[:FIELD_MAX_CHARS],
        'certificates': values.get('职业类证书', ''),
    }


def extract_sections(text: str) -> Dict:
    """从教学标准正文提取结构化字段（纯文本输入，便于单元测试）。"""
    cleaned = _clean_lines(text)
    sections = _top_sections(cleaned)

    name_body = _find_section(sections, '专业名称')
    years_body = _find_section(sections, '修业年限')
    admission_body = _find_section(sections, '入学')
    career_body = _find_section(sections, '职业面向')
    goal_body = _find_section(sections, '培养目标')
    spec_body = _find_section(sections, '培养规格')
    courses_body = _find_section(sections, '课程设置')
    hours_raw = _body_after_heading(cleaned, '学时安排') or _find_section(sections, '学时安排')

    name_line = _join_paragraph(name_body)
    name_match = re.match(r'([^（(]+)', name_line)
    major_name = name_match.group(1).strip() if name_match else ''
    code_match = re.search(r'[（(](\d{6})[）)]', name_line)

    level_match = re.search(r'（(中等职业教育|高等职业教育|职业教育本科)）', cleaned)
    education_level = level_match.group(1) if level_match else ''

    data = {
        'major_name': major_name,
        'education_level': education_level,
        'basic_years': _join_paragraph(years_body)[:40],
        'admission_requirement': _join_paragraph(admission_body)[:200],
        'career_orientation': _join_paragraph(career_body),
        'training_goal': _join_paragraph(goal_body),
        'training_spec': _join_paragraph(spec_body),
        'courses_section': _join_paragraph(courses_body),
        'hours_section': _join_paragraph(hours_raw),
    }
    data['major_code_hint'] = code_match.group(1) if code_match else ''
    data.update(_parse_career_fields(career_body))

    hours_text = _join_paragraph(hours_raw)
    total_hours = _search_group(r'总学时[^0-9]{0,12}(\d{3,4})\s*学时', hours_text)
    data['total_hours'] = int(total_hours) if total_hours else 0
    data['credit_conversion'] = _search_group(
        r'(\d+\s*[～~\-至]\s*\d+\s*学时折算\s*1\s*学分)', hours_text
    ) or ''
    return data


def _build_standard(
    text: str,
    major_code: str,
    source_url: str = '',
    source_file: str = '',
) -> Optional[TeachingStandard]:
    data = extract_sections(text)
    if not data['major_name'] and not data['training_goal']:
        return None

    standard = TeachingStandard(
        major_code=major_code or data.get('major_code_hint', ''),
        major_name=data['major_name'],
        education_level=(
            data['education_level']
            or _EDUCATION_LEVEL_BY_PREFIX.get((major_code or '')[:1], '')
        ),
        basic_years=data['basic_years'],
        admission_requirement=data['admission_requirement'],
        major_group=data['major_group'],
        category_name=data['category_name'],
        category_code=data['category_code'],
        industry=data['industry'],
        career_categories=data['career_categories'],
        jobs=data['jobs'],
        certificates=data['certificates'],
        career_orientation=data['career_orientation'],
        training_goal=data['training_goal'],
        training_spec=data['training_spec'],
        courses_section=data['courses_section'],
        hours_section=data['hours_section'],
        total_hours=data['total_hours'],
        credit_conversion=data['credit_conversion'],
        raw_text=text[:20000],
        source_url=source_url,
        source_file=source_file,
    )
    return standard


def parse_pdf_bytes(
    raw: bytes,
    major_code: str,
    source_url: str = '',
    source_file: str = '',
) -> Optional[TeachingStandard]:
    """解析 PDF 字节流。"""
    if not raw:
        return None
    try:
        import pdfplumber
    except ImportError:
        print("[WARNING] 未安装 pdfplumber，无法解析 PDF: pip install pdfplumber")
        return None

    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            text = '\n'.join((page.extract_text() or '') for page in pdf.pages)
    except Exception as e:
        print(f"[ERROR] PDF 解析失败: {e}")
        return None

    return _build_standard(text, major_code, source_url=source_url, source_file=source_file)


def parse_pdf_file(path: Path, major_code: str) -> Optional[TeachingStandard]:
    """解析本地 PDF 文件。"""
    try:
        raw = path.read_bytes()
    except OSError as e:
        print(f"[ERROR] 读取 PDF 失败 {path}: {e}")
        return None
    return parse_pdf_bytes(raw, major_code, source_file=path.name)


def fetch_and_parse_pdf(
    url: str,
    major_code: str,
    cache_dir: Optional[Path] = None,
    use_cache: bool = True,
    timeout: int = 60,
    refresh: bool = False,
) -> Optional[TeachingStandard]:
    """下载并解析教学标准 PDF；失败返回 None（不抛出异常）。"""
    if not url:
        return None
    raw = download_bytes(
        url,
        cache_dir=cache_dir if use_cache else None,
        timeout=timeout,
        max_bytes=PDF_MAX_BYTES,
        refresh=refresh,
    )
    if raw is None:
        return None
    return parse_pdf_bytes(raw, major_code, source_url=url)
