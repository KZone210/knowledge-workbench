# 变更日志

> 本文件由 `tools/changelog.py` 自动维护，**仅保留最近 5 次代码变更**，超出自动删除。
> 每次修改以 unified diff 形式记录（只存改动行，最小占用）。

### 2026-09-05 09:27:26 | 自动分类改为按文件扩展名归类 + 支持上传视频/音频/压缩包（参考开源 Automatic-File-Organiser 扩展名映射）

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -149,23 +149,30 @@
             return {"ok": False, "filename": filename, "error": f"解析失败: {e}"}

 

+        # 标题回退：无文本媒体/未提取到标题时，parser 会回退到临时文件 basename

+        # （形如 ".hex…" 的内部 uuid），不得入库为 title。改用用户原始文件名去扩展名。

+        if not title or title.startswith("."):

+            title = os.path.splitext(filename)[0] or filename

+

         # 3) 加密落盘（此时磁盘写入的是密文副本）

         stored_name, abs_path = store.save_plain_to_enc(dek, str(tmp), ext)

         file_size = os.path.getsize(abs_path)

 

+        # 文件类型分类：统一按扩展名归类（不再做内容主题关键词分类）

+        category = classify.classify_by_ext(ext)

+

         if len(text.strip()) < 20:

             meta = {

                 "filename": filename, "stored_name": stored_name, "file_size": file_size,

-                "ext": ext, "title": title, "category": "未分类",

-                "tags": [], "keywords": [], "summary": "（未提取到有效文本，可能是扫描件/图片型 PDF）",

+                "ext": ext, "title": title, "category": category,

+                "tags": [], "keywords": [], "summary": "（未提取到有效文本：可能是扫描件、图片或无文本媒体）",

                 "word_count": 0, "content": text, "path": rel_path,

             }

             doc_id = store.insert_document(meta, user_id, dek)

-            return {"ok": True, "id": doc_id, "filename": filename, "title": title, "category": "未分类",

+            return {"ok": True, "id": doc_id, "filename": filename, "title": title, "category": category,

                     "keywords": [], "summary": meta["summary"], "low_text": True}

 

         keywords = nlp.extract_keywords(text, top_n=8)

-        category, score, matched = classify.classify(text, keywords)

+        tags = classify.build_tags(keywords, category)

         summary = nlp.summarize(text, top_n=3)

-        tags = classify.build_tags(keywords, category)

         meta = {

             "filename": filename, "stored_name": stored_name, "file_size": file_size,
```

**文件:** `backend/classify.py`
```diff
--- backend/classify.py
+++ backend/classify.py
@@ -1,111 +1,107 @@
 # -*- coding: utf-8 -*-

-"""自动分类模块：预设类别关键词库 + 加权打分，输出类别与标签。"""

+"""自动分类模块：按文件扩展名归类为「文件类型」。

 

-# 类别关键词库：词 → 权重（出现 1 次以上的词权重更高）

-CATEGORIES = {

-    "技术开发": {

-        "python": 3, "代码": 3, "编程": 3, "开发": 2, "前端": 3, "后端": 3, "数据库": 2,

-        "接口": 2, "api": 3, "部署": 2, "软件": 2, "程序": 2, "框架": 2, "算法": 2,

-        "调试": 2, "bug": 3, "git": 3, "函数": 2, "变量": 2, "服务器": 2, "linux": 3,

-        "docker": 3, "js": 3, "html": 3, "css": 3, "javascript": 3, "react": 3,

-        "vue": 3, "架构": 2, "安全": 2, "漏洞": 2, "注入": 2, "测试": 2, "编译": 2,

+历史版本按内容主题（8 大类关键词加权打分）分类，已废弃；现改为扩展名归类。

+扩展名分组参考开源项目 BoddapuLokesh/Automatic-File-Organiser，类别名本地化为

+中文并按知识库场景增补（见 FILE_CATEGORIES）。

+

+对外接口（保持模块符号兼容）：

+- FILE_CATEGORIES      : 文件类型 → 扩展名集合（扩展名不含点、小写）

+- FILE_CATEGORY_ORDER  : 前端分类栏展示顺序

+- FILE_TYPE_NAMES      : 全部文件类型名（含“其他”）

+- DEFAULT_CATEGORY     : 未匹配扩展名时的兜底类别

+- classify_by_ext(ext) -> 文件类型类别名

+- build_tags(keywords, category) -> 标签列表

+"""

+

+from __future__ import annotations

+

+# 文件类型 → 扩展名（不含点、小写）。可按需增补。

+FILE_CATEGORIES: dict[str, set[str]] = {

+    "文档": {

+        "pdf", "doc", "docx", "txt", "xls", "xlsx", "ppt", "pptx",

+        "odt", "rtf", "csv", "md", "markdown",

     },

-    "人工智能": {

-        "人工智能": 3, "大模型": 3, "机器学习": 3, "深度学习": 3, "神经网络": 3,

-        "gpt": 3, "openai": 3, "模型": 2, "训练": 2, "推理": 2, "提示词": 3,

-        "prompt": 3, "agent": 3, "智能体": 3, "aigc": 3, "生成": 2, "扩散模型": 3,

-        "llm": 3, "transformer": 3, "多模态": 3, "语义": 2, "embedding": 3,

-        "chatgpt": 3, "ai": 3, "数字人": 2, "语音识别": 3, "图像识别": 3, "nlp": 3,

+    "图片": {

+        "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "svg", "webp", "ico",

     },

-    "金融投资": {

-        "投资": 3, "股票": 3, "基金": 3, "市场": 2, "财报": 3, "估值": 3, "收益率": 3,

-        "资产": 2, "债券": 3, "行情": 2, "a股": 3, "美股": 3, "港股": 3, "仓位": 3,

-        "风险": 2, "涨": 2, "跌": 2, "板块": 2, "指数": 2, "量化": 3, "回测": 3,

-        "因子": 3, "交易": 2, "市值": 2, "市盈率": 3, "净利润": 3, "营收": 3,

-        "现金流": 3, "货币": 2, "利率": 2, "通胀": 3, "宏观经济": 3, "gdp": 3,

-        "银行": 2, "信贷": 3, "理财": 3, "保险": 2, "证券": 2, "期货": 3, "期权": 3,

-        "分红": 2, "认购": 2, "IPO": 3, "定投": 3, "比特币": 3, "区块链": 3,

+    "视频": {

+        "mp4", "avi", "mov", "wmv", "flv", "mkv", "webm", "m4v", "3gp",

     },

-    "营销运营": {

-        "运营": 3, "营销": 3, "用户": 2, "流量": 3, "转化": 3, "品牌": 2, "内容": 2,

-        "投放": 3, "增长": 2, "涨粉": 3, "粉丝": 2, "直播间": 3, "短视频": 3,

-        "抖音": 3, "小红书": 3, "公众号": 3, "爆款": 3, "选题": 3, "矩阵": 3,

-        "变现": 3, "电商": 3, "销售": 2, "成交": 3, "数据": 2, "复盘": 3, "私域": 3,

-        "社群": 3, "广告": 2, "点击率": 3, "曝光": 3, "留存": 3, "拉新": 3,

+    "音频": {

+        "mp3", "wav", "aac", "flac", "ogg", "wma", "m4a", "opus",

     },

-    "教育学习": {

-        "学习": 3, "教程": 3, "课程": 3, "知识": 2, "培训": 3, "笔记": 2, "方法": 2,

-        "读书": 3, "阅读": 3, "写作": 3, "考试": 3, "复习": 3, "知识点": 3,

-        "技能": 2, "练习": 2, "教学": 3, "老师": 2, "学生": 2, "教材": 3, "背诵": 3,

-        "思维导图": 3, "理解": 2, "归纳": 2, "总结": 2, "效率": 2, "番茄": 3,

+    "压缩包": {

+        "zip", "rar", "7z", "tar", "gz", "bz2", "xz",

     },

-    "健康养生": {

-        "健康": 3, "养生": 3, "饮食": 3, "运动": 3, "睡眠": 3, "医疗": 3, "身体": 2,

-        "营养": 3, "锻炼": 3, "跑步": 3, "瑜伽": 3, "健身": 3, "疾病": 3, "医生": 2,

-        "症状": 3, "治疗": 3, "药物": 3, "体检": 3, "心理": 2, "情绪": 2, "压力": 2,

-        "焦虑": 3, "免疫力": 3, "维生素": 3, "体重": 3, "血糖": 3, "血压": 3,

-    },

-    "职场管理": {

-        "管理": 3, "团队": 3, "职场": 3, "领导力": 3, "效率": 2, "项目": 2, "组织": 2,

-        "沟通": 2, "汇报": 3, "目标": 2, "执行": 2, "复盘": 3, "激励": 3, "招聘": 3,

-        "面试": 3, "简历": 3, "晋升": 3, "绩效": 3, "kpi": 3, "okr": 3, "会议": 2,

-        "协作": 2, "流程": 2, "制度": 2, "员工": 2, "老板": 2, "同事": 2, "裁员": 3,

-    },

-    "生活随笔": {

-        "生活": 3, "随笔": 3, "日记": 3, "心情": 3, "旅行": 3, "美食": 3, "周末": 2,

-        "朋友": 2, "家庭": 2, "孩子": 2, "父母": 2, "记录": 2, "感悟": 3, "回忆": 3,

-        "日常": 3, "探店": 3, "风景": 2, "电影": 2, "音乐": 2, "读书会": 3,

+    "代码": {

+        "py", "js", "html", "htm", "css", "java", "c", "cpp", "h", "php",

+        "rb", "go", "rs", "json", "xml", "yaml", "sql", "sh",

     },

 }

 

-DEFAULT_CATEGORY = "未分类"

+# 反查表：扩展名(小写) → 文件类型

+_EXT_TO_CATEGORY: dict[str, str] = {}

+for _cat, _exts in FILE_CATEGORIES.items():

+    for _ext in _exts:

+        _EXT_TO_CATEGORY[_ext] = _cat

+

+# 分类栏展示顺序

+FILE_CATEGORY_ORDER: list[str] = ["文档", "图片", "视频", "音频", "压缩包", "代码", "其他"]

+

+# 全部文件类型名（含兜底“其他”）

+FILE_TYPE_NAMES: list[str] = list(FILE_CATEGORY_ORDER)

+

+# 未匹配扩展名/无扩展名 → 其他

+DEFAULT_CATEGORY: str = "其他"

 

 

-def classify(text, keywords=None):

+def _normalize(ext: str) -> str:

+    """把各种形式的扩展名规整为“不含点、小写、仅最后一段”。

+

+    兼容 '.pdf' / 'PDF' / 'archive.tar.gz' / 'dir/file.JPG' 等输入。

     """

-    基于关键词加权打分分类。

-    返回 (category, score, matched_words)。

-    """

-    if not text:

-        return DEFAULT_CATEGORY, 0.0, []

-    low_text = text.lower()

-    scores = {}

-    matched = {}

-    for cat, words in CATEGORIES.items():

-        score = 0.0

-        hits = []

-        for word, weight in words.items():

-            w = word.lower()

-            cnt = low_text.count(w)

-            if cnt > 0:

-                s = weight * (1.0 + 0.5 * min(cnt, 4))

-                score += s

-                hits.append(word)

-        if score > 0:

-            scores[cat] = score

-            matched[cat] = hits

-

-    if not scores:

-        return DEFAULT_CATEGORY, 0.0, []

-

-    best = max(scores, key=scores.get)

-    best_score = scores[best]

-    second_score = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0

-

-    # 阈值：至少 2 个命中词且总分 >= 5

-    if len(matched[best]) < 2 or best_score < 5:

-        return DEFAULT_CATEGORY, best_score, matched[best]

-

-    # 平局：与第二名差距 < 15% 且绝对差距 < 3（避免多主题文档误伤）

-    tie = second_score > 0 and (best_score - second_score) < max(1.0, best_score * 0.15)

-    if tie:

-        return DEFAULT_CATEGORY, best_score, matched[best]

-    return best, best_score, matched[best]

+    if not ext:

+        return ""

+    s = str(ext).strip().lower().replace("\\", "/")

+    s = s.rsplit("/", 1)[-1]

+    if "." in s:

+        s = s.rsplit(".", 1)[-1]

+    return s

 

 

-def build_tags(keywords, category):

-    """标签 = 关键词 + 类别。"""

-    tags = list(keywords)

-    if category != DEFAULT_CATEGORY:

-        tags.append(category)

+def classify_by_ext(ext: str) -> str:

+    """按文件扩展名归类为文件类型。

+

+    Args:

+        ext: 扩展名或文件名，如 '.pdf'、'pdf'、'报告.PDF'、'a.tar.gz'。

+

+    Returns:

+        文件类型中文名；无法识别/无扩展名时返回 DEFAULT_CATEGORY（其他）。

+    """

+    key = _normalize(ext)

+    if not key:

+        return DEFAULT_CATEGORY

+    return _EXT_TO_CATEGORY.get(key, DEFAULT_CATEGORY)

+

+

+def build_tags(keywords: list[str] | None, category: str | None = None) -> list[str]:

+    """生成文档标签列表。

+

+    新版 category 语义为「文件类型」（文档/图片/...），是文件固有属性而非常规

+    内容标签，因此不再把 category 追加进 tags，避免文件类型标签淹没关键词云。

+    仅返回去重后的内容关键词。

+

+    Args:

+        keywords: 内容关键词（可空）。

+        category: 保留兼容参数（新逻辑不再使用）。

+

+    Returns:

+        去重后的标签列表。

+    """

+    tags: list[str] = []

+    for kw in (keywords or []):

+        t = str(kw).strip()

+        if t and t not in tags:

+            tags.append(t)

     return tags
```

**文件:** `backend/parser.py`
```diff
--- backend/parser.py
+++ backend/parser.py
@@ -6,9 +6,20 @@
 from html.parser import HTMLParser

 

+# 无文本可提取的媒体/压缩包扩展名：入库时不走文本解析，直接返回空文本

+# （视频 + 音频 + 压缩包）。这些二进制文件绝不能被当作文本读取。

+MEDIA_NO_TEXT_EXTS = {

+    # 视频

+    ".mp4", ".avi", ".mov", ".wmv", ".flv", ".mkv", ".webm", ".m4v", ".3gp",

+    # 音频

+    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".wma", ".m4a", ".opus",

+    # 压缩包

+    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz",

+}

+

 SUPPORTED_EXTS = {

     ".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm",

     ".xlsx", ".xls", ".pptx",

     ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif", ".gif",

-}

+} | MEDIA_NO_TEXT_EXTS

 IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff", ".tif", ".gif"}

 

@@ -196,4 +207,7 @@
     ext = os.path.splitext(path)[1].lower()

     base = os.path.splitext(os.path.basename(path))[0]

+    # 无文本媒体（视频/音频/压缩包）：直接返回空文本，严禁把二进制当文本读

+    if ext in MEDIA_NO_TEXT_EXTS:

+        return "", base

     if ext == ".pdf":

         text = _parse_pdf(path)
```

**文件:** `backend/security/auth.py`
```diff
--- backend/security/auth.py
+++ backend/security/auth.py
@@ -332,5 +332,5 @@
                     crypto.enc_field(dek, r["filename"] or ""),

                     crypto.enc_field(dek, r["title"] or ""),

-                    crypto.enc_field(dek, r["category"] or "未分类"),

+                    crypto.enc_field(dek, r["category"] or "其他"),

                     crypto.enc_field(dek, r["tags"] or "[]"),

                     crypto.enc_field(dek, r["keywords"] or "[]"),
```

**文件:** `backend/store.py`
```diff
--- backend/store.py
+++ backend/store.py
@@ -34,5 +34,5 @@
     ext         TEXT DEFAULT '',

     title       TEXT DEFAULT '',

-    category    TEXT DEFAULT '未分类',

+    category    TEXT DEFAULT '其他',

     tags        TEXT DEFAULT '[]',

     keywords    TEXT DEFAULT '[]',

@@ -364,5 +364,5 @@
     counts = {}

     for r in rows:

-        cat = vault.dec_row(dek, dict(r)).get("category") or "未分类"

+        cat = vault.dec_row(dek, dict(r)).get("category") or "其他"

         counts[cat] = counts.get(cat, 0) + 1

     total = sum(counts.values())
```

**文件:** `frontend/app.js`
```diff
--- frontend/app.js
+++ frontend/app.js
@@ -2,15 +2,13 @@
 "use strict";

 

-/* ---------- 分类颜色 ---------- */

+/* ---------- 分类颜色（文件类型） ---------- */

 const CAT_COLORS = {

-  "技术开发": "#0a84ff",

-  "人工智能": "#bf5af2",

-  "金融投资": "#ff9f0a",

-  "营销运营": "#ff375f",

-  "教育学习": "#30d158",

-  "健康养生": "#64d2ff",

-  "职场管理": "#ffd60a",

-  "生活随笔": "#ff9f0a",

-  "未分类": "#8e8e93",

+  "文档": "#0a84ff",

+  "图片": "#30d158",

+  "视频": "#ff375f",

+  "音频": "#ff9f0a",

+  "压缩包": "#ffd60a",

+  "代码": "#bf5af2",

+  "其他": "#8e8e93",

 };

 const EXT_META = {

@@ -22,4 +20,16 @@
   bmp: ["IMG", "#bf5af2"], webp: ["IMG", "#bf5af2"], tiff: ["IMG", "#bf5af2"],

   tif: ["IMG", "#bf5af2"], gif: ["IMG", "#bf5af2"],

+  // 视频

+  mp4: ["MP4", "#ff375f"], mkv: ["MKV", "#ff375f"], mov: ["MOV", "#ff375f"],

+  avi: ["AVI", "#ff375f"], webm: ["WEBM", "#ff375f"], m4v: ["M4V", "#ff375f"],

+  wmv: ["WMV", "#ff375f"], flv: ["FLV", "#ff375f"], "3gp": ["3GP", "#ff375f"],

+  // 音频

+  mp3: ["MP3", "#ff9f0a"], wav: ["WAV", "#ff9f0a"], flac: ["FLAC", "#ff9f0a"],

+  aac: ["AAC", "#ff9f0a"], m4a: ["M4A", "#ff9f0a"], ogg: ["OGG", "#ff9f0a"],

+  opus: ["OPUS", "#ff9f0a"], wma: ["WMA", "#ff9f0a"],

+  // 压缩包

+  zip: ["ZIP", "#ffd60a"], rar: ["RAR", "#ffd60a"], "7z": ["7Z", "#ffd60a"],

+  tar: ["TAR", "#ffd60a"], gz: ["GZ", "#ffd60a"], bz2: ["BZ2", "#ffd60a"],

+  xz: ["XZ", "#ffd60a"],

 };

 const DEFAULT_EXT = ["FILE", "#636366"];

@@ -177,5 +187,5 @@
   const nav = $("catNav");

   const counts = { "全部": total, ...state.categories };

-  const order = ["全部", "技术开发", "人工智能", "金融投资", "营销运营", "教育学习", "健康养生", "职场管理", "生活随笔", "未分类"];

+  const order = ["全部", "文档", "图片", "视频", "音频", "压缩包", "代码", "其他"];

   const cats = order.filter((c) => counts[c] !== undefined);

   nav.innerHTML = cats

@@ -251,5 +261,5 @@
     } else {

       $("emptyTitle").textContent = "还没有文档";

-      $("emptySub").textContent = "把收藏的报告、笔记拖进来，自动总结并分类";

+      $("emptySub").textContent = "拖入文档 / 图片 / 视频 / 音频 / 压缩包，自动归类并管理";

     }

     return;

@@ -261,5 +271,5 @@
       const ext = (d.ext || "").replace(".", "").toLowerCase();

       const [label, color] = EXT_META[ext] || DEFAULT_EXT;

-      const cat = d.category || "未分类";

+      const cat = d.category || "其他";

       const kw = (d.keywords || []).slice(0, 3);

       const title = hl(d.title || d.filename, state.q);

@@ -360,5 +370,5 @@
 async function uploadFiles(fileList) {

   const files = Array.from(fileList).filter(

-    (f) => /\.(pdf|docx|md|markdown|txt|html|htm|xlsx|xls|pptx|png|jpg|jpeg|bmp|webp|tiff|tif|gif)$/i.test(f.name)

+    (f) => /\.(pdf|docx|md|markdown|txt|html|htm|xlsx|xls|pptx|png|jpg|jpeg|bmp|webp|tiff|tif|gif|mp4|avi|mov|wmv|flv|mkv|webm|m4v|3gp|mp3|wav|aac|flac|ogg|wma|m4a|opus|zip|rar|7z|tar|gz|bz2|xz)$/i.test(f.name)

   );

   if (files.length !== fileList.length) toast("已忽略不支持的文件类型");

@@ -387,5 +397,12 @@
 /* ---------- 详情抽屉（焦点管理 + 键盘闭环） ---------- */

 const PREVIEW_IMAGE = new Set(["png", "jpg", "jpeg", "bmp", "webp", "tiff", "tif", "gif"]);

-const PREVIEW_NO = new Set(["xlsx", "xls", "pptx"]);

+const PREVIEW_NO = new Set([

+  // Office：下载查看

+  "xlsx", "xls", "pptx",

+  // 视频 / 音频 / 压缩包：无文本预览，统一提示下载

+  "mp4", "avi", "mov", "wmv", "flv", "mkv", "webm", "m4v", "3gp",

+  "mp3", "wav", "aac", "flac", "ogg", "wma", "m4a", "opus",

+  "zip", "rar", "7z", "tar", "gz", "bz2", "xz",

+]);

 

 /* 源文件预览：fetch 带鉴权头取 blob → objectURL（img/iframe 无法带自定义 header） */

@@ -418,5 +435,5 @@
 function renderDrawer(d) {

   const body = $("drawerBody");

-  const cat = d.category || "未分类";

+  const cat = d.category || "其他";

   const kw = d.keywords || [];

   const tags = d.tags || [];
```

**文件:** `frontend/index.html`
```diff
--- frontend/index.html
+++ frontend/index.html
@@ -266,5 +266,5 @@
 

     <div class="sidebar-foot">

-      <div class="drop-hint">把报告 / 笔记拖进窗口即可入库</div>

+      <div class="drop-hint">把文档 / 图片 / 视频 / 压缩包拖进窗口即可入库</div>

     </div>

   </aside>

@@ -313,5 +313,5 @@
         <div class="drop-icon">📥</div>

         <div class="drop-title">松开导入文档</div>

-        <div class="drop-sub">支持 PDF / Word / Markdown / TXT / HTML</div>

+        <div class="drop-sub">支持 文档 / 图片 / 视频 / 音频 / 压缩包 / 代码</div>

       </div>

     </div>

@@ -327,5 +327,5 @@
         <div class="empty-icon">🗂️</div>

         <div class="empty-title" id="emptyTitle">还没有文档</div>

-        <div class="empty-sub" id="emptySub">把收藏的报告、笔记拖进来，自动总结并分类</div>

+        <div class="empty-sub" id="emptySub">拖入文档 / 图片 / 视频 / 音频 / 压缩包，自动归类并管理</div>

         <button class="reset-btn" id="resetFilter" hidden>清除筛选条件</button>

       </div>

@@ -424,5 +424,5 @@
 <div class="toast" id="toast" hidden></div>

 

-<input type="file" id="fileInput" multiple accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.xlsx,.xls,.pptx,.png,.jpg,.jpeg,.bmp,.webp,.tiff,.tif,.gif" hidden>

+<input type="file" id="fileInput" multiple accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.xlsx,.xls,.pptx,.png,.jpg,.jpeg,.bmp,.webp,.tiff,.tif,.gif,.mp4,.avi,.mov,.wmv,.flv,.mkv,.webm,.m4v,.3gp,.mp3,.wav,.aac,.flac,.ogg,.wma,.m4a,.opus,.zip,.rar,.7z,.tar,.gz,.bz2,.xz" hidden>

 

 <script src="app.js?v=5"></script>
```

**文件:** `tools/reclassify_ext.py`（新增文件）
```diff
--- /dev/null
+++ tools/reclassify_ext.py
@@ -0,0 +1,356 @@
+# -*- coding: utf-8 -*-
+"""存量文档重分类工具：按文件扩展名更新 documents 的 category/tags。
+
+背景
+====
+早期版本把文档按「内容主题 8 大类」分类并存入 documents.category / tags。
+本次把自动分类改为「按文件扩展名归类为文件类型」（文档/图片/视频/音频/压缩包/
+代码/其他）。本工具用于将存量文档（此前按主题分类入库的数据）统一改为按扩展名
+归类，只更新 category 与 tags 两列，绝不动文件本体、内容、keywords 等其他字段。
+
+数据红线（务必遵守）
+====================
+1) 仅更新 documents.category 与 documents.tags 两列。
+2) 执行前自动把 knowledge.db 备份到同目录（带时间戳副本），不删除原库。
+3) 数据与程序本体隔离：默认连接 KB_DATA_DIR（环境变量）指向的数据库；
+   也可用 --db / --data-dir 显式指定外置库。
+4) 敏感列（category/tags/keywords/...）在库内为密文（enc_ver=1，AES-GCM）。
+   因此本工具需要账号密码解开该用户 DEK，先解密再按扩展名重算，再加密写回。
+
+用法
+====
+  # 预览（不写库）：列出将变化的行数、旧→新分布
+  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --dry-run
+
+  # 正式执行（自动备份后写库）
+  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --yes
+  # 指定用户名（默认询问；单用户环境通常为 king）
+  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --username king --yes
+"""
+from __future__ import annotations
+
+import argparse
+import base64
+import getpass
+import json
+import os
+import sqlite3
+import sys
+from collections import Counter, defaultdict
+from datetime import datetime
+
+# Windows 控制台默认 GBK 无法输出中文时转为 UTF-8，避免脚本报 UnicodeEncodeError
+try:
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
+except Exception:
+    pass
+
+# 允许从项目根直接以 `python tools/reclassify_ext.py` 运行
+BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+if BASE not in sys.path:
+    sys.path.insert(0, BASE)
+
+from backend import classify
+from backend.security import auth, crypto  # noqa: E402
+
+# 早期「内容主题」类别名：存量 tags 中若混入这些标签，重分类时剔除
+_LEGACY_TOPIC_NAMES = frozenset({
+    "技术开发", "人工智能", "金融投资", "营销运营", "教育学习",
+    "健康养生", "职场管理", "生活随笔", "未分类",
+})
+# 文件类型名同样不作为内容标签保留
+_FILE_TYPE_NAMES = frozenset(classify.FILE_TYPE_NAMES)
+
+
+def _now_stamp() -> str:
+    return datetime.now().strftime("%Y%m%d_%H%M%S")
+
+
+def resolve_db_path(args) -> str:
+    """解析数据库路径：--db > --data-dir > 环境 KB_DATA_DIR > 项目内 data/。"""
+    if getattr(args, "db", None):
+        return os.path.abspath(args.db)
+    data_dir = getattr(args, "data_dir", None) or os.environ.get("KB_DATA_DIR", "").strip()
+    if not data_dir:
+        data_dir = os.path.join(BASE, "data")
+    return os.path.join(os.path.abspath(data_dir), "knowledge.db")
+
+
+def backup_db(db_path: str) -> str:
+    """同目录生成带时间戳的 SQLite 一致性备份，返回备份路径。"""
+    if not os.path.exists(db_path):
+        raise FileNotFoundError(f"数据库不存在: {db_path}")
+    backup_path = os.path.join(
+        os.path.dirname(db_path),
+        f"knowledge_{_now_stamp()}_reclass.bak.db",
+    )
+    src = sqlite3.connect(db_path)
+    try:
+        dst = sqlite3.connect(backup_path)
+        try:
+            with dst:
+                src.backup(dst)
+        finally:
+            dst.close()
+    finally:
+        src.close()
+    return backup_path
+
+
+def list_users(conn: sqlite3.Connection) -> list[dict]:
+    conn.row_factory = sqlite3.Row
+    rows = conn.execute(
+        """SELECT u.id, u.username, u.role,
+                  (SELECT COUNT(*) FROM documents d WHERE d.user_id = u.id) AS doc_count
+           FROM users u ORDER BY u.id"""
+    ).fetchall()
+    return [dict(r) for r in rows]
+
+
+def fetch_docs(conn: sqlite3.Connection, user_id: int) -> list[dict]:
+    conn.row_factory = sqlite3.Row
+    rows = conn.execute(
+        """SELECT id, stored_name, ext, filename, category, tags, keywords, enc_ver
+           FROM documents WHERE user_id=? ORDER BY id""",
+        (user_id,),
+    ).fetchall()
+    return [dict(r) for r in rows]
+
+
+def resolve_user_dek(conn: sqlite3.Connection, username: str, password: str) -> bytes:
+    """按登录同款流程解出该用户 DEK：KEK=Argon2id(password,salt) → 解 wrapped_dek。"""
+    conn.row_factory = sqlite3.Row
+    row = conn.execute(
+        "SELECT salt_kek, wrapped_dek, dek_check FROM users WHERE username=?",
+        (username,),
+    ).fetchone()
+    if row is None:
+        raise ValueError(f"用户不存在: {username}")
+    try:
+        kek = auth._derive_kek(password, row["salt_kek"])
+        dek = crypto.dec_bytes(kek, base64.b64decode(row["wrapped_dek"]))
+    except Exception:
+        raise PermissionError("密码错误，无法解开该用户密钥")
+    if not crypto.verify_dek(dek, row["dek_check"]):
+        raise PermissionError("密钥自检失败：请确认账号密码正确")
+    return dek
+
+
+def _ext_of_stored(stored_name: str, ext_col: str, filename: str) -> str:
+    """优先 stored_name（服务端生成的存储名，扩展名可靠），其次 ext 列/原始文件名。"""
+    for src in (stored_name or "", filename or ""):
+        name = src.strip()
+        if name:
+            ext = os.path.splitext(name)[1]
+            if ext:
+                return ext
+    if ext_col:
+        return ext_col
+    return ""
+
+
+def compute_new_tags(old_tags: list[str]) -> list[str]:
+    """剔除旧主题标签与文件类型名，保留真正的关键词标签。"""
+    out: list[str] = []
+    for t in old_tags or []:
+        s = str(t).strip()
+        if not s or s in _LEGACY_TOPIC_NAMES or s in _FILE_TYPE_NAMES:
+            continue
+        if s not in out:
+            out.append(s)
+    return out
+
+
+def plan_doc(doc: dict, dek: bytes) -> dict:
+    """对单篇文档计算重分类方案。
+
+    仅解密读取 category/tags 以规划新值；keywords 仅用于观测，不修改。
+    返回 {doc, old_category, old_tags, new_category, new_tags, changed}
+    """
+    decrypted = None
+    enc_ver = doc.get("enc_ver") or 0
+    if enc_ver == 1:
+        row = dict(doc)
+        from backend.security import vault
+        decrypted = vault.dec_row(dek, row)
+    else:
+        # enc_ver != 1（正常经过启动迁移后应不存在）：category/tags 可能明文或异常
+        raise RuntimeError(
+            f"文档 id={doc['id']} enc_ver={enc_ver} 非预期（期望 1）。"
+            "请先正常启动一次服务完成旧库迁移，再执行重分类。"
+        )
+
+    old_category = decrypted.get("category") or classify.DEFAULT_CATEGORY
+    old_tags = decrypted.get("tags") or []
+    ext = _ext_of_stored(doc.get("stored_name") or "", doc.get("ext") or "",
+                         decrypted.get("filename") or "")
+    new_category = classify.classify_by_ext(ext)
+    new_tags = compute_new_tags(old_tags)
+    changed = (new_category != old_category) or (new_tags != old_tags)
+    return {
+        "doc": doc,
+        "old_category": old_category,
+        "old_tags": old_tags,
+        "new_category": new_category,
+        "new_tags": new_tags,
+        "changed": changed,
+        "enc_ver": enc_ver,
+    }
+
+
+def encode_fields(dek: bytes, new_category: str, new_tags: list[str]) -> tuple[str, str]:
+    """把新 category/tags 加密为库内密文（tags 先 JSON 序列化）。"""
+    cat_raw = crypto.enc_field(dek, new_category)
+    tags_raw = crypto.enc_field(dek, json.dumps(new_tags, ensure_ascii=False))
+    return cat_raw, tags_raw
+
+
+def write_doc(conn: sqlite3.Connection, doc_id: int, dek: bytes,
+              new_category: str, new_tags: list[str]) -> None:
+    cat_raw, tags_raw = encode_fields(dek, new_category, new_tags)
+    conn.execute(
+        "UPDATE documents SET category=?, tags=? WHERE id=?",
+        (cat_raw, tags_raw, doc_id),
+    )
+
+
+def summarize(plans: list[dict]) -> dict:
+    transitions = Counter()
+    new_counts = Counter()
+    old_counts = Counter()
+    changed_ids = []
+    for p in plans:
+        old_counts[p["old_category"]] += 1
+        new_counts[p["new_category"]] += 1
+        if p["changed"]:
+            transitions[(p["old_category"], p["new_category"])] += 1
+            changed_ids.append(p["doc"]["id"])
+    return {
+        "total": len(plans),
+        "changed": len(changed_ids),
+        "unchanged": len(plans) - len(changed_ids),
+        "old_counts": old_counts,
+        "new_counts": new_counts,
+        "transitions": transitions,
+        "changed_ids": changed_ids,
+    }
+
+
+def print_summary(summary: dict, dry_run: bool) -> None:
+    mode = "DRY-RUN（仅预览，未写库）" if dry_run else "已写库"
+    print(f"\n===== 重分类结果 [{mode}] =====")
+    print(f"存量文档总数      : {summary['total']}")
+    print(f"需要更新(category或tags变化): {summary['changed']}")
+    print(f"无需变化          : {summary['unchanged']}")
+    if summary["total"] == 0:
+        return
+    print("\n[按扩展名归类后的新分布]")
+    for cat in classify.FILE_CATEGORY_ORDER:
+        if summary["new_counts"][cat]:
+            print(f"  {cat:<4}: {summary['new_counts'][cat]}")
+    print("\n[旧分布]")
+    for cat, n in summary["old_counts"].most_common():
+        print(f"  {cat:<4}: {n}")
+    print("\n[主题→文件类型迁移明细(仅发生变化的)]")
+    for (old, new), n in sorted(summary["transitions"].items(), key=lambda x: -x[1]):
+        if n:
+            print(f"  {old} -> {new} : {n}")
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser(description="存量文档按扩展名重分类（只动 category/tags，执行前自动备份）")
+    ap.add_argument("--db", default=None, help="直接指定 knowledge.db 路径（优先级最高）")
+    ap.add_argument("--data-dir", default=None,
+                    help="数据目录（含 knowledge.db 与 documents/；默认取 KB_DATA_DIR 环境变量，未设置则项目 data/）")
+    ap.add_argument("--username", default=None, help="要处理的用户名（默认交互询问）")
+    ap.add_argument("--password", default=None, help="该用户密码（不建议明文传参，默认交互输入）")
+    ap.add_argument("--dry-run", action="store_true", help="仅预览影响行数与分布，不写库、不备份")
+    ap.add_argument("--yes", action="store_true", help="跳过确认提示（正式写库仍会自动备份）")
+    args = ap.parse_args()
+
+    db_path = resolve_db_path(args)
+    if not os.path.exists(db_path):
+        print(f"[错误] 数据库不存在: {db_path}\n提示：可用 --data-dir 或 --db 指定外置数据目录。")
+        return 2
+    print(f"[信息] 使用数据库: {db_path}")
+
+    conn = sqlite3.connect(db_path)
+    try:
+        users = list_users(conn)
+        if not users:
+            print("[错误] 库中无用户，无法继续。")
+            return 2
+        username = (args.username or "").strip()
+        if not username:
+            print("可选用户: " + ", ".join(f"{u['username']}(文档{u['doc_count']})" for u in users))
+            username = input("输入要处理的用户名: ").strip() or (users[0]["username"] if len(users) == 1 else "")
+        target = next((u for u in users if u["username"] == username.lower()), None)
+        if target is None:
+            print(f"[错误] 用户不存在: {username}")
+            return 2
+        print(f"[信息] 处理用户: {target['username']}（角色 {target['role']}，文档 {target['doc_count']} 条）")
+
+        docs = fetch_docs(conn, target["id"])
+        if not docs:
+            print("[信息] 该用户没有存量文档，无需重分类。")
+            print_summary({"total": 0, "changed": 0, "unchanged": 0,
+                           "old_counts": Counter(), "new_counts": Counter(),
+                           "transitions": Counter()}, dry_run=args.dry_run)
+            return 0
+
+        password = args.password
+        if password is None:
+            password = getpass.getpass(f"输入用户 {target['username']} 的密码（仅用于内存解开DEK）: ")
+        if not password:
+            print("[错误] 未提供密码，无法解密存量数据。")
+            return 2
+        dek = resolve_user_dek(conn, target["username"], password)
+
+        plans = []
+        for doc in docs:
+            try:
+                plans.append(plan_doc(doc, dek))
+            except RuntimeError as e:
+                print(f"[跳过] {e}")
+        summary = summarize(plans)
+        print_summary(summary, dry_run=args.dry_run)
+
+        if summary["changed"] == 0:
+            print("\n[信息] 无变化，无需写库。")
+            return 0
+        if args.dry_run:
+            print("\n[提示] 已加 --dry-run，未写库、未备份。去掉 --dry-run 并加 --yes 正式执行。")
+            return 0
+
+        if not args.yes:
+            ans = input(f"\n将更新 {summary['changed']} 条文档的 category/tags，"
+                        f"执行前自动备份 knowledge.db。确认执行？[y/N] ").strip().lower()
+            if ans not in ("y", "yes"):
+                print("已取消。")
+                return 0
+
+        backup_path = backup_db(db_path)
+        print(f"[备份] knowledge.db -> {backup_path}")
+
+        conn.execute("BEGIN")
+        try:
+            for p in plans:
+                if p["changed"]:
+                    write_doc(conn, p["doc"]["id"], dek, p["new_category"], p["new_tags"])
+            conn.commit()
+        except Exception:
+            conn.rollback()
+            raise
+        print(f"[完成] 已更新 {summary['changed']} 条文档（category/tags）。")
+    finally:
+        conn.close()
+    return 0
+
+
+if __name__ == "__main__":
+    try:
+        sys.exit(main())
+    except KeyboardInterrupt:
+        print("\n已取消。")
+        sys.exit(130)
```


---

### 2026-09-02 12:30:40 | 安全审计与加固：CORS收紧/安全响应头/关闭API文档/IP限流/锁定消息模糊化/上传限制/路径穿越防御/审计记录来源IP

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -4,7 +4,13 @@
 运行: python app.py  (或 uvicorn app:app)

 安全模型: 磁盘恒密文 · 密码即钥匙 · 会话内存驻留 · 全部 API 鉴权

+加固说明（2026-09-02 安全审计）:

+- CORS 收紧为同源回环地址（本地单机部署，前端静态资源由本服务托管）

+- 安全响应头（nosniff / frame / referrer / CSP / permissions-policy）

+- 关闭 /docs /redoc /openapi 暴露

+- 登录/注册/找回/改密接入 IP 级限流；账号锁定消息模糊化

+- 上传文件大小/数量限制；serve_file 路径穿越纵深防御；limit 参数收敛

+- 审计日志记录来源 IP

 """

 import os

-import shutil

 import uuid

 import logging

@@ -13,9 +19,9 @@
 from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Depends, Request

 from fastapi.middleware.cors import CORSMiddleware

-from fastapi.responses import Response

+from fastapi.responses import Response, JSONResponse

 from fastapi.staticfiles import StaticFiles

 

 from backend import parser, nlp, classify, store

-from backend.security import auth, deps

+from backend.security import auth, deps, ratelimit

 from backend.security.deps import get_current_user, require_admin

 

@@ -26,8 +32,62 @@
 FRONTEND_DIR = BASE_DIR / "frontend"

 

-app = FastAPI(title="个人知识管理工作台", version="2.0.0")

+# ---------------- 安全常量 ----------------

+MAX_FILE_SIZE = 200 * 1024 * 1024      # 单文件上传上限 200MB（解析/解密均走流式/内存可控）

+MAX_FILES_PER_BATCH = 50               # 单次批量上传文件数上限

+MAX_JSON_BODY = 1 * 1024 * 1024        # JSON 请求体上限 1MB（multipart 上传走流式，不在此限）

+_ALLOWED_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]

+_ALLOWED_HEADERS = ["Authorization", "Content-Type"]

+

+_PORT = int(os.environ.get("PORT", 8787))

+

+app = FastAPI(

+    title="个人知识管理工作台", version="2.0.0",

+    docs_url=None, redoc_url=None, openapi_url=None,  # 纵深防御：关闭 API 结构暴露

+)

+# CORS 收紧：本地单机应用，前端与 API 同源托管，仅放行回环地址

 app.add_middleware(

-    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],

+    CORSMiddleware,

+    allow_origins=[f"http://127.0.0.1:{_PORT}", f"http://localhost:{_PORT}"],

+    allow_methods=_ALLOWED_METHODS,

+    allow_headers=_ALLOWED_HEADERS,

+    allow_credentials=False,

+    max_age=600,

 )

+

+

+@app.middleware("http")

+async def security_headers(request: Request, call_next):

+    """安全响应头：防 MIME 嗅探 / 点击劫持 / 信息泄露 / 权限滥用。"""

+    resp = await call_next(request)

+    resp.headers.setdefault("X-Content-Type-Options", "nosniff")

+    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # 允许同源 iframe 预览 PDF

+    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

+    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")

+    resp.headers.setdefault(

+        "Content-Security-Policy",

+        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "

+        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "

+        "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "

+        "frame-ancestors 'self'",

+    )

+    return resp

+

+

+@app.middleware("http")

+async def limit_json_body(request: Request, call_next):

+    """JSON 请求体大小限制（multipart 上传为流式 SpooledTemporaryFile，不受此限）。"""

+    if request.method in ("POST", "PUT", "PATCH") and not request.url.path.startswith("/api/upload"):

+        cl = request.headers.get("content-length")

+        if cl and cl.isdigit() and int(cl) > MAX_JSON_BODY:

+            return JSONResponse({"detail": "请求体过大"}, status_code=413)

+    return await call_next(request)

+

+

+def _rl(limiter, action: str):

+    """IP 限流依赖工厂：按 (来源IP, 动作) 滑动窗口计数，超限 429。"""

+    def dep(request: Request):

+        if not limiter.allow(f"{ratelimit.client_ip(request)}:{action}"):

+            raise HTTPException(429, "操作过于频繁，请稍后再试")

+    return dep

 

 

@@ -65,9 +125,20 @@
         }

 

-    # 1) 上传流写随机明文临时文件（带原扩展名，供解析器按类型分发）

+    # 1) 上传流写随机明文临时文件（带原扩展名，供解析器按类型分发），流式计数防超大文件

     tmp = Path(store.DOCS_DIR) / f".{uuid.uuid4().hex}{ext}"

     try:

+        written = 0

         with open(tmp, "wb") as f:

-            shutil.copyfileobj(upload.file, f)

+            while True:

+                chunk = upload.file.read(1 << 20)

+                if not chunk:

+                    break

+                written += len(chunk)

+                if written > MAX_FILE_SIZE:

+                    return {

+                        "ok": False, "filename": filename,

+                        "error": f"文件过大（上限 {MAX_FILE_SIZE // (1024 * 1024)}MB），已拒绝入库",

+                    }

+                f.write(chunk)

 

         # 2) 解析（明文阶段，仅内存/临时）

@@ -114,5 +185,6 @@
                 os.remove(tmp)

             except OSError:

-                pass

+                # 明文临时文件残留即泄露风险：删除失败必须告警（Windows 沙箱等环境会拦截 os.remove）

+                log.warning("临时明文文件删除失败，请手动清理: %s", tmp)

 

 

@@ -120,5 +192,6 @@
 

 @app.post("/api/auth/register")

-def auth_register(payload: dict):

+def auth_register(payload: dict, request: Request,

+                  _=Depends(_rl(ratelimit.register_limiter, "register"))):

     """注册普通用户。questions: [{"question","answer"}, ...] 1-3 个（可选但推荐）。"""

     try:

@@ -127,4 +200,5 @@
             payload.get("password", ""),

             payload.get("questions"),

+            ip=ratelimit.client_ip(request),

         )

     except (ValueError, PermissionError) as e:

@@ -134,8 +208,9 @@
 

 @app.post("/api/auth/login")

-def auth_login(payload: dict):

+def auth_login(payload: dict, request: Request,

+               _=Depends(_rl(ratelimit.login_limiter, "login"))):

     try:

         result = auth.login(payload.get("username", ""), payload.get("password", ""),

-                            bool(payload.get("remember", False)))

+                            bool(payload.get("remember", False)), ip=ratelimit.client_ip(request))

     except ValueError as e:

         raise HTTPException(401, str(e))

@@ -163,7 +238,10 @@
 

 @app.post("/api/auth/change-password")

-def auth_change_password(payload: dict, sess=Depends(get_current_user)):

-    try:

-        auth.change_password(sess.user_id, payload.get("old_password", ""), payload.get("new_password", ""))

+def auth_change_password(payload: dict, request: Request,

+                         sess=Depends(get_current_user),

+                         _=Depends(_rl(ratelimit.change_pw_limiter, "change_pw"))):

+    try:

+        auth.change_password(sess.user_id, payload.get("old_password", ""),

+                             payload.get("new_password", ""), ip=ratelimit.client_ip(request))

     except ValueError as e:

         raise HTTPException(400, str(e))

@@ -172,10 +250,12 @@
 

 @app.get("/api/auth/questions")

-def auth_questions(username: str = Query("")):

+def auth_questions(username: str = Query("", max_length=64),

+                   _=Depends(_rl(ratelimit.questions_limiter, "questions"))):

     return {"ok": True, "questions": auth.get_security_questions(username)}

 

 

 @app.post("/api/auth/reset-password")

-def auth_reset_password(payload: dict):

+def auth_reset_password(payload: dict, request: Request,

+                        _=Depends(_rl(ratelimit.reset_limiter, "reset"))):

     """安全问题找回：answers 顺序与注册时一致，全部答对才放行。"""

     try:

@@ -184,4 +264,5 @@
             payload.get("answers") or [],

             payload.get("new_password", ""),

+            ip=ratelimit.client_ip(request),

         )

     except ValueError as e:

@@ -198,7 +279,8 @@
 

 @app.post("/api/admin/users/{username}/reset-password")

-def admin_reset(username: str, payload: dict, sess=Depends(require_admin)):

-    try:

-        auth.admin_reset_password(sess.username, username, payload.get("new_password", ""))

+def admin_reset(username: str, payload: dict, request: Request, sess=Depends(require_admin)):

+    try:

+        auth.admin_reset_password(sess.username, username, payload.get("new_password", ""),

+                                  ip=ratelimit.client_ip(request))

     except (ValueError, PermissionError) as e:

         raise HTTPException(400, str(e))

@@ -207,5 +289,5 @@
 

 @app.get("/api/admin/audit")

-def admin_audit(limit: int = Query(100), _=Depends(require_admin)):

+def admin_audit(limit: int = Query(100, ge=1, le=500), _=Depends(require_admin)):

     return {"ok": True, "items": auth.audit_log_recent(limit)}

 

@@ -220,4 +302,8 @@
 ):

     """批量上传。paths 与 files 一一对应，保存每个文件的原始文件夹路径。"""

+    if not files:

+        raise HTTPException(400, "未选择文件")

+    if len(files) > MAX_FILES_PER_BATCH:

+        raise HTTPException(400, f"单次最多上传 {MAX_FILES_PER_BATCH} 个文件")

     dek, user_id = sess.dek, sess.user_id

     results = []

@@ -234,5 +320,5 @@
     q: str = Query(""),

     tag: str = Query(""),

-    limit: int = Query(200),

+    limit: int = Query(200, ge=1, le=500),

     sess=Depends(get_current_user),

 ):

@@ -268,5 +354,11 @@
 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):

-    """下载文档：先校验归属（纵深防御），再密文解密到内存字节直接响应。"""

+    """下载文档：先校验归属（纵深防御），再密文解密到内存字节直接响应。

+

+    路径穿越纵深防御：stored_name 必须是纯文件名（服务端生成），含路径分隔符一律拒绝，

+    防止历史异常数据/数据库被篡改后拼接 DOCS_DIR 越权读取。

+    """

+    if not stored_name or os.path.basename(stored_name) != stored_name:

+        raise HTTPException(404, "文件不存在")

     if not store.doc_owned_by(stored_name, sess.user_id):

         raise HTTPException(404, "文件不存在")
```

**文件:** `backend\security\auth.py`
```diff
--- backend\security\auth.py
+++ backend\security\auth.py
@@ -94,8 +94,11 @@
 # ---------------- 限流 ----------------

 def _check_lock(row) -> None:

+    """账号锁定检查。锁定期间返回与密码错误一致的提示（防账号存在性/锁定状态枚举），

+    且执行一次伪校验使耗时对齐（防时序侧信道）。"""

     if row["lock_until"]:

         until = datetime.strptime(row["lock_until"], "%Y-%m-%d %H:%M:%S")

         if until > datetime.now():

-            raise PermissionError(f"失败次数过多，已锁定至 {row['lock_until']}，请稍后再试")

+            verify_password(_DUMMY_HASH, "dummy-password-zz")

+            raise PermissionError("用户名或密码错误")

 

 

@@ -121,5 +124,9 @@
 

 # ---------------- 审计 ----------------

-def audit(actor: str, action: str, target: str = "", detail: str = "") -> None:

+def audit(actor: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:

+    """写入审计日志。ip 为来源地址（本地回环归一化为 loopback），并入 detail 前缀。

+    审计失败不阻断主流程。"""

+    if ip:

+        detail = f"[{ip}] {detail}".strip()

     try:

         conn = _conn()

@@ -342,5 +349,15 @@
 

 # ---------------- 注册 ----------------

-def register_user(username: str, password: str, questions: list[dict] | None = None) -> dict:

+def _validate_password(pw: str, ctx: str = "密码") -> str:

+    """统一密码强度校验（含长度上限，防超大输入耗尽 Argon2 计算资源）。"""

+    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 128:

+        raise ValueError(f"{ctx}需 8-128 位")

+    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):

+        raise ValueError(f"{ctx}至少 8 位且同时包含字母和数字")

+    return pw

+

+

+def register_user(username: str, password: str, questions: list[dict] | None = None,

+                  ip: str = "") -> dict:

     """注册普通用户。questions: [{"question": str, "answer": str}, ...]（1-3 个）。

 

@@ -352,6 +369,5 @@
     if not all(c.isalnum() or c in "_-" for c in username):

         raise ValueError("用户名仅允许字母/数字/下划线/中划线")

-    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):

-        raise ValueError("密码至少 8 位且同时包含字母和数字")

+    _validate_password(password)

 

     master = get_master_key()

@@ -404,13 +420,17 @@
 

     _archive_wrap(uid, salt_kek, wrapped, retired=1)

-    audit("system", "register", username)

+    audit("system", "register", username, ip=ip)

     return {"id": uid, "username": username, "questions": len(questions)}

 

 

 # ---------------- 登录 / 登出 ----------------

-def login(username: str, password: str, remember: bool = False) -> dict:

+def login(username: str, password: str, remember: bool = False, ip: str = "") -> dict:

     """登录。成功 → 创建会话；失败 → 统一提示（不泄露账号存在性）。

     remember=True → 30 天长期会话（"记住我"，同一设备免密访问）。"""

     username = username.strip().lower()

+    if not isinstance(password, str) or not password or len(password) > 128:

+        # 超大/缺失密码直接按失败处理（耗时对齐防探测）

+        verify_password(_DUMMY_HASH, "dummy-password-zz")

+        raise ValueError("用户名或密码错误")

     conn = _conn()

     row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

@@ -456,5 +476,5 @@
         timeout=timeout,

     )

-    audit("auth", "login", row["username"])

+    audit("auth", "login", row["username"], ip=ip)

     return {

         "token": token, "username": row["username"], "role": row["role"],

@@ -493,8 +513,7 @@
 

 # ---------------- 改密（用户自助） ----------------

-def change_password(user_id: int, old_password: str, new_password: str) -> None:

+def change_password(user_id: int, old_password: str, new_password: str, ip: str = "") -> None:

     """改密：只重新包裹 DEK，不重加密数据。事务 + 版本化 + 自检。"""

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

 

     conn = _conn()

@@ -567,11 +586,14 @@
 

     sessions.delete_by_user(user_id)  # 改密后旧会话全部失效

-    audit("auth", "change_password", f"user#{user_id}")

+    audit("auth", "change_password", f"user#{user_id}", ip=ip)

 

 

 # ---------------- 安全问题找回 ----------------

 def get_security_questions(username: str) -> list[str]:

-    """找回第一步：返回该用户的问题列表（不暴露任何其他信息）。"""

-    username = username.strip().lower()

+    """找回第一步：返回该用户的问题列表（不暴露任何其他信息）。

+

+    枚举防护依赖两层：IP 限流（app 路由层）+ 输入长度限制；问题本身不敏感。

+    """

+    username = username.strip().lower()[:64]

     conn = _conn()

     row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()

@@ -586,11 +608,11 @@
 

 

-def reset_password_via_questions(username: str, answers: list[str], new_password: str) -> None:

+def reset_password_via_questions(username: str, answers: list[str], new_password: str,

+                                 ip: str = "") -> None:

     """找回第二步：全部答对 → 解 DEK → 设新密码（单事务原子）。

 

     限流：任一题连续失败 5 次锁定 15 分钟（复用 users.login_fail/lock_until）。

     """

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

     username = username.strip().lower()

     conn = _conn()

@@ -655,5 +677,5 @@
         raise

     sessions.delete_by_user(user["id"])

-    audit("auth", "reset_via_questions", username)

+    audit("auth", "reset_via_questions", username, ip=ip)

 

 

@@ -671,11 +693,11 @@
 

 

-def admin_reset_password(admin_username: str, target_username: str, new_password: str) -> None:

+def admin_reset_password(admin_username: str, target_username: str, new_password: str,

+                         ip: str = "") -> None:

     """管理员重置用户密码（Master Key 通道，不依赖旧密码）。

 

     事务原子 + 版本化 + 自检；重置后强制用户下次改密并踢掉旧会话。

     """

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

     target_username = target_username.strip().lower()

     master = get_master_key()

@@ -729,5 +751,5 @@
         raise

     sessions.delete_by_user(target["id"])

-    audit("admin", "reset_password", target_username, f"by {admin_username}")

+    audit("admin", "reset_password", target_username, f"by {admin_username}", ip=ip)

 

 
```

**文件:** `backend\security\ratelimit.py`（新增文件）
```diff
--- /dev/null
+++ backend\security\ratelimit.py
@@ -0,0 +1,58 @@
+# -*- coding: utf-8 -*-
+"""IP 级限流：内存滑动窗口（按 来源IP + 动作 独立计数）。
+
+防御目标（账号级锁定挡不住的场景）：
+- 登录/找回/改密等认证接口被分布式暴力破解（多用户名轮换绕开单账号锁定）
+- 注册接口被批量滥用（垃圾账号泛滥）
+- 认证接口被高频探测（用户名枚举 / 存在性确认）
+
+实现：进程内存 dict[key -> deque[时间戳]]，滑动窗口内超限即拒绝（429）。
+惰性清理过期条目与空队列，防止 key 无限膨胀。
+"""
+import time
+from collections import deque, defaultdict
+from threading import Lock
+
+
+class RateLimiter:
+    def __init__(self, limit: int, window: float, max_keys: int = 10000):
+        self.limit = limit
+        self.window = window
+        self.max_keys = max_keys
+        self._hits: dict[str, deque] = defaultdict(deque)
+        self._lock = Lock()
+
+    def allow(self, key: str) -> bool:
+        """窗口内允许则记录本次并返回 True；超限返回 False。"""
+        now = time.time()
+        with self._lock:
+            q = self._hits[key]
+            while q and now - q[0] > self.window:
+                q.popleft()
+            if len(q) >= self.limit:
+                return False
+            q.append(now)
+            if len(self._hits) > self.max_keys:
+                self._sweep(now)
+            return True
+
+    def _sweep(self, now: float):
+        """清理空队列，防止 key 无限膨胀。"""
+        for k in [k for k, q in self._hits.items() if not q]:
+            del self._hits[k]
+
+
+# 全局限流器实例（按动作维度，独立窗口）
+login_limiter     = RateLimiter(limit=10, window=60)    # 登录：10 次/分钟/IP
+register_limiter  = RateLimiter(limit=20, window=3600)  # 注册：20 次/小时/IP
+questions_limiter = RateLimiter(limit=20, window=60)    # 安全问题获取：20 次/分钟/IP
+reset_limiter     = RateLimiter(limit=10, window=60)    # 安全问题重置：10 次/分钟/IP
+change_pw_limiter = RateLimiter(limit=10, window=60)    # 改密：10 次/分钟/IP
+
+
+def client_ip(request) -> str:
+    """取客户端 IP（回环地址归一化；不透传不可信的 X-Forwarded-For，本地直连无需代理头）。"""
+    host = getattr(request.client, "host", "") or ""
+    if host in ("127.0.0.1", "::1"):
+        return "loopback"
+    return host
```

---

### 2026-09-02 12:05:21 | 实现数据与程序本体隔离（根治更新程序后文件丢失）：新增 backend/paths.py 统一数据路径（环境变量 KB_DATA_DIR 优先，回落项目内 data/ 兼容旧部署）；store.py/auth.py 改走统一路径；用户数据已迁移至外置目录 D:/agent/知识工作台_数据（documents/knowledge.db/kbtest）；start.bat/start.sh 注入 KB_DATA_DIR；tools/backup.py 支持多根联合备份（外置数据以 kbdata/ 前缀并入快照，restore 自动拆分回数据目录）。app.py 本轮无代码变更

**文件:** `backend\paths.py`（新增文件）
```diff
--- /dev/null
+++ backend\paths.py
@@ -0,0 +1,35 @@
+# -*- coding: utf-8 -*-
+"""
+统一数据路径解析：实现「数据与程序本体隔离」。
+================================================
+数据目录优先级：
+  1. 环境变量 KB_DATA_DIR（start.bat / start.sh 注入，指向外置数据目录）
+  2. 程序根目录下 data/（兼容旧部署 / 直接 python app.py 运行）
+
+数据目录独立于程序目录后，更新/替换程序代码不会影响任何用户数据。
+"""
+import os
+
+PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+
+
+def get_data_dir() -> str:
+    """返回数据根目录（自动创建由各使用方负责，或在此确保存在）。"""
+    env = os.environ.get("KB_DATA_DIR", "").strip()
+    if env:
+        return env
+    return os.path.join(PROJECT_ROOT, "data")
+
+
+def get_docs_dir() -> str:
+    return os.path.join(get_data_dir(), "documents")
+
+
+def get_db_path() -> str:
+    return os.path.join(get_data_dir(), "knowledge.db")
+
+
+def ensure_data_dir() -> str:
+    d = get_data_dir()
+    os.makedirs(d, exist_ok=True)
+    return d
```

**文件:** `backend\store.py`
```diff
--- backend\store.py
+++ backend\store.py
@@ -18,9 +18,10 @@
 

 from .security import vault, crypto

+from .paths import get_data_dir, get_docs_dir, get_db_path

 

 BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

-DATA_DIR = os.path.join(BASE_DIR, "data")

-DOCS_DIR = os.path.join(DATA_DIR, "documents")

-DB_PATH = os.path.join(DATA_DIR, "knowledge.db")

+DATA_DIR = get_data_dir()          # 数据根目录（外置 KB_DATA_DIR 或项目内 data/）

+DOCS_DIR = get_docs_dir()

+DB_PATH = get_db_path()

 

 _SCHEMA = """
```

**文件:** `backend\security\auth.py`
```diff
--- backend\security\auth.py
+++ backend\security\auth.py
@@ -29,4 +29,5 @@
 from . import crypto

 from .session import sessions, REMEMBER_TIMEOUT

+from ..paths import get_db_path, get_data_dir, get_docs_dir

 

 # ---------------- Argon2id ----------------

@@ -42,8 +43,5 @@
 _master_lock = threading.Lock()

 

-DB_PATH = os.path.join(

-    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),

-    "data", "knowledge.db",

-)

+DB_PATH = get_db_path()

 BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

 

@@ -271,5 +269,5 @@
 def _cleanup_stray_tmp() -> None:

     """启动清理：删除崩溃残留的明文临时文件（. 开头的隐藏临时文件），杜绝明文残留。"""

-    docs_dir = os.path.join(BASE_DIR, "data", "documents")

+    docs_dir = get_docs_dir()

     if os.path.isdir(docs_dir):

         for f in os.listdir(docs_dir):

@@ -307,5 +305,5 @@
         "SELECT id, enc_ver, stored_name, filename, title, category, tags, keywords, summary, content, path FROM documents"

     ).fetchall()

-    docs_dir = os.path.join(BASE_DIR, "data", "documents")

+    docs_dir = get_docs_dir()

     for r in rows:

         # 1) 文件副本：按文件头检测，非 KBENC 密文则加密覆盖（幂等，兼容字段已加密但文件漏加密的历史状态）
```

**文件:** `start.bat`
```diff
--- start.bat
+++ start.bat
@@ -6,4 +6,8 @@
 set PY=C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\python.exe

 if not exist "%PY%" set PY=python

+

+rem ===== 数据目录外置：程序与用户数据隔离（更新程序不影响数据）=====

+set KB_DATA_DIR=D:\agent\知识工作台_数据

+if not exist "%KB_DATA_DIR%" mkdir "%KB_DATA_DIR%"

 

 rem ===== 检测服务是否已在运行（端口 8787 被占用 = 已在运行）=====
```

**文件:** `start.sh`
```diff
--- start.sh
+++ start.sh
@@ -6,4 +6,8 @@
 [ -x "$PY" ] || PY=python

 

+# 数据目录外置：程序与用户数据隔离（更新程序不影响数据）

+export KB_DATA_DIR="D:/agent/知识工作台_数据"

+mkdir -p "$KB_DATA_DIR"

+

 echo "========================================"

 echo "  个人知识管理工作台  正在启动..."
```

**文件:** `tools\backup.py`
```diff
--- tools\backup.py
+++ tools\backup.py
@@ -11,5 +11,5 @@
 

 用法:

-  python tools/backup.py backup  [--comment "说明"] [--dir 备份根]   # 一键备份

+  python tools/backup.py backup  [--comment "说明"] [--dir 备份根] [--data-dir 数据目录]  # 一键备份

   python tools/backup.py list   [--dir 备份根]                      # 查看快照

   python tools/backup.py restore <快照名> [--to 目标目录] [--dir 备份根]  # 恢复

@@ -17,4 +17,6 @@
 

 备份根默认 <项目根>/backup/，--dir 可指向其他位置（U 盘 / 网盘同步目录）。

+外置数据目录（KB_DATA_DIR / --data-dir）以 kbdata/ 前缀并入同一快照，

+restore 时自动拆回数据目录；代码与用户数据一起备份、一起回滚。

 knowledge.db 等 SQLite 文件用 Online Backup API 生成一致性副本，运行中备份也安全。

 """

@@ -135,4 +137,16 @@
 

 

+def _resolve_data_dir(args) -> str | None:

+    """外置数据目录：--data-dir > 环境变量 KB_DATA_DIR；未配置且不在项目内则 None。"""

+    d = getattr(args, "data_dir", None) or os.environ.get("KB_DATA_DIR", "").strip()

+    if not d:

+        return None

+    d = os.path.abspath(d)

+    # 若指向项目内 data/（兼容旧部署），BASE 扫描已覆盖，无需单独收集

+    if d == os.path.join(BASE, "data"):

+        return None

+    return d

+

+

 # ---------- 子命令 ----------

 def cmd_backup(args):

@@ -153,4 +167,17 @@
             files[rel.replace("\\", "/")] = info

             total += info["size"]

+

+    # 外置数据目录：以 kbdata/ 前缀并入同一快照（与代码一起、对象库去重仍然生效）

+    data_dir = _resolve_data_dir(args)

+    data_count = 0

+    if data_dir and os.path.isdir(data_dir):

+        for dirpath, dirnames, filenames in os.walk(data_dir):

+            for fn in filenames:

+                full = os.path.join(dirpath, fn)

+                rel = os.path.relpath(full, data_dir).replace("\\", "/")

+                info = _ingest(root, full, "kbdata/" + rel)

+                files["kbdata/" + rel] = info

+                total += info["size"]

+                data_count += 1

 

     name = datetime.now().strftime("%Y%m%d_%H%M%S")

@@ -181,5 +208,6 @@
 

     print(f"  ✓ 备份完成: {os.path.basename(snap)[:-5]}")

-    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}")

+    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}"

+          + (f"（其中数据目录 {data_count} 个）" if data_count else ""))

     if prev:

         print(f"    相对上次: 新增/变更 {added} 个文件, 新增存储 {_fmt(added_bytes)}")

@@ -226,10 +254,17 @@
         sys.exit(1)

     meta = json.load(open(p, encoding="utf-8"))

+    data_dir = _resolve_data_dir(args)

     dst_root = os.path.abspath(args.to) if args.to else BASE

     print(f"  恢复快照 {os.path.basename(p)[:-5]} -> {dst_root}")

     n = 0

     for rel, info in meta["files"].items():

-        full = os.path.realpath(os.path.join(dst_root, rel))

-        if not full.startswith(os.path.realpath(dst_root) + os.sep):

+        # kbdata/ 前缀 → 外置数据目录（未配置则回落项目内 data/）

+        if rel.startswith("kbdata/"):

+            target = data_dir or os.path.join(BASE, "data")

+            rel = rel[len("kbdata/"):]

+        else:

+            target = dst_root

+        full = os.path.realpath(os.path.join(target, rel))

+        if not full.startswith(os.path.realpath(target) + os.sep):

             raise ValueError(f"非法路径: {rel}")

         os.makedirs(os.path.dirname(full), exist_ok=True)

@@ -288,4 +323,6 @@
         p.add_argument("--dir", default=DEFAULT_BACKUP_ROOT,

                        help=f"备份根目录(默认 {DEFAULT_BACKUP_ROOT})")

+        p.add_argument("--data-dir", default=None,

+                       help="外置数据目录(默认取环境变量 KB_DATA_DIR；未配置则只备份代码)")

 

     p1 = sub.add_parser("backup")
```

---

### 2026-09-02 11:25:36 | 撤除程序内嵌的「历史版本」备份逻辑，改由独立增量备份工具接管：删除 backend/versions.py（CHANGELOG 解析+反向 patch 恢复）与 /api/versions、/api/versions/{index}/restore 两个接口；前端移除侧边栏入口、历史版本面板、恢复确认弹窗及全部 JS/CSS（含 loadAll、Esc/Tab 键盘处理、enterApp 中的残留分支）。备份职责完全移交 tools/backup.py（内容寻址增量备份，已存 20260902_112513 基线快照）

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -266,23 +266,4 @@
 

 

-# ---------------- 历史版本（代码级，鉴权即可） ----------------

-from backend import versions

-

-

-@app.get("/api/versions")

-def version_list(_=Depends(require_admin)):

-    """历史版本（代码回滚）仅管理员可用。"""

-    return {"ok": True, "items": versions.load_versions()}

-

-

-@app.post("/api/versions/{index}/restore")

-def version_restore(index: int, _=Depends(require_admin)):

-    try:

-        result = versions.restore_version(index)

-    except ValueError as e:

-        raise HTTPException(400, str(e))

-    return {"ok": True, **result}

-

-

 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):
```

**文件:** `frontend\app.js`
```diff
--- frontend\app.js
+++ frontend\app.js
@@ -137,8 +137,5 @@
 /* ---------- 加载数据 ---------- */

 async function loadAll() {

-  // 历史版本仅管理员可加载（普通用户后端 403，直接跳过避免报错）

-  const jobs = [loadStats(), loadCategories(), loadDocuments()];

-  if (auth.user?.role === "admin") jobs.push(loadVersions());

-  await Promise.all(jobs);

+  await Promise.all([loadStats(), loadCategories(), loadDocuments()]);

 }

 

@@ -656,176 +653,10 @@
 $("sidebarMask").addEventListener("click", closeSidebar);

 

-/* ---------- 历史版本 ---------- */

-const versionState = {

-  items: [],

-  open: false,

-  expanded: new Set(),

-  restoringIndex: null,

-};

-

-async function loadVersions() {

-  try {

-    const d = await api("/api/versions");

-    versionState.items = d.items || [];

-    const badge = $("versionBadge");

-    const n = versionState.items.length;

-    if (n > 0) {

-      badge.textContent = n;

-      badge.hidden = false;

-    } else {

-      badge.hidden = true;

-    }

-  } catch (e) { /* ignore */ }

-}

-

-function openVersionPanel() {

-  versionState.open = true;

-  state.lastFocused = document.activeElement;

-  $("versionMask").hidden = false;

-  renderVersions();

-  requestAnimationFrame(() => {

-    $("versionMask").classList.add("show");

-    $("versionPanel").classList.add("open");

-    $("versionPanel").setAttribute("aria-hidden", "false");

-    $("versionClose").focus();

-  });

-}

-

-function closeVersionPanel() {

-  $("versionPanel").classList.remove("open");

-  $("versionMask").classList.remove("show");

-  $("versionPanel").setAttribute("aria-hidden", "true");

-  versionState.open = false;

-  setTimeout(() => {

-    $("versionMask").hidden = true;

-    if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();

-    state.lastFocused = null;

-  }, 450);

-}

-

-function renderVersions() {

-  const body = $("versionBody");

-  if (!versionState.items.length) {

-    body.innerHTML = '<div class="version-empty">暂无历史版本<br>每次升级确认方案后会自动归档到这里</div>';

-    return;

-  }

-  body.innerHTML = versionState.items

-    .map(

-      (v, i) => `

-      <div class="version-item ${i === 0 ? "latest" : ""} ${versionState.expanded.has(v.index) ? "expanded" : ""}" data-vindex="${v.index}" style="animation-delay:${Math.min(i * 40, 300)}ms">

-        <div class="version-item-head" role="button" tabindex="0" aria-expanded="${versionState.expanded.has(v.index)}">

-          <span class="version-time">${esc(v.time)}${i === 0 ? " · 最新" : ""}</span>

-          <span class="version-desc">${esc(v.desc || "（无说明）")}</span>

-          <span class="version-files">${v.files.length} 个文件</span>

-          <span class="version-arrow">▼</span>

-        </div>

-        <div class="version-detail">

-          ${v.files.map((f) => {

-            const lines = f.diff ? f.diff.split("\n").length : 0;

-            return `

-            <div class="version-file-block">

-              <button class="version-file-head" role="button" aria-expanded="false">

-                <span class="version-file-name">${esc(f.path)}</span>

-                <span class="version-file-meta">${lines} 行</span>

-                <span class="version-file-arrow">▼</span>

-              </button>

-              <div class="version-diff">${renderDiff(f.diff)}</div>

-            </div>`;

-          }).join("")}

-          <div class="version-actions">

-            <button class="restore-btn" data-rindex="${v.index}">${i === 0 ? "撤销此升级" : "恢复到该版本"}</button>

-            <span class="restore-hint">恢复会覆盖当前代码，操作前自动留档</span>

-          </div>

-        </div>

-      </div>`

-    )

-    .join("");

-

-  body.querySelectorAll(".version-item-head").forEach((el) => {

-    const toggle = () => {

-      const vindex = Number(el.closest(".version-item").dataset.vindex);

-      if (versionState.expanded.has(vindex)) versionState.expanded.delete(vindex);

-      else versionState.expanded.add(vindex);

-      renderVersions();

-    };

-    el.addEventListener("click", toggle);

-    el.addEventListener("keydown", (e) => {

-      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }

-    });

-  });

-  body.querySelectorAll(".version-file-head").forEach((el) => {

-    const toggle = () => {

-      const block = el.closest(".version-file-block");

-      const open = block.classList.toggle("open");

-      el.setAttribute("aria-expanded", String(open));

-    };

-    el.addEventListener("click", toggle);

-  });

-  body.querySelectorAll(".restore-btn").forEach((el) =>

-    el.addEventListener("click", () => {

-      versionState.restoringIndex = Number(el.dataset.rindex);

-      const v = versionState.items.find((x) => x.index === versionState.restoringIndex);

-      const isLatest = versionState.items[0] && versionState.items[0].index === v.index;

-      $("restoreTitle").textContent = isLatest ? "撤销此升级？" : "恢复到该版本？";

-      $("restoreSub").textContent = `将${isLatest ? "撤销" : "恢复到"} ${esc(v.time)} 的「${esc(v.desc || "无说明")}」，涉及 ${v.files.length} 个文件，当前代码会被覆盖（恢复前会自动留档）。`;

-      $("restoreMask").hidden = false;

-      requestAnimationFrame(() => $("restoreCancel").focus());

-    })

-  );

-}

-

-/* diff 语法着色 */

-function renderDiff(diff) {

-  return esc(diff)

-    .split("\n")

-    .map((line) => {

-      if (line.startsWith("@@")) return `<span class="hl">${line}</span>`;

-      if (line.startsWith("-") && !line.startsWith("---")) return `<span class="dl">${line}</span>`;

-      if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="al">${line}</span>`;

-      if (line.startsWith(" ") || line.startsWith("-") || line.startsWith("+") || line.startsWith("\\")) return `<span class="cl">${line}</span>`;

-      return `<span class="cl">${line}</span>`;

-    })

-    .join("\n");

-}

-

-async function confirmRestore() {

-  if (versionState.restoringIndex === null) return;

-  const idx = versionState.restoringIndex;

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-  try {

-    const res = await api(`/api/versions/${idx}/restore`, { method: "POST" });

-    if (res.has_errors) {

-      toast("恢复完成，但有 " + res.results.filter((r) => !r.ok).length + " 个文件失败，请查看");

-    } else {

-      toast("已恢复到 " + res.target.time + " 的版本");

-    }

-    closeVersionPanel();

-    await loadAll();

-    setTimeout(() => toast("代码已切换，若界面异常请重启服务（双击 start.bat）"), 800);

-  } catch (e) {

-    toast("恢复失败: " + e.message);

-  }

-}

-

-$("versionEntry").addEventListener("click", openVersionPanel);

-$("versionClose").addEventListener("click", closeVersionPanel);

-$("versionMask").addEventListener("click", closeVersionPanel);

-$("restoreCancel").addEventListener("click", () => {

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-});

-$("restoreConfirm").addEventListener("click", confirmRestore);

-

 /* ---------- 全局键盘 ---------- */

 document.addEventListener("keydown", (e) => {

-  // Esc 优先关弹窗 → 版本面板 → 抽屉 → 侧边栏

+  // Esc 优先关弹窗 → 抽屉 → 侧边栏

   if (e.key === "Escape") {

     if (state.modalOpen) {

       $("modalCancel").click();

-    } else if (!versionState.restoringIndex && !$("restoreMask").hidden) {

-      $("restoreCancel").click();

-    } else if (versionState.open) {

-      closeVersionPanel();

     } else if (state.drawerOpen) {

       closeDrawer();

@@ -842,15 +673,7 @@
     return;

   }

-  // 抽屉/版本面板焦点陷阱

+  // 抽屉焦点陷阱

   if (e.key === "Tab") {

-    if (versionState.open) {

-      const focusables = $("versionPanel").querySelectorAll('button, [tabindex]:not([tabindex="-1"])');

-      if (focusables.length) {

-        const first = focusables[0];

-        const last = focusables[focusables.length - 1];

-        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }

-        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }

-      }

-    } else if (state.drawerOpen) {

+    if (state.drawerOpen) {

       const focusables = $("drawer").querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])');

       if (focusables.length) {

@@ -862,5 +685,5 @@
     }

   }

-  // 删除/恢复弹窗焦点陷阱

+  // 删除弹窗焦点陷阱

   if (e.key === "Tab" && state.modalOpen) {

     const btns = $("modalMask").querySelectorAll("button");

@@ -932,6 +755,4 @@
   renderUserMenu();

   $("menuAdmin").hidden = user.role !== "admin";

-  // 历史版本（代码回滚）仅管理员可用：非管理员隐藏入口

-  $("versionEntry").hidden = user.role !== "admin";

   loadAll().catch((e) => toast("加载失败: " + e.message));

 }
```

**文件:** `frontend\index.html`
```diff
--- frontend\index.html
+++ frontend\index.html
@@ -265,10 +265,4 @@
     </div>

 

-    <button class="version-entry" id="versionEntry" title="查看历史版本">

-      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>

-      <span>历史版本</span>

-      <span class="version-badge" id="versionBadge" hidden>0</span>

-    </button>

-

     <div class="sidebar-foot">

       <div class="drop-hint">把报告 / 笔记拖进窗口即可入库</div>

@@ -381,29 +375,4 @@
 </div>

 

-<!-- 历史版本面板 -->

-<div class="version-mask" id="versionMask" hidden></div>

-<aside class="version-panel" id="versionPanel" role="dialog" aria-modal="true" aria-hidden="true" aria-label="历史版本">

-  <div class="drawer-head">

-    <button class="icon-btn" id="versionClose" title="关闭（Esc）" aria-label="关闭历史版本">✕</button>

-    <div class="version-head-title">

-      <span>🕘 历史版本</span>

-      <span class="version-head-sub">最近 5 次代码升级，点击可查看改动 / 恢复</span>

-    </div>

-  </div>

-  <div class="version-body" id="versionBody"></div>

-</aside>

-

-<!-- 恢复确认弹窗 -->

-<div class="modal-mask" id="restoreMask" hidden>

-  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="restoreTitle" aria-describedby="restoreSub">

-    <div class="modal-title" id="restoreTitle">恢复到该版本？</div>

-    <div class="modal-sub" id="restoreSub"></div>

-    <div class="modal-actions">

-      <button class="modal-btn cancel" id="restoreCancel">取消</button>

-      <button class="modal-btn danger" id="restoreConfirm">恢复</button>

-    </div>

-  </div>

-</div>

-

 <!-- 修改密码弹窗 -->

 <div class="modal-mask" id="pwModalMask" hidden>
```

**文件:** `frontend\style.css`
```diff
--- frontend\style.css
+++ frontend\style.css
@@ -162,130 +162,4 @@
 .drop-hint { font-size: 11px; color: var(--label-2); text-align: center; line-height: 1.6; }

 

-/* 历史版本入口 */

-.version-entry {

-  display: flex; align-items: center; gap: 9px;

-  width: 100%; margin-bottom: 14px;

-  padding: 10px 12px; border-radius: 10px;

-  background: var(--card); color: var(--label);

-  font-size: 13.5px; font-weight: 600;

-  border: 1px solid var(--separator);

-  transition: all 0.2s var(--spring);

-}

-.version-entry:hover { background: var(--card-hover); border-color: var(--accent); transform: translateY(-1px); }

-.version-entry:active { transform: scale(0.98); }

-.version-badge {

-  margin-left: auto;

-  font-size: 11px; font-weight: 700; color: var(--accent-2);

-  background: rgba(100, 210, 255, 0.12);

-  border-radius: 999px; padding: 1px 8px;

-}

-

-/* 历史版本面板 */

-.version-mask {

-  position: fixed; inset: 0; z-index: 90;

-  background: rgba(0, 0, 0, 0.5);

-  backdrop-filter: blur(4px);

-  -webkit-backdrop-filter: blur(4px);

-  opacity: 0; transition: opacity 0.3s var(--ease);

-}

-.version-mask.show { opacity: 1; }

-

-.version-panel {

-  position: fixed; top: 0; right: 0; bottom: 0; z-index: 91;

-  width: min(620px, 94vw);

-  background: var(--surface);

-  border-left: 1px solid var(--separator);

-  box-shadow: -20px 0 60px rgba(0, 0, 0, 0.5);

-  transform: translateX(102%);

-  transition: transform 0.45s var(--spring);

-  display: flex; flex-direction: column;

-}

-.version-panel.open { transform: translateX(0); }

-.version-head-title { display: flex; flex-direction: column; gap: 2px; margin-left: 12px; }

-.version-head-title span:first-child { font-size: 15px; font-weight: 700; }

-.version-head-sub { font-size: 11px; color: var(--label-2); }

-

-.version-body { flex: 1; overflow-y: auto; padding: 18px 20px 50px; }

-.version-body::-webkit-scrollbar { width: 8px; }

-.version-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

-

-.version-empty { text-align: center; padding: 60px 20px; color: var(--label-2); font-size: 13px; line-height: 1.8; }

-

-.version-item {

-  background: var(--card);

-  border: 1px solid var(--separator);

-  border-radius: 14px;

-  margin-bottom: 12px;

-  overflow: hidden;

-  animation: fadeUp 0.35s var(--spring) backwards;

-}

-.version-item.latest { border-color: rgba(10, 132, 255, 0.4); }

-.version-item-head {

-  display: flex; align-items: center; gap: 10px;

-  padding: 13px 16px; cursor: pointer;

-  transition: background 0.2s;

-}

-.version-item-head:hover { background: var(--hover-soft); }

-.version-time { font-size: 12.5px; font-weight: 700; color: var(--accent-2); flex-shrink: 0; }

-.version-desc {

-  flex: 1; min-width: 0;

-  font-size: 13px; line-height: 1.5;

-  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;

-}

-.version-files { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-arrow { color: var(--label-2); font-size: 12px; transition: transform 0.3s var(--spring); flex-shrink: 0; }

-.version-item.expanded .version-arrow { transform: rotate(180deg); }

-

-.version-detail { display: none; padding: 0 16px 14px; }

-.version-item.expanded .version-detail { display: block; animation: fadeIn 0.25s var(--ease); }

-.version-file-block { margin-bottom: 10px; border: 1px solid var(--separator); border-radius: 10px; overflow: hidden; }

-.version-file-head {

-  display: flex; align-items: center; gap: 8px;

-  width: 100%; padding: 9px 12px;

-  background: var(--hover-soft);

-  cursor: pointer;

-  transition: background 0.2s;

-}

-.version-file-head:hover { background: var(--chip-bg); }

-.version-file-name {

-  flex: 1; min-width: 0;

-  font-size: 12px; font-weight: 600; color: var(--label);

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;

-}

-.version-file-meta { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-file-arrow { color: var(--label-2); font-size: 10px; flex-shrink: 0; transition: transform 0.3s var(--spring); }

-.version-file-block.open .version-file-arrow { transform: rotate(180deg); }

-.version-diff {

-  background: var(--code-bg);

-  padding: 12px 14px;

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  font-size: 12px; line-height: 1.7;

-  max-height: 320px; overflow: auto;

-  white-space: pre-wrap; word-break: break-all;

-  color: rgba(242, 242, 247, 0.75);

-  border-top: 1px solid var(--separator);

-  display: none;

-}

-.version-file-block.open .version-diff { display: block; animation: fadeIn 0.2s var(--ease); }

-.version-diff::-webkit-scrollbar { width: 6px; height: 6px; }

-.version-diff::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }

-.version-diff .dl { color: var(--danger); }

-.version-diff .al { color: var(--green); }

-.version-diff .cl { color: var(--label-2); }

-.version-diff .hl { color: var(--accent-2); }

-

-.version-actions { display: flex; gap: 10px; align-items: center; margin-top: 4px; }

-.restore-btn {

-  font-size: 12.5px; font-weight: 700; color: #fff;

-  background: var(--accent); border-radius: 10px;

-  padding: 8px 18px;

-  transition: all 0.2s var(--spring);

-}

-.restore-btn:hover { filter: brightness(1.12); transform: translateY(-1px); }

-.restore-btn:active { transform: scale(0.96); }

-.restore-btn:disabled { background: var(--separator); color: var(--label-2); cursor: not-allowed; transform: none; }

-.restore-hint { font-size: 11px; color: var(--label-2); }

-

 /* ============ 主区域 ============ */

 .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
```

---

### 2026-09-02 11:25:35 | 撤除程序内嵌的「历史版本」备份逻辑，改由独立增量备份工具接管：删除 backend/versions.py（CHANGELOG 解析+反向 patch 恢复）与 /api/versions、/api/versions/{index}/restore 两个接口；前端移除侧边栏入口、历史版本面板、恢复确认弹窗及全部 JS/CSS（含 loadAll、Esc/Tab 键盘处理、enterApp 中的残留分支）。备份职责完全移交 tools/backup.py（内容寻址增量备份，已存 20260902_112513 基线快照）

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -266,23 +266,4 @@
 

 

-# ---------------- 历史版本（代码级，鉴权即可） ----------------

-from backend import versions

-

-

-@app.get("/api/versions")

-def version_list(_=Depends(require_admin)):

-    """历史版本（代码回滚）仅管理员可用。"""

-    return {"ok": True, "items": versions.load_versions()}

-

-

-@app.post("/api/versions/{index}/restore")

-def version_restore(index: int, _=Depends(require_admin)):

-    try:

-        result = versions.restore_version(index)

-    except ValueError as e:

-        raise HTTPException(400, str(e))

-    return {"ok": True, **result}

-

-

 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):
```

**文件:** `frontend\app.js`
```diff
--- frontend\app.js
+++ frontend\app.js
@@ -137,8 +137,5 @@
 /* ---------- 加载数据 ---------- */

 async function loadAll() {

-  // 历史版本仅管理员可加载（普通用户后端 403，直接跳过避免报错）

-  const jobs = [loadStats(), loadCategories(), loadDocuments()];

-  if (auth.user?.role === "admin") jobs.push(loadVersions());

-  await Promise.all(jobs);

+  await Promise.all([loadStats(), loadCategories(), loadDocuments()]);

 }

 

@@ -656,176 +653,10 @@
 $("sidebarMask").addEventListener("click", closeSidebar);

 

-/* ---------- 历史版本 ---------- */

-const versionState = {

-  items: [],

-  open: false,

-  expanded: new Set(),

-  restoringIndex: null,

-};

-

-async function loadVersions() {

-  try {

-    const d = await api("/api/versions");

-    versionState.items = d.items || [];

-    const badge = $("versionBadge");

-    const n = versionState.items.length;

-    if (n > 0) {

-      badge.textContent = n;

-      badge.hidden = false;

-    } else {

-      badge.hidden = true;

-    }

-  } catch (e) { /* ignore */ }

-}

-

-function openVersionPanel() {

-  versionState.open = true;

-  state.lastFocused = document.activeElement;

-  $("versionMask").hidden = false;

-  renderVersions();

-  requestAnimationFrame(() => {

-    $("versionMask").classList.add("show");

-    $("versionPanel").classList.add("open");

-    $("versionPanel").setAttribute("aria-hidden", "false");

-    $("versionClose").focus();

-  });

-}

-

-function closeVersionPanel() {

-  $("versionPanel").classList.remove("open");

-  $("versionMask").classList.remove("show");

-  $("versionPanel").setAttribute("aria-hidden", "true");

-  versionState.open = false;

-  setTimeout(() => {

-    $("versionMask").hidden = true;

-    if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();

-    state.lastFocused = null;

-  }, 450);

-}

-

-function renderVersions() {

-  const body = $("versionBody");

-  if (!versionState.items.length) {

-    body.innerHTML = '<div class="version-empty">暂无历史版本<br>每次升级确认方案后会自动归档到这里</div>';

-    return;

-  }

-  body.innerHTML = versionState.items

-    .map(

-      (v, i) => `

-      <div class="version-item ${i === 0 ? "latest" : ""} ${versionState.expanded.has(v.index) ? "expanded" : ""}" data-vindex="${v.index}" style="animation-delay:${Math.min(i * 40, 300)}ms">

-        <div class="version-item-head" role="button" tabindex="0" aria-expanded="${versionState.expanded.has(v.index)}">

-          <span class="version-time">${esc(v.time)}${i === 0 ? " · 最新" : ""}</span>

-          <span class="version-desc">${esc(v.desc || "（无说明）")}</span>

-          <span class="version-files">${v.files.length} 个文件</span>

-          <span class="version-arrow">▼</span>

-        </div>

-        <div class="version-detail">

-          ${v.files.map((f) => {

-            const lines = f.diff ? f.diff.split("\n").length : 0;

-            return `

-            <div class="version-file-block">

-              <button class="version-file-head" role="button" aria-expanded="false">

-                <span class="version-file-name">${esc(f.path)}</span>

-                <span class="version-file-meta">${lines} 行</span>

-                <span class="version-file-arrow">▼</span>

-              </button>

-              <div class="version-diff">${renderDiff(f.diff)}</div>

-            </div>`;

-          }).join("")}

-          <div class="version-actions">

-            <button class="restore-btn" data-rindex="${v.index}">${i === 0 ? "撤销此升级" : "恢复到该版本"}</button>

-            <span class="restore-hint">恢复会覆盖当前代码，操作前自动留档</span>

-          </div>

-        </div>

-      </div>`

-    )

-    .join("");

-

-  body.querySelectorAll(".version-item-head").forEach((el) => {

-    const toggle = () => {

-      const vindex = Number(el.closest(".version-item").dataset.vindex);

-      if (versionState.expanded.has(vindex)) versionState.expanded.delete(vindex);

-      else versionState.expanded.add(vindex);

-      renderVersions();

-    };

-    el.addEventListener("click", toggle);

-    el.addEventListener("keydown", (e) => {

-      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }

-    });

-  });

-  body.querySelectorAll(".version-file-head").forEach((el) => {

-    const toggle = () => {

-      const block = el.closest(".version-file-block");

-      const open = block.classList.toggle("open");

-      el.setAttribute("aria-expanded", String(open));

-    };

-    el.addEventListener("click", toggle);

-  });

-  body.querySelectorAll(".restore-btn").forEach((el) =>

-    el.addEventListener("click", () => {

-      versionState.restoringIndex = Number(el.dataset.rindex);

-      const v = versionState.items.find((x) => x.index === versionState.restoringIndex);

-      const isLatest = versionState.items[0] && versionState.items[0].index === v.index;

-      $("restoreTitle").textContent = isLatest ? "撤销此升级？" : "恢复到该版本？";

-      $("restoreSub").textContent = `将${isLatest ? "撤销" : "恢复到"} ${esc(v.time)} 的「${esc(v.desc || "无说明")}」，涉及 ${v.files.length} 个文件，当前代码会被覆盖（恢复前会自动留档）。`;

-      $("restoreMask").hidden = false;

-      requestAnimationFrame(() => $("restoreCancel").focus());

-    })

-  );

-}

-

-/* diff 语法着色 */

-function renderDiff(diff) {

-  return esc(diff)

-    .split("\n")

-    .map((line) => {

-      if (line.startsWith("@@")) return `<span class="hl">${line}</span>`;

-      if (line.startsWith("-") && !line.startsWith("---")) return `<span class="dl">${line}</span>`;

-      if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="al">${line}</span>`;

-      if (line.startsWith(" ") || line.startsWith("-") || line.startsWith("+") || line.startsWith("\\")) return `<span class="cl">${line}</span>`;

-      return `<span class="cl">${line}</span>`;

-    })

-    .join("\n");

-}

-

-async function confirmRestore() {

-  if (versionState.restoringIndex === null) return;

-  const idx = versionState.restoringIndex;

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-  try {

-    const res = await api(`/api/versions/${idx}/restore`, { method: "POST" });

-    if (res.has_errors) {

-      toast("恢复完成，但有 " + res.results.filter((r) => !r.ok).length + " 个文件失败，请查看");

-    } else {

-      toast("已恢复到 " + res.target.time + " 的版本");

-    }

-    closeVersionPanel();

-    await loadAll();

-    setTimeout(() => toast("代码已切换，若界面异常请重启服务（双击 start.bat）"), 800);

-  } catch (e) {

-    toast("恢复失败: " + e.message);

-  }

-}

-

-$("versionEntry").addEventListener("click", openVersionPanel);

-$("versionClose").addEventListener("click", closeVersionPanel);

-$("versionMask").addEventListener("click", closeVersionPanel);

-$("restoreCancel").addEventListener("click", () => {

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-});

-$("restoreConfirm").addEventListener("click", confirmRestore);

-

 /* ---------- 全局键盘 ---------- */

 document.addEventListener("keydown", (e) => {

-  // Esc 优先关弹窗 → 版本面板 → 抽屉 → 侧边栏

+  // Esc 优先关弹窗 → 抽屉 → 侧边栏

   if (e.key === "Escape") {

     if (state.modalOpen) {

       $("modalCancel").click();

-    } else if (!versionState.restoringIndex && !$("restoreMask").hidden) {

-      $("restoreCancel").click();

-    } else if (versionState.open) {

-      closeVersionPanel();

     } else if (state.drawerOpen) {

       closeDrawer();

@@ -842,15 +673,7 @@
     return;

   }

-  // 抽屉/版本面板焦点陷阱

+  // 抽屉焦点陷阱

   if (e.key === "Tab") {

-    if (versionState.open) {

-      const focusables = $("versionPanel").querySelectorAll('button, [tabindex]:not([tabindex="-1"])');

-      if (focusables.length) {

-        const first = focusables[0];

-        const last = focusables[focusables.length - 1];

-        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }

-        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }

-      }

-    } else if (state.drawerOpen) {

+    if (state.drawerOpen) {

       const focusables = $("drawer").querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])');

       if (focusables.length) {

@@ -862,5 +685,5 @@
     }

   }

-  // 删除/恢复弹窗焦点陷阱

+  // 删除弹窗焦点陷阱

   if (e.key === "Tab" && state.modalOpen) {

     const btns = $("modalMask").querySelectorAll("button");

@@ -932,6 +755,4 @@
   renderUserMenu();

   $("menuAdmin").hidden = user.role !== "admin";

-  // 历史版本（代码回滚）仅管理员可用：非管理员隐藏入口

-  $("versionEntry").hidden = user.role !== "admin";

   loadAll().catch((e) => toast("加载失败: " + e.message));

 }
```

**文件:** `frontend\index.html`
```diff
--- frontend\index.html
+++ frontend\index.html
@@ -265,10 +265,4 @@
     </div>

 

-    <button class="version-entry" id="versionEntry" title="查看历史版本">

-      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>

-      <span>历史版本</span>

-      <span class="version-badge" id="versionBadge" hidden>0</span>

-    </button>

-

     <div class="sidebar-foot">

       <div class="drop-hint">把报告 / 笔记拖进窗口即可入库</div>

@@ -381,29 +375,4 @@
 </div>

 

-<!-- 历史版本面板 -->

-<div class="version-mask" id="versionMask" hidden></div>

-<aside class="version-panel" id="versionPanel" role="dialog" aria-modal="true" aria-hidden="true" aria-label="历史版本">

-  <div class="drawer-head">

-    <button class="icon-btn" id="versionClose" title="关闭（Esc）" aria-label="关闭历史版本">✕</button>

-    <div class="version-head-title">

-      <span>🕘 历史版本</span>

-      <span class="version-head-sub">最近 5 次代码升级，点击可查看改动 / 恢复</span>

-    </div>

-  </div>

-  <div class="version-body" id="versionBody"></div>

-</aside>

-

-<!-- 恢复确认弹窗 -->

-<div class="modal-mask" id="restoreMask" hidden>

-  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="restoreTitle" aria-describedby="restoreSub">

-    <div class="modal-title" id="restoreTitle">恢复到该版本？</div>

-    <div class="modal-sub" id="restoreSub"></div>

-    <div class="modal-actions">

-      <button class="modal-btn cancel" id="restoreCancel">取消</button>

-      <button class="modal-btn danger" id="restoreConfirm">恢复</button>

-    </div>

-  </div>

-</div>

-

 <!-- 修改密码弹窗 -->

 <div class="modal-mask" id="pwModalMask" hidden>
```

**文件:** `frontend\style.css`
```diff
--- frontend\style.css
+++ frontend\style.css
@@ -162,130 +162,4 @@
 .drop-hint { font-size: 11px; color: var(--label-2); text-align: center; line-height: 1.6; }

 

-/* 历史版本入口 */

-.version-entry {

-  display: flex; align-items: center; gap: 9px;

-  width: 100%; margin-bottom: 14px;

-  padding: 10px 12px; border-radius: 10px;

-  background: var(--card); color: var(--label);

-  font-size: 13.5px; font-weight: 600;

-  border: 1px solid var(--separator);

-  transition: all 0.2s var(--spring);

-}

-.version-entry:hover { background: var(--card-hover); border-color: var(--accent); transform: translateY(-1px); }

-.version-entry:active { transform: scale(0.98); }

-.version-badge {

-  margin-left: auto;

-  font-size: 11px; font-weight: 700; color: var(--accent-2);

-  background: rgba(100, 210, 255, 0.12);

-  border-radius: 999px; padding: 1px 8px;

-}

-

-/* 历史版本面板 */

-.version-mask {

-  position: fixed; inset: 0; z-index: 90;

-  background: rgba(0, 0, 0, 0.5);

-  backdrop-filter: blur(4px);

-  -webkit-backdrop-filter: blur(4px);

-  opacity: 0; transition: opacity 0.3s var(--ease);

-}

-.version-mask.show { opacity: 1; }

-

-.version-panel {

-  position: fixed; top: 0; right: 0; bottom: 0; z-index: 91;

-  width: min(620px, 94vw);

-  background: var(--surface);

-  border-left: 1px solid var(--separator);

-  box-shadow: -20px 0 60px rgba(0, 0, 0, 0.5);

-  transform: translateX(102%);

-  transition: transform 0.45s var(--spring);

-  display: flex; flex-direction: column;

-}

-.version-panel.open { transform: translateX(0); }

-.version-head-title { display: flex; flex-direction: column; gap: 2px; margin-left: 12px; }

-.version-head-title span:first-child { font-size: 15px; font-weight: 700; }

-.version-head-sub { font-size: 11px; color: var(--label-2); }

-

-.version-body { flex: 1; overflow-y: auto; padding: 18px 20px 50px; }

-.version-body::-webkit-scrollbar { width: 8px; }

-.version-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

-

-.version-empty { text-align: center; padding: 60px 20px; color: var(--label-2); font-size: 13px; line-height: 1.8; }

-

-.version-item {

-  background: var(--card);

-  border: 1px solid var(--separator);

-  border-radius: 14px;

-  margin-bottom: 12px;

-  overflow: hidden;

-  animation: fadeUp 0.35s var(--spring) backwards;

-}

-.version-item.latest { border-color: rgba(10, 132, 255, 0.4); }

-.version-item-head {

-  display: flex; align-items: center; gap: 10px;

-  padding: 13px 16px; cursor: pointer;

-  transition: background 0.2s;

-}

-.version-item-head:hover { background: var(--hover-soft); }

-.version-time { font-size: 12.5px; font-weight: 700; color: var(--accent-2); flex-shrink: 0; }

-.version-desc {

-  flex: 1; min-width: 0;

-  font-size: 13px; line-height: 1.5;

-  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;

-}

-.version-files { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-arrow { color: var(--label-2); font-size: 12px; transition: transform 0.3s var(--spring); flex-shrink: 0; }

-.version-item.expanded .version-arrow { transform: rotate(180deg); }

-

-.version-detail { display: none; padding: 0 16px 14px; }

-.version-item.expanded .version-detail { display: block; animation: fadeIn 0.25s var(--ease); }

-.version-file-block { margin-bottom: 10px; border: 1px solid var(--separator); border-radius: 10px; overflow: hidden; }

-.version-file-head {

-  display: flex; align-items: center; gap: 8px;

-  width: 100%; padding: 9px 12px;

-  background: var(--hover-soft);

-  cursor: pointer;

-  transition: background 0.2s;

-}

-.version-file-head:hover { background: var(--chip-bg); }

-.version-file-name {

-  flex: 1; min-width: 0;

-  font-size: 12px; font-weight: 600; color: var(--label);

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;

-}

-.version-file-meta { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-file-arrow { color: var(--label-2); font-size: 10px; flex-shrink: 0; transition: transform 0.3s var(--spring); }

-.version-file-block.open .version-file-arrow { transform: rotate(180deg); }

-.version-diff {

-  background: var(--code-bg);

-  padding: 12px 14px;

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  font-size: 12px; line-height: 1.7;

-  max-height: 320px; overflow: auto;

-  white-space: pre-wrap; word-break: break-all;

-  color: rgba(242, 242, 247, 0.75);

-  border-top: 1px solid var(--separator);

-  display: none;

-}

-.version-file-block.open .version-diff { display: block; animation: fadeIn 0.2s var(--ease); }

-.version-diff::-webkit-scrollbar { width: 6px; height: 6px; }

-.version-diff::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }

-.version-diff .dl { color: var(--danger); }

-.version-diff .al { color: var(--green); }

-.version-diff .cl { color: var(--label-2); }

-.version-diff .hl { color: var(--accent-2); }

-

-.version-actions { display: flex; gap: 10px; align-items: center; margin-top: 4px; }

-.restore-btn {

-  font-size: 12.5px; font-weight: 700; color: #fff;

-  background: var(--accent); border-radius: 10px;

-  padding: 8px 18px;

-  transition: all 0.2s var(--spring);

-}

-.restore-btn:hover { filter: brightness(1.12); transform: translateY(-1px); }

-.restore-btn:active { transform: scale(0.96); }

-.restore-btn:disabled { background: var(--separator); color: var(--label-2); cursor: not-allowed; transform: none; }

-.restore-hint { font-size: 11px; color: var(--label-2); }

-

 /* ============ 主区域 ============ */

 .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
```
