# Daily AI News 📡

每天 **北京时间 9:03** 自动推送 AI 资讯到微信。

## 📋 推送内容

| 类别 | 内容 |
|------|------|
| 🔥 AI 最新热点 | 人工智能行业最新动态 |
| 🧠 AI + 心理学 | AI 在心理/精神健康领域的进展 |
| 📅 北京展会论坛 | 北京及周边 AI / 辅助生育相关展会 |
| 📖 辅助生育学习 | 试管婴儿 / ART 最新资讯 |

## 🚀 部署方式

### 前提
1. 注册 [ServerChan](https://sct.ftqq.com) 获取 Key
2. 在 GitHub 上 fork 或 push 此仓库

### 设置 GitHub Secrets

仓库 → Settings → Secrets and variables → Actions → New repository secret

| Name | Value |
|------|-------|
| `SERVERCHAN_KEY` | 你的 ServerChan SendKey |

### 验证

- 进入 Actions 页面，手动触发 `🚀 每日 AI 资讯推送` workflow
- 等待运行完成，微信就会收到推送

之后每天 **9:03 AM** 会自动推送，无需任何手动操作。

## 🛠 本地测试

```bash
pip install -r requirements.txt
SERVERCHAN_KEY=你的key python push_news.py
```
