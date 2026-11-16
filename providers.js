/* Let AI Explain: calling AI providers straight from the browser (hosted mode).
 *
 * A port of the local server's provider code in let_ai_explain.py: request bodies, the SSE
 * stream parsers, error mapping and model lists. It yields the same page events as the
 * server's /api/respond stream: {delta}, then {done} or {error, message[, retryable]}, plus
 * progress {model}, {wait} and {reasoning}. tests/providers.test.mjs runs the same SSE
 * fixtures through both versions so they can't drift apart.
 *
 * Plain script: in the page it sets window.LAEProviders; in Node, module.exports.
 * Keys are passed in by the caller and sent only to the provider they belong to.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LAEProviders = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const APP_NAME = 'Let AI Explain';
  const APP_URL = 'https://github.com/viyaleta/let-ai-explain'; // sent to OpenRouter as HTTP-Referer
  const OPENAI_API = 'https://api.openai.com/v1';
  const OPENROUTER_API = 'https://openrouter.ai/api/v1';
  const OPENROUTER_AUTH = 'https://openrouter.ai/auth';
  const OPENROUTER_FREE = 'openrouter/free';
  const ANTHROPIC_API = 'https://api.anthropic.com/v1';
  const ANTHROPIC_VERSION = '2023-06-01';
  const GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta';

  const PROVIDERS = {
    openrouter: { name: 'OpenRouter', usage_url: 'https://openrouter.ai/settings/credits' },
    openai: { name: 'OpenAI', usage_url: 'https://platform.openai.com/usage' },
    anthropic: { name: 'Anthropic', usage_url: 'https://console.anthropic.com/settings/usage' },
    gemini: { name: 'Google Gemini', usage_url: 'https://aistudio.google.com/usage' }
  };
  const KEY_PROVIDERS = ['openai', 'anthropic', 'gemini'];
  const API_KEY_SHAPE = /^[\x21-\x7e]{8,512}$/; // printable, no spaces: safe in a header
  const REASONING_EFFORTS = ['minimal', 'low', 'medium', 'high'];

  class ProviderError extends Error {
    constructor(code, message = '', status = 400, retryable = false) {
      super(message || code);
      this.name = 'ProviderError';
      this.code = code; this.status = status; this.retryable = !!retryable;
    }
  }

  function keyHeaders(provider, key) {
    if (provider === 'anthropic') return { 'x-api-key': key, 'anthropic-version': ANTHROPIC_VERSION, 'anthropic-dangerous-direct-browser-access': 'true' };
    if (provider === 'gemini') return { 'x-goog-api-key': key }; // a header, never ?key= (see geminiReachable)
    const h = { Authorization: 'Bearer ' + key };
    if (provider === 'openrouter') Object.assign(h, { 'HTTP-Referer': APP_URL, 'X-Title': APP_NAME });
    return h;
  }

  /* ---------------- errors ---------------- */
  function errorFromBody(text, status) {
    let j;
    try { j = JSON.parse(text); } catch { return [`http_${status}`, String(text || '').slice(0, 300)]; }
    if (j && typeof j.error === 'object' && j.error) return [j.error.code || j.error.type || `http_${status}`, j.error.message || ''];
    if (j && typeof j.error === 'string') return [j.error, j.error_description || ''];
    if (j && 'detail' in j) return [`http_${status}`, String(j.detail)];
    return [`http_${status}`, ''];
  }
  const RETRYABLE_STATUS = [502, 503, 504];
  const IDLE_TIMEOUT = /upstream idle timeout|idle timeout/i;
  function openrouterRetryable(status, message) {
    const n = Number.parseInt(status, 10);
    return RETRYABLE_STATUS.includes(n) || IDLE_TIMEOUT.test(String(message || ''));
  }
  function mapProviderError(provider, status, text, images = false) {
    let [code, msg] = errorFromBody(text, status);
    code = String(code);
    const blob = String(text || '').slice(0, 4000).toLowerCase(), name = PROVIDERS[provider]?.name || provider;
    let e;
    if (images && [400, 404, 415, 422].includes(status) && /image|vision|multimodal|modalit/.test(blob)) e = new ProviderError('images_unsupported', msg || "This model doesn't accept images.", 400);
    else if (status === 402 || /insufficient[_ ](credits|quota|balance)|credit balance is too low|out of credits/.test(blob)) e = new ProviderError('out_of_credits', msg, 402);
    // Google: a key limited to other websites, IP addresses, apps or APIs (API_KEY_*_BLOCKED).
    else if (provider === 'gemini' && /api_key_[a-z_]+_blocked|requests from referer/.test(blob)) e = new ProviderError('key_restricted', msg || 'Your Google API key is restricted.', 403);
    else if (status === 401 || /api_key_invalid|api key not valid|invalid x-api-key|invalid api key|incorrect api key/.test(blob)) e = new ProviderError('invalid_key', msg || `${name} didn't accept the key.`, 401);
    else if (status === 403) e = provider === 'openrouter' && /moderat|flagged/.test(blob) ? new ProviderError('blocked', msg, 403) : new ProviderError('invalid_key', msg || `${name} didn't allow this key.`, 403);
    else if (status === 429) e = new ProviderError('rate_limited', msg, 429);
    else if (status === 404) e = new ProviderError('model_not_found', msg, 404);
    else if (status === 503 || status === 529 || blob.includes('overloaded')) e = new ProviderError('provider_busy', msg, 503);
    else e = new ProviderError(code && !/^\d+$/.test(code) ? code : `http_${status}`, msg, status < 500 ? status : 502);
    if (provider === 'openrouter' && openrouterRetryable(status, text)) e.retryable = true;
    return e;
  }
  const STREAM_ERROR_TYPES = {
    overloaded_error: 'provider_busy', rate_limit_error: 'rate_limited', authentication_error: 'invalid_key',
    permission_error: 'invalid_key', not_found_error: 'model_not_found', billing_error: 'out_of_credits'
  };
  const STREAM_STATUS = { 401: 'invalid_key', 403: 'invalid_key', 402: 'out_of_credits', 404: 'model_not_found', 429: 'rate_limited', 503: 'provider_busy', 529: 'provider_busy' };
  function streamError(code, message = '') {
    if (typeof code === 'number' || (typeof code === 'string' && /^\d+$/.test(code))) code = STREAM_STATUS[+code] || `http_${code}`;
    code = STREAM_ERROR_TYPES[code] || code || 'error';
    if (/insufficient credits|credit balance/i.test(String(message))) code = 'out_of_credits';
    return { error: code, message: String(message || '').slice(0, 500) };
  }
  function midstreamError(e) {
    const err = streamError(e.code || 'error', e.message || '');
    if (err.error === 'error' || err.error.startsWith('http_5')) err.error = 'model_error';
    if (openrouterRetryable(e.code, e.message || '')) err.retryable = true;
    return err;
  }

  /* ---------------- SSE ---------------- */
  // Lines of an SSE stream → the decoded JSON "data:" objects; ":" comment lines become {_comment}.
  function sseEvent(raw) {
    const line = raw.trim();
    if (line.startsWith(':')) return { _comment: line.slice(1).trim() };
    if (!line.startsWith('data:')) return null;
    const data = line.slice(5).trim();
    if (!data || data === '[DONE]') return null;
    try { const ev = JSON.parse(data); return ev && typeof ev === 'object' && !Array.isArray(ev) ? ev : null; } catch { return null; }
  }
  async function* sseEvents(body) {
    const reader = body.getReader(), dec = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const ev = sseEvent(buf.slice(0, i)); buf = buf.slice(i + 1);
          if (ev) yield ev;
        }
      }
      buf += dec.decode();
      if (buf) { const ev = sseEvent(buf); if (ev) yield ev; }
    } finally { reader.releaseLock?.(); }
  }
  function* linesToEvents(lines) { for (const l of lines) { const ev = sseEvent(l); if (ev) yield ev; } }

  /* ---------------- stream parsers (same as the server's) ---------------- */
  function finish(reason, ok, cut) {
    if (reason == null) return [];
    if (cut.includes(reason)) return [{ error: 'incomplete', message: 'max_output_tokens' }];
    if (ok.includes(reason)) return [{ done: true }];
    return [{ error: 'blocked', message: String(reason) }];
  }
  async function* parseOpenAI(events) {
    let model = null;
    for await (const ev of events) {
      const t = ev.type;
      const m = ev.response && typeof ev.response === 'object' ? ev.response.model : null;
      if (typeof m === 'string' && m && m !== model) { model = m; yield { model }; }
      if (t === 'response.output_text.delta') yield { delta: ev.delta ?? '' };
      else if (t === 'response.completed') { yield { done: true }; return; }
      else if (t === 'response.failed') { const err = ev.response?.error || {}; yield { error: err.code || 'response_failed', message: err.message || '' }; return; }
      else if (t === 'response.incomplete') { const det = ev.response?.incomplete_details || {}; yield { error: 'incomplete', message: det.reason || '' }; return; }
      else if (t === 'error') { const err = ev.error && typeof ev.error === 'object' ? ev.error : ev; yield { error: err.code || 'error', message: err.message || '' }; return; }
    }
  }
  async function* parseOpenRouter(events) {
    let reason = null, model = null, reasoning = 0;
    for await (const ev of events) {
      if ('_comment' in ev) { yield { wait: true }; continue; }
      if (ev.error && typeof ev.error === 'object') { yield midstreamError(ev.error); return; }
      if (typeof ev.model === 'string' && ev.model && ev.model !== model) { model = ev.model; yield { model }; }
      const ch = (ev.choices || [])[0];
      if (!ch) continue;
      const delta = ch.delta || {};
      if ((typeof delta.reasoning === 'string' && delta.reasoning) || (delta.reasoning_details && delta.reasoning_details.length !== 0)) yield { reasoning: ++reasoning };
      if (typeof delta.content === 'string' && delta.content) yield { delta: delta.content };
      if (ch.finish_reason === 'error' || (ch.error && typeof ch.error === 'object')) { yield midstreamError(ch.error || ev.error || {}); return; }
      reason = ch.finish_reason || reason;
    }
    yield* finish(reason, ['stop', 'tool_calls', 'function_call'], ['length']);
  }
  async function* parseAnthropic(events) {
    let stop = null;
    for await (const ev of events) {
      const t = ev.type;
      if (t === 'message_start' && typeof ev.message?.model === 'string') yield { model: ev.message.model };
      if (t === 'content_block_delta') { const d = ev.delta || {}; if (d.type === 'text_delta' && d.text) yield { delta: d.text }; }
      else if (t === 'message_delta') stop = ev.delta?.stop_reason || stop;
      else if (t === 'message_stop') { yield* finish(stop || 'end_turn', ['end_turn', 'stop_sequence', 'pause_turn'], ['max_tokens', 'model_context_window_exceeded']); return; }
      else if (t === 'error') { const e = ev.error || {}; yield streamError(e.type || 'error', e.message || ''); return; }
    }
  }
  async function* parseGemini(events) {
    let reason = null, model = null;
    for await (const ev of events) {
      if (typeof ev.modelVersion === 'string' && ev.modelVersion && ev.modelVersion !== model) { model = ev.modelVersion; yield { model }; }
      if (ev.error && typeof ev.error === 'object') { yield streamError(ev.error.code || ev.error.status, ev.error.message || ''); return; }
      const block = ev.promptFeedback?.blockReason;
      if (block) { yield { error: 'blocked', message: block }; return; }
      const cand = (ev.candidates || [])[0];
      if (!cand) continue;
      for (const part of cand.content?.parts || []) {
        if (part.thought) continue; // thinking summaries aren't part of the answer
        if (typeof part.text === 'string' && part.text) yield { delta: part.text };
      }
      reason = cand.finishReason || reason;
    }
    yield* finish(reason, ['STOP', 'FINISH_REASON_UNSPECIFIED'], ['MAX_TOKENS']);
  }
  const PARSERS = { openai: parseOpenAI, openrouter: parseOpenRouter, anthropic: parseAnthropic, gemini: parseGemini };

  /* ---------------- request bodies (same as the server's) ---------------- */
  const splitDataUrl = u => { const [head, data] = u.split(','); return [head.slice(5).split(';')[0], data]; };
  function buildRequest(provider, model, instructions, text, images = [], maxTokens = 16000, effort = null) {
    if (provider === 'openai') {
      const content = images.length ? [{ type: 'input_text', text }, ...images.map(u => ({ type: 'input_image', image_url: u, detail: 'low' }))] : text;
      return [OPENAI_API + '/responses', { model, instructions, input: [{ role: 'user', content }], store: false, stream: true }];
    }
    if (provider === 'openrouter') {
      const content = images.length ? [{ type: 'text', text }, ...images.map(u => ({ type: 'image_url', image_url: { url: u, detail: 'low' } }))] : text;
      const messages = [...(instructions ? [{ role: 'system', content: instructions }] : []), { role: 'user', content }];
      const body = { model, messages, stream: true };
      if (REASONING_EFFORTS.includes(effort)) body.reasoning = { effort }; // models without reasoning ignore it
      return [OPENROUTER_API + '/chat/completions', body];
    }
    if (provider === 'anthropic') {
      const content = [...images.map(splitDataUrl).map(([mt, d]) => ({ type: 'image', source: { type: 'base64', media_type: mt, data: d } })), { type: 'text', text }];
      const body = { model, max_tokens: maxTokens, stream: true, messages: [{ role: 'user', content }] };
      if (instructions) body.system = instructions;
      return [ANTHROPIC_API + '/messages', body];
    }
    if (provider === 'gemini') {
      const parts = [{ text }, ...images.map(splitDataUrl).map(([mt, d]) => ({ inline_data: { mime_type: mt, data: d } }))];
      const body = { contents: [{ role: 'user', parts }] };
      if (instructions) body.systemInstruction = { parts: [{ text: instructions }] };
      return [`${GEMINI_API}/models/${encodeURIComponent(model)}:streamGenerateContent?alt=sse`, body];
    }
    throw new ProviderError('bad_provider', 'Unknown provider.');
  }

  /* ---------------- HTTP ---------------- */
  // Network failures (offline, DNS, a CORS-blocked error reply) become upstream_unreachable.
  async function send(provider, url, opts, f = fetch, images = false) {
    let r;
    try { r = await f(url, opts); }
    catch (e) {
      if (e?.name === 'AbortError' || e?.name === 'TimeoutError') throw e;
      if (provider === 'gemini' && await geminiReachable(f)) throw new ProviderError('key_restricted', 'Your Google API key is restricted.', 403);
      throw new ProviderError('upstream_unreachable', `Couldn't reach ${PROVIDERS[provider].name}.`, 502);
    }
    if (!r.ok) throw mapProviderError(provider, r.status, await r.text().catch(() => ''), images);
    return r;
  }
  // The Gemini key goes in the x-goog-api-key header, never ?key=: Google's CORS preflight allows
  // that header from any site (checked from the hosted site; see tests/provider_cors_check.sh), and
  // a header keeps the key out of URLs, history and logs. The model list and answers use it alike.
  // When a keyed request fails without any readable reply, ask Google the same way with a dummy key:
  // if that one gets an answer, Google is reachable from this page and the header passes CORS, so
  // the failure is this key's own restrictions (an error Google may send without CORS headers).
  async function geminiReachable(f) {
    try {
      const signal = typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(8000) : undefined;
      await f(`${GEMINI_API}/models?pageSize=1`, { headers: { Accept: 'application/json', 'x-goog-api-key': 'reachability-check' }, signal });
      return true;
    } catch { return false; }
  }
  async function getJson(provider, url, key, f = fetch, signal) {
    return (await send(provider, url, { headers: { Accept: 'application/json', ...keyHeaders(provider, key) }, signal }, f)).json();
  }

  /* ---------------- model lists (same as the server's) ---------------- */
  const OPENAI_SKIP = /audio|realtime|tts|transcribe|image|search|embedding|moderation|instruct|codex|computer|deep-research|davinci|babbage|gpt-3\.5|-\d{4}-\d{2}-\d{2}$|-\d{4}$/;
  const GEMINI_SKIP = /embedding|tts|image|live|native-audio|robotics|computer-use|aqa|learnlm|gemma|veo|imagen/;
  const versionOf = (text, pattern) => { const m = String(text).match(pattern); return m ? Number.parseFloat(m[1]) : 0; };
  const maxBy = (arr, key, dflt) => arr.length ? arr.reduce((a, b) => (key(b) > key(a) ? b : a)) : dflt;

  async function openrouterCredits(key, f = fetch) {
    try {
      const d = (await getJson('openrouter', OPENROUTER_API + '/credits', key, f)).data || {};
      return Number(d.total_credits || 0) - Number(d.total_usage || 0);
    } catch {}
    let d;
    try { d = (await getJson('openrouter', OPENROUTER_API + '/key', key, f)).data || {}; } catch { return null; }
    if (d.is_free_tier) return 0;
    return typeof d.limit_remaining === 'number' ? d.limit_remaining : null;
  }

  // Models the key can use for chat, newest first where the provider says, plus a sensible default.
  async function listModels(provider, key, f = fetch) {
    if (provider === 'openai') {
      const ids = ((await getJson(provider, OPENAI_API + '/models', key, f)).data || []).map(m => m.id).filter(Boolean)
        .filter(i => /^(gpt-|o\d|chatgpt-)/.test(i) && !OPENAI_SKIP.test(i)).sort().reverse();
      const general = ids.filter(i => /^gpt-\d+(\.\d+)?$/.test(i));
      const dflt = maxBy(general, i => versionOf(i, /gpt-(\d+(?:\.\d+)?)/), null);
      return { models: ids.map(i => ({ slug: i, name: i })), default: dflt || ids[0] || null };
    }
    if (provider === 'anthropic') {
      const data = ((await getJson(provider, ANTHROPIC_API + '/models?limit=1000', key, f)).data || [])
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
      const models = data.filter(m => m.id).map(m => ({ slug: m.id, name: m.display_name || m.id }));
      const sonnet = models.map(m => m.slug).filter(s => s.includes('sonnet'));
      return { models, default: (sonnet.length ? sonnet : models.map(m => m.slug))[0] || null };
    }
    if (provider === 'gemini') {
      let found = [], token = '';
      for (let i = 0; i < 5; i++) {
        const j = await getJson(provider, `${GEMINI_API}/models?pageSize=1000${token ? '&pageToken=' + encodeURIComponent(token) : ''}`, key, f);
        found = found.concat(j.models || []);
        token = j.nextPageToken || '';
        if (!token) break;
      }
      const models = [];
      for (const m of found) {
        const slug = String(m.name || '').replace(/^models\//, '');
        if ((m.supportedGenerationMethods || []).includes('generateContent') && slug.includes('gemini') && !GEMINI_SKIP.test(slug))
          models.push({ slug, name: m.displayName || slug });
      }
      const gv = s => versionOf(s, /gemini-(\d+(?:\.\d+)?)/);
      models.sort((a, b) => gv(b.slug) - gv(a.slug) || (b.slug > a.slug ? 1 : b.slug < a.slug ? -1 : 0));
      const flash = models.map(m => m.slug).filter(s => /^gemini-\d+(\.\d+)?-flash(-preview.*)?$/.test(s));
      // Newest stable Flash first; a preview only when no stable one is listed.
      flash.sort((a, b) => (Number(!b.includes('preview')) - Number(!a.includes('preview'))) || gv(b) - gv(a));
      return { models, default: (flash.length ? flash : models.map(m => m.slug))[0] || null };
    }
    if (provider === 'openrouter') {
      const data = (await getJson(provider, OPENROUTER_API + '/models', key, f)).data || [];
      const models = [];
      for (const m of data) {
        const arch = m.architecture || {};
        if (m.id && (arch.output_modalities || ['text']).includes('text'))
          models.push({ slug: m.id, name: m.name || m.id, images: (arch.input_modalities || []).includes('image') });
      }
      models.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
      if (!models.some(m => m.slug === OPENROUTER_FREE)) models.unshift({ slug: OPENROUTER_FREE, name: 'Free models (OpenRouter picks)', images: true });
      const credits = await openrouterCredits(key, f);
      let dflt;
      if (credits !== null && credits <= 0) dflt = OPENROUTER_FREE;
      else {
        const flash = models.map(m => m.slug).filter(s => /^google\/gemini-\d+(\.\d+)?-flash$/.test(s));
        dflt = maxBy(flash, s => versionOf(s, /gemini-(\d+(?:\.\d+)?)/), OPENROUTER_FREE);
      }
      return { models, default: dflt, credits };
    }
    throw new ProviderError('bad_provider', 'Unknown provider.');
  }

  // Checking a pasted key: it works if it can list models.
  async function checkKey(provider, key, f = fetch) {
    const name = PROVIDERS[provider]?.name;
    if (!KEY_PROVIDERS.includes(provider)) throw new ProviderError('bad_provider', 'Choose OpenAI, Anthropic or Google Gemini.');
    if (!API_KEY_SHAPE.test(key)) throw new ProviderError('invalid_key', "That doesn't look like an API key.");
    let list;
    try { list = await listModels(provider, key, f); }
    catch (e) {
      if (e.code === 'key_restricted') throw e;
      if (e.code === 'invalid_key' || e.code === 'model_not_found' || [400, 401, 403].includes(e.status)) throw new ProviderError('invalid_key', `${name} didn't accept that key.`);
      throw e;
    }
    if (!list.models.length) throw new ProviderError('no_models', 'That key works, but it has no chat models available.');
    return list;
  }

  /* ---------------- one streamed answer ---------------- */
  // Yields the page events for one answer. Errors before the stream starts are thrown as ProviderError.
  async function* respond({ provider, key, model, instructions = '', input, images = [], effort = null, signal, fetch: f = fetch }) {
    const headers = { ...keyHeaders(provider, key), 'Content-Type': 'application/json', Accept: 'text/event-stream' };
    let r;
    // Some older Claude models allow fewer output tokens; step down if the first size is refused.
    for (const maxTokens of [16000, 8192, 4096]) {
      const [url, body] = buildRequest(provider, model, instructions, input, images, maxTokens, effort);
      try { r = await send(provider, url, { method: 'POST', headers, body: JSON.stringify(body), signal }, f, images.length > 0); break; }
      catch (e) {
        if (!(provider === 'anthropic' && e.status === 400 && String(e.message).includes('max_tokens') && maxTokens > 4096)) throw e;
      }
    }
    let ended = false;
    try {
      for await (const ev of PARSERS[provider](sseEvents(r.body))) {
        yield ev;
        if ('done' in ev || 'error' in ev) { ended = true; return; }
      }
    } catch (e) {
      if (e?.name === 'AbortError') throw e;
      ended = true;
      yield { error: 'interrupted', message: '' };
      return;
    } finally {
      if (!ended) try { await r.body?.cancel?.(); } catch {}
    }
    if (!ended) yield { error: 'interrupted', message: '' };
  }

  /* ---------------- OpenRouter sign-in (OAuth PKCE) ---------------- */
  const b64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  async function pkcePair() {
    const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    return { verifier, challenge };
  }
  const openrouterAuthUrl = (callbackUrl, challenge) =>
    `${OPENROUTER_AUTH}?${new URLSearchParams({ callback_url: callbackUrl, code_challenge: challenge, code_challenge_method: 'S256' })}`;
  async function openrouterExchange(code, verifier, f = fetch) {
    const r = await send('openrouter', OPENROUTER_API + '/auth/keys', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, code_verifier: verifier, code_challenge_method: 'S256' })
    }, f);
    const key = (await r.json()).key;
    if (!key) throw new ProviderError('no_key', "OpenRouter didn't return a key.");
    return key;
  }

  return {
    PROVIDERS, KEY_PROVIDERS, OPENROUTER_FREE, ProviderError, keyHeaders,
    errorFromBody, mapProviderError, streamError, midstreamError, openrouterRetryable,
    sseEvent, sseEvents, linesToEvents, PARSERS, buildRequest, listModels, checkKey, respond,
    pkcePair, openrouterAuthUrl, openrouterExchange, maskKey: k => (k ? '••••' + String(k).slice(-4) : '')
  };
});
