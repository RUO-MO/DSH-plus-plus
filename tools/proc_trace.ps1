$log = 'd:\DSH\DSH++\tools\proc_trace.log'
Remove-Item $log -ErrorAction SilentlyContinue
$seen = New-Object 'System.Collections.Generic.HashSet[int]'
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    $procs = @(Get-Process -ErrorAction SilentlyContinue)
    foreach ($p in $procs) {
        $id = $p.Id
        if ($seen.Contains($id)) { continue }
        [void]$seen.Add($id)
        $ts = Get-Date -Format 'HH:mm:ss.fff'
        $cmd = ''; $ppid = '?'
        try {
            $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
            if ($pp) { $ppid = $pp.ParentProcessId; $cmd = $pp.CommandLine }
        } catch { }
        if ($cmd -is [string] -and $cmd.Length -gt 90) { $cmd = $cmd.Substring(0,90) }
        Add-Content $log ("[{0}] NEW {1} PID={2} PPID={3} | {4}" -f $ts, $p.ProcessName, $id, $ppid, $cmd)
    }
    Start-Sleep -Milliseconds 12
}
Add-Content $log "===== sample end ====="