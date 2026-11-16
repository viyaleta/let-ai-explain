#!/bin/bash
# Can a page on the hosted site call each AI provider straight from the browser?
# Sends the preflight a browser would send, then real requests with fake keys, and prints the
# CORS headers. A response without access-control-allow-origin can't be read by the page, so the
# page sees a network error instead of the provider's error message.
#
#   tests/provider_cors_check.sh [origin]     (default origin: https://let-ai-explain.vercel.app)
#
# Uses no real keys and sends no user data.
set -uo pipefail
O="${1:-https://let-ai-explain.vercel.app}"
ok=0; bad=0

check() {  # name, expect (yes|no), curl args...
  local name=$1 expect=$2; shift 2
  local h; h=$(curl -s -o /dev/null -D - --max-time 20 -H "Origin: $O" "$@" | tr -d '\r')
  local status; status=$(printf '%s\n' "$h" | awk 'toupper($1) ~ /^HTTP/ {print $2; exit}')
  local acao; acao=$(printf '%s\n' "$h" | awk -F': ' 'tolower($1)=="access-control-allow-origin" {print $2; exit}')
  local readable=no; { [ "$acao" = "*" ] || [ "$acao" = "$O" ]; } && readable=yes
  local mark=OK; [ "$readable" != "$expect" ] && { mark=UNEXPECTED; bad=$((bad+1)); } || ok=$((ok+1))
  printf '%-11s %-62s HTTP %-4s readable=%-3s %s\n' "$mark" "$name" "${status:-?}" "$readable" "${acao:+(allow-origin: $acao)}"
}
pre() {  # name, url, method, headers
  check "preflight: $1" yes -X OPTIONS "$2" -H "Access-Control-Request-Method: $3" -H "Access-Control-Request-Headers: $4"
}
json=(-H "Content-Type: application/json")

echo "Origin: $O"
echo "--- OpenRouter"
pre "chat/completions" https://openrouter.ai/api/v1/chat/completions POST "authorization,content-type,x-title"
check "chat/completions, fake key (401)" yes -X POST https://openrouter.ai/api/v1/chat/completions -H "Authorization: Bearer sk-or-fake" "${json[@]}" -d '{"model":"openrouter/free","messages":[{"role":"user","content":"hi"}],"stream":true}'
pre "auth/keys (PKCE code exchange)" https://openrouter.ai/api/v1/auth/keys POST "content-type"
check "auth/keys, fake code (400)" yes -X POST https://openrouter.ai/api/v1/auth/keys "${json[@]}" -d '{"code":"fake","code_verifier":"fake","code_challenge_method":"S256"}'
check "models (public)" yes https://openrouter.ai/api/v1/models
check "credits, fake key" yes https://openrouter.ai/api/v1/credits -H "Authorization: Bearer sk-or-fake"

echo "--- OpenAI"
pre "responses" https://api.openai.com/v1/responses POST "authorization,content-type"
check "models, fake key (401)" yes https://api.openai.com/v1/models -H "Authorization: Bearer sk-fake"
check "responses, no key (401)" yes -X POST https://api.openai.com/v1/responses "${json[@]}" -d '{}'
# Known gap: a wrong key on POST /responses comes back without CORS headers.
check "responses, fake key (401) [known gap]" no -X POST https://api.openai.com/v1/responses -H "Authorization: Bearer sk-fake" "${json[@]}" -d '{"model":"gpt-5","input":"hi","stream":true}'

echo "--- Anthropic (needs anthropic-dangerous-direct-browser-access: true)"
AH=(-H "x-api-key: fake" -H "anthropic-version: 2023-06-01" -H "anthropic-dangerous-direct-browser-access: true")
pre "messages" https://api.anthropic.com/v1/messages POST "x-api-key,anthropic-version,anthropic-dangerous-direct-browser-access,content-type"
check "messages, fake key (401)" yes -X POST https://api.anthropic.com/v1/messages "${AH[@]}" "${json[@]}" -d '{"model":"x","max_tokens":10,"messages":[{"role":"user","content":"hi"}],"stream":true}'
check "messages, fake key, without the header (401)" no -X POST https://api.anthropic.com/v1/messages -H "x-api-key: fake" -H "anthropic-version: 2023-06-01" "${json[@]}" -d '{}'
check "models, fake key (401)" yes https://api.anthropic.com/v1/models "${AH[@]}"

echo "--- Google Gemini (echoes the origin)"
G=https://generativelanguage.googleapis.com/v1beta
pre "streamGenerateContent" "$G/models/gemini-2.5-flash:streamGenerateContent?alt=sse" POST "x-goog-api-key,content-type"
check "streamGenerateContent, fake key (400)" yes -X POST "$G/models/gemini-2.5-flash:streamGenerateContent?alt=sse" -H "x-goog-api-key: fake" "${json[@]}" -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}'
check "models, fake key (400)" yes "$G/models?pageSize=10" -H "x-goog-api-key: fake"

echo "--- Other services the page already calls"
check "en.wikipedia.org API" yes "https://en.wikipedia.org/w/api.php?action=query&format=json&origin=*&meta=siteinfo"
check "commons.wikimedia.org API" yes "https://commons.wikimedia.org/w/api.php?action=query&format=json&origin=*&meta=siteinfo"
check "api.crossref.org" yes "https://api.crossref.org/works?rows=1"

echo "--- $ok as expected, $bad unexpected"
[ "$bad" -eq 0 ]
