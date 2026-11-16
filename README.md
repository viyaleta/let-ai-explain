# Let AI Explain

**Type a topic, paste a link, or drop in some text, and get an explanation that actually makes sense.** Then highlight anything you don't get and ask about it, right in the margin.

Let AI Explain runs on your own computer and uses an AI account you already have: your **ChatGPT plan**, an **OpenRouter** account, or your own **API key** from OpenAI, Anthropic or Google.

---

## Run it

### Download

The easiest way: download the app, open it, and you're done. No Python or terminal needed.

| Your computer | Download |
|---|---|
| **Mac** (Apple Silicon or Intel, macOS 11+) | [LetAIExplain-macOS.dmg](https://github.com/viyaleta/let-ai-explain/releases/latest/download/LetAIExplain-macOS.dmg) (or the [.zip](https://github.com/viyaleta/let-ai-explain/releases/latest/download/LetAIExplain-macOS.zip)) |
| **Windows** 10 or 11 (64-bit) | [LetAIExplain-Windows-Setup.exe](https://github.com/viyaleta/let-ai-explain/releases/latest/download/LetAIExplain-Windows-Setup.exe) (or the portable [.zip](https://github.com/viyaleta/let-ai-explain/releases/latest/download/LetAIExplain-Windows.zip)) |

All versions are on the [Releases page](https://github.com/viyaleta/let-ai-explain/releases/latest).

**Mac:** open the .dmg and drag **Let AI Explain** into **Applications**. **Windows:** run the installer (no admin rights needed), or unzip the .zip anywhere and run **Let AI Explain.exe**.

**The first time you open it**, your computer may warn you, because the app isn't signed by Apple or Microsoft yet. This is a one-time step:

- **macOS 14 or earlier:** right-click (or Control-click) **Let AI Explain** in Applications, choose **Open**, then click **Open** again.
- **macOS 15 or later:** open the app once and close the warning. Then go to **System Settings → Privacy & Security**, scroll down, click **Open Anyway** next to Let AI Explain, and confirm.
- **Windows:** if you see "Windows protected your PC", click **More info**, then **Run anyway**.

The app opens in its own window. Sign-in pages and links open in your usual browser; when you finish signing in there, come back to the app window and it updates by itself. To quit, close the window.

> On Windows, the app window uses Microsoft Edge WebView2, which comes with Windows 11 and most Windows 10 PCs. If it's missing, the installer offers to open Microsoft's download page.

### Run from source

Prefer to run the Python script yourself? It works on macOS, Windows and Linux and opens in your browser.

#### What you need

- **Python 3.9 or newer.** Check by running `python3 --version` (on Windows: `py --version`). If you don't have it, install it from [python.org/downloads](https://www.python.org/downloads/).

That's it. There are no packages to install.

#### 1. Get the code

**With Git:**

```bash
git clone https://github.com/viyaleta/let-ai-explain.git
cd let-ai-explain
```

**Without Git:** click the green **Code** button at the top of this page, choose **Download ZIP**, unzip it, and open a terminal in that folder.

#### 2. Start the app

**macOS / Linux:**

```bash
python3 let_ai_explain.py
```

**Windows:**

```bash
py let_ai_explain.py
```

Your browser opens to **http://let-ai-explain.localhost:1455** (it starts at http://127.0.0.1:1455 and switches over if your browser supports `.localhost` names; if not, it stays on 127.0.0.1, which works the same). If nothing opens, copy either address into your browser yourself.

**Install it as an app (optional):** in Chrome or Edge, choose **Install app** in the address bar or the ⋮ menu. It then opens in its own window from your Dock, Start menu or app list, as long as the script is running.

Want it in its own window instead of a browser tab? Install [pywebview](https://pywebview.flowrl.com/) once with `pip install "pywebview>=6.2"`, then run `python3 desktop.py` (Windows: `py desktop.py`).

#### 3. Stop it

Go back to the terminal and press **Ctrl + C**. Your connection is saved, so next time just run step 2 again.

### What you need either way

One of these AI accounts:

- a **ChatGPT Plus or Pro** plan, or
- an **OpenRouter** account (free to create, with some free models), or
- an **API key** from OpenAI, Anthropic (Claude) or Google (Gemini).

### Connect your AI

Pick one:

| Option | Best if… | What it costs |
|---|---|---|
| **Continue with ChatGPT** | You pay for ChatGPT Plus or Pro | Counts toward your ChatGPT plan, nothing extra |
| **Sign in with OpenRouter** | You want Claude, GPT, Gemini and more from one account | Pay-as-you-go credits, with some free models |
| **Use an API key** | You already have an OpenAI, Anthropic or Google API key | Billed by that provider; Gemini has a free tier |

- **ChatGPT:** click **Continue with ChatGPT**, sign in, and approve **ChatGPT plan usage**.
- **OpenRouter:** click **Sign in with OpenRouter**, log in (Google, GitHub or email), and click **Authorize**.
- **API key:** choose your provider, paste your key, and click **Save**. Get one from [OpenAI](https://platform.openai.com/api-keys), [Anthropic](https://console.anthropic.com/settings/keys) or [Google AI Studio](https://aistudio.google.com/apikey).

You only need to do this once; the app remembers you next time. Switch or disconnect anytime from the account menu in the top right, where you can also pick a model.

---

## How to use it

**Ask for an explanation.** The search bar reads like a sentence:

> Let AI explain **[ black holes ]** **[ clearly ▾ ]** **Go**

You can type:
- **A topic:** `Heisenberg uncertainty principle`
- **A link:** a Wikipedia article, an arXiv paper, a blog post, documentation
- **An arXiv ID:** `1706.03762`
- **Pasted text:** a paragraph, an abstract or an article excerpt

Pick how you want it explained:

| Option | Best for |
|---|---|
| **simply** | You're new to the topic. Everyday words, no jargon. |
| **clearly** | You're a curious learner. Builds up gradually, with some math. |
| **expertly** | You work in the field. Precise terms, derivations, edge cases. |

Every explanation starts with *why* the idea matters, builds intuition from one concrete example, and only then gets formal. It ends with a short summary of the core idea.

**Highlight anything you don't understand.** Select a word, sentence or paragraph and a small menu appears:

- **Explain this:** a quick, plain answer in the margin
- **Elaborate:** a deeper explanation inserted right below that paragraph
- **Visualize:** a diagram, chart, map or figure in the margin
- **Ask:** type your own question about the highlighted text

**Ask follow-ups.** Use the box at the bottom of the right-hand pane to ask anything about the explanation. It remembers your earlier questions.

**Go deeper.** At the end of an explanation, click **Go deeper** to continue at a more advanced level, or pick one of the suggested directions.

**Check the sources.** Click **References** next to Go to show citations. References are checked to make sure they exist before they're shown, and they're hidden by default.

**Tidy up.** Hover over any margin note, elaboration or Go deeper section and click **×** to remove it. Changed your mind? Click **Undo**.

**Come back later.** Click **History** to reopen past explanations, notes and all, without using any AI. Click **Let AI explain** to start fresh.

**Save your notes.** Click **Export** to save the explanation as **HTML** or **PDF**, with your margin questions and answers beside the text they're about. For PDF, choose **Save as PDF** in the print dialog.

**Other features:** math is typeset properly, diagrams and charts are drawn for you, and there's a light/dark mode toggle in the top right.

---

## Privacy

- **Everything runs on your computer.** The app is a small local server. Your requests go only to the AI provider you connected and to the websites you ask it to read.
- **Everything is saved in one folder on your computer**, readable only by your user account: `~/.config/let-ai-explain/` on macOS and Linux, `%APPDATA%\let-ai-explain\` on Windows.
- **Your sign-in and API keys** are kept there and sent only to the provider they belong to, never to the browser page or anywhere else.
- **History is saved on this computer** in the same folder (`history/`), shared by the browser and the desktop app. It keeps your 100 most recent explanations and deletes ones older than 30 days. Clear it anytime from the History panel. Your theme and level choices are saved there too (`prefs.json`).
- **The app can't see your chat history** with ChatGPT or any other service.
- **Keep spending in check:**
  - ChatGPT: [ChatGPT settings → Usage](https://chatgpt.com/settings/usage)
  - OpenRouter: set a credit limit on the key at [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys)
  - API keys: set a budget or spending limit in your provider's console

**To disconnect completely:** choose **Disconnect** in the app's account menu. Then revoke access where you signed up (remove *Let AI Explain* under ChatGPT settings → Apps, delete the key on OpenRouter, or revoke your API key), and delete the `let-ai-explain` folder above.

---

## Troubleshooting

**"python3: command not found"**
Python isn't installed, or on Windows it's called `py`. Install it from [python.org](https://www.python.org/downloads/) and check **Add Python to PATH** during setup.

**The page says it can't reach the server**
When running from source, the app has to stay running in the terminal while you use it. Start it again with `python3 let_ai_explain.py`. In the desktop app, quit and reopen it.

**The desktop app's window is blank (Windows)**
Install the [Microsoft Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703), then open the app again.

**ChatGPT keeps asking me to sign in**
Make sure you approved **ChatGPT plan usage** on the sign-in screen; signing in without it isn't enough. Open the account menu and choose **Allow ChatGPT plan usage**.

**"You've reached a usage limit"**
Your ChatGPT plan limit, your OpenRouter credits or free-model allowance, or your API budget is used up for now. Check the usage links under [Privacy](#privacy), or switch to another connection in the account menu.

**"The provider rejected your key"**
The key was mistyped, revoked, or has no credits. Create a new one and paste it again from the account menu.

**OpenRouter free models are slow or busy**
Free models have lower limits and can be overloaded. Try again shortly, pick another free model, or add a few dollars of credits.

**A link couldn't be read**
Some pages need JavaScript to load, block automated readers, or are PDFs. Copy the text and paste it in instead.

**Port 1455 is already in use**
The app automatically picks another free port. Use the address it prints in the terminal.

---

## FAQ

**Does this cost money?**
The app itself is free. AI usage is billed by whichever account you connect: it counts toward your ChatGPT plan, uses your OpenRouter credits (or free models), or is billed to your API key.

**Which option should I choose?**
If you already pay for ChatGPT, use that. If you want to try it for free, sign in with OpenRouter and pick a free model, or use a free Gemini API key. If you prefer Claude, use OpenRouter or an Anthropic key.

**Which model does it use?**
Whichever you pick in the account menu. The list shows the models available to your connection.

**Why does it run locally instead of on a website?**
It keeps your keys and sign-in on your own computer, and it's how OpenAI allows open-source tools to use your ChatGPT plan. A hosted version is planned.

**How do I update it?**
Desktop app: download the latest version from the [Releases page](https://github.com/viyaleta/let-ai-explain/releases/latest) and install it over the old one. From source: run `git pull` in the folder, or download the ZIP again. Your connection is kept either way.

---

## License

The source code is released under the [MIT License](LICENSE). Use it, change it and share it freely.

**The name and logo aren't included.** The MIT License covers the code only, not the name "Let AI Explain" or its logo. If you publish a modified version or host a copy for other people, please give it a different name and logo so users can tell it apart from this project.

Built by [Viyaleta Apgar](https://www.linkedin.com/in/viyaleta/) · [GitHub](https://github.com/viyaleta/let-ai-explain)
