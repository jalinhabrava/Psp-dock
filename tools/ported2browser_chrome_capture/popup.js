(() => {
  'use strict';
  const KEY = 'pspdock_official_capture_v1';
  const SOURCE = 'https://ported2browser.com/';
  const $ = id => document.getElementById(id);

  async function refresh() {
    const item = (await chrome.storage.local.get(KEY))[KEY] || {};
    $('cards').textContent = Object.keys(item.cards || {}).length;
    $('details').textContent = Object.keys(item.details || {}).length;
    $('pages').textContent = Object.keys(item.visited || {}).length;
  }
  $('export').addEventListener('click', async () => {
    const item = (await chrome.storage.local.get(KEY))[KEY];
    if (!item || item.source !== SOURCE) {
      $('status').textContent = 'Todavía no hay páginas originales capturadas.';
      return;
    }
    const data = {
      schema:1,
      source:SOURCE,
      provenance:'pages_visited_by_user_in_normal_chrome',
      exported_at:new Date().toISOString(),
      cards:Object.values(item.cards || {}),
      details:Object.values(item.details || {}),
      pages_visited:Object.entries(item.visited || {}).map(
        ([path, info]) => ({path,...info})),
      runtime_verified:false
    };
    const blob = new Blob([JSON.stringify(data,null,2)+'\n'],
      {type:'application/json'});
    const uri = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = uri;
    anchor.download = 'ported2browser-original-pspdock.json';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(uri),2500);
    $('status').textContent = 'JSON exportado. Adjunta el archivo a ChatGPT.';
  });
  $('reset').addEventListener('click', async () => {
    if (!confirm('¿Vaciar las fichas y destinos guardados por el capturador?'))
      return;
    await chrome.storage.local.remove(KEY);
    $('status').textContent = 'Captura vaciada. No se modificó la web original.';
    await refresh();
  });
  void refresh();
})();
