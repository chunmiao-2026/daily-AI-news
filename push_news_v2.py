#!/usr/bin/env python3
"""
每日 AI 资讯推送 v2 — AI 增强版
===============================

改进（2026-07-22）：
  1. 高质量 RSS 源 — 去掉 Google News，改用 TechCrunch / MIT Tech Review / ArXiv 等
  2. Claude AI 精选 — 自动筛选最重要的 5-10 条 + 中文摘要
  3. 主题分组 — 按大模型 / 学术前沿 / 工具产品 等分组，不是按来源罗列
  4. 中英双语覆盖 — 英文源 → Claude 翻译成中文输出

架构：
  cron-job.org → GitHub Actions → 抓取 RSS → Claude 精选摘要 → ServerChan → 微信

所需 Secrets（GitHub 仓库 Settings → Secrets and variables → Actions）：
  - SERVERCHAN_KEY    （已有）
  - ANTHROPIC_API_KEY （需要新增 — 到 https://console.anthropic.com/ 获取）
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

# 给 feedparser 底层的 socket 加默认超时，避免某个源挂起拖死整个 job
socket.setdefaulttimeout(RSS_TIMEOUT)

# ── 高质量 RSS 源（2026-07-22 全面更新）──────────────────────
# 替换了原来的 6 个 Google News 低质源
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
    "ai_psychology": [  # 🧠 AI + 心理健康
        {"name": "Google News - AI 心理健康", "url": "https://news.google.com/rss/search?q=AI+mental+health+psychology&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News - AI 心理(中)", "url": "https://news.google.com/rss/search?q=AI+心理健康+心理治疗&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
    ],
    "fertility": [      # 🔬 辅助生殖
        {"name": "Google News - IVF", "url": "https://news.google.com/rss/search?q=IVF+fertility+assisted+reproduction&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Google News - 辅助生殖(中)", "url": "https://news.google.com/rss/search?q=辅助生殖+试管婴儿+备孕&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
    ],
    "beijing_events": [ # 📅 北京展会 / 论坛
        {"name": "Google News - 北京AI展会", "url": "https://news.google.com/rss/search?q=北京+AI+人工智能+展会+论坛&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - 北京辅助生殖展会", "url": "https://news.google.com/rss/search?q=北京+辅助生殖+试管婴儿+展会+论坛&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
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
从提供的原始资讯列表中，**按下面四个方向分组**，每个方向筛选出最重要的条目，用中文写出精炼摘要：

1. 🤖 AI 行业与研究 — 大模型进展、AI 公司动态、学术前沿
2. 🧠 AI + 心理健康 — AI 在心理治疗 / 心理健康领域的应用与进展
3. 🔬 辅助生殖 — 辅助生育 / 试管婴儿行业的最新资讯
4. 📅 北京展会 / 论坛 — 北京即将举办的 AI / 心理健康 / 辅助生殖相关展会论坛

## 筛选原则
- **四个方向尽量都有内容**；若某方向当天确实无相关资讯，标注「今日无重要资讯」
- ✅ 保留：实质性技术突破、有影响力的公司动态、值得关注的论文、真实的展会 / 论坛信息
- ❌ 舍弃：纯广告软文、标题党、多源重复报道（只留最好一条）
- 🌟 优先：能提供「信息差」的内容
- ⚠️ 心理健康、辅助生殖、展会本身就是你要覆盖的方向，不要因为「不属于 AI 领域」而丢弃

## 输出格式要求
- 大标题用「📰 每日精选 · YYYY-MM-DD」
- 四个方向各用 emoji 小标题（如上）
- 每条用「n. **标题**」开头，链接放在标题上
- 摘要用 `> 一句话核心 + 一句话为什么重要` 的格式
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
    lines.append("📡 来源: RSS 聚合（v2 基础版）")
    return "\n".join(lines)


def build_ai_markdown(claude_summary: str) -> str:
    """AI 增强版 — 在 Claude 输出外层包装推送信息"""
    now = datetime.now(BJT)
    return (
        f"{claude_summary}\n\n"
        f"---\n"
        f"🤖 Claude 筛选 · 🕐 {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"📡 来源: TechCrunch / MIT TR / ArXiv / HF Papers 等"
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

    # 1. 抓取所有 RSS 源
    for category, sources in RSS_SOURCES.items():
        label = CATEGORY_LABELS.get(category, category)
        print(f"\n📂 {label}")
        for source in sources:
            if source.get("format") == "json":
                all_articles[category].extend(fetch_hf_papers())
            else:
                all_articles[category].extend(fetch_feed(source))

        # 去重
        all_articles[category] = deduplicate(all_articles[category])
        print(f"  → 共 {len(all_articles[category])} 条（去重后）")

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
