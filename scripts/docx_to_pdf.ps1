# 用本机 Word 把 docx 转成 PDF，用于导出验收时人工看 Word 里的真实排版（仅 Windows + 已安装 Word）。
# 用法：powershell -File scripts/docx_to_pdf.ps1 <输入.docx> [<输出.pdf>]
param([Parameter(Mandatory = $true)][string]$In, [string]$Out = "")
$in = (Resolve-Path $In).Path
if (-not $Out) { $Out = [System.IO.Path]::ChangeExtension($in, ".pdf") }
$Out = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $Out))
$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {
    $doc = $word.Documents.Open($in, $false, $true)
    $doc.ExportAsFixedFormat($Out, 17)  # 17 = wdExportFormatPDF
    $doc.Close($false)
    Write-Output "ok: $Out"
} finally {
    $word.Quit()
}
