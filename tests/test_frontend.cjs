const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(fetch) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', disabled: false, attributes: {},
      files: [{}], value: '', textContent: '',
      setAttribute(key, value) { this.attributes[key] = value; },
      classList: { add() {}, remove() {} }
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document: { getElementById: element, addEventListener() {}, querySelectorAll() { return []; } },
    fetch, FormData: class { append() {} }, URLSearchParams,
    TypeError, window: { location: { origin: 'http://test' } }, alert() {}, confirm() { return true; }
  });
  vm.runInContext(fs.readFileSync('frontend/app.js', 'utf8'), context);
  // Existing upload tests operate with a short-lived in-memory test session.
  vm.runInContext("authToken = 'frontend-only-test-token'", context);
  return { context, element };
}

test('duplicate clicks send one request and button recovers after validation error', async () => {
  let resolve, count = 0;
  const { context, element } = setup(() => { count++; return new Promise(r => { resolve = r; }); });
  const first = context.analyze();
  await context.analyze();
  assert.equal(count, 1);
  assert.equal(element('btnAnalyze').disabled, true);
  resolve({ ok: false, status: 400, json: async () => ({ detail: 'El archivo está vacío.' }) });
  await first;
  assert.match(element('analysisCard').innerHTML, /El archivo está vacío/);
  assert.equal(element('btnAnalyze').disabled, false);
  assert.equal(element('analysisCard').attributes['aria-busy'], 'false');
});

test('server errors hide internal details and allow retry', async () => {
  let count = 0;
  const { context, element } = setup(async () => {
    count++;
    return { ok: false, status: 500, json: async () => ({ detail: 'private database details' }) };
  });
  await context.analyze();
  await context.analyze();
  assert.equal(count, 2);
  assert.match(element('analysisCard').innerHTML, /servidor no ha podido/);
  assert.doesNotMatch(element('analysisCard').innerHTML, /private database/);
  assert.equal(element('btnAnalyze').disabled, false);
});

test('network failure shows connection guidance and restores button', async () => {
  const { context, element } = setup(async () => { throw new TypeError('fetch failed'); });
  await context.analyze();
  assert.match(element('analysisCard').innerHTML, /No se pudo conectar/);
  assert.equal(element('btnAnalyze').disabled, false);
});

test('valid analysis renders and refreshes history once', async () => {
  const requests = [];
  const { context, element } = setup(async url => {
    requests.push(url);
    return { ok: true, json: async () => url === '/analyze'
      ? { id: 1, filename: 'ok.pdf', score: 80, status: 'OK', findings: [] }
      : { total: 0, items: [] } };
  });
  await context.analyze();
  assert.equal(requests.length, 2);
  assert.match(element('analysisCard').innerHTML, /ok.pdf/);
  assert.equal(element('btnAnalyze').disabled, false);
});

test('validation messages are escaped and non-JSON errors have a fallback', async () => {
  const { context, element } = setup(async () => ({
    ok: false, status: 400, json: async () => ({ detail: '<img src=x onerror=alert(1)>' })
  }));
  await context.analyze();
  assert.doesNotMatch(element('analysisCard').innerHTML, /<img/);
  assert.match(element('analysisCard').innerHTML, /&lt;img/);
  const message = await context.uploadErrorMessage({
    status: 413, json: async () => { throw new Error('not JSON'); }
  });
  assert.match(message, /HTTP 413/);
});


test('every document request carries the bearer token', async () => {
  const requests = [];
  const { context } = setup(async (url, options) => {
    requests.push({ url, authorization: options.headers.Authorization });
    return { ok: true, json: async () => url === '/analyze'
      ? { id: 1, filename: 'doc.pdf', score: 70, status: 'REVISAR', findings: [] }
      : { total: 0, items: [] } };
  });
  await context.analyze();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].authorization, 'Bearer frontend-only-test-token');
  assert.equal(requests[1].authorization, 'Bearer frontend-only-test-token');
});

test('logout prevents further document requests', async () => {
  let count = 0;
  const { context, element } = setup(async () => { count++; });
  context.logout();
  assert.equal(element('workspace').hidden, true);
  assert.equal(element('loginPanel').hidden, false);
  await context.analyze();
  assert.equal(count, 0);
  assert.match(element('analysisCard').innerHTML, /Inicia sesión/);
});

test('login obtains bearer token without storing credentials', async () => {
  const requests = [];
  const { context, element } = setup(async (url, options) => {
    requests.push(url);
    if (url === '/auth/login') {
      assert.equal(options.method, 'POST');
      assert.equal(options.headers['Content-Type'], 'application/json');
      return { ok: true, json: async () => ({ access_token: 'new-token' }) };
    }
    return { ok: true, json: async () => ({ total: 0, items: [] }) };
  });
  element('loginEmail').value = 'owner@example.com';
  element('loginOrg').value = 'gestoria';
  element('loginPassword').value = 'password';
  await context.login({ preventDefault() {} });
  assert.equal(requests.length, 2);
  assert.equal(element('loginPassword').value, '');
  assert.equal(element('workspace').hidden, false);
  assert.equal(element('loginPanel').hidden, true);
  assert.equal(vm.runInContext('authToken', context), 'new-token');
});


test('owner login displays staff panel and loads roster with bearer token', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/auth/login') return {
      ok: true, json: async () => ({ access_token: 'owner-session', role: 'owner', user_id: 7 })
    };
    if (url === '/staff') return { ok: true, json: async () => ({
      items: [{ user_id: 7, email: 'owner@example.com', role: 'owner', is_active: true }]
    }) };
    return { ok: true, json: async () => ({ total: 0, items: [] }) };
  });
  element('loginEmail').value = 'owner@example.com';
  element('loginOrg').value = 'gestoria';
  element('loginPassword').value = 'initial-password';
  await context.login({ preventDefault() {} });
  assert.equal(element('staffPanel').hidden, false);
  assert.equal(element('staffRoleAdmin').hidden, false);
  assert.ok(calls.some(x => x.url === '/staff' &&
    x.options.headers.Authorization === 'Bearer owner-session'));
  assert.match(element('staffBody').innerHTML, /owner@example.com/);
  assert.doesNotMatch(element('staffBody').innerHTML, /data-disable-id="7"/);
});

test('employee login cannot see or fetch staff panel', async () => {
  const requests = [];
  const { context, element } = setup(async url => {
    requests.push(url);
    if (url === '/auth/login') return { ok: true, json: async () =>
      ({ access_token: 'employee-session', role: 'employee', user_id: 9 }) };
    return { ok: true, json: async () => ({ total: 0, items: [] }) };
  });
  element('loginEmail').value = 'worker@example.com';
  element('loginOrg').value = 'gestoria';
  element('loginPassword').value = 'test-password';
  await context.login({ preventDefault() {} });
  assert.equal(element('staffPanel').hidden, true);
  assert.equal(element('staffRoleAdmin').hidden, true);
  assert.deepEqual(requests, ['/auth/login', '/analyses?limit=10&offset=0']);
});

test('staff creation sends protected JSON without leaving password in form', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/staff' && options.method === 'POST')
      return { ok: true, json: async () => ({ user_id: 11 }) };
    return { ok: true, json: async () => ({ items: [] }) };
  });
  vm.runInContext("currentRole = 'owner'; currentUserId = 7", context);
  element('staffEmail').value = 'person@example.com';
  element('staffRole').value = 'employee';
  element('staffPassword').value = 'temporary-long-password';
  await context.addStaff({ preventDefault() {} });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/staff');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer frontend-only-test-token');
  assert.equal(JSON.parse(calls[0].options.body).email, 'person@example.com');
  assert.equal(element('staffPassword').value, '');
  assert.match(element('staffSuccess').textContent, /Empleado añadido/);
});

test('staff roster escapes hostile data and manager can disable access', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    if (url === '/staff') return { ok: true, json: async () => ({ items: [
      { user_id: 12, email: '<img src=x onerror=alert(1)>', role: 'employee', is_active: true }
    ] }) };
    return { ok: true, json: async () => ({ status: 'disabled' }) };
  });
  vm.runInContext("currentRole = 'owner'; currentUserId = 7", context);
  await context.loadStaff();
  assert.match(element('staffBody').innerHTML, /&lt;img/);
  assert.doesNotMatch(element('staffBody').innerHTML, /<img/);
  await context.disableStaff(12);
  assert.ok(calls.some(x => x.url === '/staff/12/disable' &&
    x.options.method === 'POST' &&
    x.options.headers.Authorization === 'Bearer frontend-only-test-token'));
  assert.match(element('staffSuccess').textContent, /desactivado/);
});
