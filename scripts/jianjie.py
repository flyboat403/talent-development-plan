#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专业简介索引：加载、按代码/名称定位、在线下载与本地目录兜底。"""

import json
import urllib.parse
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from md_parser import find_local_md, parse_md_content, parse_md_file
from netutil import download_text

JIANJIE_DEFAULT_PATH = "assets/moe_jianjie_final_github.json"
MD_DIR_DEFAULT = "assets/moe_pdfs_md"
MD_CACHE_DEFAULT = "temp/md_cache"


class JianjieIndex:
    """专业简介索引，按专业代码与专业类代码建立 O(1) 定位表。"""

    def __init__(self, entries: Iterable[Dict]):
        self.entries: List[Dict] = [e for e in entries if isinstance(e, dict)]
        self.by_code: Dict[str, Dict] = {}
        self.by_category: Dict[str, List[Dict]] = {}
        for entry in self.entries:
            code = str(entry.get('major_code', ''))
            if code:
                self.by_code.setdefault(code, entry)
            if len(code) >= 4:
                self.by_category.setdefault(code[:4], []).append(entry)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def find(self, category_code: str, major_code: str) -> Optional[Dict]:
        if major_code and major_code in self.by_code:
            return self.by_code[major_code]
        bucket = self.by_category.get((category_code or major_code[:4])[:4])
        return bucket[0] if bucket else None

    def search_name(self, query: str) -> List[Dict]:
        if not query:
            return []
        return [e for e in self.entries if query in str(e.get('major_name', ''))]


def load_jianjie_index(json_path: Path) -> JianjieIndex:
    """加载专业简介索引；文件缺失或损坏时返回空索引。"""
    if not json_path.exists():
        return JianjieIndex([])
    try:
        data = json.loads(json_path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARNING] 专业简介索引加载失败: {e}")
        return JianjieIndex([])
    return JianjieIndex(data if isinstance(data, list) else [])


def find_jianjie_entry(
    index: Union[JianjieIndex, List[Dict]],
    category_code: str,
    major_code: str,
) -> Optional[Dict]:
    """在简介索引中定位专业条目（精确代码优先，其次同专业类）。"""
    if isinstance(index, JianjieIndex):
        return index.find(category_code, major_code)
    return JianjieIndex(index).find(category_code, major_code)


def download_md_content(
    md_url: str,
    cache_dir: Optional[Path] = None,
    timeout: int = 30,
    refresh: bool = False,
) -> Optional[str]:
    """下载专业简介 MD 内容（带缓存、TTL、体积上限与重试）。"""
    return download_text(md_url, cache_dir=cache_dir, timeout=timeout, refresh=refresh)


def _with_source(result: Dict, source: str, md_file: str, md_url: str = '') -> Dict:
    result['source'] = source
    result['md_file'] = md_file
    if md_url:
        result['md_url'] = md_url
    return result


def search_from_md_fallback(
    category_code: str,
    major_code: str,
    md_dir: Path,
    jianjie_index: Optional[JianjieIndex] = None,
    cache_dir: Optional[Path] = None,
    refresh: bool = False,
) -> Optional[Dict]:
    """专业简介备选检索：索引下载优先，本地目录兜底；无有效内容返回 None。"""
    category_code = category_code or (major_code[:4] if major_code else "")
    if not category_code:
        return None

    if jianjie_index:
        entry = find_jianjie_entry(jianjie_index, category_code, major_code)
        if entry:
            md_url = entry.get('md_url', '')
            content = download_md_content(md_url, cache_dir, refresh=refresh)
            if content:
                target = major_code or entry.get('major_code', '')
                parsed = parse_md_content(
                    content, target, source_file=entry.get('major_name', '')
                )
                if parsed:
                    if not parsed.get('major_name'):
                        parsed['major_name'] = entry.get('major_name', '')
                    if not parsed.get('education_level'):
                        parsed['education_level'] = entry.get('education_level', '')
                    md_file = urllib.parse.unquote(md_url.rsplit('/', 1)[-1])
                    return _with_source(parsed, 'moe_jianjie_github', md_file, md_url)
                print("[WARNING] 简介内容中未找到该专业代码，转本地目录兜底")
        else:
            print("[WARNING] 简介索引中未找到该专业，尝试本地目录兜底")

    md_path = find_local_md(md_dir, category_code)
    if md_path is None:
        return None

    parsed = parse_md_file(md_path, major_code)
    if not parsed:
        return None
    return _with_source(parsed, 'moe_pdfs_md', md_path.name)


def fill_result_from_jianjie(
    result: Dict,
    jianjie_index: Union[JianjieIndex, List[Dict]],
    cache_dir: Optional[Path] = None,
    md_dir: Optional[Path] = None,
    refresh: bool = False,
) -> bool:
    """用专业简介补全结果中缺失的字段（职业面向/培养目标/接续专业），返回是否补全。"""
    fields = ('career_orientation', 'training_goal', 'continuation_majors')
    if not jianjie_index:
        return False
    if all(result.get(k) for k in fields):
        return False

    major_code = str(result.get('major_code', ''))
    category_code = major_code[:4]

    parsed: Optional[Dict] = None
    md_url = ''
    entry = find_jianjie_entry(jianjie_index, category_code, major_code)
    if entry:
        md_url = entry.get('md_url', '')
        content = download_md_content(md_url, cache_dir, refresh=refresh)
        if content:
            parsed = parse_md_content(
                content, major_code, source_file=entry.get('major_name', '')
            )

    if not parsed:
        local_md = find_local_md(md_dir, category_code)
        if local_md:
            parsed = parse_md_file(local_md, major_code)
            md_url = ''

    if not parsed:
        return False

    filled = False
    for key in fields:
        if not result.get(key) and parsed.get(key):
            result[key] = parsed[key]
            filled = True

    if not result.get('major_name'):
        result['major_name'] = parsed.get('major_name') or (entry or {}).get('major_name', '')

    if filled:
        result['content_source'] = 'moe_jianjie_github' if md_url else 'moe_pdfs_md'
        if md_url:
            result['md_url'] = md_url
    return filled
