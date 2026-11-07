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

test('staff invitations are issued without sending employee passwords', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ invite_token: 'random-once-secret', expires_in: 86400 }) };
  });
  vm.runInContext("currentRole = 'owner'; currentUserId = 7", context);
  element('loginOrg').value = 'my-office';
  element('staffEmail').value = 'person@example.com';
  element('staffRole').value = 'employee';
  await context.addStaff({ preventDefault() {} });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/staff/invitations');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer frontend-only-test-token');
  assert.equal(JSON.parse(calls[0].options.body).email, 'person@example.com');
  assert.equal(JSON.parse(calls[0].options.body).password, undefined);
  assert.match(element('staffInviteLink').value, /#invite=random-once-secret&org=my-office/);
  assert.equal(element('staffInviteLinkPanel').hidden, false);
});

test('invitee accepts a link with their own password and token clears', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ status: 'accepted' }) };
  });
  vm.runInContext("pendingInviteToken = 'one-time-token-value'", context);
  element('inviteNewPassword').value = 'invitee-custom-strong-pass';
  element('inviteConfirmPassword').value = 'invitee-custom-strong-pass';
  await context.acceptInvite({ preventDefault() {} });
  assert.equal(calls[0].url, '/auth/invitations/accept');
  assert.equal(JSON.parse(calls[0].options.body).token, 'one-time-token-value');
  assert.equal(JSON.parse(calls[0].options.body).password, 'invitee-custom-strong-pass');
  assert.equal(element('inviteNewPassword').value, '');
  assert.equal(element('loginPanel').hidden, false);
  assert.equal(vm.runInContext('pendingInviteToken', context), null);
});

test('password change invalidates current browser session', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ status: 'updated' }) };
  });
  element('currentPassword').value = 'previous-password';
  element('newPassword').value = 'brand-new-secure-pass';
  element('newPasswordRepeat').value = 'brand-new-secure-pass';
  await context.updatePassword({ preventDefault() {} });
  assert.equal(calls[0].url, '/auth/password');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer frontend-only-test-token');
  assert.equal(JSON.parse(calls[0].options.body).new_password, 'brand-new-secure-pass');
  assert.equal(vm.runInContext('authToken', context), null);
  assert.equal(element('currentPassword').value, '');
  assert.equal(element('workspace').hidden, true);
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


test('invitation URL fragment is cleared before showing acceptance form', () => {
  const { context, element } = setup(async () => ({ ok: true }));
  vm.runInContext(`
    window.location.hash = '#invite=abcdefghijklmnopqrstuvwxyz123456&org=gestoria';
    window.location.pathname = '/';
    window.history = { replaceState(_a, _b, uri) { window.location.hash = ''; window.location.pathname = uri; } };
  `, context);
  context.initializeInvite();
  assert.equal(element('invitePanel').hidden, false);
  assert.equal(element('loginPanel').hidden, true);
  assert.equal(element('loginOrg').value, 'gestoria');
  assert.equal(vm.runInContext('pendingInviteToken', context), 'abcdefghijklmnopqrstuvwxyz123456');
  assert.equal(vm.runInContext('window.location.hash', context), '');
});

test('only owners and admins see safely escaped report deletion controls', async () => {
  for (const role of ['owner', 'admin', 'employee']) {
    const { context, element } = setup(async () => ({ ok: true, json: async () => ({
      total: 1, items: [{ id: 42, filename: '<script>alert(1)</script>', score: 70, status: 'REVISAR' }]
    }) }));
    vm.runInContext(`currentRole = '${role}'`, context);
    await context.loadAnalyses();
    const html = element('historyBody').innerHTML;
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
    assert.equal(html.includes('data-delete-id="42"'), role !== 'employee');
    assert.match(html, /data-export-id="42"/);
  }
});

test('cancelled report deletion sends no request and keeps the report', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options) => {
    calls.push({ url, options });
    return { status: 204 };
  });
  vm.runInContext("currentRole = 'owner'", context);
  context.confirm = () => false;
  const button = element('deleteButton');
  await context.deleteAnalysis('42', 'reporte.pdf', button);
  assert.equal(calls.length, 0);
  assert.equal(button.disabled, false);
});

test('report deletion requires manager role, uses bearer DELETE, refreshes and reports success', async () => {
  const calls = [];
  const { context, element } = setup(async (url, options = {}) => {
    calls.push({ url, options });
    if (options.method === 'DELETE') return { status: 204 };
    return { ok: true, json: async () => ({ total: 0, items: [] }) };
  });
  const button = element('deleteButton');
  vm.runInContext("currentRole = 'employee'", context);
  await context.deleteAnalysis('42', 'doc.pdf', button);
  assert.equal(calls.length, 0);
  vm.runInContext("currentRole = 'owner'", context);
  await context.deleteAnalysis('42', 'doc.pdf', button);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/analyses/42');
  assert.equal(calls[0].options.method, 'DELETE');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer frontend-only-test-token');
  assert.match(calls[1].url, /^\/analyses\?/);
  assert.match(element('historySuccess').textContent, /Informe eliminado/);
  assert.equal(element('historyError').textContent, '');
  assert.equal(button.disabled, false);
});

test('concurrent clicks cannot delete twice and failures do not expose server internals', async () => {
  let resolve;
  const calls = [];
  const { context, element } = setup((url, options) => {
    calls.push(url);
    return new Promise(r => { resolve = r; });
  });
  vm.runInContext("currentRole = 'admin'", context);
  const button = element('deleteButton');
  const first = context.deleteAnalysis(42, 'doc.pdf', button);
  await context.deleteAnalysis(42, 'doc.pdf', button);
  assert.deepEqual(calls, ['/analyses/42']);
  assert.equal(button.disabled, true);
  resolve({ status: 500, json: async () => ({ detail: 'private SQL error' }) });
  await first;
  assert.equal(button.disabled, false);
  assert.match(element('historyError').textContent, /No se pudo eliminar/);
  assert.doesNotMatch(element('historyError').textContent, /private SQL/);
});

test('logout during report deletion does not restore private history or show notices', async () => {
  let resolve;
  const { context, element } = setup(() => new Promise(r => { resolve = r; }));
  vm.runInContext("currentRole = 'owner'", context);
  const first = context.deleteAnalysis(42, 'doc.pdf', element('deleteButton'));
  context.logout();
  resolve({ status: 204 });
  await first;
  assert.equal(element('historySuccess').textContent, '');
  assert.equal(element('historyBody').innerHTML, '');
});

test('stale 401 response cannot log out a newly authenticated session', async () => {
  let finish;
  const { context } = setup(() => new Promise(resolve => { finish = resolve; }));
  const oldRequest = context.apiFetch('/analyses');
  context.logout();
  vm.runInContext("authToken = 'new-session'", context);
  finish({ status: 401 });
  await assert.rejects(oldRequest, /La sesión ha caducado/);
  assert.equal(vm.runInContext('authToken', context), 'new-session');
});

test('late report detail never renders tenant A data in tenant B session', async () => {
  let finish;
  const { context, element } = setup(() => new Promise(resolve => { finish = resolve; }));
  const oldRequest = context.openDetail(123);
  context.logout();
  vm.runInContext("authToken = 'new-session'", context);
  finish({ ok: true, json: async () => ({
    id: 123, filename: 'tenant-A-private.pdf', score: 91, findings: []
  }) });
  await oldRequest;
  assert.doesNotMatch(element('modalSubtitle').textContent, /tenant-A-private/);
  assert.equal(element('modalContent').innerHTML, '');
});

test('late analysis does not repaint UI or refresh history after logout', async () => {
  let finish;
  const calls = [];
  const { context, element } = setup(url => {
    calls.push(url);
    return new Promise(resolve => { finish = resolve; });
  });
  const oldRequest = context.analyze();
  context.logout();
  vm.runInContext("authToken = 'new-session'", context);
  finish({ ok: true, json: async () => ({
    id: 5, filename: 'tenant-A-private.pdf', score: 93, findings: []
  }) });
  await oldRequest;
  assert.deepEqual(calls, ['/analyze']);
  assert.equal(element('analysisCard').innerHTML, '');
});

test('late export does not download a prior tenant report', async () => {
  let finish;
  let alerts = 0;
  const { context } = setup(() => new Promise(resolve => { finish = resolve; }));
  context.alert = () => { alerts++; };
  const oldRequest = context.downloadExport(123);
  context.logout();
  vm.runInContext("authToken = 'new-session'", context);
  finish({ ok: true, blob: async () => { throw Error('Must not read old report blob'); } });
  await oldRequest;
  assert.equal(alerts, 0);
});

test('pending login cannot resurrect credentials after logout', async () => {
  let finish;
  const { context } = setup(() => new Promise(resolve => { finish = resolve; }));
  const pending = context.login({ preventDefault() {} });
  context.logout();
  finish({ ok: true, json: async () => ({ access_token: 'stale-token' }) });
  await pending;
  assert.equal(vm.runInContext('authToken', context), null);
});
