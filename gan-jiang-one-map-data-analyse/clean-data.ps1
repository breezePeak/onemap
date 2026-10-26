param([switch]$Preview, [int]$Port=8766)
$ErrorActionPreference='Stop'

# Use this project only; never traverse directory links.
$projectRoot=[IO.Path]::GetFullPath($PSScriptRoot)
function Assert-LocalPath([string]$Path) {
    if ($Path.StartsWith('\\?\UNC\')) { $Path='\\'+$Path.Substring(8) }
    elseif ($Path.StartsWith('\\?\')) { $Path=$Path.Substring(4) }
    $full=[IO.Path]::GetFullPath($Path)
    if (!$full.StartsWith($projectRoot+[IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Outside project: $full"
    }
    return $full
}
function Remove-GeneratedItem([string]$Path) {
    $full=Assert-LocalPath $Path
    # Windows PowerShell 5.1 needs the extended path form for deep Office profiles.
    $diskPath=if ($full.StartsWith('\\')) { '\\?\UNC\'+$full.Substring(2) } else { '\\?\'+$full }
    $item=Get-Item -LiteralPath $diskPath -Force
    if ($item.PSIsContainer) {
        if (!($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            foreach ($child in @(Get-ChildItem -LiteralPath $diskPath -Force)) {
                Remove-GeneratedItem $child.FullName
            }
        }
        # Delete the directory link itself without recursing into its target.
        [IO.Directory]::Delete($diskPath)
    } else {
        Remove-Item -LiteralPath $diskPath -Force
    }
}

try {
    if (!(Test-Path -LiteralPath (Join-Path $projectRoot 'app/server.py') -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $projectRoot 'scripts/source_config.py') -PathType Leaf)) {
        throw 'Project markers are missing. Keep this script in the project directory.'
    }
    $ancestor=Get-Item -LiteralPath $projectRoot -Force
    while ($null -ne $ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked project directory: $($ancestor.FullName)" }
        $ancestor=$ancestor.Parent
    }
    $targets=@()
    foreach ($name in @('work','outputs','解压材料')) {
        $container=Assert-LocalPath (Join-Path $projectRoot $name)
        if (!(Test-Path -LiteralPath $container)) { continue }
        $item=Get-Item -LiteralPath $container -Force
        if (!$item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Expected a local data directory: $container"
        }
        $targets+=@(Get-ChildItem -LiteralPath $container -Force | Where-Object {
            $name -ne 'work' -or $_.Name -notin @('python_packages','cad_runtime')
        } | ForEach-Object { Assert-LocalPath $_.FullName })
    }
    $cache=Assert-LocalPath (Join-Path $projectRoot 'cache_reuse.json')
    if (Test-Path -LiteralPath $cache) { $targets+=$cache }
    $reviews=Assert-LocalPath (Join-Path $projectRoot 'reviewed_fields.json')
    if ((Test-Path -LiteralPath $reviews) -and ((Get-Item -LiteralPath $reviews -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Linked review file: $reviews"
    }
    Write-Host 'Deletes collected data, task uploads, review decisions, caches and exports.'
    Write-Host 'Preserves source materials, test_result.xlsx, code and Python/CAD dependencies.'
    foreach ($target in $targets) { Write-Host "DELETE $target" }
    Write-Host "RESET  $reviews -> []"
    if ($Preview) { Write-Host 'Preview only. No files or services changed.'; exit 0 }

    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'stop-tool.ps1') -Port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Workbench could not be stopped; no data was deleted.' }
    foreach ($target in $targets) { Remove-GeneratedItem $target }
    [IO.File]::WriteAllText($reviews, "[]`r`n", (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Cleanup complete. Restart the workbench to run a fresh test.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "Cleanup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    Write-Host $_.InvocationInfo.PositionMessage
    Write-Host 'If deletion already started, some data may have been removed. Fix the error and run again.'
    exit 1
}
