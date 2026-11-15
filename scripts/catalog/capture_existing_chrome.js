/* Run in DevTools of the ALREADY OPEN, authorized original directory tab.
 * No extension, CDP/profile restart, cookies, credentials or disc files.
 * Serial normal same-origin HTML reads; stop immediately on access denial.
 */
void (async () => {
  'use strict';
  const SOURCE = 'https://ported2browser.com/';
  if (location.origin !== new URL(SOURCE).origin || location.pathname !== '/')
    throw Error('Use the existing original directory homepage tab.');
  const text = node => node?.textContent?.replace(/\s+/g, ' ').trim() || '';
  const report = window.pspDockAudit = {
    schema: 2, source: SOURCE, kind: 'existing_chrome_public_dom',
    started_at: new Date().toISOString(),
    reported_total: Number(text(document.querySelector('#result-count')).replace(/,/g, '')),
    pages: [], platform_pages: [], games: [], complete: false,
  };
  const records = new Map();
  const publicUrl = value => {
    try {
      if (typeof value !== 'string' || value.length > 2048 || /[\x00-\x20\\]/.test(value)) return null;
      const url = new URL(value), host = url.hostname.toLowerCase();
      if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.port ||
          !/^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) ||
          host === 'ported2browser.com' || host.endsWith('.ported2browser.com') ||
          ['ported2browser.org','ported2browser.sbs'].includes(host) ||
          /\.(?:local|localhost|internal|test|invalid|example)$/.test(host) ||
          Array.from(url.searchParams.keys()).some(key => /token|secret|session|auth|password|api.?key/i.test(key)))
        return null;
      return url.href;
    } catch { return null; }
  };
  async function read(value) {
    const url = new URL(value, SOURCE);
    if (url.origin !== location.origin || url.hash || url.username || url.password ||
        !(url.pathname === '/' || /^\/ports\/[0-9A-Z]{26}$/.test(url.pathname)) ||
        Array.from(url.searchParams.keys()).some(key => !['page','platform'].includes(key)))
      throw Error('Unexpected directory navigation');
    await new Promise(resolve => setTimeout(resolve, 1600));
    const response = await fetch(url.href, {redirect:'error', signal:AbortSignal.timeout(30000)});
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (!response.ok || page.querySelector('#human-form') ||
        /Just a moment|Verify you are human/.test(page.title))
      throw Error('Access denied or human check: HTTP ' + response.status);
    return page;
  }
  function cards(page, url) {
    const nodes = Array.from(page.querySelectorAll('#catalog-results article.card[data-port-id]'));
    if (!nodes.length) throw Error('No directory cards at ' + url);
    for (const node of nodes) {
      const id = node.dataset.portId;
      if (!/^[0-9A-Z]{26}$/.test(id) ||
          node.querySelector('a.atropos')?.getAttribute('href') !== SOURCE + 'ports/' + id)
        throw Error('Invalid card identity');
      if (!records.has(id)) records.set(id, {
        id, title:text(node.querySelector('.card-box h2')),
        author:text(node.querySelector('.card-box p')), detail_url:SOURCE+'ports/'+id,
        flags:Array.from(node.querySelectorAll('.cover-tags b,.cover > b')).flatMap(n => n.className.split(/\s+/)).filter(Boolean),
        broken:node.classList.contains('is-broken'), original_platforms:[],
        direct_url:null, direct_candidates:[], link_status:'pending',
        runtime_verified:false, gamepad_tested:false,
      });
    }
    report.pages.push({url, count:nodes.length, at:new Date().toISOString()});
    report.games = Array.from(records.values());
  }
  const nextPage = page => page.querySelector('#catalog-results nav[data-pagination] a[rel=next]')?.getAttribute('href');
  function download() {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:'application/json'}));
    link.download = 'pspdock-ported2browser-complete-' + new Date().toISOString().slice(0,10) + '.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  }
  try {
    if (!Number.isInteger(report.reported_total) || report.reported_total < 1)
      throw Error('Directory total is missing');
    cards(document, SOURCE);
    let next = nextPage(document);
    while (next && report.pages.length < 15) {
      const page = await read(next);
      cards(page, next);
      next = nextPage(page);
      console.log('PSPDOCK listing', records.size, '/', report.reported_total);
    }
    if (next) throw Error('Pagination cap reached');
    report.listing_complete = records.size === report.reported_total;
    for (const game of report.games) {
      const page = await read(game.detail_url);
      if (text(page.querySelector('h1')) !== game.title) throw Error('Detail identity mismatch: ' + game.id);
      game.direct_candidates = Array.from(new Set(Array.from(page.querySelectorAll('a.button.play[data-id][data-page]'))
        .filter(a => a.dataset.id === game.id && a.dataset.page === '/ports/' + game.id)
        .map(a => publicUrl(a.getAttribute('href'))).filter(Boolean)));
      game.direct_url = game.direct_candidates.length === 1 ? game.direct_candidates[0] : null;
      game.controls = Object.fromEntries(Array.from(page.querySelectorAll('ul.controls li.ctl'))
        .map(n => [text(n.querySelector('.ctl-label')).toLowerCase(), n.classList.contains('is-supported') && !n.classList.contains('is-unsupported')]));
      game.requirements = Array.from(page.querySelectorAll('.port-alert'))
        .map(n => ({type:n.classList.contains('is-disc')?'requires_disc_image':'notice', text:text(n)}));
      game.requires_disc_image = game.requirements.some(n => n.type === 'requires_disc_image');
      game.description = text(page.querySelector('main')).split('Updates')[0].slice(0,20000);
      game.observed_at = new Date().toISOString();
      console.log('PSPDOCK detail', game.title);
    }
    const facets = Array.from(document.querySelectorAll('a.platform-option[data-platform]')).filter(a => a.dataset.platform);
    for (const facet of facets) {
      let next = facet.getAttribute('href'), count = 0;
      while (next && count++ < 15) {
        const page = await read(next);
        const nodes = Array.from(page.querySelectorAll('#catalog-results article.card[data-port-id]'));
        if (!nodes.length) throw Error('Empty platform: ' + facet.dataset.platform);
        for (const node of nodes) {
          const game = records.get(node.dataset.portId);
          if (game && !game.original_platforms.includes(facet.dataset.platform)) game.original_platforms.push(facet.dataset.platform);
        }
        report.platform_pages.push({platform:facet.dataset.platform, url:next, count:nodes.length, at:new Date().toISOString()});
        next = nextPage(page);
      }
      if (next) throw Error('Platform pagination cap reached');
    }
    report.platforms_complete = report.games.every(g => g.original_platforms.length);
    report.complete = report.listing_complete && report.platforms_complete && report.games.every(g => g.observed_at);
  } catch (error) {
    report.error = String(error);
    console.error('PSPDOCK stopped without bypass:', report.error);
  } finally {
    report.finished_at = new Date().toISOString();
    download();
    console.log('PSPDOCK export', report.games.length, report.complete);
  }
})();
