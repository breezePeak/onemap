param([string]$Manifest = 'work/ocr_manifest.json', [switch]$Describe)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] > $null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] > $null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime] > $null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] > $null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($operation, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'Windows Chinese OCR unavailable' }
if ($Describe) {
    @{engine='Windows.Media.Ocr';version=[Diagnostics.FileVersionInfo]::GetVersionInfo((Join-Path $env:WINDIR 'System32/Windows.Media.Ocr.dll')).FileVersion;os_version=[Environment]::OSVersion.Version.ToString();language=$engine.RecognizerLanguage.LanguageTag} | ConvertTo-Json -Compress
    exit
}
$items = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$done = 0
$failed = 0
$pageFailures = @()
foreach ($item in $items) {
    if ((Test-Path -LiteralPath $item.text) -and (Test-Path -LiteralPath ($item.text + '.layout.json'))) { continue }
    $bitmap = $null
    $stream = $null
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($item.path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        if ($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'Image exceeds OCR dimension limit' }
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
        if (-not (Test-Path -LiteralPath $item.text)) { [IO.File]::WriteAllText($item.text, $lines, [Text.UTF8Encoding]::new($false)) }
        $words = @($result.Lines | ForEach-Object { $_.Words | ForEach-Object { @{ text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y; width=$_.BoundingRect.Width; height=$_.BoundingRect.Height } } })
        [IO.File]::WriteAllText(($item.text + '.layout.json'), (@{width=$bitmap.PixelWidth;height=$bitmap.PixelHeight;words=$words} | ConvertTo-Json -Depth 5 -Compress), [Text.UTF8Encoding]::new($false))
        Remove-Item -LiteralPath ($item.text + '.error') -ErrorAction SilentlyContinue
        $done++
        if ($done % 25 -eq 0) { Write-Output "OCR $done pages" }
    } catch {
        $failed++
        [IO.File]::WriteAllText(($item.text + '.error'), $_.Exception.Message)
        $pageFailures += @{ source=$item.source; path=$item.path; error=$_.Exception.Message }
        Write-Output "OCR error: $($item.path): $($_.Exception.Message)"
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
$errorListPath = Join-Path (Split-Path -Parent ([IO.Path]::GetFullPath($Manifest))) 'ocr_errors.json'
[IO.File]::WriteAllText($errorListPath, (ConvertTo-Json -InputObject @($pageFailures) -Depth 5), [Text.UTF8Encoding]::new($false))
Write-Output "OCR completed: $done new pages"
if ($failed -gt 0) { Write-Output "OCR incomplete: $failed pages recorded in $errorListPath; other pages continue" }
