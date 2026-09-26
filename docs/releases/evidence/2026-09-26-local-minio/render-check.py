from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[4]
script = (repo / 'scripts/k8s-up.sh').read_text()
blocks = re.findall(r"node --input-type=module -e '([\s\S]*?)'", script)
render = [block for block in blocks if 'const expectedArtifact' in block]
assert len(render) == 1
with tempfile.TemporaryDirectory(prefix='agat-minio-render-') as tmp:
    root = Path(tmp)
    for tag in ('1.7.0', 'dev.2026-09-26'):
        manifests = root / tag
        shutil.copytree(repo / 'deploy/k8s/docker-desktop', manifests)
        subprocess.run(['node', '--input-type=module', '-e', render[0], str(manifests),
                        f'agat-local/coordinator:{tag}', f'agat-local/minio:{tag}'], check=True)
        rendered = subprocess.run(['kubectl', 'kustomize', str(manifests)],
                                  text=True, capture_output=True, check=True).stdout
        source = (manifests / 'artifact-store.yaml').read_text()
        assert source.count(f'image: agat-local/coordinator:{tag}') == 1
        assert source.count(f'image: agat-local/minio:{tag}') == 1
        assert f'image: agat-local/minio:{tag}' in rendered
        assert 'docker.io/minio/minio:' not in rendered
        print(f'PASS: actual render block + Kustomize, tag={tag}')
    stubs = root / 'bin'
    stubs.mkdir()
    calls = root / 'kubectl-called'
    docker = stubs / 'docker'
    docker.write_text('#!/bin/sh\n[ "$1" = image ] && [ "$2" = inspect ] || exit 72\nexit 1\n')
    kubectl = stubs / 'kubectl'
    kubectl.write_text(f'#!/bin/sh\ntouch "{calls}"\nexit 73\n')
    docker.chmod(0o755)
    kubectl.chmod(0o755)
    env = dict(os.environ, PATH=f'{stubs}:{os.environ["PATH"]}',
               AGAT_K8S_SKIP_BUILD='true', AGAT_K8S_IMAGE_TAG='missing-validation-image')
    result = subprocess.run(['bash', str(repo / 'scripts/k8s-up.sh')],
                            cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode == 1, (result.returncode, result.stderr)
    assert 'ещё не собран' in result.stderr
    assert not calls.exists(), 'preflight reached kubectl despite missing MinIO image'
    print('PASS: missing-image skip-build exits before any kubectl call')
