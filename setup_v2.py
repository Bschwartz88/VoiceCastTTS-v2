"""Reproducible Windows source setup using only Python's standard library.
A preinstalled 64-bit Python 3.10+ bootstraps a separate pinned runtime here.
"""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
RUNTIME_VERSION = '3.14.10'
RUNTIME_URL = 'https://api.nuget.org/v3-flatcontainer/python/3.14.10/python.3.14.10.nupkg'
RUNTIME_SHA512 = 'fZln2RPYmfZLoVEYVNaczQCatkuZyLHSuhhMhjzmgtHSFP+mlChqyVpVmjr5Wi2o6ZQ3WwK+B0vefqz9kCpTcQ=='
MAX_RUNTIME_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.digest()


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('Setup target must remain inside the project folder.')
    return path


def verify_runtime_archive(path):
    if Path(path).stat().st_size > MAX_RUNTIME_BYTES:
        raise ValueError('Runtime archive exceeds its size limit.')
    if base64.b64encode(digest(path, 'sha512')).decode() != RUNTIME_SHA512:
        raise ValueError('Runtime archive checksum mismatch; nothing will be executed.')


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if urllib.parse.urlsplit(url).scheme != 'https':
            raise ValueError('Setup refuses an insecure download redirect.')
        return super().redirect_request(request, fp, code, message, headers, url)


def download(url, destination, maximum):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('Setup downloads require HTTPS.')
    destination = inside(ROOT, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = inside(ROOT, destination.with_suffix(destination.suffix + '.part'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HTTPSOnly())
    try:
        with opener.open(url, timeout=30) as response, temporary.open('wb') as output:
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise ValueError('Download exceeded its size limit.')
                output.write(block)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def extract_runtime(archive, destination):
    destination = inside(ROOT, destination)
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > MAX_EXTRACTED_BYTES:
            raise ValueError('Expanded runtime exceeds its size limit.')
        for item in package.infolist():
            inside(destination, destination / item.filename)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Runtime archive must not contain symbolic links.')
        package.extractall(destination)


def verify_signature(executable):
    # Fixed command, literal-path escaping; no shell and no execution-policy changes.
    literal = str(executable).replace("'", "''")
    command = (f"$s=Get-AuthenticodeSignature -LiteralPath '{literal}'; "
               "if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Python Software Foundation(,|$)'){exit 1}")
    environment = os.environ.copy()
    # PowerShell 7 can export a module path incompatible with Windows PowerShell 5.1.
    environment['PSModulePath'] = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'Modules')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                   check=True, env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def prepare_runtime(archive_override=None):
    final = inside(ROOT, ROOT / '.runtime' / ('python-' + RUNTIME_VERSION))
    executable = final / 'tools' / 'python.exe'
    if executable.is_file():
        verify_signature(executable)
        return executable
    if final.exists():
        raise ValueError('Incomplete private runtime found. Preserve it and investigate before retrying.')
    parent = inside(ROOT, ROOT / '.runtime')
    parent.mkdir(exist_ok=True)
    archive = Path(archive_override).resolve() if archive_override else parent / 'python-runtime.nupkg'
    if archive_override is None and not archive.exists():
        print('Downloading the pinned Python runtime into this folder...', flush=True)
        download(RUNTIME_URL, archive, MAX_RUNTIME_BYTES)
    verify_runtime_archive(archive)
    with tempfile.TemporaryDirectory(prefix='extract-', dir=parent) as staging:
        staging = inside(ROOT, staging)
        extract_runtime(archive, staging)
        verify_signature(staging / 'tools' / 'python.exe')
        shutil.move(str(staging), str(final))
    return executable


def model_manifest():
    # Read only literal integrity constants; do not import the app before dependencies exist.
    module = ast.parse((ROOT / 'voice_cast.py').read_text(encoding='utf-8'))
    values = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('MODEL_FILES', 'MODEL_RELEASE_URL'):
                    values[target.id] = ast.literal_eval(node.value)
    files = values['MODEL_FILES']
    if set(files) != {'kokoro-v1.0.onnx', 'voices-v1.0.bin'}:
        raise ValueError('Unexpected model manifest.')
    return values['MODEL_RELEASE_URL'], files


def prepare_models(cache=None):
    base, files = model_manifest()
    for name, expected in files.items():
        destination = inside(ROOT, ROOT / name)
        if destination.exists():
            if digest(destination).hex() != expected:
                raise ValueError('Existing model checksum mismatch: ' + name)
            continue
        source = Path(cache) / name if cache else None
        if source is not None:
            if digest(source).hex() != expected:
                raise ValueError('Cached model checksum mismatch: ' + name)
            shutil.copyfile(source, destination)
        else:
            print('Downloading and verifying ' + name + '...', flush=True)
            download(base + '/' + name, destination, 512 * 1024 * 1024)
        if digest(destination).hex() != expected:
            destination.unlink()
            raise ValueError('Downloaded model checksum mismatch: ' + name)


def prepare_environment(runtime, wheelhouse=None):
    python = inside(ROOT, ROOT / '.venv' / 'Scripts' / 'python.exe')
    marker = ROOT / '.venv' / 'installed.ok'
    if marker.exists():
        marker.unlink()
    if not python.is_file():
        subprocess.run([str(runtime), '-I', '-m', 'venv', str(ROOT / '.venv')], check=True)
    validation = ('import sys,pathlib,json; '
                  'print(json.dumps({"prefix":str(pathlib.Path(sys.prefix).resolve()),'
                  '"base":str(pathlib.Path(sys.base_prefix).resolve()),"version":list(sys.version_info[:3])}))')
    data = json.loads(subprocess.check_output([str(python), '-I', '-c', validation], text=True))
    if (Path(data['prefix']) != (ROOT / '.venv').resolve() or
        Path(data['base']) != runtime.parent.resolve() or data['version'] != [3, 14, 10]):
        raise ValueError('Existing environment uses a different runtime; no packages were changed.')
    environment = os.environ.copy()
    environment.update({'PIP_CONFIG_FILE': os.devnull, 'PIP_INDEX_URL': 'https://pypi.org/simple',
                        'PIP_CACHE_DIR': str(ROOT / '.superpowers' / 'setup-cache'),
                        'PIP_DISABLE_PIP_VERSION_CHECK': '1'})
    for name in ('PIP_EXTRA_INDEX_URL', 'PIP_TRUSTED_HOST', 'PIP_FIND_LINKS'):
        environment.pop(name, None)
    command = [str(python), '-I', '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:']
    if wheelhouse:
        command += ['--no-index', '--find-links', str(Path(wheelhouse).resolve())]
    for lock in ('requirements-tools-lock.txt', 'requirements-runtime-lock.txt'):
        subprocess.run(command + ['-r', str(ROOT / lock)], check=True, env=environment)
    subprocess.run([str(python), '-I', '-m', 'pip', 'check'], check=True, env=environment)
    return marker


def main(argv=None):
    parser = argparse.ArgumentParser(description='Set up VoiceCastTTS v2 locally from source.')
    parser.add_argument('--runtime-archive', type=Path, help='Optional verified offline CPython archive')
    parser.add_argument('--wheelhouse', type=Path, help='Optional offline wheel cache')
    parser.add_argument('--model-cache', type=Path, help='Optional directory containing the two pinned models')
    args = parser.parse_args(argv)
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64') or sys.maxsize < 2**32:
        parser.error('This beta setup supports 64-bit Windows and a 64-bit Python bootstrap.')
    runtime = prepare_runtime(args.runtime_archive)
    marker = prepare_environment(runtime, args.wheelhouse)
    prepare_models(args.model_cache)
    configuration = inside(ROOT, ROOT / 'voice_config.json')
    if not configuration.exists():
        shutil.copyfile(ROOT / 'voice_config.example.json', configuration)
    marker.write_text('ok\n', encoding='ascii')
    print('Setup complete. Run Launch_VoiceCastTTS_v2.bat. Your files stay in this folder.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('Setup could not finish: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
