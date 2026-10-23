param([int]$Port=8766)
$ErrorActionPreference='Stop'
try {
    $listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $Port)
    if (!$listeners.Count) {
        Write-Host "Workbench is already stopped (port $Port)."
        exit 0
    }
    foreach ($serviceProcessId in ($listeners.OwningProcess | Select-Object -Unique)) {
        $serviceProcess=Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId"
        $expectedPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if (!(Test-Path -LiteralPath $expectedPython)) { $expectedPython=(Get-Command python -ErrorAction Stop).Source }
        $expectedScript=Join-Path $PSScriptRoot 'app/server.py'
        $command=$serviceProcess.CommandLine -replace '\\','/'
        $isScript=$command -match ('(?i)(?:^|\s)"?(?:'+[regex]::Escape(($expectedScript -replace '\\','/'))+'|app/server\.py)"?(?=\s|$)')
        if ($serviceProcess.Name -ne 'python.exe' -or !$isScript -or
            [IO.Path]::GetFullPath($serviceProcess.ExecutablePath) -ne [IO.Path]::GetFullPath($expectedPython)) {
            throw "Port $Port belongs to an unrecognized process ($serviceProcessId); it was not stopped."
        }
        Stop-Process -Id $serviceProcessId -ErrorAction Stop
        Write-Host "Workbench stopped (PID $serviceProcessId, port $Port)."
    }
} catch {
    Write-Host "Failed to stop workbench: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
