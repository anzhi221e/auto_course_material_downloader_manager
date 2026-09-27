# 课程材料自动下载与归档

[English](README.md)

从 Canvas 和 Dropbox 自动下载课程材料，并自动按周归档。

把阅读材料按 **syllabus 实际指定的内容** 归入 `<课程>/Week 03_1012_1018/`，
区分必读和选读，并告诉你还缺什么。

针对 Canvas 开发，但所有与学校、学期相关的值都集中在一个配置文件里。

---

## 它解决什么问题

下载一学期的阅读材料不难，难的是：

- **这个 PDF 属于第几周？** `3034157.pdf`、`hall_1996.pdf` 这种文件名什么也看不出来
- **是必读还是选读？** syllabus 上标着，下载下来就没了
- **还缺什么？** 手里有 100 个文件不等于齐了
- **老师的组织方式不统一。** 模块、Files 文件夹、写在页面正文里的链接、Dropbox——
  同一个学校里都可能并存

这个工具把 syllabus 解析成索引，从老师实际放材料的地方取回来，然后两边对账。

---

## 核心设计原则

> **有权威依据就不猜；两者都不确定时，什么都不做。**

下载夹是系统默认的，里面混着报税单、发票、截图。所以归类采用白名单逻辑，
按四个层级判定：

| 层级 | 依据 | 确定性 |
|---|---|---|
| 1 | 抓取时从 Canvas 结构直接读到并记录的归属 | 精确 |
| 2 | 文件名对上了带周次的模块/文件夹里的文件 | 精确 |
| 3 | 文件被改过名，但和已知条目重合 ≥75% | 较高 |
| 4 | syllabus 匹配：**必须**有作者姓氏，再加标题词覆盖 | 打分 |

第 4 层的最佳候选若没有领先次佳足够分差，文件**原地不动**并列入待定清单。
失败形态是拒绝，不是错放。

---

## 快速上手

### 最快的路：跑初始化向导

把这学期的 syllabus 都放进一个文件夹，然后：

```bash
pip install pymupdf python-docx
python setup_wizard.py
```

它会：

- 问你课程文件夹建在哪、下载夹在哪
- 扫描那个文件夹，自动跳过不是 syllabus 的文件
- 猜每门课的名字，并**逐个让你确认**
- 四种解析器全试一遍，选解析效果最好的那个
- **从 syllabus 里的上课日期反推学期日历**——包括停课周，靠相邻两周的日期跳变识别
- 建好每门课的文件夹、每一周的文件夹、以及作业归档文件夹
- 替你写出 `courses.json`

> **请核对它给出的课程名。** 这是从 syllabus 正文猜的，PDF 断行会让它猜偏。
> 文件夹名一错，后面所有归类都跟着错，所以向导会停下来逐个问你。

然后验证并建立索引：

```bash
python weeks.py            # 核对推断出的日历和你的实际课表
python build_index.py      # 解析 syllabus 建立索引
```

Windows：双击 `0 初始化向导.bat` 即可。

如果向导哪里猜错了，或者你更想手动配置，见下面的手动流程。

---

### 手动配置

#### 第 1 步 — 环境

```bash
python --version            # 需要 3.9+
pip install pymupdf python-docx
node --version              # 可选，只用于跑 JS 测试
```

#### 第 2 步 — 建立配置

```bash
cp courses.example.json courses.json
```

然后编辑 `courses.json`。**这是唯一需要你改的文件。**

#### 第 3 步 — 设置学期日历

```jsonc
"term": {
  "week1_monday": "2026-09-28",
  "week_count": 10,
  "has_week0": false,
  "breaks": [ { "name": "Thanksgiving", "monday": "2026-11-23" } ]
}
```

**选择：quarter 制还是 semester 制？**

| 学制 | `week_count` |
|---|---|
| quarter（学季） | 10–11 |
| semester（学期） | 14–16 |

**选择：第一周之前有 orientation 周吗？**
设 `has_week0: true`，会多出一个 `Week 00_…` 文件夹。

> ⚠️ **停课周是最容易出错的地方。** 周次**不是**"上周 +7 天"。
> 一个停课周会让之后所有周整体错一位。把所有停课都列进去——感恩节、
> 秋假、春假、reading week——编号就会正确跳过它们。

继续之前先验证：

```bash
python weeks.py
```

它会打印算出来的日历。**请核对第 1 周，以及每个停课周之后的那一周**，
和你的实际课表对照。后面所有环节都依赖这个日历。

#### 第 4 步 — 描述你的课程

每门课一条。`folder` 必须和磁盘上的文件夹名完全一致。

```jsonc
{
  "folder": "Ethnographic Research",
  "syllabus": "Syllabus ANTH 33506.pdf",
  "parser": "era",
  "canvas_course_id": "12345",
  "has_week0": false,
  "public": false
}
```

**选择：用哪个 `parser`？** 打开 syllabus，看它的周标题长什么样：

| 你的 syllabus 写成 | `parser` |
|---|---|
| `Week 1 (Sep 30) Ethnography in Context` | `era` |
| `Week 1: Symbolic technologies`，且有 `Required Readings:` 小节 | `ai` |
| `WEEK 1 (9/29, 10/1): NARRATIVES` | `persp` |
| 不标周次，按上课日期分段，如 `9/29, Tuesday – 主题` | `linc` |

四种都不像？见[增加 syllabus 格式](#增加-syllabus-格式)。

**选择：这门课的材料放在哪？** 这决定你用哪个工具，见下一节。

#### 第 5 步 — 建立索引

```bash
python build_index.py
```

它会打印每周解析出多少条阅读。**请对着 syllabus 核对这些数字。**
如果某周是 0 或者明显不对，多半是 `parser` 选错了。

---

## 选择取材方式

老师分发材料至少有五种方式。找到你这门课属于哪种：

| 你在课程里看到的 | 用 | 配置 |
|---|---|---|
| 模块名叫 `Week 3`，下面挂着文件 | `canvas_snippet.js` | `canvas_course_id` |
| 没有模块，但 **Files** 里有叫 `Week 3` 的文件夹 | `canvas_snippet.js` | 同上 |
| 模块里只有 Page，阅读材料是**写在页面正文里**的链接 | `canvas_snippet.js` | 同上 |
| 课程**对外公开**，你在旁听 / 没有正式选课 | `fetch_public_course.py` | `"public": true` |
| 给的是 Dropbox / Drive 文件夹，或者 Canvas 上压根没有 | `ingest_zip.py` | `canvas_course_id: null` |

前三种是**自动识别**的——三个来源并行尝试、取并集，任何一个失败都不会
影响其余的。你不需要事先知道自己的课属于哪一种。

### Canvas 课程（走浏览器）

```
1. 在 Chrome 里打开你的 Canvas，确认已登录
2. F12 → Console
3. 粘贴 canvas_snippet.js 的全部内容，回车
4. 运行：python sort_downloads.py
```

#### 关于 Chrome 的粘贴警告

第一次往开发者工具的控制台粘贴代码时，Chrome 可能会拦下来并弹出警告
（这是防 self-XSS 的保护）。真的弹了的话，它会要求你**手打**——不是粘贴——
下面这句，然后回车：

```
allow pasting
```

有两个地方容易踩：

- **必须手动打字。** 粘贴这句话本身就绕过了这道保护的意义，所以 Chrome 不认
  粘贴来的副本。
- **只有 Chrome 真的问了才打。** `allow pasting` 不是命令，只是开发者工具在
  监听的一句确认语。如果它压根没警告，你却打了这句，会看到：

  ```
  Uncaught SyntaxError: Unexpected identifier 'pasting'
  ```

  **这个报错无害**，只说明 Chrome 本来就没拦你——直接粘贴脚本就行。

允许过一次之后，这个 Chrome 配置文件就一直有效，不会再问。

#### 另外两个 Chrome 设置值得先确认

脚本会连续下载几十个文件，所以跑之前：

- **设置 → 下载内容 → "下载前询问每个文件的保存位置"** 必须**关闭**，
  否则每个文件都会弹一次保存框。
- 第一个文件下载时会弹出**"是否允许下载多个文件？"**的权限条，点**允许**。
  如果错过了，只有第一个文件会下来——重跑一次脚本即可，已下过的会自动跳过。

脚本用你**现有的登录会话**导出清单并触发下载。**不创建、不保存任何访问令牌。**
很多学校关闭了个人 API 令牌，这条路仍然可行。

改 snippet 顶部的常量可以限定范围：

```js
const ONLY_COURSE_IDS = [];      // [] = 全部课程；[12345] = 只处理这一门
const EXTRA_COURSE_IDS = [];     // 课程列表接口漏掉的课，手动补 id
const DOWNLOAD_FILES = true;     // false = 只导出清单，不下载
```

### 公开课程（不需要浏览器）

如果课程对外可读，它的文件链接自带签名、不依赖登录会话，Python 可以全程直连：

```bash
python fetch_public_course.py --all-public          # 预演
python fetch_public_course.py --all-public --apply  # 下载
```

把这些课设成 `"public": true`。老师发布新的一周后重跑即可，已下过的自动跳过。

### Dropbox / Drive 压缩包

把整个文件夹打包下载（Dropbox 把分享链接末尾的 `dl=0` 改成 `dl=1`），然后：

```bash
python ingest_zip.py "路径/archive.zip" --course "课程文件夹" --list   # 看结构
python ingest_zip.py "路径/archive.zip" --course "课程文件夹"          # 预演
python ingest_zip.py "路径/archive.zip" --course "课程文件夹" --apply
```

它优先用压缩包**自己的文件夹结构**——`Week 3`、`10.13`、`Oct 27`、`October 13`
都认——对不上再退回 syllabus 匹配。**只处理你指定的那一个压缩包**，
绝不自动扫描下载夹里的压缩文件。

---

## 日常使用

| 命令 | 作用 |
|---|---|
| `python sort_downloads.py` | 归类下载夹里的文件 |
| `python sort_downloads.py --coverage` | **对着 syllabus 逐周对账** |
| `python sort_downloads.py --undo` | 撤销上一批移动 |
| `python reorganize.py --apply` | 规则或 syllabus 变动后，重排已归档的文件 |
| `python build_index.py` | syllabus 更新后重建索引 |

Windows 用户：带编号的 `.bat` 文件是这些命令的双击入口。

`--coverage` 是最该定期跑的：

```
  ✓ Week 02_1005_1011  必读 5/5   选读 1/2   （文件夹里 9 个）
  ! Week 03_1012_1018  必读 2/4   选读 1/3   （文件夹里 7 个）
        缺(必读): Marsilli-Vargas 2022 — Genres of Listening
```

**归类正确不等于材料齐全。** 这是唯一能发现"某篇悄无声息漏掉了"的手段。

---

## 必读 / 选读怎么判定

必读放周文件夹，选读放 `Week 03_…/Additional Readings/`。
**你自己的作业放 `Week 03_…/Assignments/`，所有脚本都永不触碰那里。**

```
Ethnographic Research/
├── Week 03_1012_1018/
│   ├── reading.pdf              <- 必读
│   ├── Additional Readings/     <- 选读
│   └── Assignments/             <- 你的东西。不扫、不计数、不移动
└── Submitted Assignments Archive/
```


按你的 syllabus 实际使用的方式识别：

- **小节标题** —— `Required Readings:`、`Further Readings (optional):`、`Optional/Provided:`
- **逐条标记** —— `**` 必读、`*` 强烈推荐、`+` 选读
- **Canvas 页面结构** —— 阅读材料以链接形式写在页面正文时，看正文里的标题

**同一篇文献被跨周指定时，每一周都会放一份**，这样打开任意一周的文件夹，
那周要读的东西都齐。

---

## 配置参考

### 周次叫法

默认认得：`Week 3`、`Unit 3`、`Wk 3`、`Session 3`、`Topic 3`、`第3周`。
可以自己加（正则里要有一个捕获组取数字）：

```jsonc
"week_patterns": ["\\bSemaine\\s*(\\d{1,2})\\b", "\\bLecture\\s*(\\d{1,2})\\b"]
```

### 匹配门槛

```jsonc
"matching": {
  "min_score": 8.0,       // 提高 → 更严格；降低 → 更激进
  "margin": 2.0,          // 最佳必须领先次佳这么多分
  "max_candidates": 10,   // 命中超过这个数的文件判为书目，不处理
  "min_age_seconds": 30   // 忽略还在写入的文件
}
```

默认值刻意偏保守。如果该匹配的文件被跳过了，把 `min_score` 降到 5 左右；
如果出现任何错放，提高 `margin`。**分数已归一化**，所以不管你的索引是
50 条还是 500 条，这些数值含义一致。

### 路径

```jsonc
"paths": {
  "base_dir": "",                                  // "" = 本文件夹的上一级
  "downloads_dir": "C:\\\\Users\\\\用户名\\\\Downloads"
}
```

### 增加 syllabus 格式

`build_index.py` 里每种格式一个切分函数，各约 3 行。复制最接近的那个，
改成你的周标题正则，注册进 `SPLITTERS`，然后去 `test_sorting.py` 加一条用例。

### 增加取材来源

`canvas_snippet.js` 暴露 `buildManifest(io, opts)`，其中 `io` 只是
`{getJSON, getText, parseDoc, log, warn}`——不碰全局，也不碰 fetch。
加一个来源就是加一个往 `byId`（文件）和 `place`（每个文件的周次）里写的循环，
照着现有三个写。然后去 `test_canvas_scrape.js` 加一条 fixture。

---

## 安全保证

下载夹和你所有其它下载混在一起，所以：

- **`Assignments/` 永不进入。** 不扫描、不计数、不移动，任意层级都一样——
  你的作业稿就算取名和某篇阅读一样，也不会被动
- syllabus 里的作者姓氏**必须**出现，否则什么都不搬
- 只考虑配置里列出的扩展名，`.exe`、`.zip`、`.jpg` 压根不进候选
- 只扫顶层，不递归进子文件夹
- 跳过 `.crdownload` / `.part` 和 30 秒内修改过的文件
- 命中条目数超过 `max_candidates` 的文件判为书目，原地不动
- **文件名证据高于正文。** 文件**名叫**某篇文献，它就是那篇；
  正文里**引用**了某篇文献，它不是。只有文件名是 `3034157.pdf`
  这类无意义编号时才去读正文
- 先预演、确认、再移动。每次移动都记流水账，`--undo` 可撤销
- 路径超长自动缩短文件名（Windows `MAX_PATH` 是 260）
- 重复下载（`file (1).pdf`）会被识别，不会归档两份

---

## 测试

```bash
python test_sorting.py        # 37 项
node test_canvas_scrape.js    # 10 项
```

不需要网络，不依赖任何个人数据。每一条用例都对应一个真实发生过的 bug，包括：

- 第 7 周指定的某一章被归到第 2 周，因为文件名里同时含有书名
- 带连字符的姓氏（`Lévi-Strauss`）永远匹配不上，因为索引存了连字符，
  而文件名分词只产生纯字母
- 阈值是对着 300 条索引调的，换成更小的索引就静默拒判一切——
  现在分数与语料规模无关
- syllabus 里的导读散文被当成引文，虚增了"缺失"清单
- 因为**一门**课返回 403 就删掉了整个数据源，连带弄坏了所有依赖它的课

**改匹配规则之前，先加用例。**

---

## 隐私

不提交任何账号相关数据。`.gitignore` 排除了：

| 文件 | 原因 |
|---|---|
| `canvas_manifest.json` | **含带签名的下载链接**——公开它等于把课程材料的访问权放出去 |
| `readings_index.json` | 老师 syllabus 的完整摘录 |
| `moves_log.csv` | 绝对路径，含你的用户名 |
| `courses.json`、`config.json`、`file_titles.json` | 本地路径和课程结构 |

不向任何地方上传数据。不创建也不保存访问令牌——浏览器脚本跑在你现有的
登录会话里，公开课程抓取用的是服务器本来就发出的签名。

---

## 排查

| 现象 | 可能原因 |
|---|---|
| `build_index.py` 某周解析出 0 条 | 那门课的 `parser` 选错了 |
| 假期之后的周文件夹日期不对 | `breaks` 里漏了一个停课周 |
| 所有文件都进了"定不了"清单 | 索引偏小，默认阈值太严——降低 `min_score` |
| Canvas 的 files 接口返回 403 | 正常，脚本会改用逐个文件查询 |
| snippet 报告有文件但定不出周次 | 模块/文件夹名对不上任何 `week_patterns` |
| `SyntaxError: Unexpected identifier 'pasting'` | Chrome 没拦你，你却打了 `allow pasting`。无害，直接粘贴脚本 |
| 只下来了第一个文件 | 错过了「是否允许下载多个文件」的权限条，点允许后重跑 |
| 每个文件都弹保存对话框 | 到 Chrome 设置里关掉「下载前询问每个文件的保存位置」 |

---

## 许可

MIT，见 [LICENSE](LICENSE)。
