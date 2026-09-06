import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSessionCookie, hasValidSession } from '../src/auth.ts';
import { handleRequest } from '../src/handler.ts';
const password = 'a-random-test-password-at-least-24-characters';
const origin = 'https://studio.example';
const cookie = async () => (await createSessionCookie(password)).split(';')[0];
function environment() {
  const forwarded: Request[] = [];
  return { forwarded, env: { APP_ACCESS_PASSWORD: password, QINGZHI_CONTAINER: { getByName() { return { async fetch(req: Request) { forwarded.push(req); return new Response('studio'); } }; } } } };
}
test('missing password fails closed without starting a container', async () => {
  const { env, forwarded } = environment();
  env.APP_ACCESS_PASSWORD = '';
  assert.equal((await handleRequest(new Request(origin), env)).status, 503);
  assert.equal(forwarded.length, 0);
});
test('session signature, expiry and password rotation', async () => {
  const now = Date.now();
  const signed = (await createSessionCookie(password, now)).split(';')[0];
  const req = new Request(origin, {headers: {Cookie: signed}});
  assert.equal(await hasValidSession(req, password, now), true);
  assert.equal(await hasValidSession(req, password + 'changed', now), false);
  assert.equal(await hasValidSession(req, password, now + 13 * 3600000), false);
  assert.equal(await hasValidSession(new Request(origin, {headers: {Cookie: signed + 'x'}}), password), false);
});
test('login rejects cross-origin and wrong passwords; issues secure cookie', async () => {
  const { env } = environment();
  const req = (body: string, from = origin) => new Request(origin + '/_auth/login', {method:'POST', headers: {Origin:from, 'Content-Type':'application/x-www-form-urlencoded'}, body});
  assert.equal((await handleRequest(req('password=' + password, 'https://evil.example'), env)).status, 403);
  assert.equal((await handleRequest(req('password=wrong'), env)).status, 401);
  const response = await handleRequest(req('password=' + password), env);
  assert.equal(response.status, 303);
  assert.match(response.headers.get('set-cookie')!, /HttpOnly; Secure; SameSite=Strict/);
});
test('all assets/downloads protected; auth cookie stripped upstream', async () => {
  const { env, forwarded } = environment();
  for (const path of ['/','/media/private.mp4','/_stcore/stream']) {
    assert.equal((await handleRequest(new Request(origin + path), env)).status, 401);
  }
  const response = await handleRequest(new Request(origin + '/media/private.mp4', {headers: {Cookie: await cookie() + '; app=ok'}}), env);
  assert.equal(response.status, 200);
  assert.equal(forwarded[0].headers.get('Cookie')?.trim(), 'app=ok');
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
});
test('WebSocket requires same origin and authenticated cookie', async () => {
  const { env, forwarded } = environment();
  const headers = {Cookie: await cookie(), Upgrade:'websocket', Origin:'https://evil.example'};
  assert.equal((await handleRequest(new Request(origin + '/_stcore/stream', {headers}), env)).status, 403);
  headers.Origin = origin;
  assert.equal((await handleRequest(new Request(origin + '/_stcore/stream', {headers}), env)).status, 200);
  assert.equal(forwarded[0].headers.get('Upgrade'), 'websocket');
});
