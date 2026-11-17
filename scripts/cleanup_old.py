# -*- coding: utf-8 -*-
"""两周滚动清理：删除 output/ 下超过保留期的历史期次目录。

规则（2026-11-17 用户定）：
- 过往采集结果每两周清理一次，保留最近 14 天（含边界当天）。
- 只删展示产物（output/2026-MM-DD*/），不碰 history.json（去重依赖）。
- 技能目录与实际工作区两个 output 同步清理。

用法:
    python scripts/cleanup_old.py                 # 默认 dry-run，只列清单
    python scripts/cleanup_old.py --apply         # 实际删除
    python scripts/cleanup_old.py --days 14 --apply
    python scripts/cleanup_old.py --self-test     # 自测日期判定
"""
import argparse
import os
import re
import shutil
from datetime import date, datetime, timedelta

RE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def extract_date(name: str):
    """从目录名开头提取日期；非期次目录返回 None。"""
    m = RE_DATE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def is_expired(name: str, today: date, days: int = 14) -> bool:
    d = extract_date(name)
    if d is None:
        return False  # demo-*、_all_in_one 等非期次目录不动
    return d < today - timedelta(days=days - 1)


def scan(output_root: str, today: date, days: int):
    if not os.path.isdir(output_root):
        return [], []
    names = [n for n in os.listdir(output_root)
             if os.path.isdir(os.path.join(output_root, n))]
    expired = sorted(n for n in names if is_expired(n, today, days))
    kept = sorted(n for n in names if not is_expired(n, today, days))
    return expired, kept


def remove_dirs(output_root: str, names, apply: bool):
    removed = []
    for n in names:
        p = os.path.join(output_root, n)
        if apply and os.path.isdir(p):
            shutil.rmtree(p)
        removed.append(p)
    return removed


def self_test():
    today = date(2026, 11, 17)
    cases = [
        ("2026-11-16", False),   # 1 天前
        ("2026-11-04", False),   # 边界：最近14天的第一天(11/04~11/17)
        ("2026-11-03", True),    # 第15天，删
        ("2026-11-02", True),
        ("2026-10-30", True),
        ("2026-09-11-noon2", True),
        ("demo-cleared", False),
        ("_all_in_one", False),
    ]
    ok = 0
    for name, want in cases:
        got = is_expired(name, today, 14)
        flag = "OK" if got == want else "FAIL"
        print(f"  [{flag}] {name:22s} expired={got} expect={want}")
        ok += got == want
    print(f"self-test: {ok}/{len(cases)} passed")
    return ok == len(cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="技能根目录")
    ap.add_argument("--workspace", default="D:/WorkBuddyProjects/2026-09-04-14-21-09", help="实际工作区根目录")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--today", default=None, help="覆盖今天(YYYY-MM-DD)，便于测试")
    ap.add_argument("--apply", action="store_true", help="实际删除；默认只列清单")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(0 if self_test() else 1)

    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    cutoff = today - timedelta(days=args.days - 1)
    print(f"今天 {today} | 保留 {cutoff} 及之后（最近 {args.days} 天）| 模式 {'删除' if args.apply else 'DRY-RUN'}")

    roots = [
        os.path.join(args.root, "output"),
        os.path.join(args.workspace, "output"),
    ]
    all_expired = set()
    for out in roots:
        expired, kept = scan(out, today, args.days)
        print(f"\n=== {out} ===")
        print(f"  保留 {len(kept)}: {', '.join(kept) if kept else '(空)'}")
        print(f"  待删 {len(expired)}: {', '.join(expired) if expired else '(空)'}")
        all_expired.update(expired)

    if all_expired:
        print(f"\n合计待删期次（去重）: {len(all_expired)} 个")
        if args.apply:
            for out in roots:
                remove_dirs(out, [n for n in all_expired if os.path.isdir(os.path.join(out, n))], True)
            print("已删除。history.json 未改动。")
        else:
            print("DRY-RUN：未删除任何内容，加 --apply 执行。")
    else:
        print("\n无需清理。")


if __name__ == "__main__":
    main()
