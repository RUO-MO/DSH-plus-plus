$log = 'd:\DSH\DSH++\tools\spawn_fast.log'
Remove-Item $log -ErrorAction SilentlyContinue
$seen = New-Object 'System.Collections.Generic.HashSet[int]'
$names = 'cmd','conhost','node','npm','pnpm','python','py','powershell','pwsh'
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    $procs = @(Get-Process -Name $names -ErrorAction SilentlyContinue)
    foreach ($p in $procs) {
        $id = $p.Id
        if ($seen.Contains($id)) { continue }
        [void]$seen.Add($id)
        $ts = Get-Date -Format 'HH:mm:ss.fff'
        $cmd = ''
        $ppid = '?'
        try {
            $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
            if ($pp) { $ppid = $pp.ParentProcessId; $cmd = $pp.CommandLine }
        } catch { }
        if ($cmd -is [string] -and $cmd.Length -gt 200) { $cmd = $cmd.Substring(0,200) }
        Add-Content $log ("[{0}] NEW {1} PID={2} PPID={3} | {4}" -f $ts, $p.ProcessName, $id, $ppid, $cmd)
    }
    Start-Sleep -Milliseconds 15
}
Add-Content $log "===== sample end ====="