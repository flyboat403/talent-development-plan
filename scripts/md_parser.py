#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专业简介 Markdown 解析：提取单个专业代码对应的简介字段。"""

import re
from pathlib import Path
from typing import Dict, Optional

EDUCATION_LEVEL_BY_PREFIX = {
    '7': '中等职业教育',
    '5': '高等职业教育',
    '2': '职业教育本科',
}


def parse_md_content(content: str, target_code: str, source_file: str = '') -> Optional[Dict]:
    """解析专业简介 MD 文本；未找到目标专业代码或无有效字段时返回 None。"""
    if not content or not target_code:
        return None

    result = {
        'major_code': target_code,
        'major_name': '',
        'education_level': '',
        'career_orientation': '',
        'training_goal': '',
        'continuation_majors': '',
        'source_file': source_file,
        'pdf_url': '',
    }

    section_pattern = rf'专业代码\s+{re.escape(target_code)}\s*\n(.*?)(?=专业代码\s+\d{{6}}|$)'
    section_match = re.search(section_pattern, content, re.DOTALL)
    if not section_match:
        return None

    section = section_match.group(1)

    name_match = re.search(r'专业名称\s+([^\n]+)', section)
    if name_match:
        result['major_name'] = name_match.group(1).strip()

    orientation_match = re.search(r'# 职业面向\s*\n+(.*?)(?=\n# |\Z)', section, re.DOTALL)
    if orientation_match:
        result['career_orientation'] = orientation_match.group(1).strip()[:500]

    goal_match = re.search(r'# 培养目标定位\s*\n+(.*?)(?=\n# |\Z)', section, re.DOTALL)
    if goal_match:
        result['training_goal'] = goal_match.group(1).strip()[:800]

    continuation_match = re.search(
        r'# 接续专业举例\s*\n+(.*?)(?=\n专业代码\s+\d{6}|\Z)', section, re.DOTALL
    )
    if continuation_match:
        result['continuation_majors'] = continuation_match.group(1).strip()[:500]

    result['education_level'] = EDUCATION_LEVEL_BY_PREFIX.get(target_code[:1], '')

    if not (result['major_name'] or result['career_orientation'] or result['training_goal']):
        return None
    return result


def parse_md_file(md_path: Path, target_code: str) -> Optional[Dict]:
    """解析本地专业简介 MD 文件。"""
    if not md_path.exists():
        return None
    try:
        content = md_path.read_text(encoding='utf-8')
    except OSError as e:
        print(f"[ERROR] 读取MD文件失败 {md_path}: {e}")
        return None
    return parse_md_content(content, target_code, source_file=md_path.name)


def find_local_md(md_dir: Optional[Path], category_code: str) -> Optional[Path]:
    """按专业类代码定位本地简介文件（确定性选择：优先类代码前缀完全匹配）。"""
    if md_dir is None or not category_code or not md_dir.exists():
        return None
    pattern = re.compile(rf'^{re.escape(category_code)}')
    matched = sorted(
        (f for f in md_dir.iterdir()
         if f.is_file() and f.suffix == '.md' and pattern.match(f.name)),
        key=lambda p: p.name,
    )
    if not matched:
        return None
    exact = [f for f in matched if f.stem.startswith(category_code)]
    return (exact or matched)[0]
