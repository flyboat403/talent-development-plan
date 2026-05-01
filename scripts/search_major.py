#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pyright: reportMissingImports=false, reportPossiblyUnboundVariable=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false, reportReturnType=false, reportOptionalMemberAccess=false, reportInvalidTypeForm=false
"""
专业教学标准检索脚本

数据源优先级（更新）：
1. 职业教育专业目录.xlsx → 获取专业代码 + 专业类代码
2. moe_pdfs_final.json（JSON索引） → 通过专业代码查 PDF URL
3. moe_pdfs_md/{类代码}*.md → 通过专业类代码查专业简介（备选）

流程：
1. 从 XLSX 获取专业代码和专业类代码
2. 从 moe_pdfs_final.json 查找专业 → 得到 PDF URL
3. 如果有 PDF URL，下载并解析 PDF
4. 如果 moe_pdfs_final.json 中没有，则从 moe_pdfs_md 目录解析
"""

import json
import re
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None  # 显式赋值，避免 "possibly unbound" 警告
    HAS_PANDAS = False

# ============================
# T1.2: XLSX 读取函数
# ============================

def load_xlsx_catalog(xlsx_path: Path) -> Any:
    """加载职业教育专业目录"""
    if not HAS_PANDAS or pd is None:
        raise ImportError("需要 pandas 库: pip install pandas openpyxl")
    if not xlsx_path.exists():
        raise FileNotFoundError(f"专业目录文件不存在: {xlsx_path}")
    return pd.read_excel(xlsx_path)


def search_from_xlsx(query: str, level: str, df: Any) -> dict:
    """
    从XLSX检索专业代码和类代码

    Args:
        query: 专业名称
        level: 教育层次（"中职"/"高职"/"职教本科"）
        df: XLSX数据DataFrame

    Returns:
        {
            "success": bool,
            "major_code": str,      # 专业代码（6位）
            "major_name": str,       # 专业名称
            "category_code": str,    # 专业类代码（4位，用于查找MD文件）
            "level_in_xlsx": str,    # XLSX中的层次字段
            "alternatives": []       # 多个候选时返回列表
        }
    """
    # 层次映射：用户输入 → XLSX中的值
    level_map = {
        "中职": "中职",
        "中等职业教育": "中职",
        "高职": "高职专科",
        "高等职业教育": "高职专科",
        "高职专科": "高职专科",
        "职教本科": "职教本科",
        "职业教育本科": "职教本科",
    }
    xlsx_level = level_map.get(level, level)

    # 层次过滤
    level_mask = df['层次'].str.contains(xlsx_level, na=False)
    df_level = df[level_mask]

    # 精确匹配
    exact = df_level[df_level['专业名称'] == query]
    if len(exact) == 1:
        row = exact.iloc[0]
        return {
            "success": True,
            "major_code": str(row['专业代码']),
            "major_name": row['专业名称'],
            "category_code": str(row['专业类代码'])[:4],
            "level_in_xlsx": row['层次'],
            "alternatives": []
        }
    if len(exact) > 1:
        alts = []
        for _, row in exact.iterrows():
            alts.append({
                "major_code": str(row['专业代码']),
                "major_name": row['专业名称'],
                "category_code": str(row['专业类代码'])[:4],
                "level_in_xlsx": row['层次'],
                "备注": str(row['备注']) if pd.notna(row['备注']) else ""
            })
        return {
            "success": True,
            "alternatives": alts
        }

    # 模糊匹配
    fuzzy = df_level[df_level['专业名称'].str.contains(query, na=False)]
    if len(fuzzy) == 1:
        row = fuzzy.iloc[0]
        return {
            "success": True,
            "major_code": str(row['专业代码']),
            "major_name": row['专业名称'],
            "category_code": str(row['专业类代码'])[:4],
            "level_in_xlsx": row['层次'],
            "alternatives": []
        }
    if len(fuzzy) > 1:
        alts = []
        for _, row in fuzzy.iterrows():
            alts.append({
                "major_code": str(row['专业代码']),
                "major_name": row['专业名称'],
                "category_code": str(row['专业类代码'])[:4],
                "level_in_xlsx": row['层次'],
                "备注": str(row['备注']) if pd.notna(row['备注']) else ""
            })
        return {
            "success": True,
            "alternatives": alts
        }

    # 未找到
    return {
        "success": False,
        "error": f"未在专业目录中找到匹配: query='{query}', level='{level}'"
    }


# ============================
# T1.1/T1.4: JSON 检索（保留现有）
# ============================

def load_major_data(json_path: Path) -> List[Dict]:
    """加载专业教学标准JSON数据"""
    if not json_path.exists():
        raise FileNotFoundError(f"数据文件不存在: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def search_by_code(data: List[Dict], code: str) -> Optional[Dict]:
    """按专业代码检索"""
    for item in data:
        if item.get('major_code') == code:
            return item
    return None


def search_by_name(data: List[Dict], name: str) -> List[Dict]:
    """按专业名称检索（支持模糊匹配）"""
    results = []
    for item in data:
        if name in item.get('major_name', ''):
            results.append(item)
    return results


def search_by_level(data: List[Dict], level: str) -> List[Dict]:
    """按教育层次筛选"""
    results = []
    for item in data:
        if level in item.get('education_level', ''):
            results.append(item)
    return results


# ============================
# T1.5: MD 备选检索
# ============================

def search_from_md_fallback(category_code: str, major_code: str, md_dir: Path) -> Optional[dict]:
    """
    通过专业类代码查找 moe_pdfs_md 目录中的文件

    文件命名格式: {类代码}{类名}.md 如 "6601机械设计制造类.md"
    或 "6601 机械设计制造类.md"

    Args:
        category_code: 专业类代码（4位）
        major_code: 专业代码（6位，用于定位具体专业）
        md_dir: MD文件目录

    Returns:
        {
            "success": bool,
            "source": str,
            "major_code": str,
            "major_name": str,
            "category_name": str,
            "career_orientation": str,
            "training_goal": str,
            "md_file": str
        } 或 None
    """
    if not md_dir.exists():
        return None

    # 匹配文件名前4位 = category_code
    pattern = re.compile(rf'^{re.escape(category_code)}')
    matched_files = []
    for f in md_dir.iterdir():
        if f.is_file() and f.suffix == '.md' and pattern.match(f.stem):
            matched_files.append(f)

    if not matched_files:
        return None

    # 如果有多个匹配（不太可能），取第一个
    md_path = matched_files[0]

    # 解析MD文件
    result = parse_md_file(md_path, major_code)
    if result:
        result['source'] = 'moe_pdfs_md'
        result['md_file'] = str(md_path.name)
        return result

    return None


# ============================
# T1.6: 用户交互提示函数
# ============================

def format_user_prompt(result: dict) -> str:
    """生成用户交互提示文本"""
    if result.get('alternatives'):
        # 多候选提示
        prompt = "找到多个匹配的专业，请选择：\n"
        for i, alt in enumerate(result['alternatives'], 1):
            note = f" [{alt.get('备注')}]" if alt.get('备注') else ""
            prompt += f"{i}. {alt['major_name']} ({alt['major_code']}) - {alt['level_in_xlsx']}{note}\n"
        prompt += "请输入序号或专业代码："
        return prompt

    if result.get('source') == 'moe_pdfs_md':
        # 专业简介确认提示
        return """
⚠️ 未找到专业教学标准PDF，但找到专业简介文件。
专业简介包含的信息较教学标准少，是否继续使用？
- 输入 "Y" 或 "是" 继续使用专业简介
- 输入 "N" 或 "否" 终止，请上传教学标准文件
"""

    if result.get('need_upload'):
        # 上传提示
        return f"""
❌ 在所有数据源中均未找到该专业的教学标准。
专业名称：{result.get('major_name')}
教育层次：{result.get('level')}
请上传该专业的教学标准文件（PDF/DOCX格式）。
"""

    return ""  # 成功找到无需提示


# ============================
# PDF 解析（保留现有）
# ============================

def parse_pdf_to_result(
    pdf_url: str,
    major_code: str,
    project_root: Path,
    cache_dir: Optional[str] = "temp/pdf_cache"
) -> Optional[Dict]:
    """解析PDF获取专业教学标准完整内容"""
    try:
        from pdf_parser import fetch_and_parse_pdf, to_dict

        cache_path = project_root / cache_dir if cache_dir else None
        standard = fetch_and_parse_pdf(
            url=pdf_url,
            major_code=major_code,
            cache_dir=cache_path,
            use_cache=True
        )

        if standard:
            return to_dict(standard)
        return None

    except ImportError:
        print("[WARNING] PDF解析模块未安装，跳过PDF解析")
        print("[HINT] 请安装依赖: pip install pdfplumber 或 pip install pypdf")
        return None
    except Exception as e:
        print(f"[ERROR] PDF解析异常: {e}")
        return None


# ============================
# MD 解析（保留现有）
# ============================

def parse_md_file(md_path: Path, target_code: str) -> Optional[Dict]:
    """
    解析专业教学标准MD文件

    MD文件格式：
    ```
    专业代码 710201

    专业名称 计算机应用

    基本修业年限 三年

    # 职业面向
    ...

    # 培养目标定位
    ...
    ```
    """
    if not md_path.exists():
        return None

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()

        result = {
            'major_code': target_code,
            'major_name': '',
            'education_level': '',
            'career_orientation': '',
            'training_goal': '',
            'source_file': str(md_path.name),
            'pdf_url': ''
        }

        major_section_pattern = rf'专业代码\s+{target_code}\s*\n(.*?)(?=专业代码\s+\d{{6}}|$)'
        major_match = re.search(major_section_pattern, content, re.DOTALL)

        if not major_match:
            return result

        major_section = major_match.group(1)

        name_match = re.search(r'专业名称\s+([^\n]+)', major_section)
        if name_match:
            result['major_name'] = name_match.group(1).strip()

        orientation_match = re.search(r'# 职业面向\s*\n+(.*?)(?=\n# |\Z)', major_section, re.DOTALL)
        if orientation_match:
            result['career_orientation'] = orientation_match.group(1).strip()[:500]

        goal_match = re.search(r'# 培养目标定位\s*\n+(.*?)(?=\n# |\Z)', major_section, re.DOTALL)
        if goal_match:
            result['training_goal'] = goal_match.group(1).strip()[:800]

        if target_code.startswith('7'):
            result['education_level'] = '中等职业教育'
        elif target_code.startswith('5'):
            result['education_level'] = '高等职业教育'
        elif target_code.startswith('2'):
            result['education_level'] = '职业教育本科'

        return result

    except Exception as e:
        print(f"[ERROR] 解析MD文件失败 {md_path}: {e}")
        return None


# ============================
# T1.7: 重构 search_major() 主函数
# ============================

def search_major(
    query: str,
    level: Optional[str] = None,
    xlsx_path: str = "assets/职业教育专业目录.xlsx",
    json_path: str = "assets/moe_pdfs_final.json",
    md_dir: str = "assets/moe_pdfs_md",
    output_path: Optional[str] = None,
    use_fallback: bool = True,
    parse_pdf: bool = True,
    cache_dir: Optional[str] = "temp/pdf_cache",
    interactive: bool = False
) -> Dict:
    """
    检索专业教学标准

    完整流程：XLSX → JSON → MD → 用户提示

    Args:
        query: 专业名称或代码
        level: 教育层次（中职/高职/职教本科）
        xlsx_path: XLSX专业目录路径
        json_path: JSON索引路径
        md_dir: MD文件目录
        output_path: 输出文件路径（可选）
        use_fallback: 是否使用备用数据源
        parse_pdf: 是否解析PDF
        cache_dir: PDF缓存目录
        interactive: 是否启用交互模式

    Returns:
        检索结果字典
    """
    if not query or not query.strip():
        return {"query": query, "level": level, "total": 0, "results": [],
                "error": "查询参数不能为空", "status": "error"}

    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # ========== Step 1: XLSX 检索 → 获取专业代码 + 类代码 ==========
    major_code = None
    category_code = None
    major_name = None
    xlsx_level = None
    xlsx_exact = False  # XLSX是否精确匹配

    xlsx_full = project_root / xlsx_path
    if xlsx_full.exists() and HAS_PANDAS:
        print(f"[INFO] Step 1: 从专业目录检索 '{query}' (层次: {level})...")
        try:
            df = load_xlsx_catalog(xlsx_full)
            xlsx_result = search_from_xlsx(query, level or "", df)

            if xlsx_result.get('alternatives'):
                # 多候选
                if interactive:
                    print(format_user_prompt(xlsx_result))
                    # TODO: 在实际交互中读取用户输入
                return {
                    "query": query,
                    "level": level,
                    "status": "multiple_choices",
                    "total": len(xlsx_result['alternatives']),
                    "alternatives": xlsx_result['alternatives'],
                    "prompt": format_user_prompt(xlsx_result)
                }

            if xlsx_result.get('success'):
                major_code = xlsx_result['major_code']
                xlsx_exact = True
                category_code = xlsx_result['category_code']
                major_name = xlsx_result['major_name']
                xlsx_level = xlsx_result['level_in_xlsx']
                print(f"[OK] XLSX检索: {major_name} ({major_code}), 类代码={category_code}")
            else:
                print(f"[WARNING] XLSX未找到匹配: {xlsx_result.get('error')}")
        except Exception as e:
            print(f"[WARNING] XLSX检索失败: {e}")
    else:
        print("[WARNING] XLSX文件不存在或pandas未安装，跳过XLSX检索")

    # 如果XLSX没找到专业代码，尝试直接从JSON中搜索（兼容旧流程）
    if not major_code:
        print("[INFO] 尝试直接从JSON检索...")

    # ========== Step 2: JSON 检索 → 获取 PDF URL ==========
    json_full = project_root / json_path
    if not json_full.exists():
        json_full = project_root / "assets/moe_pdfs_new.json"

    results = []
    pdf_parsed = False
    data_source = "primary"

    if json_full.exists():
        try:
            data = load_major_data(json_full)

            # 优先用专业代码检索
            if major_code:
                result = search_by_code(data, major_code)
                if result:
                    results = [result]

            # 没有XLSX精确匹配时，用名称检索（避免XLSX精确匹配但JSON无此代码时匹配到错误专业）
            if not results and not xlsx_exact and major_name:
                results = search_by_name(data, major_name)

            # 没有XLSX结果时，直接用原始query检索
            if not results and not xlsx_exact:
                if query.isdigit() and len(query) == 6:
                    result = search_by_code(data, query)
                    if result:
                        results = [result]
                else:
                    results = search_by_name(data, query)

            # 层次过滤
            if level and results:
                level_map = {"中职": "中等", "高职": "高等", "职教本科": "本科"}
                level_kw = level_map.get(level, level)
                filtered = search_by_level(results, level_kw)
                if filtered:
                    results = filtered

            # PDF解析
            if results and parse_pdf:
                for i, result in enumerate(results):
                    pdf_url = result.get('pdf_url', '')
                    mc = result.get('major_code', '')
                    if pdf_url:
                        print(f"[INFO] Step 2: 找到PDF URL，尝试解析: {mc}")
                        pdf_result = parse_pdf_to_result(pdf_url, mc, project_root, cache_dir)
                        if pdf_result:
                            result.update(pdf_result)
                            result['pdf_parsed'] = True
                            pdf_parsed = True
                            print(f"[OK] PDF解析成功: {mc}")
                        else:
                            result['pdf_parsed'] = False
                            result['pdf_parse_error'] = "PDF解析失败"
                    else:
                        result['pdf_parsed'] = False
                        result['pdf_parse_error'] = "无PDF URL"

                data_source = "primary"

        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[WARNING] 主数据源加载失败: {e}")

    # ========== Step 3: MD 备选检索 ==========
    if not results and use_fallback:
        print(f"[INFO] Step 3: 主数据源未找到，尝试MD备选检索...")
        md_full = project_root / md_dir

        if md_full.exists() and category_code:
            md_result = search_from_md_fallback(category_code, major_code or "", md_full)
            if md_result:
                results = [md_result]
                data_source = "fallback"
                print(f"[OK] MD备选检索成功: {md_result.get('major_name')} ({md_result.get('major_code')})")
            else:
                print(f"[WARNING] MD备选也未找到匹配")
        elif not category_code:
            print("[WARNING] 缺少专业类代码，无法进行MD备选检索")

    # ========== Step 4: 构建结果 ==========
    need_upload = len(results) == 0
    source_label = data_source

    if need_upload:
        source_label = "not_found"

    output = {
        "query": query,
        "level": level,
        "total": len(results),
        "results": results,
        "data_source": source_label,
        "major_code": major_code,
        "category_code": category_code,
        "major_name": major_name or query,
        "xlsx_level": xlsx_level,
        "need_upload": need_upload,
        "pdf_parsed": pdf_parsed,
        "source": results[0].get('source') if results and results[0].get('source') else source_label
    }

    # 用户提示
    if interactive:
        prompt = format_user_prompt(output)
        if prompt:
            print(prompt)

    # 保存到文件
    if output_path:
        try:
            output_full_path = project_root / output_path
            output_full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_full_path, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            print(f"[OK] 结果已保存到: {output_full_path}")
        except IOError as e:
            print(f"[ERROR] 保存文件失败: {e}")

    return output


# ============================
# 命令行入口
# ============================

def main():
    parser = argparse.ArgumentParser(description='检索专业教学标准')
    parser.add_argument('--major', '-m', required=True, help='专业名称或代码')
    parser.add_argument('--level', '-l', help='教育层次（中职/高职/职教本科）')
    parser.add_argument('--xlsx', default='assets/职业教育专业目录.xlsx', help='XLSX专业目录路径')
    parser.add_argument('--json', '-j', default='assets/moe_pdfs_final.json', help='JSON索引路径')
    parser.add_argument('--md-dir', default='assets/moe_pdfs_md', help='MD备选目录')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--no-pdf', action='store_true', help='不解析PDF，仅返回基本信息')
    parser.add_argument('--cache-dir', default='temp/pdf_cache', help='PDF缓存目录')
    parser.add_argument('--interactive', '-i', action='store_true', help='交互模式')

    args = parser.parse_args()

    result = search_major(
        query=args.major,
        level=args.level,
        xlsx_path=args.xlsx,
        json_path=args.json,
        md_dir=args.md_dir,
        output_path=args.output,
        parse_pdf=not args.no_pdf,
        cache_dir=args.cache_dir,
        interactive=args.interactive
    )

    print(f"\n{'='*60}")
    if result.get('status') == 'multiple_choices':
        print(f"找到 {result.get('total', 0)} 个匹配专业，请选择：")
        print(f"{'='*60}")
        print("\n" + result.get('prompt', ''))
        return

    print(f"共找到 {result['total']} 条记录 (数据源: {result['data_source']})")
    print(f"{'='*60}")

    if result['total'] == 0:
        print(f"\n❌ 未找到专业 '{result['query']}' 的教学标准")
        print(result.get('prompt', ''))
        return

    for i, item in enumerate(result['results'], 1):
        print(f"\n[{i}] {item.get('major_name', 'N/A')}")
        print(f"    专业代码: {item.get('major_code', 'N/A')}")
        print(f"    教育层次: {item.get('education_level', 'N/A')}")
        print(f"    PDF链接: {item.get('pdf_url', 'N/A')}")
        print(f"    数据来源: {item.get('source', result['data_source'])}")

        if item.get('pdf_parsed'):
            print(f"    PDF解析: ✅ 已解析")
            if item.get('career_orientation'):
                print(f"    职业面向: {item['career_orientation'][:100]}...")
        elif item.get('pdf_parse_error'):
            print(f"    PDF解析: ❌ {item['pdf_parse_error']}")

        if item.get('career_orientation'):
            print(f"    职业面向: {item['career_orientation'][:100]}...")
        if item.get('training_goal'):
            print(f"    培养目标: {item['training_goal'][:100]}...")

    # 额外信息
    if result.get('major_code'):
        print(f"\n    专业代码: {result['major_code']}")
    if result.get('category_code'):
        print(f"    专业类代码: {result['category_code']}")
    if result.get('need_upload'):
        print(f"\n    ⚠️ 需要上传教学标准文件")


if __name__ == '__main__':
    main()
