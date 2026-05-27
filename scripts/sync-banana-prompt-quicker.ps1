param(
  [string]$Ref = "main",
  [string]$OutputRoot = "web/public/banana-prompt-quicker",
  [string]$SourceRoot = "",
  [string]$SourceZip = "",
  [switch]$DownloadExternalImages,
  [switch]$SkipExternalImages,
  [switch]$CreateMissingPlaceholders,
  [int]$TimeoutSec = 120
)

$ErrorActionPreference = "Stop"

$repo = "glidea/banana-prompt-quicker"
$repoUrl = "https://github.com/$repo"
$cdnBase = "https://cdn.jsdelivr.net/gh/$repo@$Ref/"
$rawBase = "https://raw.githubusercontent.com/$repo/$Ref/"
$localUrlBase = "/banana-prompt-quicker/"
$externalFolder = "external"
$headers = @{
  "User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
  "Accept" = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
  "Referer" = $repoUrl
}

function Resolve-WorkspacePath {
  param([string]$PathValue)
  if ([string]::IsNullOrWhiteSpace($PathValue)) {
    return $null
  }
  if ([System.IO.Path]::IsPathRooted($PathValue)) {
    return [System.IO.Path]::GetFullPath($PathValue)
  }
  return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Initialize-Directory {
  param([string]$PathValue)
  if (-not (Test-Path -LiteralPath $PathValue)) {
    New-Item -ItemType Directory -Force -Path $PathValue | Out-Null
  }
}

function Invoke-DownloadText {
  param([string[]]$Urls)
  foreach ($url in $Urls) {
    try {
      return (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec -Headers $headers).Content
    } catch {
      Write-Warning "Failed to download $url"
    }
  }
  throw "Unable to download prompts.json from upstream."
}

function Convert-HexToText {
  param([string]$Hex)
  if ([string]::IsNullOrWhiteSpace($Hex) -or ($Hex.Length % 2) -ne 0) {
    return $null
  }

  $bytes = New-Object byte[] ($Hex.Length / 2)
  for ($i = 0; $i -lt $bytes.Length; $i++) {
    $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16)
  }
  return [System.Text.Encoding]::UTF8.GetString($bytes)
}

function Get-DownloadUrl {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $null
  }

  if (-not ($Value.StartsWith("http://") -or $Value.StartsWith("https://"))) {
    return $null
  }

  $uri = [Uri]$Value
  if ($uri.Host -eq "camo.githubusercontent.com") {
    $hex = ($uri.AbsolutePath.Trim("/") -split "/")[-1]
    $decoded = Convert-HexToText $hex
    if ($decoded -and ($decoded.StartsWith("http://") -or $decoded.StartsWith("https://"))) {
      return $decoded
    }
  }

  if ($uri.Host -eq "github.com" -and $uri.AbsolutePath -match "^/([^/]+/[^/]+)/blob/(.+)$") {
    $rawPath = $uri.AbsolutePath -replace "/blob/", "/raw/"
    return "https://github.com$rawPath$($uri.Query)"
  }

  return $Value
}

function Get-RepositoryRelativePath {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return $null
  }

  if ($Value.StartsWith($localUrlBase)) {
    return $Value.Substring($localUrlBase.Length)
  }

  if ($Value.StartsWith("http://") -or $Value.StartsWith("https://")) {
    $uri = [Uri]$Value
    $cdnPrefix = "/gh/$repo@$Ref/"
    $rawPrefix = "/$repo/$Ref/"
    if ($uri.Host -eq "cdn.jsdelivr.net" -and $uri.AbsolutePath.StartsWith($cdnPrefix)) {
      return [Uri]::UnescapeDataString($uri.AbsolutePath.Substring($cdnPrefix.Length))
    }
    if ($uri.Host -eq "raw.githubusercontent.com" -and $uri.AbsolutePath.StartsWith($rawPrefix)) {
      return [Uri]::UnescapeDataString($uri.AbsolutePath.Substring($rawPrefix.Length))
    }
    return $null
  }

  return $Value.TrimStart(".", "/", "\")
}

function Get-ExistingRepositoryAssetPath {
  param(
    [string]$RelativePath,
    [string]$SourceRootPath
  )

  if ([string]::IsNullOrWhiteSpace($RelativePath) -or [string]::IsNullOrWhiteSpace($SourceRootPath)) {
    return $null
  }

  $relative = $RelativePath -replace "/", "\"
  $candidate = Join-Path $SourceRootPath $relative
  if (Test-Path -LiteralPath $candidate -PathType Leaf) {
    return $candidate
  }

  $fileName = [System.IO.Path]::GetFileName($relative)
  $parent = Join-Path $SourceRootPath ([System.IO.Path]::GetDirectoryName($relative))
  if (Test-Path -LiteralPath $parent -PathType Container) {
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($fileName)
    $extension = [System.IO.Path]::GetExtension($fileName)
    $stemChars = [char[]]$stem
    [Array]::Sort($stemChars)
    $sortedStem = -join $stemChars
    $nearMatch = Get-ChildItem -LiteralPath $parent -File |
      Where-Object { $_.Extension -eq $extension -and ([System.Math]::Abs($_.BaseName.Length - $stem.Length) -le 1) } |
      Where-Object {
        $base = $_.BaseName
        $baseChars = [char[]]$base
        [Array]::Sort($baseChars)
        $sortedBase = -join $baseChars
        ($base -replace "[^a-zA-Z0-9]", "") -eq ($stem -replace "[^a-zA-Z0-9]", "") -or
          $base.StartsWith($stem.Substring(0, [System.Math]::Min(5, $stem.Length))) -or
          $sortedBase -eq $sortedStem
      } |
      Select-Object -First 1
    if ($nearMatch) {
      return $nearMatch.FullName
    }
  }

  return $null
}

function Get-SafePathSegment {
  param([string]$Value)
  $safe = $Value -replace "[^a-zA-Z0-9._-]", "-"
  $safe = $safe.Trim("-")
  if ([string]::IsNullOrWhiteSpace($safe)) {
    return "asset"
  }
  return $safe.ToLowerInvariant()
}

function Get-UrlHash {
  param([string]$Value)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $hash = $sha.ComputeHash($bytes)
    return -join ($hash[0..11] | ForEach-Object { $_.ToString("x2") })
  } finally {
    $sha.Dispose()
  }
}

function Get-ExternalExtension {
  param([string]$UrlValue)
  $downloadUrl = Get-DownloadUrl $UrlValue
  $uri = [Uri]$downloadUrl
  $extension = [System.IO.Path]::GetExtension($uri.AbsolutePath)
  if ([string]::IsNullOrWhiteSpace($extension)) {
    $query = [System.Web.HttpUtility]::ParseQueryString($uri.Query)
    $format = $query.Get("format")
    if ($format) {
      $extension = ".$format"
    }
  }
  if ([string]::IsNullOrWhiteSpace($extension) -or $extension.Length -gt 8) {
    $extension = ".jpg"
  }
  return ($extension -replace "[^a-zA-Z0-9.]", "").ToLowerInvariant()
}

function Get-ExternalRelativePath {
  param([string]$UrlValue)
  $downloadUrl = Get-DownloadUrl $UrlValue
  $uri = [Uri]$downloadUrl
  $hostSegment = Get-SafePathSegment $uri.Host
  $extension = Get-ExternalExtension $downloadUrl
  $hash = Get-UrlHash $downloadUrl
  return "$externalFolder/$hostSegment/$hash$extension"
}

function Copy-RepositoryAsset {
  param(
    [string]$RelativePath,
    [string]$SourceRootPath,
    [string]$OutputRootPath
  )

  $sourcePath = Get-ExistingRepositoryAssetPath $RelativePath $SourceRootPath
  if (-not $sourcePath) {
    return $false
  }

  $targetPath = Join-Path $OutputRootPath ($RelativePath -replace "/", "\")
  Initialize-Directory (Split-Path -Parent $targetPath)
  Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
  return $true
}

function Save-ExternalAsset {
  param(
    [string]$UrlValue,
    [string]$RelativePath,
    [string]$OutputRootPath
  )

  $targetPath = Join-Path $OutputRootPath ($RelativePath -replace "/", "\")
  if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
    $firstLine = Get-Content -Path $targetPath -Encoding UTF8 -TotalCount 1 -ErrorAction SilentlyContinue
    if (($firstLine -is [string]) -and $firstLine.TrimStart().StartsWith("<svg")) {
      return $false
    }
    return $true
  }

  if ($SkipExternalImages) {
    return $false
  }

  $downloadUrl = Get-DownloadUrl $UrlValue
  Initialize-Directory (Split-Path -Parent $targetPath)
  try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $targetPath -UseBasicParsing -TimeoutSec $TimeoutSec -Headers $headers
    return $true
  } catch {
    Write-Warning "Failed to download external asset: $downloadUrl"
    if (Test-Path -LiteralPath $targetPath) {
      Remove-Item -LiteralPath $targetPath -Force
    }
    return $false
  }
}

function Test-UsableAssetFile {
  param([string]$PathValue)
  if (-not (Test-Path -LiteralPath $PathValue -PathType Leaf)) {
    return $false
  }

  $extension = [System.IO.Path]::GetExtension($PathValue)
  if ($extension -eq ".svg") {
    return $true
  }

  $firstLine = Get-Content -Path $PathValue -Encoding UTF8 -TotalCount 1 -ErrorAction SilentlyContinue
  if (($firstLine -is [string]) -and $firstLine.TrimStart().StartsWith("<svg")) {
    return $false
  }

  return $true
}

function Get-PlaceholderLocalUrl {
  param([string]$LocalUrl)
  if ($LocalUrl.EndsWith(".svg")) {
    return $LocalUrl
  }
  return [System.Text.RegularExpressions.Regex]::Replace($LocalUrl, "\.[^./?]+$", ".svg")
}

function Update-ItemAssetUrl {
  param(
    [object]$Item,
    [string]$FromUrl,
    [string]$ToUrl
  )

  if ($Item.preview -and ([string]$Item.preview) -eq $FromUrl) {
    $Item.preview = $ToUrl
  }

  if ($Item.reference_image_urls) {
    $nextReferences = @()
    foreach ($url in $Item.reference_image_urls) {
      if (([string]$url) -eq $FromUrl) {
        $nextReferences += $ToUrl
      } else {
        $nextReferences += $url
      }
    }
    $Item.reference_image_urls = $nextReferences
  }
}

function New-MissingAssetPlaceholder {
  param(
    [string]$TargetPath,
    [string]$Title,
    [string]$UrlValue
  )

  Initialize-Directory (Split-Path -Parent $TargetPath)
  $safeTitle = [System.Security.SecurityElement]::Escape($Title)
  $safeUrl = [System.Security.SecurityElement]::Escape($UrlValue)
  $svg = @"
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900" role="img" aria-label="示例图暂未离线保存">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#f8fafc"/>
      <stop offset="1" stop-color="#e7e5e4"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="900" fill="url(#bg)"/>
  <rect x="120" y="120" width="960" height="660" rx="28" fill="#ffffff" stroke="#d6d3d1" stroke-width="3"/>
  <circle cx="600" cy="334" r="92" fill="#f5f5f4" stroke="#d6d3d1" stroke-width="3"/>
  <path d="M552 348l35 35 68-86" fill="none" stroke="#78716c" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="600" y="500" text-anchor="middle" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="42" font-weight="700" fill="#292524">示例图暂未离线保存</text>
  <text x="600" y="566" text-anchor="middle" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="28" fill="#57534e">$safeTitle</text>
  <text x="600" y="636" text-anchor="middle" font-family="Arial, 'Microsoft YaHei', sans-serif" font-size="20" fill="#78716c">源站拒绝或超时，已保留原始地址用于后续同步</text>
  <text x="600" y="690" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#a8a29e">$safeUrl</text>
</svg>
"@
  Set-Content -Path $TargetPath -Value $svg -Encoding UTF8
}

function Convert-ToLocalAsset {
  param(
    [string]$Value,
    [string]$SourceRootPath,
    [string]$OutputRootPath
  )

  $repoRelative = Get-RepositoryRelativePath $Value
  if ($repoRelative) {
    if ($SourceRootPath) {
      [void](Copy-RepositoryAsset $repoRelative $SourceRootPath $OutputRootPath)
    }
    return "$localUrlBase$($repoRelative -replace "\\", "/")"
  }

  if ($Value.StartsWith("http://") -or $Value.StartsWith("https://")) {
    $externalRelative = Get-ExternalRelativePath $Value
    [void](Save-ExternalAsset $Value $externalRelative $OutputRootPath)
    return "$localUrlBase$externalRelative"
  }

  return $Value
}

function Get-PromptsPayload {
  param([string]$SourceRootPath)
  if ($SourceRootPath) {
    $promptsPath = Join-Path $SourceRootPath "prompts.json"
    if (Test-Path -LiteralPath $promptsPath -PathType Leaf) {
      return Get-Content -Path $promptsPath -Encoding UTF8 -Raw
    }
  }
  return Invoke-DownloadText @("$($cdnBase)prompts.json", "$($rawBase)prompts.json")
}

$outputRootPath = Resolve-WorkspacePath $OutputRoot
Initialize-Directory $outputRootPath

$sourceRootPath = Resolve-WorkspacePath $SourceRoot
if (-not $sourceRootPath -and $SourceZip) {
  $sourceZipPath = Resolve-WorkspacePath $SourceZip
  if (-not (Test-Path -LiteralPath $sourceZipPath -PathType Leaf)) {
    throw "SourceZip not found: $SourceZip"
  }
  $extractRoot = Resolve-WorkspacePath ".tmp/banana-prompt-quicker-sync"
  Initialize-Directory $extractRoot
  tar -xf $sourceZipPath -C $extractRoot
  $sourceRootPath = (Get-ChildItem -LiteralPath $extractRoot -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "prompts.json") } | Select-Object -First 1).FullName
}

if (-not $sourceRootPath) {
  $defaultSourceRoot = Resolve-WorkspacePath ".tmp/banana-prompt-quicker-main"
  if (Test-Path -LiteralPath (Join-Path $defaultSourceRoot "prompts.json") -PathType Leaf) {
    $sourceRootPath = $defaultSourceRoot
  }
}

if ($sourceRootPath -and -not (Test-Path -LiteralPath (Join-Path $sourceRootPath "prompts.json") -PathType Leaf)) {
  throw "SourceRoot does not contain prompts.json: $sourceRootPath"
}

if ($sourceRootPath) {
  $sourceImagesPath = Join-Path $sourceRootPath "images"
  if (Test-Path -LiteralPath $sourceImagesPath -PathType Container) {
    $targetImagesPath = Join-Path $outputRootPath "images"
    Initialize-Directory $targetImagesPath
    Copy-Item -Path (Join-Path $sourceImagesPath "*") -Destination $targetImagesPath -Recurse -Force
  }
}

$jsonText = Get-PromptsPayload $sourceRootPath
$payload = $jsonText | ConvertFrom-Json
$items = if ($payload -is [array]) { $payload } elseif ($payload.prompts) { $payload.prompts } else { @() }

foreach ($item in $items) {
  if ($item.preview) {
    $item.preview = Convert-ToLocalAsset ([string]$item.preview) $sourceRootPath $outputRootPath
  }
  if ($item.reference_image_urls) {
    $nextReferences = @()
    foreach ($url in $item.reference_image_urls) {
      $nextReferences += Convert-ToLocalAsset ([string]$url) $sourceRootPath $outputRootPath
    }
    $item.reference_image_urls = $nextReferences
  }
}

$missingAssetRecords = @()
$localRefs = New-Object System.Collections.Generic.List[string]
foreach ($item in $items) {
  if ($item.preview -and ([string]$item.preview).StartsWith($localUrlBase)) {
    $localRefs.Add([string]$item.preview)
  }
  if ($item.reference_image_urls) {
    foreach ($url in $item.reference_image_urls) {
      if ($url -and ([string]$url).StartsWith($localUrlBase)) {
        $localRefs.Add([string]$url)
      }
    }
  }
}

$missingBeforePlaceholders = @()
foreach ($url in ($localRefs | Sort-Object -Unique)) {
  $relative = ([string]$url).Substring($localUrlBase.Length) -replace "/", "\"
  $assetPath = Join-Path $outputRootPath $relative
  if (-not (Test-UsableAssetFile $assetPath)) {
    $missingBeforePlaceholders += $url
  }
}

if ($CreateMissingPlaceholders -and $missingBeforePlaceholders.Count -gt 0) {
  foreach ($missingUrl in $missingBeforePlaceholders) {
    $titles = @()
    foreach ($item in $items) {
      $refs = @()
      if ($item.preview) {
        $refs += [string]$item.preview
      }
      if ($item.reference_image_urls) {
        $refs += @($item.reference_image_urls | ForEach-Object { [string]$_ })
      }
      if ($refs -contains $missingUrl) {
        $titles += [string]$item.title
      }
    }
    $placeholderUrl = Get-PlaceholderLocalUrl $missingUrl
    foreach ($item in $items) {
      Update-ItemAssetUrl $item $missingUrl $placeholderUrl
    }
    $originalRelative = ([string]$missingUrl).Substring($localUrlBase.Length) -replace "/", "\"
    $originalTargetPath = Join-Path $outputRootPath $originalRelative
    if (Test-Path -LiteralPath $originalTargetPath -PathType Leaf) {
      $firstLine = Get-Content -Path $originalTargetPath -Encoding UTF8 -TotalCount 1 -ErrorAction SilentlyContinue
      if (($firstLine -is [string]) -and $firstLine.TrimStart().StartsWith("<svg")) {
        Remove-Item -LiteralPath $originalTargetPath -Force
      }
    }
    $relative = ([string]$placeholderUrl).Substring($localUrlBase.Length) -replace "/", "\"
    $targetPath = Join-Path $outputRootPath $relative
    New-MissingAssetPlaceholder $targetPath ($titles -join " / ") $missingUrl
    $missingAssetRecords += [ordered]@{
      local_url = $placeholderUrl
      original_local_url = $missingUrl
      titles = $titles
      reason = "Download failed or source blocked during sync."
    }
  }
}

$localRefs.Clear()
foreach ($item in $items) {
  if ($item.preview -and ([string]$item.preview).StartsWith($localUrlBase)) {
    $localRefs.Add([string]$item.preview)
  }
  if ($item.reference_image_urls) {
    foreach ($url in $item.reference_image_urls) {
      if ($url -and ([string]$url).StartsWith($localUrlBase)) {
        $localRefs.Add([string]$url)
      }
    }
  }
}

$snapshot = [ordered]@{
  source = $repoUrl
  ref = $Ref
  synced_at = (Get-Date).ToString("o")
  prompt_count = @($items).Count
  missing_asset_count = @($missingAssetRecords).Count
  missing_assets = $missingAssetRecords
  prompts = $items
}

$snapshotPath = Join-Path $outputRootPath "prompts.json"
$snapshot | ConvertTo-Json -Depth 40 | Set-Content -Path $snapshotPath -Encoding UTF8

$missing = @()
foreach ($url in ($localRefs | Sort-Object -Unique)) {
  $relative = ([string]$url).Substring($localUrlBase.Length) -replace "/", "\"
  $assetPath = Join-Path $outputRootPath $relative
  if (-not (Test-UsableAssetFile $assetPath)) {
    $missing += $url
  }
}

Write-Host "Wrote $snapshotPath"
Write-Host "Prompt count: $(@($items).Count)"
Write-Host "Local asset refs: $(@($localRefs | Sort-Object -Unique).Count)"
Write-Host "Missing local asset refs: $(@($missing).Count)"
if ($missing.Count -gt 0) {
  $missing | ForEach-Object { Write-Warning "Missing $_" }
  exit 2
}
