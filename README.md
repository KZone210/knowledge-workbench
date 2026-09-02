# 个人知识管理工作台

把收藏的报告、笔记丢进去，**自动提取文本 → 关键词 → 摘要 → 分类 → 打标签**，全部本地算法零成本。

## 功能

- 📥 **多格式入库**：PDF / Word / Markdown / TXT / HTML
- 🧠 **自动摘要**：TextRank 句子图算法提取关键句（纯本地）
- 🏷️ **自动关键词**：jieba TF-IDF 提取 8 个标签
- 📂 **自动分类**：内置 8 个类别关键词库（技术开发 / 人工智能 / 金融投资 / 营销运营 / 教育学习 / 健康养生 / 职场管理 / 生活随笔）
- 🔍 **全文搜索**：标题、标签、摘要、原文全局搜索；`/` 键快速聚焦
- 🖱️ **拖拽上传**：把文件拖进窗口即可入库，**阶段状态机**（上传中→分析中→完成/失败）+ 失败重试
- 🔗 **关键词跳转**：抽屉内点击关键词直接触发全局搜索
- 📊 **统计看板**：文档数、总词数、占用空间、热门标签云
- ♿ **WCAG 2.2 兼容**：键盘可达（Tab/Enter/Space）、焦点环可见、焦点陷阱、抽屉/弹窗焦点管理 + 焦点还原
- 📱 **移动端响应式**：<860px 自动变侧边栏抽屉 + 汉堡按钮
- 🌙 **iOS 深色风格**：原生毛玻璃、抽屉滑入、卡片动画、cubic-bezier spring 缓动

## 启动

### Windows（双击即可）

双击 `start.bat`，浏览器自动访问 `http://127.0.0.1:8787`

### 命令行

```bash
# 激活环境（首次需要）
"C:/Users/King/.workbuddy/binaries/python/envs/kb/Scripts/python.exe" -m pip install -r requirements.txt

# 启动
"C:/Users/King/.workbuddy/binaries/python/envs/kb/Scripts/python.exe" app.py
```

## 项目结构

```
个人知识管理工作平台/
├── app.py              FastAPI 入口（路由 + 静态文件）
├── backend/
│   ├── parser.py       PDF/Word/MD/TXT/HTML 文本提取
│   ├── nlp.py          jieba 分词 + TF-IDF + TextRank 摘要
│   ├── classify.py     关键词加权分类（8 个预设类别）
│   └── store.py        元数据 + 全文 SQLite
├── frontend/            # iOS 风格单页前端（原生 HTML/CSS/JS，零依赖）
├── data/               # 数据目录（启动时自动创建）
│   ├── documents/      入库文件副本（按时间戳命名）
│   └── knowledge.db    SQLite 数据库
├── start.bat           Windows 一键启动
├── start.sh            Git Bash 启动
└── requirements.txt
```

## API

| 方法   | 路径                          | 说明                                |
| ------ | ----------------------------- | ----------------------------------- |
| POST   | /api/upload                   | 批量上传文件（multipart, files[]）  |
| GET    | /api/documents                | 文档列表（支持 category/q/tag 筛选）|
| GET    | /api/documents/{id}           | 文档详情（含原文）                  |
| DELETE | /api/documents/{id}           | 删除文档（连同副本文件）            |
| GET    | /api/categories               | 分类计数                            |
| GET    | /api/stats                    | 统计信息                            |
| GET    | /api/files/{stored_name}      | 下载入库原文件                      |

## 分类算法

每个类别维护一个**关键词权重表**。文档入库时统计文本命中关键词的次数并加权打分：

```
score(cat) = Σ weight[word] * (1 + 0.5 * min(count, 4))
```

判定规则：
- 命中词 < 2 且总分 < 5 → 「未分类」
- 第一名与第二名差距 < 15% → 平局判「未分类」（避免多主题文档误判）
- 否则取分数最高类别

可在 `backend/classify.py` 的 `CATEGORIES` 字典里扩展关键词。

## 摘要算法

`backend/nlp.py` 中的 `summarize()`：
1. 按句末标点分句（过滤 < 15 字短句）
2. jieba 分词 + 停用词过滤构建词频向量
3. 句子间余弦相似度建图
4. PageRank 迭代 30 次
5. 取 top-3 句子，按原文顺序拼接

## 扩展建议

- 接 LLM API 升级总结：把 `process_file()` 里的 `nlp.summarize()` 替换为 API 调用，保留关键词与分类作为 prompt 输入
- 添加 `watchdog` 监听指定文件夹，新文件自动入库
- 接入向量数据库（FAISS）做语义检索

## 注意事项

- 所有数据本地存储，删除文件 = 不可恢复
- 默认端口 `8787`，可通过环境变量 `PORT` 修改
- 首次启动 jieba 加载词典可能略慢（< 2 秒）