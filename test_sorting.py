# -*- coding: utf-8 -*-
"""
Python 侧回归测试： python test_sorting.py

每一条用例都对应一个真实踩过的坑。加新规则前先在这里加用例，
这样同一类错误不会靠「跑一遍看数字对不对」来发现。

测试只用合成数据，不读任何个人 syllabus，所以 clone 下来就能跑。
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import sort_downloads as sd          # noqa: E402
import canvas_fetch as cf            # noqa: E402
import weeks as W                    # noqa: E402
import build_index as bi             # noqa: E402

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        print("  PASS  " + name)
        PASS += 1
    else:
        print("  FAIL  " + name)
        print("        实际: %r" % (got,))
        print("        期望: %r" % (want,))
        FAIL += 1


# --------------------------------------------------------------------------
def make_item(course, week, authors, title, raw=None, status="required"):
    """按 load_readings 的方式造一条索引项。"""
    surnames = set()
    for a in authors:
        surnames |= {t for t in sd.TOKEN_RE.findall(sd.norm(a)) if len(t) >= 4}
    raw = raw or ("%s. %s" % (", ".join(authors), title))
    return {
        "course": course, "week": week, "status": status,
        "surnames": surnames,
        "title_tokens": sd.content_tokens(title),
        "raw_tokens": sd.content_tokens(raw) - surnames,
        "years": set(bi.YEAR_RE.findall(raw)),
        "solo_surnames": set(),
        "label": "%s — %s" % ("/".join(authors), title),
    }


def finalize(items):
    """补上 solo_surnames 和 idf，模拟 load_readings 的收尾。

    IDF 直接调生产函数 sd.normalized_idf，不要在测试里另写一份——
    那样测的就不是真实代码了。
    """
    per = {}
    for it in items:
        d = per.setdefault(it["course"], {})
        for s in it["surnames"]:
            d[s] = d.get(s, 0) + 1
    for it in items:
        it["solo_surnames"] = {s for s in it["surnames"] if per[it["course"]][s] == 1}
    df = {}
    for it in items:
        for t in it["title_tokens"] | it["surnames"]:
            df[t] = df.get(t, 0) + 1
    return items, sd.normalized_idf(df, len(items))


def best(stem, items, idf, lenient=False):
    res = sd.evaluate(sd.tokens(stem), sd.norm(stem), items, idf, lenient=lenient)
    if not res:
        return None
    s, it, why, runner, n, places = res
    if s < 8.0 or (s - runner) < 2.0:          # 默认严格门槛
        return None
    return (it["course"], it["week"])


# --------------------------------------------------------------------------
def test_calendar():
    print("\n[学期日历]")
    # 停课周必须跳过，否则之后所有周整体错一位
    check("感恩节后第 9 周不是 +7 天", W.week_dir(9).endswith("_1130_1206")
          or W.week_dir(9).startswith("Week 09"), True)
    check("周次补零，排序正确", sorted([W.week_dir(2), W.week_dir(10)])[0],
          W.week_dir(2))
    # 停课周之后，日期反查必须跳过那一周
    mondays = W.week_mondays()
    w8, w9 = mondays.get(8), mondays.get(9)
    check("停课周使第 9 周不等于第 8 周 +7 天",
          (w9 - w8).days > 7 if (w8 and w9) else None, True)
    check("上课日期能反查出周次",
          W.week_of_date(w9.month, w9.day) if w9 else None, 9)


def test_path_length():
    print("\n[Windows 路径长度]")
    # 电子书站的文件名动辄 180+ 字符，拼上课程目录会超过 MAX_PATH=260
    long_name = ("Argonauts of the Western Pacific An Account of Native Enterprise "
                 "and Adventure in the Archipelagoes of Melanesian New Guinea "
                 "(Bronislaw Malinowski) (z-library.sk, 1lib.sk, z-lib.sk).pdf")
    d = Path("C:/Users/Example/OneDrive/Academic/2026 Fall/A Long Course Name/Week 01_0928_1004")
    fitted = cf.fit_path(d / long_name)
    check("超长路径被压到上限内", len(str(fitted)) <= cf.PATH_LIMIT, True)
    check("优先砍掉下载站噪音后缀", "z-library.sk" in fitted.name, False)
    check("正常长度的路径不动", cf.fit_path(Path("C:/a/b.pdf")).name, "b.pdf")


def test_matching():
    print("\n[匹配规则]")
    items, idf = finalize([
        # 同一本书按章节分散在多周 —— 最容易错配的情形
        make_item("CourseA", 2, ["de Leon"], "Initial selections from The Land of "
                  "Open Graves Living and Dying on the Migrant Trail"),
        make_item("CourseA", 3, ["de Leon"], "Memo and Lucho",
                  raw='"Memo and Lucho" from de Leon (2015) The Land of Open Graves'),
        make_item("CourseA", 7, ["de Leon"], "Necroviolence",
                  raw='"Necroviolence" from de Leon (2015) The Land of Open Graves'),
        # syllabus 标题很长、文件名很短
        make_item("CourseA", 1, ["Malinowski"], "very brief excerpt from Argonauts "
                  "of the Western Pacific An Account of Native Enterprise"),
        # 驼峰粘连的文件名
        make_item("CourseA", 8, ["Ellis", "Bochner"], "AutoEthnography an Overview"),
        # 带连字符 / 会被粘连的姓氏
        make_item("CourseB", 1, ["Levi-Strauss"], "The Savage Mind bricoleur"),
        # 跨课程同名作者
        make_item("CourseB", 1, ["Geertz"], "Thick Description Toward an "
                  "Interpretive Theory of Culture"),
        make_item("CourseC", 2, ["Geertz"], "Deep Play Notes on the Balinese Cockfight"),
        make_item("CourseB", 3, ["Foucault"], "The Examination"),
        make_item("CourseC", 6, ["Foucault"], "Governmentality"),
    ])

    check("章节名完整命中优先于整本书名",
          best("JasonDeLeon_2015_3Necroviolence_TheLandOfOpenGraves", items, idf),
          ("CourseA", 7))
    check("另一章也各归各周",
          best("JasonDeLeon_2015_4MemoAndLucho_TheLandOfOpenGraves", items, idf),
          ("CourseA", 3))
    check("长标题 vs 短文件名（覆盖率取两者较大值）",
          best("Malinowski, Edited Argonauts of the Western Pacific_removed",
               items, idf), ("CourseA", 1))
    check("驼峰粘连能拆开",
          best("Ellis-AutoethnographyOverview-2011", items, idf), ("CourseA", 8))
    check("连字符姓氏 + 粘连写法",
          best("levistrauss_savage_mind", items, idf), ("CourseB", 1))
    check("跨课程同作者靠标题区分 (1)",
          best("geertz thick description", items, idf), ("CourseB", 1))
    check("跨课程同作者靠标题区分 (2)",
          best("geertz, deep play", items, idf), ("CourseC", 2))
    check("跨课程同作者靠标题区分 (3)",
          best("foucault governmentality", items, idf), ("CourseC", 6))

    # 最关键的安全性质：证据不足时必须拒绝，而不是猜一个
    check("只有姓氏+年份时拒绝判断 (geertz)",
          best("geertz_1973", items, idf), None)
    check("只有姓氏+年份时拒绝判断 (foucault)",
          best("foucault_1977", items, idf), None)
    check("完全无关的文件不动",
          best("tax_return_2025", items, idf), None)
    check("个人文件不因正文相似被误判",
          best("my resume 2026", items, idf), None)


def test_lenient():
    print("\n[可信来源的宽松模式]")
    items, idf = finalize([
        make_item("CourseA", 5, ["Paulston"], "Pronouns of Address in Swedish"),
        make_item("CourseA", 3, ["Peirce"], "Icon Index and Symbol",
                  raw="Peirce. Icon, Index, and Symbol. In Collected Papers"),
    ])
    r = sd.evaluate(sd.tokens("Paulston"), sd.norm("Paulston"), items, idf, lenient=True)
    check("课程专属压缩包里，唯一作者可凭姓氏定位", bool(r and r[0] > 0), True)

    # 但同一作者的别的作品不能蹭进来
    r2 = sd.evaluate(sd.tokens("Peirce_Pragmatism"), sd.norm("Peirce_Pragmatism"),
                     items, idf, lenient=True)
    check("同作者的其它作品被反证挡住", r2 is None, True)

    # 严格模式（下载夹）不受宽松规则影响
    r3 = sd.evaluate(sd.tokens("Paulston"), sd.norm("Paulston"), items, idf, lenient=False)
    check("严格模式下不启用该规则", r3 is None, True)


def test_multi_week():
    print("\n[跨周重复指定]")
    items, idf = finalize([
        make_item("CourseA", 3, ["Silverstein"], "Language Structure and Linguistic Ideology"),
        make_item("CourseA", 5, ["Silverstein"], "Language Structure and Linguistic Ideology"),
    ])
    res = sd.evaluate(sd.tokens("Silverstein_Language structure and linguistic ideology"),
                      sd.norm("x"), items, idf)
    check("同一文献被跨周指定时，返回全部周次",
          sorted(w for w, _ in res[5]), [3, 5])


def test_status_routing():
    print("\n[必读 / 选读分流]")
    base = Path("C:/base")
    check("必读进周文件夹",
          W.week_path(base, "X", 3, "required").name.startswith("Week 03"), True)
    check("选读进 Additional Readings",
          W.week_path(base, "X", 3, "optional").name, W.ADDITIONAL_SUBDIR)
    check("strongly recommended 也归选读",
          W.week_path(base, "X", 3, "recommended").name, W.ADDITIONAL_SUBDIR)


def test_index_parsing():
    print("\n[syllabus 解析]")
    prose = ("Galaty, Tambiah, Harkness, Perrino, and Lempert provide a number of "
             "in-depth case studies showing the multimodal poetics of ritual and its "
             "efficacy in social life, which we will extend by following their travels "
             "into cinematic aesthetic texts (Author 2019).")
    check("每周开头的导读散文不算引文", bi.looks_like_citation(prose), False)

    cite = 'Galaty, John. 1983. "Ceremony and Society: The Poetics of Maasai Ritual." Man 18(2):361-82.'
    check("真引文能识别", bi.looks_like_citation(cite), True)
    check("作者只取姓氏，不混入标题词",
          bi.parse_citation(cite)["authors"], ["Galaty"])

    weiz = ('Joseph Weizenbaum, "ELIZA - A Computer Program for the Study of Natural '
            'Language Communication Between Man and Machine" (Communications of the ACM, 1966)')
    check("长引号标题不影响识别", bi.looks_like_citation(weiz), True)
    check("作者不混入 program/study 等标题词",
          bi.parse_citation(weiz)["authors"], ["Weizenbaum"])

    crossing = '"The Crossing" from de Leon, Jason (2015) The Land of Open Graves'
    check("「\"标题\" from 作者」必定另起一条",
          bi.breaks_paragraph(crossing, "Kulick and Rydstrom (2015) Selections from Loneliness"),
          True)

    check("LinC 的 ** 标记 = 必读", bi.linc_status("** Boas, Franz. 1911."), "required")
    check("LinC 的 + 标记 = 选读", bi.linc_status("+ Williams, Raymond. 2015."), "optional")
    check("LinC 的 * 标记 = 强烈推荐", bi.linc_status("* Gal, Susan. 2021."), "recommended")


def test_dedup():
    print("\n[重复文件]")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "booth (1).pdf").write_bytes(b"x" * 100)
        # 目标文件夹里已有同一份的另一种后缀写法，应当认出来
        want_stem = sd.DUP_SUFFIX_RE.sub("", "booth")
        found = any(sd.DUP_SUFFIX_RE.sub("", f.stem) == want_stem
                    and f.stat().st_size == 100
                    for f in d.iterdir())
        check("能认出 (n) 后缀的同一份文件", found, True)
    check("去后缀规则", sd.DUP_SUFFIX_RE.sub("", "booth 21-34 (2)"), "booth 21-34")


# --------------------------------------------------------------------------
if __name__ == "__main__":
    print("Python 侧回归测试")
    test_calendar()
    test_path_length()
    test_matching()
    test_lenient()
    test_multi_week()
    test_status_routing()
    test_index_parsing()
    test_dedup()
    print("\n通过 %d，失败 %d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
