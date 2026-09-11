#!/usr/bin/env python3
"""
每日 AI 资讯推送 v2 — AI 增强版
===============================

改进（2026-09-11）：
  1. 新鲜度过滤 — 新闻类只收 72h 内，展会类放宽到 60 天
  2. 跨天去重 — history.json 记住近 7 天发过的标题/链接，彻底消除「连着 3 天相同内容」
  3. 数据源升级 — AI+心理 / 辅助生殖 改为「PubMed 学术 + Google News 新闻」双轨；北京展会扩充到三类全覆盖

架构：
  cron-job.org → GitHub Actions → 抓取 RSS/PubMed → DeepSeek 精选摘要 → ServerChan → 微信

所需 Secrets（GitHub 仓库 Settings → Secrets and variables → Actions）：
  - SERVERCHAN_KEY    （已有）
  - ANTHROPIC_API_KEY （DeepSeek Key，走 Anthropic 兼容接口）
"""

import feedparser
import os
import sys
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape

# Windows 控制台默认 GBK，打印 emoji 会报 UnicodeEncodeError；强制 UTF-8 输出（Linux/GH Actions 上无害）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 配置 ──────────────────────────────────────────────────────
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# DeepSeek 走 Anthropic 兼容接口（也可指向官方 api.anthropic.com）
# 用 `or` 兜底：即使环境变量被注入为空字符串，也回退到默认值
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or "https://api.deepseek.com/anthropic"
# 摘要模型：deepseek-chat（标准对话模型，直接出正文）；推理模型会先烧 token 在 thinking 上
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL") or "deepseek-chat"
BJT = timezone(timedelta(hours=8))

MAX_PER_SOURCE = 8       # 每个 RSS 源最多取多少条
MAX_PER_CATEGORY = 15    # 每个方向最多喂给 Claude 多少条（保证四方向均衡）
MAX_TOTAL_ARTICLES = 80  # 最多喂给 Claude 多少条
RSS_TIMEOUT = 20         # RSS 请求超时（秒）

# 各分类新鲜度窗口（小时）：新闻类 72h，展会类放宽到 60 天（会议提前数周发布）
FRESH_HOURS = {
    "ai_industry": 72,
    "ai_research": 72,
    "ai_chinese": 72,
    "ai_psychology": 72,
    "fertility": 72,
    "beijing_events": 24 * 60,
}

# 跨天去重：只对新闻类去重（展会本就该临近多天重复提醒）
DEDUP_CATEGORIES = {"ai_industry", "ai_research", "ai_chinese", "ai_psychology", "fertility"}
HISTORY_FILE = "history.json"
HISTORY_DAYS = 7

# 给 feedparser 底层的 socket 加默认超时，避免某个源挂起拖死整个 job
socket.setdefaulttimeout(RSS_TIMEOUT)

# ── 数据源 ──────────────────────────────────────────────────────
# 新闻层：Google News 搜索 RSS；学术层：PubMed E-utilities（format="pubmed"）
RSS_SOURCES = {
    "ai_industry": [    # 🏭 AI 产业动态 — 一线科技媒体
        {"name": "TechCrunch AI",         "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"name": "VentureBeat AI",        "url": "https://venturebeat.com/category/ai/feed/"},
        {"name": "The Verge AI",          "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
        {"name": "Ars Technica",          "url": "https://feeds.arstechnica.com/arstechnica/index"},
    ],
    "ai_research": [    # 🔬 AI 前沿研究 — 学术 + 深度技术
        {"name": "ArXiv cs.AI",          "url": "https://rss.arxiv.org/rss/cs.AI"},
        {"name": "MIT Tech Review",      "url": "https://www.technologyreview.com/feed/"},
        {"name": "Wired AI",             "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
        {"name": "Hugging Face Papers",  "url": "https://huggingface.co/api/daily_papers", "format": "json"},
    ],
    "ai_chinese": [     # 📰 中文 AI 资讯 — 国内一线
        {"name": "钛媒体", "url": "https://www.tmtpost.com/rss"},
        {"name": "36氪",     "url": "https://36kr.com/feed",
         "keywords": ["AI","人工智能","大模型","GPT","ChatGPT","Claude","Gemini","LLM",
                       "机器学习","深度学习","算法","芯片","算力","机器人","自动驾驶",
                       "AIGC","多模态","OpenAI","meta","微軟","Anthropic","智能"]},
    ],
    "ai_psychology": [  # 🧠 AI + 心理健康（学术 PubMed + 新闻 Google News）
        {"name": "PubMed - AI心理健康", "format": "pubmed",
         "query": "artificial intelligence AND (mental health OR psychotherapy OR psychology)"},
        {"name": "PubMed - 数字心理干预", "format": "pubmed",
         "query": "digital mental health OR chatbot therapy OR large language model psychiatry"},
        {"name": "Google News - AI心理(中)", "url": "https://news.google.com/rss/search?q=AI+心理健康+心理治疗&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - AI mental health(en)", "url": "https://news.google.com/rss/search?q=AI+mental+health+therapy&hl=en-US&gl=US&ceid=US:en"},
    ],
    "fertility": [      # 🔬 辅助生殖（学术 PubMed + 新闻 Google News）
        {"name": "PubMed - 辅助生殖技术", "format": "pubmed",
         "query": "assisted reproduction OR in vitro fertilization OR IVF OR intracytoplasmic sperm injection"},
        {"name": "PubMed - 生殖医学前沿", "format": "pubmed",
         "query": "ovarian stimulation OR embryo transfer OR preimplantation genetic testing"},
        {"name": "Google News - 辅助生殖(中)", "url": "https://news.google.com/rss/search?q=辅助生殖+试管婴儿+备孕&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - IVF(en)", "url": "https://news.google.com/rss/search?q=IVF+fertility+assisted+reproduction&hl=en-US&gl=US&ceid=US:en"},
    ],
    "beijing_events": [ # 📅 北京展会 / 论坛（AI + 心理 + 生殖医学 三类，Google News 多关键词）
        {"name": "北京-AI会议(中)", "url": "https://news.google.com/rss/search?q=北京+人工智能+会议+论坛+峰会+展会&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "北京-AI大会(中)", "url": "https://news.google.com/rss/search?q=北京+人工智能+大会+博览会+展览&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "北京-心理会议(中)", "url": "https://news.google.com/rss/search?q=北京+心理学+学术会议+论坛&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "北京-心理健康会议(中)", "url": "https://news.google.com/rss/search?q=北京+心理健康+会议+论坛+研讨会&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "北京-生殖医学会议(中)", "url": "https://news.google.com/rss/search?q=北京+生殖医学+辅助生殖+试管婴儿+会议+年会&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "北京-生殖医学学术(中)", "url": "https://news.google.com/rss/search?q=北京+生殖医学+学术会议+论坛&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Beijing-AI-conf(en)", "url": "https://news.google.com/rss/search?q=Beijing+AI+conference+summit+expo&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Beijing-psych-IVF-conf(en)", "url": "https://news.google.com/rss/search?q=Beijing+psychology+conference+IVF+fertility+reproductive&hl=en-US&gl=US&ceid=US:en"},
    ],
}

CATEGORY_LABELS = {
    "ai_industry":   "🏭 AI 产业动态",
    "ai_research":   "🔬 AI 前沿研究",
    "ai_chinese":    "📰 中文 AI 资讯",
    "ai_psychology": "🧠 AI + 心理健康",
    "fertility":     "🔬 辅助生殖",
    "beijing_events": "📅 北京展会 / 论坛",
}

# ── 工具函数 ──────────────────────────────────────────────────


def clean_html(text: str) -> str:
    """去除 HTML 标签、反转义 HTML 实体"""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_date(entry) -> datetime | None:
    """从 RSS entry 中解析发布时间"""
    from time import mktime
    for field in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, field, None)
        if tp:
            try:
                return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
            except Exception:
                pass
    return None


def matches_keywords(text: str, keywords: list) -> bool:
    """检查文本是否包含任一关键词（大小写不敏感）"""
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def is_fresh(article: dict, category: str) -> bool:
    """按分类新鲜度窗口过滤：日期缺失或超窗即丢弃"""
    hours = FRESH_HOURS.get(category, 72)
    d = article.get("date")
    if d is None:
        return False
    return d > datetime.now(timezone.utc) - timedelta(hours=hours)


def normalize_url(link: str) -> str:
    """规范化 URL 用于跨天去重：去查询串（Google News 的 oc=/utm_ 跟踪参数每天变）、统一小写"""
    link = (link or "").strip().lower()
    link = link.split("#")[0]
    link = link.split("?")[0]
    return link


# ── 跨天去重（history.json）────────────────────────────────────


def load_history() -> dict:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"titles": {}, "urls": {}}


def save_history(history: dict) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        print(f"  💾 去重历史已保存（{len(history.get('titles', {}))} 标题 / {len(history.get('urls', {}))} 链接）")
    except Exception as e:
        print(f"  ⚠️  保存去重历史失败（不影响推送）: {e}")


def prune_history(history: dict, days: int = HISTORY_DAYS) -> None:
    """清理超过 N 天的记录，避免文件无限膨胀"""
    cutoff = (datetime.now(BJT) - timedelta(days=days)).strftime("%Y-%m-%d")
    for key in ("titles", "urls"):
        bucket = history.get(key)
        if isinstance(bucket, dict):
            for k in [k for k, v in bucket.items() if v < cutoff]:
                del bucket[k]


def filter_seen(articles: list, history: dict) -> list:
    """过滤掉近 7 天已推送过的标题/链接"""
    seen_titles = set(history.get("titles", {}).keys())
    seen_urls = set(history.get("urls", {}).keys())
    result = []
    for art in articles:
        t = art["title"].lower().strip()
        u = normalize_url(art.get("link", ""))
        if t and t in seen_titles:
            continue
        if u and u in seen_urls:
            continue
        result.append(art)
    return result


def record_seen(articles: list, history: dict, today: str) -> None:
    """把当天抓取到的新闻类文章记入历史"""
    titles = history.setdefault("titles", {})
    urls = history.setdefault("urls", {})
    for art in articles:
        t = art["title"].lower().strip()
        u = normalize_url(art.get("link", ""))
        if t:
            titles[t] = today
        if u:
            urls[u] = today


# ── 抓取 RSS ──────────────────────────────────────────────────


def fetch_feed(source: dict) -> list[dict]:
    """抓取单个 RSS 源"""
    name = source["name"]
    url = source["url"]
    keywords = source.get("keywords")

    try:
        feed = feedparser.parse(url)

        # bozo 表示解析异常，但如果还有条目就继续用
        if feed.bozo and not feed.entries:
            reason = getattr(feed, "bozo_exception", "未知错误")
            print(f"  ⚠️  {name}: 解析失败 ({reason})")
            return []
        if feed.bozo:
            reason = getattr(feed, "bozo_exception", "")
            print(f"  ⚠️  {name}: 部分异常 ({reason})，使用已有条目")

        articles = []
        for entry in feed.entries[:MAX_PER_SOURCE]:
            title = clean_html(entry.get("title", ""))
            if not title:
                continue

            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", entry.get("description", "")))

            # 关键词过滤（用于 36kr 等全站 feed）
            if keywords and not matches_keywords(title + " " + summary, keywords):
                continue

            if len(summary) > 250:
                summary = summary[:250] + "…"

            pub_date = parse_date(entry)
            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "date": pub_date,
                "source_name": name,
            })

        icon = "✅" if articles else "📭"
        print(f"  {icon} {name}: {len(articles)} 条")
        return articles

    except Exception as e:
        print(f"  ❌ {name}: 请求异常 - {e}")
        return []


def fetch_hf_papers() -> list[dict]:
    """抓取 Hugging Face Daily Papers（JSON API，非 RSS）"""
    try:
        url = "https://huggingface.co/api/daily_papers"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=RSS_TIMEOUT)
        data = json.loads(resp.read().decode("utf-8"))

        articles = []
        for paper in data[:MAX_PER_SOURCE]:
            title = clean_html(paper.get("title", ""))
            if not title:
                continue

            paper_id = paper.get("paper_id", "")
            arxiv_url = f"https://arxiv.org/abs/{paper_id}" if paper_id else paper.get("url", "")
            summary = clean_html(paper.get("summary", ""))
            if len(summary) > 250:
                summary = summary[:250] + "…"

            # 解析发布时间
            pub_date = None
            pub_str = paper.get("publishedAt") or paper.get("published_at")
            if pub_str:
                try:
                    pub_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except Exception:
                    pass

            articles.append({
                "title": title,
                "link": arxiv_url,
                "summary": summary,
                "date": pub_date,
                "source_name": "Hugging Face Daily Papers",
            })

        print(f"  ✅ Hugging Face Papers: {len(articles)} 条")
        return articles

    except Exception as e:
        print(f"  ❌ Hugging Face Papers: 请求异常 - {e}")
        return []


def fetch_pubmed(query: str, name: str) -> list[dict]:
    """通过 PubMed E-utilities 抓取最新论文（学术层，免费稳定）"""
    if not query:
        return []

    try:
        # esearch：按 pub_date 倒序取最新论文 ID
        esearch_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&term={urllib.parse.quote(query)}"
            f"&retmax={MAX_PER_SOURCE}&sort=pub_date&retmode=json"
        )
        req = urllib.request.Request(esearch_url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=RSS_TIMEOUT).read().decode("utf-8"))
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            print(f"  📭 {name}: 0 条")
            return []

        # esummary：批量取标题/期刊/日期/作者
        esummary_url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&id={','.join(ids)}&retmode=json"
        )
        req2 = urllib.request.Request(esummary_url, headers={"User-Agent": "Mozilla/5.0"})
        sdata = json.loads(urllib.request.urlopen(req2, timeout=RSS_TIMEOUT).read().decode("utf-8"))
        result = sdata.get("result", {})

        articles = []
        for pmid in ids:
            doc = result.get(pmid, {})
            if not isinstance(doc, dict):
                continue
            title = clean_html(doc.get("title", ""))
            if not title:
                continue

            # 解析日期：优先 epubdate（电子上线日期），回退 pubdate（出刊日期可能含未来日期）
            pub_date = None
            date_str = doc.get("epubdate", "") or doc.get("pubdate", "")
            for fmt in ("%Y %b %d", "%Y %b", "%Y"):
                try:
                    pub_date = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
                    break
                except Exception:
                    continue

            journal = doc.get("fulljournalname", "") or doc.get("source", "")
            authors = ", ".join(a.get("name", "") for a in doc.get("authors", [])[:3])
            meta = " ".join(x for x in [authors, journal] if x)
            summary = meta if meta else "学术论文"

            articles.append({
                "title": title,
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "summary": summary,
                "date": pub_date,
                "source_name": name,
            })

        print(f"  ✅ {name}: {len(articles)} 条")
        return articles

    except Exception as e:
        print(f"  ❌ {name}: 请求异常 - {e}")
        return []


def deduplicate(articles: list[dict]) -> list[dict]:
    """按标题去重（大小写不敏感）"""
    seen = set()
    result = []
    for art in articles:
        key = art["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(art)
    return result


# ── Claude API 集成 ───────────────────────────────────────────


def call_claude_for_summary(all_articles: dict) -> str | None:
    """
    调用 Claude API 进行智能筛选 + 主题分类 + 中文摘要
    返回格式化后的 Markdown 字符串，失败时返回 None
    """
    if not ANTHROPIC_API_KEY:
        print("  ℹ️  未配置 ANTHROPIC_API_KEY，跳过 AI 摘要")
        return None

    # 组装文章文本（每个分类先均衡保留，再全局排序截断，避免某方向挤占其他方向）
    flat_items = []
    for cat, arts in all_articles.items():
        label = CATEGORY_LABELS.get(cat, cat)
        # 分类内按时间排序，最多取 MAX_PER_CATEGORY 条
        cat_sorted = sorted(
            arts,
            key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:MAX_PER_CATEGORY]
        for art in cat_sorted:
            flat_items.append((label, art))

    # 按时间排序（最新的在前），超出上限的截断
    flat_items.sort(
        key=lambda x: x[1]["date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if len(flat_items) > MAX_TOTAL_ARTICLES:
        print(f"  📊 截断至 {MAX_TOTAL_ARTICLES} 条（原始 {len(flat_items)} 条）")
        flat_items = flat_items[:MAX_TOTAL_ARTICLES]

    # 格式化为文本
    article_lines = []
    for i, (cat_label, art) in enumerate(flat_items, 1):
        date_str = ""
        if art["date"]:
            date_str = art["date"].astimezone(BJT).strftime("%m-%d %H:%M")
        article_lines.append(
            f"{i}. [{cat_label}] [{art['source_name']}] {art['title']}\n"
            f"   时间: {date_str}\n"
            f"   摘要: {art['summary']}\n"
            f"   链接: {art['link']}"
        )

    articles_text = "\n\n".join(article_lines)
    today_str = datetime.now(BJT).strftime("%Y-%m-%d")

    system_prompt = """你是每日资讯的资深编辑，负责从大量信息中筛选出最有价值的资讯，覆盖四个方向。

## 你的任务
从提供的原始资讯列表中，按下面四个方向分组筛选，用中文写精炼摘要：

1. 🤖 AI 行业与研究 — 大模型进展、AI 公司动态、学术前沿
2. 🧠 AI + 心理健康 — AI 在心理治疗/心理健康的应用；分「新闻动态」和「学术/专业」两个子块
3. 🔬 辅助生殖 — 辅助生育/试管婴儿行业动态；分「新闻动态」和「学术/专业」两个子块
4. 📅 北京展会 / 论坛 — 北京即将举办的 AI / 心理健康 / 辅助生殖相关会议、展会、论坛

## 来源识别
原始资讯的 [来源] 里，含「PubMed」的是学术论文（归入「学术/专业」子块），含「News」的是新闻（归入「新闻动态」子块）。

## 筛选原则
- 四个方向尽量都有内容；某方向当天确实无相关资讯，标注「今日无重要资讯」
- ✅ 保留：实质性技术突破、有影响力的公司动态、值得关注的论文、真实的会议信息
- ❌ 舍弃：纯广告软文、标题党、多源重复报道（只留最好一条）、过期/已结束的活动
- 🌟 优先：能提供「信息差」的内容

## 北京展会方向特别要求
- 只保留**真实、有明确时间/地点/主办方**的会议/展会/论坛；无法确认时间地点的宁可不列
- 优先**未来**即将举办的活动，标注会议日期；已结束的一律舍弃
- 覆盖 AI、心理健康、辅助生殖三个主题，缺哪类就注明
- 同类会议合并成一条呈现，避免罗列同质信息

## 输出格式
- 大标题用「📰 每日精选 · YYYY-MM-DD」
- 四个方向各用 emoji 小标题
- 每条用「n. **标题**」开头，链接放在标题上
- 摘要用 `> 一句话核心 + 一句话为什么重要` 格式
- 学术论文标注期刊名/年份
- 末尾用 `[来源]` 标注
- 组与组之间用空行分隔
- **所有内容用中文输出**，英文源的内容翻译为中文概括"""

    user_prompt = f"""今天是 **{today_str}**。以下是今天采集到的 {len(flat_items)} 条资讯（覆盖 AI / 心理健康 / 辅助生殖 / 北京展会四个方向）：

{articles_text}

请按上述要求筛选并输出每日精选。"""

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 3000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    api_url = ANTHROPIC_BASE_URL.rstrip("/") + "/v1/messages"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        print(f"  🤖 调用 Claude API（{len(flat_items)} 条文章 → {MAX_TOTAL_ARTICLES} 上限）...")
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read().decode("utf-8"))

        if "content" in result:
            # 跳过 reasoning 模型的 thinking 块，只取正文 text 块
            content = "".join(
                block.get("text", "") for block in result["content"]
                if block.get("type") == "text"
            )
            if not content:
                print("  ⚠️  模型仅返回 thinking 无正文（可能是推理模型），回退基础格式")
                return None
            usage = result.get("usage", {})
            print(f"  ✅ Claude API 成功 "
                  f"(输入: {usage.get('input_tokens', '?')} tok, "
                  f"输出: {usage.get('output_tokens', '?')} tok)")
            return content.strip()
        else:
            print(f"  ❌ Claude API 返回格式异常: {json.dumps(result, ensure_ascii=False)[:300]}")
            return None

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ❌ Claude API HTTP {e.code}: {body[:500]}")
        return None
    except Exception as e:
        print(f"  ❌ Claude API 请求异常: {e}")
        return None


# ── 格式化与推送 ──────────────────────────────────────────────


def build_basic_markdown(all_articles: dict) -> str:
    """基础格式化 — Claude API 不可用时的回退方案"""
    today = datetime.now(BJT).strftime("%Y-%m-%d %A")
    lines = [f"# 每日 AI 资讯", f"**{today}**\n"]

    for category, articles in all_articles.items():
        label = CATEGORY_LABELS.get(category, category)
        lines.append(f"## {label}\n")

        if not articles:
            lines.append("_暂无相关资讯_\n")
            continue

        sorted_arts = sorted(
            articles,
            key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for art in sorted_arts[:8]:
            date_str = (
                art["date"].astimezone(BJT).strftime(" (%m-%d %H:%M)")
                if art["date"] else ""
            )
            summary = f" > {art['summary']}" if art["summary"] else ""
            lines.append(f"- [{art['title']}]({art['link']}){date_str}{summary}")

        lines.append("")

    lines.append("---")
    lines.append(f"🕐 {datetime.now(BJT).strftime('%Y-%m-%d %H:%M')}")
    lines.append("📡 来源: RSS + PubMed 聚合（v2 基础版）")
    return "\n".join(lines)


def build_ai_markdown(claude_summary: str) -> str:
    """AI 增强版 — 在 Claude 输出外层包装推送信息"""
    now = datetime.now(BJT)
    return (
        f"{claude_summary}\n\n"
        f"---\n"
        f"🤖 Claude 筛选 · 🕐 {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"📡 来源: TechCrunch / ArXiv / PubMed / Google News 等"
    )


def send_serverchan(title: str, content: str) -> bool:
    """通过 ServerChan 推送到微信"""
    if not SERVERCHAN_KEY:
        print("❌ 未设置 SERVERCHAN_KEY")
        return False

    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data)
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0:
            print("✅ 推送成功！")
            return True
        else:
            print(f"❌ 推送失败: {json.dumps(result, ensure_ascii=False)}")
            return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False


# ── 主流程 ────────────────────────────────────────────────────


def main():
    print("=" * 55)
    print("📰 每日 AI 资讯推送 v2 · AI 增强版")
    print(f"⏰ {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    all_articles: dict[str, list[dict]] = {cat: [] for cat in RSS_SOURCES}

    # 0. 加载跨天去重历史
    history = load_history()
    prune_history(history)
    today_str = datetime.now(BJT).strftime("%Y-%m-%d")
    print(f"📜 去重历史已加载（近 {HISTORY_DAYS} 天）")

    # 1. 抓取所有源
    for category, sources in RSS_SOURCES.items():
        label = CATEGORY_LABELS.get(category, category)
        print(f"\n📂 {label}")
        for source in sources:
            fmt = source.get("format")
            if fmt == "json":
                all_articles[category].extend(fetch_hf_papers())
            elif fmt == "pubmed":
                all_articles[category].extend(fetch_pubmed(source.get("query", ""), source["name"]))
            else:
                all_articles[category].extend(fetch_feed(source))

        # 去重（标题）
        all_articles[category] = deduplicate(all_articles[category])
        # 新鲜度过滤
        all_articles[category] = [a for a in all_articles[category] if is_fresh(a, category)]
        # 跨天去重（仅新闻类）
        if category in DEDUP_CATEGORIES:
            before = len(all_articles[category])
            all_articles[category] = filter_seen(all_articles[category], history)
            dropped = before - len(all_articles[category])
            if dropped:
                print(f"  🚫 跨天去重剔除 {dropped} 条（近 {HISTORY_DAYS} 天已发过）")
        print(f"  → 共 {len(all_articles[category])} 条（去重+新鲜度过滤后）")

    # 2. 统计
    total = sum(len(v) for v in all_articles.values())
    print(f"\n{'=' * 55}")
    print(f"📊 总计获取 {total} 条资讯")

    if total == 0:
        print("❌ 未获取到任何资讯，跳过推送")
        sys.exit(1)

    # 3. 生成内容：AI 精选 或 基础回退
    if ANTHROPIC_API_KEY:
        print(f"\n🤖 调用 Claude 进行 AI 精选...")
        claude_result = call_claude_for_summary(all_articles)
        if claude_result:
            markdown = build_ai_markdown(claude_result)
            print("✅ AI 精选完成")
        else:
            print("⚠️  AI 精选失败，回退到基础格式")
            markdown = build_basic_markdown(all_articles)
    else:
        print("\nℹ️  未配置 ANTHROPIC_API_KEY，使用基础格式")
        markdown = build_basic_markdown(all_articles)

    print(f"\n📝 内容已生成（{len(markdown)} 字符）")

    # 4. 推送到微信
    print(f"\n📤 推送到微信...")
    title = f"每日 AI 精选 · {datetime.now(BJT).strftime('%Y-%m-%d')}"
    success = send_serverchan(title, markdown)

    # 5. 记录当天新闻类文章到去重历史（无论推送是否成功，抓取到的都记，避免下次重复）
    for category in DEDUP_CATEGORIES:
        record_seen(all_articles[category], history, today_str)
    save_history(history)

    if not success:
        print("⚠️  推送失败，内容已保存到 push_result.md")
        backup_path = "push_result_v2.md"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"   → 备份文件: {backup_path}")
        sys.exit(1)

    print(f"\n{'=' * 55}")
    print("✅ 任务完成！明天见 👋")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
