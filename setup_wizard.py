# -*- coding: utf-8 -*-
"""
setup_wizard.py -- 一次性初始化：扫描 syllabus，推断学期和课程，建好全部文件夹。

把这学期的 syllabus 都丢进一个文件夹，然后运行这个脚本。它会：

  1. 问你课程文件夹要建在哪、下载夹在哪
  2. 逐份 syllabus 猜课程名
  3. 试四种解析器，选解析出周次最多的那个
  4. 从周标题里的上课日期反推学期日历：
       - 第一周的周一
       - 总周数
       - **停课周**（相邻两周的日期差超过 7 天，中间那一周就是停课周）
  5. 打印完整方案让你确认
  6. 写出 courses.json，建好课程文件夹和周文件夹，再跑一遍 build_index

用法：
    python setup_wizard.py                    # 交互式
    python setup_wizard.py --syllabi "路径"    # 指定 syllabus 所在文件夹
    python setup_wizard.py --apply            # 跳过确认直接执行
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import build_index as bi                      # noqa: E402

COURSES_FILE = HERE / "courses.json"
SYLLABUS_EXTS = {".pdf", ".docx"}

# 从 syllabus 正文里认课程代码，如 ANTH 33506 / MAPS 30000 / LING 31100
CODE_RE = re.compile(r"\b([A-Z]{3,4})\s?(\d{4,5})\b")


# --------------------------------------------------------------------------
def ask(prompt, default=""):
    """交互式提问；非交互环境（管道、--apply）直接用默认值。"""
    if not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        got = input("%s%s: " % (prompt, "  [%s]" % default if default else "")).strip()
    except EOFError:
        return default
    return got or default


def clean_title(s):
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip(" -–—:,.")


NOISE_WORDS = re.compile(
    r"\b(course\s+)?syllabus\b|\bjump to today\b|\b(autumn|fall|winter|spring|summer)\b"
    r"|\b20\d{2}\b|\(copy\)|\bfinal\b|\bdraft\b|\bv\d+\b", re.I)
# 以介词/连词结尾说明标题被截断了（"PERSPECTIVES IN"）
DANGLING = re.compile(r"\b(in|of|and|for|the|to|on|a|an|with)$", re.I)


def _name_candidates(text, path):
    """从文件名和正文各取若干候选课程名。"""
    cands = []   # [(名字, 来源)]

    # 文件名：去掉 syllabus / 课程代码 / 年份 / 括号内容
    stem = re.sub(r"\.(pdf|docx)$", "", path.name, flags=re.I)
    stem = CODE_RE.sub(" ", stem)
    stem = re.sub(r"\([^)]*\)", " ", stem)
    stem = NOISE_WORDS.sub(" ", stem)
    stem = clean_title(re.sub(r"[_\d]+", " ", stem))
    if stem:
        cands.append((stem, "file"))

    # 正文前几行：把断行的标题拼回去
    skip = re.compile(r"^(jump to today|class meeting|office hours?|email|"
                      r"instructors?|professor)\b", re.I)
    lines = [clean_title(x) for x in text[:4000].splitlines()]
    lines = [x for x in lines if x]
    for i, t in enumerate(lines[:30]):
        if len(t) < 6 or skip.match(t) or "@" in t or t.lower().startswith("http"):
            continue
        if re.match(r"^(dr|prof|professor|mr|ms|mrs)\.?\s", t, re.I):
            continue
        if re.search(r"[.!?]$", t):
            continue
        merged = NOISE_WORDS.sub(" ", CODE_RE.sub(" ", t))
        merged = clean_title(re.sub(r"\s{2,}", " ", merged))
        if not merged:
            continue
        # 标题被断成两行时（"PERSPECTIVES IN" / "SOCIAL SCIENCE ANALYSIS"），拼起来
        if DANGLING.search(merged) and i + 1 < len(lines):
            nxt = clean_title(NOISE_WORDS.sub(" ", CODE_RE.sub(" ", lines[i + 1])))
            if nxt and not re.search(r"[.!?]$", nxt):
                merged = clean_title(merged + " " + nxt)
        cands.append((merged, "text"))
    return cands


LEAD_JUNK = re.compile(r"^(for|in|of|the|a|an|to|on|and)\s+", re.I)


def _strip_lead(t):
    prev = None
    while prev != t:
        prev = t
        t = LEAD_JUNK.sub("", t).strip()
    return t


def _score_name(t, source="text"):
    """给候选课程名打分，挑最像课程标题的那个。"""
    if not t or len(t) < 6:
        return -99
    if t[0].islower():
        return -99                             # 小写开头 = 正文句子片段，不是标题
    words = t.split()
    s = 1.5 if source == "text" else 0.0       # 正文里的标题通常是官方名
    s += 3.0 if 2 <= len(words) <= 9 else -2.0
    s += 2.0 if 12 <= len(t) <= 60 else -1.0
    if DANGLING.search(t):
        s -= 5.0                               # 以介词结尾 = 被截断
    if t.isupper():
        s -= 1.0                               # 全大写通常是排版，不是最终名字
    if re.search(r"\d", t):
        s -= 1.0
    return s


def guess_course_name(text, path):
    """猜课程名（只是建议，最终由你确认）。"""
    codes = ["%s %s" % (m.group(1), m.group(2))
             for m in CODE_RE.finditer(text[:3000])]
    cands = [(_strip_lead(t), src) for t, src in _name_candidates(text, path)]
    cands = [(t, src) for t, src in cands if t]
    best = max(cands, key=lambda c: _score_name(*c))[0] if cands else path.stem
    # 全大写的话转成词首大写，更像文件夹名
    if best.isupper():
        best = " ".join(w.capitalize() if len(w) > 2 else w.lower()
                        for w in best.split())
        best = best[0].upper() + best[1:]
    return clean_title(best), sorted(set(codes))


def detect_parser(text):
    """四种解析器都试一遍，选解析出「有引文的周」最多的那个。"""
    best, best_n = None, -1
    detail = {}
    for name, fn in bi.SPLITTERS.items():
        try:
            chunks = fn(text)
        except Exception:
            chunks = {}
        n = 0
        for wk, chunk in chunks.items():
            if wk is None:
                continue
            for para, _st, _mk in bi.reflow(chunk):
                if bi.looks_like_citation(para):
                    n += 1
                    break
        detail[name] = (len(chunks), n)
        if n > best_n:
            best, best_n = name, n
    return best, detail


def session_dates(text, parser):
    """
    从 syllabus 里抠出「周次 -> 上课日期(月,日)」。
    era / ai / persp 用周标题里的日期；linc 用课次日期 + 出现顺序。
    """
    out = {}
    if parser == "linc":
        # 不标周次，只有日期。按出现顺序，每两次课算一周（同一周的日期会相邻）
        dates = []
        for m in re.finditer(r"^\s*(\d{1,2})/(\d{1,2})\s*[,\s]", text, re.M):
            d = (int(m.group(1)), int(m.group(2)))
            if d not in dates:
                dates.append(d)
        if not dates:
            return {}
        # 把相隔 <7 天的日期归为同一周
        wk, prev = 1, dates[0]
        out[wk] = prev
        for d in dates[1:]:
            gap = (date(2000, d[0], d[1]) - date(2000, prev[0], prev[1])).days
            if gap >= 5:                       # 跨到下一周
                wk += 1
                out[wk] = d
            prev = d
        return out

    for k, v in bi.dates_from_headings(text, parser).items():
        mo, day = (int(x) for x in k.split("/"))
        if v not in out:
            out[v] = (mo, day)
    return out


def infer_term(all_dates, year):
    """
    汇总各课的「周次 -> 日期」，反推学期日历。

    停课周的识别：相邻两周的日期差超过 7 天，说明中间夹了一周没上课。
    这是最容易被忽略、又会让之后所有周整体错位的地方，所以自动推断出来。
    """
    merged = {}
    for dmap in all_dates:
        for wk, md in dmap.items():
            merged.setdefault(wk, []).append(md)
    if not merged:
        return None

    # 每周取最早的日期作为代表
    rep = {}
    for wk, mds in merged.items():
        rep[wk] = min(date(year, mo, day) for mo, day in mds)

    weeks = sorted(rep)
    first = rep[weeks[0]]
    # 第 1 周的周一
    w1 = first - timedelta(days=first.weekday())
    if weeks[0] == 0:                          # 有 Week 0，则第 1 周是它的下一周
        w1 += timedelta(days=7)

    breaks = []
    for a, b in zip(weeks, weeks[1:]):
        if b != a + 1:
            continue
        gap = (rep[b] - rep[a]).days
        if gap > 10:                           # 中间夹了停课周
            mon_a = rep[a] - timedelta(days=rep[a].weekday())
            n_skip = round(gap / 7) - 1
            for i in range(1, n_skip + 1):
                breaks.append(mon_a + timedelta(days=7 * i))

    return {
        "name": "%d %s" % (year, "Autumn" if w1.month >= 8 else
                           ("Spring" if w1.month <= 5 else "Summer")),
        "week1_monday": w1.isoformat(),
        "week_count": max(max(weeks), 10),
        "has_week0": 0 in rep,
        "breaks": [{"name": "Break", "monday": b.isoformat()} for b in breaks],
    }


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syllabi", help="存放 syllabus 的文件夹")
    ap.add_argument("--base", help="课程文件夹要建在哪")
    ap.add_argument("--downloads", help="下载夹")
    ap.add_argument("--year", type=int, help="学期所在年份")
    ap.add_argument("--apply", action="store_true", help="跳过确认直接执行")
    args = ap.parse_args()

    if COURSES_FILE.exists() and not args.apply:
        print("注意：courses.json 已存在。继续会覆盖它。")
        if ask("仍要继续？输入 yes 确认", "no").lower() not in ("y", "yes"):
            return

    print("\n=== 第 1 步：位置 ===")
    base = Path(args.base or ask("课程文件夹建在哪", str(HERE.parent))).expanduser()
    syl_default = ""
    for cand in (base / "PRINT MATERIALS", base / "Syllabi", base / "syllabi", base):
        if cand.is_dir() and any(p.suffix.lower() in SYLLABUS_EXTS for p in cand.iterdir()):
            syl_default = str(cand)
            break
    syl_dir = Path(args.syllabi or ask("syllabus 放在哪", syl_default)).expanduser()
    downloads = args.downloads or ask(
        "下载夹在哪", str(Path(os.path.expanduser("~")) / "Downloads"))

    if not syl_dir.is_dir():
        sys.exit("找不到 syllabus 文件夹：%s" % syl_dir)
    files = sorted(p for p in syl_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in SYLLABUS_EXTS)
    if not files:
        sys.exit("%s 里没有 pdf / docx" % syl_dir)

    print("\n=== 第 2 步：识别 %d 份 syllabus ===" % len(files))
    courses, all_dates = [], []
    year = args.year
    for p in files:
        try:
            text = bi.cut_tail(bi.load_text(p))
        except Exception as e:
            print("  [读不了] %s —— %s" % (p.name, e))
            continue
        parser, detail = detect_parser(text)
        if detail.get(parser, (0, 0))[1] == 0:
            print("  [跳过] %s —— 四种解析器都没解析出周次，可能不是 syllabus" % p.name)
            continue
        name, codes = guess_course_name(text, p)
        # 文件夹名一旦建错，后面所有归类都会跟着错，所以逐个让你确认。
        # 非交互运行（--apply / 管道）时直接采用建议值。
        name = ask("      课程文件夹名", name) or name
        if year is None:
            ym = re.search(r"\b(20\d{2})\b", text[:4000])
            year = int(ym.group(1)) if ym else date.today().year
        dmap = session_dates(text, parser)
        all_dates.append(dmap)
        courses.append({
            "folder": name, "syllabus": p.name, "parser": parser,
            "canvas_course_id": None, "codes": codes,
            "has_week0": 0 in dmap, "public": False,
        })
        print("  %s" % p.name)
        print("      课程名 -> %s" % name)
        print("      解析器 -> %s（解析出 %d 周有引文）%s"
              % (parser, detail[parser][1],
                 ("  代码 " + "/".join(codes)) if codes else ""))

    if not courses:
        sys.exit("没有识别出任何课程")

    interactive = bool(sys.stdin and sys.stdin.isatty())
    if not interactive:
        # 课程名是从 syllabus 猜的，PDF 断行、排版差异都会让它猜偏。
        # 文件夹名一旦建错，后面所有归类都跟着错，所以必须提醒复核。
        print("\n  ⚠ 以上课程名是猜的，请核对。不对的话有两个办法：")
        print("    - 直接编辑 courses.json 的 folder 字段（文件夹已按猜的名字建好，需一并改名）")
        print("    - 或者交互式重跑：python setup_wizard.py")

    print("\n=== 第 3 步：推断学期日历 ===")
    term = infer_term(all_dates, year)
    if not term:
        sys.exit("syllabus 里找不到上课日期，请手写 courses.json 的 term 段")
    print("  第 1 周周一 : %s" % term["week1_monday"])
    print("  总周数      : %d" % term["week_count"])
    print("  有 Week 0   : %s" % ("是" if term["has_week0"] else "否"))
    if term["breaks"]:
        for b in term["breaks"]:
            print("  停课周      : %s 当周  <- 从日期跳变推断出来的" % b["monday"])
    else:
        print("  停课周      : 未发现")

    cfg = {
        "canvas_base_url": ask("\n你学校的 Canvas 地址", "https://canvas.example.edu"),
        "term": term,
        "paths": {"base_dir": str(base), "downloads_dir": downloads},
        "matching": {"min_score": 8.0, "margin": 2.0,
                     "max_candidates": 10, "min_age_seconds": 30},
        "courses": courses,
    }

    # 用推断出来的 term 算周文件夹名
    COURSES_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    import weeks as W
    import importlib
    importlib.reload(W)

    print("\n=== 第 4 步：将要创建的文件夹 ===")
    plan = []
    for c in courses:
        cdir = base / c["folder"]
        plan.append(cdir)
        for wk in ([0] if c["has_week0"] else []) + list(range(1, term["week_count"] + 1)):
            wdir = cdir / W.week_dir(wk)
            plan.append(wdir)
            # 每周一个作业目录。脚本永不进入（见 weeks.PROTECTED_DIRS），
            # 所以你的作业稿和下载来的阅读材料彻底隔离。
            plan.append(wdir / W.ASSIGNMENTS_SUBDIR)
        plan.append(cdir / "Submitted Assignments Archive")
    todo = [p for p in plan if not p.exists()]
    for c in courses:
        cdir = base / c["folder"]
        mark = "新建" if not cdir.exists() else "已有"
        print("  [%s] %s" % (mark, c["folder"]))
    nw = term["week_count"] + (1 if term["has_week0"] else 0)
    print("\n  共需创建 %d 个文件夹（%d 门课 × %d 周，每周含 %s/，另加作业归档）"
          % (len(todo), len(courses), nw, W.ASSIGNMENTS_SUBDIR))
    print("  周文件夹示例：%s ... %s" % (W.week_dir(1), W.week_dir(term["week_count"])))

    if not args.apply:
        if ask("\n确认创建？输入 yes", "no").lower() not in ("y", "yes"):
            print("已取消。courses.json 已写出，你可以手动检查后再跑 --apply。")
            return

    for p in todo:
        p.mkdir(parents=True, exist_ok=True)
    print("\n已创建 %d 个文件夹。" % len(todo))
    print("courses.json 已写出：%s" % COURSES_FILE)
    print("\n下一步：")
    print("  python weeks.py            # 核对学期日历")
    print("  python build_index.py      # 解析 syllabus 建立索引")


if __name__ == "__main__":
    main()
