#!/usr/bin/env python3
"""
行业监测日报 🌤️
每天 9:13 在 GitHub Actions 中运行 → 通过 ServerChan 推送到微信
覆盖 4 个方向：AI行业、AI+心理、辅助生殖、北京展会
双语呈现 + 48小时新鲜度过滤

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

MAX_PER_CATEGORY = 5
FRESH_HOURS = 48  # 只推送 48 小时内的内容

# ── RSS 源（4 大方向 × 双语） ──
CATEGORIES = {
    "ai_industry": {
        "icon": "🤖",
        "name_cn": "AI 行业动态",
        "name_en": "AI Industry",
        "sources": [
            {"label": "中文", "url": "https://news.google.com/rss/search?q=AI+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%A4%A7%E6%A8%A1%E5%9E%8B+%E6%99%BA%E8%83%BD%E4%BD%93&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "lang": "zh"},
            {"label": "English", "url": "https://news.google.com/rss/search?q=AI+LLM+agent+launch+latest&hl=en-US&gl=US&ceid=US:en", "lang": "en"},
            {"label": "English", "url": "https://news.google.com/rss/search?q=artificial+intelligence+startup+funding&hl=en-US&gl=US&ceid=US:en", "lang": "en"},
        ],
    },
    "ai_psychology": {
        "icon": "🧠",
        "name_cn": "AI + 心理健康",
        "name_en": "AI + Mental Health",
        "sources": [
            {"label": "中文", "url": "https://news.google.com/rss/search?q=AI+%E5%BF%83%E7%90%86%E5%81%A5%E5%BA%B7+%E5%BF%83%E7%90%86%E6%B2%BB%E7%96%97+%E6%95%B0%E5%AD%97%E7%96%97%E6%B3%95&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "lang": "zh"},
            {"label": "English", "url": "https://news.google.com/rss/search?q=AI+mental+health+therapy+counseling&hl=en-US&gl=US&ceid=US:en", "lang": "en"},
        ],
    },
    "fertility": {
        "icon": "🔬",
        "name_cn": "辅助生殖行业",
        "name_en": "Fertility & IVF",
        "sources": [
            {"label": "中文", "url": "https://news.google.com/rss/search?q=%E8%BE%85%E5%8A%A9%E7%94%9F%E6%AE%96+IVF+%E8%AF%95%E7%AE%A1%E5%A9%B4%E5%84%BF+%E7%94%9F%E8%82%B2%E5%8C%BB%E5%AD%A6&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "lang": "zh"},
            {"label": "English", "url": "https://news.google.com/rss/search?q=IVF+fertility+assisted+reproduction+latest&hl=en-US&gl=US&ceid=US:en", "lang": "en"},
        ],
    },
    "beijing_events": {
        "icon": "📅",
        "name_cn": "北京活动",
        "name_en": "Beijing Events",
        "sources": [
            {"label": "中文", "url": "https://news.google.com/rss/search?q=%E5%8C%97%E4%BA%AC+%E5%B1%95%E4%BC%9A+%E8%AE%BA%E5%9D%9B+AI+%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD+%E5%BF%83%E7%90%86%E5%81%A5%E5%BA%B7+%E8%BE%85%E5%8A%A9%E7%94%9F%E6%AE%96&hl=zh-CN&gl=CN&ceid=CN:zh-Hans", "lang": "zh"},
        ],
    },
}

CATEGORY_ORDER = ["ai_industry", "ai_psychology", "fertility", "beijing_events"]

# ── 语言检测 ──

def detect_lang(text: str) -> str:
    """粗略判断文本是否以英文为主"""
    if not text:
        return "zh"
    cn_chars = len(re.findall(r'[一-鿿]', text))
    en_chars = len(re.findall(r'[a-zA-Z]', text))
    return "zh" if cn_chars > en_chars else "en"

LANG_FLAG = {"zh": "🇨🇳", "en": "🇺🇸"}
LANG_LABEL = {"zh": "中文", "en": "English"}

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

def is_fresh(pub_date, hours=FRESH_HOURS):
    """判断文章是否在指定小时数内发布"""
    if pub_date is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return pub_date > cutoff

def fetch_feed(source: dict) -> list[dict]:
    try:
        feed = feedparser.parse(source["url"])
        if feed.bozo and not feed.entries:
            return []
        articles = []
        for entry in feed.entries:
            title = clean_html(entry.get("title", ""))
            if not title:
                continue
            link = entry.get("link", "")
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            if len(summary) > 120:
                summary = summary[:120] + "…"
            pub_date = parse_date(entry)

            # 关键过滤：只保留新鲜内容
            if not is_fresh(pub_date):
                continue

            # 如果 source 指定了语言，就用它；否则自动检测
            lang = source.get("lang", detect_lang(title))

            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "date": pub_date,
                "lang": lang,
                "source_label": source["label"],
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

def build_daily_report(all_articles: dict) -> str:
    """构建排版清晰的日报（双语分段）"""
    now = datetime.now(BJT)
    today_str = now.strftime("%Y-%m-%d")
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekday_cn[now.weekday()]

    # ── 标题区 ──
    lines = []
    lines.append(f"# 📡 行业日报")
    lines.append(f"**{today_str} {wd}**  |  🌐 中英双语  |  ⏱ 近48小时")
    lines.append("")
    lines.append("---")

    # ── 各板块 ──
    for cat_key in CATEGORY_ORDER:
        cat = CATEGORIES[cat_key]
        articles = all_articles.get(cat_key, [])

        if not articles:
            continue

        # 板块标题（双语）
        lines.append("")
        lines.append(f"## {cat['icon']} {cat['name_cn']} / {cat['name_en']}")
        lines.append("")

        # 按语言分组排序
        zh_articles = [a for a in articles if a.get("lang") == "zh"]
        en_articles = [a for a in articles if a.get("lang") == "en"]

        # 中文内容
        for art in zh_articles[:MAX_PER_CATEGORY]:
            d = art["date"].astimezone(BJT) if art["date"] else now
            time_str = d.strftime("%H:%M")
            summary = f" — {art['summary']}" if art["summary"] else ""
            lines.append(f"- 🇨🇳 [{art['title']}]({art['link']}) `{time_str}`{summary}")

        # 英文内容
        for art in en_articles[:MAX_PER_CATEGORY]:
            d = art["date"].astimezone(BJT) if art["date"] else now
            time_str = d.strftime("%H:%M")
            summary = f" — {art['summary']}" if art["summary"] else ""
            lines.append(f"- 🇺🇸 [{art['title']}]({art['link']}) `{time_str}`{summary}")

    # ── 尾部 ──
    lines.append("")
    lines.append("---")
    lines.append(f"🕐 {now.strftime('%Y-%m-%d %H:%M')}  ·  每日自动推送  ·  [GitHub Actions]")
    lines.append("💬 如需深度分析，在 Claude Code 中说「帮我查一下[方向]」")
    lines.append("")

    return "\n".join(lines)

# ── 主流程 ──

def main():
    print("=" * 50)
    print("📡 行业监测日报 - 开始抓取")
    print(f"⏰ {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    all_articles = {k: [] for k in CATEGORIES}

    for cat_key in CATEGORY_ORDER:
        cat = CATEGORIES[cat_key]
        print(f"\n📂 {cat['icon']} {cat['name_cn']} / {cat['name_en']}")

        for source in cat["sources"]:
            articles = fetch_feed(source)
            print(f"  {source['label']}: {len(articles)} 条新鲜资讯")
            all_articles[cat_key].extend(articles)

        # 去重（相同标题只留一条）
        seen = set()
        deduped = []
        for art in all_articles[cat_key]:
            key = art["title"].lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(art)
        all_articles[cat_key] = deduped

        cn_count = len([a for a in deduped if a.get("lang") == "zh"])
        en_count = len([a for a in deduped if a.get("lang") == "en"])
        print(f"  → 合计 {len(deduped)} 条（🇨🇳{cn_count} 🇺🇸{en_count}，已去重）")

    total = sum(len(v) for v in all_articles.values())
    print(f"\n📊 总计 {total} 条新鲜资讯（48小时内）")

    if total == 0:
        print("⚠️  没有新鲜资讯，跳过推送")
        return

    markdown = build_daily_report(all_articles)
    print(f"\n📝 日报已生成 ({len(markdown)} 字符)")

    print(f"\n📤 推送到微信...")
    title = f"📡 行业日报 {datetime.now(BJT).strftime('%m/%d %A')}"
    success = send_serverchan(title, markdown)

    if not success:
        with open("daily_report_output.md", "w", encoding="utf-8") as f:
            f.write(markdown)
        print("⚠️  推送失败，内容已保存到 daily_report_output.md")
        sys.exit(1)

    print(f"\n✅ 日报推送完成！")


if __name__ == "__main__":
    main()
