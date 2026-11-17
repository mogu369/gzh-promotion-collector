# 数据源说明

本 skill 支持 3 种数据源，按优先级自动选择。

## P0 · 红狐公众号爆款雷达 API

- **数据**：全网公众号，每日持续收录爆款文章
- **质量**：最高，标题/作者/阅读数/在看/点赞/评论全字段
- **配置**：需在 https://redfox.hk/settings/api-keys?source=workbuddy 申请 API Key

启用步骤：
1. 登录红狐 → 设置 → API Keys → 新建 Key
2. 把 Key 写入环境变量：
   - macOS/Linux：`export REDFOX_API_KEY="ak_xxxx..."`
   - Windows PowerShell：`$env:REDFOX_API_KEY = "ak_xxxx..."`
3. 触发本 skill 时会自动检测 key，存在则走 P0，不存在则继续走 P1

接口细节见 `scripts/fetch_redfox.py` 顶部注释。

## P1 · 联网搜索（默认）

- **数据**：通过 agent 的 WebSearch 工具聚合公开搜索结果
- **质量**：中等，受搜索引擎收录影响
- **配置**：无需任何 key，开箱即用

使用 query 模板（agent 内部循环调用）：
```
site:mp.weixin.qq.com "{关键词}" 官方 OR 品牌 OR 福利
公众号 "{关键词}" 活动 福利 抽奖
```

每个关键词取前 5 条，合并后传给 P2 流程（process_articles.py）。

## P2 · 手动导入

当 P0/P1 都拿不到数据时，可以让用户把文章列表粘贴为 JSON：

```json
[
  {"title": "...", "url": "...", "source": "...", "publish_date": "2026-09-01", "summary": "..."}
]
```

保存到 `data/raw_articles.json` 后跳到 process_articles.py 即可。

## 数据局限

- 微信生态对爬虫严格，第三方数据源都有滞后性
- 「官方/品牌发布」判定是启发式，存在误判可能
- 建议每次采集后人工抽查 5-10 条，避免把非目标内容入库