import os
import PyInstaller.__main__

script_path = 'app.py'

static_folder = 'static'
templates_folder = 'templates'

icon_path = 'static/logo.ico'

PyInstaller.__main__.run([
    '--onefile',
    '--noconsole',
    '--add-data', f'{static_folder}{os.pathsep}static',
    '--add-data', f'{templates_folder}{os.pathsep}templates',
    '--icon', icon_path,
    script_path
])
