"""Scrive version_info.txt per PyInstaller: nome, versione e autore dentro l'exe (Proprietà → Dettagli).
Un exe con queste informazioni viene preso meno spesso per un virus dagli antivirus."""
import re
v = re.search(r"^VERSION = '(.+?)'", open('splitwatch.py', encoding='utf-8').read(), re.M).group(1)
n = [int(x) for x in re.findall(r'\d+', v)][:4]
n += [0] * (4 - len(n))
t = tuple(n)
open('version_info.txt', 'w', encoding='utf-8').write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041004B0', [
      StringStruct('CompanyName', 'IW1FZR Amateur Radio Tools'),
      StringStruct('FileDescription', 'FZR SDR - ricevitore SDR e skimmer CW/RTTY'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'FZRSDR'),
      StringStruct('LegalCopyright', 'IW1FZR Luca'),
      StringStruct('OriginalFilename', 'FZRSDR.exe'),
      StringStruct('ProductName', 'FZR SDR'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x0410, 1200])])
  ]
)
""")
print('version_info.txt', v, t)
