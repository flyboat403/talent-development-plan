#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专业目录（XLSX）与教学标准 PDF 索引（JSON）的加载与检索。"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

LEVEL_ALIASES = {
    "中职": "中职",
    "中等职业教育": "中职",
    "高职": "高职专科",
    "高等职业教育": "高职专科",
    "高职专科": "高职专科",
    "职教本科": "职教本科",
    "职业教育本科": "职教本科",
}

LEVEL_KEYWORDS = {"中职": "中等", "高职": "高等", "职教本科": "本科"}


def load_xlsx_catalog(xlsx_path: Path) -> Any:
    """加载职业教育专业目录 XLSX。"""
    if not HAS_PANDAS or pd is None:
        raise ImportError("需要 pandas 库: pip install pandas openpyxl")
    if not xlsx_path.exists():
        raise FileNotFoundError(f"专业目录文件不存在: {xlsx_path}")
    return pd.read_excel(xlsx_path)


def _row_to_entry(row) -> Dict:
    return {
        "major_code": str(row['专业代码']),
        "major_name": row['专业名称'],
        "category_code": str(row['专业类代码'])[:4],
        "level_in_xlsx": row['层次'],
        "备注": str(row['备注']) if pd.notna(row['备注']) else "",
    }


def search_from_xlsx(query: str, level: str, df: Any) -> Dict:
    """从专业目录检索专业代码与专业类代码；多候选时返回 alternatives。"""
    xlsx_level = LEVEL_ALIASES.get(level, level)
    df_level = df[df['层次'].str.contains(xlsx_level, na=False)]

    for frame in (df_level[df_level['专业名称'] == query],
                  df_level[df_level['专业名称'].str.contains(query, na=False)]):
        if len(frame) == 1:
            entry = _row_to_entry(frame.iloc[0])
            entry.update({"success": True, "alternatives": []})
            return entry
        if len(frame) > 1:
            return {"success": True, "alternatives": [_row_to_entry(r) for _, r in frame.iterrows()]}

    return {
        "success": False,
        "error": f"未在专业目录中找到匹配: query='{query}', level='{level}'",
    }


def load_major_data(json_path: Path) -> List[Dict]:
    """加载教学标准 PDF 索引 JSON。"""
    if not json_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {json_path}")
    return json.loads(json_path.read_text(encoding='utf-8'))


def search_by_code(data: List[Dict], code: str) -> Optional[Dict]:
    for item in data:
        if item.get('major_code') == code:
            return item
    return None


def search_by_name(data: List[Dict], name: str) -> List[Dict]:
    return [item for item in data if name in item.get('major_name', '')]


def search_by_level(data: List[Dict], level: str) -> List[Dict]:
    return [item for item in data if level in item.get('education_level', '')]
