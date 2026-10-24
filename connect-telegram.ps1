param([switch]$RegisterModerator)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
. (Join-Path $PSScriptRoot 'tools\write-settings-atomic.ps1')
$envPath = Join-Path $PSScriptRoot '.env'
function Call-Telegram($method, $body = @{}) {
    try {
        $reply = Invoke-RestMethod -Uri ("https://api.telegram.org/bot{0}/{1}" -f $script:botToken, $method) -Method Post -Body $body -TimeoutSec 30
        if (-not $reply.ok) { throw 'API failure' }
        return $reply.result
    } catch {
        # Never print exception objects: their URLs can contain the bot token.
        throw 'Telegram request failed. Check your connection and bot token. Close any other bot process before retrying.'
    }
}
try {
    if (-not (Test-Path -LiteralPath $envPath)) { throw 'Run setup-secrets.ps1 first.' }
    $settings = [IO.File]::ReadAllLines($envPath)
    $connectedGroup = @($settings | Where-Object { $_ -match '^TELEGRAM_ADMIN_CHAT_ID=-?\d+$' })
    if ($connectedGroup.Count -and -not $RegisterModerator) {
        throw 'Group is already connected. Setup polling is disabled after connection to protect pending bot events.'
    }
    if ($RegisterModerator -and $connectedGroup.Count -ne 1) { throw 'Connect one group first.' }
    $expectedGroup = if ($RegisterModerator) { $connectedGroup[0].Split('=')[1] } else { $null }
    $tokenLine = @($settings | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' })
    if ($tokenLine.Count -ne 1) { throw 'Bot token setting is missing or duplicated.' }
    $script:botToken = $tokenLine[0].Substring('TELEGRAM_BOT_TOKEN='.Length)
    $me = Call-Telegram 'getMe'
    $webhook = Call-Telegram 'getWebhookInfo'
    if ($webhook.url) { throw 'This bot has a webhook configured. No settings were changed.' }
    foreach ($channel in @('@autostock_demo_test', '@autostock_demo_main')) {
        $chat = Call-Telegram 'getChat' @{chat_id=$channel}
        if ($chat.type -ne 'channel') { throw 'Expected a Telegram channel.' }
        $member = Call-Telegram 'getChatMember' @{chat_id=$channel; user_id=$me.id}
        if ($member.status -ne 'administrator' -or -not $member.can_post_messages) {
            throw "Give the bot administrator and posting rights in $channel, then retry."
        }
        Write-Host "Channel access OK: $channel"
    }
    $nonce = [guid]::NewGuid().ToString('N').Substring(0,8)
    $command = '/connect_' + $nonce + '@' + $me.username
    Write-Host ''
    Write-Host 'Send this exact command in your MODERATION GROUP (not in a channel or private chat):'
    Write-Host $command
    Write-Host 'Waiting up to 3 minutes...'
    $deadline = (Get-Date).AddMinutes(3)
    $offset = 0
    while ((Get-Date) -lt $deadline) {
        $updates = @(Call-Telegram 'getUpdates' @{timeout=15; offset=$offset; allowed_updates='["message","callback_query"]'})
        foreach ($update in $updates) {
            $offset = [Math]::Max($offset, ([long]$update.update_id + 1))
            $message = $update.message
            if ($message.text -ne $command -or $message.chat.type -notin @('group','supergroup')) { continue }
            if ($expectedGroup -and [string]$message.chat.id -ne $expectedGroup) { continue }
            if (-not $message.from.id -or $message.from.is_bot) { continue }
            $operator = Call-Telegram 'getChatMember' @{chat_id=$message.chat.id; user_id=$message.from.id}
            if ($operator.status -notin @('creator','administrator')) {
                throw 'The connection command must be sent by the group owner or an administrator.'
            }
            Write-Host ('Group found: ' + $message.chat.title + ' (ID ' + $message.chat.id + ')')
            Write-Host ('Moderator ID: ' + $message.from.id)
            $answer = Read-Host 'Connect this group? Type YES to confirm'
            if ($answer -cne 'YES') { throw 'Cancelled. Settings were not changed.' }
            $newSettings = @($settings | Where-Object { $_ -notmatch '^TELEGRAM_(ADMIN_CHAT_ID|MODERATOR_IDS)=' })
            $newSettings += 'TELEGRAM_ADMIN_CHAT_ID=' + $message.chat.id
            $newSettings += 'TELEGRAM_MODERATOR_IDS=' + $message.from.id
            # Refuse to overwrite concurrent edits.
            if ([IO.File]::ReadAllText($envPath) -ne ($settings -join [Environment]::NewLine) + [Environment]::NewLine) {
                throw 'Settings changed while connecting. Run this script again.'
            }
            Write-SettingsAtomic -Path $envPath -Lines $newSettings
            Write-Host 'CONNECTED. Group saved locally. No messages were published.'
            exit 0
        }
    }
    throw 'No matching command received. Run again and send the new command to the group.'
} catch {
    Write-Host $_.Exception.Message
    exit 1
} finally {
    $script:botToken = $null
    $tokenLine = $null
    $settings = $null
    $newSettings = $null
}
