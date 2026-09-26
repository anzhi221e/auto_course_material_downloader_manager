/* ===========================================================================
 * Canvas 抓取 —— 在已登录的 Canvas 页面的控制台里粘贴运行
 *
 * 【使用步骤】
 *   1. Chrome 打开你学校的 Canvas，确认已登录
 *   2. 按 F12，切到 Console 标签
 *   3. 把本文件全部内容粘进去，回车
 *   4. 回到命令行跑：python sort_downloads.py
 *
 * 【Chrome 的粘贴警告】
 *   首次往控制台粘贴代码时，Chrome 可能拦下来（防 self-XSS）。真弹了的话，
 *   按提示**手打**（不能粘贴）这句再回车：  allow pasting
 *
 *   注意：**只有 Chrome 真的问了才打这句**。它不是命令，只是开发者工具在
 *   监听的一句确认语。没警告却打了，会看到
 *       Uncaught SyntaxError: Unexpected identifier 'pasting'
 *   这个报错无害，只说明本来就没拦你，直接粘贴脚本即可。
 *
 * 【跑之前先确认两个 Chrome 设置】
 *   - 设置 → 下载内容 →「下载前询问每个文件的保存位置」要**关闭**，
 *     否则几十个文件会弹几十次保存框
 *   - 第一个文件下载时会弹「是否允许下载多个文件？」，点**允许**；
 *     错过了就只有第一个文件会下来，重跑一次即可（已下过的会跳过）
 *
 * 为什么长这个样子（核心逻辑和浏览器 IO 分开）：
 * 这段代码以前是「盲改」的 —— 我没有 Canvas 会话，每次改完只能让你去跑，
 * 于是同一类错误反复出现：把某一门课的 403 当成全校禁用而删掉整个数据源、
 * 恢复了调用却忘了把结果并进文件表、只看 Modules 而漏掉 Files 文件夹……
 *
 * 现在核心逻辑 buildManifest() 不碰 fetch，只依赖一个注入的 io 对象，
 * 所以可以在 Node 里用假数据回归测试（见 test_canvas_scrape.js）。
 *
 * 另一条原则：**任何数据源失败都只记录、不删除**。
 * 每门课的组织方式都不一样，三种来源要全试并取并集：
 *   1. Modules -> 模块名里的 Week N        （Ethnographic Research 用这个）
 *   2. Files 文件夹 -> 文件夹名里的 Week N  （Perspectives 用这个）
 *   3. Page 正文 HTML 里的文件链接          （Identity and Culture in AI 用这个）
 * =========================================================================== */

var CanvasScrape = (function () {
  const WEEK_RE = /\b(?:week|unit|wk)\s*0*(\d{1,2})\b/i;

  function weekFromName(name) {
    const m = WEEK_RE.exec(name || '');
    return m ? parseInt(m[1], 10) : null;
  }

  // 正文里的链接是预览形式（…/files/123?verifier=…&wrap=1），直接取会拿到 HTML
  // 预览页。改写成下载形式并保留 verifier —— 有签名就不需要登录会话。
  function downloadUrl(courseId, fid, rawHref) {
    let u = '/courses/' + courseId + '/files/' + fid + '/download?download_frd=1';
    const v = /verifier=([0-9a-f-]+)/.exec(rawHref || '');
    if (v) u += '&verifier=' + v[1];
    return u;
  }

  function parseFileAnchors(html, courseId, parseDoc) {
    const doc = parseDoc(html);
    const out = [];
    const seen = new Set();
    const anchors = doc ? doc.querySelectorAll('a[href*="/files/"]') : [];
    for (const a of anchors) {
      const href = a.getAttribute('href') || '';
      const m = /\/files\/(\d+)/.exec(href);
      if (!m || seen.has(m[1])) continue;
      seen.add(m[1]);
      out.push({
        id: Number(m[1]),
        name: (a.getAttribute('title') || a.textContent || '').trim().replace(/^"|"$/g, ''),
        url: downloadUrl(courseId, m[1], href),
      });
    }
    return out;
  }

  /**
   * io 必须提供：
   *   getJSON(path)  -> 数组（自动翻页）；失败返回 []
   *   getText(url)   -> 字符串；失败返回 ''
   *   parseDoc(html) -> 类 DOM 对象，支持 querySelectorAll
   *   log(...), warn(...)
   */
  async function buildManifest(io, opts) {
    opts = opts || {};
    const only = opts.onlyCourseIds || [];
    const extra = opts.extraCourseIds || [];

    const ids = new Set(extra);
    for (const p of ['/courses?per_page=100',
                     '/users/self/favorites/courses?per_page=100',
                     '/dashboard/dashboard_cards']) {
      for (const c of await io.getJSON(p)) if (c && c.id) ids.add(c.id);
    }
    for (const st of ['active', 'invited', 'completed']) {
      for (const e of await io.getJSON('/users/self/enrollments?per_page=100&state[]=' + st)) {
        if (e && e.course_id) ids.add(e.course_id);
      }
    }

    const manifest = { generated_at: new Date().toISOString(), courses: [] };

    for (const id of ids) {
      if (only.length && !only.includes(id)) continue;
      const info = (await io.getJSON('/courses/' + id))[0];
      if (!info || !info.name) continue;

      // ---- 来源 1：Modules --------------------------------------------
      const modules = await io.getJSON('/courses/' + id + '/modules?include[]=items&per_page=100');
      for (const m of modules) {
        if (!m.items) {
          m.items = await io.getJSON('/courses/' + id + '/modules/' + m.id + '/items?per_page=100');
        }
      }

      // ---- 来源 2：Files 批量接口 + 文件夹层级 --------------------------
      const bulk = await io.getJSON('/courses/' + id + '/files?per_page=100');
      const folders = await io.getJSON('/courses/' + id + '/folders?per_page=100');
      const folderName = new Map(folders.map(d => [d.id, d.name || '']));

      // 批量接口的结果必须先并进文件表，不能只从模块条目填
      const byId = new Map();
      for (const f of bulk) if (f && f.id && f.url) byId.set(f.id, f);

      // ---- 来源 3：Page 正文里的文件链接 --------------------------------
      const pageFiles = [];       // [{fileId, name, url, moduleName}]
      for (const m of modules) {
        for (const it of (m.items || [])) {
          if (it.type !== 'Page' || !it.html_url) continue;
          const html = await io.getText(it.html_url);
          if (!html) continue;
          for (const a of parseFileAnchors(html, id, io.parseDoc)) {
            pageFiles.push({ fileId: a.id, name: a.name, url: a.url, moduleName: m.name });
          }
        }
      }

      // 模块条目引用到、但批量接口没给的文件，逐个补查；查不到就用页面锚点兜底
      const fromPage = new Map(pageFiles.map(p => [p.fileId, p]));
      const wanted = new Set();
      for (const m of modules) {
        for (const it of (m.items || [])) {
          if (it.type === 'File' && it.content_id) wanted.add(it.content_id);
        }
      }
      for (const p of pageFiles) wanted.add(p.fileId);

      let resolved = 0, failed = 0;
      for (const fid of wanted) {
        if (byId.has(fid)) continue;
        const got = (await io.getJSON('/courses/' + id + '/files/' + fid))[0]
                 || (await io.getJSON('/files/' + fid))[0];
        if (got && got.url) { byId.set(got.id, got); resolved++; continue; }
        const p = fromPage.get(fid);
        if (p) {
          byId.set(fid, { id: fid, display_name: p.name, filename: p.name,
                          size: null, url: p.url, from_anchor: true });
          resolved++;
        } else { failed++; }
      }

      // ---- 归属计算：每个文件属于哪一周、必读还是选读 --------------------
      // 优先级：模块 > Page 所属模块 > Files 文件夹。
      // 模块是老师主动组织的教学顺序，文件夹更像存储布局。
      const place = new Map();
      const put = (fid, wk, src) => {
        if (wk === null || wk === undefined) return;
        if (!place.has(fid)) place.set(fid, { week: wk, source: src });
      };
      for (const m of modules) {
        const wk = weekFromName(m.name);
        for (const it of (m.items || [])) {
          if (it.type === 'File' && it.content_id) put(it.content_id, wk, '模块 ' + m.name);
        }
      }
      for (const p of pageFiles) put(p.fileId, weekFromName(p.moduleName), '模块 ' + p.moduleName);
      for (const f of byId.values()) {
        put(f.id, weekFromName(folderName.get(f.folder_id) || ''),
            'Files 文件夹 ' + (folderName.get(f.folder_id) || ''));
      }

      const files = [...byId.values()];
      const placements = files
        .filter(f => place.has(f.id))
        .map(f => ({ file_id: f.id, display_name: f.display_name || f.filename,
                     url: f.url, week: place.get(f.id).week,
                     source: place.get(f.id).source }));

      io.log(id + '  ' + info.name);
      io.log('   模块 ' + modules.length + ' | Files 接口 ' + bulk.length
           + ' | 文件夹 ' + folders.length + ' | Page 内链接 ' + pageFiles.length
           + ' | 合计文件 ' + files.length + ' | 定出周次 ' + placements.length
           + (failed ? ' | 取不到 ' + failed : ''));
      if (modules.length) io.log('   模块名：' + modules.map(m => m.name).join(' | '));
      const wkf = folders.filter(d => weekFromName(d.name));
      if (wkf.length) io.log('   周文件夹：' + wkf.map(d => d.name).join(' | '));
      // 有数据却一个周次都定不出来，八成是漏了某个来源，明确报警而不是静默
      if (files.length && !placements.length) {
        io.warn('   [!] 有 ' + files.length + ' 个文件但定不出任何周次，请把这行发给 Claude');
      }
      if (!files.length && (modules.length || folders.length)) {
        io.warn('   [!] 有模块/文件夹但一个文件都没拿到，请把这行发给 Claude');
      }

      manifest.courses.push({
        id: id, name: info.name, course_code: info.course_code || '',
        files: files.map(f => ({ id: f.id, display_name: f.display_name,
                                 filename: f.filename, size: f.size, url: f.url,
                                 folder_id: f.folder_id })),
        folders: folders.map(d => ({ id: d.id, name: d.name, full_name: d.full_name })),
        modules: modules.map(m => ({
          id: m.id, name: m.name,
          items: (m.items || []).map(i => ({ type: i.type, title: i.title,
                                             content_id: i.content_id,
                                             html_url: i.html_url })),
        })),
        placements: placements,
      });
    }
    return manifest;
  }

  return { buildManifest, weekFromName, parseFileAnchors, downloadUrl };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = CanvasScrape;                      // Node 回归测试用
} else {
  // ======================= 浏览器里的实际运行 =========================
  (async () => {
    // 课程列表接口漏掉的课，手动补 id（课程页地址栏 /courses/XXXXX 里那串数字）
    const EXTRA_COURSE_IDS = [];
    // 只处理这几门；留空 = 全部
    const ONLY_COURSE_IDS = [];
    const DOWNLOAD_FILES = true;
    const DELAY_MS = 1200;

    const io = {
      async getJSON(path) {
        let url = path.startsWith('http') ? path : '/api/v1' + path;
        const out = [];
        while (url) {
          let r;
          try { r = await fetch(url, { credentials: 'same-origin' }); } catch (e) { break; }
          if (!r.ok) { console.warn('   [' + r.status + '] ' + url); break; }
          const d = await r.json();
          out.push(...(Array.isArray(d) ? d : [d]));
          const m = (r.headers.get('Link') || '').split(',')
            .map(s => /<([^>]+)>;\s*rel="next"/.exec(s)).find(Boolean);
          url = m ? m[1] : null;
        }
        return out;
      },
      async getText(url) {
        try {
          const r = await fetch(url, { credentials: 'same-origin' });
          return r.ok ? await r.text() : '';
        } catch (e) { return ''; }
      },
      parseDoc: html => new DOMParser().parseFromString(html, 'text/html'),
      log: (...a) => console.log(...a),
      warn: (...a) => console.warn(...a),
    };

    const manifest = await CanvasScrape.buildManifest(io, {
      onlyCourseIds: ONLY_COURSE_IDS, extraCourseIds: EXTRA_COURSE_IDS,
    });

    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 1)],
      { type: 'application/json' }));
    a.download = 'canvas_manifest.json';
    document.body.appendChild(a); a.click(); a.remove();
    console.log('\n✅ 清单已导出');

    if (!DOWNLOAD_FILES) return;
    const queue = [];
    for (const c of manifest.courses) {
      for (const p of c.placements) {
        if (!queue.some(q => q.url === p.url)) queue.push(p);
      }
    }
    if (!queue.length) { console.log('没有可下载的文件'); return; }
    console.log('\n开始下载 ' + queue.length + ' 个，约 '
      + Math.ceil(queue.length * DELAY_MS / 1000) + ' 秒。浏览器会问是否允许下载多个文件，点允许。\n');
    for (let i = 0; i < queue.length; i++) {
      const el = document.createElement('a');
      el.href = queue[i].url; el.download = queue[i].display_name || '';
      el.style.display = 'none';
      document.body.appendChild(el); el.click(); el.remove();
      console.log('  [' + (i + 1) + '/' + queue.length + '] Week ' + queue[i].week
        + '  ' + queue[i].display_name);
      await new Promise(r => setTimeout(r, DELAY_MS));
    }
    console.log('\n✅ 下载已触发完，回去双击「3 整理下载文件夹.bat」');
  })();
}
