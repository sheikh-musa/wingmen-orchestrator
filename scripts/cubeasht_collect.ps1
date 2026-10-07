# cubeasht_collect.ps1 — READ-ONLY metrics probe for cubeasht (Musa's Windows desktop).
# Sent over ssh on stdin by scripts/cubeasht_monitor.py:
#   ssh cubeasht 'powershell -NoProfile -ExecutionPolicy Bypass -Command -' < cubeasht_collect.ps1
# Emits ONE JSON object on stdout between the CUBEMON_JSON_BEGIN/END markers.
# Every probe is independent and wrapped in try/catch: a missing source (no GPU,
# no NVMe counters, no LibreHardwareMonitor) becomes an "<name>_error" string,
# never a crash. Installs/changes NOTHING on the host.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$out = [ordered]@{}

try {
  $os = Get-CimInstance Win32_OperatingSystem
  $out.ram_total_kb = [int64]$os.TotalVisibleMemorySize
  $out.ram_free_kb = [int64]$os.FreePhysicalMemory
  $out.last_boot = $os.LastBootUpTime.ToUniversalTime().ToString('o')
} catch { $out.os_error = "$_" }

try {
  $cpu = @(Get-CimInstance Win32_Processor)
  $out.cpu_name = ($cpu | Select-Object -First 1).Name
  $out.cpu_load_pct = [double](($cpu | Measure-Object -Property LoadPercentage -Average).Average)
  $out.cpu_threads = [int](($cpu | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
} catch { $out.cpu_error = "$_" }

try {
  $c = Get-PSDrive -Name C
  $out.disk_c_used_bytes = [int64]$c.Used
  $out.disk_c_free_bytes = [int64]$c.Free
} catch { $out.disk_error = "$_" }

try {
  $nv = & nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>&1
  if ($LASTEXITCODE -ne 0) { throw "nvidia-smi rc=$LASTEXITCODE $nv" }
  $out.nvidia_smi = (@($nv) | ForEach-Object { "$_" }) -join "`n"
} catch { $out.gpu_error = "$_" }

try {
  $disks = @()
  foreach ($pd in @(Get-PhysicalDisk)) {
    $rc = $null
    try { $rc = $pd | Get-StorageReliabilityCounter } catch { }
    $disks += [ordered]@{
      name = "$($pd.FriendlyName)"
      bus = "$($pd.BusType)"
      media = "$($pd.MediaType)"
      temp_c = $(if ($rc) { $rc.Temperature } else { $null })
      temp_max_c = $(if ($rc) { $rc.TemperatureMax } else { $null })
      wear_pct = $(if ($rc) { $rc.Wear } else { $null })
    }
  }
  $out.disks = $disks
} catch { $out.disks_error = "$_" }

# CPU package temperature: only via LibreHardwareMonitor's WMI provider (not
# installed yet — awaiting Musa's OK). Absent namespace => cpu_temp_error, no crash.
try {
  $s = @(Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop |
    Where-Object { $_.SensorType -eq 'Temperature' -and ($_.Name -like 'CPU Package*' -or $_.Name -like 'Core (Tctl/Tdie)*') })
  $out.lhm_cpu_temps = @($s | ForEach-Object { [ordered]@{ name = "$($_.Name)"; value = [double]$_.Value } })
} catch { $out.lhm_error = "$_" }

try {
  $vm = @(Get-Process -Name 'vmmem*' -ErrorAction Stop)
  $out.vmmem_ws_bytes = [int64](($vm | Measure-Object -Property WorkingSet64 -Sum).Sum)
} catch { $out.vmmem_error = "$_" }

Write-Output 'CUBEMON_JSON_BEGIN'
Write-Output ($out | ConvertTo-Json -Depth 5 -Compress)
Write-Output 'CUBEMON_JSON_END'
