'use strict';
// Run with: node --test tests/frontend_i18n.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '../web/i18n.js'), 'utf8');
function setup(locale, saved = null) {
  const values = new Map(saved ? [['psp-dock-language', saved]] : []);
  const storage = {
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, value); },
  };
  const doc = {
    readyState: 'loading',
    body: null,
    documentElement: { lang: '' },
    listeners: {},
    addEventListener(event, listener) { this.listeners[event] = listener; },
    dispatchEvent() {},
  };
  const context = {
    window: {},
    document: doc,
    navigator: { language: locale, languages: [locale] },
    localStorage: storage,
    CustomEvent: class {},
    Node: { TEXT_NODE: 3, ELEMENT_NODE: 1 },
    MutationObserver: class {},
  };
  vm.runInNewContext(script, context);
  return { i18n: context.window.DockI18n, values, doc };
}

test('detects Spanish and English locale, falls back to English', () => {
  for (const [locale, expected] of [['es-ES', 'es'], ['es-MX', 'es'], ['en-US', 'en'], ['fr-FR', 'en']]) {
    const { i18n } = setup(locale);
    assert.equal(i18n.locale(), expected, locale);
    assert.equal(i18n.mode(), 'auto');
  }
});

test('manual preference survives new sessions and automatic mode can be restored', () => {
  const first = setup('es-ES');
  first.i18n.setLanguage('en');
  assert.equal(first.i18n.locale(), 'en');
  assert.equal(first.values.get('psp-dock-language'), 'en');
  assert.equal(setup('es-ES', first.values.get('psp-dock-language')).i18n.locale(), 'en');
  first.i18n.setLanguage('auto');
  assert.equal(first.i18n.locale(), 'es');
  assert.equal(first.values.get('psp-dock-language'), 'auto');
  assert.equal(setup('en-GB', 'auto').i18n.locale(), 'en');
});

test('translates prominent screens while preserving arbitrary game titles and URLs', () => {
  const { i18n } = setup('en-GB');
  assert.equal(i18n.translate('Conecta tu PSP'), 'Connect your PSP');
  assert.equal(i18n.translate('Copiar juegos pendientes'), 'Copy missing games');
  assert.equal(i18n.translate('Sincronizar partidas'), 'Sync saves');
  assert.equal(i18n.translate('Mejoras gráficas'), 'Graphics enhancements');
  assert.equal(i18n.translate('Idioma / Language'), 'Language / Idioma');
  assert.equal(i18n.translate('Dariusburst'), 'Dariusburst');
  assert.equal(i18n.translate('https://example.com/Conecta tu PSP'), 'https://example.com/Conecta tu PSP');
});
