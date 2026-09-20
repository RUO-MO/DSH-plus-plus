# WMI 事件驱动的进程启动追踪：能抓到一闪即没的进程
$log = 'd:\DSH\DSH++\tools\spawn_probe.log'
$stop = 'd:\DSH\DSH++\tools\spawn_probe.stop'
Remove-Item $log,$stop -ErrorAction SilentlyContinue
New-Item -ItemType File -Path $log -Force | Out-Null

Register-CimIndicationEvent -Query "SELECT * FROM Win32_ProcessStartTrace" -SourceIdentifier psi -ErrorAction SilentlyContinue

while (-not (Test-Path $stop)) {
    $e = Wait-Event -SourceIdentifier psi -Timeout 1 -ErrorAction SilentlyContinue
    if ($e) {
        $d = $e.SourceEventArgs.NewEvent
        $name = $d.ProcessName
        $pid_ = $d.ProcessID
        $ppid = $d.ParentProcessID
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
        Add-Content $log ("[{0}] START {1} PID={2} PPID={3}" -f $ts, $name, $pid_, $ppid)
        Remove-Event -EventIdentifier $e.EventIdentifier -ErrorAction SilentlyContinue
    }
}