# -*- coding: utf-8 -*-
"""
ingest_zip.py -- 把网盘整包下载的 zip 解开，按周次归入 <课程>/Week N/。

为什么单独做一个脚本：Language in Culture I 没有 Canvas page，
全部材料放在 Dropbox 共享文件夹（共享链接末尾 dl=0 改成 dl=1 就能整包下成 zip）。
下载夹里还有大量与课程无关的压缩包，所以绝不自动扫描 zip，
只处理你明确指定的那一个。

判定周次的优先级：
  1. 压缩包内部的文件夹名 —— 带「Week 3」或上课日期（10/13、Oct 13）就直接采用，
     这是原作者自己的分类，比任何猜测都准
  2. 对不上时退回 syllabus 匹配（和 sort_downloads.py 同一套规则和门槛）
  3. 还是定不了就不动它，列进待定清单

用法：
    python ingest_zip.py "D:\\Downloads\\LinC.zip" --course "Language in Culture I"
    python ingest_zip.py "...zip" --course "..." --list     # 只看压缩包结构
    python ingest_zip.py "...zip" --course "..." --apply    # 确认后实际解压归位
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sort_downloads as sd                      # noqa: E402  复用同一套匹配规则
import canvas_fetch as cf                        # noqa: E402  复用 fit_path / detect_week
import weeks as W                                # noqa: E402  周文件夹命名

INDEX_FILE = HERE / "readings_index.json"

SKIP_ENTRY = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$|desktop\.ini$)", re.I)

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
# 文件夹名里的日期：10.13 / 10-13 / 10_13 / 10/13 / Oct 13 / October 13
DATE_RE = re.compile(r"\b(\d{1,2})[./\-_](\d{1,2})\b|\b([A-Za-z]{3,9})\.?\s*(\d{1,2})\b")


def week_from_path(rel, date_to_week):
    """从压缩包内部路径里找周次：先看 Week/Unit 字样，再看日期。"""
    for part in reversed(Path(rel).parts[:-1]):      # 只看目录名，不看文件名
        wk = cf.detect_week(part)
        if wk:
            return wk, "目录名 %r" % part
        for m in DATE_RE.finditer(part):
            if m.group(1):
                mo, day = int(m.group(1)), int(m.group(2))
            else:
                mo, day = MONTHS.get(m.group(3)[:3].lower()), int(m.group(4))
            key = "%s/%s" % (mo, day)
            if mo and key in date_to_week:
                return date_to_week[key], "目录名 %r 里的日期 %s" % (part, key)
    return None, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zipfile")
    ap.add_argument("--course", required=True, help="目标课程文件夹名")
    ap.add_argument("--list", action="store_true", help="只打印压缩包结构")
    ap.add_argument("--apply", action="store_true", help="确认后实际解压归位")
    ap.add_argument("--min-score", type=float, default=3.0, dest="min_score",
                    help="匹配分数下限（压缩包是可信来源，默认比下载夹宽松）")
    ap.add_argument("--margin", type=float, default=1.5,
                    help="最佳与次佳周次的分差下限")
    args = ap.parse_args()

    zp = Path(args.zipfile)
    if not zp.is_file():
        sys.exit("找不到压缩包：%s" % zp)
    if not INDEX_FILE.exists():
        sys.exit("找不到 readings_index.json，请先运行 build_index.py")

    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    if args.course not in index["courses"]:
        sys.exit("课程名要和文件夹完全一致，可选：\n  " +
                 "\n  ".join(index["courses"]))
    cmeta = index["courses"][args.course]
    date_to_week = cmeta.get("date_to_week") or {}

    cfg = sd.load_config()
    base = Path(cfg["base_dir"])
    exts = {e.lower() for e in cfg["extensions"]}

    zf = zipfile.ZipFile(zp)
    entries = [i for i in zf.infolist()
               if not i.is_dir() and not SKIP_ENTRY.search(i.filename)]

    if args.list:
        dirs = {}
        for i in entries:
            dirs.setdefault(str(Path(i.filename).parent), 0)
            dirs[str(Path(i.filename).parent)] += 1
        print("压缩包 %s，共 %d 个文件\n" % (zp.name, len(entries)))
        for d in sorted(dirs):
            wk, why = week_from_path(d + "/x", date_to_week)
            print("  [%s] %-60s %d 个文件" % (
                "Week %-2d" % wk if wk else "  ?   ", d[:60], dirs[d]))
        return

    # 只有 syllabus 匹配那一路需要索引和 IDF
    items, idf = sd.load_readings()
    items = [it for it in items if it["course"] == args.course]

    plan, undecided, skipped, existing = [], [], [], []
    for i in entries:
        rel = i.filename
        name = Path(rel).name
        if Path(name).suffix.lower() not in exts:
            skipped.append(rel)
            continue

        wk, why = week_from_path(rel, date_to_week)
        stem = Path(name).stem
        if wk is not None:
            places = [(wk, "required")]
        else:
            # lenient：压缩包是这门课专属的，不是杂乱的下载夹，可以放宽门槛
            res = sd.evaluate(sd.tokens(stem), sd.norm(stem), items, idf, lenient=True)
            if res and res[0] >= args.min_score and (res[0] - res[3]) >= args.margin:
                places = res[5]
                why = "syllabus 匹配 %.1f 分（%s）" % (res[0], res[2])
            else:
                undecided.append((rel, res))
                continue

        # 一份文件可能要放进多个周（跨周重复指定），选读的进 Additional Readings
        dests = []
        for w, st in places:
            d = cf.fit_path(W.week_path(base, args.course, w, st) / name)
            if d.exists() and d.stat().st_size == i.file_size:
                continue
            dests.append(d)
        if not dests:
            existing.append(rel)
            continue
        plan.append((i, dests, places, why))

    print("压缩包 %s，共 %d 个文件\n" % (zp.name, len(entries)))
    if plan:
        n_out = sum(len(e[1]) for e in plan)
        print("【将要解压归位】%d 个文件，落到 %d 个位置" % (len(plan), n_out))
        by_dir = {}
        for i, dests, places, why in plan:
            for d in dests:
                by_dir.setdefault(str(d.parent.relative_to(base / args.course)),
                                  []).append((i.filename, why))
        for k in sorted(by_dir):
            print("\n  -- %s --  (%d)" % (k, len(by_dir[k])))
            for rel, why in by_dir[k]:
                print("     %s" % rel)
                print("         依据: %s" % why)
        print()

    if undecided:
        print("【定不了周次，未处理】%d 个" % len(undecided))
        for rel, res in undecided:
            hint = ""
            if res:
                hint = "  最像 Week %d（%.1f 分，次佳 %.1f）" % (res[1]["week"], res[0], res[3])
            print("  %s%s" % (rel, hint))
        print()

    if existing:
        print("【该周已有同一份，未重复解压】%d 个\n" % len(existing))

    if skipped:
        print("【非阅读材料扩展名，跳过】%d 个\n" % len(skipped))

    if not args.apply:
        print("以上是预演。确认无误后加 --apply 实际执行。")
        return

    done = 0
    for i, dests, places, why in plan:
        data = None
        for dest in dests:
            dest.parent.mkdir(parents=True, exist_ok=True)
            final = sd.unique_dest(dest)
            try:
                if data is None:
                    with zf.open(i) as src:
                        data = src.read()
                final.write_bytes(data)
            except Exception as e:
                print("  [失败] %s -> %s -- %s: %s"
                      % (i.filename, dest.parent.name, type(e).__name__, e))
                continue
            done += 1
    print("已写出 %d 份文件（含跨周重复指定的副本）。压缩包本身未删除：%s" % (done, zp))


if __name__ == "__main__":
    main()
