#!/usr/bin/env python3
"""
行业监测周报
每周六在 GitHub Actions 中运行 → 通过 ServerChan 推送到微信
覆盖 4 个方向：AI行业、AI+心理、辅助生殖、北京展会

数据来源：RSS 聚合（免费、无需 API Key）
推送渠道：ServerChan (https://sct.ftqq.com)
"""

import feedparser
import os
import sys
import re
import urllib.parse
import urllib.request
import json
from datetime import datetime, timezone, timedelta
from html import unescape

# ── 配置 ──
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
BJT = timezone(timedelta(hours=8))

MAX_PER_CATEGORY = 6
MAX_PER_SOURCE = 4
RSS_TIMEOUT = 15

# ── RSS 源（4 大方向） ──
RSS_SOURCES = {
    "ai_industry": [
        {"name": "Google News - AI 人工智能", "url": "https://news.google.com/rss/search?q=AI+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%A4%A7%E6%A8%A1%E5%9E%8B+%E6%99%BA%E8%83%BD%E4%BD%93&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - AI Global", "url": "https://news.google.com/rss/search?q=AI+artificial+intelligence+LLM+agent&hl=en-US&gl=US&ceid=US:en"},
        {"name": "Ars Technica - AI", "url": "https://feeds.arstechnica.com/arstechnica/index"},
        {"name": "TechCrunch - AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    ],
    "ai_psychology": [
        {"name": "Google News - AI 心理", "url": "https://news.google.com/rss/search?q=AI+%E5%BF%83%E7%90%86%E5%81%A5%E5%BA%B7+%E5%BF%83%E7%90%86%E6%B2%BB%E7%96%97+%E6%95%B0%E5%AD%97%E7%96%97%E6%B3%95&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - AI Mental Health", "url": "https://news.google.com/rss/search?q=AI+mental+health+therapy+counseling&hl=en-US&gl=US&ceid=US:en"},
        {"name": "ScienceDaily - Mind & Brain", "url": "https://www.sciencedaily.com/rss/mind_brain.xml"},
    ],
    "fertility": [
        {"name": "Google News - 辅助生殖", "url": "https://news.google.com/rss/search?q=%E8%BE%85%E5%8A%A9%E7%94%9F%E6%AE%96+IVF+%E8%AF%95%E7%AE%A1%E5%A9%B4%E5%84%BF+%E7%94%9F%E8%82%B2%E5%8C%BB%E5%AD%A6&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - Fertility IVF", "url": "https://news.google.com/rss/search?q=IVF+fertility+assisted+reproduction&hl=en-US&gl=US&ceid=US:en"},
    ],
    "beijing_events": [
        {"name": "Google News - 北京 展会 AI", "url": "https://news.google.com/rss/search?q=%E5%8C%97%E4%BA%AC+AI+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%B1%95%E4%BC%9A+%E8%AE%BA%E5%9D%9B+%E5%B3%B0%E4%BC%9A&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
        {"name": "Google News - 北京 心理 辅助生殖", "url": "https://news.google.com/rss/search?q=%E5%8C%97%E4%BA%AC+%E5%BF%83%E7%90%86%E5%81%A5%E5%BA%B7+%E8%BE%85%E5%8A%A9%E7%94%9F%E6%AE%96+%E5%8C%BB%E7%96%97+%E5%B1%95%E4%BC%9A+%E8%AE%BA%E5%9D%9B&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"},
    ],
}

CATEGORY_NAMES = {
    "ai_industry": "🤖 AI 行业动态",
    "ai_psychology": "🧠 AI + 心理健康",
    "fertility": "🔬 辅助生殖行业",
    "beijing_events": "📅 北京展会 / 论坛",
}

CATEGORY_ICONS = {
    "ai_industry": "🤖",
    "ai_psychology": "🧠",
    "fertility": "🔬",
    "beijing_events": "📅",
}

# ── 工具函数 ──

def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def parse_date(entry):
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
    try:
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            return []
        articles = []
        for entry in feed.entries[:MAX_PER_SOURCE]:
            title = clean_html(entry.get("title", ""))
            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            if len(summary) > 150:
                summary = summary[:150] + "…"
            pub_date = parse_date(entry)
            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "date": pub_date,
                "source_name": source["name"],
            })
        return articles
    except Exception:
        return []

def send_serverchan(title: str, content: str) -> bool:
    if not SERVERCHAN_KEY:
        print("❌ 未设置 SERVERCHAN_KEY")
        return False

    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
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

def build_weekly_report(all_articles: dict) -> str:
    now = datetime.now(BJT)
    week_end = now.strftime("%Y-%m-%d")
    # 往前推 7 天
    week_start = (now - timedelta(days=7)).strftime("%m/%d")

    lines = [f"# 📡 行业监测周报", f"**{week_start} — {week_end}**\n"]

    for category in ["ai_industry", "ai_psychology", "fertility", "beijing_events"]:
        articles = all_articles.get(category, [])
        cat_name = CATEGORY_NAMES.get(category, category)
        icon = CATEGORY_ICONS.get(category, "📌")
        lines.append(f"## {cat_name}\n")

        if not articles:
            lines.append("_本周暂无相关资讯_\n")
            continue

        sorted_arts = sorted(
            articles,
            key=lambda a: a["date"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        for art in sorted_arts[:MAX_PER_CATEGORY]:
          date_str = ""
          if art["date"]:
              d = art["date"].astimezone(BJT)
              date_str = f"（{d.strftime('%m-%d')}）"
          summary = f"\n  > {art['summary']}" if art["summary"] else ""
          lines.append(f"- [{art['title']}]({art['link']}){date_str}{summary}")

        lines.append("")

    lines.append("---")
    lines.append(f"🕐 推送时间: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M')}")
    lines.append("🤖 来源: RSS 聚合 | 由 GitHub Actions 自动触发")
    lines.append("📌 如需深入分析，可在 Claude Code 中说「帮我查一下[方向]」")

    return "\n".join(lines)

# ── 主流程 ──

def main():
    print("=" * 50)
    print("📡 行业监测周报 - 开始抓取")
    print(f"⏰ {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    all_articles = {cat: [] for cat in RSS_SOURCES}

    for category, sources in RSS_SOURCES.items():
        print(f"\n📂 {CATEGORY_NAMES.get(category, category)}")
        for source in sources:
            articles = fetch_feed(source)
            all_articles[category].extend(articles)

        # 去重
        seen = set()
        deduped = []
        for art in all_articles[category]:
            key = art["title"].lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(art)
        all_articles[category] = deduped
        print(f"  → 共 {len(deduped)} 条（去重后）")

    total = sum(len(v) for v in all_articles.values())
    print(f"\n📊 总计 {total} 条资讯")

    markdown = build_weekly_report(all_articles)
    print(f"\n📝 报告已生成 ({len(markdown)} 字符)")

    print(f"\n📤 推送到微信...")
    title = f"📡 行业周报 {datetime.now(BJT).strftime('%m/%d')}"
    success = send_serverchan(title, markdown)

    if not success:
        with open("weekly_report_output.md", "w", encoding="utf-8") as f:
            f.write(markdown)
        sys.exit(1)

    print(f"\n✅ 周报推送完成！")

if __name__ == "__main__":
    main()
