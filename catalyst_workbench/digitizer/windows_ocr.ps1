param([string]$ImagePath, [switch]$Probe)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $ocrType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
    if ($Probe) {
        @{available=$true; languages=@($ocrType::AvailableRecognizerLanguages | ForEach-Object {$_.LanguageTag}); max_dimension=$ocrType::MaxImageDimension} | ConvertTo-Json -Compress
        exit 0
    }
    $fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
    $accessType = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
    $streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
    $decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
    $resultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
    $languageType = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
    $awaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetGenericArguments().Count -eq 1} | Select-Object -First 1
    function Await-OcrOperation($Operation, $ResultType) {
        $task = $awaitMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
        $task.Wait()
        return $task.Result
    }
    $imageFile = Await-OcrOperation ($fileType::GetFileFromPathAsync($ImagePath)) $fileType
    $imageStream = Await-OcrOperation ($imageFile.OpenAsync($accessType::Read)) $streamType
    $decoder = Await-OcrOperation ($decoderType::CreateAsync($imageStream)) $decoderType
    $bitmap = Await-OcrOperation ($decoder.GetSoftwareBitmapAsync()) $bitmapType
    $english = $ocrType::TryCreateFromLanguage($languageType::new('en-US'))
    $engine = if ($english) {$english} else {$ocrType::TryCreateFromUserProfileLanguages()}
    if (-not $engine) {throw 'No installed OCR recognizer language is available.'}
    $recognized = Await-OcrOperation ($engine.RecognizeAsync($bitmap)) $resultType
    $lines = @($recognized.Lines | ForEach-Object {
        $words = @($_.Words | ForEach-Object {
            $rect = $_.BoundingRect
            @{text=$_.Text; bbox=@($rect.X,$rect.Y,($rect.X+$rect.Width),($rect.Y+$rect.Height))}
        })
        @{text=$_.Text; words=$words}
    })
    @{available=$true; text=$recognized.Text; language=$engine.RecognizerLanguage.LanguageTag; lines=$lines} | ConvertTo-Json -Depth 8 -Compress
    $bitmap.Dispose()
    $imageStream.Dispose()
} catch {
    @{available=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
