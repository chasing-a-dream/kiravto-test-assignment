$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    Write-Host 'Settings already exist. Nothing was overwritten.'
    exit 1
}
$secret = Read-Host 'Paste Telegram bot token (hidden input)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ($token -notmatch '^\d+:[A-Za-z0-9_-]+$') {
        Write-Host 'Invalid token format. Run this script again.'
        exit 1
    }
    $apiToken = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $settings = @(
        "TELEGRAM_BOT_TOKEN=$token",
        'TELEGRAM_TEST_CHANNEL=@autostock_demo_test',
        'TELEGRAM_PRODUCTION_CHANNEL=@autostock_demo_main',
        'TELEGRAM_ADMIN_CHAT_ID=',
        'TELEGRAM_MODE=test',
        'PRODUCTION_ENABLED=false',
        "API_TOKEN=$apiToken",
        'LLM_ENABLED=false',
        'LLM_API_KEY='
    )
    [IO.File]::WriteAllLines($envPath, $settings, (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Saved locally. Token was not displayed. Admin group connection is still pending.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $token = $null
    $settings = $null
    $apiToken = $null
}
