$ErrorActionPreference = 'Stop'
$installRoot = $PSScriptRoot
Set-Location -LiteralPath $installRoot
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Windows x64 is required.' }
    $offlineRoot = Join-Path $installRoot 'offline'
    $manifestPath = Join-Path $offlineRoot 'manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host 'Verifying all bundled prerequisites (no network requests)...'
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $target = [IO.Path]::GetFullPath((Join-Path $offlineRoot $entry.Name))
        if (-not $target.StartsWith($offlineRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid package path.' }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Missing bundled file: $($entry.Name)" }
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value.sha256) { throw "Checksum mismatch: $($entry.Name)" }
    }
    $runtimeRoot = Join-Path $installRoot 'runtime'
    $pythonRoot = Join-Path $runtimeRoot 'python'
    $fingerprint = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
    $readyFile = Join-Path $runtimeRoot 'installed.txt'
    if ((Test-Path -LiteralPath (Join-Path $pythonRoot 'python.exe')) -and (Test-Path -LiteralPath $readyFile) -and ((Get-Content -LiteralPath $readyFile -Raw).Trim() -eq $fingerprint)) {
        Write-Host 'Runtime already installed and matches this package.'
        exit 0
    }
    if (Test-Path -LiteralPath (Join-Path $installRoot 'data\server.lock')) {
        $lockStream = $null
        try { $lockStream = [IO.File]::Open((Join-Path $installRoot 'data\server.lock'),'Open','ReadWrite','None') }
        catch { throw 'Please close the application before installing/updating runtime.' }
        finally { if ($lockStream) { $lockStream.Dispose() } }
    }
    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    $staging = Join-Path $runtimeRoot ('install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    Expand-Archive -LiteralPath (Join-Path $offlineRoot 'python-3.13.15-embed-amd64.zip') -DestinationPath $staging
    @('python313.zip','.','Lib\site-packages','import site') | Set-Content -LiteralPath (Join-Path $staging 'python313._pth') -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $staging 'Lib\site-packages') -Force | Out-Null
    & (Join-Path $staging 'python.exe') (Join-Path $installRoot 'bootstrap_offline.py')
    if ($LASTEXITCODE -ne 0) { throw 'Bundled Python dependency installation failed. The previous runtime was not changed.' }
    if (Test-Path -LiteralPath $pythonRoot) {
        Move-Item -LiteralPath $pythonRoot -Destination (Join-Path $runtimeRoot ('python-backup-' + [guid]::NewGuid().ToString('N')))
    }
    Move-Item -LiteralPath $staging -Destination $pythonRoot
    $fingerprint | Set-Content -LiteralPath $readyFile -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'data') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'models') -Force | Out-Null
    Write-Host 'Ready. Application, runtime, data and models are separate.'
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'No model or existing task data was deleted.'
    exit 1
}
