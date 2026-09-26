# -*- coding: utf-8 -*-
"""
canvas_fetch.py -- 依据浏览器导出的 canvas_manifest.json，按周下载课程材料。

背景：UChicago 关闭了 Canvas 个人访问令牌（Access Token），
所以不能用官方 API 鉴权。改用的办法是：在你已登录的 Canvas 页面控制台里
运行 canvas_snippet.js，用当前会话把「模块 -> 周次 -> 文件」的清单导出成
canvas_manifest.json。清单里的下载链接自带 Canvas 签名（verifier），
本脚本可以直接取用，全程不需要令牌，也不碰你的密码和 Cookie。

用法：
    python canvas_fetch.py --dry-run     # 先看会下载什么、分到哪一周
    python canvas_fetch.py               # 实际下载
    python canvas_fetch.py --course "Perspectives"
    python canvas_fetch.py --show-modules # 只列出各课的模块名（排查周次识别）

签名链接有时效。如果出现大量 403/410，回到 Canvas 页面重跑一次
canvas_snippet.js 生成新的清单即可。
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weeks as W                                  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
INDEX_FILE = HERE / "readings_index.json"
CONFIG_FILE = HERE / "config.json"
MANIFEST_FILE = HERE / "canvas_manifest.json"
MAP_FILE = HERE / "downloaded_map.json"      # 文件名 -> 课程/周次，供 sort_downloads 精确认领

# 模块名里的周次。尽量宽容一点：Week 3 / WEEK 03 / Unit 3 / 第3周
# 从模块名/文件夹名里认周次的正则。可以在 courses.json 的
# week_patterns 里补充 —— 不同学校、不同语言的叫法不一样
# （Session 3、Lecture 3、Topic 3、Semaine 3…），不该写死在代码里。
DEFAULT_WEEK_PATTERNS = [
    r"\bweek\s*0*(\d{1,2})\b",
    r"\bunit\s*0*(\d{1,2})\b",
    r"\bwk\.?\s*0*(\d{1,2})\b",
    r"\bsession\s*0*(\d{1,2})\b",
    r"\btopic\s*0*(\d{1,2})\b",
    r"第\s*0*(\d{1,2})\s*[周週]",
]


def _week_patterns():
    try:
        import weeks as _W
        extra = _W.config().get("week_patterns") or []
    except Exception:
        extra = []
    return [re.compile(p, re.I) for p in list(extra) + DEFAULT_WEEK_PATTERNS]


WEEK_PATTERNS = _week_patterns()


def detect_week(name):
    for pat in WEEK_PATTERNS:
        m = pat.search(name or "")
        if m:
            return int(m.group(1))
    return None


def load_config():
    cfg = {"base_dir": str(HERE.parent)}
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    return cfg


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip(" .")
    return name[:180] or "untitled"


# Windows 默认 MAX_PATH 是 260，且本机 LongPathsEnabled=0。
# 电子书站的文件名动辄一百八十多字符，拼上课程目录就会超限，
# 移动/写入会直接以 WinError 3 失败——所以落盘前一律先量一量。
PATH_LIMIT = 250

# 纯属噪音的下载站后缀，路径超长时优先砍它
JUNK_SUFFIX_RE = re.compile(
    r"(?:\s*\((?:[^()]*(?:z-?lib|z-library|1lib|libgen|annas-archive)[^()]*)\))+\s*$"
    r"|\s*-\s*libgen\.\w+\s*$|_zhelper-search\s*$",
    re.I,
)


def fit_path(dest, limit=PATH_LIMIT):
    """保证 dest 的完整路径不超过 limit：先去噪音后缀，还不够就截断文件名。"""
    if len(str(dest)) <= limit:
        return dest

    stem = JUNK_SUFFIX_RE.sub("", dest.stem).strip(" .")
    cand = dest.with_name(stem + dest.suffix) if stem else dest
    if len(str(cand)) <= limit:
        return cand

    room = limit - len(str(cand.parent)) - 1 - len(cand.suffix)
    if room < 20:
        return cand            # 目录本身就太深了，交给调用方报错
    return cand.with_name(cand.stem[:room].strip(" .") + cand.suffix)


def norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_course_folder(canvas_name, canvas_code, index, cfg=None, course_id=None):
    """把 Canvas 上的课程名对应到本地的课程文件夹。"""
    # 手动指定优先：config.json 里 {"course_name_map": {"课程id或名字片段": "本地文件夹"}}
    manual = (cfg or {}).get("course_name_map") or {}
    for k, folder in manual.items():
        if str(k) == str(course_id) or norm_key(k) and norm_key(k) in norm_key(canvas_name):
            return folder

    cname, ccode = norm_key(canvas_name), norm_key(canvas_code)
    for folder, meta in index["courses"].items():
        if norm_key(folder) and norm_key(folder) in cname:
            return folder
        for code in meta.get("codes", []):
            k = norm_key(code)
            if k and (k in cname or k in ccode):
                return folder
    # 退一步：关键词重合度最高的那门
    best, best_hits = None, 0
    for folder in index["courses"]:
        words = {w for w in re.findall(r"[a-z]{4,}", folder.lower())}
        hits = sum(1 for w in words if w in (canvas_name or "").lower())
        if hits > best_hits:
            best, best_hits = folder, hits
    return best if best_hits >= 2 else None


class NotLoggedIn(Exception):
    """Canvas 把我们重定向到了登录页。"""


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        # 未鉴权时 Canvas 不报错，而是回一个 200 的登录页。
        # 不拦住的话就会把一坨 HTML 当成 PDF 写进课程文件夹。
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype and dest.suffix.lower() != ".html":
            raise NotLoggedIn(r.geturl())
        with tmp.open("wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    tmp.replace(dest)


def adopt_manifest_from_downloads():
    """
    从下载夹里取最新的 canvas_manifest.json（含 (1)(2) 这类重名版本）。

    注意：必须「比本地这份新就换」，不能只在本地没有时才取。
    浏览器重新导出时会存成 canvas_manifest (1).json，
    早先的版本因为本地已有旧清单就不去看下载夹，结果一直在用过期数据。
    """
    import shutil
    dl = Path(load_config().get("downloads_dir", r"D:\Downloads"))
    if not dl.is_dir():
        return
    cands = sorted(dl.glob("canvas_manifest*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        return
    newest = cands[0]
    if MANIFEST_FILE.exists() and newest.stat().st_mtime <= MANIFEST_FILE.stat().st_mtime:
        return
    shutil.move(str(newest), str(MANIFEST_FILE))
    print("已从下载夹取来更新的清单：%s\n" % newest.name)
    # 顺手清掉下载夹里剩下的旧版本，免得越积越多
    for p in cands[1:]:
        try:
            p.unlink()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--course", help="只处理名字包含该字符串的课程")
    ap.add_argument("--show-modules", action="store_true",
                    help="只列出各课模块名和识别到的周次")
    args = ap.parse_args()

    # 浏览器会把清单下到默认下载夹，这里自动取过来，省得手动搬
    adopt_manifest_from_downloads()

    if not MANIFEST_FILE.exists():
        sys.exit(
            "找不到 %s\n\n"
            "请先在浏览器里生成清单：\n"
            "  1. Chrome 打开 %s 并确认已登录\n"
            "  2. F12 -> Console，首次粘贴需先手打 allow pasting 回车\n"
            "  3. 粘贴 canvas_snippet.js 的全部内容，回车\n"
            "  4. 生成的 canvas_manifest.json 会被自动从下载夹取走"
            % (MANIFEST_FILE,
               load_config().get("canvas_base_url") or "你学校的 Canvas")
        )
    if not INDEX_FILE.exists():
        sys.exit("找不到 %s，请先运行 build_index.py" % INDEX_FILE)

    cfg = load_config()
    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    base = Path(cfg["base_dir"])

    print("清单生成时间：%s，共 %d 门课\n" % (
        man.get("generated_at", "?"), len(man.get("courses", []))))

    name_map = {}
    if MAP_FILE.exists():
        name_map = json.loads(MAP_FILE.read_text(encoding="utf-8"))

    total_new = total_skip = total_fail = 0
    unplaced, external = [], []

    for c in man.get("courses", []):
        folder = match_course_folder(c.get("name"), c.get("course_code"), index,
                                     cfg, c.get("id"))
        if args.course and (not folder or args.course.lower() not in folder.lower()):
            if args.course.lower() not in (c.get("name") or "").lower():
                continue
        print("=== %s" % c.get("name"))
        if not folder:
            print("    对应不上本地课程文件夹，跳过。"
                  "（可在 config.json 的 course_name_map 里手动指定）\n")
            continue
        print("    -> 本地文件夹：%s" % folder)

        by_id = {f["id"]: f for f in c.get("files", [])}
        claimed = set()

        if args.show_modules:
            for m in c.get("modules", []):
                wk = detect_week(m.get("name"))
                print("    [%s] %s" % ("Week %d" % wk if wk else "无周次", m.get("name")))
            print()
            continue

        for m in c.get("modules", []):
            wk = detect_week(m.get("name"))
            if wk is None:
                n_files = sum(1 for it in m.get("items", []) if it.get("type") == "File")
                if n_files:
                    print("    [跳过模块] %s —— 模块名里没有周次（%d 个文件）"
                          % (m.get("name"), n_files))
                continue
            dest_dir = base / folder / W.week_dir(wk)

            for it in m.get("items", []):
                if it.get("type") == "ExternalUrl" and it.get("external_url"):
                    external.append((folder, wk, it["external_url"]))
                    continue
                if it.get("type") != "File":
                    continue
                f = by_id.get(it.get("content_id"))
                if not f:
                    unplaced.append((folder, it.get("title"), "清单里没有这个文件的下载链接"))
                    continue
                claimed.add(f["id"])
                fname = safe_name(f.get("display_name") or f.get("filename"))
                dest = fit_path(dest_dir / fname)
                fname = dest.name
                if dest.exists() and f.get("size") and dest.stat().st_size == f["size"]:
                    total_skip += 1
                    continue
                print("    Week %-2d  %s" % (wk, fname))
                if not args.dry_run:
                    try:
                        download(f["url"], dest)
                    except NotLoggedIn:
                        print("        [跳过] Canvas 要求登录才能下载。\n"
                              "        本校的下载链接不带签名，Python 下不了，"
                              "请用浏览器 snippet 下载（DOWNLOAD_FILES = true），\n"
                              "        再用「3 整理下载文件夹.bat」归位。")
                        return      # 一个不行就全都不行，没必要继续试
                    except urllib.error.HTTPError as e:
                        print("        [下载失败 %s] 签名可能已过期，"
                              "请重跑 canvas_snippet.js" % e.code)
                        total_fail += 1
                        continue
                    except Exception as e:
                        print("        [下载失败] %s" % e)
                        total_fail += 1
                        continue
                name_map[fname] = {"course": folder, "week": wk}
                total_new += 1

        # 挂在模块之外的文件（Files 页里有，但没被任何模块引用）
        # 不在模块里的文件：先看所在 Files 文件夹叫不叫 Week N
        fname = {d["id"]: d.get("name") or "" for d in c.get("folders", [])}
        loose, by_folder = [], {}
        for f in c.get("files", []):
            if f["id"] in claimed:
                continue
            wk = detect_week(fname.get(f.get("folder_id"), ""))
            (by_folder.setdefault(wk, []) if wk is not None else loose).append(f)

        for wk in sorted(by_folder):
            dest_dir = base / folder / W.week_dir(wk)
            for f in by_folder[wk]:
                fname_ = safe_name(f.get("display_name") or f.get("filename"))
                dest = fit_path(dest_dir / fname_)
                if dest.exists() and f.get("size") and dest.stat().st_size == f["size"]:
                    total_skip += 1
                    continue
                print("    Week %-2d  %s   (来自 Files 文件夹)" % (wk, dest.name))
                if not args.dry_run:
                    try:
                        download(f["url"], dest)
                    except NotLoggedIn:
                        print("        [跳过] 需要登录，请用浏览器 snippet 下载")
                        return
                    except Exception as e:
                        print("        [下载失败] %s" % e)
                        total_fail += 1
                        continue
                name_map[dest.name] = {"course": folder, "week": wk}
                total_new += 1

        if loose:
            print("    另有 %d 个文件既不在模块里、所在文件夹也没有周次：" % len(loose))
            for f in loose:
                print("        %s" % (f.get("display_name") or f.get("filename")))
        print()

    if external:
        print("以下是 Canvas 上以外链形式给出的材料 —— 脚本下不了，需要你手动打开：")
        seen = set()
        for folder, wk, url in external:
            key = (folder, wk, url)
            if key in seen:
                continue
            seen.add(key)
            print("  %s / Week %d\n      %s" % (folder, wk, url))
        print()

    if unplaced:
        print("以下模块条目拿不到下载链接（多半是外部链接或 Canvas Page）：")
        for folder, title, why in unplaced[:20]:
            print("  %s / %s —— %s" % (folder, title, why))
        print()

    if not args.dry_run and name_map:
        MAP_FILE.write_text(json.dumps(name_map, ensure_ascii=False, indent=1),
                            encoding="utf-8")

    print("合计：新增 %d，已存在跳过 %d，失败 %d%s" % (
        total_new, total_skip, total_fail,
        "（--dry-run，未实际下载）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
