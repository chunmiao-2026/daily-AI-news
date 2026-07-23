# n8n 信息源升级指南

## 升级内容

将 n8n 自动采集管道中的 RSS 源从 Google News 替换为高质量专业源。

## 操作步骤

### 1. 打开 n8n

- 浏览器打开 http://localhost:5678
- 进入 **Workflows** 页面

### 2. 编辑 AI News RSS 工作流

找到并打开 **🤖 AI News RSS** 工作流：

1. 找到 **RSS Feed Read** 节点（或类似名称的 RSS 抓取节点）
2. 点击节点进入编辑
3. 在 URL 字段中，**替换或添加**以下高质量 RSS 源：

#### 替换 Hacker News RSS（可选）
```
原: https://hnrss.org/frontpage?points=100
替换为:
https://hnrss.org/frontpage?points=50  (降低门槛，获取更多内容)
```

#### 添加以下高质量源（用 +Add 添加新 RSS Feed Read 节点）

| 源名称 | RSS URL | 说明 |
|--------|---------|------|
| TechCrunch AI | `https://techcrunch.com/category/artificial-intelligence/feed/` | 硅谷一线AI媒体 |
| VentureBeat AI | `https://venturebeat.com/category/ai/feed/` | AI产业深度报道 |
| The Verge AI | `https://www.theverge.com/ai-artificial-intelligence/rss.xml` | 消费科技AI |
| MIT Tech Review AI | `https://www.technologyreview.com/topic/artificial-intelligence/rss/` | 学术+技术深度 |
| Wired AI | `https://www.wired.com/feed/tag/ai/latest/rss` | 科技文化AI |

### 3. 编辑 RSS → Obsidian Inbox 工作流

打开 **📡 RSS → Obsidian Inbox** 工作流：

同样添加上述 RSS 源。如果你想要更全面的采集（包含中文），额外添加：

| 源名称 | RSS URL | 说明 |
|--------|---------|------|
| ArXiv cs.AI | `https://rss.arxiv.org/rss/cs.AI` | 最新AI论文 |
| Hugging Face Papers | `https://huggingface.co/api/daily_papers` | 每日热门论文（JSON格式，需用HTTP Request节点） |
| 机器之心 | `https://jiqizhixin.com/rss` | 国内AI媒体 |

### 4. 编辑 GitHub Trending 工作流

打开 **🐙 GitHub Trending** 工作流：

这个工作流目前抓取 GitHub Trending RSS，可以保留不变，或者新增：

| 源名称 | RSS URL | 说明 |
|--------|---------|------|
| GitHub Trending AI | `https://github.com/trending?since=daily&spoken_language_code=` | 或使用现有RSS |

### 5. 保存

每个工作流编辑后点击右上角 **Save**。

---

## n8n 节点配置示例

### 添加 RSS Feed Read 节点

1. 从左侧节点面板拖入 **RSS Feed Read** 节点
2. 配置：
   - **URL**: 粘贴上面的 RSS 地址
   - **Send Query?**: 保持默认
   - 不要勾选「Use Proxy」
3. 点击右上角 **Execute Node** 测试是否能获取到内容
4. 如果返回绿色勾 ✓，说明配置成功

### 添加 HTTP Request 节点（用于 Hugging Face）

Hugging Face 返回 JSON 而非 RSS，需要用 HTTP Request 节点：

1. 拖入 **HTTP Request** 节点
2. 配置：
   - **Method**: GET
   - **URL**: `https://huggingface.co/api/daily_papers`
   - **Response Format**: JSON
3. 连接到一个 **Set** 或 **Code** 节点来转换格式，使其与 RSS 节点输出一致

---

## 验证方法

添加新源后，手动运行工作流：
1. 点击工作流右上角 **Execute Workflow** 按钮
2. 等待执行完成
3. 检查 Obsidian `_inbox` 目录是否生成了新的 Markdown 文件
4. 打开文件确认内容格式正确
