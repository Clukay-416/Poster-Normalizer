"""Build a complete application + offline AI prerequisites archive in GitHub-sized parts."""
import hashlib
import io
import json
import zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'dist-full';OUT.mkdir(exist_ok=True)
VERSION='0.4.0-rc1'
PREFIX='PosterNormalizer_V'+VERSION+'/'
name='PosterNormalizer_V'+VERSION+'_Windows_Full.zip'
class SplitWriter(io.RawIOBase):
    """Stream ZIP64 directly into parts; never keep a second 9 GB archive."""
    def __init__(self):
        super().__init__();self.parts=[];self.totalhash=hashlib.sha256();self.position=0;self.stream=None
    def writable(self):return True
    def tell(self):return self.position
    def write(self,data):
        original=len(data);view=memoryview(data)
        while view:
            if self.stream is None:
                self.path=OUT/(name+'.'+str(len(self.parts)+1).zfill(3))
                self.stream=self.path.open('wb');self.size=0;self.digest=hashlib.sha256()
            chunk=view[:900*1024*1024-self.size]
            self.stream.write(chunk);self.digest.update(chunk);self.totalhash.update(chunk)
            self.size+=len(chunk);self.position+=len(chunk);view=view[len(chunk):]
            if self.size==900*1024*1024:self.finish_part()
        return original
    def finish_part(self):
        if self.stream is not None:
            self.stream.close();self.stream=None
            self.parts.append({'name':self.path.name,'size':self.size,'sha256':self.digest.hexdigest()})
sink=SplitWriter()
app_zip=ROOT/'dist'/f'PosterNormalizer_V{VERSION}_Windows_Offline.zip'
with zipfile.ZipFile(app_zip) as source,zipfile.ZipFile(sink,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as dest:
    for entry in source.infolist():
        with source.open(entry) as src,dest.open(entry.filename,'w',force_zip64=True) as out:
            while chunk:=src.read(8*1024*1024):out.write(chunk)
    for profile in ('modern','powerpaint','anytext'):
        folder=ROOT/'offline-ai'/profile
        manifest=json.loads((folder/'manifest.json').read_text())
        if manifest['profile']!=profile or not manifest['import_test_passed']:raise ValueError('Invalid AI pack')
        for entry in manifest['parts']:
            path=folder/entry['name'];h=hashlib.sha256()
            with path.open('rb') as f:
                for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
            if path.stat().st_size!=entry['size'] or h.hexdigest()!=entry['sha256']:raise ValueError('AI part hash mismatch')
            dest.write(path,PREFIX+f'offline-ai/{profile}/'+path.name,compress_type=zipfile.ZIP_STORED)
        for file in ('manifest.json','requirements.lock'):
            dest.write(folder/file,PREFIX+f'offline-ai/{profile}/'+file)
sink.finish_part()
parts=sink.parts;totalhash=sink.totalhash
(OUT/'full-manifest.json').write_text(json.dumps({'archive':name,'sha256':totalhash.hexdigest(),'parts':parts},indent=2))
# Standard CMD/PowerShell only: no external archive utility or download at install time.
script='''$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$manifest=Get-Content -LiteralPath 'full-manifest.json' -Raw | ConvertFrom-Json
$output=[IO.File]::Create((Join-Path $PSScriptRoot $manifest.archive))
try {
 foreach($part in $manifest.parts) {
  Write-Host ('Verifying and joining '+$part.name)
  if((Get-FileHash -LiteralPath $part.name -Algorithm SHA256).Hash.ToLowerInvariant() -ne $part.sha256) { throw ('Damaged part: '+$part.name) }
  $partStream=[IO.File]::OpenRead((Join-Path $PSScriptRoot $part.name))
  try { $partStream.CopyTo($output) } finally { $partStream.Dispose() }
 }
} finally { $output.Dispose() }
if((Get-FileHash -LiteralPath $manifest.archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw 'Archive checksum mismatch' }
Write-Host 'Extracting complete package. This can take several minutes.'
Expand-Archive -LiteralPath $manifest.archive -DestinationPath $PSScriptRoot
Write-Host 'Done. Open the extracted folder and double-click start.cmd.'
'''
(OUT/'Extract-Full-Package.ps1').write_text(script,encoding='utf-8-sig')
(OUT/'Extract-Full-Package.cmd').write_bytes(b'@echo off\r\ncd /d "%~dp0"\r\npowershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Extract-Full-Package.ps1"\r\npause\r\n')
print(json.dumps({'parts':len(parts),'compressed_bytes':sum(p['size'] for p in parts),'sha256':totalhash.hexdigest()}))
