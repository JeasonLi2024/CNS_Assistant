"""Prompt constants and target fields for metadata extraction."""

TARGET_FIELDS = [
    "ics",
    "ccs",
    "标准层级",
    "标准号",
    "代替标准号",
    "发布日期",
    "实施日期",
    "标准中文名称",
    "标准英文名称",
    "采标信息",
    "提出单位",
    "归口单位",
    "起草单位",
    "起草人",
    "引用文件",
    "专业术语",
    "标准性质",
    "制修订",
    "源文件",
]

PROMPT = "从标准文档 Markdown 中抽取国标元数据字段。缺失字段必须留空，禁止编造。"

