#!/usr/bin/env python3
"""
每日 AI 精选 · 本地 Claude Code 版
==================================

流程：
  1. 抓取高质量 RSS 源 → 写入 Obsidian _inbox/
  2. 从 _inbox/ 读取最近文章
  3. Claude Code 智能筛选 + 中文摘要
  4. ServerChan → 微信推送

不依赖 n8n，自包含运行。

用法：
  python daily_digest.py

Windows 计划任务：
  每天 9:10 自动运行（用 daily_digest.cmd）
"""

import feedparser
import os
import sys
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────
SERVERCHAN_KEY = os.environ.get(
    "SERVERCHAN_KEY", "SCT373272TyDxZsyNLueGsv2iWONzoDVv0"
)
OBSIDIAN_INBOX = Path(r"C:\Users\001\wiki\_inbox")
BJT = timezone(timedelta(hours=8))
FRESH_HOURS = 36  # 读取过去 36h 内的 Inbox 文件
MAX_ARTICLES = 50  # 最多喂给 Claude 的篇数

# ── 高质量 RSS 源 ────────────────────────────────────────────
RSS_SOURCES = [
    # AI 产业
    {"name": "TechCrunch AI",         "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "VentureBeat AI",        "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "The Verge AI",          "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "Ars Technica",          "url": "https://feeds.arstechnica.com/arstechnica/index"},
    # AI 研究
    {"name": "ArXiv cs.AI",           "url": "https://rss.arxiv.org/rss/cs.AI"},
    {"name": "MIT Tech Review",       "url": "https://www.technologyreview.com/feed/"},
    {"name": "Wired AI",              "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
    # 中文
    {"name": "钛媒体",                 "url": "https://www.tmtpost.com/rss"},
    {"name": "36氪",                   "url": "https://36kr.com/feed",
     "keywords": ["AI","人工智能","大模型","GPT","ChatGPT","Claude","Gemini","LLM",
                   "机器学习","深度学习","算法","芯片","算力","机器人","自动驾驶",
                   "AIGC","多模态","OpenAI","meta","微软","Anthropic","智能"]},
]

MAX_PER_SOURCE = 5  # 每个源最多取多少条
RSS_TIMEOUT = 20


# ── 工具函数 ──────────────────────────────────────────────────


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    from html import unescape
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def matches_keywords(text: str, keywords: list) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def parse_date(entry):
    from time import mktime
    for field in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, field, None)
        if tp:
            try:
                return datetime.fromtimestamp(mktime(tp), tz=timezone.utc)
            except Exception:
                pass
    return None


# ── RSS 抓取 ────────────────────────────────────────────────


def fetch_rss(source: dict) -> list[dict]:
    """抓取单个 RSS 源，返回文章列表"""
    name = source["name"]
    url = source["url"]
    keywords = source.get("keywords")

    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            reason = getattr(feed, "bozo_exception", "")
            print(f"  ⚠️  {name}: 解析失败 ({str(reason)[:60]})")
            return []

        articles = []
        for entry in feed.entries[:MAX_PER_SOURCE]:
            title = clean_html(entry.get("title", ""))
            if not title:
                continue

            link = entry.get("link", "")
            summary = clean_html(
                entry.get("summary", entry.get("description", ""))
            )

            if keywords and not matches_keywords(title + " " + summary, keywords):
                continue

            if len(summary) > 300:
                summary = summary[:300] + "…"

            pub_date = parse_date(entry)
            content = clean_html(entry.get("content", [{}])[0].get("value", "")
                                 if isinstance(entry.get("content"), list)
                                 else entry.get("summary", ""))

            articles.append({
                "title": title,
                "link": link,
                "summary": summary,
                "content": content[:500],
                "date": pub_date,
                "source_name": name,
            })

        icon = "✅" if articles else "📭"
        print(f"  {icon} {name}: {len(articles)} 条")
        return articles

    except Exception as e:
        print(f"  ❌ {name}: 异常 - {e}")
        return []


# ── 写入 Obsidian Inbox ────────────────────────────────────


def write_to_inbox(articles: list[dict]):
    """将文章写入 Obsidian _inbox/ 目录"""
    OBSIDIAN_INBOX.mkdir(parents=True, exist_ok=True)
    count = 0
    for art in articles:
        date_str = ""
        if art["date"]:
            date_str = art["date"].astimezone(BJT).strftime("%Y-%m-%d")
        else:
            date_str = datetime.now(BJT).strftime("%Y-%m-%d")

        slug = re.sub(r"[^a-z0-9]+", "-", art["title"].lower())[:60].strip("-")
        filename = f"{date_str}-{slug}.md"
        filepath = OBSIDIAN_INBOX / filename

        if filepath.exists():
            continue  # 不重复写入

        escaped_title = art["title"].replace('"', '\\"')
        md = f"""---
title: "{escaped_title}"
source: {art['source_name']}
url: {art['link']}
date: {date_str}
tags: [inbox, ai]
---

# {art['title']}

来源: [{art['link']}]({art['link']})

{art['content'] or art['summary']}
"""
        filepath.write_text(md, encoding="utf-8")
        count += 1

    print(f"  📝 写入 {count} 条新文章到 Inbox")


# ── 读取 Inbox ─────────────────────────────────────────────


def read_inbox() -> list[dict]:
    """读取 _inbox/ 中今天写入的文章（文件名前缀 YYYY-MM-DD）"""
    articles = []
    today_str = datetime.now(BJT).strftime("%Y-%m-%d")

    if not OBSIDIAN_INBOX.exists():
        return articles

    for f in sorted(OBSIDIAN_INBOX.glob("*.md"), reverse=True):
        # 只读取今天日期的文件
        if not f.name.startswith(today_str):
            continue

        content = f.read_text(encoding="utf-8")

        title = f.stem
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                front = parts[1]
                body = parts[2]
                for line in front.split("\n"):
                    if line.lower().startswith("title:"):
                        title = line.split(":", 1)[1].strip().strip('"').strip("'")
                        break

        summary_line = ""
        for line in body.strip().split("\n"):
            if line.strip() and not line.startswith("#") and not line.startswith("来源"):
                summary_line = line.strip()[:300]
                break

        articles.append({
            "file": f.name,
            "title": title,
            "time": datetime.fromtimestamp(f.stat().st_mtime, tz=BJT).strftime("%m-%d %H:%M"),
            "body": summary_line or body[:300],
        })

    print(f"  📂 {len(articles)} 篇（今天 {today_str}）")
    return articles[:MAX_ARTICLES]


def archive_old_inbox():
    """将超过 2 天的 Inbox 文件移入 _archive/ 子目录"""
    from datetime import timedelta
    archive_dir = OBSIDIAN_INBOX / "_archive"
    cutoff = datetime.now(BJT) - timedelta(days=2)
    moved = 0

    for f in OBSIDIAN_INBOX.glob("*.md"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=BJT)
        if mtime < cutoff:
            archive_dir.mkdir(parents=True, exist_ok=True)
            f.rename(archive_dir / f.name)
            moved += 1

    if moved:
        print(f"  🗂️  归档 {moved} 篇旧文章到 _archive/")


# ── Claude Code CLI ─────────────────────────────────────────


def call_claude_for_summary(articles_text: str) -> str | None:
    """用本地 Claude Code 生成 AI 精选摘要"""
    system_prompt = """你是每日 AI 资讯的资深编辑，擅长从大量信息中快速筛选出最有价值的资讯。

## 你的任务
从提供的原始资讯列表中，**筛选出 5-10 条最重要的**，按主题分组，用中文写出精炼摘要。

## 筛选原则
- ✅ 保留：有实质性技术突破的报道、有影响力的公司动态、值得关注的学术论文
- ❌ 舍弃：纯产品推广软文、标题党、多源重复报道（只保留最好的一条）、与 AI 无关的内容
- 🌟 优先：能为读者提供「信息差」的内容（大多数人还不知道但应该知道的）

## 输出格式要求
- 大标题用「📰 每日 AI 精选 · YYYY-MM-DD」
- 每组主题用小标题，格式如「📈 大模型新进展」
- 每条用「n. **标题**」开头，链接保留
- 摘要用「> 一句话核心 + 一句话为什么重要」的格式
- 末尾标注 [来源]
- **所有内容用中文输出**"""

    today = datetime.now(BJT).strftime("%Y-%m-%d %A")
    full_prompt = f"{system_prompt}\n\n---\n\n今天是 **{today}**。以下是各渠道采集到的 AI 资讯：\n\n{articles_text}\n\n---\n\n请筛选并输出每日精选。"

    try:
        print("  🤖 Claude Code 摘要中...")
        claude_path = r"C:\Users\001\AppData\Roaming\npm\claude.cmd"
        result = subprocess.run(
            [claude_path, "--print"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode == 0 and result.stdout.strip():
            output = result.stdout.strip()
            lines = output.split("\n")
            print(f"  ✅ {len(output)} 字符, {len(lines)} 行")
            return output
        else:
            print(f"  ⚠️  Claude Code 退出码 {result.returncode}")
            if result.stderr:
                print(f"     stderr: {result.stderr[:200]}")
            if result.stdout:
                print(f"     stdout: {result.stdout[:200]}")
            return None

    except subprocess.TimeoutExpired:
        print("  ❌ 超时 (>180s)")
        return None
    except FileNotFoundError:
        print(f"  ❌ 未找到 claude.cmd — Claude Code 未安装或路径不对")
        return None
    except Exception as e:
        print(f"  ❌ 异常: {e}")
        return None


# ── 推送 ─────────────────────────────────────────────────────


def send_serverchan(title: str, content: str) -> bool:
    if not SERVERCHAN_KEY:
        print("❌ 未配置 SERVERCHAN_KEY")
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
    print("📰 每日 AI 精选 · 本地版")
    print(f"⏰ {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # 1. RSS 抓取 → 写入 Inbox（归档用）
    print(f"\n📡 抓取 RSS（{len(RSS_SOURCES)} 个源）...")
    all_articles = []
    for source in RSS_SOURCES:
        all_articles.extend(fetch_rss(source))

    # 去重
    seen = set()
    deduped = []
    for art in all_articles:
        key = art["title"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(art)
    all_articles = deduped

    print(f"\n📊 共 {len(all_articles)} 条（去重后）")

    if all_articles:
        write_to_inbox(all_articles)
    else:
        print("  📭 RSS 源无新内容，尝试读取 Inbox 作为回退")

    # 归档超过 2 天的旧文件
    archive_old_inbox()

    # 2. 决定文章来源：优先用 RSS 刚抓取到的，回退到 Inbox
    if all_articles:
        feed_articles = all_articles
    else:
        # 回退：读 Inbox 中今天写入的文件
        print(f"\n📂 回退到读取 Obsidian Inbox...")
        inbox_articles = read_inbox()
        if not inbox_articles:
            print("\n⚠️  Inbox 也无内容，跳过推送")
            sys.exit(0)
        feed_articles = []
        for art in inbox_articles:
            feed_articles.append({
                "title": art["title"],
                "summary": art["body"],
                "link": "",
                "source_name": "Inbox",
                "date": None,
            })

    # 3. 格式化 → Claude 摘要
    today_str = datetime.now(BJT).strftime("%Y-%m-%d")
    article_lines = []
    for i, art in enumerate(feed_articles, 1):
        date_str = ""
        if art.get("date"):
            date_str = art["date"].astimezone(BJT).strftime("%m-%d %H:%M")
        source = art.get("source_name", "")
        link = art.get("link", "")
        summary = art.get("summary", art.get("content", ""))[:300]
        article_lines.append(
            f"#{i} [{source}] {art['title']}\n"
            f"   时间: {date_str}\n"
            f"   摘要: {summary}\n"
            f"   链接: {link}"
        )

    articles_text = "\n\n".join(article_lines)

    print(f"\n🤖 Claude Code 精选（{len(feed_articles)} 篇）...")
    claude_result = call_claude_for_summary(articles_text)

    if claude_result:
        markdown = f"{claude_result}\n\n---\n🤖 Claude Code 精选 · 🕐 {datetime.now(BJT).strftime('%Y-%m-%d %H:%M')}"
        print(f"   📝 AI 精选 · {len(markdown)} 字符")
    else:
        # 回退：基础格式
        lines = [f"# 每日 AI 资讯", f"**{today_str}**\n"]
        for art in feed_articles[:10]:
            s = art.get("summary", "")[:150]
            lines.append(f"- **{art['title']}**")
            if s:
                lines.append(f"  > {s}")
        lines.append(f"\n🕐 {datetime.now(BJT).strftime('%Y-%m-%d %H:%M')}")
        markdown = "\n".join(lines)
        print(f"   📝 基础回退 · {len(markdown)} 字符")

    # 4. 推送微信
    print(f"\n📤 推送到微信...")
    title = f"每日 AI 精选 · {today_str}"
    success = send_serverchan(title, markdown)

    if not success:
        Path("digest_backup.md").write_text(markdown, encoding="utf-8")
        print("⚠️  推送失败，已备份")
        sys.exit(1)

    print(f"\n{'=' * 55}")
    print("✅ 完成！🥷")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
