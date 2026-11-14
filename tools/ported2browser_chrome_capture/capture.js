/* Passive reading only on original Ported2Browser pages that the user opens.
 * No requests, navigation, cookies, credentials, game files or downloads.
 */
(() => {
  'use strict';
  const ORIGIN = 'https://ported2browser.com';
  const KEY = 'pspdock_official_capture_v1';
  const ID_RE = /^[0-9A-Z]{26}$/;
  const PLATFORMS = new Set([
    'NES','Super Nintendo','Nintendo 64','GameCube','Wii',
    'Game Boy','Game Boy Advance','Nintendo Switch',
    'Genesis','Dreamcast','Sega CD','SegaMD',
    'DOS','Windows','macOS','ZX Spectrum',
    'Xbox','Xbox 360','Xbox One','Xbox Series X|S',
    'PlayStation','PlayStation 2','PSP','PlayStation 3',
    'PlayStation 4','Arcade','Neo Geo','Wii U'
  ]);
  if (location.origin !== ORIGIN) return;

  const content = (node, max = 160) =>
    (node?.textContent || '').trim().replace(/\s+/g,' ').slice(0,max);
  const unique = values => Array.from(new Set(values.filter(Boolean)));
  const child = (node, query) => node?.querySelector?.(query) || null;

  function validDetail(link, id) {
    try {
      const url = new URL(link, location.href);
      return url.origin === ORIGIN && !url.search && !url.hash &&
        url.pathname === '/ports/' + id ? url.href : null;
    } catch { return null; }
  }
  function safeGameUrl(link) {
    try {
      if (typeof link !== 'string' || link.length > 2048 ||
          /[\x00-\x20\\]/.test(link)) return null;
      const url = new URL(link);
      const host = url.hostname.toLowerCase();
      if (url.protocol !== 'https:' || !host.includes('.') ||
          url.username || url.password || (url.port && url.port !== '443') ||
          /^\d+(?:\.\d+){3}$/.test(host) ||
          host === 'localhost' || host.endsWith('.local') ||
          host.endsWith('.internal') || host.endsWith('.invalid') ||
          host === 'ported2browser.com' ||
          host.endsWith('.ported2browser.com') ||
          host === 'ported2browser.org' || host === 'ported2browser.sbs')
        return null;
      for (const name of url.searchParams.keys()) {
        if (/token|secret|session|auth|code|password|api.?key/i.test(name))
          return null;
      }
      return url.href;
    } catch { return null; }
  }

  function publicVisitedPath() {
    // A URL may contain private parameters. Preserve only known directory
    // navigation facets; never export arbitrary query strings or tokens.
    const current = new URL(location.href);
    const safe = new URLSearchParams();
    const tab = current.searchParams.get('tab');
    const page = current.searchParams.get('page');
    const platform = current.searchParams.get('platform');
    if (tab && ['recent','popular','az'].includes(tab))
      safe.set('tab',tab);
    if (page && /^[0-9]{1,2}$/.test(page) &&
        Number(page) > 0 && Number(page) <= 30)
      safe.set('page',page);
    if (platform && PLATFORMS.has(platform))
      safe.set('platform',platform);
    return current.pathname + (safe.size ? '?' + safe.toString() : '');
  }

  function parseCard(node, tab, platform) {
    const id = node.getAttribute?.('data-port-id') || '';
    if (!ID_RE.test(id)) return null;
    const detailUrl = validDetail(
      child(node,'a.atropos')?.getAttribute?.('href') || '', id);
    const title = content(child(node,'.card-box h2'),120);
    if (!detailUrl || title.length < 2) return null;
    const flags = unique(Array.from(
      node.querySelectorAll('.cover-tags b, .cover > b'),
      element => (element.className || '').split(/\s+/).filter(Boolean)
    ).flat());
    if (node.classList?.contains('is-broken')) flags.push('broken_link');
    const alerts = unique(Array.from(node.querySelectorAll('[aria-label]'),
      item => item.getAttribute('aria-label') || '')
      .filter(value => /requires|broken|setup|alert/i.test(value))
      .map(value => value.slice(0,240)));
    return {
      id,title,
      author: content(child(node,'.card-box p'),120),
      detail_url:detailUrl,
      original_platform:platform || null,
      flags:unique(flags), alerts, observed_in:[tab],
      status:'directory_only'
    };
  }

  function readCards() {
    const params = new URL(location.href).searchParams;
    const platformName = params.get('platform');
    const platform = PLATFORMS.has(platformName) ? platformName : null;
    const rows = [];
    for (const node of document.querySelectorAll(
      '#catalog-results article.card[data-port-id]'))
      rows.push(parseCard(node,'current',platform));
    for (const template of document.querySelectorAll(
      'template[data-catalog-preload]')) {
      const tab = (template.getAttribute('data-catalog-preload') || '').slice(0,20);
      for (const node of template.content?.querySelectorAll?.(
        'article.card[data-port-id]') || [])
        rows.push(parseCard(node,tab,platform));
    }
    return rows.filter(Boolean);
  }

  function readDetail() {
    const matched = /^\/ports\/([0-9A-Z]{26})$/.exec(location.pathname);
    if (!matched || !document.querySelector('.port-page')) return null;
    const id = matched[1];
    const title = content(document.querySelector('.port-page h1'),120);
    if (title.length < 2) return null;
    const links = unique(Array.from(document.querySelectorAll(
      '.port-actions .play-list a.button.play[data-id][data-page]'), anchor => {
      if (anchor.getAttribute('data-id') !== id ||
          anchor.getAttribute('data-page') !== '/ports/' + id) return null;
      return safeGameUrl(anchor.getAttribute('href') || '');
    }));
    const controls = {};
    for (const row of document.querySelectorAll('ul.controls li.ctl')) {
      const label = content(child(row,'.ctl-label'),40).toLowerCase();
      if (!['gamepad','keyboard','touch'].includes(label)) continue;
      controls[label] = row.classList.contains('is-supported') &&
        !row.classList.contains('is-unsupported');
    }
    const alerts = Array.from(document.querySelectorAll('.port-alert'),
      node => ({
        type:node.classList.contains('is-disc') ?
          'requires_disc_image' : 'notice',
        text:content(node,240)
      })).filter(item => item.text);
    const tags = unique(Array.from(document.querySelectorAll(
      '.port-head .tags a[href]'), node => {
      const url = new URL(node.getAttribute('href'),location.href);
      return url.origin === ORIGIN ? url.searchParams.get('tag') : null;
    })).slice(0,24);
    return {
      id,title, detail_url:ORIGIN+'/ports/'+id,
      direct_candidates:links,
      direct_url:links.length === 1 ? links[0] : null,
      controls,alerts,tags,
      requires_disc_image:alerts.some(item =>
        item.type === 'requires_disc_image'),
      status:links.length === 1 ? 'direct_candidate' :
        links.length > 1 ? 'multiple_candidates' : 'no_direct_link',
      runtime_verified:false,gamepad_tested:false
    };
  }

  let running = false;
  let again = false;
  let digest = '';
  async function capture() {
    if (running) { again = true; return; }
    running = true;
    try {
      const cards = readCards(), detail = readDetail();
      if (!cards.length && !detail) return;
      const fingerprint = JSON.stringify([location.pathname,location.search,
                                          cards,detail]);
      if (fingerprint === digest) return;
      digest = fingerprint;
      const item = (await chrome.storage.local.get(KEY))[KEY] || {
        schema:1, source:ORIGIN+'/', cards:{}, details:{}, visited:{}
      };
      if (item.schema !== 1 || item.source !== ORIGIN+'/') return;
      for (const card of cards) {
        const before = item.cards[card.id];
        item.cards[card.id] = before ? {
          ...card,
          flags:unique([...before.flags,...card.flags]),
          alerts:unique([...before.alerts,...card.alerts]),
          observed_in:unique([...before.observed_in,...card.observed_in]),
          original_platform:card.original_platform || before.original_platform
        } : card;
      }
      if (detail) item.details[detail.id] = detail;
      const path = publicVisitedPath().slice(0,400);
      item.visited[path] = {
        at:new Date().toISOString(),
        listed:cards.length,
        detail:detail?.id || null
      };
      await chrome.storage.local.set({[KEY]:item});
    } catch {
      // Reading metadata must never interfere with the real site.
    } finally {
      running = false;
      if (again) { again = false; void capture(); }
    }
  }

  let pending = null;
  function schedule() {
    if (pending != null) return;
    pending = setTimeout(() => {
      pending = null;
      void capture();
    },500);
  }
  schedule();
  new MutationObserver(schedule).observe(document.documentElement,
    {childList:true,subtree:true});
  window.addEventListener('popstate',schedule);
  window.addEventListener('hashchange',schedule);
})();
