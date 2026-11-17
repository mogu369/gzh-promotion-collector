"""
把 data/articles.json 中已有的文章入历史库（用于首次启用跨日去重时回填）。
之后由 process_articles.py 自动维护历史库。
"""
import argparse
import json
import sys
from pathlib import Path

# 把当前目录加入 sys.path 以便 import process_articles
sys.path.insert(0, str(Path(__file__).parent))
from process_articles import (
    load_history,
    save_history,
    update_history,
    dedupe_key,
    HISTORY_SCHEMA_VERSION,
)


def main():
    ap = argparse.ArgumentParser(description="回填历史库（一次性工具）")
    ap.add_argument("--input", default="data/articles.json",
                    help="要回填的文章 JSON 路径")
    ap.add_argument("--history-file", default="data/history.json",
                    help="历史库路径")
    ap.add_argument("--from-date", default=None,
                    help="回填时使用的 seen_date，YYYY-MM-DD（默认取文章内 _seen_on 或今天）")
    args = ap.parse_args()

    articles = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if isinstance(articles, dict):
        articles = articles.get("articles", [])

    history_path = Path(args.history_file)
    history = load_history(history_path)

    seen_date = args.from_date
    if not seen_date:
        # 用文章内 _seen_on 推断
        sample = next((a.get("_seen_on") for a in articles if a.get("_seen_on")), None)
        seen_date = sample or __import__("datetime").date.today().isoformat()
        print(f"[backfill] 未指定 from-date，使用 {seen_date}")

    # 转换格式：process_articles 的 update_history 期望 list[dict] 含 title/category/url
    payload = []
    for a in articles:
        a.setdefault("title", "")
        a.setdefault("url", "")
        a.setdefault("category", "other")
        payload.append(a)

    new_count = update_history(history, payload, seen_date)
    save_history(history_path, history)

    print(f"[backfill] 回填 {len(payload)} 篇：新增 {new_count} 条 / "
          f"更新 {len(payload) - new_count} 条 → 历史库现有 {len(history['items'])} 条")


if __name__ == "__main__":
    main()
