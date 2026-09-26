/* ===========================================================================
 * canvas_snippet.js 的脱机回归测试： node test_canvas_scrape.js
 *
 * 三个用例就是我们实际踩过的三种课程形态。每个 bug 修完都在这里留一条，
 * 这样同一类错误不会再靠「让你去 Canvas 跑一遍」来发现。
 * =========================================================================== */
const S = require('./canvas_snippet.js');

let pass = 0, fail = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log((ok ? '  PASS  ' : '  FAIL  ') + name);
  if (!ok) { console.log('        实际: ' + JSON.stringify(got));
             console.log('        期望: ' + JSON.stringify(want)); fail++; }
  else pass++;
}

// 极简 DOM：只需支持 querySelectorAll('a[href*="/files/"]')
function fakeParseDoc(html) {
  const anchors = [];
  const re = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[1], text = m[2].replace(/<[^>]+>/g, '');
    const get = n => { const r = new RegExp(n + '="([^"]*)"').exec(attrs); return r ? r[1] : null; };
    anchors.push({ getAttribute: get, textContent: text });
  }
  return {
    querySelectorAll: sel => sel.includes('/files/')
      ? anchors.filter(a => (a.getAttribute('href') || '').includes('/files/'))
      : anchors,
  };
}

function makeIo(routes, pages) {
  const logs = [];
  return {
    logs,
    async getJSON(path) {
      const v = routes[path];
      if (v === undefined) return [];          // 未定义的端点当作空
      if (v === 403 || v === 404) return [];   // 被禁用/不存在
      return Array.isArray(v) ? v : [v];
    },
    async getText(url) { return (pages && pages[url]) || ''; },
    parseDoc: fakeParseDoc,
    log: (...a) => logs.push(a.join(' ')),
    warn: (...a) => logs.push('WARN ' + a.join(' ')),
  };
}

const NO_COURSES = {
  '/courses?per_page=100': [],
  '/users/self/favorites/courses?per_page=100': [],
  '/dashboard/dashboard_cards': [],
  '/users/self/enrollments?per_page=100&state[]=active': [],
  '/users/self/enrollments?per_page=100&state[]=invited': [],
  '/users/self/enrollments?per_page=100&state[]=completed': [],
};

// --------------------------------------------------------------------------
// 用例 A：Ethnographic Research —— 有模块和 File 条目，但批量 /files 返回 403
// 这正是当初「因为一门课 403 就删掉整个数据源」的场景
// --------------------------------------------------------------------------
async function caseA() {
  const io = makeIo(Object.assign({}, NO_COURSES, {
    '/courses?per_page=100': [{ id: 1, name: 'ERA' }],
    '/courses/1': { id: 1, name: 'ERA', course_code: 'ANTH 33506' },
    '/courses/1/modules?include[]=items&per_page=100': [
      { id: 10, name: 'Week 1, Sep 30: Ethnography in Historical Context',
        items: [{ type: 'File', title: 'Malinowski', content_id: 500 }] },
      { id: 11, name: 'Week 2, Oct 7: Observation',
        items: [{ type: 'File', title: 'Newton', content_id: 501 }] },
    ],
    '/courses/1/files?per_page=100': 403,      // 被禁用
    '/courses/1/folders?per_page=100': 403,
    '/courses/1/files/500': { id: 500, display_name: 'malinowski.pdf', url: '/u/500' },
    '/courses/1/files/501': { id: 501, display_name: 'newton.pdf', url: '/u/501' },
  }));
  const m = await S.buildManifest(io, {});
  const p = m.courses[0].placements;
  check('A 批量接口 403 时仍靠逐个解析拿到 2 个文件', p.length, 2);
  check('A 周次来自模块名', p.map(x => x.week), [1, 2]);
}

// --------------------------------------------------------------------------
// 用例 B：Identity and Culture in AI —— 模块里只有 Page，材料在 Page 正文里
// --------------------------------------------------------------------------
async function caseB() {
  const pageHtml = `
    <a id="900" title="geertz_1973.pdf" class="instructure_file_link"
       href="https://canvas.example.edu/courses/2/files/900?verifier=abc123&wrap=1">Thick Description</a>
    <a id="901" title="hall_1996.pdf"
       href="/courses/2/files/901?verifier=def456&wrap=1">Who Needs 'Identity'?</a>`;
  const io = makeIo(Object.assign({}, NO_COURSES, {
    '/courses/2': { id: 2, name: 'Identity and Culture in the Age of AI' },
    '/courses/2/modules?include[]=items&per_page=100': [
      { id: 20, name: 'Week 1: Symbolic technologies',
        items: [{ type: 'Page', title: '9/28 Discussion', html_url: '/p/w1' },
                { type: 'Assignment', title: 'Fieldnote 1' }] },
    ],
    '/courses/2/files?per_page=100': 403,
    '/courses/2/folders?per_page=100': 403,
    '/courses/2/files/900': 403,               // 旁听身份，files API 也不给
    '/files/900': 403,
    '/courses/2/files/901': 403,
    '/files/901': 403,
  }), { '/p/w1': pageHtml });
  const m = await S.buildManifest(io, { extraCourseIds: [2], onlyCourseIds: [2] });
  const p = m.courses[0].placements;
  check('B 从 Page 正文抽出 2 个文件', p.length, 2);
  check('B 周次来自 Page 所属模块', p.map(x => x.week), [1, 1]);
  check('B 文件名用锚点 title 兜底', p.map(x => x.display_name),
        ['geertz_1973.pdf', 'hall_1996.pdf']);
  check('B 预览链接改写成下载链接并保留 verifier',
        p[0].url, '/courses/2/files/900/download?download_frd=1&verifier=abc123');
}

// --------------------------------------------------------------------------
// 用例 C：Perspectives —— 没有模块，周次在 Files 文件夹名上
// --------------------------------------------------------------------------
async function caseC() {
  const io = makeIo(Object.assign({}, NO_COURSES, {
    '/courses?per_page=100': [{ id: 3, name: 'Perspectives' }],
    '/courses/3': { id: 3, name: 'MAPS 30000 Perspectives' },
    '/courses/3/modules?include[]=items&per_page=100': [],      // 没有模块
    '/courses/3/files?per_page=100': [
      { id: 700, display_name: 'booth.pdf', url: '/u/700', folder_id: 81 },
      { id: 701, display_name: 'abbott.pdf', url: '/u/701', folder_id: 82 },
      { id: 702, display_name: 'grade rubric.pdf', url: '/u/702', folder_id: 80 },
    ],
    '/courses/3/folders?per_page=100': [
      { id: 80, name: 'course files' }, { id: 81, name: 'Week 0' }, { id: 82, name: 'Week 1' },
    ],
  }));
  const m = await S.buildManifest(io, {});
  const p = m.courses[0].placements;
  check('C 无模块时靠文件夹名定周次', p.length, 2);
  check('C 周次正确（Week 0 / Week 1）', p.map(x => x.week), [0, 1]);
  check('C 根目录下无周次的文件不参与', p.map(x => x.display_name),
        ['booth.pdf', 'abbott.pdf']);
}

// --------------------------------------------------------------------------
// 用例 D：有文件却定不出任何周次时，必须发出警告而不是静默
// --------------------------------------------------------------------------
async function caseD() {
  const io = makeIo(Object.assign({}, NO_COURSES, {
    '/courses?per_page=100': [{ id: 4, name: 'X' }],
    '/courses/4': { id: 4, name: 'X' },
    '/courses/4/files?per_page=100': [{ id: 1, display_name: 'a.pdf', url: '/u', folder_id: 1 }],
    '/courses/4/folders?per_page=100': [{ id: 1, name: 'course files' }],
  }));
  await S.buildManifest(io, {});
  check('D 定不出周次时有警告', io.logs.some(l => l.startsWith('WARN')), true);
}

(async () => {
  console.log('canvas_snippet.js 回归测试\n');
  await caseA(); await caseB(); await caseC(); await caseD();
  console.log('\n通过 ' + pass + '，失败 ' + fail);
  process.exit(fail ? 1 : 0);
})();
