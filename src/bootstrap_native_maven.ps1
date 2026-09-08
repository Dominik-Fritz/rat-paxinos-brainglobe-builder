$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runtime = Join-Path $Root "data\native_abba_runtime"
$MavenRoot = Join-Path $Runtime "maven"
$Version = "3.9.9"
$MavenHome = Join-Path $MavenRoot "apache-maven-$Version"
$Marker = Join-Path $MavenRoot "runtime-manifest.json"
$BaseUrl = "https://archive.apache.org/dist/maven/maven-3/$Version/binaries/apache-maven-$Version-bin.zip"
$Archive = Join-Path $Runtime "downloads\apache-maven-$Version-bin.zip"
$ChecksumFile = "$Archive.sha512"
$JavaHome = Join-Path $Runtime "java"
$JavaExe = Join-Path $JavaHome "bin\java.exe"

if (-not (Test-Path $JavaExe)) {
    throw "MAVEN_JAVA_MISSING: builder-local Java must be bootstrapped before Maven: $JavaExe"
}
$env:JAVA_HOME = $JavaHome
$env:Path = "$(Join-Path $JavaHome 'bin');$env:Path"

if (Test-Path $Marker) {
    $Existing = Get-Content $Marker -Raw | ConvertFrom-Json
    if ($Existing.pinned_version -eq $Version -and (Test-Path (Join-Path $MavenHome "bin\mvn.cmd"))) {
        Write-Host "Builder-local Apache Maven $Version is ready."
        & (Join-Path $MavenHome "bin\mvn.cmd") --version
        exit $LASTEXITCODE
    }
    throw "MAVEN_CACHE_CORRUPT: runtime marker/version does not match $Version"
}

New-Item -ItemType Directory -Force -Path $MavenRoot, (Join-Path $Runtime "downloads") | Out-Null
try {
    Write-Host "Downloading Apache Maven $Version..."
    Invoke-WebRequest -Uri $BaseUrl -OutFile $Archive -UseBasicParsing -TimeoutSec 900
    Invoke-WebRequest -Uri "$BaseUrl.sha512" -OutFile $ChecksumFile -UseBasicParsing -TimeoutSec 120
} catch {
    throw "MAVEN_NETWORK: Maven download failed: $($_.Exception.Message)"
}
$Expected = ((Get-Content $ChecksumFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$Observed = (Get-FileHash -Algorithm SHA512 $Archive).Hash.ToLowerInvariant()
if ($Expected -notmatch '^[0-9a-f]{128}$') {
    throw "MAVEN_METADATA: publisher SHA-512 is malformed"
}
if ($Observed -ne $Expected) {
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    throw "MAVEN_HASH: expected $Expected, got $Observed"
}
$Stage = Join-Path $Runtime "maven.partial"
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Stage | Out-Null
try { Expand-Archive -Path $Archive -DestinationPath $Stage -Force }
catch { throw "MAVEN_CACHE_CORRUPT: Maven archive cannot be extracted: $($_.Exception.Message)" }
$Extracted = Join-Path $Stage "apache-maven-$Version"
if (-not (Test-Path (Join-Path $Extracted "bin\mvn.cmd"))) {
    throw "MAVEN_COMPONENT_MISSING: extracted archive has no bin\mvn.cmd"
}
Remove-Item $MavenHome -Recurse -Force -ErrorAction SilentlyContinue
Move-Item $Extracted $MavenHome
Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
$ArchiveSha256 = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
@{
    pinned_version = $Version
    distribution = "Apache Maven"
    download_url = $BaseUrl
    archive_sha512 = $Observed
    archive_sha256 = $ArchiveSha256
    installed_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -Encoding UTF8 $Marker
& (Join-Path $MavenHome "bin\mvn.cmd") --version
if ($LASTEXITCODE -ne 0) { throw "MAVEN_CACHE_CORRUPT: mvn --version failed with $LASTEXITCODE" }
