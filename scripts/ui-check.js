const { chromium } = require('playwright');
const path = require('path');
const { pathToFileURL } = require('url');
const WIDGET = pathToFileURL(path.join(__dirname, '..', 'ui', 'widget.html')).href;
const SETTINGS = pathToFileURL(path.join(__dirname, '..', 'ui', 'settings.html')).href;

const png1 = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

const PHOTOS = (urlMode) => {
  const mk = (id, name, kind, stage) => ({
    id, name, mtime: 1763000000, kind, size: 10,
    url: urlMode === 'bad' ? 'http://127.0.0.1:1/m/tok/' + stage + '/' + id + '?v=1' : '',
  });
  return [
    mk('0::a.png', 'Первый кадр', 'image', 'full'),
    mk('0::clip.mp4', 'Клип', 'video', 'raw'),
    mk('0::b.png', 'Второй кадр', 'image', 'full'),
  ];
};

const mkBridge = (mode) => {
  const stateOk = mode !== 'error';
  const photos = PHOTOS(mode === 'badurl' ? 'bad' : 'ok');
  const cfg = {
    folders: ['C:\\pics'],
    interval: 2, shuffle: false, transition: 'fade', frame: 'white',
    padding: 16, radius: 8, fit: 'contain', caption: 'name', recursive: false,
    video_mode: 'once',
  };
  return `
    window.__setConfigCalls = [];
    const state = ${JSON.stringify({ ok: stateOk, count: photos.length, photos })};
    window.astra = {
      loadUi: () => Promise.resolve({}),
      callBackend: (method, params) => {
        if (method === 'pf_state') {
          if (!${JSON.stringify(stateOk)}) return Promise.resolve({ ok: false, err: 'Папки не найдены: X', count: 0 });
          return Promise.resolve(state);
        }
        if (method === 'pf_photo') {
          if (params.stage === 'raw') return Promise.resolve({ ok: true, kind: 'video', data: 'data:video/mp4;base64,AAAAIGZ0aXBpc29tcA==' });
          if (params.id.endsWith('a.png')) return Promise.resolve({ ok: true, kind: 'image', data: '${png1}' });
          if (params.id.endsWith('b.png')) return Promise.resolve({ ok: true, kind: 'image', data: '${png1}' });
          return Promise.resolve({ ok: false, err: 'no such id' });
        }
        if (method === 'pf_caption') return Promise.resolve({ text: 'подпись' });
        if (method === 'pf_pick') return Promise.resolve({ ok: true, status: 'started' });
        if (method === 'pf_pick_result') {
          if (!window.__pickOnce) { window.__pickOnce = 1; return Promise.resolve({ ok: true, status: 'picking' }); }
          return Promise.resolve({ ok: true, status: 'done', path: 'D:\\\\Photos\\\\Отпуск' });
        }
        return Promise.resolve({ ok: true });
      },
      widget: {
        getContext: () => Promise.resolve({
          widgetId: 'photo-frame-widget', instanceId: 'i1', formatId: 'frame', viewId: '',
          preview: ${JSON.stringify(mode === 'preview')}, active: true, width: 640, height: 420,
          config: ${JSON.stringify(cfg)},
        }),
        onContextChange: () => () => {},
        setConfig: (obj) => { window.__setConfigCalls.push(obj); return Promise.resolve(); },
      },
    };
  `;
};

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const run = async (page) => { page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));
    page.on('console', m => { const t = String(m.text());
      if (m.type() === 'error' && !/ERR_CONNECTION_REFUSED|ERR_INVALID_URL|Failed to load media|net::ERR|Not allowed to load local resource/.test(t)) errors.push('CONSOLE ' + t); }); };
  const fs = require('fs');
  const shot = async (page, name) => { fs.mkdirSync('.tmp-ui-check', { recursive: true });
    await page.screenshot({ path: `.tmp-ui-check/${name}.png` }); };
  for (const theme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: 640, height: 420 }, colorScheme: theme });
    run(page);
    await page.addInitScript(mkBridge('normal'));
    await page.goto(WIDGET);
    await page.waitForFunction(() => {
      if (!['Первый', 'Второй'].some(s => String(document.getElementById('caption').textContent).includes(s)))
        return false;
      return document.querySelectorAll('img.layer').length >= 1;
    }, { timeout: 6000, polling: 200 });
    const cap1 = await page.$eval('#caption', e => e.textContent);
    if (!['Первый', 'Второй'].some(s => String(cap1).includes(s))) errors.push(`${theme}: first caption wrong: ` + cap1);
    await shot(page, `widget-first-${theme}`);
    const changed = await page.waitForFunction((prev) => {
      const c = String(document.getElementById('caption').textContent);
      return !c.includes(prev) && ['Первый', 'Второй'].some(s => c.includes(s));
    }, cap1, { timeout: 9000, polling: 200 }).then(() => true).catch(() => false);
    if (!changed) errors.push(`${theme}: смена кадра не произошла (caption=${cap1})`);
    const vidCount = await page.evaluate(() => document.querySelectorAll('video.layer').length);
    if (vidCount) errors.push(`${theme}: битое видео прикреплено к DOM`);
    await shot(page, `widget-second-${theme}`);
    console.log(`widget ${theme}: мост, подписи, пропуск битого видео, смена — ок (${cap1} → ${changed})`);
    await page.close();
  }

  const bad = await browser.newPage({ viewport: { width: 640, height: 420 } });
  run(bad);
  await bad.addInitScript(mkBridge('badurl'));
  await bad.goto(WIDGET);
  await bad.waitForFunction(() => {
    const c = String(document.getElementById('caption').textContent);
    return ['Первый', 'Второй'].some(s => c.includes(s)) && document.querySelectorAll('img.layer').length >= 1;
  }, { timeout: 8000, polling: 200 });
  const badCap = await bad.$eval('#caption', e => e.textContent);
  if (!['Первый', 'Второй'].some(s => String(badCap).includes(s))) errors.push('badurl: caption не через мост-фолбэк: ' + badCap);
  console.log('битый url медиасервера корректно уходит в мост-фолбэк');
  await bad.close();

  const pv = await browser.newPage({ viewport: { width: 320, height: 220 } });
  run(pv);
  await pv.addInitScript(mkBridge('preview'));
  await pv.goto(WIDGET);
  await pv.waitForTimeout(500);
  if (!(await pv.$eval('#empty', e => e.textContent)).includes('рабочем')) errors.push('preview placeholder missing');
  await shot(pv, 'widget-preview');
  console.log('preview: плейхолдер — ок');
  await pv.close();

  const er = await browser.newPage({ viewport: { width: 320, height: 220 } });
  run(er);
  await er.addInitScript(mkBridge('error'));
  await er.goto(WIDGET);
  await er.waitForTimeout(500);
  if (!(await er.$eval('#empty', e => e.textContent)).includes('не найдены')) errors.push('error text missing');
  await shot(er, 'widget-error');
  console.log('ошибка папок показана корректно');
  await er.close();

  const st = await browser.newPage({ viewport: { width: 640, height: 620 } });
  run(st);
  await st.addInitScript(mkBridge('normal'));
  await st.goto(SETTINGS);
  await st.waitForSelector('#folds .frow input');
  await st.click('#transition button[data-v="kenburns"]');
  await st.click('#frame button[data-v="dark"]');
  await st.fill('#interval', '45');
  await st.dispatchEvent('#interval', 'change');
  await st.click('#manualBtn');
  const rows = await st.$$('#folds .frow input');
  await rows[rows.length - 1].fill('E:\\Рисунки');
  await rows[rows.length - 1].dispatchEvent('change');
  await st.click('#pickBtn');
  await st.waitForTimeout(1600);
  await st.click('#check');
  await st.waitForTimeout(400);
  const status = await st.$eval('#status', e => e.textContent);
  if (!status.includes('3')) errors.push('check status wrong: ' + status);
  const calls = await st.evaluate(() => window.__setConfigCalls);
  if (!calls.some(c => c.transition === 'kenburns')) errors.push('setConfig transition missing');
  if (!calls.some(c => c.frame === 'dark')) errors.push('setConfig frame missing');
  if (!calls.some(c => c.interval === 45)) errors.push('setConfig interval missing');
  const okFolders = calls.filter(c => Array.isArray(c.folders) && c.folders.includes('E:\\Рисунки')
    && c.folders.includes('D:\\Photos\\Отпуск'));
  if (!okFolders.length) errors.push('setConfig folders (manual+native) missing: ' + JSON.stringify(calls.map(c => c.folders)));
  await shot(st, 'settings');
  console.log(`настройки: seg + папки + нативный выбор + счёт — ок (setConfig ${calls.length} раз)`);
  await st.close();

  const spv = await browser.newPage({ viewport: { width: 560, height: 560 } });
  run(spv);
  await spv.addInitScript(mkBridge('preview'));
  await spv.goto(SETTINGS);
  await spv.waitForTimeout(400);
  const disabledCount = await spv.$$eval('input,select,button', els => els.filter(e => e.disabled).length);
  if (disabledCount < 10) errors.push('preview inputs not disabled: ' + disabledCount);
  await shot(spv, 'settings-preview');
  console.log('настройки превью: контролы задизейблены');
  await spv.close();

  await browser.close();
  if (errors.length) { console.log('FAIL'); errors.forEach(e => console.log(' -', e)); process.exit(1); }
  console.log('БЕНД ОК: виджет, мост, видео, фолбэки, подписи, превью, ошибки, настройки');
})();
