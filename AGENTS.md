# AGENTS.md — talent-development-plan

> 这是一个 **OpenCode 技能包**，用于生成职业院校人才培养方案（Markdown → DOCX）。非传统软件项目，无 web 服务、无 API、无构建系统。

## 项目结构

```
SKILL.md                          ← 主技能定义（764行，详细工作流）
assets/
  人才培养方案-中职模板.md         ← 中职格式模板（含表4公共基础课）
  人才培养方案-高职模板.md         ← 高职格式模板
references/
  national-standards.md           ← 国家标准：公共基础课清单、学时底线、术语表（必加载）
  template-chapters.md            ← 标准章节结构模板（备选）
  images/课程体系架构图.png        ← 示例架构图
scripts/
  preprocess.py                   ← 文档预处理：PDF/DOCX→纯文本，保留表格
  create_curriculum_image.py      ← 课程体系架构图生成（PIL/Pillow）
  postprocess_docx.py             ← DOCX后处理：添加表格边框、表头加粗居中
```

## 核心工作流（6 Phase）

用户需提供 **3 份材料**：专业名称及代码 + 专业教学标准 + 职业分析报告。

```
Phase 0: 材料预处理 → scripts/preprocess.py（markitdown → 降级到 python-docx/pdfplumber）
Phase 1: 解析样例结构 → 选择内置模板（中职/高职/中高职一体化）或用户自定义
Phase 2: 提取教学标准数据 → 专业信息/培养目标/课程体系/学时等
Phase 3: 提取职业分析报告数据 → 岗位/工作任务/学习领域/学习情境等
Phase 4: 融合数据逐章生成 → 职业面向/培养目标/培养规格/课程体系等
  ⚠️ 课程体系完成后必须触发**交互确认**，用户确认课程后才能继续
Phase 5: Markdown → DOCX 交付
  5.1 生成完整MD（含课程体系架构图）
  5.2 pandoc 转换 → postprocess_docx.py 后处理
  5.3 自检清单（约25项）
```

## 关键命令

```bash
# 预处理：任意格式转纯文本
python scripts/preprocess.py <input_file> -o <output.txt>

# 生成课程架构图（传入JSON或文件路径）
python scripts/create_curriculum_image.py --data '<json_string>' -o <output_dir>/images/课程体系架构图.png

# pandoc 转换（必须从MD所在目录执行，否则相对路径图片会失效）
cd <output_dir> && pandoc 方案.md -o 方案_no_border.docx --reference-doc=<skill_dir>/references/人才培养方案模板.docx

# DOCX后处理（必须执行，否则表格无边框）
python scripts/postprocess_docx.py --input 方案_no_border.docx --output 方案.docx --font-size 10 --margins 2.54,2.54,3.17,3.17
```

## Python 依赖

```
markitdown==0.1.1
python-docx==1.1.2
pdfplumber==0.11.4
Pillow==11.1.0
系统依赖: pandoc
```

## 架构图路径约定（必须遵守）

- PNG 保存到 **输出目录** 的 `images/` 子目录下
- MD 中使用 **相对路径** 引用：`![课程体系架构图](images/课程体系架构图.png)`
- pandoc 必须在 MD 所在目录执行，否则相对路径解析失败
- 转换后必须用 `python-docx` 检查 `doc.inline_shapes` 长度 ≥ 1，确认图片嵌入成功

## ⚠️ 已知问题

- `references/人才培养方案模板.docx` 在 SKILL.md 中被引用为 `--reference-doc`，但当前仓库中**不存在此文件**。pandoc 转换时会自动使用默认样式。如需正式公文格式，需自行创建该 DOCX 文件。

## 强制规则速查

| 规则 | 内容 |
|------|------|
| **规则A** | 课程体系架构图必须嵌入MD，PNG保存到输出目录 `images/` 子目录 |
| **规则B** | 表4 公共基础课**必须从模板提取**，禁止改写国标内容，仅允许专业名称替换和学时调整 |
| **规则C** | 其余章节基于教学标准+职业分析报告融合生成 |
| **规则D** | 专业课程的课程目标和主要内容**必须从双源材料提取整合**，禁止 LLM 纯推理 |
| **表3规范** | 职业能力列禁止填写代码编号（Z001/F001等），必须是完整能力描述文字 |
| **学时底线** | 三年制中职≥3000学时，高职≥2500学时，一体化≥4500学时；公共基础课≥33%/25%；选修课≥10%；实践≥50% |
| **交互确认** | 课程体系双源构建完成后必须暂停，呈现清单让用户确认（最多3轮） |

## 术语

全程使用规范职教术语："立德树人""工学结合""岗课赛证融通""产教融合""1+X证书制度""学习领域/学习情境"等。详见 `references/national-standards.md` 第五节术语表。
