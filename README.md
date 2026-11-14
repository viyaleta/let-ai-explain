# Let AI Explain

**Type a topic, paste a link, or drop in some text, and get an explanation that actually makes sense.** Then highlight anything you don't get and ask about it, right in the margin.

Let AI Explain runs on your own computer and uses your existing **ChatGPT plan** through Sign in with ChatGPT. There's no API key to set up and no extra bill.

---

## Run it

### What you need

- **Python 3.9 or newer.** Check by running `python3 --version` (on Windows: `py --version`). If you don't have it, install it from [python.org/downloads](https://www.python.org/downloads/).
- **A ChatGPT Plus or Pro account** that's eligible to use its plan in other apps.

That's it. There are no packages to install.

### 1. Get the code

**With Git:**

```bash
git clone https://github.com/viyaleta/let-ai-explain.git
cd let-ai-explain
```

**Without Git:** click the green **Code** button at the top of this page, choose **Download ZIP**, unzip it, and open a terminal in that folder.

### 2. Start the app

**macOS / Linux:**

```bash
python3 let_ai_explain.py
```

**Windows:**

```bash
py let_ai_explain.py
```

Your browser opens to **http://127.0.0.1:1455**. If it doesn't, copy that address into your browser yourself.

### 3. Sign in

Click **Continue with ChatGPT**, sign in, and approve **ChatGPT plan usage**. You'll come back to the app, ready to go. You only need to do this once; the app remembers you next time.

### 4. Stop it

Go back to the terminal and press **Ctrl + C**. Your sign-in is saved, so next time just run step 2 again.

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

Every explanation starts with *why* the idea matters, builds intuition from one concrete example, and only then gets formal. It ends with a short recap of the core idea.

**Highlight anything you don't understand.** Select a word, sentence or paragraph and a small menu appears:

- **Explain this:** a quick, plain answer in the margin
- **Elaborate:** a deeper explanation inserted right below that paragraph
- **Visualize:** a diagram, chart or figure in the margin
- **Ask:** type your own question about the highlighted text

**Ask follow-ups.** Use the box at the bottom of the right-hand pane to ask anything about the explanation. It remembers your earlier questions.

**Go deeper.** At the end of an explanation, click **Go deeper** to continue at a more advanced level, or pick one of the suggested directions.

**Check the sources.** Click **References** next to Go to show citations. Every reference is checked to make sure it exists before it's shown, and references are hidden by default.

**Save your notes.** Click **Export** to save the explanation as **HTML** or **PDF**, with your margin questions and answers placed beside the text they're about. For PDF, choose **Save as PDF** in the print dialog.

**Other features:** math is typeset properly, diagrams and charts are drawn for you, and there's a light/dark mode toggle in the top right.

---

## Privacy

- **Everything runs on your computer.** The app is a small local server; nothing goes through anyone else's servers except OpenAI's (for the AI) and the websites you ask it to read.
- **Your ChatGPT sign-in stays on your computer**, in `~/.config/let-ai-explain/`, readable only by your user account. It's never sent to the browser page or anywhere else.
- **The app can't see your ChatGPT chat history.** It only gets permission to send requests that count toward your plan.
- **The app asks OpenAI not to store its requests** (`store: false`), so they don't show up as saved conversations. OpenAI's usual data policies still apply.
- **To check or limit usage**, go to [ChatGPT settings → Usage](https://chatgpt.com/settings/usage).

**To disconnect completely:** click your account in the app and choose **Sign out**. Then remove *Let AI Explain* under ChatGPT settings → Apps, and delete the `~/.config/let-ai-explain/` folder.

---

## Troubleshooting

**"python3: command not found"**
Python isn't installed, or on Windows it's called `py`. Install it from [python.org](https://www.python.org/downloads/) and check **Add Python to PATH** during setup.

**The page says it can't reach the server**
The app has to stay running in the terminal while you use it. Start it again with `python3 let_ai_explain.py`.

**It keeps asking me to sign in**
Make sure you approved **ChatGPT plan usage** on the sign-in screen; signing in without it isn't enough. Open the account menu in the app and choose **Allow ChatGPT plan usage**.

**"You've reached the ChatGPT plan usage limit"**
Your plan, or the limit for this app, is used up for now. Check [ChatGPT settings → Usage](https://chatgpt.com/settings/usage), or try again later.

**"ChatGPT plan usage isn't available for this account"**
Some plans and workspaces aren't eligible yet. A personal Plus or Pro account is the most reliable.

**A link couldn't be read**
Some pages need JavaScript to load, block automated readers, or are PDFs. Copy the text and paste it in instead.

**Port 1455 is already in use**
The app automatically picks another free port. Use the address it prints in the terminal.

---

## FAQ

**Does this cost money?**
The app is free. AI requests count toward your existing ChatGPT plan, the same way using ChatGPT does.

**Why does it run locally instead of on a website?**
OpenAI currently lets open-source tools use your ChatGPT plan when they run on your own computer. A hosted version needs OpenAI's approval, which has been requested.

**Can I use a Claude, Gemini or OpenAI API key instead?**
Not in this version, which is built around Sign in with ChatGPT.

**Which model does it use?**
Whichever you pick from the account menu. The list shows the models available on your ChatGPT plan.

**How do I update it?**
If you used Git, run `git pull` in the folder. Otherwise, download the ZIP again. Your sign-in is kept either way.

---

## License

[MIT](LICENSE). Free to use, change and share.

Built by [Viyaleta Apgar](https://www.linkedin.com/in/viyaleta/) · [GitHub](https://github.com/viyaleta/let-ai-explain)
