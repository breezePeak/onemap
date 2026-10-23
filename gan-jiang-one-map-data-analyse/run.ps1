param([switch]$SkipOcr)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING='utf-8'
$runtime=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$python=Join-Path $runtime 'python/python.exe'
$node=Join-Path $runtime 'node/bin/node.exe'
if (!(Test-Path -LiteralPath $python)) { $python='python' }
if (!(Test-Path -LiteralPath $node)) { $node='node' }
function Run-Python([string]$script) {
    & $python $script
    if ($LASTEXITCODE -ne 0) { throw "Failed: $script" }
}
Run-Python 'scripts/test_match_engine.py'
Run-Python 'scripts/test_review_service.py'
Run-Python 'scripts/test_cross_stage.py'
Run-Python 'scripts/reuse_material_cache.py'
Run-Python 'scripts/inspect_sources.py'
Run-Python 'scripts/extract_materials.py'
Run-Python 'scripts/extract_pdfs.py'
& $node scripts/render_pdfs.mjs
if ($LASTEXITCODE -ne 0) { throw 'PDF rendering failed' }
if (!$SkipOcr) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ocr_images.ps1
    if ($LASTEXITCODE -ne 0) { throw 'OCR failed' }
}
Run-Python 'scripts/vector_outputs.py'
Run-Python 'scripts/spatial_matches.py'
Run-Python 'scripts/extract_fields.py'
if ($SkipOcr) {
    & $python scripts/expand_materials.py --skip-ocr
} else {
    & $python scripts/expand_materials.py
}
if ($LASTEXITCODE -ne 0) { throw 'Cross-stage expansion failed' }
Run-Python 'scripts/link_stages.py'
Run-Python 'scripts/build_patches.py'
Run-Python 'scripts/vector_outputs.py'
Run-Python 'scripts/governance_outputs.py'
& $node scripts/fill_workbook.mjs
if ($LASTEXITCODE -ne 0) { throw 'Workbook export failed' }
Run-Python 'scripts/preserve_original_package.py'
Run-Python 'scripts/verify_result.py'
Run-Python 'scripts/governance_outputs.py'
Write-Host 'Done: outputs/governance'
