# -*- coding: utf-8 -*-
"""把所有采集期次的有效条目汇总到一张统一表（HTML + Markdown）。

用法:
    python scripts/all_in_one.py --output-dir output/_all_in_one
    python scripts/all_in_one.py --output-dir output/_all_in_one --all   # 全量归档（ ordinarily 不用）

设计要点：
- 默认只展示最新一期结果（2026-11-17 用户定：每次只展示最新结果）。
- 不再拆成"不限地区/遂宁周边"两个页面，地域改为表格中的一列。
- 加 --all 才输出全部历史期次，用于归档；日常展示入口只给最新一期。
- 同一 URL 去重，保留最新期次的记录。
- 生成可搜索/筛选的单文件 HTML，以及一份 Markdown 表。
"""
import argparse
import glob
import html
import json
import os
import re
from datetime import date

CAT_CN = {
    "lottery": "抽奖", "red_packet": "红包", "discount": "优惠",
    "welfare": "公共福利", "gift": "礼品", "ticket": "门票", "other": "其他",
}

TODAY = date.today()


def _period_sort_key(period: str):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", period)
    if m:
        return m.group(0)
    return period


LOCAL_REGION_LABELS = set()  # 收集各期出现过的本地标签（按用户城市动态）


def load_items(root: str):
    seen = {}
    rows = []
    files = sorted(glob.glob(os.path.join(root, "output", "2026-*", "region-classified.json")))
    for f in files:
        period = os.path.basename(os.path.dirname(f))
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        local_label = d.get("local_label") or "本地及周边"
        LOCAL_REGION_LABELS.add(local_label)
        for bucket in ("suining_region", "unrestricted"):
            for a in d.get(bucket, []):
                key = a.get("url") or a.get("title", "")
                rec = {
                    "period": period,
                    "date": period,
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "source": a.get("source", ""),
                    "official": bool(a.get("is_official")),
                    "category": a.get("category", "other"),
                    "region": a.get("_region") or (local_label if bucket == "suining_region" else "不限地域"),
                    "summary": a.get("summary", ""),
                }
                if key in seen:
                    # 同一活动再次出现：更新为最新期次，不重复成行
                    old = rows[seen[key]]
                    if rec["date"] > old["date"]:
                        rows[seen[key]] = rec
                    continue
                seen[key] = len(rows)
                rows.append(rec)
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows, files


def _end_date_from_title(title: str):
    """从标题粗略提取结束日期，仅用于"是否可能过期"提示。"""
    m = re.search(r"(?:至|—|-|~|到)\s*(\d{1,2})[.\-/月](\d{1,2})", title)
    if m:
        mon, day = int(m.group(1)), int(m.group(2))
        if 1 <= mon <= 12 and 1 <= day <= 31:
            try:
                return date(2026, mon, day)
            except ValueError:
                return None
    return None


def _status(rec):
    end = _end_date_from_title(rec["title"])
    if end and end < TODAY:
        return "已过期", end.isoformat()
    return "进行中/待核", end.isoformat() if end else ""


def build_html(rows, latest_period):
    from collections import Counter
    cat_count = Counter(r["category"] for r in rows)
    period_count = Counter(r["period"] for r in rows)
    total = len(rows)

    trs = []
    for i, r in enumerate(rows, 1):
        status, end = _status(r)
        stale = ' class="stale"' if status == "已过期" else ""
        latest = ' class="latest"' if r["period"] == latest_period else ""
        official = '<span class="tag off">官方</span>' if r["official"] else '<span class="tag un">未识别</span>'
        region_cls = "any" if r["region"] == "不限地域" else "loc"
        title = html.escape(r["title"])
        url = html.escape(r["url"], quote=True)
        link = f'<a href="{url}" target="_blank" rel="noopener">{title}</a>' if url else title
        trs.append(
            f'<tr{stale}{latest} data-cat="{r["category"]}" data-region="{r["region"]}" '
            f'data-text="{html.escape((r["title"] + r["source"]).lower(), quote=True)}">'
            f'<td class="num">{i}</td>'
            f'<td class="date">{r["date"]}<div class="period">{r["period"]}</div></td>'
            f'<td><span class="cat c-{r["category"]}">{CAT_CN.get(r["category"], r["category"])}</span></td>'
            f'<td><span class="region {region_cls}">{r["region"]}</span></td>'
            f'<td class="title-cell">{link}<div class="src">{html.escape(r["source"])} {official}</div>'
            f'<div class="st">{status}{(" · 至 " + end) if end else ""}</div></td>'
            f'</tr>'
        )

    cat_opts = "".join(
        f'<option value="{k}">{CAT_CN.get(k, k)}（{v}）</option>'
        for k, v in sorted(cat_count.items(), key=lambda x: -x[1])
    )
    latest_n = period_count.get(latest_period, 0)

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>福利汇总总表 · {total} 条</title>
<style>
:root{{--bg:#f7f8fa;--card:#fff;--ink:#1f2329;--sub:#646a73;--line:#e5e6eb;--brand:#2563eb;--loc:#d97706;--any:#0f9d58;--stale:#b0b4ba}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 6px}}
.meta{{color:var(--sub);font-size:13px;margin-bottom:16px}}
.toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}}
input,select,button{{font:14px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}}
input{{flex:1;min-width:220px}}
button{{cursor:pointer;border-color:var(--brand);color:var(--brand)}}
button:hover{{background:#eff4ff}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.04)}}
th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
th{{background:#f2f3f5;color:var(--sub);font-weight:600;position:sticky;top:0;z-index:1;white-space:nowrap}}
tr:hover td{{background:#fafbfc}}
tr.stale td{{color:var(--stale)}}
tr.latest td{{background:#fffbe6}}
tr.latest:hover td{{background:#fff6cc}}
.num{{color:var(--sub);width:34px}}
.date{{white-space:nowrap;font-variant-numeric:tabular-nums;width:96px}}
.period{{font-size:11px;color:var(--sub)}}
.title-cell a{{color:var(--ink);text-decoration:none;font-weight:500}}
.title-cell a:hover{{color:var(--brand);text-decoration:underline}}
.src{{font-size:12px;color:var(--sub);margin-top:3px}}
.st{{font-size:11px;color:var(--any);margin-top:2px}}
tr.stale .st{{color:var(--stale)}}
.tag{{font-size:11px;padding:1px 6px;border-radius:4px;margin-left:4px}}
.tag.off{{background:#e8f5e9;color:#137333}}
.tag.un{{background:#f1f3f4;color:#80868b}}
.cat{{font-size:12px;padding:2px 8px;border-radius:10px;white-space:nowrap}}
.c-lottery{{background:#eef2ff;color:#4338ca}} .c-red_packet{{background:#fef2f2;color:#b91c1c}}
.c-discount{{background:#fff7ed;color:#c2410c}} .c-welfare{{background:#ecfdf5;color:#047857}}
.c-gift{{background:#f5f3ff;color:#7c3aed}} .c-ticket{{background:#ecfeff;color:#0e7490}}
.c-other{{background:#f1f3f4;color:#5f6368}}
.region{{font-size:12px;white-space:nowrap}}
.region.loc{{color:var(--loc);font-weight:600}} .region.any{{color:var(--any)}}
.count{{color:var(--sub);font-size:13px;margin:8px 0}}
</style></head><body><div class="wrap">
<h1>公众号福利汇总总表</h1>
<div class="meta">共 {total} 条（{len(period_count)} 期，按 URL 去重）· 生成于 {date.today().isoformat()} · 最新一期 <b>{latest_period}</b>（{latest_n} 条，黄色高亮）</div>
<div class="toolbar">
  <input id="q" placeholder="搜索活动标题或来源…">
  <select id="cat"><option value="">全部类别</option>{cat_opts}</select>
  <select id="region"><option value="">全部地域</option>{''.join(f'<option value="{html.escape(x)}">{html.escape(x)}</option>' for x in sorted(LOCAL_REGION_LABELS))}<option value="不限地域">不限地域</option></select>
  <button id="btn-latest">仅看最新一期</button>
  <button id="btn-active">仅看未过期</button>
  <button id="btn-reset">重置</button>
</div>
<div class="count" id="count"></div>
<table id="tbl"><thead><tr><th>#</th><th>采集期</th><th>类别</th><th>地域</th><th>活动 / 来源 / 状态</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
</div>
<script>
const q=document.getElementById('q'),cat=document.getElementById('cat'),reg=document.getElementById('region');
const rows=[...document.querySelectorAll('#tbl tbody tr')],cnt=document.getElementById('count');
function apply(){{
 let n=0;
 rows.forEach(r=>{{
  const okText=!q.value||r.dataset.text.includes(q.value.toLowerCase());
  const okCat=!cat.value||r.dataset.cat===cat.value;
  const okReg=!reg.value||r.dataset.region===reg.value;
  const okLatest=!r.dataset.latestLock||r.classList.contains('latest');
  const okActive=!r.dataset.activeLock||!r.classList.contains('stale');
  const show=okText&&okCat&&okReg&&okLatest&&okActive;
  r.style.display=show?'':'none'; if(show)n++;
 }});
 cnt.textContent='显示 '+n+' / {total} 条';
}}
q.oninput=apply; cat.onchange=apply; reg.onchange=apply;
document.getElementById('btn-latest').onclick=()=>{{rows.forEach(r=>r.dataset.latestLock=r.dataset.latestLock?'':'1');apply()}};
document.getElementById('btn-active').onclick=()=>{{rows.forEach(r=>r.dataset.activeLock=r.dataset.activeLock?'':'1');apply()}};
document.getElementById('btn-reset').onclick=()=>{{q.value='';cat.value='';reg.value='';rows.forEach(r=>{{delete r.dataset.latestLock;delete r.dataset.activeLock}});apply()}};
apply();
</script></body></html>"""
    return doc


def build_markdown(rows, latest_period):
    lines = ["# 公众号福利汇总总表", "",
             f"> 共 {len(rows)} 条（按 URL 去重）· 生成于 {date.today().isoformat()} · 最新一期 **{latest_period}**", ""]
    lines.append("| # | 采集期 | 类别 | 地域 | 活动 | 来源 | 状态 |")
    lines.append("|---|--------|------|------|------|------|------|")
    for i, r in enumerate(rows, 1):
        status, end = _status(r)
        title = r["title"].replace("|", "／").replace("\n", " ")
        title = f"[{title}]({r['url']})" if r["url"] else title
        src = r["source"].replace("|", "／")
        flag = "·官方" if r["official"] else ""
        st = status + (f"（至{end}）" if end else "")
        mark = " ⭐" if r["period"] == latest_period else ""
        lines.append(f"| {i} | {r['period']}{mark} | {CAT_CN.get(r['category'], r['category'])} | "
                     f"{r['region']} | {title} | {src}{flag} | {st} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="技能根目录")
    ap.add_argument("--output-dir", default="output/_all_in_one")
    ap.add_argument("--all", action="store_true", help="输出全部历史期次（归档用）；默认只展示最新一期")
    ap.add_argument("--latest-only", action="store_true", help="兼容旧参数：等价于默认行为")
    args = ap.parse_args()

    rows, files = load_items(args.root)
    latest_period = os.path.basename(os.path.dirname(files[-1])) if files else ""
    if not args.all:
        rows = [r for r in rows if r["period"] == latest_period]

    out = os.path.join(args.root, args.output_dir)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(build_html(rows, latest_period))
    with open(os.path.join(out, "all-in-one.md"), "w", encoding="utf-8") as f:
        f.write(build_markdown(rows, latest_period))
    with open(os.path.join(out, "all-in-one.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"期数: {len(files)}  条目: {len(rows)}  最新期: {latest_period}")
    print(f"产物: {out}")


if __name__ == "__main__":
    main()
