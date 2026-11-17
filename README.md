# 公众号福利自动检索器

按固定规则联网检索官方号发布的红包、抽奖答题、免费公教、消费券、品牌免单等福利，严格筛掉参与不了的，活动级去重后输出**一张按类目 / 地域分组的分类展示表**。只用联网搜索，不需要配账号。

## 一句话安装

需要 Node.js **22.20 或更高版本**。在本仓库页面复制这句，在终端里执行：

```bash
npx skills add <owner>/<repo>
```

例如：

```bash
npx skills add zhangsan/gzh-promotion-collector
```

安装时会让你选装到哪个 AI 工具（Claude Code / Codex / Cursor / OpenCode 等 75+ 种）以及装到当前项目还是全局（加 `-g` 全局可用）。装完重启对应工具即可调用。

只装这一个 skill、指定工具、全局安装：

```bash
npx skills add <owner>/<repo> --skill gzh-promotion-collector -a claude-code -g
```

> `<owner>/<repo>` 换成你实际的用户名和仓库名。GitHub 仓库可用；Gitee 仓库需确认 skills CLI 是否支持（官方主力支持 GitHub / GitLab）。

## 首次使用：填城市

打开 `data/config.json`，把城市改成你自己的（留空则默认遂宁）：

```json
{
  "home_city": "北京",
  "home_province": "北京",
  "nearby_cities": ["天津", "廊坊"]
}
```

也可以不配置文件，直接在对话里说城市——每一轮它都会问你这次用哪个城市，回「同上」就沿用上次。

## 怎么触发

对 AI 说下面任意一句：

- "刷新福利列表"
- "近期有哪些能薅的官方福利"
- "收集公众号红包/抽奖/福利类推文"
- "公众号活动汇总"

## 它会做什么

1. 按八条检索线联网搜索（现金红包排第一），不只搜答题。
2. 每条过五关：第一性原理（低成本 × 有收获）→ 7 条参与硬排除 → 官方来源红线 → 时间地域收口 → 去重。
3. 列出筛出的清单让你确认，确认后输出唯一一张分类展示表。

## 运行环境

- Python 3（仅标准库，无第三方依赖）
- 联网搜索能力（默认）
- 可选：配置 `REDFOX_API_KEY` 切换到红狐公众号数据源（更高质量）

## 目录结构

| 路径 | 说明 |
|---|---|
| `SKILL.md` | 规则总文件（收录判据、硬排除、检索线、输出格式） |
| `scripts/` | 12 个 Python 脚本（检索处理、去重、地域判定、导出、自测） |
| `references/` | 关键词分类表、数据源说明 |
| `data/config.json` | 你的城市配置（首次使用改这里） |
| `data/history.json` | 空白历史库，首次使用自动建档 |
| `data/raw_articles.json` | 原始候选数据模板 |

## 自测

改过脚本后先跑自测再用于生产：

```bash
python scripts/principle_check.py
python scripts/region_filter.py --self-test
python scripts/region_filter.py --self-test-city
python scripts/source_filter.py
python scripts/cleanup_old.py --self-test
```
