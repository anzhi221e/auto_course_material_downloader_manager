# -*- coding: utf-8 -*-
"""
reorganize.py -- 以 syllabus 为准，重排已经归档在课程文件夹里的材料。

做三件事：
  1. 选读材料（syllabus 标了 optional / recommended，或列在 Optional 小节下的）
     下沉到该周的 "Additional Readings" 子文件夹，和必读区分开
  2. 跨周重复指定的文献，在每一个被指定的周里都放一份
     —— 目标是「打开任意一周的文件夹，那周要读的东西都齐」
  3. 对不上 syllabus 的文件（教授资料库里的补充文献）原地不动并列出来，
     加 --unlisted-to-library 才会收进课程根目录的 _Library 文件夹

用法：
    python reorganize.py                      # 全部课程，预演
    python reorganize.py --course "Language in Culture I"
    python reorganize.py --apply
    python reorganize.py --apply --unlisted-to-library
"""
import argparse
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sort_downloads as sd                       # noqa: E402
import canvas_fetch as cf                         # noqa: E402
import weeks as W                                 # noqa: E402

# 不参与重排的目录统一由 weeks.PROTECTED_DIRS 定义。
# 原先这里只检查课程根目录下的第一层，所以 Week 03/Assignments/ 不受保护。


def course_files(cdir, exts):
    """课程目录下所有参与重排的文件（含 Additional Readings 里的）。"""
    out = []
    for p in cdir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(cdir)
        if W.is_protected(rel):            # 任意一层命中即跳过
            continue
        if len(rel.parts) == 1:
            continue                    # 课程根目录下的散文件（syllabus 等）不动
        out.append(p)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--course")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--unlisted-to-library", action="store_true",
                    dest="to_library",
                    help="把对不上 syllabus 的文件收进课程根目录的 _Library 文件夹")
    ap.add_argument("--min-score", type=float, default=3.0, dest="min_score")
    ap.add_argument("--margin", type=float, default=1.5)
    args = ap.parse_args()

    cfg = sd.load_config()
    base = Path(cfg["base_dir"])
    exts = {e.lower() for e in cfg["extensions"]}
    items, idf = sd.load_readings()
    # Canvas 清单给出的周次是权威的（来自模块结构），优先于 syllabus 猜测
    exact, fuzzy = sd.load_manifest_maps()

    courses = sorted({it["course"] for it in items})
    if args.course:
        courses = [c for c in courses if args.course.lower() in c.lower()]
        if not courses:
            sys.exit("没有匹配的课程")

    total_moves = total_copies = total_unlisted = 0

    for course in courses:
        cdir = base / course
        if not cdir.is_dir():
            continue
        sub = [it for it in items if it["course"] == course]
        files = course_files(cdir, exts)

        moves, copies, unlisted = [], [], []
        # 先记下每个目标位置已经有什么，避免重复放
        for p in files:
            stem = sd.DUP_SUFFIX_RE.sub("", p.stem)

            places, why = None, ""

            # 0. 抓取时就记下的权威归属（来自 Canvas 页面结构本身）。
            #    有它就不要再猜——否则 Week 1 选读里的 keane_2016.pdf
            #    会被当成 Week 2 必读的另一篇 Keane 挪走。
            pl = sd.placement_of(p.name)
            if pl and pl[0] == course:
                places = [(pl[1], pl[2])]
                why = "抓取时记录的归属"

            # 1. Canvas 清单认得这个文件名：周次直接采信，只需再判必读/选读
            hit = None
            if places is None:
                hit = exact.get(sd.norm(stem))
                if not hit:
                    fz = sd.fuzzy_manifest_match(stem, fuzzy)
                    hit = (fz[1], fz[2]) if fz else None
            if hit and hit[0] == course:
                wk = hit[1]
                places = [(wk, sd.status_in_week(stem, course, wk, items, idf))]
                why = "Canvas 清单 Week %d" % wk

            # 2. 清单里没有就退回 syllabus 匹配
            if places is None:
                res = sd.evaluate(sd.tokens(stem), sd.norm(stem), sub, idf, lenient=True)
                if not (res and res[0] >= args.min_score
                        and (res[0] - res[3]) >= args.margin):
                    unlisted.append(p)
                    continue
                places, why = res[5], res[2]

            wanted = [cf.fit_path(W.week_path(base, course, w, st) / p.name)
                      for w, st in places]
            wanted_set = {str(d) for d in wanted}

            if str(p) in wanted_set:
                primary = p                      # 已经在对的地方
            else:
                primary = wanted[0]
                moves.append((p, primary, why))

            for d in wanted:
                if str(d) == str(primary) or str(d) == str(p):
                    continue
                if d.exists():
                    continue
                copies.append((primary, d))

        if not (moves or copies or unlisted):
            continue

        print("\n=== %s" % course)
        if moves:
            print("  【移动】%d 个" % len(moves))
            for src, dst, why in moves:
                print("    %s" % src.name[:70])
                print("        %s  ->  %s"
                      % (src.parent.relative_to(cdir), dst.parent.relative_to(cdir)))
        if copies:
            print("  【复制到重复指定的周】%d 份" % len(copies))
            for src, dst in copies:
                print("    %s\n        -> %s"
                      % (src.name[:70], dst.parent.relative_to(cdir)))
        if unlisted:
            print("  【对不上 syllabus】%d 个%s"
                  % (len(unlisted), "，将收进 " + W.UNLISTED_SUBDIR
                     if args.to_library else "，保持原位"))
            for p in unlisted[:15]:
                print("    %s" % p.name[:78])
            if len(unlisted) > 15:
                print("    … 另有 %d 个" % (len(unlisted) - 15))

        total_moves += len(moves)
        total_copies += len(copies)
        total_unlisted += len(unlisted)

        if not args.apply:
            continue

        for src, dst, _ in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(sd.unique_dest(dst)))
            except Exception as e:
                print("    [移动失败] %s -- %s: %s" % (src.name, type(e).__name__, e))
        for src, dst in copies:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(sd.unique_dest(dst)))
            except Exception as e:
                print("    [复制失败] %s -- %s: %s" % (src.name, type(e).__name__, e))
        if args.to_library:
            lib = cdir / W.UNLISTED_SUBDIR
            for p in unlisted:
                lib.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(p), str(sd.unique_dest(cf.fit_path(lib / p.name))))
                except Exception as e:
                    print("    [收纳失败] %s -- %s: %s" % (p.name, type(e).__name__, e))

    print("\n合计：移动 %d，复制 %d，对不上 syllabus %d%s"
          % (total_moves, total_copies, total_unlisted,
             "（--apply 未加，以上仅为预演）" if not args.apply else ""))
    # 空的周文件夹清理：只删自己建出来又没用上的 Additional Readings
    if args.apply:
        for course in courses:
            for d in (base / course).rglob(W.ADDITIONAL_SUBDIR):
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()


if __name__ == "__main__":
    main()
