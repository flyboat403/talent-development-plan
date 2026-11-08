#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心逻辑回归测试：运行 python3 -m unittest discover -s tests -v"""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import jianjie
import md_parser
import netutil
import pdf_parser
import search_major

MD_SAMPLE = """专业代码 740202

专业名称 西餐烹饪

基本修业年限 三年

# 职业面向

面向西式烹调师等职业。

# 培养目标定位

培养德智体美劳全面发展的人才。

# 接续专业举例

接续高职专科专业举例：西式烹饪工艺

专业代码 740203

专业名称 中西面点

# 职业面向

面向中式面点师等职业。
"""

PDF_TEXT = """机械加工技术专业教学标准（中等职业教育）
1 概述
本标准适用于机械加工技术专业。
2 专业名称（专业代码）
机械加工技术（660102）
3 入学基本要求
初级中等学校毕业
4 基本修业年限
三年
5 职业面向
所属专业大类（代码） 装备制造大类（66）
所属专业类（代码） 机械设计制造类（6601）
对应行业（代码） 通用设备制造业（34）
主要职业类别（代码） 机械冷加工人员（6-18-01）
常用机械加工设备操作与维护、零件加工工艺实施
主要岗位（群）或技术领域
、产品质量检测
职业类证书 机械工程制图、数控车铣加工
6 培养目标
本专业培养德智体美劳全面发展的人才。
1
7 培养规格
（1）坚定拥护中国共产党领导。
8 课程设置及学时安排
8.1 课程设置
主要包括公共基础课程和专业课程。
8.2 学时安排
3年总学时一般为3200学时。
16～18学时折算1学分。
9 师资队伍
2 机床夹具与应用
9.1 队伍结构
专任教师数量符合规定。
10 教学条件
10.1 教学设施
具备实训条件。
"""


class TestMdParser(unittest.TestCase):
    def test_extracts_major_fields(self):
        result = md_parser.parse_md_content(MD_SAMPLE, "740202")
        self.assertEqual(result['major_name'], "西餐烹饪")
        self.assertIn("西式烹调师", result['career_orientation'])
        self.assertIn("德智体美劳", result['training_goal'])
        self.assertIn("西式烹饪工艺", result['continuation_majors'])
        self.assertEqual(result['education_level'], "中等职业教育")

    def test_unknown_code_returns_none(self):
        self.assertIsNone(md_parser.parse_md_content(MD_SAMPLE, "999999"))

    def test_empty_code_returns_none(self):
        self.assertIsNone(md_parser.parse_md_content(MD_SAMPLE, ""))

    def test_empty_content_returns_none(self):
        self.assertIsNone(md_parser.parse_md_content("", "740202"))

    def test_find_local_md_prefers_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "7402餐饮类.md").write_text(MD_SAMPLE, encoding='utf-8')
            (base / "74021其他类.md").write_text(MD_SAMPLE, encoding='utf-8')
            found = md_parser.find_local_md(base, "7402")
            self.assertIsNotNone(found)
            self.assertTrue(found.name.startswith("7402"))

    def test_find_local_md_missing_dir(self):
        self.assertIsNone(md_parser.find_local_md(Path('/nonexistent-dir'), "7402"))


class TestNetSafety(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        for url in ('file:///etc/passwd', 'ftp://example.com/a.md', 'data:text/plain,x'):
            self.assertFalse(netutil.validate_download_url(url), url)

    def test_rejects_private_and_loopback(self):
        for url in ('http://127.0.0.1/a', 'http://10.0.0.5/a', 'http://192.168.1.1/a',
                    'http://localhost/a', 'http://[::1]/a', 'http://169.254.169.254/a'):
            self.assertFalse(netutil.validate_download_url(url), url)

    def test_accepts_public_https(self):
        self.assertTrue(netutil.validate_download_url('https://github.com/a/b.md'))

    def test_safe_cache_name_strips_path_components(self):
        name = netutil.safe_cache_name('https://x/a/%2F..%2F..%2Fetc%2Fcron.d%2Fevil')
        self.assertNotIn('/', name)
        self.assertNotIn('..', name)
        self.assertTrue(name.endswith('evil'))

    def test_safe_cache_name_is_url_dependent(self):
        a = netutil.safe_cache_name('https://a.com/x/同名.md')
        b = netutil.safe_cache_name('https://b.com/x/同名.md')
        self.assertNotEqual(a, b)

    def test_write_cache_refuses_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'cache'
            cache.mkdir()
            self.assertFalse(netutil.write_cache(cache, '../../escape.bin', b'x'))
            self.assertFalse((Path(tmp).parent / 'escape.bin').exists())
            self.assertFalse(netutil.write_cache(cache, '/etc/never-write.bin', b'x'))
            self.assertFalse(Path('/etc/never-write.bin').exists())


class TestJianjie(unittest.TestCase):
    def setUp(self):
        self.index = jianjie.JianjieIndex([
            {'major_code': '740202', 'major_name': '西餐烹饪',
             'education_level': '中等职业教育', 'md_url': ''},
            {'major_code': '740203', 'major_name': '中西面点',
             'education_level': '中等职业教育', 'md_url': ''},
            {'major_code': '510204', 'major_name': '数字媒体技术',
             'education_level': '高等职业教育专科', 'md_url': ''},
        ])

    def test_exact_code_match(self):
        entry = jianjie.find_jianjie_entry(self.index, "7402", "740203")
        self.assertEqual(entry['major_name'], "中西面点")

    def test_category_fallback(self):
        entry = jianjie.find_jianjie_entry(self.index, "5102", "519999")
        self.assertEqual(entry['major_code'], "510204")

    def test_search_name(self):
        self.assertEqual(len(self.index.search_name("面点")), 1)

    def test_empty_identifiers_return_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "7402餐饮类.md").write_text(MD_SAMPLE, encoding='utf-8')
            self.assertIsNone(jianjie.search_from_md_fallback("", "", base))

    def test_fill_with_missing_entry_does_not_crash(self):
        result = {'major_code': '740202', 'major_name': '',
                  'career_orientation': '', 'training_goal': '',
                  'continuation_majors': ''}
        other_index = jianjie.JianjieIndex([
            {'major_code': '999999', 'major_name': '占位', 'md_url': ''}
        ])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "7402餐饮类.md").write_text(MD_SAMPLE, encoding='utf-8')
            self.assertTrue(
                jianjie.fill_result_from_jianjie(result, other_index, None, base)
            )
            self.assertEqual(result['major_name'], "西餐烹饪")
            self.assertEqual(result['content_source'], 'moe_pdfs_md')


class TestPdfParser(unittest.TestCase):
    def test_extract_sections(self):
        data = pdf_parser.extract_sections(PDF_TEXT)
        self.assertEqual(data['major_name'], "机械加工技术")
        self.assertEqual(data['education_level'], "中等职业教育")
        self.assertEqual(data['basic_years'], "三年")
        self.assertEqual(data['category_code'], "6601")
        self.assertEqual(data['category_name'], "机械设计制造类")
        self.assertEqual(data['total_hours'], 3200)
        self.assertIn("16～18学时折算1学分", data['credit_conversion'])
        self.assertIn("产品质量检测", data['jobs'])
        self.assertIn("机械工程制图", data['certificates'])
        self.assertIn("德智体美劳", data['training_goal'])

    def test_ignores_table_false_headings(self):
        sections = pdf_parser._top_sections(pdf_parser._clean_lines(PDF_TEXT))
        titles = [title for _n, title, _b in sections]
        self.assertNotIn("机床夹具与应用", titles)
        self.assertEqual(titles[0], "概述")
        self.assertEqual(titles[-1], "教学条件")

    def test_empty_pdf_bytes(self):
        self.assertIsNone(pdf_parser.parse_pdf_bytes(b'', "660102"))

    def test_invalid_pdf_bytes(self):
        self.assertIsNone(pdf_parser.parse_pdf_bytes(b'not a pdf', "660102"))


class TestSearchMajor(unittest.TestCase):
    def test_empty_query_returns_error_with_data_source(self):
        result = search_major.search_major("")
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['data_source'], 'error')
        self.assertEqual(result['total'], 0)

    def test_whitespace_query_returns_error(self):
        self.assertEqual(search_major.search_major("   ")['status'], 'error')

    def test_level_keyword_mapping(self):
        self.assertTrue(search_major._level_matches("中等职业教育", "中职"))
        self.assertFalse(search_major._level_matches("高等职业教育专科", "中职"))
        self.assertTrue(search_major._level_matches("任意", None))

    def test_resolve_codes_skips_numeric_query(self):
        index = jianjie.JianjieIndex([])
        self.assertEqual(
            search_major._resolve_codes_from_jianjie("660102", "中职", index),
            (None, None),
        )

    def test_resolve_codes_by_unique_name(self):
        index = jianjie.JianjieIndex([
            {'major_code': '740202', 'major_name': '西餐烹饪',
             'education_level': '中等职业教育', 'md_url': ''},
        ])
        self.assertEqual(
            search_major._resolve_codes_from_jianjie("西餐烹饪", "中职", index),
            ('740202', '7402'),
        )

    def test_resolve_codes_respects_level(self):
        index = jianjie.JianjieIndex([
            {'major_code': '740202', 'major_name': '西餐烹饪',
             'education_level': '中等职业教育', 'md_url': ''},
        ])
        self.assertEqual(
            search_major._resolve_codes_from_jianjie("西餐烹饪", "高职", index),
            (None, None),
        )


if __name__ == '__main__':
    unittest.main()
