"""Start VoiceCastTTS v2 and open Chrome in a dedicated v2 profile after binding its port."""
import os
from pathlib import Path
import subprocess
import sys
import session_access

ROOT = Path(__file__).resolve().parent

def find_chrome():
    candidates = (
        Path(os.environ.get('PROGRAMFILES', r'C:\Program Files')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Google/Chrome/Application/chrome.exe',
    )
    return next((path for path in candidates if path.is_file()), None)

def open_v2_chrome(chrome, port):
    profile = ROOT / '.superpowers' / 'chrome-app-profile'
    if not profile.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError('Chrome profile must remain in VoiceCastTTS v2 folder.')
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([str(chrome), f'--user-data-dir={profile}', '--no-first-run',
                      '--no-default-browser-check', '--new-window', session_access.launch_url(port)])

def main():
    if Path(sys.prefix).resolve() != ROOT / '.venv' or sys.prefix == sys.base_prefix:
        print('VoiceCastTTS v2 requires its own .venv. Run Install_Dependencies.bat, then use the v2 launcher.')
        return 1
    expected_runtime = ROOT / '.runtime' / 'python-3.14.10' / 'tools'
    if Path(sys.base_prefix).resolve() != expected_runtime.resolve() or sys.version_info[:3] < (3, 14, 10):
        print('VoiceCastTTS v2 needs its approved private Python runtime. Rebuild the v2 environment before launching.')
        return 1
    import web_server
    chrome = find_chrome()
    if chrome is None:
        print('Chrome was not found. Install Chrome or open VoiceCastTTS v2 address manually.')
        return 1
    # Use the actual bound port, including a fallback when 7871 is occupied.
    os.environ.pop('VOICECAST_NO_BROWSER', None)
    web_server.open_browser = lambda port: open_v2_chrome(chrome, port)
    web_server.run_server()
    return 0

if __name__ == '__main__':
    sys.exit(main())
