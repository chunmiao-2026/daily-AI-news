#!/usr/bin/env python3
"""
每日AI资讯推送
定时在 GitHub Actions 中运行 → 通过 ServerChan 推送到微信

数据来源：RSS 聚合（免费、无需 API Key）
推送渠道：ServerChan (https://sct.ftqq.com)
"""

import feedparser
import os
import sys
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html import unescape

# ── 配置 ──────────────────────────────────────────────────────
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
BJT = timezone(timedelta(hours=8))  # 北京时间

# 每类最多保留多少条
MAX_PER_CATEGORY = 8
# 每个 RSS 源最多取多少条
MAX_PER_SOURCE = 5
# RSS 请求超时（秒）
RSS_TIMEOUT = 15

# ── RSS 源 ────────────────────────────────────────────────────
RSS_SOURCES = {
    "ai_hot": [
        {
            "name": "Google News - AI",
            "url": "https://news.google.com/rss/search?q=AI+人工智能&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
        {
            "name": "Google News - Artificial Intelligence",
            "url": "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
        },
        {
            "name": "Ars Technica - AI",
            "url": "https://feeds.arstechnica.com/arstechnica/index",
        },
    ],
    "ai_psychology": [
        {
            "name": "Google News - AI Psychology",
            "url": "https://news.google.com/rss/search?q=AI+psychology+mental+health&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
        {
            "name": "Psychology Today - AI",
            "url": "https://www.psychologytoday.com/us/front/rss",
        },
    ],
    "beijing_events": [
        {
            "name": "Google News - 北京 AI 展会",
            "url": "https://news.google.com/rss/search?q=北京+AI+人工智能+展会+论坛+2026&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
        {
            "name": "Google News - 北京 辅助生育 展会",
            "url": "https://news.google.com/rss/search?q=北京+辅助生育+试管婴儿+展会+论坛&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
    ],
    "fertility": [
        {
            "name": "Google News - IVF Fertility",
            "url": "https://news.google.com/rss/search?q=IVF+fertility+assisted+reproduction&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
        {
            "name": "Google News - 辅助生育",
            "url": "https://news.google.com/rss/search?q=辅助生育+试管婴儿+备孕&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
        },
    ],
}

# 类别中文名
CATEGORY_NAMES = {
    "ai_hot": "🔥 AI 最新热点",
    "ai_psychology": "🧠 AI + 心理学领域",
    "beijing_events": "📅 北京 AI / 辅助生育展会论坛",
    "fertility": "📖 辅助生育学习资料",
}

# ── 工具函数 ──────────────────────────────────────────────────


def clean_html(text: str) -> str:
    """去除 HTML 标签，清理空白"""
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_date(entry) -> datetime | None:
    """尝试从 RSS entry 中解析发布时间"""
    for field in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, field, None)
        if tp:
            try:
                from time import mktime

                return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
            except Exception:
                pass
    return None


def fetch_feed(source: dict) -> list[dict]:
    """抓取单个 RSS 源，返回文章列表"""
    try:
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            print(f"  ⚠️  {source['name']}: 解析失败")
            return []
        articles = []
        for entry in feed.entries[:MAX_PER_SOURCE]:
            title = clean_html(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            # 截断过长的摘要
            if len(summary) > 200:
                summary = summary[:200] + "…"
            pub_date = parse_date(entry)
            articles.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "date": pub_date,
                    "source_name": source["name"],
                }
            )
        print(f"  ✅ {source['name']}: 获取 {len(articles)} 条")
        return articles
    except Exception as e:
        print(f"  ❌ {source['name']}: 错误 - {e}")
        return []


def format_article(art: dict) -> str:
    """将单篇文章格式化为 Markdown 列表项"""
    date_str = ""
    if art["date"]:
        date_str = art["date"].astimezone(BJT).strftime(" (%m-%d %H:%M)")
    summary = f" > {art['summary']}" if art["summary"] else ""
    return f"- [{art['title']}]({art['link']}){date_str}{summary}"


def send_serverchan(title: str, content: str) -> bool:
    """
    通过 ServerChan 推送消息到微信
    使用 urllib（避免编码问题）
    """
    if not SERVERCHAN_KEY:
        print("❌ 未设置 SERVERCHAN_KEY 环境变量")
        return False

    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = urllib.parse.urlencode(
        {"title": title, "desp": content}
    ).encode("utf-8")

    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")

    try:
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code") == 0:
            print(f"✅ 推送成功！")
            return True
        else:
            print(f"❌ 推送失败: {result}")
            return False
    except Exception as e:
        print(f"❌ 推送异常: {e}")
        return False


def build_markdown(all_articles: dict) -> str:
    """将分类后的文章组装成 Markdown 推送内容"""
    today = datetime.now(BJT).strftime("%Y-%m-%d %A")
    lines = [f"# 每日 AI 资讯推送", f"**{today}**\n"]

    for category, articles in all_articles.items():
        cat_name = CATEGORY_NAMES.get(category, category)
        lines.append(f"## {cat_name}\n")

        if not articles:
            lines.append("_暂无相关资讯_\n")
            continue

        # 按时间排序（最新的在前）
        sorted_arts = sorted(
            articles, key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True
        )

        for art in sorted_arts[:MAX_PER_CATEGORY]:
            lines.append(format_article(art))

        lines.append("")  # 空行分隔

    # 尾部信息
    lines.append("---")
    lines.append(f"🕐 推送时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M')}")
    lines.append("🤖 来源: RSS 聚合")

    return "\n".join(lines)


# ── 主流程 ────────────────────────────────────────────────────


def main():
    print("=" * 50)
    print("📡 每日 AI 资讯推送 - 开始抓取")
    print(f"⏰ {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    all_articles: dict[str, list] = {cat: [] for cat in RSS_SOURCES}

    # 1. 抓取所有 RSS 源
    for category, sources in RSS_SOURCES.items():
        print(f"\n📂 类别: {CATEGORY_NAMES.get(category, category)}")
        for source in sources:
            articles = fetch_feed(source)
            all_articles[category].extend(articles)

        # 去重（按标题去重）
        seen_titles = set()
        deduped = []
        for art in all_articles[category]:
            key = art["title"].lower().strip()
            if key and key not in seen_titles:
                seen_titles.add(key)
                deduped.append(art)
        all_articles[category] = deduped

        print(f"  → 共 {len(all_articles[category])} 条（去重后）")

    # 2. 统计总数
    total = sum(len(v) for v in all_articles.values())
    print(f"\n{'=' * 50}")
    print(f"📊 总计获取 {total} 条资讯")

    # 3. 组装 Markdown
    markdown = build_markdown(all_articles)
    print(f"\n📝 Markdown 内容已生成 ({len(markdown)} 字符)")

    # 4. 推送到微信
    print(f"\n📤 推送到微信...")
    title = f"每日 AI 资讯推送（{datetime.now(BJT).strftime('%Y-%m-%d')}）"
    success = send_serverchan(title, markdown)

    if not success:
        print("⚠️  推送失败，将内容输出到日志以便排查")
        # 把内容写入文件作为备份
        with open("push_result.md", "w", encoding="utf-8") as f:
            f.write(markdown)
        sys.exit(1)

    print(f"\n✅ 任务完成！")


if __name__ == "__main__":
    # 需要 feedparser，先尝试导入
    try:
        import json
    except ImportError:
        print("❌ Python 环境缺少 json 模块（不应该发生）")
        sys.exit(1)
    main()
