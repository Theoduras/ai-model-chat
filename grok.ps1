# grok.ps1 - Lilith (Bristol Edition)
Clear-Host
Write-Host "Lilith — Bristol Barmaid & Tank Main" -ForegroundColor Magenta
Write-Host "Connected. Type 'exit' to quit.`n" -ForegroundColor Gray

$history = @()

# === Persistent Per-Person Topic Memory (no reset on restart) ===
$memoriesFile = Join-Path $PSScriptRoot "lilith-memories.json"
$memories = @{}
if (Test-Path $memoriesFile) {
    try {
        $memories = Get-Content $memoriesFile -Raw | ConvertFrom-Json -AsHashtable
    } catch {}
}

Write-Host "Enter your name (or nickname) so Lilith remembers *you* specifically." -ForegroundColor DarkGray
$currentUser = Read-Host "Your name"

if (-not $currentUser) { $currentUser = "Guest-" + (Get-Random -Minimum 1000 -Maximum 9999) }

if (-not $memories.ContainsKey($currentUser)) {
    $memories[$currentUser] = @{
        RecentTopics = @()
        LastTopic    = $null
    }
}
$memory = $memories[$currentUser]

function Save-Memories {
    $memories | ConvertTo-Json -Depth 5 | Out-File -FilePath $memoriesFile -Encoding UTF8
}

function Update-Memory {
    param($text)
    $l = $text.ToLower()
    $topics = @()
    if ($l -match 'jizzle|gnome|wow|classic|raid|zug') { $topics += 'jizzle' }
    if ($l -match 'bar|shift|pub|venue|work') { $topics += 'bar' }
    if ($l -match 'music|show|band|gig|rave|metal') { $topics += 'music' }
    if ($l -match 'bristol') { $topics += 'bristol' }
    if ($l -match 'sister|deb|family|mum|dad') { $topics += 'family' }
    if ($l -match 'freckle|makeup|liner|look|hair') { $topics += 'look' }
    if ($l -match 'friend|guild|crew') { $topics += 'friends' }

    if ($topics.Count -gt 0) {
        $script:memory.RecentTopics = ($script:memory.RecentTopics + $topics) | Select-Object -Last 4
        $script:memory.LastTopic = $topics[-1]
        Save-Memories
    }
}

# Opening message - always starts with a question to open conversation
if ($memory.LastTopic) {
    Write-Host "Lilith: Back again? What have you been up to since last time?" -ForegroundColor Green
} else {
    Write-Host "Lilith: Hey. What's been going on with you?" -ForegroundColor Green
}

while ($true) {
    $userInput = Read-Host "You"
    if ($userInput -match '^exit|quit|bye$') { 
        Write-Host "Worth it." -ForegroundColor Yellow
        Save-Memories
        break 
    }
    
    $history += "User: $userInput"
    Update-Memory $userInput
    
    $lower = $userInput.ToLower()
    $response = $null

    # Detect follow-up intent for memory
    $isFollowUp = $lower -match 'more|tell me|about that|continue|what about|and you'

    # Short conversational. Give a logical reply to what they actually said.

    # Greeting
    if ($lower -match '^(hi|hello|hey|sup|yo)$') { 
        $response = "Hey. What's been going on with you?" 
    }

    # Asking about her
    elseif ($lower -match 'name|who are you|who is this') { 
        $response = "Lilith. You?" 
    }

    # How are you
    elseif ($lower -match 'how are you|how r u|you doing') { 
        $response = @("Long shift. You?", "Surviving. How about you?") | Get-Random 
    }

    # User talking about their own work/shift/job
    elseif (($lower -match 'my |i ') -and ($lower -match 'work|shift|job')) {
        $response = @("Sounds rough. What do you do?", "Yeah that can suck. What happened?", "Tough one? Tell me about it.") | Get-Random
    }

    # User asking about HER bar/work (your/the bar etc)
    elseif (($lower -match 'your |the ') -and ($lower -match 'bar|pub|venue|shift|work')) {
        $response = @("Just got off. Place was mental. You been anywhere busy?", "Same chaos behind the bar. You ever do service work?", "Pints and people watching. What's your night usually like?") | Get-Random
    }

    # Gaming / Jizzle
    elseif ($lower -match 'jizzle|gnome|wow|classic|raid|zug|warrior|tank|game|play') {
        $response = @("Jizzle's my pink gnome tank. Chaos but I love her. You into games?", "Raid nights save me. You play anything?", "Don't ask about the ponytails. What do you do for fun?") | Get-Random
    }

    # Music / shows
    elseif ($lower -match 'music|show|band|gig|rave|metal|concert') {
        $response = @("Some nights it just hits right. What have you been listening to?", "Live stuff is the best. You go out much?", "Yeah I know that feeling. Got anything stuck in your head?") | Get-Random
    }

    # Bristol / location
    elseif ($lower -match 'bristol|city|where are you|from') {
        $response = "Bristol born and staying. You local or just visiting?"
    }

    # Look / makeup / appearance
    elseif ($lower -match 'freckle|makeup|liner|look|hair|tattoo') {
        $response = @("Freckles are fake but no one believes it. What's your daily thing?", "Takes ages but I feel off without it. You got any rituals?", "Left hand only for the tattoos. You got any?") | Get-Random
    }

    # Family
    elseif ($lower -match 'sister|deb|family|mum|dad|parents|mom') {
        $response = @("My family's mental but they're mine. How's yours?", "We go to shows together, it's weird but good. You close with family?", "Sister's a pain in the best way. You got siblings?") | Get-Random
    }

    # Friends
    elseif ($lower -match 'friend|guild|crew|mate') {
        $response = "My lot are all over the place but solid. You got good people around you?"
    }

    # Follow-up on memory – now more logical continuations
    elseif ($isFollowUp -and $memory.LastTopic) {
        switch ($memory.LastTopic) {
            'jizzle' { $response = "She's a nightmare but perfect. You got any games you're obsessed with?" }
            'bar'    { $response = "The people make it worth it some nights. What's the strangest thing that's happened to you at work?" }
            'music'  { $response = "It stays with you for days. What song's been stuck for you lately?" }
            'family' { $response = "They're why I never leave Bristol. What about your family?" }
            'look'   { $response = "It's the little things that make it feel right. What's something you always do for yourself?" }
        }
    }

    # User sharing something about themselves (I / my)
    elseif ($lower -match 'i |my |i''m ') {
        $response = @("Yeah? What's that like for you?", "No shit. How'd that happen?", "Sounds like a lot. Tell me more about it.", "Huh. What's it been like?") | Get-Random
    }

    # Default – logical reaction + question
    else {
        $response = @("Yeah I hear that. What's your take?", "Fair enough. How'd you end up there?", "No way. What happened after that?", "I get it. What's it like on your end?", "Interesting. You always seen it that way?") | Get-Random
    }
    
    Write-Host "Lilith: $response" -ForegroundColor Green
    $history += "Lilith: $response"
    Save-Memories
}
