"""
公众号福利推文仪表盘渲染器。

输入：process_articles.py 处理后的 JSON（含 articles + stats）
输出：浏览器可直接打开的 HTML 文件

分类用色：
- red_packet 红包 - 红
- lottery    抽奖 - 紫
- welfare    福利 - 蓝
- discount   优惠 - 橙
- other      其他 - 灰
"""
import argparse
import json
from pathlib import Path
from datetime import datetime

CATEGORY_META = {
    "red_packet": {"label": "红包",   "icon": "🧧", "color": "#e74c3c", "bg": "#fde8e7"},
    "lottery":    {"label": "抽奖",   "icon": "🎁", "color": "#8e44ad", "bg": "#f3e8fb"},
    # ⚠️ 2026-11-05 补 gift / ticket：
    # 分类器会产出 7 类（见 category_defs.CATEGORY_LABELS），
    # 但本表原先只有 5 类，导致「送礼」「票根」两类的文章被 for 循环直接跳过——
    # 结果是仪表盘里静默少内容、且不报错（11-05 掉了成都礼包与免单四连两篇）。
    # 新增分类时务必同步这张表，否则又会「跑通但少东西」。
    "gift":       {"label": "送礼",   "icon": "🎀", "color": "#d81b60", "bg": "#fde7f0"},
    "ticket":     {"label": "票根",   "icon": "🎟️", "color": "#00897b", "bg": "#e0f2f1"},
    "welfare":    {"label": "福利",   "icon": "🎉", "color": "#2980b9", "bg": "#e3f1fc"},
    "discount":   {"label": "优惠",   "icon": "💰", "color": "#e67e22", "bg": "#fcf0e1"},
    "other":      {"label": "其他",   "icon": "📌", "color": "#7f8c8d", "bg": "#ecf0f1"},
}

HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>公众号福利推文仪表盘</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f5f6fa; color: #2c3e50; line-height: 1.6; }}
  header {{ background: linear-gradient(135deg, #667eea, #764ba2);
           color: #fff; padding: 32px 24px; }}
  header h1 {{ margin: 0 0 8px; font-size: 24px; }}
  header .meta {{ opacity: 0.85; font-size: 14px; }}
  .stats {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 20px 24px; }}
  .stat-card {{ background: #fff; border-radius: 8px; padding: 14px 18px;
               box-shadow: 0 2px 6px rgba(0,0,0,0.05); min-width: 140px; }}
  .stat-card .num {{ font-size: 26px; font-weight: bold; }}
  .stat-card .lbl {{ color: #7f8c8d; font-size: 13px; margin-top: 4px; }}
  .filters {{ padding: 0 24px 16px; display: flex; gap: 8px; flex-wrap: wrap;
             align-items: center; }}
  .filter-btn {{ border: none; padding: 6px 14px; border-radius: 16px;
                cursor: pointer; font-size: 13px; transition: 0.2s;
                background: #fff; color: #555; }}
  .filter-btn:hover {{ transform: translateY(-1px); }}
  .filter-btn.active {{ background: #2c3e50; color: #fff; }}
  .filter-btn.official {{ background: #f1c40f; color: #2c3e50; }}
  .filter-btn.official.active {{ background: #f39c12; color: #fff; }}
  main {{ padding: 0 24px 40px; display: grid;
         grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
         gap: 20px; }}
  .category-section {{ background: #fff; border-radius: 12px;
                      box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden; }}
  .category-header {{ padding: 16px 20px; display: flex; align-items: center;
                     gap: 8px; font-weight: bold; }}
  .category-header .count {{ background: rgba(0,0,0,0.1); padding: 2px 10px;
                            border-radius: 12px; font-size: 13px; }}
  .article {{ padding: 14px 20px; border-top: 1px solid #ecf0f1;
             transition: background 0.15s; }}
  .article:hover {{ background: #f8f9fa; }}
  .article a {{ color: #2c3e50; text-decoration: none; font-weight: 500; }}
  .article a:hover {{ color: #2980b9; }}
  .article .meta {{ font-size: 12px; color: #7f8c8d; margin-top: 4px;
                   display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 10px;
           font-size: 11px; font-weight: bold; }}
  .badge.official {{ background: #f1c40f; color: #2c3e50; }}
  .badge.relay {{ background: #dfe6e9; color: #2d3436; }}
  .badge.revived {{ background: #d5f5e3; color: #145a32; }}
  .badge.cat {{ background: rgba(0,0,0,0.06); }}
  .acct {{ color: #0984e3; font-weight: 600; }}
  .copy-btn {{ margin-left: auto; padding: 3px 10px; border: 1px solid #dcdde1;
              background: #f8f9fa; color: #2c3e50; border-radius: 12px;
              cursor: pointer; font-size: 11px; transition: 0.2s; }}
  .copy-btn:hover {{ background: #2c3e50; color: #fff; border-color: #2c3e50; }}
  .copy-btn.copied {{ background: #27ae60; color: #fff; border-color: #27ae60; }}
  footer {{ padding: 20px 24px; text-align: center; color: #95a5a6;
           font-size: 12px; }}
  .empty {{ padding: 40px; text-align: center; color: #95a5a6; }}
  .hidden {{ display: none; }}
</style>
</head>
<body>
<header>
  <h1>📢 公众号福利推文仪表盘</h1>
  <div class="meta">生成时间：{generated_at} · 数据源：{data_source}</div>
</header>
"""

HTML_STATS = """
<section class="stats">
  <div class="stat-card"><div class="num">{total}</div><div class="lbl">收集篇数</div></div>
  <div class="stat-card"><div class="num">{kept}</div><div class="lbl">去重后保留</div></div>
  <div class="stat-card"><div class="num">{official}</div><div class="lbl">官方/品牌发布</div></div>
  <div class="stat-card"><div class="num">{cat_count}</div><div class="lbl">覆盖主题数</div></div>
</section>
"""

HTML_FILTERS = """
<section class="filters">
  <strong style="font-size:13px;color:#7f8c8d;">筛选：</strong>
  <button class="filter-btn active" data-filter="all">全部</button>
  {cat_buttons}
  <button class="filter-btn official" data-filter="official-only">仅看官方</button>
</section>
"""

HTML_FOOT = """
<footer>由 gzh-promotion-collector 自动生成 · 数据为公开渠道采集，去重分类后渲染</footer>
<script>
  // 复制链接按钮
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const url = btn.dataset.url;
      if (!url || url === '#') {
        btn.textContent = '无链接';
        return;
      }
      try {
        await navigator.clipboard.writeText(url);
        const original = btn.textContent;
        btn.textContent = '✓ 已复制';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = original;
          btn.classList.remove('copied');
        }, 1500);
      } catch (err) {
        // 降级：选中文本
        const ta = document.createElement('textarea');
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        btn.textContent = '✓ 已复制';
      }
    });
  });
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const f = btn.dataset.filter;
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.category-section').forEach(sec => {
        const cat = sec.dataset.category;
        const onlyOfficial = sec.dataset.officialOnly === '1';
        if (f === 'all') {
          sec.classList.remove('hidden');
        } else if (f === 'official-only') {
          sec.classList.toggle('hidden', !onlyOfficial);
        } else {
          sec.classList.toggle('hidden', cat !== f);
        }
      });
    });
  });
</script>
</body>
</html>
"""


def render_section(cat: str, articles: list) -> str:
    """渲染单个分类区块。"""
    meta = CATEGORY_META.get(cat, CATEGORY_META["other"])
    official_count = sum(1 for a in articles if a.get("is_official"))
    items_html = []
    for a in articles:
        badges = []
        kind = a.get("source_kind", "unknown")
        if kind == "official":
            badges.append('<span class="badge official">官方号</span>')
        elif kind == "aggregator":
            badges.append('<span class="badge relay">线报转述</span>')
        elif a.get("is_official"):
            badges.append('<span class="badge official">官方</span>')
        badges.append(f'<span class="badge cat">{meta["label"]}</span>')
        if a.get("_revived_from"):
            badges.append('<span class="badge revived">往期留存</span>')
        title = a["title"].replace("<", "&lt;").replace(">", "&gt;")
        source = a["source"].replace("<", "&lt;")
        # 账号名单独露出：用户要看的是「谁发的」，不是「活动主办方」
        acct = a.get("source_account") or ""
        acct_html = (f'<span class="acct">🏷️ {acct}</span>' if acct else "")
        date = a.get("publish_date", "")
        url = a.get("url", "#")
        items_html.append(f"""
<div class="article">
  <a href="{url}" target="_blank" rel="noopener">{title}</a>
  <div class="meta">
    {''.join(badges)}
    {acct_html}
    <span>📨 {source}</span>
    <span>📅 {date}</span>
    <button class="copy-btn" data-url="{url}">📋 复制链接</button>
  </div>
</div>""")
    return f"""
<section class="category-section" data-category="{cat}" data-official-only="{1 if official_count > 0 and official_count == len(articles) else 0}">
  <div class="category-header" style="background:{meta['bg']};color:{meta['color']};">
    <span style="font-size:20px;">{meta['icon']}</span>
    <span>{meta['label']}</span>
    <span class="count">{len(articles)}</span>
  </div>
  {''.join(items_html) if items_html else '<div class="empty">无内容</div>'}
</section>"""


def normalize_input(data: dict) -> dict:
    """把 region_filter.py 产出的三桶结构拍平为 articles + stats。

    兼容两种入参：
      ① articles.json           → 含 "articles"，原样使用
      ② region-classified.json  → suining_region / unrestricted / excluded_other_regions
    ⚠️ 2026-10-22 修复：原先只认 "articles"，直接把 region-classified.json 喂进来会渲染出
    「0 篇」的空仪表盘（不报错、静默错），必须做兼容。
    """
    if "articles" in data:
        return data

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

    by_category = {}
    for a in merged:
        if a.get("_region_bucket") == "exclude":   # 无法参与的不进主仪表盘
            continue
        c = a.get("category", "other")
        by_category[c] = by_category.get(c, 0) + 1

    stats = dict(data.get("stats") or {})
    stats.update({
        "total": len(merged),
        "kept": sum(1 for a in merged if a.get("_region_bucket") != "exclude"),
        "by_category": by_category,
    })
    return {"articles": merged, "stats": stats, "generated_at": data.get("generated_at")}


def render_dashboard(data: dict, data_source_label: str = "联网搜索") -> str:
    """生成完整 HTML。"""
    data = normalize_input(data)
    stats = data.get("stats", {})
    articles = [a for a in data.get("articles", []) if a.get("_region_bucket") != "exclude"]
    by_cat = stats.get("by_category", {})

    # 按 CATEGORY_META 顺序输出分类区块
    sections = []
    cat_buttons = []
    for cat, meta in CATEGORY_META.items():
        if cat in by_cat:
            cat_articles = [a for a in articles if a.get("category") == cat]
            sections.append(render_section(cat, cat_articles))
            cat_buttons.append(
                f'<button class="filter-btn" data-filter="{cat}" '
                f'style="background:{meta["bg"]};color:{meta["color"]};">{meta["icon"]} {meta["label"]} ({by_cat[cat]})</button>'
            )

    if not sections:
        sections.append('<div class="empty" style="grid-column:1/-1;">本次未收集到符合条件的推文</div>')

    head = HTML_HEAD.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data_source=data_source_label,
    )
    stats_html = HTML_STATS.format(
        total=stats.get("total", len(articles)),
        kept=stats.get("kept", len(articles)),
        official=sum(1 for a in articles if a.get("is_official")),
        cat_count=len([c for c in by_cat if c != "other"] or ([1] if "other" in by_cat else [])),
    )
    filters_html = HTML_FILTERS.format(cat_buttons="".join(cat_buttons))

    return head + stats_html + filters_html + "<main>" + "".join(sections) + "</main>" + HTML_FOOT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--data-source", default="联网搜索")
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    html = render_dashboard(data, args.data_source)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    normalized = normalize_input(data)
    kept = [a for a in normalized.get("articles", []) if a.get("_region_bucket") != "exclude"]
    print(f"[render_dashboard] 已生成 {out}，共 {len(kept)} 篇（已排除无法参与 "
          f"{len(normalized.get('articles', [])) - len(kept)} 篇）")


if __name__ == "__main__":
    main()