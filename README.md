# Let AI Explain

A local explainer that runs on your ChatGPT plan. Type a topic, paste a link (Wikipedia, arXiv or any web page) or a passage of text, and get a layered, intuition-first explanation with math, diagrams, charts and images. Select any word or passage to have it explained, ask about it, or expand it in place, and ask follow-up questions in the margin.

## Requirements

- Python 3.9 or newer (standard library only)
- A ChatGPT account with plan usage enabled for this app
- Optional: `pip install "pyjwt[crypto]"` to also verify the ID token signature at sign-in

## Run

```sh
git clone https://github.com/viyaleta/let-ai-explain.git
cd let-ai-explain
python3 let_ai_explain.py
```

The server starts at http://127.0.0.1:1455/ (or another free port if that one is taken) and opens it in your browser. Choose **Continue with ChatGPT** to sign in. Press Ctrl+C to stop.

Options:

- `--no-browser`: don't open the browser automatically.
- `LET_AI_EXPLAIN_HOME=/some/folder`: store settings and credentials somewhere other than the default.

## Where your data lives

Sign-in credentials are stored with owner-only permissions in `~/.config/let-ai-explain`. If you used the app under its earlier name, the old settings folder is moved there automatically the first time you start it, so you stay signed in.

## License

Released under the MIT License. See [LICENSE](LICENSE).
