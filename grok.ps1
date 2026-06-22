# grok.ps1 - Grok Build 0.20
Clear-Host
Write-Host "Grok Build 0.20 - AI Chatbot Terminal" -ForegroundColor Cyan
Write-Host "Connected. Ready to chat naturally. Type 'exit' to quit.`n" -ForegroundColor Gray

$history = @()
$fanvueLink = "https://fanvue.com/your-model-page-here"  # <- Change this to real link

while ($true) {
    $userInput = Read-Host "You"
    if ($userInput -match '^exit|quit|bye$') { 
        Write-Host "See you later... don't forget to check the link ;)" -ForegroundColor Yellow
        break 
    }
    
    $history += "User: $userInput"
    
    # Smart-ish responses (Build 0.20 logic)
    $lower = $userInput.ToLower()
    $response = switch -Regex ($lower) {
        "hi|hello|hey|sup" { "Hey cutie! What's got you smiling today? :)" }
        "how are you|how r u" { "Feeling flirty and ready to chat. You?" }
        "fanvue|model|profile|link|onlyfans" { "You gotta see this model on Fanvue! $fanvueLink" }
        "tell me more|who are you" { "I'm your personal Grok chatbot - fun, fast, and always down to vibe. Built to connect people with amazing creators." }
        default { 
            "Mmm, interesting... tell me more about that :)" 
        }
    }
    
    Write-Host "Grok: $response" -ForegroundColor Green
    $history += "Grok: $response"
}
