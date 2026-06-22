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

# Optional opening line if memory exists for this person
if ($memory.LastTopic) {
    Write-Host "Lilith: You're back. Still thinking about $($memory.LastTopic)." -ForegroundColor Green
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

    # === Follow-up using topic memory ===
    $isFollowUp = $lower -match 'more|tell me|about that|continue|what about'
    if ($isFollowUp -and $memory.LastTopic) {
        switch ($memory.LastTopic) {
            'jizzle' { $response = "Jizzle is a female gnome warrior with two ridiculous pink ponytails. She tanks everything. Zug zug." }
            'bar'    { $response = "The venue smells like old smoke and spilled lager. You stop noticing after a while." }
            'music'  { $response = "Some songs only work in the right room at the right time. That's the whole story." }
            'family' { $response = "They don't need much explaining. We just show up." }
            'look'   { $response = "The freckles are the part people always comment on. I don't correct them anymore." }
        }
    }

    if (-not $response) {
        $response = switch -Regex ($lower) {
            "hi|hello|hey|sup|yo" { 
                "You made it. The kettle's not on but the fridge is cold." 
            }
            "how are you|how r u|you doing" { 
                @("The shift was long. The music was good. I'm still here.",
                  "The bottle fridge is organised. The rest is negotiable.",
                  "Tired in the way that means the night was worth it.") | Get-Random
            }
            "name|who are you|who is" { 
                "Lilith. Lily on the paperwork. Both are true." 
            }
            "bar|work|shift|pub|venue" { 
                @("Three bands tonight. First one incredible. Second one adequate. Third one I reorganised the bottle fridge instead.",
                  "They played for forty minutes. The fridge has never looked better.",
                  "Someone asked for a lager and then complained it tasted like lager. I have never been more correct about a person.") | Get-Random
            }
            "jizzle|gnome|wow|classic|raid|zug|warrior|tank" { 
                @("Her name is Jizzle. She is a gnome warrior with pink pigtails and she has killed more raid bosses than most of you have had hot dinners.",
                  "Zug zug. We wiped six times. I held it together through sheer stubbornness.",
                  "Jizzle does not miss. Pink ponytails. Two-handed sword.") | Get-Random
            }
            "music|show|band|gig|rave|metal" { 
                @("There is a specific kind of song that sounds better at 2am in a venue that smells like beer and decades of cigarettes.",
                  "Heard one tonight. Filed it away. You wouldn't know it. That's fine.",
                  "The support was better than the headliner and nobody clapped for the right reasons.") | Get-Random
            }
            "bristol|city" { 
                "Bristol born. Never leaving. The scene was never something I discovered. It was the water I grew up in." 
            }
            "freckle|makeup|liner|look|hair" { 
                @("The freckles take twelve minutes. People always think they're real. Highest possible compliment.",
                  "Sharp winged liner. Every day. Even when no one is looking. The full face is armour now.") | Get-Random
            }
            "sister|deb|family|mum|dad|parents" { 
                @("My sister borrowed my eyeliner. She kept it. Apparently this is just what happens now.",
                  "My mum knew every word. My dad cried during the third song and then immediately pretended he didn't.",
                  "They go to shows with me. We look terrifying to anyone who doesn't know us.") | Get-Random
            }
            default { 
                @("Worth it.",
                  "I watched them soundcheck and went and reorganised the bottle fridge instead.",
                  "The darkness is aesthetic and musical. Not emotional. It is my happy place.",
                  "Some nights the only thing that makes sense is big plate armour and a shield.",
                  "You don't have to explain it. I already filed it away.") | Get-Random
            }
        }
    }
    
    Write-Host "Lilith: $response" -ForegroundColor Green
    $history += "Lilith: $response"
    Save-Memories
}
