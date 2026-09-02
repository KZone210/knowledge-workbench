# -*- coding: utf-8 -*-
"""NLP 模块：jieba 分词、TF-IDF 关键词、TextRank 句子摘要（纯本地算法）。"""
import re
import math
from collections import Counter

import jieba
import jieba.analyse

# ---- 延迟初始化，避免导入即加载词典 ----
_loaded = False
STOPWORDS = set()


def _load():
    global _loaded
    if _loaded:
        return
    # jieba 默认词典路径随包安装，无需联网
    jieba.setLogLevel(60)
    _init_stopwords()
    _loaded = True


def _init_stopwords():
    words = """
的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好
自己 这 那 他 她 它 们 与 及 或 而 但 并 又 被 把 让 向 从 对 于 等 地 得 之 其
个 中 为 以 时 年 月 日 天 次 我们 你们 他们 这个 那个 这些 那些 什么 怎么 为什么
可以 不能 应该 已经 正在 通过 进行 使用 需要 一些 这样 那样 然后 因为 所以 如果
虽然 但是 而且 并且 还是 或者 只是 不过 由于 以及 关于 对于 其中 目前 现在 今天
昨天 明天 时候 时间 问题 情况 方面 部分 东西 事情 方法 方式 工作 生活 学习 自己
关注 重要 建议 主要 相关 一般 同时 另外 其实 就是 就是 不是 没有 可能 比较 特别
非常 越来越 一种 一个 很多 有些 各种 其他 以上 以下 首先 其次 最后 因此 从而
"""
    global STOPWORDS
    STOPWORDS = set(w for w in words.split() if w)


def _norm_text(text):
    text = re.sub(r"[\s\u3000]+", " ", text)
    return text.strip()


def tokenize(text, for_keywords=False):
    """分词并过滤停用词/单字/纯数字。"""
    _load()
    words = jieba.lcut(text)
    out = []
    for w in words:
        w = w.strip()
        if not w or len(w) < 2:
            continue
        if w in STOPWORDS:
            continue
        if w.isdigit() or re.fullmatch(r"[\W_]+", w):
            continue
        out.append(w)
    return out


def extract_keywords(text, top_n=8):
    """TF-IDF 关键词提取。"""
    _load()
    if len(text) < 20:
        words = tokenize(text)
        counts = Counter(words)
        return [w for w, _ in counts.most_common(top_n)]
    try:
        tags = jieba.analyse.extract_tags(text, topK=top_n * 2)
    except Exception:
        tags = []
    # 二次过滤（去停用词、单字、纯数字）
    result = [
        t for t in tags
        if t not in STOPWORDS and len(t) >= 2 and not re.fullmatch(r"\d+", t)
    ][:top_n]
    if not result:
        words = tokenize(text)
        counts = Counter(words)
        result = [w for w, _ in counts.most_common(top_n)]
    return result


# ---------------------------------------------------------------
# TextRank 句子摘要
# ---------------------------------------------------------------

def _split_sentences(text, max_len=200):
    """分句：按中英文标点/换行切分。"""
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    sentences = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 超长句按逗号再切
        if len(p) > max_len:
            sub = re.split(r"(?<=[，,：:])", p)
            cur = ""
            for s in sub:
                if len(cur) + len(s) > max_len and cur:
                    sentences.append(cur)
                    cur = s
                else:
                    cur += s
            if cur:
                sentences.append(cur)
        else:
            sentences.append(p)
    # 过滤太短的句子
    sentences = [s for s in sentences if len(s) >= 15]
    return sentences


def _sentence_vec(sentence, idf_map):
    """句子向量：词频 * IDF。"""
    vec = Counter()
    for w in tokenize(sentence):
        vec[w] += idf_map.get(w, 1.0)
    return vec


def _cosine(v1, v2):
    if not v1 or not v2:
        return 0.0
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[w] * v2[w] for w in common)
    n1 = math.sqrt(sum(v * v for v in v1.values()))
    n2 = math.sqrt(sum(v * v for v in v2.values()))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def summarize(text, top_n=3, min_sentences=2):
    """
    TextRank 摘要：句子建图 + PageRank 迭代，按原文顺序返回 top 句。
    文本过短时直接返回开头若干句。
    """
    text = _norm_text(text)
    if len(text) < 80:
        sentences = _split_sentences(text)
        if not sentences:
            return text[:100] or "（内容过短，无法生成摘要）"
        return "".join(sentences[:min_sentences])[:150]

    sentences = _split_sentences(text)
    if len(sentences) <= top_n:
        return "".join(sentences)

    # 全文档词频 → 近似 IDF
    doc_counter = Counter()
    for s in sentences:
        doc_counter.update(tokenize(s))
    total = sum(doc_counter.values())
    idf_map = {w: math.log((total + 1) / (c + 1)) + 1 for w, c in doc_counter.items()}

    vecs = [_sentence_vec(s, idf_map) for s in sentences]
    n = len(sentences)
    d = 0.85  # 阻尼系数
    scores = [1.0 / n] * n

    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = _cosine(vecs[i], vecs[j])
            if s > 0:
                sim[i][j] = sim[j][i] = s

    # 归一化出边权重 + PageRank 迭代
    out_w = [sum(sim[i]) or 1e-9 for i in range(n)]
    for _ in range(30):
        new_scores = [(1 - d) / n] * n
        for i in range(n):
            for j in range(n):
                if sim[i][j] > 0:
                    new_scores[j] += d * (sim[i][j] / out_w[i]) * scores[i]
        scores = new_scores

    # 取 top_n 句，按原文顺序拼接
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)[:top_n]
    ranked.sort()
    summary = "".join(sentences[i] for i in ranked)
    return summary[:400]


def word_count(text):
    return len(tokenize(text))
