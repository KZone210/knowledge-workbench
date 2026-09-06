# -*- coding: utf-8 -*-
"""自动分类模块：按文件扩展名归类为「文件类型」。

历史版本按内容主题（8 大类关键词加权打分）分类，已废弃；现改为扩展名归类。
扩展名分组参考开源项目 BoddapuLokesh/Automatic-File-Organiser，类别名本地化为
中文并按知识库场景增补（见 FILE_CATEGORIES）。

对外接口（保持模块符号兼容）：
- FILE_CATEGORIES      : 文件类型 → 扩展名集合（扩展名不含点、小写）
- FILE_CATEGORY_ORDER  : 前端分类栏展示顺序
- FILE_TYPE_NAMES      : 全部文件类型名（含“其他”）
- DEFAULT_CATEGORY     : 未匹配扩展名时的兜底类别
- classify_by_ext(ext) -> 文件类型类别名
- build_tags(keywords, category) -> 标签列表
"""

from __future__ import annotations

# 文件类型 → 扩展名（不含点、小写）。可按需增补。
FILE_CATEGORIES: dict[str, set[str]] = {
    "文档": {
        "pdf", "doc", "docx", "txt", "xls", "xlsx", "ppt", "pptx",
        "odt", "rtf", "csv", "md", "markdown",
    },
    "图片": {
        "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "svg", "webp", "ico",
    },
    "视频": {
        "mp4", "avi", "mov", "wmv", "flv", "mkv", "webm", "m4v", "3gp",
    },
    "音频": {
        "mp3", "wav", "aac", "flac", "ogg", "wma", "m4a", "opus",
    },
    "压缩包": {
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz",
    },
    "代码": {
        "py", "js", "html", "htm", "css", "java", "c", "cpp", "h", "php",
        "rb", "go", "rs", "json", "xml", "yaml", "sql", "sh",
    },
}

# 反查表：扩展名(小写) → 文件类型
_EXT_TO_CATEGORY: dict[str, str] = {}
for _cat, _exts in FILE_CATEGORIES.items():
    for _ext in _exts:
        _EXT_TO_CATEGORY[_ext] = _cat

# 分类栏展示顺序
FILE_CATEGORY_ORDER: list[str] = ["文档", "图片", "视频", "音频", "压缩包", "代码", "其他"]

# 全部文件类型名（含兜底“其他”）
FILE_TYPE_NAMES: list[str] = list(FILE_CATEGORY_ORDER)

# 未匹配扩展名/无扩展名 → 其他
DEFAULT_CATEGORY: str = "其他"


def _normalize(ext: str) -> str:
    """把各种形式的扩展名规整为“不含点、小写、仅最后一段”。

    兼容 '.pdf' / 'PDF' / 'archive.tar.gz' / 'dir/file.JPG' 等输入。
    """
    if not ext:
        return ""
    s = str(ext).strip().lower().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s


def classify_by_ext(ext: str) -> str:
    """按文件扩展名归类为文件类型。

    Args:
        ext: 扩展名或文件名，如 '.pdf'、'pdf'、'报告.PDF'、'a.tar.gz'。

    Returns:
        文件类型中文名；无法识别/无扩展名时返回 DEFAULT_CATEGORY（其他）。
    """
    key = _normalize(ext)
    if not key:
        return DEFAULT_CATEGORY
    return _EXT_TO_CATEGORY.get(key, DEFAULT_CATEGORY)


def build_tags(keywords: list[str] | None, category: str | None = None) -> list[str]:
    """生成文档标签列表。

    新版 category 语义为「文件类型」（文档/图片/...），是文件固有属性而非常规
    内容标签，因此不再把 category 追加进 tags，避免文件类型标签淹没关键词云。
    仅返回去重后的内容关键词。

    Args:
        keywords: 内容关键词（可空）。
        category: 保留兼容参数（新逻辑不再使用）。

    Returns:
        去重后的标签列表。
    """
    tags: list[str] = []
    for kw in (keywords or []):
        t = str(kw).strip()
        if t and t not in tags:
            tags.append(t)
    return tags
