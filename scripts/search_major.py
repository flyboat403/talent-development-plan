#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专业教学标准检索脚本

数据源优先级：
1. 职业教育专业目录.xlsx → 专业代码 + 专业类代码
2. moe_pdfs_final_github.json → 教学标准 PDF URL（下载后由 pdf_parser 解析正文）
3. moe_jianjie_final_github.json → 专业简介 MD URL（PDF 不可用或字段缺失时补全）
4. moe_pdfs_md/ 本地目录 → 索引缺失或下载失败时的离线兜底

用法：
    python3 scripts/search_major.py -m "机械加工技术" -l 中职 -o output/result.json
"""

import argparse
import json
from pathlib import Path
from typing import Dict, Optional

from catalog import (
    HAS_PANDAS,
    LEVEL_KEYWORDS,
    load_major_data,
    load_xlsx_catalog,
    search_by_code,
    search_by_level,
    search_by_name,
    search_from_xlsx,
)
from jianjie import (
    JIANJIE_DEFAULT_PATH,
    MD_CACHE_DEFAULT,
    MD_DIR_DEFAULT,
    JianjieIndex,
    fill_result_from_jianjie,
    load_jianjie_index,
    search_from_md_fallback,
)
from pdf_parser import fetch_and_parse_pdf, to_dict

XLSX_DEFAULT = "assets/职业教育专业目录.xlsx"
PDF_INDEX_DEFAULT = "assets/moe_pdfs_final_github.json"
PDF_CACHE_DEFAULT = "temp/pdf_cache"
CONTENT_FIELDS = ('career_orientation', 'training_goal', 'continuation_majors')


def format_user_prompt(result: dict) -> str:
    """生成多候选或降级场景的用户提示文本。"""
    if result.get('alternatives'):
        prompt = "找到多个匹配的专业，请选择：\n"
        for i, alt in enumerate(result['alternatives'], 1):
            note = f" [{alt.get('备注')}]" if alt.get('备注') else ""
            prompt += f"{i}. {alt['major_name']} ({alt['major_code']}) - {alt['level_in_xlsx']}{note}\n"
        return prompt + "请输入序号或专业代码："

    if result.get('source') in ('moe_pdfs_md', 'moe_jianjie_github'):
        return (
            "\n⚠️ 未获取到专业教学标准正文，仅找到专业简介。\n"
            "专业简介信息量少于教学标准，是否继续使用？\n"
            '- 输入 "Y" 或 "是" 继续使用专业简介\n'
            '- 输入 "N" 或 "否" 终止，请上传教学标准文件\n'
        )

    if result.get('need_upload'):
        return (
            f"\n❌ 在所有数据源中均未找到该专业的教学标准。\n"
            f"专业名称：{result.get('major_name')}\n"
            f"教育层次：{result.get('level')}\n"
            "请上传该专业的教学标准文件（PDF/DOCX格式）。\n"
        )

    return ""


def parse_pdf_to_result(
    pdf_url: str,
    major_code: str,
    cache_dir: Optional[Path] = None,
    timeout: int = 60,
    refresh: bool = False,
) -> Optional[Dict]:
    """下载并解析教学标准 PDF，返回可合并到检索结果的字典。"""
    standard = fetch_and_parse_pdf(
        url=pdf_url,
        major_code=major_code,
        cache_dir=cache_dir,
        use_cache=True,
        timeout=timeout,
        refresh=refresh,
    )
    return to_dict(standard) if standard else None


def _level_matches(education_level: str, level: Optional[str]) -> bool:
    if not level:
        return True
    keyword = LEVEL_KEYWORDS.get(level, level)
    return keyword in (education_level or '')


def _resolve_codes_from_jianjie(
    query: str,
    level: Optional[str],
    jianjie_index: JianjieIndex,
) -> tuple:
    """XLSX 不可用时，用简介索引按名称反查专业代码（同层次唯一命中才采用）。"""
    if not query or query.isdigit():
        return None, None
    hits = [
        e for e in jianjie_index.search_name(query)
        if _level_matches(e.get('education_level', ''), level)
    ]
    if len(hits) != 1:
        return None, None
    code = str(hits[0].get('major_code', ''))
    return (code or None), (code[:4] if code else None)


def _parse_pdf_results(
    results,
    pdf_cache_dir: Optional[Path],
    refresh: bool,
) -> bool:
    parsed_any = False
    for result in results:
        pdf_url = result.get('pdf_url', '')
        major_code = result.get('major_code', '')
        if not pdf_url:
            result['pdf_parsed'] = False
            result['pdf_parse_error'] = "无PDF URL"
            continue
        print(f"[INFO] Step 2: 找到PDF URL，尝试解析: {major_code}")
        pdf_result = parse_pdf_to_result(
            pdf_url, major_code, pdf_cache_dir, refresh=refresh
        )
        if pdf_result:
            result.update(pdf_result)
            result['pdf_parsed'] = True
            parsed_any = True
            print(f"[OK] PDF解析成功: {major_code}")
        else:
            result['pdf_parsed'] = False
            result['pdf_parse_error'] = "PDF解析失败"
    return parsed_any


def search_major(
    query: str,
    level: Optional[str] = None,
    xlsx_path: str = XLSX_DEFAULT,
    json_path: str = PDF_INDEX_DEFAULT,
    jianjie_json_path: str = JIANJIE_DEFAULT_PATH,
    md_dir: str = MD_DIR_DEFAULT,
    output_path: Optional[str] = None,
    use_fallback: bool = True,
    parse_pdf: bool = True,
    cache_dir: Optional[str] = PDF_CACHE_DEFAULT,
    md_cache_dir: Optional[str] = MD_CACHE_DEFAULT,
    interactive: bool = False,
    refresh_cache: bool = False,
) -> Dict:
    """检索专业教学标准：XLSX → PDF 索引 → 简介索引 → 本地目录兜底。"""
    if not query or not query.strip():
        return {
            "query": query, "level": level, "total": 0, "results": [],
            "data_source": "error", "need_upload": True, "source": "error",
            "major_code": None, "category_code": None, "major_name": query,
            "xlsx_level": None, "pdf_parsed": False,
            "error": "查询参数不能为空", "status": "error",
        }

    project_root = Path(__file__).resolve().parent.parent
    pdf_cache_full = (project_root / cache_dir) if cache_dir else None
    md_cache_full = (project_root / md_cache_dir) if md_cache_dir else None
    md_full = project_root / md_dir

    major_code = category_code = major_name = xlsx_level = None
    xlsx_exact = False

    # ---------- Step 1: 专业目录 ----------
    xlsx_full = project_root / xlsx_path
    if xlsx_full.exists() and HAS_PANDAS:
        print(f"[INFO] Step 1: 从专业目录检索 '{query}' (层次: {level})...")
        try:
            xlsx_result = search_from_xlsx(query, level or "", load_xlsx_catalog(xlsx_full))
        except Exception as e:
            print(f"[WARNING] XLSX检索失败: {e}")
            xlsx_result = None

        if xlsx_result and xlsx_result.get('alternatives'):
            alternatives = xlsx_result['alternatives']
            return {
                "query": query, "level": level, "status": "multiple_choices",
                "total": len(alternatives), "alternatives": alternatives,
                "data_source": "multiple_choices", "need_upload": False,
                "results": [], "source": "multiple_choices",
                "major_code": None, "category_code": None,
                "major_name": query, "xlsx_level": None, "pdf_parsed": False,
                "prompt": format_user_prompt(xlsx_result),
            }
        if xlsx_result and xlsx_result.get('success'):
            major_code = xlsx_result['major_code']
            category_code = xlsx_result['category_code']
            major_name = xlsx_result['major_name']
            xlsx_level = xlsx_result['level_in_xlsx']
            xlsx_exact = True
            print(f"[OK] XLSX检索: {major_name} ({major_code}), 类代码={category_code}")
        elif xlsx_result:
            print(f"[WARNING] XLSX未找到匹配: {xlsx_result.get('error')}")
    else:
        print("[WARNING] XLSX文件不存在或pandas未安装，跳过XLSX检索")

    # ---------- Step 2: PDF 索引 ----------
    results = []
    pdf_parsed = False
    data_source = "primary"
    json_full = project_root / json_path

    if json_full.exists():
        try:
            data = load_major_data(json_full)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            print(f"[WARNING] 主数据源加载失败: {e}")
            data = []

        if data:
            if major_code:
                found = search_by_code(data, major_code)
                if found:
                    results = [found]
            if not results and not xlsx_exact and major_name:
                results = search_by_name(data, major_name)
            if not results and not xlsx_exact:
                if query.isdigit() and len(query) == 6:
                    found = search_by_code(data, query)
                    if found:
                        results = [found]
                else:
                    results = search_by_name(data, query)

            if results and level:
                level_kw = LEVEL_KEYWORDS.get(level, level)
                filtered = search_by_level(results, level_kw)
                if not filtered:
                    print(f"[WARNING] 无 {level} 层次的教学标准，拒绝跨层次回退")
                results = filtered

            if results and parse_pdf:
                pdf_parsed = _parse_pdf_results(
                    results, pdf_cache_full, refresh_cache
                )

            pending = [
                r for r in results
                if not all(r.get(k) for k in CONTENT_FIELDS)
            ]
            if pending:
                jianjie_index = load_jianjie_index(project_root / jianjie_json_path)
                if jianjie_index:
                    for r in pending:
                        if fill_result_from_jianjie(
                            r, jianjie_index, md_cache_full, md_full, refresh=refresh_cache
                        ):
                            print(f"[OK] 已用专业简介补全内容: {r.get('major_code')}")

            if results:
                data_source = "primary"

    # ---------- Step 3: 专业简介备选 ----------
    if not results and use_fallback:
        print("[INFO] Step 3: 主数据源未找到，尝试专业简介备选检索...")
        jianjie_index = load_jianjie_index(project_root / jianjie_json_path)

        if not major_code:
            major_code, category_code = _resolve_codes_from_jianjie(
                query, level, jianjie_index
            )
            if major_code:
                print(f"[OK] 简介索引名称反查: {query} → {major_code}")

        if jianjie_index:
            print(f"[INFO] 已加载专业简介索引 {len(jianjie_index)} 条: {jianjie_json_path}")
        else:
            print("[WARNING] 专业简介索引不可用，降级为本地目录扫描")

        if jianjie_index or md_full.exists():
            md_result = search_from_md_fallback(
                category_code or "",
                major_code or "",
                md_full,
                jianjie_index=jianjie_index,
                cache_dir=md_cache_full,
                refresh=refresh_cache,
            )
            if md_result and _level_matches(md_result.get('education_level', ''), level):
                results = [md_result]
                data_source = "fallback"
                print(
                    f"[OK] 专业简介检索成功: {md_result.get('major_name')} "
                    f"({md_result.get('major_code')}) via {md_result.get('source')}"
                )
            else:
                print("[WARNING] 专业简介索引与本地目录均未找到匹配")

    # ---------- Step 4: 构建结果 ----------
    need_upload = len(results) == 0
    source_label = "not_found" if need_upload else data_source
    first_source = results[0].get('source') if results else None
    content_source = None
    for item in results:
        if item.get('content_source'):
            content_source = item['content_source']
            break

    output = {
        "query": query,
        "level": level,
        "total": len(results),
        "results": results,
        "data_source": source_label,
        "major_code": major_code,
        "category_code": category_code,
        "major_name": major_name or (results[0].get('major_name') if results else query),
        "xlsx_level": xlsx_level,
        "need_upload": need_upload,
        "pdf_parsed": pdf_parsed,
        "source": first_source or source_label,
        "content_source": content_source or first_source,
    }

    if interactive:
        prompt = format_user_prompt(output)
        if prompt:
            print(prompt)

    if output_path:
        _save_output(project_root, output_path, output)

    return output


def _save_output(project_root: Path, output_path: str, output: Dict) -> None:
    try:
        target = (project_root / output_path).resolve()
        if project_root.resolve() not in target.parents:
            print(f"[WARNING] 输出路径位于项目目录之外: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        print(f"[OK] 结果已保存到: {target}")
    except OSError as e:
        print(f"[ERROR] 保存文件失败: {e}")


def main():
    parser = argparse.ArgumentParser(description='检索专业教学标准')
    parser.add_argument('--major', '-m', required=True, help='专业名称或代码')
    parser.add_argument('--level', '-l', help='教育层次（中职/高职/职教本科）')
    parser.add_argument('--xlsx', default=XLSX_DEFAULT, help='XLSX专业目录路径')
    parser.add_argument('--json', '-j', default=PDF_INDEX_DEFAULT, help='教学标准PDF索引路径')
    parser.add_argument('--jianjie-json', default=JIANJIE_DEFAULT_PATH, help='专业简介索引路径')
    parser.add_argument('--md-dir', default=MD_DIR_DEFAULT, help='本地简介MD兜底目录')
    parser.add_argument('--output', '-o', help='输出文件路径')
    parser.add_argument('--no-pdf', action='store_true', help='不解析PDF正文（仍会用专业简介补全内容）')
    parser.add_argument('--cache-dir', default=PDF_CACHE_DEFAULT, help='PDF缓存目录')
    parser.add_argument('--md-cache-dir', default=MD_CACHE_DEFAULT, help='专业简介MD缓存目录')
    parser.add_argument('--refresh-cache', action='store_true', help='忽略本地缓存重新下载')
    parser.add_argument('--interactive', '-i', action='store_true', help='交互模式')

    args = parser.parse_args()

    result = search_major(
        query=args.major,
        level=args.level,
        xlsx_path=args.xlsx,
        json_path=args.json,
        jianjie_json_path=args.jianjie_json,
        md_dir=args.md_dir,
        output_path=args.output,
        parse_pdf=not args.no_pdf,
        cache_dir=args.cache_dir,
        md_cache_dir=args.md_cache_dir,
        interactive=args.interactive,
        refresh_cache=args.refresh_cache,
    )

    print(f"\n{'=' * 60}")
    if result.get('status') == 'multiple_choices':
        print(f"找到 {result.get('total', 0)} 个匹配专业，请选择：")
        print(f"{'=' * 60}")
        print("\n" + result.get('prompt', ''))
        return

    print(f"共找到 {result.get('total', 0)} 条记录 (数据源: {result.get('data_source')})")
    print(f"{'=' * 60}")

    if result.get('status') == 'error':
        print(f"\n❌ {result.get('error')}")
        return

    if result.get('total', 0) == 0:
        print(f"\n❌ 未找到专业 '{result.get('query')}' 的教学标准")
        print(result.get('prompt', ''))
        return

    for i, item in enumerate(result.get('results', []), 1):
        print(f"\n[{i}] {item.get('major_name', 'N/A')}")
        print(f"    专业代码: {item.get('major_code', 'N/A')}")
        print(f"    教育层次: {item.get('education_level', 'N/A')}")
        print(f"    PDF链接: {item.get('pdf_url', 'N/A')}")
        print(f"    数据来源: {item.get('source', result.get('data_source'))}")
        if item.get('content_source'):
            print(f"    内容来源: {item['content_source']}")

        if item.get('pdf_parsed'):
            print("    PDF解析: ✅ 已解析")
        elif item.get('pdf_parse_error'):
            print(f"    PDF解析: ❌ {item['pdf_parse_error']}")

        if item.get('career_orientation'):
            print(f"    职业面向: {item['career_orientation'][:100]}...")
        if item.get('training_goal'):
            print(f"    培养目标: {item['training_goal'][:100]}...")
        if item.get('total_hours'):
            print(f"    总学时: {item['total_hours']}")

    if result.get('major_code'):
        print(f"\n    专业代码: {result['major_code']}")
    if result.get('category_code'):
        print(f"    专业类代码: {result['category_code']}")
    if result.get('need_upload'):
        print("\n    ⚠️ 需要上传教学标准文件")


if __name__ == '__main__':
    main()
