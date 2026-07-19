# Daily AI News 📡

每天 **北京时间 9:03** 自动推送 AI 资讯到微信。
每 **周六 9:13** 推送行业监测周报。

## 📋 推送内容

| 时间 | 内容 | 说明 |
|------|------|------|
| 📰 每天 9:03 | AI 资讯 | AI热点、AI+心理、辅助生殖、北京展会 |
| 📡 每周六 9:13 | 行业周报 | 四大方向深度汇总 |

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

- 进入 Actions 页面，手动触发对应 workflow
- 等待运行完成，微信就会收到推送
