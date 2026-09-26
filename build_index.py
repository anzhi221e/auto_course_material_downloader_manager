# -*- coding: utf-8 -*-
"""
build_index.py -- 从四份 syllabus 解析出「课程 -> 周次 -> 阅读材料」索引。

输出 readings_index.json，供 sort_downloads.py 匹配下载文件使用。
syllabus 更新后重跑本脚本即可。
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weeks as W                                  # noqa: E402

try:                                    # Windows 控制台默认 GBK，中文会乱码
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent          # ...\2026 Fall
PRINT = BASE / "PRINT MATERIALS"
OUT = Path(__file__).resolve().parent / "readings_index.json"

# 课程清单来自 courses.json（从 courses.example.json 复制一份改）。
# 以前这里是写死的四门课，别人拿到跑不了。
COURSES = {c["folder"]: c for c in W.courses()}


# --------------------------------------------------------------------------
# 文本抽取
# --------------------------------------------------------------------------
def read_pdf(path):
    import fitz
    doc = fitz.open(path)
    return "\n".join(page.get_text() for page in doc)


def read_docx(path):
    import docx
    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def load_text(path):
    raw = read_pdf(path) if path.suffix.lower() == ".pdf" else read_docx(path)
    # NFKC 顺手把 ﬀ/ﬁ 之类连字还原成 ff/fi
    return unicodedata.normalize("NFKC", raw)


# 文末的参考文献表 / 行政条款，不属于任何一周，必须截掉
TAIL_MARKERS = (
    r"^\s*References\s*$",
    r"^\s*Bibliography\s*$",
    r"^\s*Works Cited\s*$",
    r"^\s*Other important information\s*$",
)


def cut_tail(text):
    """从最靠后的一个「参考文献/行政条款」标题处截断。"""
    cut = len(text)
    for pat in TAIL_MARKERS:
        for m in re.finditer(pat, text, re.M):
            # 只在文档后半段生效，避免误伤正文里的同名小标题
            if m.start() > len(text) * 0.4:
                cut = min(cut, m.start())
                break
    return text[:cut]


BULLET_ONLY = re.compile(r"^[\s*+•●▪·–—\-]+$")

# 结构性标题行：遇到就切段，并且本身丢弃（AI 课的 syllabus 大量使用）
BREAK_LINES = (
    re.compile(r"^\s*(Required|Further|Optional)\s+Readings?\b", re.I),
    re.compile(r"^\s*Optional\s*/\s*Provided\s*:", re.I),
    re.compile(r"^\s*Pick\s+One\b", re.I),
    re.compile(r"^\s*(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\s*,?\s*\d{1,2}/\d{1,2}", re.I),
    re.compile(r"^\s*(Critical Computing Lab|Discussion Board|Content Warning|CW)\b", re.I),
    re.compile(r"^\s*https?://"),
    re.compile(r"^\s*\(https?://"),
    re.compile(r"^\s*[IVX]+\.\s+[A-Z][A-Z\s,\-]+$"),   # LinC 的 "I. IMAGE-TEXT" 分部标题
)


def is_break_line(s):
    return any(p.match(s) for p in BREAK_LINES)


# ---- 必读 / 选读 ---------------------------------------------------------
# ERA 和 AI 课靠小标题划分；LinC 靠每条引文前的 ** / * / + 标记；
# Perspectives 不区分，一律当必读。
SECTION_MARKERS = (
    (re.compile(r"^\s*Optional\s*/\s*Provided", re.I), "optional"),
    (re.compile(r"^\s*(Further|Optional|Additional|Supplement\w*)\s+Readings?", re.I), "optional"),
    (re.compile(r"^\s*Required\s+Readings?", re.I), "required"),
    (re.compile(r"^\s*Pick\s+One", re.I), "required"),      # 选一篇，仍属必读
)


def section_status(s):
    for pat, st in SECTION_MARKERS:
        if pat.match(s):
            return st
    return None


# LinC 的图例（原文）：Required/essential 标 **；strongly recommended 标 *；
# Optional/supplementary 标 +。标记也可能出现在行中（针对某段页码）。
def linc_status(raw):
    head = raw[:3]
    if head.startswith("**"):
        return "required"
    if head.startswith("+"):
        return "optional"
    if "**" in raw:
        return "required"          # 行中标了 ** 的段落属于必读
    if head.startswith("*") or "*" in raw:
        return "recommended"
    if "+" in raw:
        return "optional"
    return "required"              # 没有任何标记的按必读处理


# 「姓名串」里允许出现的成分：大写开头的词（含缩写点、连字符）、连接词、姓氏小品词
NAMEWORD = (
    r"(?:[A-ZÀ-ɏ][A-Za-zÀ-ɏ'’.\-]*"   # 含 Žižek、Émile 等重音字母
    r"|and|et|al\.?|&|de|van|von|della|di|du|la|le|dos|bin)"
)
NAMERUN = NAMEWORD + r"(?:[ ,]+" + NAMEWORD + r"){0,7}"

# 判断一行是不是「新引文的开头」
NEW_CITE_PATTERNS = (
    # 姓名串, 后接大写字母或引号标题
    #   Ozawa-de Silva, Chikako / Joseph Weizenbaum, "ELIZA / Booth et al., "Making
    re.compile(r"^" + NAMERUN + r"\s*,\s*[\"“]?[A-Z]"),
    # 姓名串 (Year)
    #   Max Horkheimer and Theodor W. Adorno (1947) / Emily M. Bender, et al. (2021)
    re.compile(r"^" + NAMERUN + r"\s*\(\d{4}[a-z]?\)"),
)

# 以引号标题开头，例如 "Participant Observation," from Bernard...
# 这类只有在上一条引文已经写完时才算新条目，否则多半是续行（如 Scott 的章节名）
QUOTE_START = re.compile(r"^[\"“][A-Z]")
COMPLETE_TAIL = re.compile(r"[.)\]]\s*$")

# 形如 "The Crossing" from de Leon, Jason (2015) …
# 这种一定是新条目，不管上一条有没有以句号收尾。
# 少了这条，ERA 第 5 周的 "The Crossing" 会被并进上一行 Kulick 的引文里，
# 导致那一条的作者变成 Kulick、标题变成 The Crossing。
QUOTED_TITLE_THEN_SOURCE = re.compile(
    r"^[\"“][^\"”]{3,90}[\"”]\s*,?\s+(?:from|in|by)\s+[A-ZÀ-ɏ]", re.I
)


def starts_new_citation(s):
    return any(p.match(s) for p in NEW_CITE_PATTERNS) or QUOTE_START.match(s)


def breaks_paragraph(s, cur):
    """在 reflow 里判断 s 是否应该另起一条引文。"""
    if any(p.match(s) for p in NEW_CITE_PATTERNS):
        return True
    if QUOTED_TITLE_THEN_SOURCE.match(s):
        return True
    if QUOTE_START.match(s):
        return bool(cur and YEAR_RE.search(cur) and COMPLETE_TAIL.search(cur))
    return False


def reflow(text):
    """
    把 PDF 里被换行拆散的引文重新拼成一条条段落。

    返回 [(段落, 所属小节状态, 原始行首标记)]。
    小节标题（Optional/Provided: 之类）本身不作为内容返回，但会改变后续段落的状态——
    早先版本把它们直接丢掉，结果丢失了必读/选读的区分。
    """
    out, cur, cur_mark, status = [], "", "", "required"
    for line in text.splitlines():
        stripped = line.strip()
        s = LEADING_MARK.sub("", line).strip()
        if not s:
            continue

        st = section_status(s)
        if st:                                   # 小节标题：切段并切换状态
            if cur:
                out.append((cur, status, cur_mark))
                cur, cur_mark = "", ""
            status = st
            continue

        if BULLET_ONLY.match(stripped) or is_break_line(s):
            if cur:
                out.append((cur, status, cur_mark))
                cur, cur_mark = "", ""
            continue

        mm = LEADING_MARK.match(stripped)
        mark = mm.group(0).strip() if mm else ""

        if cur and breaks_paragraph(s, cur):
            out.append((cur, status, cur_mark))
            cur, cur_mark = s, mark
        elif not cur:
            cur, cur_mark = s, mark
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append((cur, status, cur_mark))
    return out


# --------------------------------------------------------------------------
# 引文解析
# --------------------------------------------------------------------------
QUOTE = r'["“”‘’]'
YEAR_RE = re.compile(r"\b(1[89]\d\d|20[0-4]\d)\b")
SOURCE_RE = re.compile(
    r"\((DROPBOX|ONLINE|CANVAS|Online Library Reserves|Kanopy)\b", re.I
)

# 行首的编者记号（LinC 用 ** / * / + 标注精读程度；其他课用项目符号）
LEADING_MARK = re.compile(r"^[\s*+•●▪–—-]+")

# 作者段到此为止：遇到引号标题、(年份) 或裸年份就停。
# 这一步很关键——早先版本把年份之前的所有大写词都当成姓氏，
# 结果 "Joseph Weizenbaum, \"ELIZA - A Computer Program for the Study of...\" (…1966)"
# 的「作者」里混进了 program / study，导致无关文件被误判。
AUTHOR_STOP_RE = re.compile(r"[\"“]|\(\d{4}|\b(?:1[89]\d\d|20[0-4]\d)\b")
LEADING_QUOTED_TITLE_RE = re.compile(
    r"^[\"“].{4,200}?[\"”]\s*,?\s*(?:from|in|by)?\s*", re.S
)
AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+|\s*&\s*|;\s*|\s+with\s+", re.I)
EDITOR_NOISE_RE = re.compile(r"\b(eds?|editors?|trans|et\s+al|comp)\.?", re.I)
WORD_RE = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’\-]*")

NOISE_PREFIXES = (
    "optional", "further reading", "required reading", "pick one",
    "content warning", "cw:", "discussion board", "week ", "http",
    "read if", "see also", "note:", "n.b.", "upload", "submit", "please",
)

# 不能当作「作者姓氏」的常见词。宁可漏掉一个真姓氏，
# 也不能让 study / program / country 这类词变成匹配依据。
COMMON_WORDS = set("""
about above across after against along among around because before behind below
beside between beyond during except inside outside since through toward under
until within without
another anything else everything nothing other others same something such thing
things whatever
being been does doing done have having make makes making take taken taking give
given goes going come comes came know known knows think thought thinks says said
work works working used uses using need needs want wants
account action actions activity address age aim aims analysis approach area art
based body case cases change child children city class community concept
condition context country culture data day design development difference
discourse effect end essay example experience fact family field figure film form
forms future group history human idea ideas image images issue kind knowledge
language languages life light line list man matter meaning media method methods
mind model money nature number object order paper part people person place point
police policy politics power practice present problem process program programs
project public question questions race reason record research result review
right role school science self sense series service set sex society space state
states story structure student students study studies subject system technology
term terms text texts theory time today topic trade truth type value view voice
war water way woman women word words world writing year years
first second third next last other new old good great long little own same
american english french german chinese british african european western eastern
north south east west
""".split())

# 不能当作「作者姓氏」的常见词
NOT_SURNAMES = COMMON_WORDS | {
    "selections", "selection", "introduction", "chapter", "chapters", "excerpt",
    "excerpts", "from", "the", "and", "with", "part", "volume", "pages", "page",
    "film", "ethnographic", "peer", "further", "required", "optional", "pick",
    "online", "dropbox", "canvas", "press", "university", "books", "cambridge",
    "chicago", "harvard", "duke", "columbia", "routledge", "penguin", "wiley",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
    "discussion", "readings", "reading", "lab", "computing", "critical",
    "generative", "finals", "week", "exam", "class", "board", "warning",
}


def clean(s):
    s = unicodedata.normalize("NFC", s)
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


PAGERANGE_RE = re.compile(r"\bpp?\.\s*[ivxlcIVXLC\d]", re.I)

# 导读散文的特征：有谓语动词、第一人称、指示语。引文是名词性的，不会有这些。
PROSE_RE = re.compile(
    r"\b(?:we|our|us|these\s+readings?|this\s+week|provides?|offers?|discusses?|"
    r"argues?|shows?|demonstrates?|highlights?|introduces?|examines?|explores?|"
    r"considers?|suggests?|traces?|asks?|will\s+\w+|allows?|helps?|gives?\s+us)\b",
    re.I,
)


def looks_like_citation(line):
    """引文 = 开头像作者 + 至少一项书目证据（年份 / 引号标题 / 页码范围）。"""
    stripped = LEADING_MARK.sub("", line).strip()
    low = stripped.lower()
    if len(stripped) < 25:
        return False
    if any(low.startswith(p) for p in NOISE_PREFIXES):
        return False
    if not starts_new_citation(stripped):
        return False
    has_quote = bool(re.search(QUOTE + r".{6,}?" + QUOTE, stripped))
    has_pages = bool(PAGERANGE_RE.search(stripped))
    has_src = bool(SOURCE_RE.search(stripped))

    # 每周开头的导读段落会以「Galaty, Tambiah, Harkness… provide a number of…」
    # 这种形式出现，正好长得像「姓氏, 姓氏」开头的引文，而且里面常带引号词，
    # 光看「有没有引号」拦不住。真正可靠的信号是年份的位置：
    # 引文的出版年紧跟作者（"Galaty, John. 1983."），散文里的年份要到很后面才出现。
    ym = YEAR_RE.search(stripped)
    if ym and ym.start() <= 140:
        return True                     # 出版年紧跟作者，是典型引文
    if PROSE_RE.search(stripped):
        return False                    # 有谓语动词/第一人称，是导读散文
    return bool(ym or has_pages or has_src or (has_quote and len(stripped) < 200))


def author_zone_of(raw):
    """截出引文里真正属于作者的那一小段。"""
    rest = raw
    m = LEADING_QUOTED_TITLE_RE.match(rest)
    if m:                      # 形如 "Participant Observation," from Bernard, ...
        rest = rest[m.end():]
    stop = AUTHOR_STOP_RE.search(rest)
    return rest[: stop.start()] if stop else rest[:120]


def surnames_of(zone):
    """从作者段里取出姓氏。兼容 "Surname, First" 与 "First M. Last" 两种写法。"""
    zone = EDITOR_NOISE_RE.sub("", zone)
    out = []
    for part in AUTHOR_SPLIT_RE.split(zone):
        part = part.strip(" .,")
        if not part:
            continue
        if "," in part:
            head = part.split(",")[0].strip()
            words = WORD_RE.findall(head)
            # "Bernard" → 姓；"Ozawa-de Silva" → 两词都算；"Emily M. Bender" → 取末词
            cands = words if len(words) <= 2 else words[-1:]
        else:
            words = WORD_RE.findall(part)
            cands = words[-1:] if words else []
        for w in cands:
            if len(w) < 4 or w.lower() in NOT_SURNAMES:
                continue
            if not w[0].isupper():
                continue
            out.append(w)
    seen = set()
    return [a for a in out if not (a.lower() in seen or seen.add(a.lower()))]


def parse_citation(line):
    """从一行引文里抽出 authors / year / title。"""
    raw = clean(LEADING_MARK.sub("", line))

    ym = YEAR_RE.search(raw)
    year = ym.group(1) if ym else None
    years = YEAR_RE.findall(raw)

    authors = surnames_of(author_zone_of(raw))

    # 标题：优先引号内，其次年份之后到第一个句点 / "In " 的部分
    title = ""
    qm = re.search(QUOTE + r"(.{6,300}?)" + QUOTE, raw)
    if qm:
        title = qm.group(1)
    elif ym:
        tail = raw[ym.end():]
        tail = re.sub(r"^[\s.)\]\[,:;-]+", "", tail)
        tail = re.split(r"\.\s+In\s|\.\s+[A-Z][a-z]+ University|\(pp?\.", tail)[0]
        title = tail[:200]

    src = SOURCE_RE.search(raw)

    return {
        "raw": raw,
        "authors": authors[:6],
        "year": year,
        "years": sorted(set(years)),
        "title": clean(title).strip(" .,:;-\""),
        "source": src.group(1).upper() if src else None,
    }


# --------------------------------------------------------------------------
# 四个格式各自的分周切分器
# --------------------------------------------------------------------------
def _split_by(text, pat, to_week):
    marks = list(pat.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        wk = to_week(m)
        if wk is None:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.setdefault(wk, []).append(text[m.end():end])
    return {w: "\n".join(chunks) for w, chunks in out.items()}


def split_era(text):
    return _split_by(
        text, re.compile(r"^\s*Week\s+(\d+)\s*\(", re.M), lambda m: int(m.group(1))
    )


def split_ai(text):
    return _split_by(
        text, re.compile(r"^\s*Week\s+(\d+)\s*:", re.M), lambda m: int(m.group(1))
    )


def split_persp(text):
    return _split_by(
        text, re.compile(r"^\s*WEEK\s+(\d+)\s*\(", re.M), lambda m: int(m.group(1))
    )


def split_linc(text):
    # 按上课日期排课、不标周次的 syllabus。周次由学期日历算出来，
    # 不再需要为每门这种课手写一张日期表。
    def to_week(m):
        mo, day = m.group(1).split("/")
        return W.week_of_date(int(mo), int(day))
    return _split_by(text, re.compile(r"^\s*(\d{1,2}/\d{1,2})[,\s]", re.M), to_week)


SPLITTERS = {"era": split_era, "ai": split_ai, "persp": split_persp, "linc": split_linc}

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

# 周标题里附带的上课日期：Week 1 (Sep 30) / WEEK 2 (10/6, 10/8) / Week 3, Oct 14:
HEADING_DATE_RE = re.compile(
    r"^\s*WEEK\s+(\d+)\b[^\n]*?[(,:]([^)\n]*)", re.I | re.M
)
DATE_IN_TEXT_RE = re.compile(
    r"(\d{1,2})\s*/\s*(\d{1,2})|([A-Za-z]{3,9})\.?\s+(\d{1,2})"
)


def dates_from_headings(text, parser):
    """从「Week N (Sep 30)」这类标题里把上课日期抠出来，折算成 m/d -> 周次。"""
    if parser == "linc":
        return {}
    out = {}
    for m in HEADING_DATE_RE.finditer(text):
        wk = int(m.group(1))
        for d in DATE_IN_TEXT_RE.finditer(m.group(2) or ""):
            if d.group(1):
                mo, day = int(d.group(1)), int(d.group(2))
            else:
                mo = MONTHS.get(d.group(3)[:3].lower())
                day = int(d.group(4))
            if mo and 1 <= mo <= 12 and 1 <= day <= 31:
                out["%d/%d" % (mo, day)] = wk
    return out


# --------------------------------------------------------------------------
def build():
    index = {"courses": {}}
    for course, meta in COURSES.items():
        path = PRINT / meta["syllabus"]
        if not path.exists():
            print("[!] 找不到 syllabus: %s" % path, file=sys.stderr)
            continue
        text = cut_tail(load_text(path))
        weeks_text = SPLITTERS[meta["parser"]](text)

        weeks = {}
        for wk in sorted(weeks_text):
            cites = []
            for para, status, mark in reflow(weeks_text[wk]):
                if looks_like_citation(para):
                    c = parse_citation(para)
                    if not c["authors"]:
                        continue
                    # LinC 用行首/行中标记，其余课程用小节标题
                    c["status"] = (linc_status(mark + " " + para)
                                   if meta["parser"] == "linc" else status)
                    cites.append(c)
            seen, uniq = set(), []
            for c in cites:
                k = c["raw"][:90].lower()
                if k not in seen:
                    seen.add(k)
                    uniq.append(c)
            weeks[str(wk)] = uniq

        index["courses"][course] = {
            "folder": course,
            "canvas_course_id": meta["canvas_course_id"],
            "codes": meta["codes"],
            "syllabus": meta["syllabus"],
            # 上课日期 -> 周次。Dropbox/网盘里按日期分的文件夹靠它折算
            "date_to_week": dates_from_headings(text, meta["parser"]),
            "weeks": weeks,
        }
        total = sum(len(v) for v in weeks.values())
        wk_list = ", ".join("W%s:%d" % (w, len(weeks[w])) for w in sorted(weeks, key=int))
        print("%s\n    共 %d 条  (%s)" % (course, total, wk_list))

    OUT.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n索引已写入 %s" % OUT)


if __name__ == "__main__":
    build()
