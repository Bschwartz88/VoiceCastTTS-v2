"""Check a proposed clean publishing ref locally; emits categories/counts, never secret values.
Usage: python scripts/check_publication.py refs/heads/main
This is a bounded pattern/history check, not a complete secrets scanner.
"""
from pathlib import Path
import re
import subprocess
import sys
import json
import argparse

PRIVATE = re.compile(r'(^|/)(\.superpowers|\.runtime|\.venv|venv|custom_voices|output|\.local_data|docs/superpowers)(/|$)|(^|/)(HANDOFF\.md|CHECKPOINT\.md|LOCAL_REVIEW\.md|LAB_CHECKPOINT\.md|LAB_REVIEW\.md|GITHUB_PRIVACY_REVIEW_LOCAL\.md|user_settings\.json|voice_config\.json|studio-session\.json|VoiceCast_Studio_Session_Summary\.md|\.env(?:\..*)?)$|\.(?:pem|key|bundle|wav|mp3|onnx|bin|npy|npz|exe|dll|pyd|whl|nupkg)$', re.I)
SECRET = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9_-]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
PERSONAL = re.compile(rb'[A-Za-z]:[\\/]+Users[\\/]+(?!Public|Default|%|YOUR|Your)[^\\/\r\n"\x27]{2,60}', re.I)

def check(ref, terms_file=None):
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args])
    private_terms = []
    if terms_file is not None:
        values = json.loads(Path(terms_file).read_text(encoding='utf-8'))
        if not isinstance(values, list) or any(not isinstance(v, str) or len(v) < 4 for v in values):
            raise ValueError('Private terms must be a list of strings of at least four characters.')
        private_terms = [v.casefold().encode('utf-8') for v in values]
    term_matches = 0
    objects = git('rev-list','--objects',ref).decode().splitlines()
    objects_by_id = {}
    private = set()
    for entry in objects:
        sha, _, path = entry.partition(' ')
        objects_by_id[sha] = path
    # One local Git process reads exact object bytes; none are uploaded.
    batch = subprocess.run(['git','-C',str(root),'cat-file','--batch'],input=('\n'.join(objects_by_id)+'\n').encode(),stdout=subprocess.PIPE,check=True).stdout
    offset=0; blobs=0; secrets=0; personal=0
    while offset < len(batch):
        end=batch.index(b'\n',offset); header=batch[offset:end].split();offset=end+1
        size=int(header[2]); data=batch[offset:offset+size];offset+=size+1
        if header[1] != b'blob':continue
        path=objects_by_id[header[0].decode()]
        if path and PRIVATE.search(path) and not (path in ('custom_voices/.gitkeep', 'output/previews/.gitkeep') and not data) and path != '.env.example':
            private.add(path)
        blobs+=1
        secrets+=bool(SECRET.search(data))
        personal+=bool(PERSONAL.search(data))
        folded=data.lower()
        term_matches+=any(term in folded for term in private_terms)
    emails=set(git('log',ref,'--format=%ae%n%ce').decode().splitlines())
    identity=sum(bool(e) and e != '277738194+Bschwartz88@users.noreply.github.com' for e in emails)
    print(f'Publishing history: {blobs} blobs; private paths {len(private)}; credential patterns {secrets}; personal paths {personal}; private commit emails {identity}; private-term matches {term_matches}')
    return not (private or secrets or personal or identity or term_matches)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ref')
    parser.add_argument('--terms-file', type=Path, help='Optional LOCAL-ONLY JSON list of private terms; never commit it.')
    args=parser.parse_args()
    raise SystemExit(0 if check(args.ref,args.terms_file) else 1)
