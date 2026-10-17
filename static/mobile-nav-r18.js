/* Classroom Booking R18: mobile navigation presentation only.
   Clone existing anchors verbatim. No route, permission or business change. */
(function () {
  'use strict';
  var QUERY = '(max-width: 800px)';
  var mql = window.matchMedia(QUERY);
  var mounted = null;
  var ICONS = {
    schedule: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18M8 14h3M8 17h3M15 14h2"/></svg>',
    furniture: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 11V7a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v4M5 17h14M4 13a2 2 0 0 1 2 2v2h12v-2a2 2 0 0 1 4 0v4H2v-4a2 2 0 0 1 2-2ZM5 19v2M19 19v2"/></svg>',
    rooms: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v17M3 21h18M9 7h2M15 7h1M9 11h2M15 11h1M9 15h2M15 15h1M11 21v-3h3v3"/></svg>',
    lookup: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.7"/><path d="m16 16 5 5M8 11h6M11 8v6"/></svg>',
    management: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/></svg>',
    more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    item: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
  };
  function create(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function normalizeName(link) {
    return (link.textContent || '').replace(/[▦▤⌂⚙✓⇥◴♙→↗]/g, '').replace(/\s+/g, ' ').trim();
  }
  function findLink(links, phrase) {
    return links.find(function (link) { return normalizeName(link) === phrase; });
  }
  function isCurrent(link, name) {
    if (link.classList.contains('active') || link.getAttribute('aria-current') === 'page') return true;
    var path = window.location.pathname;
    var target;
    try { target = new URL(link.href, document.baseURI).pathname; } catch (_err) { return false; }
    if (path === target) return true;
    if (name === 'rooms' && path.indexOf('/rooms/') === 0) return true;
    return false;
  }
  function icon(name) {
    var node = create('span', 'mobile-nav-r18-icon');
    node.innerHTML = ICONS[name] || ICONS.item; // Constant in-code SVG, never user data.
    return node;
  }
  function teardown() {
    if (!mounted) return;
    if (mounted.dialog && mounted.dialog.open) mounted.dialog.close();
    mounted.nodes.forEach(function (node) { node.remove(); });
    document.documentElement.classList.remove('mobile-r18-ready');
    mounted = null;
  }
  function mount() {
    if (!mql.matches) { teardown(); return; }
    if (mounted) return;
    var sidebar = document.querySelector('.sidebar');
    var originalNav = sidebar && sidebar.querySelector('nav');
    if (!sidebar || !originalNav) return;
    var originalLinks = Array.from(originalNav.querySelectorAll('a[href]'));
    if (!originalLinks.length) return;
    var items = [
      ['schedule', '课表', '日 / 周课表'],
      ['furniture', '桌椅', '桌椅借用'],
      ['rooms', '教室', '发现教室'],
      ['lookup', '我的', '查询我的借用']
    ];
    if (!findLink(originalLinks, '查询我的借用')) {
      items[3] = ['management', '管理', '管理总览'];
    }
    var chosen = [];
    items.forEach(function (item) {
      var source = findLink(originalLinks, item[2]);
      if (source) chosen.push({source: source, key: item[0], label: item[1]});
    });
    if (!chosen.length) return; // Keep old navigation if selection fails.
    var remaining = originalLinks.filter(function (l) { return !chosen.some(function (x) { return x.source === l; }); });
    var bar = create('nav', 'mobile-nav-r18');
    bar.setAttribute('aria-label', '手机版快捷导航');
    var inner = create('div', 'mobile-nav-r18-inner');
    bar.appendChild(inner);
    chosen.forEach(function (item) {
      var a = create('a', 'mobile-nav-r18-tab');
      a.href = item.source.href;
      a.appendChild(icon(item.key));
      a.appendChild(create('span', 'mobile-nav-r18-tab-label', item.label));
      a.title = normalizeName(item.source);
      if (isCurrent(item.source, item.key)) {
        a.classList.add('is-current');
        a.setAttribute('aria-current', 'page');
      }
      inner.appendChild(a);
    });
    var dialog = null;
    if (remaining.length) {
      var more = create('button', 'mobile-nav-r18-tab mobile-nav-r18-more');
      more.type = 'button';
      more.setAttribute('aria-haspopup', 'dialog');
      more.setAttribute('aria-expanded', 'false');
      more.appendChild(icon('more'));
      more.appendChild(create('span', 'mobile-nav-r18-tab-label', '更多'));
      inner.appendChild(more);
      dialog = create('dialog', 'mobile-nav-r18-sheet');
      dialog.setAttribute('aria-label', '全部导航入口');
      var header = create('div', 'mobile-nav-r18-sheet-header');
      var heading = create('div', 'mobile-nav-r18-sheet-title');
      heading.appendChild(create('strong', '', '全部功能'));
      heading.appendChild(create('small', '', '选择需要访问的页面'));
      var close = create('button', 'mobile-nav-r18-close');
      close.type = 'button';
      close.setAttribute('aria-label', '关闭功能菜单');
      close.appendChild(icon('close'));
      header.appendChild(heading);
      header.appendChild(close);
      dialog.appendChild(header);
      var grid = create('div', 'mobile-nav-r18-sheet-list');
      remaining.forEach(function (source) {
        var link = create('a', 'mobile-nav-r18-sheet-link');
        link.href = source.href;
        link.appendChild(create('span', 'mobile-nav-r18-sheet-link-icon'));
        link.firstChild.appendChild(icon('item'));
        link.appendChild(create('span', 'mobile-nav-r18-sheet-link-text', normalizeName(source)));
        if (isCurrent(source, 'other')) {
          link.setAttribute('aria-current', 'page');
          more.classList.add('is-current');
        }
        grid.appendChild(link);
      });
      dialog.appendChild(grid);
      function closeDialog() {
        if (dialog.open && typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
      }
      more.addEventListener('click', function () {
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        more.setAttribute('aria-expanded', 'true');
        close.focus();
      });
      close.addEventListener('click', closeDialog);
      dialog.addEventListener('close', function () {
        more.setAttribute('aria-expanded', 'false');
        if (mql.matches) more.focus();
      });
      dialog.addEventListener('click', function (event) {
        if (event.target === dialog) closeDialog();
      });
    }
    document.body.appendChild(bar);
    if (dialog) document.body.appendChild(dialog);
    mounted = {dialog:dialog,nodes:dialog?[bar,dialog]:[bar]};
    // Hide original nav only after every replacement link was successfully created.
    document.documentElement.classList.add('mobile-r18-ready');
  }
  function init() {
    mount();
    if (typeof mql.addEventListener === 'function') mql.addEventListener('change', mount);
    else if (typeof mql.addListener === 'function') mql.addListener(mount);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
}());
