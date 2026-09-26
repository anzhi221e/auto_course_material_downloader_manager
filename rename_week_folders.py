# -*- coding: utf-8 -*-
"""
rename_week_folders.py -- 把 Week 1 … Week 10 改名成 Week 01_0928_1004 这种带日期的格式。

一次性迁移工具，跑完就不用再管了（脚本以后都按新格式建文件夹）。

会做四件事：
  1. 把已有的周文件夹改名成新格式
  2. 补齐缺的周文件夹（Perspectives 额外有 Week 00）
  3. 同步改写 moves_log.csv 里的路径，否则「撤销上次整理」会找不到文件
  4. 原文件夹里的内容原封不动跟着走（改名不是复制，不碰文件本身）

用法：
    python rename_week_folders.py            # 预演
    python rename_week_folders.py --apply    # 执行
"""
import argparse
import csv
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import weeks as W                                    # noqa: E402

INDEX_FILE = HERE / "readings_index.json"
CONFIG_FILE = HERE / "config.json"
LOG_FILE = HERE / "moves_log.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not INDEX_FILE.exists():
        sys.exit("找不到 readings_index.json，请先运行 build_index.py")
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    base = Path(cfg.get("base_dir", str(HERE.parent)))

    renames, creates, conflicts = [], [], []

    for course in index["courses"]:
        cdir = base / course
        if not cdir.is_dir():
            print("[!] 课程文件夹不存在，跳过：%s" % cdir)
            continue

        existing = {}
        for d in cdir.iterdir():
            if not d.is_dir():
                continue
            wk = W.parse_week_dir(d.name)
            if wk is not None:
                existing.setdefault(wk, []).append(d)

        wanted = list(range(1, 11))
        if course in W.COURSES_WITH_WEEK0:
            wanted.insert(0, 0)

        for wk in wanted:
            target = cdir / W.week_dir(wk)
            got = existing.get(wk, [])
            if not got:
                creates.append(target)
            elif len(got) > 1:
                conflicts.append((course, wk, [d.name for d in got]))
            elif got[0].name != target.name:
                renames.append((got[0], target))

        # 不在 1..10（或 0）范围内的周文件夹，只提示不动
        for wk, dirs in sorted(existing.items()):
            if wk not in wanted:
                for d in dirs:
                    conflicts.append((course, wk, [d.name + "  （不在预期周次内，未处理）"]))

    print("【改名】%d 个" % len(renames))
    for old, new in renames:
        n = sum(1 for _ in old.rglob("*") if _.is_file())
        print("  %s\\%s  ->  %s   （内含 %d 个文件）"
              % (old.parent.name, old.name, new.name, n))

    print("\n【新建】%d 个" % len(creates))
    for c in creates:
        print("  %s\\%s" % (c.parent.name, c.name))

    if conflicts:
        print("\n【需要你确认，未处理】")
        for course, wk, names in conflicts:
            print("  %s  Week %s: %s" % (course, wk, " / ".join(names)))

    if not args.apply:
        print("\n以上是预演。确认无误后加 --apply 执行。")
        return

    for old, new in renames:
        if new.exists():
            print("  [跳过] 目标已存在：%s" % new)
            continue
        old.rename(new)
    for c in creates:
        c.mkdir(parents=True, exist_ok=True)

    # moves_log.csv 里存的是绝对路径，改名后必须同步，否则撤销功能会失效
    if LOG_FILE.exists() and renames:
        rows = list(csv.DictReader(LOG_FILE.open(encoding="utf-8")))
        pairs = [(str(o), str(n)) for o, n in renames]
        changed = 0
        for r in rows:
            for o, n in pairs:
                if r["dest"].startswith(o):
                    r["dest"] = n + r["dest"][len(o):]
                    changed += 1
                    break
        with LOG_FILE.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["batch", "src", "dest", "course", "week", "score"])
            for r in rows:
                w.writerow([r["batch"], r["src"], r["dest"], r["course"],
                            r["week"], r["score"]])
        print("\n已同步改写 moves_log.csv 中 %d 条路径（撤销功能保持可用）" % changed)

    print("\n完成：改名 %d 个，新建 %d 个。" % (len(renames), len(creates)))


if __name__ == "__main__":
    main()
