/* Classroom Booking R21: conservative near-real-time availability/stock sync.
 * Adds only read-only polling, targeted DOM updates and pre-submit checks.
 * Django remains authoritative for concurrent reservations and stock accounting.
 */
(() => {
  'use strict';
  const currentPath = location.pathname;
  const schedule = /^\/schedule\/?$/.test(currentPath) && !!document.querySelector('[data-v3-cell]');
  const furniture = /^\/furniture\/?$/.test(currentPath) && !!document.querySelector('.v3-inventory');
  if (!schedule && !furniture || !window.fetch || !window.DOMParser) return;

  const PERIOD_MS = 5000;
  const FETCH_TIMEOUT_MS = 10000;
  const bypass = new WeakSet();
  const pending = new WeakSet();
  let inFlight = null;
  let lastGood = 0;
  const node = document.createElement('div');
  node.id = 'r21-live-status';
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');
  node.textContent = '正在同步最新状态…';
  const anchor = document.querySelector('.page-heading') || document.querySelector('main');
  if (anchor && anchor.parentNode) anchor.insertAdjacentElement('afterend', node);
  else document.body.insertBefore(node, document.body.firstChild);

  function notice(text, kind = '') {
    node.classList.toggle('r21-live-warning', !!kind);
    node.textContent = text || ('实时数据已同步 · ' + new Date(lastGood).toLocaleTimeString());
  }
  function dialogWarning(message) {
    const dialog = document.querySelector('#v3-quick-dialog');
    if (!dialog || !dialog.open) return;
    const feedback = dialog.querySelector('#v3-quick-feedback');
    if (feedback) { feedback.textContent = message; feedback.hidden = false; }
  }
  const key = cell => cell.getAttribute('data-v3-date') + '\u0001' + cell.getAttribute('data-v3-period');
  const stockInfo = doc => {
    const rows = doc.querySelectorAll('.v3-inventory table tbody tr');
    const result = new Map();
    rows.forEach(row => {
      if (row.cells.length < 4) return;
      const name = row.cells[0].textContent.trim();
      const count = Number(row.cells[3].textContent.trim());
      if (name && Number.isFinite(count)) result.set(name, count);
    });
    return result;
  };

  async function getFresh() {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const resp = await fetch(location.href, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'follow',
        signal: ctrl.signal, headers: {'Accept': 'text/html', 'X-R21-Live-Refresh': '1'}
      });
      if (!resp.ok || !(resp.headers.get('content-type') || '').toLowerCase().includes('text/html'))
        throw new Error('无法获取新的页面状态');
      const redirected = new URL(resp.url, location.href);
      if (redirected.origin !== location.origin || redirected.pathname !== currentPath)
        throw new Error('页面已重定向（可能需要重新登录）');
      const doc = new DOMParser().parseFromString(await resp.text(), 'text/html');
      if (schedule && !doc.querySelector('[data-v3-cell]')) throw new Error('课表结构已变化');
      if (furniture && !doc.querySelector('.v3-inventory table tbody')) throw new Error('库存表结构已变化');
      return doc;
    } finally { clearTimeout(timeout); }
  }

  function replaceTableBody(selector, doc) {
    const old = document.querySelector(selector + ' table tbody');
    const fresh = doc.querySelector(selector + ' table tbody');
    if (!old || !fresh) return false;
    if (old.innerHTML === fresh.innerHTML) return false;
    old.replaceChildren(...Array.from(fresh.childNodes, child => document.importNode(child, true)));
    return true;
  }

  function syncInventoryForm(doc) {
    const source = doc.querySelector('[data-furniture-form] select[name="item_name"]');
    if (!source) return;
    document.querySelectorAll('[data-furniture-form] select[name="item_name"]').forEach(choice => {
      const value = choice.value;
      const before = Array.from(choice.options, o => o.value + '=' + o.textContent).join('|');
      const after = Array.from(source.options, o => o.value + '=' + o.textContent).join('|');
      if (before === after) return;
      choice.replaceChildren(...Array.from(source.options, o => document.importNode(o, true)));
      if (Array.from(choice.options).some(o => o.value === value)) choice.value = value;
      else { choice.value = ''; notice('刚才选中的物品已下架或库存改变，请重新选择。', 'warning'); }
    });
    const options = document.querySelector('#v3-stock-names');
    const newer = doc.querySelector('#v3-stock-names');
    if (options && newer && options.innerHTML !== newer.innerHTML) {
      options.replaceChildren(...Array.from(newer.childNodes, child => document.importNode(child, true)));
    }
  }

  function syncFurniture(doc) {
    let changed = false;
    changed = replaceTableBody('.v3-inventory', doc) || changed;
    changed = replaceTableBody('.v3-public-loans', doc) || changed;
    // Staff-only management list is already permission-filtered by the server.
    const admin = '.v3-admin-area:last-of-type';
    changed = replaceTableBody(admin, doc) || changed;
    const count = document.querySelector('.v3-public-loans > .section-line > span');
    const countNext = doc.querySelector('.v3-public-loans > .section-line > span');
    if (count && countNext) count.textContent = countNext.textContent;
    const publicPanel = document.querySelector('.v3-public-loans');
    const nextPanel = doc.querySelector('.v3-public-loans');
    if (publicPanel && nextPanel) {
      const oldPager = publicPanel.querySelector(':scope > nav.pagination');
      const freshPager = nextPanel.querySelector(':scope > nav.pagination');
      if (oldPager && freshPager) oldPager.replaceWith(document.importNode(freshPager, true));
      else if (oldPager && !freshPager) oldPager.remove();
      else if (!oldPager && freshPager) publicPanel.append(document.importNode(freshPager, true));
    }
    syncInventoryForm(doc);
    return changed;
  }

  function scheduleMap(doc) {
    const map = new Map();
    doc.querySelectorAll('[data-v3-cell]').forEach(c => map.set(key(c), c));
    return map;
  }
  function selectionOptions(cell) {
    return Array.from(cell.querySelectorAll('[data-v3-room-id]')).map(el => ({
      id: el.getAttribute('data-v3-room-id'), name: el.textContent.trim()
    })).filter(item => item.id);
  }
  function syncDialog(latest) {
    const dialog = document.querySelector('#v3-quick-dialog');
    if (!dialog || !dialog.open) return;
    const form = dialog.querySelector('#v3-quick-form');
    if (form && pending.has(form)) return;
    const day = dialog.querySelector('#v3-book-date');
    const period = dialog.querySelector('#v3-book-period');
    const selection = dialog.querySelector('#v3-book-room');
    if (!day || !period || !selection) return;
    const cell = latest.get(day.value + '\u0001' + period.value);
    if (!cell) return;
    const allowed = selectionOptions(cell);
    const prev = selection.value;
    const old = Array.from(selection.options, o => o.value + ':' + o.textContent).join('|');
    const newer = allowed.map(o => o.id + ':' + o.name).join('|');
    if (old === newer) return;
    selection.replaceChildren();
    allowed.forEach(item => {
      const o = document.createElement('option'); o.value = item.id; o.textContent = item.name;
      selection.append(o);
    });
    if (allowed.some(item => item.id === prev)) selection.value = prev;
    else {
      const o = document.createElement('option');
      o.value = ''; o.textContent = '原选择已被占用，请重新选择';
      selection.prepend(o); selection.value = '';
      notice('所选教室刚被他人占用，请重新选择。你已填写的资料仍保留。', 'warning');
      dialogWarning('当前教室刚被其他人预约，请重新选择。已填写内容不会丢失。');
    }
    if (!allowed.length) {
      selection.replaceChildren();
      const o = document.createElement('option');o.value='';o.textContent='该时段暂无可借用教室';selection.append(o);
    }
  }
  function syncSchedule(doc) {
    const oldCells = Array.from(document.querySelectorAll('[data-v3-cell]'));
    const freshMap = scheduleMap(doc);
    if (oldCells.length !== freshMap.size || oldCells.some(cell => !freshMap.has(key(cell)))) {
      notice('课表结构已变化，请手动刷新页面查看最新安排。', 'warning');
      return false;
    }
    let changed = false;
    oldCells.forEach(cell => {
      const fresh = freshMap.get(key(cell));
      if (cell.innerHTML === fresh.innerHTML && cell.className === fresh.className) return;
      changed = true;
      cell.innerHTML = fresh.innerHTML;
      cell.className = fresh.className;
      // V3's handlers are attached to the existing cell. New buttons delegate to it.
      cell.querySelectorAll('[data-v3-open]').forEach(btn => btn.addEventListener('click', () => cell.click()));
    });
    syncDialog(freshMap);
    return changed;
  }

  async function refresh(force = false) {
    if (!force && (document.hidden || !navigator.onLine)) return;
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const doc = await getFresh();
        const changed = schedule ? syncSchedule(doc) : syncFurniture(doc);
        lastGood = Date.now();
        if (changed) {
          if (!node.classList.contains('r21-live-warning')) notice('最新的教室/借用状态已自动同步 · ' + new Date().toLocaleTimeString());
        } else if (!node.classList.contains('r21-live-warning')) notice('');
        return doc;
      } catch (error) {
        notice('暂时无法同步最新状态；提交前会重新核验。', 'warning');
        return null;
      } finally { inFlight = null; }
    })();
    return inFlight;
  }

  function verifySchedule(doc, form) {
    const day = form.querySelector('#v3-book-date');
    const period = form.querySelector('#v3-book-period');
    const select = form.querySelector('#v3-book-room');
    if (!day || !period || !select || !select.value) return '请先选择有余量的教室。';
    const cell = scheduleMap(doc).get(day.value + '\u0001' + period.value);
    if (!cell) return '课表时间已经发生变化，请刷新页面再选择。';
    const allowed = selectionOptions(cell);
    if (!allowed.some(o => o.id === select.value)) return '所选教室刚被其他人占用，请重新选择；已填资料没有丢失。';
    return '';
  }
  function verifyFurniture(doc, form) {
    const available = stockInfo(doc);
    const requested = new Map();
    const rows = form.querySelectorAll('[data-loan-items] .furniture-item-row');
    for (const row of rows) {
      const select = row.querySelector('select[name="item_name"]');
      const qty = row.querySelector('input[name="item_quantity"]');
      if (!select || !qty) continue;
      const name = select.value;
      if (!name || name === '__OTHER__') continue; // Other is deliberately untracked.
      const count = Number(qty.value);
      if (!Number.isInteger(count) || count <= 0) return '请填写有效借用数量。';
      requested.set(name, (requested.get(name) || 0) + count);
    }
    for (const [name, count] of requested) {
      if (!available.has(name)) return name + ' 已不在库存中，请重新选择。';
      if (count > available.get(name)) return name + ' 当前剩余 ' + available.get(name) + ' 件，填写了 ' + count + ' 件，请调整。';
    }
    return '';
  }

  // Capture only the two booking/loan forms; all other forms and existing AJAX
  // handlers remain untouched. The server is still the ultimate arbiter.
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const checkSchedule = schedule && form.id === 'v3-quick-form';
    const checkFurniture = furniture && form.hasAttribute('data-furniture-form');
    if (!checkSchedule && !checkFurniture) return;
    if (bypass.has(form)) { bypass.delete(form); return; }
    event.preventDefault();
    event.stopImmediatePropagation();
    if (pending.has(form)) return;
    pending.add(form);
    const btn = event.submitter;
    try {
      // Always re-query before POST, even if the most recent 5s poll was fresh.
      const fresh = await getFresh();
      const issue = checkSchedule ? verifySchedule(fresh, form) : verifyFurniture(fresh, form);
      if (issue) { notice(issue, 'warning'); if (checkSchedule) dialogWarning(issue); await refresh(true); return; }
      // Submit on the next browser task: calling requestSubmit while the
      // original submit event is still unwinding can silently swallow it.
      setTimeout(() => {
        if (!form.isConnected) return;
        if (typeof form.requestSubmit !== 'function') {
          notice('浏览器不支持安全提交，请使用新版本浏览器。', 'warning');
          return;
        }
        bypass.add(form);
        try { form.requestSubmit(btn && btn.isConnected ? btn : undefined); }
        catch (err) { notice('提交未能发出，请检查表单并重试。', 'warning'); }
        finally { bypass.delete(form); }
      }, 0);
    } catch (e) {
      notice('暂时无法确认最新余量，未提交数据；请检查网络后重试。', 'warning');
    } finally { pending.delete(form); }
  }, true);

  // Foreground only; network failures won't trigger blind resubmission.
  const start = () => { refresh(); setInterval(() => refresh(), PERIOD_MS); };
  setTimeout(start, 300 + Math.floor(Math.random() * 1600));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.addEventListener('pageshow', () => refresh());
  window.addEventListener('online', () => refresh());
})();
