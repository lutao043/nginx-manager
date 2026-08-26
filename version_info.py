# UTF-8
#
# For more details about fixed file info 'ffi' see:
# https://learn.microsoft.com/en-us/windows/win32/menurc/versioninfo-resource
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(0, 3, 0, 0),
    prodvers=(0, 3, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'080404b0',
          [
            StringStruct(u'CompanyName', u'nginx-manager'),
            StringStruct(u'FileDescription', u'nginx lightweight web manager'),
            StringStruct(u'FileVersion', u'0.3.0'),
            StringStruct(u'InternalName', u'nginx-manager'),
            StringStruct(u'OriginalFilename', u'nginx-manager.exe'),
            StringStruct(u'ProductName', u'nginx-manager'),
            StringStruct(u'ProductVersion', u'0.3.0'),
            StringStruct(u'Language', u'Chinese (Simplified)')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
