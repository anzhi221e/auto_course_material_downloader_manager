# -*- coding: utf-8 -*-
"""
fetch_public_course.py -- 从「公开但你没有正式选课」的 Canvas 课程抓取阅读材料。

针对「课程对外公开、但你没有正式选课」这种情况：
  - 你没被正式录入，Canvas 的 /api/v1/.../pages 索引对你返回 404
  - 但课程对公众开放，网页版能读
  - 阅读材料不是模块里的 File 条目，而是写在 Page 正文里的超链接
  - 那些链接带 ?verifier=... 签名，**不需要登录**就能下载（已实测：
    未鉴权取回 Content-Type: application/pdf，文件头 %PDF-1.6）

所以这个脚本全程用 Python 直连，不需要浏览器、不需要你的 Cookie、也不需要令牌：

    modules 页 -> 各模块名（含 Week N）和条目
      -> 逐个条目页 -> 正文里的 <a class="instructure_file_link"> 锚点
        -> 按 Required / Further Readings 小标题判定必读还是选读
          -> 用带签名的链接直接下载，文件名取自 Content-Disposition

用法：
    python fetch_public_course.py --all-public            # 处理 courses.json 里 public=true 的课
    python fetch_public_course.py --all-public --apply
    python fetch_public_course.py --course-id 12345 --course "Course Folder Name" --apply
"""
import argparse
import html as H
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sort_downloads as sd                      # noqa: E402
import canvas_fetch as cf                        # noqa: E402
import weeks as W                                # noqa: E402

BASE_URL = W.config().get("canvas_base_url", "").rstrip("/")
UA = {"User-Agent": "Mozilla/5.0"}

# Canvas 把页面正文当作 JSON 字符串塞进 HTML，所以先把转义还原
UNESCAPE = [("\\u003c", "<"), ("\\u003e", ">"), ("\\u0026", "&"),
            ("\\u002F", "/"), ("\\/", "/"), ('\\"', '"'), ("\\n", "\n")]

FILE_ANCHOR_RE = re.compile(
    r'<a\b[^>]*?href="(?P<href>[^"]*?/files/(?P<fid>\d+)[^"]*)"[^>]*>(?P<text>.*?)</a>',
    re.S | re.I,
)
SECTION_RE = re.compile(r"(Required\s+Readings?|Further\s+Readings?|Optional\s+Readings?)",
                        re.I)


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def unescape_body(s):
    for a, b in UNESCAPE:
        s = s.replace(a, b)
    return s


def module_map(course_id):
    """从 modules 页解析出 [(周次, 模块名, [条目id...])]。"""
    _, html = fetch("%s/courses/%s/modules" % (BASE_URL, course_id))
    # 模块标题和条目在文档里是顺序出现的，按出现位置把条目归给最近的模块
    marks = []
    for m in re.finditer(r'aria-label="([^"]+?)(?: toggle module visibility)?"', html):
        name = H.unescape(m.group(1)).strip()
        if cf.detect_week(name):
            marks.append((m.start(), "mod", name))
    for m in re.finditer(r"/courses/%s/modules/items/(\d+)" % course_id, html):
        marks.append((m.start(), "item", m.group(1)))
    marks.sort()

    out, cur, seen_mod = [], None, set()
    for _, kind, val in marks:
        if kind == "mod":
            if val in seen_mod:
                continue
            seen_mod.add(val)
            cur = (cf.detect_week(val), val, [])
            out.append(cur)
        elif cur is not None and val not in cur[2]:
            cur[2].append(val)
    return out


def files_on_page(course_id, item_id):
    """抓一个模块条目页，取出正文里的文件链接。返回 [(file_id, 链接, 标题, 必读/选读)]。"""
    try:
        _, html = fetch("%s/courses/%s/modules/items/%s" % (BASE_URL, course_id, item_id))
    except urllib.error.HTTPError as e:
        print("    [%s] 条目 %s" % (e.code, item_id))
        return []
    body = unescape_body(html)

    # 记下 Required / Further Readings 小标题的位置，用来判定每个链接属于哪一段
    sections = [(m.start(), m.group(1).lower()) for m in SECTION_RE.finditer(body)]

    out, seen = [], set()
    for m in FILE_ANCHOR_RE.finditer(body):
        fid = m.group("fid")
        if fid in seen:
            continue
        seen.add(fid)
        # 正文里的链接是预览形式（…/files/123?verifier=…&wrap=1），直接取会拿到
        # 一个 HTML 预览页。改写成下载形式，并保留 verifier 签名——
        # 有签名就不需要登录会话。
        raw_href = H.unescape(m.group("href"))
        ver = re.search(r"verifier=([0-9a-f-]+)", raw_href)
        href = "%s/courses/%s/files/%s/download?download_frd=1" % (BASE_URL, course_id, fid)
        if ver:
            href += "&verifier=" + ver.group(1)
        text = H.unescape(re.sub(r"<[^>]+>", "", m.group("text"))).strip().strip('"')
        status = "required"
        for pos, label in sections:
            if pos < m.start():
                status = "optional" if label.startswith(("further", "optional")) else "required"
        out.append((fid, href, text, status))
    return out


def download(url, dest_dir, fallback_name):
    """下载并用服务器给的真实文件名落盘。返回最终路径。"""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            raise cf.NotLoggedIn(r.geturl())
        cd = r.headers.get("Content-Disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        name = cf.safe_name(H.unescape(m.group(1)) if m else fallback_name)
        size = r.headers.get("Content-Length")

        # 先看这一份是不是已经在了，是的话连正文都不用读。
        # 仍然返回路径，因为调用方要拿文件名去登记「文件名 -> 标题」对照。
        planned = cf.fit_path(dest_dir / name)
        if planned.exists() and size and planned.stat().st_size == int(size):
            return planned, False

        data = r.read()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = sd.unique_dest(planned)
    dest.write_bytes(data)
    return dest, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-public", action="store_true", dest="all_public",
                    help="处理 courses.json 里所有 public=true 的课程")
    ap.add_argument("--course-id", help="Canvas 课程 id（不用 --all-public 时必填）")
    ap.add_argument("--course", help="本地课程文件夹名（不用 --all-public 时必填）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cfg = sd.load_config()
    base = Path(cfg["base_dir"])

    # --all-public：从 courses.json 里挑出 public=true 的课逐个处理。
    # 这样课程 id 不用写死在 .bat 里，换课换学校都只改配置。
    if args.all_public:
        targets = [(c["canvas_course_id"], c["folder"]) for c in W.courses()
                   if c.get("public") and c.get("canvas_course_id")]
        if not targets:
            sys.exit("courses.json 里没有 public=true 且填了 canvas_course_id 的课程")
    elif args.course_id and args.course:
        targets = [(args.course_id, args.course)]
    else:
        sys.exit("请用 --all-public，或同时给出 --course-id 和 --course")

    for cid, folder in targets:
        if not (base / folder).is_dir():
            print("[!] 本地课程文件夹不存在，跳过：%s" % folder)
            continue
        run_one(cid, folder, base, args)


def run_one(course_id, course_folder, base, args):
    args = argparse.Namespace(**vars(args))
    args.course_id, args.course = course_id, course_folder

    titles = {}
    if sd.TITLES_FILE.exists():
        try:
            titles = json.loads(sd.TITLES_FILE.read_text(encoding='utf-8'))
        except Exception:
            titles = {}

    mods = module_map(args.course_id)
    if not mods:
        sys.exit("modules 页里没解析出带周次的模块")

    print("课程 %s —— 识别到 %d 个带周次的模块\n" % (args.course_id, len(mods)))
    total = skipped = 0
    for wk, name, items in mods:
        print("%s   (%s)" % (W.week_dir(wk), name))
        found = []
        for it in items:
            found.extend(files_on_page(args.course_id, it))
        if not found:
            print("    正文里没有文件链接")
            continue
        for fid, href, text, status in found:
            dest_dir = W.week_path(base, args.course, wk, status)
            tag = "" if status == "required" else "  [选读]"
            print("    %-9s %s%s" % (fid, text[:62], tag))
            if not args.apply:
                total += 1
                continue
            # 已经下过就跳过：按锚点文字和已有文件粗略比对
            try:
                dest, is_new = download(href, dest_dir, text + ".pdf")
            except cf.NotLoggedIn:
                print("        [跳过] 需要登录")
                continue
            except Exception as e:
                print("        [失败] %s: %s" % (type(e).__name__, e))
                continue
            # 锚点文字就是文献标题。文件名往往只剩 hall_1996.pdf，
            # 不把标题记下来，后面对账就会「文件明明在却报缺」。
            titles[dest.name] = {"title": text, "course": args.course,
                                 "week": wk, "status": status}
            if not is_new:
                print("        （已有同一份，跳过）")
                skipped += 1
                continue
            print("        -> %s" % dest.name)
            total += 1
        print()

    if args.apply and titles:
        sd.TITLES_FILE.write_text(json.dumps(titles, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        print("已记录 %d 条「文件名 -> 标题」对照到 %s\n"
              % (len(titles), sd.TITLES_FILE.name))

    print("合计 %d 个文件%s%s" % (total, "，跳过 %d 个已有" % skipped if skipped else "", "（预演，未下载）" if not args.apply else ""))
    if not args.apply:
        print("确认无误后加 --apply 实际下载。")


if __name__ == "__main__":
    main()
