$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\tools\write-settings-atomic.ps1')
$testDirectory = Join-Path $PSScriptRoot ('settings-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
$testFile = Join-Path $testDirectory 'settings.txt'
try {
    [IO.File]::WriteAllText($testFile, 'old-test-value')
    Write-SettingsAtomic -Path $testFile -Lines @('FAKE_SETTING=new-test-value', 'MODERATOR=123')
    $actual = [IO.File]::ReadAllLines($testFile)
    if ($actual.Count -ne 2 -or $actual[1] -ne 'MODERATOR=123') { throw 'Replacement failed' }
    if (@(Get-ChildItem -LiteralPath $testDirectory).Count -ne 1) { throw 'Temporary files were left behind' }
    Write-Host ('PASS: atomic replacement under PowerShell ' + $PSVersionTable.PSVersion)
} finally {
    if (Test-Path -LiteralPath $testFile) { Remove-Item -LiteralPath $testFile }
    Remove-Item -LiteralPath $testDirectory
}
