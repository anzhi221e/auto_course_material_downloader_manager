# -*- coding: utf-8 -*-
"""
weeks.py -- 周次与文件夹名的唯一事实来源，以及学期日历。

文件夹格式：Week 01_0928_1004
（补零是为了让资源管理器按 1,2,…,9,10 正确排序）

日期不再写死，而是由 courses.json 的 term 配置算出来：
    week1_monday + 逐周推进，遇到 breaks 里列的停课周就跳过

**停课周是这里最容易出错的地方。** 机械地「上周 +7 天」会从停课之后
整体错一位——2026 秋季感恩节停课，所有课的第 9 周实际是 11/30 而不是 11/23。
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
COURSES_FILE = HERE / "courses.json"
EXAMPLE_FILE = HERE / "courses.example.json"

ADDITIONAL_SUBDIR = "Additional Readings"     # 每周的选读放这里
UNLISTED_SUBDIR = "_Library (not in syllabus)"  # 对不上 syllabus 的补充资料

DIR_RE = re.compile(r"^Week\s*(\d{1,2})(?:[_\s].*)?$", re.I)

_cfg = None


def config():
    """读取 courses.json；没有就退回 courses.example.json 并提示。"""
    global _cfg
    if _cfg is None:
        path = COURSES_FILE if COURSES_FILE.exists() else EXAMPLE_FILE
        if not path.exists():
            raise SystemExit("找不到 courses.json，请从 courses.example.json 复制一份")
        _cfg = json.loads(path.read_text(encoding="utf-8"))
        _cfg["_source"] = path.name
    return _cfg


def term():
    return config().get("term", {})


def courses():
    return config().get("courses", [])


def course_by_folder(folder):
    for c in courses():
        if c.get("folder") == folder:
            return c
    return {}


def has_week0(folder=None):
    if folder is not None:
        c = course_by_folder(folder)
        if "has_week0" in c:
            return bool(c["has_week0"])
    return bool(term().get("has_week0"))


# --------------------------------------------------------------------------
def _week_mondays():
    """周次 -> 该周周一。跳过 breaks 里列的停课周。"""
    t = term()
    start = date.fromisoformat(t.get("week1_monday", "2026-09-28"))
    skips = {date.fromisoformat(b["monday"]) for b in t.get("breaks", []) if b.get("monday")}
    out = {0: start - timedelta(days=7)}          # Week 0 = 开课前一周
    cur, n = start, 1
    while n <= int(t.get("week_count", 10)):
        while cur in skips:                        # 停课周不占周次
            cur += timedelta(days=7)
        out[n] = cur
        cur += timedelta(days=7)
        n += 1
    return out


_mondays = None


def week_mondays():
    global _mondays
    if _mondays is None:
        _mondays = _week_mondays()
    return _mondays


def week_dir(wk):
    """周次 -> 文件夹名。"""
    wk = int(wk)
    m = week_mondays().get(wk)
    if m is None:
        return "Week %02d" % wk
    return "Week %02d_%s_%s" % (wk, m.strftime("%m%d"),
                                (m + timedelta(days=6)).strftime("%m%d"))


def week_of_date(month, day, year=None):
    """
    某个上课日期属于第几周。

    有了它就不需要为「按日期排课、不标周次」的课手写日期表了
    （Language in Culture I 原先就是靠一张手写表，现在由这里算）。
    """
    t = term()
    y = year or date.fromisoformat(t.get("week1_monday", "2026-09-28")).year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    for wk, mon in sorted(week_mondays().items()):
        if mon <= d <= mon + timedelta(days=6):
            return wk
    return None


def parse_week_dir(name):
    """文件夹名 -> 周次。新旧两种格式都认（Week 3 / Week 03_1012_1018）。"""
    m = DIR_RE.match(name.strip())
    return int(m.group(1)) if m else None


def week_path(base, course, wk, status="required"):
    """某门课某一周里，该状态的材料应该放在哪。"""
    p = Path(base) / course / week_dir(wk)
    return p if status == "required" else p / ADDITIONAL_SUBDIR


def expected_dirs(course):
    """某门课应有的全部周文件夹名。"""
    ws = list(range(1, int(term().get("week_count", 10)) + 1))
    if has_week0(course):
        ws.insert(0, 0)
    return [week_dir(w) for w in ws]


# 兼容旧代码：仍有地方引用 COURSES_WITH_WEEK0 / WEEK_DATES
COURSES_WITH_WEEK0 = {c["folder"] for c in courses() if c.get("has_week0")}
WEEK_DATES = {w: (m.strftime("%m%d"), (m + timedelta(days=6)).strftime("%m%d"))
              for w, m in week_mondays().items()}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("配置来源：%s" % config()["_source"])
    print("学期：%s\n" % term().get("name", "?"))
    for w, m in sorted(week_mondays().items()):
        print("  %-20s %s ~ %s" % (week_dir(w), m, m + timedelta(days=6)))
    for b in term().get("breaks", []):
        print("  （停课 %s：%s 当周）" % (b.get("name", ""), b.get("monday")))
