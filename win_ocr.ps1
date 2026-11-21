param([string]$Dir, [string]$OutJson, [string]$Language = 'zh-Hans-CN')
# 用 Windows 内置 OCR 识别目录下所有 PNG，输出带坐标的 JSON。
# 调用方：scan_pdf.py。失败时以非零退出码结束，由调用方降级处理。
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

$lang = New-Object Windows.Globalization.Language $Language
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) {
    Write-Error 'no OCR engine available'
    exit 2
}

$pages = @()
Get-ChildItem -Path $Dir -Filter '*.png' | Sort-Object Name | ForEach-Object {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($line in $result.Lines) {
        $minX = [double]::MaxValue; $minY = [double]::MaxValue; $maxX = 0.0; $maxY = 0.0
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            if ($r.X -lt $minX) { $minX = $r.X }
            if ($r.Y -lt $minY) { $minY = $r.Y }
            if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
            if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
        }
        if ($line.Words.Count -eq 0) { continue }
        $lines += [pscustomobject]@{
            text = $line.Text
            x    = [math]::Round($minX, 1)
            y    = [math]::Round($minY, 1)
            w    = [math]::Round(($maxX - $minX), 1)
            h    = [math]::Round(($maxY - $minY), 1)
        }
    }
    $pages += [pscustomobject]@{
        name   = $_.Name
        width  = $bitmap.PixelWidth
        height = $bitmap.PixelHeight
        lines  = $lines
    }
    $stream.Dispose()
}

$json = ConvertTo-Json -InputObject @{ pages = $pages } -Depth 6 -Compress
[System.IO.File]::WriteAllText($OutJson, $json, (New-Object System.Text.UTF8Encoding $false))
