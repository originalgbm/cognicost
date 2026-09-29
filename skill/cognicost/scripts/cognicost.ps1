<#
Cognicost (lite): what Claude Code and Cowork usage would cost at Anthropic API list prices.
Windows PowerShell 5.1 or newer, no dependencies. Reads local session logs only; nothing is sent anywhere.
  -Days N     look back N days (default 30, 0 = all time)
  -By         day | project | model | category | session
  -Root PATH  read only this logs folder instead of the defaults
Keep this compatible with Windows PowerShell 5.1: no ternary, ??, &&, or -AsHashtable.
#>
param(
    [int]$Days = 30,
    [ValidateSet('day', 'project', 'model', 'category', 'session')][string]$By = 'day',
    [string]$Root
)
$ErrorActionPreference = 'Stop'
$inv = [Globalization.CultureInfo]::InvariantCulture

# prices.json: model -> USD per million tokens [input, output, cache_read, cache_write_5m, cache_write_1h]
$prices = @{}
$pj = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\prices.json') | ConvertFrom-Json
foreach ($p in $pj.PSObject.Properties) { $prices[$p.Name] = @($p.Value | ForEach-Object { [double]$_ }) }

function Get-Price($model) {
    if ($prices.ContainsKey($model)) { return $prices[$model] }
    $base = $model -replace '-\d{8}$', ''
    if ($prices.ContainsKey($base)) { return $prices[$base] }
    $best = $null
    foreach ($k in $prices.Keys) {  # e.g. claude-sonnet-5-preview -> claude-sonnet-5
        if ($base.StartsWith($k) -and (($null -eq $best) -or ($k.Length -gt $best.Length))) { $best = $k }
    }
    if ($best) { return $prices[$best] }
    return $null
}

# Turn categories: deterministic, first match wins. A turn = one user prompt plus everything the agent did until the next.
$DEBUG = "\b(fix|bug|bugs|error|errors|broke|broken|fail|fails|failed|failing|crash|crashes|exception|traceback|wrong|not working|doesn'?t work|isn'?t working)\b"
$TEST = "\b(pytest|vitest|jest|unittest|mocha|npm (run )?test|yarn test|cargo test|go test|dotnet test)\b|test_\w+\.py"
$GIT = "\b(git|gh) "

function Get-Category($prompt, $tools, $cmds) {
    $edit = $false; $shell = $false; $web = $false; $plan = $false; $read = $false
    foreach ($t in $tools) {
        if ($t -in 'Edit', 'Write', 'NotebookEdit', 'MultiEdit') { $edit = $true }
        elseif ($t -in 'Bash', 'PowerShell') { $shell = $true }
        elseif ($t -in 'WebSearch', 'WebFetch') { $web = $true }
        elseif ($t -in 'EnterPlanMode', 'ExitPlanMode') { $plan = $true }
        if (($t -in 'Read', 'Grep', 'Glob') -or $t.StartsWith('mcp__code-index')) { $read = $true }
    }
    if (($prompt -match $DEBUG) -and ($edit -or $shell)) { return 'Debugging' }
    if ($edit) { return 'Coding' }
    if ($cmds -match $TEST) { return 'Testing' }
    if ($cmds -match $GIT) { return 'Git' }
    if ($plan) { return 'Planning' }
    if ($web) { return 'Research' }
    if ($shell) { return 'Shell' }
    if ($read) { return 'Exploration' }
    if ($tools.Count -gt 0) { return 'Other tools' }
    return 'Chat'
}

function New-Turn($prompt) {
    @{
        Prompt = $prompt
        Tools  = (New-Object 'System.Collections.Generic.HashSet[string]')
        Cmds   = (New-Object System.Text.StringBuilder)
        Keys   = (New-Object 'System.Collections.Generic.List[string]')
    }
}

function Complete-Turn($turn, $cowork) {
    # Cowork's tools (connectors etc.) don't fit the coding categories, so it gets its own label
    if ($cowork) { $cat = 'Cowork' }
    else {
        $p = $turn.Prompt
        if ($p.Length -gt 2000) { $p = $p.Substring(0, 2000) }
        $cat = Get-Category $p $turn.Tools $turn.Cmds.ToString()
    }
    foreach ($k in $turn.Keys) { if ($recs.ContainsKey($k)) { $recs[$k].cat = $cat } }
}

function Get-PromptText($o, $c) {
    # Text of a real user prompt; $null for tool results and injected meta records (those don't start a turn)
    if ($o.isMeta -or $o.isCompactSummary) { return $null }
    if ($c -is [string]) { return $c }
    if ($c -is [System.Array] -and $c.Count -gt 0) {
        foreach ($b in $c) { if ($b.type -eq 'tool_result') { return $null } }
        return (($c | ForEach-Object { [string]$_.text }) -join ' ')
    }
    return $null
}

# Where the logs are: Claude Code, and Cowork sessions from the Claude desktop app
if ($Root) { $roots = @($Root) }
else {
    if ($env:CLAUDE_CONFIG_DIR) { $cfg = $env:CLAUDE_CONFIG_DIR } else { $cfg = Join-Path $env:USERPROFILE '.claude' }
    $roots = @((Join-Path $cfg 'projects'), (Join-Path $env:APPDATA 'Claude\local-agent-mode-sessions'))
}
$roots = @($roots | Where-Object { Test-Path -LiteralPath $_ })
if ($roots.Count -eq 0) { throw 'No Claude Code data found.' }

# One record per API message: Claude Code logs a message several times while streaming and resumed sessions re-log
# old history, so dedupe by message id keeping the highest output count.
$recs = @{}
foreach ($file in (Get-ChildItem -LiteralPath $roots -Recurse -Filter '*.jsonl' -File)) {
    $cowork = $file.FullName -like '*local-agent-mode-sessions*'
    $turn = New-Turn ''
    $fs = New-Object IO.FileStream($file.FullName, 'Open', 'Read', 'ReadWrite')  # the live session may still be open for writing
    $reader = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if (-not $line.Contains('"message"')) { continue }
            try { $o = $line | ConvertFrom-Json } catch { continue }
            $m = $o.message
            if ($null -eq $m -or $m -is [string]) { continue }
            $c = $m.content
            if ($o.type -eq 'user') {
                $text = Get-PromptText $o $c
                if ($null -ne $text) { Complete-Turn $turn $cowork; $turn = New-Turn $text }
                continue
            }
            if ($o.type -ne 'assistant') { continue }
            if ($c -is [System.Array]) {  # streamed lines each hold one block of the same message
                foreach ($b in $c) {
                    if ($b.type -eq 'tool_use') {
                        [void]$turn.Tools.Add([string]$b.name)
                        if (($b.name -eq 'Bash' -or $b.name -eq 'PowerShell') -and $b.input) { [void]$turn.Cmds.Append(' ' + [string]$b.input.command) }
                    }
                }
            }
            $u = $m.usage; $model = [string]$m.model
            if (-not $u -or -not $model -or $model.StartsWith('<')) { continue }  # "<synthetic>" = not a real API call
            if ($m.id) { $key = [string]$m.id } else { $key = [string]$o.uuid }
            [void]$turn.Keys.Add($key)
            $out = [long]$u.output_tokens
            if ($recs.ContainsKey($key) -and $recs[$key].out -ge $out) { continue }
            $cw1 = 0; if ($u.cache_creation) { $cw1 = [long]$u.cache_creation.ephemeral_1h_input_tokens }
            $cwTotal = [long]$u.cache_creation_input_tokens
            try {
                $ts = $o.timestamp   # PowerShell 7 parses this into a DateTime, 5.1 leaves a string
                if ($ts -is [datetime]) { $day = $ts.ToLocalTime().Date }
                else { $day = [DateTimeOffset]::Parse([string]$ts, $inv).LocalDateTime.Date }
            } catch { continue }
            $cwd = [string]$o.cwd
            if (-not $cwd) { $cwd = $file.Directory.Name }
            $cwd = $cwd.Replace('\', '/').TrimEnd('/')
            $proj = ($cwd -split '/')[-1]
            if (-not $proj) { $proj = $cwd }
            if ($cowork) { $proj = 'Cowork' }  # Cowork's cwd is a VM path or blank
            $sid = [string]$o.sessionId; if (-not $sid) { $sid = '?' }
            $recs[$key] = @{
                model = $model; day = $day; out = $out; session = $sid; project = $proj; cat = 'Chat'
                in = [long]$u.input_tokens; cr = [long]$u.cache_read_input_tokens
                cw5 = [math]::Max($cwTotal - $cw1, 0); cw1 = $cw1
            }
        }
    }
    finally { $reader.Dispose(); $fs.Dispose() }
    Complete-Turn $turn $cowork
}

# Filter, group, price
$since = (Get-Date).Date.AddDays(-($Days - 1))
$agg = @{}; $unpriced = @{}
foreach ($r in $recs.Values) {
    if ($Days -gt 0 -and $r.day -lt $since) { continue }
    switch ($By) {
        'day'      { $k = $r.day.ToString('yyyy-MM-dd', $inv) }
        'project'  { $k = $r.project }
        'model'    { $k = $r.model }
        'category' { $k = $r.cat }
        'session'  { $k = $r.session.Substring(0, [math]::Min(8, $r.session.Length)) + ' ' + $r.project }
    }
    $p = Get-Price $r.model
    if ($p) { $c = ($r.in * $p[0] + $r.out * $p[1] + $r.cr * $p[2] + $r.cw5 * $p[3] + $r.cw1 * $p[4]) / 1e6 }
    else { $c = 0; $unpriced[$r.model] = $true }
    if (-not $agg.ContainsKey($k)) { $agg[$k] = New-Object 'double[]' 6 }
    $a = $agg[$k]
    $a[0] += 1; $a[1] += $r.in; $a[2] += $r.out; $a[3] += $r.cr; $a[4] += $r.cw5 + $r.cw1; $a[5] += $c
}
if ($agg.Count -eq 0) { Write-Output 'No usage in that period.'; return }

function Format-Tok([double]$n) {
    if ($n -ge 1e9) { return [string]::Format($inv, '{0:0.0}B', $n / 1e9) }
    if ($n -ge 1e6) { return [string]::Format($inv, '{0:0.0}M', $n / 1e6) }
    if ($n -ge 1e3) { return [string]::Format($inv, '{0:0.0}k', $n / 1e3) }
    return [string]([long]$n)
}
function Format-Row($k, $v) {
    @($k, [string][long]$v[0], (Format-Tok $v[1]), (Format-Tok $v[2]), (Format-Tok $v[3]), (Format-Tok $v[4]),
      ('$' + [string]::Format($inv, '{0:N2}', $v[5])))
}

if ($By -eq 'day') { $sorted = $agg.GetEnumerator() | Sort-Object Name }
else { $sorted = $agg.GetEnumerator() | Sort-Object @{ Expression = { $_.Value[5] }; Descending = $true }, @{ Expression = { $_.Name } } }
$tot = New-Object 'double[]' 6
foreach ($e in $agg.Values) { for ($i = 0; $i -lt 6; $i++) { $tot[$i] += $e[$i] } }

$head = @($By, 'msgs', 'input', 'output', 'cache read', 'cache write', 'cost')
$body = @($sorted | ForEach-Object { , (Format-Row $_.Name $_.Value) })
$total = Format-Row 'TOTAL' $tot
$w = 0..6 | ForEach-Object { $i = $_; (@($head, $total) + $body | ForEach-Object { $_[$i].Length } | Measure-Object -Maximum).Maximum }
function Format-Line($cells) {
    (0..6 | ForEach-Object { if ($_ -eq 0) { $cells[$_].PadRight($w[$_]) } else { $cells[$_].PadLeft($w[$_]) } }) -join '  '
}
$sep = (0..6 | ForEach-Object { '-' * $w[$_] }) -join '  '

if ($Days -gt 0) { $scope = "last $Days days" } else { $scope = 'all time' }
Write-Output "Cognicost - Claude Code and Cowork, $scope. Cost = tokens x API list price; your invoice may differ (seat fees, taxes, negotiated rates)."
Write-Output ''
Write-Output (Format-Line $head)
Write-Output $sep
foreach ($row in $body) { Write-Output (Format-Line $row) }
Write-Output $sep
Write-Output (Format-Line $total)
if ($unpriced.Count -gt 0) {
    Write-Output ''
    Write-Output ("No price for: " + (($unpriced.Keys | Sort-Object) -join ', ') + ' (counted as $0). Prices are bundled in prices.json next to this skill; refresh it from https://github.com/originalgbm/cognicost.')
}
