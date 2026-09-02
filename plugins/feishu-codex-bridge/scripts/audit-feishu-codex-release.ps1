[CmdletBinding()]
param(
    [string]$DesktopRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$HarnessRoot,
    [switch]$DesktopOnly,
    [ValidateSet('canonical-development', 'installed-snapshot')]
    [string]$SourceRole = 'canonical-development',
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$script:ReleaseAuditFindings = New-Object System.Collections.Generic.List[object]
$script:StrictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:ReleaseAuditMaxBytes = 16777216L
$script:ReleaseAuditMaxFilesPerComponent = 256
$script:ReleaseAuditMaxSnapshotBytesPerComponent = 67108864L

if (@('canonical-development', 'installed-snapshot') -cnotcontains $SourceRole) {
    throw 'Release audit source role must use the exact supported casing.'
}

function Get-PropertyArray {
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return @() }
    return @($property.Value)
}

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function Get-StringSha256 {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    return Get-BytesSha256 -Bytes ([System.Text.Encoding]::UTF8.GetBytes($Text))
}

function Assert-JsonMembersUnique {
    param([Parameter(Mandatory = $true)][System.Xml.XmlElement]$Element)
    $jsonType = $Element.GetAttribute('type')
    if ($jsonType -ceq 'object') {
        $names = New-Object 'System.Collections.Generic.HashSet[string]' `
            ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($child in @($Element.ChildNodes)) {
            if ($child -isnot [System.Xml.XmlElement]) { continue }
            $memberName = if ($child.HasAttribute('item')) {
                [string]$child.GetAttribute('item')
            } else {
                [string]$child.LocalName
            }
            if (-not $names.Add($memberName)) {
                throw 'JSON contains a duplicate or case-colliding member.'
            }
            Assert-JsonMembersUnique -Element $child
        }
        return
    }
    if ($jsonType -ceq 'array') {
        foreach ($child in @($Element.ChildNodes)) {
            if ($child -is [System.Xml.XmlElement]) {
                Assert-JsonMembersUnique -Element $child
            }
        }
    }
}

function ConvertFrom-UniqueJsonBytes {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $reader = $null
    $document = $null
    try {
        $text = $script:StrictUtf8.GetString($Bytes)
        Microsoft.PowerShell.Utility\Add-Type -AssemblyName System.Runtime.Serialization -ErrorAction Stop
        $reader = [System.Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader(
            $Bytes,
            [System.Xml.XmlDictionaryReaderQuotas]::Max
        )
        $document = New-Object System.Xml.XmlDocument
        $document.Load($reader)
        Assert-JsonMembersUnique -Element $document.DocumentElement
        return ($text | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        throw "$Role is invalid or contains duplicate JSON members."
    } finally {
        if ($null -ne $reader) { $reader.Close() }
    }
}

function Read-BoundedFileBytes {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    $stream = New-Object System.IO.FileStream(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        if ($stream.Length -gt $MaximumBytes -or $stream.Length -gt [int]::MaxValue) {
            throw 'Release file exceeds the bounded read limit.'
        }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $count = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($count -le 0) { throw 'Release file ended before its bounded snapshot completed.' }
            $offset += $count
        }
        if ($stream.ReadByte() -ne -1) { throw 'Release file grew during its bounded snapshot.' }
        return ,$bytes
    } finally {
        $stream.Dispose()
    }
}

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Equals($pathRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $pathRoot
    }
    return $fullPath.TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    $current = Get-NormalizedFullPath -Path $pathRoot
    $segments = @(
        $fullPath.Substring($pathRoot.Length).TrimStart('\', '/').Split(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar),
            [System.StringSplitOptions]::RemoveEmptyEntries
        )
    )
    foreach ($segment in @('') + $segments) {
        if ($segment) { $current = Join-Path $current $segment }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            return $false
        }
        if ([string]$item.PSProvider.Name -ne 'FileSystem') {
            throw "$Role must use the FileSystem provider."
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "$Role must not contain a reparse point in its path chain."
        }
    }
    return $true
}

function Assert-NoReparsePathChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    if (-not (Test-NoReparsePathChain -Path $Path -Role $Role)) {
        throw "$Role contains a missing path segment."
    }
}

function Test-SafeRelativePath {
    param([AllowEmptyString()][string]$Path)
    return [bool](
        $Path -and -not $Path.Contains('\') -and -not $Path.StartsWith('/') -and
        $Path -notmatch '(^|/)\.\.?(/|$)' -and $Path -notmatch '[:\x00-\x1f]'
    )
}

function Resolve-SafeComponentFile {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Role
    )
    if (-not (Test-SafeRelativePath -Path $RelativePath)) {
        throw "$Role uses an unsafe relative path."
    }
    $candidate = Get-NormalizedFullPath -Path (Join-Path $Root ($RelativePath.Replace('/', '\')))
    if (-not (Test-IsWithinRoot -Root $Root -Candidate $candidate)) {
        throw "$Role is missing or escaped its component root."
    }
    Assert-NoReparsePathChain -Path $candidate -Role $Role
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "$Role is missing or escaped its component root."
    }
    return $candidate
}

function Add-AuditFinding {
    param(
        [Parameter(Mandatory = $true)][string]$RuleId,
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Line = 0,
        [string]$Candidate = ''
    )
    $fingerprint = if ($Candidate) { Get-StringSha256 -Text $Candidate } else { Get-StringSha256 -Text $Path }
    $script:ReleaseAuditFindings.Add([pscustomobject][ordered]@{
        rule_id = $RuleId
        component = $Component
        path = $Path
        line = $Line
        candidate_sha256 = $fingerprint
    })
}

function Assert-NoAuditFindings {
    if ($script:ReleaseAuditFindings.Count -eq 0) { return }
    $summaries = @(
        $script:ReleaseAuditFindings |
            Sort-Object rule_id, component, path, line |
            ForEach-Object {
                '{0}:{1}:{2}:{3}:{4}' -f $_.rule_id, $_.component, $_.path, $_.line, $_.candidate_sha256
            }
    )
    throw "Release audit failed without echoing candidate values: $($summaries -join ' | ')"
}

function Resolve-AuditRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Role
    )
    $resolved = Get-NormalizedFullPath -Path $Path
    Assert-NoReparsePathChain -Path $resolved -Role $Role
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "$Role is not a directory."
    }
    return $resolved
}

function Test-IsWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Candidate
    )
    $rootPath = Get-NormalizedFullPath -Path $Root
    $candidatePath = Get-NormalizedFullPath -Path $Candidate
    if ($candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    $rootPrefix = if ($rootPath.EndsWith([string][System.IO.Path]::DirectorySeparatorChar) -or
        $rootPath.EndsWith([string][System.IO.Path]::AltDirectorySeparatorChar)) {
        $rootPath
    } else {
        $rootPath + [System.IO.Path]::DirectorySeparatorChar
    }
    return $candidatePath.StartsWith(
        $rootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-NormalizedRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$FullPath
    )
    $rootPrefix = $Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $FullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A release candidate resolved outside its component root.'
    }
    return $FullPath.Substring($rootPrefix.Length).Replace('\', '/')
}

function Get-OrdinalSortedStrings {
    param([object[]]$Values)
    [string[]]$result = @($Values | ForEach-Object { [string]$_ })
    [System.Array]::Sort($result, [System.StringComparer]::Ordinal)
    return $result
}

function Assert-InventoryPathList {
    param(
        [Parameter(Mandatory = $true)][object]$Component
    )
    $componentName = [string]$Component.name
    [string[]]$paths = @(Get-PropertyArray -Object $Component -Name 'paths' | ForEach-Object { [string]$_ })
    if ($paths.Count -eq 0) { throw "Inventory component has no paths: $componentName" }
    $sorted = @(Get-OrdinalSortedStrings -Values $paths)
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    for ($index = 0; $index -lt $paths.Count; $index++) {
        $path = $paths[$index]
        if ($path -cne $sorted[$index]) {
            throw "Inventory paths must use ordinal order: $componentName"
        }
        if (-not $seen.Add($path)) {
            throw "Inventory contains a case-folding duplicate path: $componentName"
        }
        if (-not $path -or $path.Contains('\') -or $path.StartsWith('/') -or
            $path -match '(^|/)\.\.?(/|$)' -or $path -match '[:\x00-\x1f]') {
            throw "Inventory contains an unsafe relative path: $componentName"
        }
    }
}

function Assert-ReleaseInventoryContract {
    param([Parameter(Mandatory = $true)][object]$Inventory)
    if (@($Inventory.components).Count -ne 2) {
        throw 'Release inventory must define exactly two components.'
    }
    $maxBytes = $Inventory.max_text_file_bytes
    if (-not ($maxBytes -is [int] -or $maxBytes -is [long]) -or
        [long]$maxBytes -lt 1024 -or [long]$maxBytes -gt 16777216) {
        throw 'Release inventory max_text_file_bytes must be an integer from 1024 through 16777216.'
    }
    $componentNames = @($Inventory.components | ForEach-Object { [string]$_.name })
    if (($componentNames -join ',') -cne 'desktop_bridge,harness_sibling') {
        throw 'Release inventory component order and names are invalid.'
    }
    foreach ($component in @($Inventory.components)) {
        Assert-InventoryPathList -Component $component
        $allowedSeen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($path in @(Get-PropertyArray -Object $component -Name 'allowed_empty_directories')) {
            if (-not (Test-SafeRelativePath -Path ([string]$path)) -or -not $allowedSeen.Add([string]$path)) {
                throw "Release inventory contains an unsafe or duplicate allowed directory: $($component.name)"
            }
        }
    }
    $harnessPaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    foreach ($path in @(Get-PropertyArray -Object $Inventory.components[1] -Name 'paths')) {
        [void]$harnessPaths.Add([string]$path)
    }
    $frozenSeen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($frozen in @(Get-PropertyArray -Object $Inventory.components[1] -Name 'frozen_sha256')) {
        $path = [string]$frozen.path
        $hash = [string]$frozen.sha256
        if (-not (Test-SafeRelativePath -Path $path) -or -not $harnessPaths.Contains($path) -or
            -not $frozenSeen.Add($path) -or $hash -cnotmatch '^[a-f0-9]{64}$') {
            throw 'Release inventory contains an invalid frozen Harness binding.'
        }
    }
    $exclusionIds = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($rule in @(Get-PropertyArray -Object $Inventory -Name 'exclusions')) {
        $id = [string]$rule.id
        if ($id -cnotmatch '^[a-z0-9_]+$' -or -not $exclusionIds.Add($id) -or
            $rule.must_be_absent -isnot [bool]) {
            throw 'Release inventory contains an invalid exclusion rule.'
        }
        foreach ($name in @('directory_paths', 'exact_paths')) {
            foreach ($path in @(Get-PropertyArray -Object $rule -Name $name)) {
                if (-not (Test-SafeRelativePath -Path ([string]$path))) {
                    throw "Release inventory exclusion $id contains an unsafe path."
                }
            }
        }
        foreach ($name in @(Get-PropertyArray -Object $rule -Name 'directory_names')) {
            if (-not $name -or ([string]$name) -match '[\\/:\x00-\x1f]') {
                throw "Release inventory exclusion $id contains an unsafe directory name."
            }
        }
        foreach ($suffix in @(Get-PropertyArray -Object $rule -Name 'file_suffixes')) {
            if (-not $suffix -or ([string]$suffix) -match '[\\/:\x00-\x1f]') {
                throw "Release inventory exclusion $id contains an unsafe suffix."
            }
        }
    }
}

function Get-ExpectedDirectorySet {
    param([Parameter(Mandatory = $true)][object]$Component)
    $directories = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in @(Get-PropertyArray -Object $Component -Name 'paths')) {
        $segments = ([string]$path).Split('/')
        if ($segments.Count -le 1) { continue }
        for ($count = 1; $count -lt $segments.Count; $count++) {
            [void]$directories.Add(($segments[0..($count - 1)] -join '/'))
        }
    }
    foreach ($path in @(Get-PropertyArray -Object $Component -Name 'allowed_empty_directories')) {
        $candidate = [string]$path
        if (-not $candidate -or $candidate.Contains('\') -or $candidate.StartsWith('/') -or
            $candidate -match '(^|/)\.\.?(/|$)' -or $candidate -match '[:\x00-\x1f]') {
            throw "Inventory contains an unsafe allowed directory: $($Component.name)"
        }
        $segments = $candidate.Split('/')
        for ($count = 1; $count -le $segments.Count; $count++) {
            [void]$directories.Add(($segments[0..($count - 1)] -join '/'))
        }
    }
    return ,$directories
}

function Find-ExclusionRule {
    param(
        [Parameter(Mandatory = $true)][object]$Inventory,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$IsDirectory
    )
    foreach ($rule in @(Get-PropertyArray -Object $Inventory -Name 'exclusions')) {
        if ($IsDirectory) {
            foreach ($path in @(Get-PropertyArray -Object $rule -Name 'directory_paths')) {
                if ($RelativePath.Equals([string]$path, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $rule
                }
            }
            foreach ($directoryName in @(Get-PropertyArray -Object $rule -Name 'directory_names')) {
                if ($Name.Equals([string]$directoryName, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $rule
                }
            }
        } else {
            foreach ($path in @(Get-PropertyArray -Object $rule -Name 'exact_paths')) {
                if ($RelativePath.Equals([string]$path, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $rule
                }
            }
            foreach ($suffix in @(Get-PropertyArray -Object $rule -Name 'file_suffixes')) {
                if ($RelativePath.EndsWith([string]$suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $rule
                }
            }
        }
    }
    return $null
}

function Test-ForbiddenArtifact {
    param(
        [Parameter(Mandatory = $true)][object]$Inventory,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$IsDirectory
    )
    $forbidden = $Inventory.forbidden_artifacts
    if ($IsDirectory) {
        foreach ($directoryName in @(Get-PropertyArray -Object $forbidden -Name 'directory_names')) {
            if ($Name.Equals([string]$directoryName, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
        return $false
    }
    foreach ($fileName in @(Get-PropertyArray -Object $forbidden -Name 'file_names')) {
        if ($Name.Equals([string]$fileName, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    foreach ($suffix in @(Get-PropertyArray -Object $forbidden -Name 'file_suffixes')) {
        if ($Name.EndsWith([string]$suffix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Get-ComponentCandidates {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object]$Component,
        [Parameter(Mandatory = $true)][object]$Inventory
    )
    $componentName = [string]$Component.name
    $expectedDirectories = Get-ExpectedDirectorySet -Component $Component
    $candidates = New-Object System.Collections.Generic.List[object]
    $seenCandidates = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $excluded = @{}
    foreach ($rule in @(Get-PropertyArray -Object $Inventory -Name 'exclusions')) {
        $excluded[[string]$rule.id] = New-Object System.Collections.Generic.List[string]
    }
    $stack = New-Object System.Collections.Generic.Stack[object]
    $stack.Push([pscustomobject]@{ FullPath = $Root; RelativePath = '' })
    while ($stack.Count -gt 0) {
        $current = $stack.Pop()
        Assert-NoReparsePathChain -Path ([string]$current.FullPath) `
            -Role ("release traversal {0}" -f $componentName)
        $directory = New-Object System.IO.DirectoryInfo([string]$current.FullPath)
        foreach ($entry in $directory.EnumerateFileSystemInfos()) {
            $relative = if ($current.RelativePath) {
                ([string]$current.RelativePath).TrimEnd('/') + '/' + $entry.Name
            } else {
                $entry.Name
            }
            $isDirectory = ($entry.Attributes -band [System.IO.FileAttributes]::Directory) -ne 0
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                Add-AuditFinding -RuleId 'REPARSE_POINT' -Component $componentName -Path $relative
                continue
            }
            $rule = Find-ExclusionRule -Inventory $Inventory -RelativePath $relative -Name $entry.Name -IsDirectory $isDirectory
            if ($null -ne $rule) {
                $ruleId = [string]$rule.id
                $excluded[$ruleId].Add($relative)
                if ([bool]$rule.must_be_absent) {
                    Add-AuditFinding -RuleId 'FORBIDDEN_EXCLUSION_PRESENT' -Component $componentName -Path $relative
                }
                continue
            }
            if (Test-ForbiddenArtifact -Inventory $Inventory -Name $entry.Name -IsDirectory $isDirectory) {
                Add-AuditFinding -RuleId 'FORBIDDEN_RUNTIME_ARTIFACT' -Component $componentName -Path $relative
                continue
            }
            if ($isDirectory) {
                if (-not $expectedDirectories.Contains($relative)) {
                    Add-AuditFinding -RuleId 'UNEXPECTED_DIRECTORY' -Component $componentName -Path $relative
                    continue
                }
                $stack.Push([pscustomobject]@{ FullPath = $entry.FullName; RelativePath = $relative })
                continue
            }
            if (-not $seenCandidates.Add($relative)) {
                Add-AuditFinding -RuleId 'CASE_FOLDING_DUPLICATE' -Component $componentName -Path $relative
                continue
            }
            $candidates.Add([pscustomobject]@{ Path = $relative; FullPath = $entry.FullName })
        }
    }
    $exclusionResults = New-Object System.Collections.Generic.List[object]
    foreach ($rule in @(Get-PropertyArray -Object $Inventory -Name 'exclusions')) {
        $ruleId = [string]$rule.id
        $matched = @(Get-OrdinalSortedStrings -Values @($excluded[$ruleId]))
        $canonical = if ($matched.Count -gt 0) { ($matched -join "`n") + "`n" } else { '' }
        $exclusionResults.Add([pscustomobject][ordered]@{
            component = $componentName
            rule_id = $ruleId
            match_count = $matched.Count
            matched_path_set_sha256 = Get-StringSha256 -Text $canonical
            must_be_absent = [bool]$rule.must_be_absent
            status = 'pass'
        })
    }
    return [pscustomobject]@{
        Candidates = $candidates.ToArray()
        ExclusionResults = $exclusionResults.ToArray()
    }
}

function Get-LineNumberAtIndex {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [Parameter(Mandatory = $true)][int]$Index
    )
    if ($Index -le 0) { return 1 }
    return 1 + ([regex]::Matches($Text.Substring(0, $Index), "`n")).Count
}

function Test-SyntheticTestUuid {
    param(
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )
    if ($Component -ne 'desktop_bridge' -or -not $Path.StartsWith('tests/', [System.StringComparison]::Ordinal)) {
        return $false
    }
    foreach ($segment in $Value.Split('-')) {
        if (-not $segment -or $segment.Trim($segment[0]).Length -ne 0) { return $false }
    }
    return $true
}

function Test-MarkdownStructure {
    param(
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text,
        [Parameter(Mandatory = $true)][System.Collections.Generic.HashSet[string]]$ExpectedPaths
    )
    $openFenceCharacter = ''
    $openFenceLength = 0
    $lines = [regex]::Split($Text, "\r\n|\n|\r")
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $match = [regex]::Match($lines[$index], '^\s*(?<fence>`{3,}|~{3,})')
        if (-not $match.Success) { continue }
        $fence = $match.Groups['fence'].Value
        if (-not $openFenceCharacter) {
            $openFenceCharacter = $fence.Substring(0, 1)
            $openFenceLength = $fence.Length
        } elseif ($fence.StartsWith($openFenceCharacter) -and $fence.Length -ge $openFenceLength) {
            $openFenceCharacter = ''
            $openFenceLength = 0
        }
    }
    if ($openFenceCharacter) {
        Add-AuditFinding -RuleId 'UNBALANCED_MARKDOWN_FENCE' -Component $Component -Path $RelativePath
    }

    $fullPath = Join-Path $Root ($RelativePath.Replace('/', '\'))
    foreach ($match in [regex]::Matches($Text, '(?m)!?\[[^\]]*\]\((?<target><[^>]+>|[^\s\)]+)(?:\s+["''][^"'']*["''])?\)')) {
        $target = $match.Groups['target'].Value.Trim().Trim('<', '>')
        if (-not $target -or $target.StartsWith('#') -or $target.StartsWith('//') -or
            $target -match '^[A-Za-z][A-Za-z0-9+.-]*:') {
            continue
        }
        $target = ($target -split '#', 2)[0]
        $target = ($target -split '\?', 2)[0]
        try {
            $target = [System.Uri]::UnescapeDataString($target)
            $resolvedTarget = [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $fullPath) $target))
        } catch {
            Add-AuditFinding -RuleId 'INVALID_MARKDOWN_LINK' -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $Text -Index $match.Index) -Candidate $match.Groups['target'].Value
            continue
        }
        if (-not (Test-IsWithinRoot -Root $Root -Candidate $resolvedTarget)) {
            Add-AuditFinding -RuleId 'MARKDOWN_LINK_ESCAPES_COMPONENT' -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $Text -Index $match.Index) -Candidate $match.Groups['target'].Value
            continue
        }
        $resolvedRelative = Get-NormalizedRelativePath -Root $Root -FullPath $resolvedTarget
        if (-not $ExpectedPaths.Contains($resolvedRelative)) {
            Add-AuditFinding -RuleId 'BROKEN_OR_UNINVENTORIED_MARKDOWN_LINK' -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $Text -Index $match.Index) -Candidate $match.Groups['target'].Value
        }
    }
}

function Test-TextContent {
    param(
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][System.Collections.Generic.HashSet[string]]$ExpectedPaths
    )
    try {
        $text = $script:StrictUtf8.GetString($Bytes)
    } catch [System.Text.DecoderFallbackException] {
        Add-AuditFinding -RuleId 'NON_UTF8_CONTENT' -Component $Component -Path $RelativePath
        return
    }
    if ($text.IndexOf([char]0) -ge 0) {
        Add-AuditFinding -RuleId 'BINARY_NUL_CONTENT' -Component $Component -Path $RelativePath
        return
    }
    $scaffoldPattern = '(?i)\b(?:TO' + 'DO|T' + 'BD|FIX' + 'ME|CHANGE' + 'ME)\b'
    if ([regex]::IsMatch($text, $scaffoldPattern)) {
        $match = [regex]::Match($text, $scaffoldPattern)
        Add-AuditFinding -RuleId 'UNFINISHED_SCAFFOLD' -Component $Component -Path $RelativePath `
            -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $match.Value
    }
    $sensitivePatterns = [ordered]@{
        PEM_KEY_HEADER = '-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----'
        JWT = '(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])'
        BEARER_TOKEN = '(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}'
        URL_USERINFO = '(?i)https?://[^/\s:@]+:[^/\s@]+@'
        PROVIDER_KEY = '(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{12,}|AIza[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,})(?![A-Za-z0-9])'
        REAL_FEISHU_ID = '(?<![A-Za-z0-9_])(?:(?:cli|ou|oc|on|om)_[A-Za-z0-9]{12,}|od-[A-Za-z0-9]{12,})(?![A-Za-z0-9_])'
    }
    foreach ($entry in $sensitivePatterns.GetEnumerator()) {
        foreach ($match in [regex]::Matches($text, [string]$entry.Value)) {
            Add-AuditFinding -RuleId ([string]$entry.Key) -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $match.Value
        }
    }
    foreach ($match in [regex]::Matches($text, '(?i)(?:^|[^A-Za-z0-9])(?:[A-Z0-9_]*(?:APP_SECRET|CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN|ID_TOKEN|PASSWORD|PASSWD|API_KEY|COOKIE|PRIVATE_KEY|WEBHOOK_SECRET|AUTHORIZATION))\s*[:=]\s*["''](?<value>[^"''\r\n]+)["'']')) {
        $value = $match.Groups['value'].Value.Trim()
        if ($value -match '^(?:<.+>|\$\{.+\}|%[A-Z0-9_]+%|\{\{.+\}\}|\.{3}|x{3,}|sample|example|test-only|redacted|null|none|true|false)$') {
            continue
        }
        Add-AuditFinding -RuleId 'SECRET_LITERAL_ASSIGNMENT' -Component $Component -Path $RelativePath `
            -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $value
    }
    foreach ($match in [regex]::Matches($text, '(?im)^(?:[A-Z0-9_]*(?:APP_SECRET|CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN|ID_TOKEN|PASSWORD|PASSWD|API_KEY|COOKIE|PRIVATE_KEY|WEBHOOK_SECRET|AUTHORIZATION))=(?<value>[^\s#]+)')) {
        $value = $match.Groups['value'].Value.Trim().Trim('"', "'")
        if (-not $value -or $value -match '^(?:<.+>|\$\{.+\}|%[A-Z0-9_]+%|\{\{.+\}\}|\.{3}|x{3,}|sample|example|test-only|redacted|null|none|true|false)$') {
            continue
        }
        Add-AuditFinding -RuleId 'SECRET_LITERAL_ASSIGNMENT' -Component $Component -Path $RelativePath `
            -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $value
    }
    foreach ($match in [regex]::Matches($text, '(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b')) {
        if (-not (Test-SyntheticTestUuid -Component $Component -Path $RelativePath -Value $match.Value)) {
            Add-AuditFinding -RuleId 'REAL_TASK_UUID' -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $match.Value
        }
    }
    foreach ($match in [regex]::Matches($text, '(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s"''`<>]+|\\\\[A-Za-z0-9][A-Za-z0-9._-]{1,62}\\[A-Za-z0-9$._-][^\s"''`<>]*|/(?:Users|home)/[^\s"''`<>/]+(?:/[^\s"''`<>]+)?)')) {
        $value = $match.Value.TrimEnd('.', ',', ';', ':', ')', ']')
        $isFixture = $Component -eq 'desktop_bridge' -and
            $RelativePath.StartsWith('tests/', [System.StringComparison]::Ordinal) -and
            $value -match '^(?i)X:[\\/]fixtures(?:[\\/]|$)'
        if (-not $isFixture) {
            Add-AuditFinding -RuleId 'ABSOLUTE_LOCAL_PATH' -Component $Component -Path $RelativePath `
                -Line (Get-LineNumberAtIndex -Text $text -Index $match.Index) -Candidate $value
        }
    }
    if ($RelativePath.EndsWith('.md', [System.StringComparison]::OrdinalIgnoreCase)) {
        Test-MarkdownStructure -Component $Component -Root $Root -RelativePath $RelativePath -Text $text -ExpectedPaths $ExpectedPaths
    }
}

function New-ComponentSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object]$Component,
        [Parameter(Mandatory = $true)][object]$Inventory,
        [switch]$ScanContent
    )
    $componentName = [string]$Component.name
    [string[]]$expected = @(Get-PropertyArray -Object $Component -Name 'paths' | ForEach-Object { [string]$_ })
    if ($expected.Count -gt $script:ReleaseAuditMaxFilesPerComponent) {
        throw "Release component $componentName exceeds the bounded inventory path count."
    }
    $expectedOrdinal = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
    $expectedIgnoreCase = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $expected) {
        [void]$expectedOrdinal.Add($path)
        [void]$expectedIgnoreCase.Add($path)
    }
    $enumeration = Get-ComponentCandidates -Root $Root -Component $Component -Inventory $Inventory
    $actualByPath = @{}
    foreach ($candidate in @($enumeration.Candidates)) {
        $relative = [string]$candidate.Path
        if (-not $expectedIgnoreCase.Contains($relative)) {
            Add-AuditFinding -RuleId 'UNINVENTORIED_FILE' -Component $componentName -Path $relative
            continue
        }
        if (-not $expectedOrdinal.Contains($relative)) {
            Add-AuditFinding -RuleId 'PATH_CASE_DRIFT' -Component $componentName -Path $relative
            continue
        }
        $actualByPath[$relative] = [string]$candidate.FullPath
    }
    $files = New-Object System.Collections.Generic.List[object]
    $bytesByPath = @{}
    [long]$snapshotBytes = 0
    foreach ($relative in $expected) {
        if (-not $actualByPath.ContainsKey($relative)) {
            Add-AuditFinding -RuleId 'MISSING_INVENTORY_FILE' -Component $componentName -Path $relative
            continue
        }
        $fullPath = [string]$actualByPath[$relative]
        Assert-NoReparsePathChain -Path $fullPath -Role ("release file {0}/{1}" -f $componentName, $relative)
        $fileBefore = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
        if (($fileBefore.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Add-AuditFinding -RuleId 'REPARSE_POINT' -Component $componentName -Path $relative
            continue
        }
        if ([long]$fileBefore.Length -gt [long]$Inventory.max_text_file_bytes) {
            Add-AuditFinding -RuleId 'TEXT_FILE_TOO_LARGE' -Component $componentName -Path $relative
            continue
        }
        $bytes = Read-BoundedFileBytes -Path $fullPath -MaximumBytes ([long]$Inventory.max_text_file_bytes)
        Assert-NoReparsePathChain -Path $fullPath -Role ("release file {0}/{1} after read" -f $componentName, $relative)
        $fileAfter = Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop
        $snapshotBytes += $bytes.LongLength
        if ($snapshotBytes -gt $script:ReleaseAuditMaxSnapshotBytesPerComponent) {
            throw "Release component $componentName exceeds the bounded aggregate snapshot size."
        }
        if ([long]$fileBefore.Length -ne $bytes.LongLength -or
            [long]$fileAfter.Length -ne $bytes.LongLength -or
            $fileBefore.LastWriteTimeUtc -ne $fileAfter.LastWriteTimeUtc -or
            ($fileAfter.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            Add-AuditFinding -RuleId 'SOURCE_MUTATED_DURING_READ' -Component $componentName -Path $relative
            continue
        }
        $sha256 = Get-BytesSha256 -Bytes $bytes
        $bytesByPath[$relative] = $bytes
        $files.Add([pscustomobject][ordered]@{
            path = $relative
            sha256 = $sha256
            size_bytes = $bytes.LongLength
        })
        if ($ScanContent) {
            Test-TextContent -Component $componentName -Root $Root -RelativePath $relative -Bytes $bytes -ExpectedPaths $expectedOrdinal
        }
    }
    return [pscustomobject]@{
        Component = [pscustomobject][ordered]@{
            name = $componentName
            file_count = $files.Count
            files = $files.ToArray()
        }
        ExclusionResults = @($enumeration.ExclusionResults)
        BytesByPath = $bytesByPath
    }
}

function Get-ManifestCanonicalText {
    param([Parameter(Mandatory = $true)][object[]]$Components)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('feishu-codex-source-manifest-v1')
    foreach ($component in $Components) {
        foreach ($file in @($component.files)) {
            $lines.Add(("{0}`t{1}`t{2}" -f $component.name, $file.path, $file.sha256))
        }
    }
    return ($lines -join "`n") + "`n"
}

function Get-PathSetCanonicalText {
    param([Parameter(Mandatory = $true)][object[]]$Components)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('feishu-codex-audited-path-set-v1')
    foreach ($component in $Components) {
        foreach ($file in @($component.files)) {
            $lines.Add(("{0}`t{1}" -f $component.name, $file.path))
        }
    }
    return ($lines -join "`n") + "`n"
}

function Get-SnapshotFileRecord {
    param(
        [Parameter(Mandatory = $true)][object[]]$Components,
        [Parameter(Mandatory = $true)][string]$ComponentName,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    $matches = @(
        $Components |
            Where-Object { [string]$_.name -ceq $ComponentName } |
            ForEach-Object { @($_.files) } |
            Where-Object { [string]$_.path -ceq $RelativePath }
    )
    if ($matches.Count -ne 1) {
        throw "Snapshot has no unique file record for $ComponentName/$RelativePath."
    }
    return $matches[0]
}

function Test-SkillMetadata {
    param(
        [Parameter(Mandatory = $true)][hashtable]$BytesByPath,
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$ExpectedName,
        [string]$SkillPath = 'SKILL.md',
        [string]$InterfacePath = 'agents/openai.yaml'
    )
    $text = $script:StrictUtf8.GetString([byte[]]$BytesByPath[$SkillPath])
    $lines = [regex]::Split($text, "\r\n|\n|\r")
    if ($lines.Count -lt 4 -or $lines[0] -cne '---') {
        Add-AuditFinding -RuleId 'INVALID_SKILL_FRONTMATTER' -Component $Component -Path $SkillPath
        return
    }
    $closing = -1
    for ($index = 1; $index -lt [Math]::Min($lines.Count, 60); $index++) {
        if ($lines[$index] -ceq '---') { $closing = $index; break }
    }
    if ($closing -lt 2) {
        Add-AuditFinding -RuleId 'INVALID_SKILL_FRONTMATTER' -Component $Component -Path $SkillPath
        return
    }
    $name = ''
    $description = ''
    $frontmatterKeys = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines[1..($closing - 1)]) {
        if ($line -match '^([A-Za-z0-9-]+):') { $frontmatterKeys.Add($Matches[1]) }
        if ($line -match '^name:\s*(.+?)\s*$') { $name = $Matches[1].Trim().Trim('"', "'") }
        if ($line -match '^description:\s*(.+?)\s*$') { $description = $Matches[1].Trim().Trim('"', "'") }
    }
    $allowedKeys = @('name', 'description', 'license', 'allowed-tools', 'metadata')
    $unexpectedKeys = @($frontmatterKeys | Where-Object { $_ -notin $allowedKeys })
    if ($name -cne $ExpectedName -or -not $description -or $unexpectedKeys.Count -gt 0 -or
        $name -notmatch '^[a-z0-9-]+$' -or $name.StartsWith('-') -or $name.EndsWith('-') -or
        $name.Contains('--') -or $name.Length -gt 64 -or $description.Length -gt 1024 -or
        $description -match '[<>]') {
        Add-AuditFinding -RuleId 'INVALID_SKILL_FRONTMATTER' -Component $Component -Path $SkillPath
    }
    $interfaceText = $script:StrictUtf8.GetString([byte[]]$BytesByPath[$InterfacePath])
    foreach ($marker in @('display_name:', 'short_description:', 'default_prompt:')) {
        if ($interfaceText -notmatch [regex]::Escape($marker)) {
            Add-AuditFinding -RuleId 'INVALID_SKILL_INTERFACE' -Component $Component -Path $InterfacePath -Candidate $marker
        }
    }
}

function Test-EvidenceSchemaNode {
    param(
        [object]$Node,
        [Parameter(Mandatory = $true)][object]$Schema,
        [Parameter(Mandatory = $true)][string]$Location
    )
    if ($null -eq $Node) { return }
    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        $refProperty = $Node.PSObject.Properties['$ref']
        if ($refProperty) {
            $reference = [string]$refProperty.Value
            if ($reference -notmatch '^#/\$defs/([^/]+)$' -or
                -not $Schema.'$defs'.PSObject.Properties[$Matches[1]]) {
                Add-AuditFinding -RuleId 'INVALID_EVIDENCE_SCHEMA_REF' -Component 'desktop_bridge' `
                    -Path 'assets/external-test-evidence.schema.json' -Candidate $Location
            }
        }
        $patternProperty = $Node.PSObject.Properties['pattern']
        if ($patternProperty) {
            try {
                [void][regex]::new([string]$patternProperty.Value)
            } catch {
                Add-AuditFinding -RuleId 'INVALID_EVIDENCE_SCHEMA_PATTERN' -Component 'desktop_bridge' `
                    -Path 'assets/external-test-evidence.schema.json' -Candidate $Location
            }
        }
        $requiredProperty = $Node.PSObject.Properties['required']
        $propertiesProperty = $Node.PSObject.Properties['properties']
        if ($requiredProperty -and $propertiesProperty) {
            foreach ($requiredName in @($requiredProperty.Value)) {
                if (-not $propertiesProperty.Value.PSObject.Properties[[string]$requiredName]) {
                    Add-AuditFinding -RuleId 'INVALID_EVIDENCE_SCHEMA_REQUIRED' -Component 'desktop_bridge' `
                        -Path 'assets/external-test-evidence.schema.json' -Candidate ($Location + '/' + $requiredName)
                }
            }
        }
        foreach ($property in $Node.PSObject.Properties) {
            Test-EvidenceSchemaNode -Node $property.Value -Schema $Schema -Location ($Location + '/' + $property.Name)
        }
        return
    }
    if ($Node -is [System.Collections.IEnumerable] -and $Node -isnot [string]) {
        $index = 0
        foreach ($item in $Node) {
            Test-EvidenceSchemaNode -Node $item -Schema $Schema -Location ($Location + '[' + $index + ']')
            $index += 1
        }
    }
}

function Assert-OutputPathSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Roots
    )
    $fullPath = Get-NormalizedFullPath -Path $Path
    foreach ($root in $Roots) {
        if (Test-IsWithinRoot -Root $root -Candidate $fullPath) {
            throw 'Release audit output must be outside every audited Skill root.'
        }
    }
    $parent = Split-Path -Parent $fullPath
    if (-not $parent) {
        throw 'Release audit output parent directory must already exist.'
    }
    $resolvedParent = Get-NormalizedFullPath -Path $parent
    Assert-NoReparsePathChain -Path $resolvedParent -Role 'release audit output parent'
    if (-not (Test-Path -LiteralPath $resolvedParent -PathType Container)) {
        throw 'Release audit output parent directory must already exist.'
    }
    $pending = $fullPath + '.pending'
    if (Test-Path -LiteralPath $fullPath) { throw 'Release audit output already exists; overwrite is forbidden.' }
    if (Test-Path -LiteralPath $pending) { throw 'Release audit pending output already exists; overwrite is forbidden.' }
    return [pscustomobject]@{ Final = $fullPath; Pending = $pending }
}

function Assert-ReleaseSourceRoute {
    param(
        [Parameter(Mandatory = $true)][string]$Desktop,
        [Parameter(Mandatory = $true)][object]$Inventory,
        [Parameter(Mandatory = $true)][string]$Role
    )

    if ([string]$Inventory.release_name -cne 'feishu-codex-bridge-plugin') {
        throw 'Release inventory name does not identify the canonical Feishu Bridge plugin.'
    }
    $desktopComponents = @($Inventory.components | Where-Object { [string]$_.name -ceq 'desktop_bridge' })
    if ($desktopComponents.Count -ne 1 -or [string]$desktopComponents[0].root_role -cne 'plugin_root') {
        throw 'Release inventory does not bind desktop_bridge to the plugin root.'
    }

    $manifestPath = Resolve-SafeComponentFile -Root $Desktop -RelativePath '.codex-plugin/plugin.json' `
        -Role 'plugin manifest for source-route verification'
    $manifestBytes = Read-BoundedFileBytes -Path $manifestPath -MaximumBytes $script:ReleaseAuditMaxBytes
    Assert-NoReparsePathChain -Path $manifestPath -Role 'plugin manifest after source-route read'
    $manifest = ConvertFrom-UniqueJsonBytes -Bytes $manifestBytes -Role 'Plugin manifest'
    if ($manifest.name -isnot [string] -or $manifest.version -isnot [string] -or
        [string]$manifest.name -cne 'feishu-codex-bridge' -or
        [string]$manifest.version -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$') {
        throw 'Plugin manifest identity is invalid during source-route verification.'
    }

    if ($Role -ceq 'canonical-development') {
        $repositoryRoot = Get-NormalizedFullPath -Path (Split-Path -Parent (Split-Path -Parent $Desktop))
        $marketplacePath = Join-Path $repositoryRoot '.agents\plugins\marketplace.json'
        Assert-NoReparsePathChain -Path $marketplacePath -Role 'repository marketplace source route'
        if (-not (Test-Path -LiteralPath $marketplacePath -PathType Leaf)) {
            throw 'Canonical development source requires the repository marketplace.'
        }
        $marketplaceBytes = Read-BoundedFileBytes -Path $marketplacePath -MaximumBytes $script:ReleaseAuditMaxBytes
        Assert-NoReparsePathChain -Path $marketplacePath -Role 'repository marketplace after read'
        $marketplace = ConvertFrom-UniqueJsonBytes -Bytes $marketplaceBytes -Role 'Repository marketplace'
        if ($marketplace.name -isnot [string] -or
            [string]$marketplace.name -cne 'feishu-codex-bridge') {
            throw 'Repository marketplace identity does not match feishu-codex-bridge.'
        }
        if ($marketplace.plugins -isnot [System.Array]) {
            throw 'Repository marketplace plugin collection is not a JSON array.'
        }
        $entries = @($marketplace.plugins | Where-Object {
            $_ -is [pscustomobject] -and $_.name -is [string] -and
            [string]$_.name -ieq 'feishu-codex-bridge'
        })
        if ($entries.Count -ne 1 -or [string]$entries[0].name -cne 'feishu-codex-bridge' -or
            $entries[0].source -isnot [pscustomobject] -or
            $entries[0].source.source -isnot [string] -or
            $entries[0].source.path -isnot [string] -or
            [string]$entries[0].source.source -cne 'local' -or
            [string]$entries[0].source.path -cne './plugins/feishu-codex-bridge') {
            throw 'Repository marketplace has no unique canonical Feishu Bridge source route.'
        }
        $routedRoot = Get-NormalizedFullPath -Path (Join-Path $repositoryRoot `
            ([string]$entries[0].source.path).Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        if (-not $routedRoot.Equals($Desktop, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'Repository marketplace resolves to a different Feishu Bridge source root.'
        }
        return
    }

    $configuredCodexHome = [Environment]::GetEnvironmentVariable(
        'CODEX_HOME',
        [EnvironmentVariableTarget]::Process
    )
    $codexHome = if ($null -ne $configuredCodexHome) {
        if ([string]::IsNullOrWhiteSpace($configuredCodexHome) -or
            $configuredCodexHome -cne $configuredCodexHome.Trim() -or
            $configuredCodexHome.IndexOf([char]0) -ge 0 -or
            -not [System.IO.Path]::IsPathFullyQualified($configuredCodexHome) -or
            $configuredCodexHome.StartsWith('\\') -or
            $configuredCodexHome.StartsWith('//')) {
            throw 'Configured CODEX_HOME is not an exact fully qualified local path.'
        }
        Resolve-AuditRoot -Path $configuredCodexHome -Role 'configured Codex home'
    } else {
        Resolve-AuditRoot -Path (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex') `
            -Role 'default Codex home'
    }
    $expectedVersionRoot = Get-NormalizedFullPath -Path (Join-Path $codexHome `
        ('plugins\cache\feishu-codex-bridge\feishu-codex-bridge\' + [string]$manifest.version))
    if (-not $expectedVersionRoot.Equals($Desktop, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Installed snapshot role is valid only for the exact versioned Codex plugin cache root.'
    }
}

$desktop = Resolve-AuditRoot -Path $DesktopRoot -Role 'Desktop Skill root'
$inventoryPath = Resolve-SafeComponentFile -Root $desktop -RelativePath 'assets/release-inventory.json' `
    -Role 'release inventory'
if (-not (Test-Path -LiteralPath $inventoryPath -PathType Leaf)) {
    throw 'Release inventory is missing from the Desktop Skill root.'
}
$inventoryBytes = Read-BoundedFileBytes -Path $inventoryPath -MaximumBytes $script:ReleaseAuditMaxBytes
Assert-NoReparsePathChain -Path $inventoryPath -Role 'release inventory after read'
$inventoryText = $script:StrictUtf8.GetString($inventoryBytes)
$inventory = ConvertFrom-UniqueJsonBytes -Bytes $inventoryBytes -Role 'Release inventory'
if ([int]$inventory.schema_version -ne 1) { throw 'Unsupported release inventory schema.' }
if ([string]$inventory.source_version -ne '4.2.0-alpha.63') { throw 'Release inventory source version is not current.' }
Assert-ReleaseInventoryContract -Inventory $inventory
$script:ReleaseAuditMaxBytes = [long]$inventory.max_text_file_bytes
Assert-ReleaseSourceRoute -Desktop $desktop -Inventory $inventory -Role $SourceRole

$desktopComponent = @($inventory.components | Where-Object { [string]$_.name -eq 'desktop_bridge' })
$harnessComponent = @($inventory.components | Where-Object { [string]$_.name -eq 'harness_sibling' })
if ($desktopComponent.Count -ne 1 -or $harnessComponent.Count -ne 1) {
    throw 'Release inventory must define exactly one Desktop and one Harness component.'
}
$desktopComponent = $desktopComponent[0]
$harnessComponent = $harnessComponent[0]
$componentRoots = [ordered]@{ desktop_bridge = $desktop }
if (-not $DesktopOnly) {
    if (-not $HarnessRoot) { throw 'Full release audit requires an explicit HarnessRoot.' }
    $harness = Resolve-AuditRoot -Path $HarnessRoot -Role 'Harness Skill root'
    if ((Test-IsWithinRoot -Root $desktop -Candidate $harness) -or
        (Test-IsWithinRoot -Root $harness -Candidate $desktop)) {
        throw 'Desktop and Harness Skill roots must not contain one another.'
    }
    $componentRoots['harness_sibling'] = $harness
}

$firstComponents = New-Object System.Collections.Generic.List[object]
$firstExclusions = New-Object System.Collections.Generic.List[object]
$firstBytesByComponent = @{}
$selectedComponents = @($desktopComponent)
if (-not $DesktopOnly) { $selectedComponents += $harnessComponent }
foreach ($component in $selectedComponents) {
    $snapshot = New-ComponentSnapshot -Root $componentRoots[[string]$component.name] `
        -Component $component -Inventory $inventory -ScanContent
    $firstComponents.Add($snapshot.Component)
    $firstBytesByComponent[[string]$component.name] = $snapshot.BytesByPath
    foreach ($result in @($snapshot.ExclusionResults)) { $firstExclusions.Add($result) }
}

# Stop before any secondary metadata read if traversal already found a link,
# unexpected artifact, or other unsafe candidate.
Assert-NoAuditFindings

Test-SkillMetadata -BytesByPath $firstBytesByComponent['desktop_bridge'] `
    -Component 'desktop_bridge' -ExpectedName 'feishu-codex-bridge' `
    -SkillPath 'skills/feishu-codex-bridge/SKILL.md' `
    -InterfacePath 'skills/feishu-codex-bridge/agents/openai.yaml'
$workspaceAgents = Join-Path (Split-Path -Parent (Split-Path -Parent $desktop)) 'AGENTS.md'
$canonicalAgents = Resolve-SafeComponentFile -Root $desktop `
    -RelativePath 'assets/AGENTS.feishu-codex-bridge.md' -Role 'canonical AGENTS rules'
$canonicalAgentsHash = Get-BytesSha256 -Bytes `
    ([byte[]]$firstBytesByComponent['desktop_bridge']['assets/AGENTS.feishu-codex-bridge.md'])
$workspaceAgentsPresentFirst = Test-NoReparsePathChain -Path $workspaceAgents -Role 'workspace AGENTS mirror'
$workspaceAgentsHashFirst = ''
if ($workspaceAgentsPresentFirst) {
    if (-not (Test-Path -LiteralPath $workspaceAgents -PathType Leaf)) {
        Add-AuditFinding -RuleId 'AGENTS_MIRROR_WRONG_TYPE' -Component 'desktop_bridge' -Path 'AGENTS.md'
    } else {
        $workspaceAgentsHashFirst = Get-BytesSha256 -Bytes (Read-BoundedFileBytes -Path $workspaceAgents `
            -MaximumBytes $script:ReleaseAuditMaxBytes)
        Assert-NoReparsePathChain -Path $workspaceAgents -Role 'workspace AGENTS mirror after read'
    }
    if ($workspaceAgentsHashFirst -cne $canonicalAgentsHash) {
        Add-AuditFinding -RuleId 'AGENTS_MIRROR_DRIFT' -Component 'desktop_bridge' -Path 'AGENTS.md'
    }
}

if (-not $DesktopOnly) {
    Test-SkillMetadata -BytesByPath $firstBytesByComponent['harness_sibling'] -Component 'harness_sibling' `
        -ExpectedName 'feishu-codex-harness-bridge'
    $harnessReference = $script:StrictUtf8.GetString(
        [byte[]]$firstBytesByComponent['harness_sibling']['references/external-lab.md']
    )
    foreach ($frozen in @(Get-PropertyArray -Object $harnessComponent -Name 'frozen_sha256')) {
        $relative = [string]$frozen.path
        $expectedHash = ([string]$frozen.sha256).ToLowerInvariant()
        $actualHash = [string](Get-SnapshotFileRecord -Components $firstComponents.ToArray() `
            -ComponentName 'harness_sibling' -RelativePath $relative).sha256
        if ($actualHash -cne $expectedHash) {
            Add-AuditFinding -RuleId 'HARNESS_FROZEN_HASH_DRIFT' -Component 'harness_sibling' -Path $relative
        }
        if ($harnessReference -notmatch [regex]::Escape($expectedHash)) {
            Add-AuditFinding -RuleId 'HARNESS_FROZEN_HASH_UNBOUND' -Component 'harness_sibling' `
                -Path 'references/external-lab.md' -Candidate $relative
        }
    }
}

$schemaText = $script:StrictUtf8.GetString(
    [byte[]]$firstBytesByComponent['desktop_bridge']['assets/external-test-evidence.schema.json']
)
$schema = $schemaText | ConvertFrom-Json
if ([string]$schema.'$schema' -ne 'https://json-schema.org/draft/2020-12/schema' -or
    [string]$schema.title -ne 'Feishu Codex Bridge P0-B external test evidence') {
    Add-AuditFinding -RuleId 'INVALID_EVIDENCE_SCHEMA' -Component 'desktop_bridge' `
        -Path 'assets/external-test-evidence.schema.json'
}
Test-EvidenceSchemaNode -Node $schema -Schema $schema -Location '#'
foreach ($faultId in 1..12 | ForEach-Object { 'F{0:d2}' -f $_ }) {
    if ($schemaText -notmatch ('"{0}"' -f [regex]::Escape($faultId))) {
        Add-AuditFinding -RuleId 'EVIDENCE_SCHEMA_MISSING_FAULT' -Component 'desktop_bridge' `
            -Path 'assets/external-test-evidence.schema.json' -Candidate $faultId
    }
}

$version = [string]$inventory.source_version
foreach ($versionBinding in @(
    'README.md',
    'feishu-codex-bridge-skill.md',
    'upgrade-bridge.md',
    'skills/feishu-codex-bridge/SKILL.md',
    'scripts/install-feishu-codex-bridge.ps1',
    'scripts/bridge_core/config.py'
)) {
    $bindingText = $script:StrictUtf8.GetString(
        [byte[]]$firstBytesByComponent['desktop_bridge'][$versionBinding]
    )
    if ($bindingText -notmatch [regex]::Escape($version)) {
        Add-AuditFinding -RuleId 'SOURCE_VERSION_DRIFT' -Component 'desktop_bridge' -Path $versionBinding
    }
}

Assert-NoAuditFindings

$firstManifestText = Get-ManifestCanonicalText -Components $firstComponents.ToArray()
$firstPathSetText = Get-PathSetCanonicalText -Components $firstComponents.ToArray()

$secondComponents = New-Object System.Collections.Generic.List[object]
$secondExclusions = New-Object System.Collections.Generic.List[object]
foreach ($component in $selectedComponents) {
    $snapshot = New-ComponentSnapshot -Root $componentRoots[[string]$component.name] `
        -Component $component -Inventory $inventory
    $secondComponents.Add($snapshot.Component)
    foreach ($result in @($snapshot.ExclusionResults)) { $secondExclusions.Add($result) }
}
$secondManifestText = Get-ManifestCanonicalText -Components $secondComponents.ToArray()
$secondPathSetText = Get-PathSetCanonicalText -Components $secondComponents.ToArray()
if ($firstManifestText -cne $secondManifestText -or $firstPathSetText -cne $secondPathSetText -or
    (($firstExclusions | ConvertTo-Json -Depth 6 -Compress) -cne ($secondExclusions | ConvertTo-Json -Depth 6 -Compress))) {
    Add-AuditFinding -RuleId 'SOURCE_MUTATED_DURING_AUDIT' -Component 'desktop_bridge' -Path '<source-snapshot>'
}
$workspaceAgentsPresentSecond = Test-NoReparsePathChain -Path $workspaceAgents -Role 'workspace AGENTS mirror after audit'
$workspaceAgentsHashSecond = ''
if ($workspaceAgentsPresentSecond) {
    if (-not (Test-Path -LiteralPath $workspaceAgents -PathType Leaf)) {
        Add-AuditFinding -RuleId 'AGENTS_MIRROR_WRONG_TYPE' -Component 'desktop_bridge' -Path 'AGENTS.md'
    } else {
        $workspaceAgentsHashSecond = Get-BytesSha256 -Bytes (Read-BoundedFileBytes -Path $workspaceAgents `
            -MaximumBytes $script:ReleaseAuditMaxBytes)
        Assert-NoReparsePathChain -Path $workspaceAgents -Role 'workspace AGENTS mirror after second read'
    }
}
if ($workspaceAgentsPresentFirst -ne $workspaceAgentsPresentSecond -or
    $workspaceAgentsHashFirst -cne $workspaceAgentsHashSecond -or
    ($workspaceAgentsPresentSecond -and $workspaceAgentsHashSecond -cne $canonicalAgentsHash)) {
    Add-AuditFinding -RuleId 'AGENTS_MIRROR_MUTATED_DURING_AUDIT' -Component 'desktop_bridge' -Path 'AGENTS.md'
}
$inventoryReadHash = Get-BytesSha256 -Bytes $inventoryBytes
$firstInventoryRecord = Get-SnapshotFileRecord -Components $firstComponents.ToArray() `
    -ComponentName 'desktop_bridge' -RelativePath 'assets/release-inventory.json'
$secondInventoryRecord = Get-SnapshotFileRecord -Components $secondComponents.ToArray() `
    -ComponentName 'desktop_bridge' -RelativePath 'assets/release-inventory.json'
if ([string]$firstInventoryRecord.sha256 -cne $inventoryReadHash -or
    [string]$secondInventoryRecord.sha256 -cne $inventoryReadHash) {
    Add-AuditFinding -RuleId 'INVENTORY_SNAPSHOT_MISMATCH' -Component 'desktop_bridge' `
        -Path 'assets/release-inventory.json'
}
Assert-NoAuditFindings

$faultContractRecord = Get-SnapshotFileRecord -Components $firstComponents.ToArray() `
    -ComponentName 'desktop_bridge' -RelativePath 'references/release-audit.md'
$result = [pscustomobject][ordered]@{
    audit_schema_version = 1
    status = 'pass'
    source_version = $version
    source_manifest_sha256 = Get-StringSha256 -Text $firstManifestText
    audited_path_set_sha256 = Get-StringSha256 -Text $firstPathSetText
    inventory_sha256 = [string]$firstInventoryRecord.sha256
    fault_contract_sha256 = [string]$faultContractRecord.sha256
    components = $firstComponents.ToArray()
    exclusion_results = $firstExclusions.ToArray()
}
$json = $result | ConvertTo-Json -Depth 12

if ($OutputPath) {
    $safeOutput = Assert-OutputPathSafe -Path $OutputPath -Roots @($componentRoots.Values)
    $stream = $null
    $pendingOwned = $false
    try {
        Assert-NoReparsePathChain -Path (Split-Path -Parent $safeOutput.Pending) `
            -Role 'release audit output parent before publication'
        $stream = New-Object System.IO.FileStream(
            $safeOutput.Pending,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
        $pendingOwned = $true
        $writer = New-Object System.IO.StreamWriter($stream, $script:Utf8NoBom)
        $stream = $null
        try {
            $writer.Write($json)
            $writer.Write("`n")
            $writer.Flush()
            $writer.BaseStream.Flush()
        } finally {
            $writer.Dispose()
        }
        [System.IO.File]::Move($safeOutput.Pending, $safeOutput.Final)
        $pendingOwned = $false
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($pendingOwned -and (Test-Path -LiteralPath $safeOutput.Pending)) {
            Remove-Item -LiteralPath $safeOutput.Pending -Force
        }
    }
}

Write-Output $json
