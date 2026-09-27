# -*- coding: utf-8 -*-
"""
sort_downloads.py -- 把下载文件夹里的课程阅读材料归入 <课程>/Week N/。

安全前提（很重要）：
  D:\\Downloads 是系统默认下载夹，里面绝大多数文件与课程无关。
  本脚本采用「白名单」逻辑：只有能和 syllabus 正面对上号的文件才会被移动，
  其余一律原地不动、也不重命名、不删除。

  判定必须同时满足：
    1. 文件名/正文里出现 syllabus 里的作者姓氏；
    2. 另有标题关键词（≥2 个）或出版年份佐证；
    3. 总分超过阈值，且最佳周次比次佳周次领先足够多（否则视为拿不准，跳过）。

用法：
    python sort_downloads.py              # 先给出计划，再询问是否执行
    python sort_downloads.py --dry-run    # 只看计划，绝不改动
    python sort_downloads.py --yes        # 不询问，直接执行
    python sort_downloads.py --undo       # 撤销上一批移动
"""
import argparse
import csv
import json
import math
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
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
MANIFEST_FILE = HERE / "canvas_manifest.json"
TITLES_FILE = HERE / "file_titles.json"
LOG_FILE = HERE / "moves_log.csv"

_titles_cache = None


def file_titles():
    """
    文件名 -> 下载时已知的文献标题。

    抓取脚本知道每个链接的锚点文字（就是文献标题），但落盘后文件名可能只剩
    hall_1996.pdf，标题信息就丢了。把它单独记下来，匹配时一并使用，
    避免「文件明明在，却报缺」。
    """
    global _titles_cache
    if _titles_cache is None:
        try:
            _titles_cache = json.loads(TITLES_FILE.read_text(encoding="utf-8"))
        except Exception:
            _titles_cache = {}
    return _titles_cache


def title_of(name):
    """sidecar 里记的标题。兼容旧格式（值是字符串）和新格式（值是对象）。"""
    v = file_titles().get(name)
    if isinstance(v, dict):
        return v.get("title") or ""
    return v or ""


def placement_of(name):
    """
    sidecar 里记的权威归属 (课程, 周次, 必读/选读)，没有就返回 None。

    这是抓取当时从 Canvas 页面结构直接读到的事实（在哪个 Week 模块下、
    在 Required 还是 Further Readings 小节下），比事后靠文件名猜要可靠得多。
    少了它，重排器会把 Week 1 选读里的 keane_2016.pdf 误判成 Week 2 必读的
    另一篇 Keane，越理越乱。
    """
    v = file_titles().get(name)
    if isinstance(v, dict) and v.get("course") and v.get("week") is not None:
        return v["course"], int(v["week"]), v.get("status", "required")
    return None


def bag(path):
    """一个文件用于匹配的词集合：文件名 + 已知标题。"""
    t = tokens(path.stem)
    extra = title_of(path.name)
    return (t | tokens(extra)) if extra else t

DEFAULT_CONFIG = {
    "downloads_dir": "D:\\Downloads",
    "base_dir": str(HERE.parent),
    "extensions": [".pdf", ".docx", ".doc", ".epub", ".txt", ".rtf",
                   ".mp3", ".m4a", ".mp4", ".mov"],
    "recurse": False,
    "scan_file_text": True,
    "min_score": 8.0,
    "margin": 2.0,
    "max_candidates": 10,
    "min_age_seconds": 30,
}

# 绝不碰的文件（即使碰巧匹配上）
SKIP_NAME_PATTERNS = (
    re.compile(r"\.(crdownload|part|tmp|partial)$", re.I),
    re.compile(r"^~\$"),                       # Office 临时文件
    re.compile(r"^(setup|install|update)\b", re.I),
    # syllabus 本身会命中一整个学期的引文，永远不参与归类
    re.compile(r"syllabus|教学大纲", re.I),
)

STOPWORDS = set("""
a an the and or of in on at to for from with without into onto by as is are was
were be been being this that these those it its their his her our your my we you
they he she not no nor but if then than so such very more most other some any
all each both few many much own same s t can will just don should now
introduction chapter chapters selections selection excerpt excerpts part volume
vol pp page pages ed eds edition press university book books new york london
paper article journal review essays essay toward towards notes note
""".split())

# 文件名里的加工痕迹，不是作品内容。判断「这个文件名指向别的作品」时要忽略它们，
# 否则 "Malinowski, Edited Argonauts…_removed.pdf" 里的 edited/removed 会变成反证。
FILE_NOISE = set("""
edited edit removed remove final draft copy scan scanned ocr compressed
merged cropped reduced version print printable full complete text extract
reading readings course syllabus week weeks pdf docx epub libgen zlib library
preface foreword forward prologue epilogue afterword appendix conclusion
selections sels chap chaps
""".split())


def author_hits(surnames, hay_tokens):
    """
    姓氏命中。除了直接相等，还认粘连写法：
    levistrauss_1962.pdf 里的 "levistrauss" 应当算作 Lévi-Strauss 命中。
    只在姓氏 ≥5 字母、且出现在长 token 的开头或结尾时才算，避免乱匹配。
    """
    hits = set(surnames) & hay_tokens
    for sn in surnames:
        if sn in hits or len(sn) < 5:
            continue
        for t in hay_tokens:
            if len(t) >= 8 and t != sn and (t.startswith(sn) or t.endswith(sn)):
                hits.add(sn)
                break
    return sorted(hits)


# --------------------------------------------------------------------------
def norm(s):
    """去重音、转小写。"""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


TOKEN_RE = re.compile(r"[a-z]{2,}")
# 浏览器重复下载会加后缀：xxx (1).pdf
DUP_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
# 驼峰粘连：Ellis-AutoethnographyOverview-2011.pdf 里的 AutoethnographyOverview
# 不切开就永远匹配不上 "AutoEthnography: an Overview"
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(s):
    # 原样和拆驼峰两种都收，这样 McElhinny 这类姓氏不会被拆坏
    return (set(TOKEN_RE.findall(norm(s)))
            | set(TOKEN_RE.findall(norm(CAMEL_RE.sub(" ", s)))))


def content_tokens(s):
    return {t for t in tokens(s) if t not in STOPWORDS and len(t) >= 4}


# --------------------------------------------------------------------------
def load_config():
    """
    配置优先级：courses.json（项目配置） > config.json（本地覆盖） > 内置默认值。

    路径和阈值统一放 courses.json，别人 clone 下来只改那一个文件就能跑；
    config.json 留给不想动 courses.json 的临时覆盖。两个都在 .gitignore 里。
    """
    cfg = dict(DEFAULT_CONFIG)
    try:
        c = W.config()
        paths = c.get("paths", {}) or {}
        if paths.get("base_dir"):
            cfg["base_dir"] = paths["base_dir"]
        if paths.get("downloads_dir"):
            cfg["downloads_dir"] = paths["downloads_dir"]
        cfg.update({k: v for k, v in (c.get("matching") or {}).items()
                    if not k.startswith("_")})
    except Exception:
        pass
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    return cfg


def normalized_idf(df, n_items):
    """
    词的稀有度权重，归一化到大致 [1, 6]，**不随语料规模变化**。

    原先用的是 log(1 + N/(1+df))，它会随条目数增长：
    300 条索引下一个独一无二的词约 5.6 分，50 条索引下只有 3.9 分。
    而阈值是按 300 条调的，结果别人课少一点就会大面积拒判。
    归一化之后，不管你这学期有 50 条还是 500 条阅读，分数含义一致。
    """
    n = max(n_items, 2)
    denom = math.log(n + 1)
    out = {}
    for t, d in df.items():
        rarity = math.log((n + 1) / (d + 0.5)) / denom      # 0（极常见）~ 1（唯一）
        out[t] = 1.0 + 5.0 * max(0.0, min(1.0, rarity))
    return out


def load_readings():
    """把索引摊平成一个候选列表，并算出各词的 IDF 权重。"""
    if not INDEX_FILE.exists():
        sys.exit("找不到 %s，请先运行 build_index.py" % INDEX_FILE)
    data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))

    items = []
    for course, cmeta in data["courses"].items():
        for wk, cites in cmeta["weeks"].items():
            for c in cites:
                # 姓氏要拆成纯字母 token 再存。直接存 "levi-strauss" 这种带连字符的
                # 整串，和文件名分出来的 token 永远相等不了，Lévi-Strauss、
                # Ozawa-de Silva、Marsilli-Vargas 这些就全都匹配不上。
                surnames = set()
                for a in c["authors"]:
                    surnames |= {t for t in TOKEN_RE.findall(norm(a)) if len(t) >= 4}
                ttoks = content_tokens(c["title"])
                if not surnames:
                    continue
                items.append({
                    "course": course,
                    "week": int(wk),
                    # required / recommended / optional —— 决定放主目录还是 Additional Readings
                    "status": c.get("status", "required"),
                    "surnames": surnames,
                    "title_tokens": ttoks,
                    # 整条引文的词（含书名、期刊名），只在标题词对不上时兜底
                    "raw_tokens": content_tokens(c["raw"]) - surnames,
                    "years": set(c["years"]),
                    "label": "%s %s — %s" % (
                        ", ".join(c["authors"][:2]), c["year"] or "", c["title"][:60]
                    ),
                })

    # IDF：在很多条引文里都出现的词（language / social / culture…）权重自动降低
    df = {}
    for it in items:
        for t in it["title_tokens"] | it["surnames"]:
            df[t] = df.get(t, 0) + 1
    idf = normalized_idf(df, len(items))

    # 在某门课里只出现过一次的姓氏。对于「可信来源」（课程专属的网盘压缩包），
    # 这种姓氏本身就足以定位——课程里只有一篇 Paulston，Paulston.pdf 不会是别的。
    # 但扫描 D:\Downloads 时不启用这条，那里全是无关文件，宽松规则会误伤。
    per_course = {}
    for it in items:
        c = per_course.setdefault(it["course"], {})
        for s in it["surnames"]:
            c[s] = c.get(s, 0) + 1
    for it in items:
        cnt = per_course[it["course"]]
        it["solo_surnames"] = {s for s in it["surnames"] if cnt[s] == 1}

    return items, idf


# --------------------------------------------------------------------------
def load_manifest_maps():
    """
    从 canvas_manifest.json 建两张表：

      exact —— 规范化文件名 -> (课程, 周次)，用于一字不差的命中
      fuzzy —— [(词集合, 课程, 周次, 原名)]，用于文件被改过名时的高门槛近似匹配

    这是最可靠的一条依据：清单来自 Canvas 的模块结构，模块名本身写着周次，
    所以归类是查表而不是猜测。你在 Canvas 网页上手点下载的文件，
    落到下载夹里用的就是这里的 display_name。
    """
    # 浏览器重新导出的清单会落在下载夹里（canvas_manifest (4).json 这种）。
    # 取件逻辑以前只写在 canvas_fetch 里，而「整理下载文件夹」走的是本文件，
    # 结果一直在用过期清单归类。所以这里也要主动取一次。
    try:
        import canvas_fetch as _cf
        _cf.adopt_manifest_from_downloads()
    except Exception:
        pass

    if not MANIFEST_FILE.exists() or not INDEX_FILE.exists():
        return {}, []
    try:
        man = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}, []

    import canvas_fetch as cf                 # 复用课程/周次识别逻辑，避免两份规则打架

    cfg = load_config()
    exact, fuzzy = {}, []
    for c in man.get("courses", []):
        folder = cf.match_course_folder(c.get("name"), c.get("course_code"), index,
                                        cfg, c.get("id"))
        if not folder:
            continue
        by_id = {f["id"]: f for f in c.get("files", [])}

        def record(nm, wk_):  # noqa: E306
            if not nm:
                return
            exact[norm(Path(nm).stem)] = (folder, max(wk_, 0))
            toks = content_tokens(Path(nm).stem)
            if len(toks) >= 3:
                fuzzy.append((toks, folder, max(wk_, 0), nm))

        # snippet 已经算好归属就直接用。周次规则只在 JS 那边维护一份，
        # 两边各写一套正是之前反复出错的根源。
        for pl in c.get("placements", []):
            record(pl.get("display_name"), int(pl["week"]))

        # 以下是旧清单（没有 placements 字段）的兼容路径
        # 有的课不用 Modules，周次体现在 Files 的文件夹名上
        # （Perspectives 的 Files 里直接有 Week 0 / Week 1 / Week 2 文件夹）。
        # 只看模块会把这类课误判成「没有周次信息」。
        fname = {d["id"]: d.get("name") or "" for d in c.get("folders", [])}
        for f in c.get("files", []):
            wk = cf.detect_week(fname.get(f.get("folder_id"), ""))
            if wk is None:
                continue
            record(f.get("display_name"), wk)
            record(f.get("filename"), wk)

        for m in c.get("modules", []):
            wk = cf.detect_week(m.get("name"))
            if wk is None:
                continue
            for it in m.get("items", []):
                if it.get("type") != "File":
                    continue
                f = by_id.get(it.get("content_id")) or {}
                # 模块条目标题也算一个别名：老师有时会给条目起和文件名不同的名字
                names = [f.get("display_name"), f.get("filename"), it.get("title")]
                for nm in filter(None, names):
                    exact[norm(Path(nm).stem)] = (folder, wk)
                    toks = content_tokens(Path(nm).stem)
                    if len(toks) >= 3:
                        fuzzy.append((toks, folder, wk, nm))
    return exact, fuzzy


def fuzzy_manifest_match(stem, fuzzy):
    """
    文件被改过名时的兜底。门槛定得很高：清单里那条的词要几乎被文件名全覆盖，
    且两边整体重合度过半。达不到就返回 None，交给后面的 syllabus 匹配。
    """
    ft = content_tokens(stem)
    if len(ft) < 3:
        return None
    hits = []
    for toks, folder, wk, disp in fuzzy:
        inter = ft & toks
        cov = len(inter) / len(toks)
        jac = len(inter) / len(ft | toks)
        if cov >= 0.75 and jac >= 0.5:
            hits.append((cov + jac, folder, wk, disp))
    if not hits:
        return None
    hits.sort(reverse=True)
    # 落点不唯一就别猜
    if len({(h[1], h[2]) for h in hits}) > 1:
        return None
    return hits[0]


def extract_text(path, limit=2500):
    """读元数据和首页（标题页）。只取首页，读得越多越容易被正文里的引用误导。"""
    suf = path.suffix.lower()
    try:
        if suf == ".pdf":
            import fitz
            doc = fitz.open(path)
            meta = doc.metadata or {}
            head = " ".join(str(meta.get(k) or "") for k in ("title", "author", "subject"))
            if len(doc):
                head += " " + doc[0].get_text()
            return head[:limit]
        if suf == ".docx":
            import docx
            d = docx.Document(str(path))
            return " ".join(p.text for p in d.paragraphs[:60])[:limit]
        if suf in (".txt", ".rtf"):
            return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        pass
    return ""


def full_title_bonus(t_toks, t_hits, idf):
    """
    syllabus 上那条的标题被文件名 100% 覆盖 —— 这说明「文件就是这一条」，
    是很强的信号，给一笔决定性加分。

    为什么需要它：一本书被按章节分到多周时，文件名里同时含书名和章节名。
    JasonDeLeon_2015_3Necroviolence_TheLandOfOpenGraves.pdf 既能对上第 2 周的
    「整本书」，也能对上第 7 周的「Necroviolence」这一章。只比词的多少，
    整本书那条会赢；但完整命中章节名的那条才是对的。

    只有标题里确实有生僻词时才给，避免「Culture」这种一词标题随便蹭分。
    """
    if not t_toks or len(t_hits) != len(t_toks):
        return 0.0
    if max((idf.get(t, 1.0) for t in t_hits), default=0.0) < 3.0:
        return 0.0
    return 4.0


def score_against(hay_tokens, hay_norm, item, idf, lenient=False):
    """
    返回 (分数, 命中说明)。证据不够硬就返回 0（宁可不动，也不能搬错）。

    lenient=True 用于「可信来源」——课程专属的网盘压缩包。
    那里每个文件本来就是这门课的材料，且文件名往往极简（Paulston.pdf、
    Goffman_Footing.pdf），要求「作者 + 2 个标题词」会把大半都挡在门外。
    所以放宽一条：姓氏在本课程内唯一时，光凭姓氏就够。
    扫描 D:\\Downloads 时绝不启用，那里全是无关文件。
    """
    a_hits = author_hits(item["surnames"], hay_tokens)
    if not a_hits:
        return 0.0, ""          # 没有作者姓氏，一票否决

    t_toks = item["title_tokens"]
    t_hits = sorted(t_toks & hay_tokens)
    need = min(2, len(t_toks)) if t_toks else 99

    # 覆盖率取「占 syllabus 标题」和「占文件名」两者的较大值。
    # 只看前者会漏判：syllabus 上的标题常常很长
    # （"very brief excerpt from Argonauts of the Western Pacific: An Account of…"），
    # 而文件名只有 "Malinowski, Edited Argonauts of the Western Pacific"，
    # 三个词全中却因为分母太大被判不及格。
    # 文件名里的实义词。注意要把「承载了姓氏的 token」也剔掉，
    # 否则 levistrauss_1962.pdf 里的 levistrauss 会被当成指向别的作品的反证。
    file_toks = {t for t in hay_tokens if len(t) >= 4 and t not in STOPWORDS}
    file_toks -= item["surnames"]
    file_toks = {t for t in file_toks
                 if not any(t.startswith(s) or t.endswith(s) for s in a_hits)}
    cov_title = len(t_hits) / len(t_toks) if t_toks else 0.0
    cov_file = len(t_hits) / len(file_toks) if file_toks else 0.0
    coverage = max(cov_title, cov_file)

    # 主判据：标题关键词要对上足够多、且占标题的比例不能太低
    strong = len(t_hits) >= need and coverage >= 0.34
    # 兜底：标题是章节名、文件名用的是书名时，看整条引文的词
    r_hits = sorted(item["raw_tokens"] & hay_tokens)
    alt = len(r_hits) >= 4
    # 可信来源专用：本课程内独一无二的姓氏。
    # 但有个坑：教授的资料库里常有同一作者的多篇文献，而 syllabus 只指定了其中一篇
    # （比如 Peirce 只指定 "Icon, Index, and Symbol"，库里却有 5 个 Peirce PDF）。
    # 所以要求文件名不能指向「别的作品」——名字里出现了对不上号的标题词就是反证。
    solo = False
    if lenient and (set(a_hits) & item.get("solo_surnames", set())):
        contradicting = file_toks - t_toks - item["raw_tokens"] - FILE_NOISE
        solo = not contradicting

    if not (strong or alt or solo):
        return 0.0, ""

    y_hit = next((y for y in item["years"] if y in hay_norm), None)
    # 标题分要乘以覆盖率，不能只把覆盖率当门槛。
    # 否则「整本书」那条会靠词多压过「某一章」那条：
    # JasonDeLeon_2015_3Necroviolence_TheLandOfOpenGraves.pdf 里
    # land/open/graves 三个词累加，会盖过完整命中的 necroviolence，
    # 结果第 7 周的指定章节被归到第 2 周的整本书上。
    # 兜底路径（只靠整条引文的词，比如书名）权重要压低，并且不给覆盖率奖励。
    # 否则「整本书」那条会靠书名里的几个词，压过「某一章」那条的完整标题命中。
    s = (3.0 * len(a_hits)
         + sum(idf.get(t, 1.0) for t in t_hits) * coverage
         + (2.0 * coverage if strong else 0.0)          # 标题命中得越完整越可信
         + full_title_bonus(t_toks, t_hits, idf)        # 标题被完整命中：这就是它
         + (0.25 * sum(idf.get(t, 1.0) for t in r_hits) if alt and not strong else 0.0)
         + (1.5 if y_hit else 0.0))

    why = "作者:%s" % "/".join(a_hits)
    if t_hits or r_hits:
        why += "  标题词:%s" % ("/".join(t_hits[:4]) if t_hits else "/".join(r_hits[:4]))
    if y_hit:
        why += "  年份:%s" % y_hit
    if solo and not (strong or alt):
        why += "  （本课程内唯一的该姓氏作者）"
    return s, why


# 文件名里没有实义信息的典型样子：JSTOR/ScienceDirect 的编号、扫描件默认名等
GENERIC_NAME_RE = re.compile(
    r"^(?:document|scan|scanned|download|untitled|file|新建|无标题|\d+|[0-9a-f]{8,}|"
    r"1-s2\.0-\S*|.*\(\d+\))$", re.I
)


def filename_informative(stem):
    """文件名本身是否足以判断内容。"""
    if GENERIC_NAME_RE.match(stem.strip()):
        return False
    return len(content_tokens(stem)) >= 2


def classify(path, items, idf, cfg):
    """
    分两层判断，顺序不能反：
      1. 只看文件名。文件名是最可靠的信号——作者名出现在文件名里，
         说明这份文件「是」这篇文献，而不是「提到」它。
      2. 只有文件名是 3034157.pdf 这种无意义串时，才去读首页和元数据。

    为什么不一律读正文：一篇引用了 Cruel Optimism 的自己的论文、
    或者版权页列着《规训与惩罚》的另一本福柯著作，全文里都会出现
    作者名和标题词，看正文就会把它们误判成指定读物。
    """
    hay = path.stem
    best = evaluate(tokens(hay), norm(hay), items, idf)
    if best is not None:
        return best
    if not cfg["scan_file_text"] or filename_informative(hay):
        return None
    extra = extract_text(path)
    if not extra:
        return None
    return evaluate(tokens(hay + " " + extra), norm(hay + " " + extra), items, idf)


def evaluate(hay_tokens, hay_norm, items, idf, lenient=False):
    scored = []
    for it in items:
        s, why = score_against(hay_tokens, hay_norm, it, idf, lenient)
        if s > 0:
            scored.append((s, it, why))
    if not scored:
        return None
    # 同分时取周次靠前的，这样并列的首选天然就是最早的一周
    scored.sort(key=lambda x: (-x[0], x[1]["week"]))
    top_s, top_it, why = scored[0]

    # 同一门课里多周并列：这是「同一篇文献被跨周重复指定」（比如 Tambiah 在
    # 10/8 和 10/13 各读一段，de León 那本横跨四周），不是判断不清。
    # 归入最早的一周，并且不把这些并列项算作竞争者，否则会被 margin 判死。
    same_course_tie = [x for x in scored
                       if abs(x[0] - top_s) < 1e-9 and x[1]["course"] == top_it["course"]]
    # 每一个被指定的周次都要有这份文件，所以把并列的周全都返回，由调用方各放一份
    placements = sorted({(x[1]["week"], x[1].get("status", "required"))
                         for x in same_course_tie})
    if len(placements) > 1:
        why += "  （本课 Week %s 重复指定，各放一份）" % "/".join(
            str(w) for w, _ in placements)

    runner = 0.0
    for s, it, _ in scored:
        if (it["course"], it["week"]) == (top_it["course"], top_it["week"]):
            continue
        if abs(s - top_s) < 1e-9 and it["course"] == top_it["course"]:
            continue                       # 同课并列，不是竞争者
        runner = s
        break
    return top_s, top_it, why, runner, len(scored), placements


# --------------------------------------------------------------------------
def places_for(stem, course, items, idf, default=None):
    """
    查这份文件在某门课里应该出现在哪些位置：[(周次, required/recommended/optional)]。

    跨周重复指定的文献会返回多个位置——每一周都要有一份，
    因为目标是「打开任意一周的文件夹，那周要读的东西都在」。
    """
    sub = [it for it in items if it["course"] == course]
    res = evaluate(tokens(stem), norm(stem), sub, idf, lenient=True)
    if res and res[5]:
        return res[5]
    return default or [(1, "required")]


def status_in_week(stem, course, week, items, idf):
    """已知课程和周次时，查这份文件在 syllabus 上是必读还是选读。查不到按必读。"""
    sub = [it for it in items if it["course"] == course and it["week"] == week]
    res = evaluate(tokens(stem), norm(stem), sub, idf, lenient=True)
    return res[1]["status"] if res else "required"


def make_entry(src, base, course, places, label, score, why):
    """一个来源 -> 一组目标位置。第一个用移动，其余用复制。"""
    dests = [W.week_path(base, course, wk, st) / src.name for wk, st in places]
    it = {"course": course, "week": places[0][0],
          "status": places[0][1], "places": places, "label": label}
    return (src, dests[0], dests[1:], it, score, why)


def gather_files(cfg):
    dl = Path(cfg["downloads_dir"])
    if not dl.is_dir():
        sys.exit("下载目录不存在：%s" % dl)
    exts = {e.lower() for e in cfg["extensions"]}
    it = dl.rglob("*") if cfg["recurse"] else dl.glob("*")
    # 万一下载夹被指到课程目录里，也不能扫进作业目录
    now = time.time()
    out = []
    for p in it:
        if not p.is_file():
            continue
        if W.is_protected(p):              # 作业目录里的东西一律不碰
            continue
        if p.suffix.lower() not in exts:
            continue
        if any(pat.search(p.name) for pat in SKIP_NAME_PATTERNS):
            continue
        try:
            if now - p.stat().st_mtime < cfg["min_age_seconds"]:
                continue                      # 可能还在下载中
        except OSError:
            continue
        out.append(p)
    return sorted(out)


def unique_dest(dest):
    import canvas_fetch as cf
    dest = cf.fit_path(dest)               # 先压到 Windows 路径上限内
    if not dest.exists():
        return dest
    stem, suf, i = dest.stem, dest.suffix, 2
    while True:
        cand = cf.fit_path(dest.with_name("%s (%d)%s" % (stem, i, suf)))
        if not cand.exists():
            return cand
        i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不改动任何文件")
    ap.add_argument("--yes", action="store_true", help="不询问，直接执行")
    ap.add_argument("--undo", action="store_true", help="撤销上一批移动")
    ap.add_argument("--coverage", action="store_true",
                    help="逐周对账：syllabus 上的文献哪些已归位、哪些还缺")
    args = ap.parse_args()

    cfg = load_config()
    if args.undo:
        return undo(cfg)

    items, idf = load_readings()
    exact, fuzzy = load_manifest_maps()
    files = gather_files(cfg)
    base = Path(cfg["base_dir"])

    if args.coverage:
        return coverage_report(items, base, cfg)

    print("扫描 %s —— 候选文件 %d 个，syllabus 条目 %d 条%s\n" % (
        cfg["downloads_dir"], len(files), len(items),
        "，Canvas 清单 %d 条" % len(exact) if exact else "，未找到 Canvas 清单"))

    plan, nearmiss, untouched, already = [], [], 0, []
    for p in files:
        stem = DUP_SUFFIX_RE.sub("", p.stem)

        # 第一优先：Canvas 清单里的精确文件名，没有任何猜测成分
        hit = exact.get(norm(stem))
        if hit:
            course, wk = hit
            # 周次由 Canvas 模块给定（可靠），必读/选读再去 syllabus 查
            places = [(wk, status_in_week(stem, course, wk, items, idf))]
            plan.append(make_entry(p, base, course, places,
                                   "Canvas 模块 Week %d" % wk, 99.0, "Canvas 清单精确匹配"))
            continue

        # 第二优先：文件被改过名，但和清单里某条高度重合
        fz = fuzzy_manifest_match(stem, fuzzy)
        if fz:
            _, course, wk, disp = fz
            places = [(wk, status_in_week(stem, course, wk, items, idf))]
            plan.append(make_entry(p, base, course, places,
                                   "Canvas 清单：%s" % disp, 95.0, "Canvas 清单近似匹配"))
            continue

        res = classify(p, items, idf, cfg)
        if res is None:
            untouched += 1
            continue
        s, it, why, runner, n_cand, places = res
        # 命中一大堆条目的，多半是书目/大纲一类的文档，不要碰
        if n_cand > cfg["max_candidates"]:
            nearmiss.append((p, it, s, runner,
                             "命中 %d 条引文，疑似书目/大纲，不处理" % n_cand))
            continue
        if s < cfg["min_score"] or (s - runner) < cfg["margin"]:
            nearmiss.append((p, it, s, runner, why))
            continue
        plan.append(make_entry(p, base, it["course"], places, it["label"], s, why))

    # 目标处已有同名同大小的文件：说明之前就归位过，别再搬一份出来变成 (2)
    kept = []
    for entry in plan:
        src, dest = entry[0], entry[1]
        # 浏览器重复下载会存成 "xxx (1).pdf"。目标处如果已经有去掉后缀的同一份，
        # 就别再搬一个副本进去——否则周文件夹里会出现成对的重复文件。
        # 目标文件夹里可能已经躺着同一份的另一种后缀写法
        # （已归位的是 "booth (1).pdf"，待搬的是 "booth.pdf"），
        # 所以要拿「去掉 (n) 后的名字 + 大小」去扫整个目标文件夹，
        # 只比对目标文件名本身会漏掉。
        hit = None
        try:
            want_stem = DUP_SUFFIX_RE.sub("", dest.stem)
            want_size = src.stat().st_size
            if dest.parent.is_dir():
                for d in dest.parent.iterdir():
                    if (d.is_file() and d.suffix == dest.suffix
                            and DUP_SUFFIX_RE.sub("", d.stem) == want_stem
                            and d.stat().st_size == want_size):
                        hit = d
                        break
        except OSError:
            pass
        if hit is not None:
            already.append((src, hit))
            continue
        kept.append(entry)
    plan = kept

    # 同一批里指向同一目标、且大小相同的多个来源（浏览器反复下载留下的
    # xxx.pdf / xxx (1).pdf / xxx (2).pdf），只搬一个，其余当重复处理
    seen_target, deduped = {}, []
    for entry in plan:
        src, dest = entry[0], entry[1]
        try:
            key = (str(dest.parent), DUP_SUFFIX_RE.sub("", dest.stem) + dest.suffix,
                   src.stat().st_size)
        except OSError:
            deduped.append(entry)
            continue
        if key in seen_target:
            already.append((src, seen_target[key]))
            continue
        seen_target[key] = dest
        deduped.append(entry)
    plan = deduped

    if plan:
        n_extra = sum(len(e[2]) for e in plan)
        print("【将要移动】%d 个文件%s" % (
            len(plan), "，另在重复指定的周次各复制 %d 份" % n_extra if n_extra else ""))
        for src, dest, extra, it, s, why in plan:
            print("  %s" % src.name)
            for d in [dest] + extra:
                tag = "" if d.parent.name.startswith("Week") else "  [选读]"
                print("      -> %s%s" % (
                    d.parent.relative_to(base / it["course"]), tag))
            print("      (%.1f 分; %s)" % (s, why))
            print("      对应: %s" % it["label"])
        print()
    else:
        print("没有可以确定归属的文件。\n")

    if nearmiss:
        print("【拿不准，保持原位】%d 个 —— 需要你或 Claude 判断" % len(nearmiss))
        for p, it, s, runner, why in nearmiss:
            print("  %s\n      最像: %s / Week %d  (%.1f 分, 次佳 %.1f; %s)" % (
                p.name, it["course"], it["week"], s, runner, why))
        print()

    if already:
        print("【目标处已有同一份，未重复搬运】%d 个 —— 下载夹里这些可以自行删除" % len(already))
        for src, dest in already:
            print("  %s\n      已在 %s" % (src.name, dest.parent.name))
        print()

    print("【未识别为课程材料，未触碰】%d 个文件\n" % untouched)

    if not plan or args.dry_run:
        if args.dry_run:
            print("（--dry-run：未做任何改动）")
        return

    if not args.yes:
        try:
            ans = input("确认移动以上 %d 个文件？[y/N] " % len(plan)).strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("已取消，未做任何改动。")
            return

    batch = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows, moved, failures, renamed = [], 0, [], []
    for src, dest, extra, it, s, why in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = unique_dest(dest)
        try:
            shutil.move(str(src), str(final))
        except Exception as e:
            failures.append((src.name, "%s: %s" % (type(e).__name__, e)))
            continue
        moved += 1
        if final.name != src.name:
            renamed.append((src.name, final.name))
        rows.append([batch, str(src), str(final), it["course"], it["week"], "%.1f" % s])

        # 跨周重复指定的：每一周都要有一份，所以复制而不是移动
        for d in extra:
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists() and d.stat().st_size == final.stat().st_size:
                continue
            try:
                shutil.copy2(str(final), str(unique_dest(d)))
            except Exception as e:
                failures.append((d.name, "复制到 %s 失败 —— %s: %s"
                                 % (d.parent.name, type(e).__name__, e)))

    if rows:
        new = not LOG_FILE.exists()
        with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["batch", "src", "dest", "course", "week", "score"])
            w.writerows(rows)
    if renamed:
        print("\n【文件名过长，已缩短】%d 个" % len(renamed))
        for old, new in renamed:
            print("  %s\n    -> %s" % (old, new))

    if failures:
        print("\n【移动失败】%d 个 —— 文件仍在下载夹，没有丢" % len(failures))
        for name, err in failures:
            print("  %s\n      %s" % (name, err))

    print("\n已移动 %d 个文件%s。记录写入 %s" % (
        moved, "，失败 %d 个" % len(failures) if failures else "", LOG_FILE.name))
    print("如需撤销：python sort_downloads.py --undo")


def coverage_report(items, base, cfg):
    """
    逐周对账：syllabus 上列的文献，哪些已经躺在对应的 Week 文件夹里，哪些还缺。

    这一步是防「悄无声息漏掉」的——归类准确不等于齐全，
    有文件被放错或压根没下载，只有对着 syllabus 数一遍才看得出来。
    """
    exts = {e.lower() for e in cfg["extensions"]}
    by_course = {}
    for it in items:
        by_course.setdefault(it["course"], {}).setdefault(it["week"], []).append(it)

    grand_req = grand_req_have = 0
    for course in sorted(by_course):
        print("\n=== %s" % course)
        for wk in sorted(by_course[course]):
            folder = base / course / W.week_dir(wk)
            present = []
            for d in (folder, folder / W.ADDITIONAL_SUBDIR):
                if d.is_dir():
                    for f in d.iterdir():
                        if f.is_file() and f.suffix.lower() in exts:
                            extra = title_of(f.name)
                            present.append((bag(f), norm(f.stem + " " + extra), f.name))

            req_have, req_miss, opt_have, opt_miss = [], [], [], []
            for it in by_course[course][wk]:
                ok = any(score_against(t, n, it, {}, lenient=True)[0] > 0
                         for t, n, _ in present)
                if it.get("status", "required") == "required":
                    (req_have if ok else req_miss).append(it)
                else:
                    (opt_have if ok else opt_miss).append(it)

            n_req = len(req_have) + len(req_miss)
            n_opt = len(opt_have) + len(opt_miss)
            grand_req += n_req
            grand_req_have += len(req_have)
            flag = "✓" if not req_miss else "!"
            print("  %s %s  必读 %d/%d   选读 %d/%d   （文件夹里 %d 个）"
                  % (flag, W.week_dir(wk), len(req_have), n_req,
                     len(opt_have), n_opt, len(present)))
            for it in req_miss:
                print("        缺(必读): %s" % it["label"])
            for it in opt_miss:
                print("        缺(选读): %s" % it["label"])

    print("\n全部课程必读合计：%d / %d 已就位" % (grand_req_have, grand_req))
    print("注：「缺」只表示没在该周文件夹里找到对得上的文件，"
          "可能是还没下载，也可能是文件名和 syllabus 差太多认不出来。")


def undo(cfg):
    if not LOG_FILE.exists():
        sys.exit("没有移动记录可撤销。")
    rows = list(csv.DictReader(LOG_FILE.open(encoding="utf-8")))
    if not rows:
        sys.exit("没有移动记录可撤销。")
    last = rows[-1]["batch"]
    targets = [r for r in rows if r["batch"] == last]
    print("撤销批次 %s（%d 个文件）" % (last, len(targets)))
    ok = 0
    for r in targets:
        dest, src = Path(r["dest"]), Path(r["src"])
        if not dest.exists():
            print("  [跳过] 已不在原处: %s" % dest.name)
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(unique_dest(src)))
        ok += 1
    keep = [r for r in rows if r["batch"] != last]
    with LOG_FILE.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["batch", "src", "dest", "course", "week", "score"])
        for r in keep:
            w.writerow([r["batch"], r["src"], r["dest"], r["course"], r["week"], r["score"]])
    print("已还原 %d 个文件。" % ok)


if __name__ == "__main__":
    main()
