"""
公众号链接导出工具。

输入：process_articles.py 处理后的 articles.json（含 articles + stats）
输出：
  - urls-by-region.md    按地域分组的链接清单（Markdown，可直接复制）
  - urls-by-category.md 按主题分组的链接清单
  - urls.csv             CSV 表格（可在 Excel/腾讯文档打开）
  - wechat-mp-paste.md   公众号编辑器专用排版（带标题/分隔/二维码占位）

公众号编辑器对 Markdown 部分语法支持有限（不支持外链预览，但保留链接文字即可点击）。
用法：
  python scripts/export_urls.py \
      --input data/articles.json \
      --output-dir output/YYYY-MM-DD \
      --history-file data/history.json   # 可选，用于标注「是否重推」
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_defs import CATEGORY_LABELS as _CAT_DEFS, CAT_ORDER
# 复用 process_articles 的去重键与低区分度 URL 判定，避免两套逻辑分叉：
# 历史库的 key 由 dedupe_key() 生成，重推判定必须用同一个函数，
# 否则搜索落地页（?docid=xxx）这类 URL 会因「去掉 query 后同名」而被误判为已推荐。
from process_articles import dedupe_key as _dedupe_key, LOW_SIGNAL_URL as _LOW_SIGNAL_URL
# 地域判定复用 region_filter 同一套逻辑（含参与可行性硬校验）——
# 不可参与的活动（exclude）一律不进入任何交付物（2026-10-15 用户要求：
# 「不要推荐无法参与的活动」）。
from region_filter import classify_region as _classify_region


def tag_region(articles: list) -> list:
    """给每篇文章打上 _region_tag/_region，返回原列表（就地修改）。"""
    for a in articles:
        r = _classify_region(a)
        a["_region_tag"] = r
        a["_region"] = REGION_LABELS.get(r, "")
    return articles


def split_participable(articles: list) -> tuple:
    """按参与可行性拆分：(可参与列表, 不可参与列表)。调用前需先 tag_region。"""
    participable = [a for a in articles if a.get("_region_tag") != "exclude"]
    excluded = [a for a in articles if a.get("_region_tag") == "exclude"]
    return participable, excluded


def load_first_seen_map(history_path: Path | None) -> dict:
    """从历史库构建 url/title -> first_seen 的映射。

    用于在导出时标注每条是否已推荐过（🔄 标记 + 首次推荐日期）。
    history.json 结构: {schema_version, retention_days, last_updated,
                        items: {"url::https://..." | "title::xxx": {...}}}
    """
    if not history_path or not history_path.exists():
        return {}
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = data.get("items", {})
    out = {}
    for key, val in items.items():
        if isinstance(val, dict) and val.get("first_seen"):
            out[key] = val["first_seen"]
    return out


# 模块级：重推标注所需（由 main() 赋值，repush_label 自动读取）
SEEN_MAP: dict = {}
TODAY_STR: str = ""


def repush_label(article: dict, today_str: str = "", seen_map: dict | None = None) -> str:
    """返回 (标题后缀标记, 是否重推)。

    规则：
      - 优先用 url 指纹查历史库，其次 title 指纹
      - 无历史记录，或 first_seen == 今天 → 全新，不加标记
      - first_seen 早于今天 → 重推，返回 " 🔄 曾于 M月D日 推荐"
    """
    seen_map = SEEN_MAP if seen_map is None else seen_map
    today_str = TODAY_STR or today_str
    url = article.get("url", "")

    # 与 process_articles.dedupe_key() 完全一致：低区分度 URL 退化到标题指纹
    first_seen = seen_map.get(_dedupe_key(article))
    # 兜底：历史库里还留着旧版逻辑写入的「原样 url」键（未去 query）
    if not first_seen and url and not _LOW_SIGNAL_URL.search(url):
        first_seen = seen_map.get(f"url::{url}")

    if not first_seen:
        return "", False
    if str(first_seen)[:10] >= today_str:
        return "", False

    try:
        _y, _m, _d = str(first_seen)[:10].split("-")
        return f" 🔄 曾于{int(_m)}月{int(_d)}日推荐", True
    except Exception:
        return " 🔄 曾推荐", True

# 主题中文标签（引用共享定义 category_defs.py，唯一真相源）
CATEGORY_LABELS = {k: v[0] for k, v in _CAT_DEFS.items()}

# 地域中文标签（本地桶标签运行时按 config.json 城市覆盖）
REGION_LABELS = {
    "suining":      "📍 适合本地及周边",
    "unrestricted": "🌐 不限参与人员（全国/线上）",
    "exclude":      "🚫 仅限其他远地",
}
_LOCAL_LABEL = None  # 由 load_articles 从 region-classified.json 的 local_label 读取


def _set_local_label(label: str):
    global _LOCAL_LABEL, REGION_LABELS
    if label:
        _LOCAL_LABEL = label
        REGION_LABELS["suining"] = f"📍 适合{label}"


def is_valid_url(url: str) -> bool:
    """判定是否为可用 URL（公众号编辑器对 mp.weixin.qq.com 友好，但其他域需注意）。"""
    if not url:
        return False
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False
    # 排除一些明显的占位/无效 URL
    bad_patterns = ["example.com", "placeholder", "{url}", "{{url}}"]
    return not any(p in url.lower() for p in bad_patterns)


def load_articles(input_path: Path) -> list:
    """读取处理后的文章列表。

    兼容两种入参：
      ① process_articles.py 产物 articles.json → {"articles": [...]}
      ② region_filter.py 产物 region-classified.json
         → {"suining_region": [...], "unrestricted": [...], "excluded_other_regions": [...]}
    兼容②时把地域直接写成 _region_bucket，并在已被硬校验剔除的条目标 exclude。
    """
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if "articles" in data:
        return data.get("articles", [])
    # 从地域分桶产物读取用户城市标签，替换「本地及周边」
    _set_local_label(data.get("local_label") or "")

    merged = []
    for key, bucket in (
        ("suining_region", "suining"),
        ("unrestricted", "unrestricted"),
        ("excluded_other_regions", "exclude"),
    ):
        for a in data.get(key, []) or []:
            a = dict(a)
            a["_region_bucket"] = bucket
            merged.append(a)
    return merged


def filter_valid(articles: list) -> list:
    """仅保留有可用 URL 的文章。"""
    return [a for a in articles if is_valid_url(a.get("url", ""))]


def write_urls_by_region(articles: list, output_path: Path, today: str, excluded_count: int = 0):
    """按地域分组导出 Markdown 链接清单（只含可参与的活动）。"""
    # 按地域分组
    by_region = {}
    for a in articles:
        region = a.get("_region_tag") or "unknown"
        by_region.setdefault(region, []).append(a)

    # 按地域内再按主题分组
    cat_order = CAT_ORDER

    lines = [
        f"# 公众号福利推文·链接清单（{today}）",
        "",
        f"> 本清单由 gzh-promotion-collector 自动生成，共收录 **{len(articles)}** 篇有可用链接的推文。",
        f"> 每个链接均可直接复制到公众号编辑器使用。",
    ]
    if excluded_count:
        lines.append(
            f"> 🚫 另有 **{excluded_count}** 篇因区域/身份限制无法参与（需定位外地/限外地身份），已自动剔除、未列出。"
        )
    lines += [
        "",
        "---",
        "",
        "## 快速复制入口",
        "",
    ]

    # 总览 - 标题+链接清单
    lines.append("### 📋 全部推文（标题+链接）")
    lines.append("")
    lines.append("> 直接复制下面的「标题」+ 换行 +「https://...」即可贴到公众号。")
    lines.append("")
    for i, a in enumerate(articles, 1):
        cat_emoji = CATEGORY_LABELS.get(a.get("category"), "📌").split()[0]
        is_off = " ⭐" if a.get("is_official") else ""
        repush, _ = repush_label(a)
        lines.append(f"{i}. {cat_emoji} {a.get('title', '')}{is_off}{repush}")
        lines.append(f"   {a.get('url', '')}")
        if a.get("summary"):
            lines.append(f"   _{a.get('summary', '')[:80]}{'...' if len(a.get('summary', '')) > 80 else ''}_")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 按地域分组（exclude 桶在 main() 已被剔除，这里只渲染可参与的两桶）
    for region_key in ["suining", "unrestricted"]:
        items = by_region.get(region_key, [])
        if not items:
            continue
        region_label = REGION_LABELS.get(region_key, region_key)
        lines.append(f"## {region_label}（{len(items)} 篇）")
        lines.append("")

        for cat in cat_order:
            cat_items = [a for a in items if a.get("category") == cat]
            if not cat_items:
                continue
            cat_label = CATEGORY_LABELS.get(cat, cat)
            lines.append(f"### {cat_label}（{len(cat_items)} 篇）")
            lines.append("")
            for a in cat_items:
                is_off = " ⭐官方" if a.get("is_official") else ""
                repush, _ = repush_label(a)
                title = a.get("title", "")
                url = a.get("url", "")
                lines.append(f"- **{title}**{is_off}{repush}")
                lines.append(f"  - 🔗 {url}")
                lines.append(f"  - 📰 {a.get('source', '')} · 📅 {a.get('publish_date', '')}")
                if a.get("summary"):
                    lines.append(f"  - 📝 {a.get('summary', '')[:120]}{'...' if len(a.get('summary', '')) > 120 else ''}")
                lines.append("")

        lines.append("---")
        lines.append("")

    # 底部说明
    lines.append("## 使用说明")
    lines.append("")
    lines.append("- 每个标题下的链接都是该推文的原文 URL，可直接复制")
    lines.append("- 公众号编辑器粘贴时建议用「超链接」按钮手动插入链接（粘贴 URL 后选中文字点超链接）")
    lines.append("- `⭐ 官方` 标记为官方/品牌发布，可优先选用")
    lines.append("- 无法参与的活动（需定位外地/限外地身份）已在生成时自动剔除，不在本清单内")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_urls_by_category(articles: list, output_path: Path, today: str):
    """按主题分组的 Markdown。"""
    cat_order = CAT_ORDER
    by_cat = {}
    for a in articles:
        by_cat.setdefault(a.get("category", "other"), []).append(a)

    lines = [f"# 公众号福利推文·按主题分类（{today}）", ""]

    for cat in cat_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        cat_label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"## {cat_label}（{len(items)} 篇）")
        lines.append("")
        for a in items:
            is_off = " ⭐" if a.get("is_official") else ""
            region = a.get("_region", "")
            lines.append(f"- **{a.get('title', '')}**{is_off}")
            lines.append(f"  - 🔗 {a.get('url', '')}")
            if region:
                lines.append(f"  - 📍 {region}")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(articles: list, output_path: Path):
    """CSV 表格，方便导入 Excel/腾讯文档。"""
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "编号", "主题", "地域", "是否官方", "标题", "URL",
            "公众号来源", "发布日期", "摘要", "URL是否可用"
        ])
        for i, a in enumerate(articles, 1):
            cat = CATEGORY_LABELS.get(a.get("category"), a.get("category", ""))
            region = a.get("_region", "")
            url = a.get("url", "")
            writer.writerow([
                i,
                cat,
                region,
                "是" if a.get("is_official") else "否",
                a.get("title", ""),
                url,
                a.get("source", ""),
                a.get("publish_date", ""),
                (a.get("summary", "") or "").replace("\n", " "),
                "可用" if is_valid_url(url) else "占位/无效",
            ])


def write_wechat_mp_paste(articles: list, output_path: Path, today: str):
    """公众号编辑器专用排版（直接粘贴到公众号后台即可）。"""
    cat_order = CAT_ORDER

    _m = int(today[5:7]) if len(today) >= 7 else 0
    lines = [
        f"# 🎁 {_m}月福利推文汇总·{today}",
        "",
        f"> 本期共收录 {len(articles)} 篇福利/抽奖/优惠推文，按主题分类整理。文末附链接清单。",
        "",
        "---",
        "",
    ]

    for cat in cat_order:
        items = [a for a in articles if a.get("category") == cat]
        if not items:
            continue
        cat_label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"## {cat_label}")
        lines.append("")

        for a in items:
            is_off = "（官方）" if a.get("is_official") else ""
            lines.append(f"### {a.get('title', '')}{is_off}")
            lines.append("")
            lines.append(f"📅 {a.get('publish_date', '')} · 📨 {a.get('source', '')}")
            lines.append("")
            if a.get("summary"):
                lines.append(f"{a.get('summary', '')}")
                lines.append("")
            # 公众号编辑器对链接的处理：用「标题」+「链接」的形式呈现
            url = a.get("url", "")
            if is_valid_url(url):
                lines.append(f"👉 **点击查看原文**：{a.get('title', '')}")
                lines.append(f"链接：{url}")
            else:
                lines.append(f"👉 链接待更新（活动页/二维码）")
            lines.append("")
            lines.append("---")
            lines.append("")

    # 底部：分类链接汇总（方便读者/编辑复制）
    lines.append("## 📋 全部链接汇总")
    lines.append("")
    lines.append("| # | 主题 | 标题 | 链接 |")
    lines.append("|---|------|------|------|")
    for i, a in enumerate(articles, 1):
        cat = CATEGORY_LABELS.get(a.get("category"), "其他").split()[0]
        title = (a.get("title", "") or "").replace("|", "/")
        url = a.get("url", "")
        if is_valid_url(url):
            lines.append(f"| {i} | {cat} | {title} | [查看]({url}) |")
        else:
            lines.append(f"| {i} | {cat} | {title} | （待更新） |")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="导出推文链接（公众号可复制）")
    parser.add_argument("--input", required=True, help="articles.json 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument(
        "--history-file", default=None,
        help="history.json 路径，用于标注「是否重推」；不传则不标注",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    articles = load_articles(input_path)
    valid_articles = filter_valid(articles)
    today = datetime.now().strftime("%Y-%m-%d")

    # 参与可行性过滤（2026-10-15 用户要求：不要推荐无法参与的活动）：
    # exclude 桶的活动不进任何交付物，只打印存档。
    tag_region(valid_articles)
    valid_articles, excluded_articles = split_participable(valid_articles)
    if excluded_articles:
        print(f"[参与可行性] 已排除 {len(excluded_articles)} 篇无法参与的活动（不进交付物）：")
        for a in excluded_articles:
            reason = a.get("_region_vetoed", "仅限其他远地/特定身份")
            print(f"  - {a['title'][:46]}  [{reason}]")

    # 重推标注：读历史库构建 first_seen 映射，注入模块级变量供 repush_label 使用
    global SEEN_MAP, TODAY_STR
    SEEN_MAP = load_first_seen_map(Path(args.history_file)) if args.history_file else {}
    TODAY_STR = today
    if SEEN_MAP:
        n_repush = sum(1 for a in valid_articles if repush_label(a)[1])
        print(f"[repush] 历史库已载入：本次 {len(valid_articles)} 篇中 {n_repush} 篇曾推荐过（已加 🔄 标记）")

    # 1. 按地域分组 Markdown
    write_urls_by_region(valid_articles, output_dir / "urls-by-region.md", today,
                         excluded_count=len(excluded_articles))
    print(f"✓ 生成: urls-by-region.md ({len(valid_articles)} 篇可参与)")

    # 2. 按主题分组 Markdown
    write_urls_by_category(valid_articles, output_dir / "urls-by-category.md", today)
    print(f"✓ 生成: urls-by-category.md")

    # 3. CSV
    write_csv(valid_articles, output_dir / "urls.csv")
    print(f"✓ 生成: urls.csv (Excel/腾讯文档可打开)")

    # 4. 公众号编辑器专用排版
    write_wechat_mp_paste(valid_articles, output_dir / "wechat-mp-paste.md", today)
    print(f"✓ 生成: wechat-mp-paste.md (公众号编辑器粘贴版)")

    # 统计
    valid_count = len(valid_articles)
    invalid_articles = [a for a in articles if not is_valid_url(a.get("url", ""))]
    print(f"\n=== 导出统计 ===")
    print(f"  总文章：{len(articles)} 篇")
    print(f"  可参与且 URL 可用（进交付物）：{valid_count} 篇")
    print(f"  无法参与（已剔除）：{len(excluded_articles)} 篇")
    print(f"  URL 占位/无效：{len(invalid_articles)} 篇（需手动补链接）")
    if invalid_articles:
        print(f"\n⚠️ 占位 URL 示例（公众号文章链接真实域名多为 mp.weixin.qq.com 或 new.qq.com，需手动核实替换）：")
        for a in invalid_articles:
            print(f"  - {a.get('title', '')[:50]}")


if __name__ == "__main__":
    main()