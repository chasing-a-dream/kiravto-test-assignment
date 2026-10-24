function Write-SettingsAtomic {
    param([string]$Path, [string[]]$Lines)
    $targetPath = [IO.Path]::GetFullPath($Path)
    $tempPath = $targetPath + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    $backupPath = $tempPath + '.backup'
    try {
        [IO.File]::WriteAllLines($tempPath, $Lines, (New-Object Text.UTF8Encoding($false)))
        # Windows PowerShell 5.1 converts $null to an empty string here.
        # An explicit backup path works on both .NET Framework and modern .NET.
        [IO.File]::Replace($tempPath, $targetPath, $backupPath)
    } finally {
        foreach ($artifactPath in @($tempPath, $backupPath)) {
            if (Test-Path -LiteralPath $artifactPath) {
                Remove-Item -LiteralPath $artifactPath -Force
            }
        }
    }
}
