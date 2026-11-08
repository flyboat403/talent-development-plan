#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 assets/moe_pdfs_md 离线快照与 SOURCE.txt 记录的指纹是否一致，
并核对简介索引与本地文件的 1:1 对应关系。

用法：
    python3 scripts/check_jianjie_snapshot.py

退出码：0 = 全部一致；1 = 存在漂移（需更新 SOURCE.txt 或重新同步数据）
"""

import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD_DIR = ROOT / "assets" / "moe_pdfs_md"
SOURCE = MD_DIR / "SOURCE.txt"
INDEX = ROOT / "assets" / "moe_jianjie_final_github.json"


def compute_snapshot(md_dir: Path):
    files = sorted(f for f in md_dir.iterdir() if f.is_file() and f.suffix == '.md')
    digest = hashlib.sha256()
    total = 0
    for f in files:
        raw = f.read_bytes()
        total += len(raw)
        digest.update(f.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(raw)
    return len(files), total, digest.hexdigest(), {f.name for f in files}


def parse_source_records(text: str):
    def grab(pattern, default=''):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else default

    return {
        'files': grab(r'文件数\s+: (\d+)'),
        'bytes': grab(r'总字节数\s+: ([\d,]+)'),
        'sha': grab(r'指纹值\s*: (\w+)'),
        'index_count': grab(r'（(\d+)\s*条专业简介索引）'),
    }


def main() -> int:
    problems = []

    if not MD_DIR.exists():
        print(f"FAIL 快照目录不存在: {MD_DIR}")
        return 1
    if not SOURCE.exists():
        print(f"FAIL 缺少元数据文件: {SOURCE}")
        return 1

    n, total, sha, local_names = compute_snapshot(MD_DIR)
    rec = parse_source_records(SOURCE.read_text(encoding='utf-8'))

    print(f"实际快照: files={n} bytes={total:,} sha256={sha}")
    print(f"记录值  : files={rec['files']} bytes={rec['bytes']} sha256={rec['sha']}")

    if rec['files'] and int(rec['files']) != n:
        problems.append(f"文件数不一致: 记录 {rec['files']} ≠ 实际 {n}")
    if rec['bytes'] and rec['bytes'].replace(',', '') != str(total):
        problems.append(f"总字节数不一致: 记录 {rec['bytes']} ≠ 实际 {total:,}")
    if rec['sha'] and rec['sha'] != sha:
        problems.append(f"SHA-256 不一致: 记录 {rec['sha']} ≠ 实际 {sha}")

    if INDEX.exists():
        index = json.loads(INDEX.read_text(encoding='utf-8'))
        index_names = {
            urllib.parse.unquote(e['md_url'].rsplit('/', 1)[-1])
            for e in index if e.get('md_url')
        }
        print(f"索引条目: {len(index)} | 索引唯一MD: {len(index_names)} | 本地MD: {n}")
        if rec['index_count'] and int(rec['index_count']) != len(index):
            problems.append(f"索引条数不一致: 记录 {rec['index_count']} ≠ 实际 {len(index)}")
        if index_names - local_names:
            problems.append(f"索引有而本地缺失 {len(index_names - local_names)} 个文件")
        if local_names - index_names:
            problems.append(f"本地有而索引未引用 {len(local_names - index_names)} 个文件")
    else:
        problems.append(f"索引文件不存在: {INDEX}")

    if problems:
        print("\nFAIL 检测到漂移:")
        for item in problems:
            print(f"  - {item}")
        print("\n请同步更新 assets/moe_pdfs_md/SOURCE.txt 中的记录值。")
        return 1

    print("\nPASS 快照、记录值、索引对应关系全部一致。")
    return 0


if __name__ == '__main__':
    sys.exit(main())
