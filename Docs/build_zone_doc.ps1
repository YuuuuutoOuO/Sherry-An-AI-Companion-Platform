$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here 'zone_prefix_caching_content.txt'
$out = Join-Path $here 'Zone_1_Zone_2_Prefix_Caching.docx'
$lines = [System.IO.File]::ReadAllLines($src, [System.Text.Encoding]::UTF8)

function Esc($s) { [System.Security.SecurityElement]::Escape($s) }
function Run($t, $sz, $bold, $color, $italic) {
    $rpr = '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft JhengHei"/>'
    if ($bold) { $rpr += '<w:b/>' }
    if ($italic) { $rpr += '<w:i/>' }
    $rpr += "<w:color w:val=`"$color`"/><w:sz w:val=`"$sz`"/><w:szCs w:val=`"$sz`"/>"
    "<w:r><w:rPr>$rpr</w:rPr><w:t xml:space=`"preserve`">$(Esc $t)</w:t></w:r>"
}
function Para($t, $sz, $bold, $color, $italic, $jc, $before, $after, $extra) {
    $ppr = "<w:spacing w:before=`"$before`" w:after=`"$after`"/><w:jc w:val=`"$jc`"/>"
    "<w:p><w:pPr>$extra$ppr</w:pPr>$(Run $t $sz $bold $color $italic)</w:p>"
}

$navy = '1F3A5B'
$body = New-Object System.Text.StringBuilder
foreach ($l in $lines) {
    if ($l.Trim() -eq '') { continue }
    $i = $l.IndexOf(':')
    $tag = $l.Substring(0, $i)
    $t = $l.Substring($i + 1)
    $x = switch ($tag) {
        'TITLE' { Para $t 38 $true $navy $false 'center' 0 80 '' }
        'SUB'   { Para $t 22 $false '808080' $false 'center' 0 160 '' }
        'H1'    { Para $t 26 $true $navy $false 'left' 240 80 '<w:keepNext/><w:pBdr><w:bottom w:val="single" w:sz="8" w:space="1" w:color="1F3A5B"/></w:pBdr>' }
        'H2'    { Para $t 22 $true '000000' $false 'left' 120 40 '<w:keepNext/>' }
        'P'     { Para $t 21 $false '000000' $false 'left' 0 80 '' }
        'B'     { Para $t 21 $false '000000' $false 'left' 0 40 '<w:ind w:left="360" w:hanging="260"/>' }
        'IMG'   { Para $t 20 $false 'C00000' $true 'center' 120 120 '<w:pBdr><w:top w:val="dashed" w:sz="6" w:space="6" w:color="999999"/><w:left w:val="dashed" w:sz="6" w:space="6" w:color="999999"/><w:bottom w:val="dashed" w:sz="6" w:space="6" w:color="999999"/><w:right w:val="dashed" w:sz="6" w:space="6" w:color="999999"/></w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/><w:spacing w:before="120" w:after="120" w:line="1500" w:lineRule="atLeast"/>' }
    }
    if ($tag -eq 'B') { $x = $x.Replace('<w:t xml:space="preserve">', '<w:t xml:space="preserve">' + [char]0x2022 + '  ') }
    [void]$body.Append($x)
}

$ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
$docXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document ' + $ns + '><w:body>' + $body.ToString() +
  '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1000" w:right="1100" w:bottom="1000" w:left="1100" w:header="500" w:footer="500" w:gutter="0"/></w:sectPr></w:body></w:document>'
$ct = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
$rels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'

if (Test-Path $out) { Remove-Item $out }
$zip = [System.IO.Compression.ZipFile]::Open($out, 'Create')
$enc = New-Object System.Text.UTF8Encoding($false)
foreach ($e in @(@('[Content_Types].xml', $ct), @('_rels/.rels', $rels), @('word/document.xml', $docXml))) {
    $entry = $zip.CreateEntry($e[0])
    $s = $entry.Open(); $b = $enc.GetBytes($e[1]); $s.Write($b, 0, $b.Length); $s.Close()
}
$zip.Dispose()
Write-Output "Created $out ($((Get-Item $out).Length) bytes)"
