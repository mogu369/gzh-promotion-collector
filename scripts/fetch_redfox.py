"""
红狐公众号爆款雷达 API 数据源（P0 路径）。

未配置 REDFOX_API_KEY 时脚本直接报错退出，让上层回退到 P1（WebSearch）。

API 文档：https://redfox.hk/docs
获取 key：https://redfox.hk/settings/api-keys?source=workbuddy

用法：
    export REDFOX_API_KEY="ak_xxxx..."
    python scripts/fetch_redfox.py \\
        --keywords "红包,抽奖,福利" \\
        --days 7 \\
        --max-per-keyword 5 \\
        --output data/raw_articles.json
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = "https://redfox.hk/api/v1"


def fetch_keyword(api_key: str, keyword: str, days: int, max_items: int) -> list:
    """单关键词调用爆款雷达，返回原始文章列表。"""
    qs = urllib.parse.urlencode({
        "keyword": keyword,
        "days": days,
        "max": max_items,
    })
    url = f"{API_BASE}/gzh/trends?{qs}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("items", [])


def normalize(item: dict) -> dict:
    """把爆款雷达返回的字段映射到统一结构。"""
    return {
        "title": item.get("title", ""),
        "url": item.get("url") or item.get("link") or "",
        "source": item.get("gzh") or item.get("source") or "",
        "publish_date": item.get("date") or item.get("publish_date") or "",
        "summary": item.get("summary") or item.get("desc") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True, help="逗号分隔")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-per-keyword", type=int, default=5)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    api_key = os.environ.get("REDFOX_API_KEY", "").strip()
    if not api_key:
        print("[fetch_redfox] REDFOX_API_KEY 未配置，回退到 P1 联网搜索。",
              file=sys.stderr)
        sys.exit(2)

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    merged = []
    for kw in keywords:
        try:
            items = fetch_keyword(api_key, kw, args.days, args.max_per_keyword)
            merged.extend(normalize(it) for it in items)
            print(f"[fetch_redfox] '{kw}' 拉取 {len(items)} 条")
        except Exception as e:
            print(f"[fetch_redfox] '{kw}' 失败：{e}", file=sys.stderr)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[fetch_redfox] 合并后 {len(merged)} 条 → {out}")


if __name__ == "__main__":
    main()