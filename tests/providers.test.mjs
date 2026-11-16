// providers.js (the browser's provider code) must behave exactly like the local server's.
//   node --test tests/
// Runs the same fixtures through both and compares; Python's answers come from tests/py_oracle.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const P = createRequire(import.meta.url)(path.join(ROOT, 'providers.js'));

function python(cases) {
  const r = spawnSync('python3', [path.join(ROOT, 'tests/py_oracle.py')], { input: JSON.stringify(cases), encoding: 'utf8' });
  if (r.status !== 0) throw new Error(r.stderr);
  return JSON.parse(r.stdout);
}
async function collect(gen) { const out = []; for await (const x of gen) out.push(x); return out; }
// An SSE body that arrives in awkward chunks (splitting lines and multi-byte characters).
function chunkedBody(bytes, size = 7) {
  let i = 0;
  return new ReadableStream({ pull(c) { if (i >= bytes.length) return c.close(); c.enqueue(bytes.slice(i, i += size)); } });
}

const SSE = readdirSync(path.join(ROOT, 'tests/fixtures/sse')).filter(f => f.endsWith('.sse')).sort();

test('stream parsers match the server for every fixture', async () => {
  const cases = SSE.map(f => ({ kind: 'parse', provider: f.split('-')[0], file: 'tests/fixtures/sse/' + f }));
  const want = python(cases);
  for (const [i, c] of cases.entries()) {
    const bytes = readFileSync(path.join(ROOT, c.file));
    const got = await collect(P.PARSERS[c.provider](P.sseEvents(chunkedBody(bytes))));
    assert.deepEqual(got, want[i], c.file);
  }
});

test('reasoning text is never passed on', async () => {
  const bytes = readFileSync(path.join(ROOT, 'tests/fixtures/sse/openrouter-ok.sse'));
  const got = await collect(P.PARSERS.openrouter(P.sseEvents(chunkedBody(bytes))));
  assert.ok(!JSON.stringify(got).includes('Let me think'));
});

test('HTTP error mapping matches the server', () => {
  const cases = [];
  const bodies = {
    openai: ['{"error":{"message":"Incorrect API key provided","type":"invalid_request_error","code":"invalid_api_key"}}', '{"error":{"message":"You exceeded your current quota","type":"insufficient_quota","code":"insufficient_quota"}}', '{"error":{"message":"model not found","code":"model_not_found"}}', 'not json at all'],
    anthropic: ['{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}', '{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', '{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low"}}', '{"type":"error","error":{"type":"invalid_request_error","message":"image input not supported by this model"}}'],
    gemini: ['{"error":{"code":400,"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT"}}', '{"error":{"code":429,"message":"Quota","status":"RESOURCE_EXHAUSTED"}}'],
    openrouter: ['{"error":{"message":"Provider returned error","code":502}}', '{"error":{"message":"upstream idle timeout","code":408}}', '{"error":{"message":"Insufficient credits","code":402}}', '{"error":{"message":"flagged by moderation","code":403}}', '{"detail":"Bad gateway"}']
  };
  for (const [provider, list] of Object.entries(bodies))
    for (const body of list)
      for (const status of [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504, 529])
        for (const images of [false, true]) cases.push({ kind: 'error', provider, status, body, images });
  const want = python(cases);
  cases.forEach((c, i) => {
    const e = P.mapProviderError(c.provider, c.status, c.body, c.images);
    assert.deepEqual({ code: e.code, message: e.message, status: e.status, retryable: e.retryable }, want[i], JSON.stringify(c));
  });
});

test('in-stream error codes match the server', () => {
  const cases = [401, '403', 402, 429, 503, 529, 500, 'overloaded_error', 'rate_limit_error', 'billing_error', 'weird', '', 'error']
    .map(code => ({ kind: 'stream_error', code, message: 'Insufficient credits? no' }))
    .concat([{ kind: 'stream_error', code: 'x', message: 'insufficient credits left' }]);
  const want = python(cases);
  cases.forEach((c, i) => assert.deepEqual(P.streamError(c.code, c.message), want[i], JSON.stringify(c)));
});

test('request bodies match the server', () => {
  const img = 'data:image/png;base64,iVBORw0KGgo=';
  const cases = [];
  for (const provider of ['openai', 'openrouter', 'anthropic', 'gemini'])
    for (const images of [[], [img, img.replace('png', 'jpeg')]])
      for (const effort of [null, 'low', 'minimal', 'bogus'])
        cases.push({ kind: 'build', provider, model: provider === 'gemini' ? 'gemini-3-flash' : 'm/x:free', instructions: 'sys', text: 'hi ✓', images, max_tokens: 8192, effort });
  cases.push({ kind: 'build', provider: 'anthropic', model: 'c', instructions: '', text: 't', images: [], max_tokens: 16000, effort: null });
  const want = python(cases);
  cases.forEach((c, i) => assert.deepEqual(P.buildRequest(c.provider, c.model, c.instructions, c.text, c.images, c.max_tokens, c.effort), want[i], JSON.stringify(c)));
});

test('model lists and defaults match the server', async () => {
  const fixtures = { openai: 'openai', anthropic: 'anthropic', gemini: 'gemini', openrouter: 'openrouter', 'openrouter-nocredits': 'openrouter' };
  const cases = Object.entries(fixtures).map(([f, provider]) => ({ kind: 'models', provider, fixture: `tests/fixtures/models/${f}.json` }));
  const want = python(cases);
  for (const [i, c] of cases.entries()) {
    const routes = JSON.parse(readFileSync(path.join(ROOT, c.fixture), 'utf8'));
    const seen = [];
    const fakeFetch = async (url, opts) => {
      seen.push(opts.headers);
      const hit = Object.entries(routes).find(([part]) => url.split('?')[0].endsWith(part));
      return hit ? new Response(JSON.stringify(hit[1]), { status: 200 }) : new Response('{"error":{"message":"nope"}}', { status: 404 });
    };
    const got = await P.listModels(c.provider, 'test-key', fakeFetch);
    assert.deepEqual(got, want[i], c.fixture);
    if (c.provider === 'anthropic') assert.equal(seen[0]['anthropic-dangerous-direct-browser-access'], 'true');
    if (c.provider === 'gemini') assert.equal(seen[0]['x-goog-api-key'], 'test-key');
  }
});

test('respond(): streams events, and maps HTTP and network failures', async () => {
  const sse = readFileSync(path.join(ROOT, 'tests/fixtures/sse/anthropic-ok.sse'));
  let sent;
  const ok = async (url, opts) => { sent = { url, opts }; return new Response(chunkedBody(sse), { status: 200 }); };
  const evs = await collect(P.respond({ provider: 'anthropic', key: 'k', model: 'claude-sonnet-5-5', instructions: 's', input: 'hi', fetch: ok }));
  assert.deepEqual(evs.at(-1), { done: true });
  assert.equal(evs.filter(e => e.delta).map(e => e.delta).join(''), 'Hello');
  assert.equal(sent.url, 'https://api.anthropic.com/v1/messages');
  assert.equal(sent.opts.headers['anthropic-dangerous-direct-browser-access'], 'true');

  const bad = async () => new Response('{"error":{"message":"Provider returned error","code":502}}', { status: 502 });
  await assert.rejects(collect(P.respond({ provider: 'openrouter', key: 'k', model: 'openrouter/free', input: 'x', fetch: bad })),
    e => e.code === 'http_502' && e.retryable === true);

  const offline = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(collect(P.respond({ provider: 'openai', key: 'k', model: 'gpt-5', input: 'x', fetch: offline })),
    e => e.code === 'upstream_unreachable');

  // Anthropic: step down max_tokens when the first size is refused.
  const sizes = [];
  const picky = async (url, opts) => {
    const b = JSON.parse(opts.body); sizes.push(b.max_tokens);
    return b.max_tokens > 8192 ? new Response('{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: 16000 > 8192"}}', { status: 400 }) : new Response(chunkedBody(sse));
  };
  await collect(P.respond({ provider: 'anthropic', key: 'k', model: 'old', input: 'x', fetch: picky }));
  assert.deepEqual(sizes, [16000, 8192]);

  // A stream that ends without a finish is "interrupted", as on the server.
  const cut = async () => new Response(chunkedBody(readFileSync(path.join(ROOT, 'tests/fixtures/sse/openrouter-no-end.sse'))));
  const tail = await collect(P.respond({ provider: 'openrouter', key: 'k', model: 'x', input: 'x', fetch: cut }));
  assert.deepEqual(tail.at(-1), { error: 'interrupted', message: '' });
});

test('checkKey(): bad shape, rejected key, no models', async () => {
  await assert.rejects(P.checkKey('openai', 'has space x'), e => e.code === 'invalid_key');
  await assert.rejects(P.checkKey('openrouter', 'sk-or-123456789'), e => e.code === 'bad_provider');
  const reject = async () => new Response('{"error":{"message":"Incorrect API key provided"}}', { status: 401 });
  await assert.rejects(P.checkKey('openai', 'sk-123456789', reject), e => e.code === 'invalid_key' && /OpenAI/.test(e.message));
  const empty = async () => new Response('{"data":[]}', { status: 200 });
  await assert.rejects(P.checkKey('openai', 'sk-123456789', empty), e => e.code === 'no_models');
});

test('PKCE: challenge is the S256 of the verifier', async () => {
  const { verifier, challenge } = await P.pkcePair();
  assert.match(verifier, /^[A-Za-z0-9_-]{43}$/);
  const { createHash } = await import('node:crypto');
  assert.equal(challenge, createHash('sha256').update(verifier).digest('base64url'));
  const u = new URL(P.openrouterAuthUrl('https://let-ai-explain.vercel.app/', challenge));
  assert.equal(u.origin + u.pathname, 'https://openrouter.ai/auth');
  assert.equal(u.searchParams.get('code_challenge_method'), 'S256');
});
