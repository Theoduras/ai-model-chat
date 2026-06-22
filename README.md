# ai-model-chat

Web + terminal chatbot that feels like a real person — Fanvue funnel ready.

## What's included

- `grok.ps1` — Interactive PowerShell terminal chatbot ("Grok Build 0.20")
- `index.html` — Standalone web chat UI (Theodora persona)
- Simple rule-based responses + easy link promotion

## Run the terminal chatbot

```powershell
cd "F:\OneDrive\OnlyFans Accounts\Chatbot AI\ai-model-chat"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\grok.ps1
```

Type `exit` to quit.

## Web version

Open `index.html` directly in any browser.

## Customization

- Edit `$fanvueLink` in `grok.ps1`
- Update responses in the script or `getBotReply()` in the HTML
- Change model name / avatar in index.html

Built for quick model chat funnels.
