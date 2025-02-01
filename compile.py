import os
import PyInstaller.__main__

script_path = 'app.py'

icon_path = 'static/logo.ico'

PyInstaller.__main__.run([
    '--onefile',
    '--noconsole',
    '--add-data', f'static{os.pathsep}static',
    '--add-data', f'templates{os.pathsep}templates',
    '--icon', icon_path,
    script_path
])
