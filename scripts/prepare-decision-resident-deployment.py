#!/usr/bin/env python3
"""Prepare a new resident shadow bundle; never register or replace a LaunchAgent."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from decision_runtime import implementation_sha256
from decision_runtime.artifacts import read_json, sealed, write_new
from decision_runtime.contracts import canonical_json, fingerprint
from decision_runtime.model_store import sha256_file, verify_manifest
from scripts.lib.decision_service import launch_agent, write_launch_agent

PROFILE = 'docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json'
POLICY = 'docs/qualification/local-decisions/policy.shadow.v1.json'
OBS = 'docs/qualification/local-decisions/shadow/observability'


def require(condition,message):
    if not condition: raise ValueError(message)


def normalized(name): return re.sub(r'[-_.]+','-',name).lower()


def requirements(path):
    result = {}
    for line in path.read_text().splitlines():
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)',line)
        require(match is not None,'Every dependency must have one exact version pin')
        name,version = match.groups();name = normalized(name)
        require(name not in result,'Duplicate dependency pin');result[name] = version
    require(bool(result),'Empty dependency pins')
    return result


def wheel_lock(directory,pins):
    wheels = {}
    for path in sorted(directory.iterdir()):
        require(path.is_file() and path.suffix == '.whl','Wheelhouse must contain only regular wheels')
        require(not (getattr(path.stat(),'st_flags',0) & getattr(stat,'SF_DATALESS',0)),'Cloud-only dependency wheel')
        with zipfile.ZipFile(path) as archive:
            metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            require(len(metadata) == 1,'Invalid wheel metadata')
            header = email.parser.BytesParser().parsebytes(archive.read(metadata[0]))
        name,version = normalized(header['Name']),header['Version']
        require(name in pins and pins[name] == version and name not in wheels,'Foreign, duplicate or wrong-version wheel')
        wheels[name] = {'filename':path.name,'sha256':sha256_file(path),'version':version}
    require(set(wheels) == set(pins),'Incomplete pinned wheelhouse')
    return wheels, ''.join(f'{name}=={pins[name]} --hash=sha256:{wheels[name]["sha256"]}\n' for name in sorted(pins))


def new_destination(path,home=None):
    home = Path.home() if home is None else home
    base = (home/'Library/Application Support/Agat/decision-shadow/releases').resolve()
    path = path.absolute()
    require(path.resolve().parent == base and not path.exists() and not path.is_symlink(),
            'Use a new direct release directory in Application Support/Agat/decision-shadow/releases')
    require(not any(path.resolve().is_relative_to((home/name).resolve()) for name in ('Documents','Desktop','Downloads')),
            'Deployment must be outside protected document directories')
    return path


def resident_manifest(path):
    def resident(file):
        require(not (getattr(file.stat(),'st_flags',0) & getattr(stat,'SF_DATALESS',0)), 'Restore cloud-only model files before preparation')
    resident(path);value = read_json(path)
    require(isinstance(value.get('snapshot'),str) and isinstance(value.get('files'),dict),'Invalid local model manifest')
    snapshot = Path(value['snapshot']);resident(snapshot)
    for name in value['files']:
        require(isinstance(name,str) and name and Path(name).name == name,'Invalid model filename')
        resident(snapshot/name)
    return verify_manifest(path)


def source_identity():
    names = [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'decision_runtime').glob('*.py'))]
    names += ['decision_runtime/models.json','decision_runtime/requirements-mlx.txt',
              'scripts/prepare-decision-resident-deployment.py','scripts/lib/decision_service.py',PROFILE,POLICY,
              f'{OBS}/prometheus-3.13.4.json',f'{OBS}/alerts.yml',f'{OBS}/alerts.test.yml']
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True,timeout=5).strip()
    files = {n:sha256_file(ROOT/n) for n in names}
    for name,checksum in files.items():
        require(hashlib.sha256(subprocess.check_output(['git','show',f'{commit}:{name}'],cwd=ROOT,timeout=5)).hexdigest() == checksum,
                'Commit deployment sources before preparation')
    return commit,files


def prometheus_agent(root,prometheus,port):
    require(type(port) is int and 1024 <= port <= 65535,'Invalid Prometheus web port')
    return {'Label':'org.agat.decision-prometheus','ProgramArguments':[str(prometheus),
            f'--config.file={root/"config/prometheus.yml"}',f'--web.listen-address=127.0.0.1:{port}',
            f'--storage.tsdb.path={root/"data/prometheus"}','--storage.tsdb.retention.time=7d','--storage.tsdb.retention.size=256MB'],
            'WorkingDirectory':str(root/'config'),'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},
            'ThrottleInterval':30,'ExitTimeOut':30,'StandardOutPath':str(root/'logs/org.agat.decision-prometheus.out.log'),
            'StandardErrorPath':str(root/'logs/org.agat.decision-prometheus.err.log')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('destination','python','manifest','wheelhouse','prometheus','promtool','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--runtime-port',type=int,default=8766)
    parser.add_argument('--monitor-port',type=int,default=9095)
    args = parser.parse_args(argv)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    def interrupted(_signal,_frame): raise KeyboardInterrupt('Resident preparation interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    directory = None;commands = [];failure = None;prepared = None
    try:
        require(platform.system() == 'Darwin','Resident bundle requires macOS')
        require(not args.output.exists() and args.output.absolute().resolve().is_relative_to((ROOT/'docs/private').resolve()),
                'Use a new output under docs/private')
        destination = new_destination(args.destination)
        require(type(args.runtime_port) is int and 1024 <= args.runtime_port <= 65535 and args.runtime_port != args.monitor_port,'Invalid or duplicate service ports')
        prometheus_agent(destination,args.prometheus,args.monitor_port)
        commit,sources = source_identity()
        profile = read_json(ROOT/PROFILE)
        require(profile['model']['implementationSha256'] == implementation_sha256(),'Runtime source/profile mismatch')
        manifest,snapshot = resident_manifest(args.manifest)
        require(manifest['artifactSha256'] == profile['model']['artifactSha256'] and manifest['revision'] == profile['model']['revision']
                and manifest['repository'] == profile['model']['repository'],
                'Model manifest/profile mismatch')
        pins = requirements(ROOT/'decision_runtime/requirements-mlx.txt');wheels,lock = wheel_lock(args.wheelhouse,pins)
        release = read_json(ROOT/f'{OBS}/prometheus-3.13.4.json')
        for name,path in [('prometheus',args.prometheus),('promtool',args.promtool)]:
            require(path.is_file() and sha256_file(path) == release['binariesSha256'][name],f'Unpinned {name} binary')
        python = args.python.absolute()
        probe = json.loads(subprocess.check_output([str(python),'-c','import json,platform,sys;print(json.dumps({"version":list(sys.version_info[:3]),"machine":platform.machine()}))'],text=True,timeout=10))
        require(probe == {'version':[3,13,12],'machine':'arm64'},'Use the tested Python 3.13.12 arm64 interpreter')
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.mkdir(mode=0o700);directory = destination
        for name in ('runtime/decision_runtime','model','bin','config','logs','data/prometheus','launchd','docs/licenses'):
            (directory/name).mkdir(parents=True,mode=0o700,exist_ok=True)
        copied = {}
        def copy(source,relative,mode=0o600):
            target = directory/relative;shutil.copyfile(source,target);target.chmod(mode);copied[relative] = sha256_file(target)
        for source in sorted((ROOT/'decision_runtime').glob('*.py')): copy(source,f'runtime/decision_runtime/{source.name}')
        for name in ('models.json','requirements-mlx.txt'): copy(ROOT/'decision_runtime'/name,f'runtime/decision_runtime/{name}')
        for name in manifest['files']: copy(snapshot/name,f'model/{name}')
        if (snapshot/'README.md').is_file(): copy(snapshot/'README.md','docs/model-card.md')
        local_manifest = {**manifest,'snapshot':str(directory/'model')}
        write_new(directory/'config/decider-2b.json',local_manifest)
        for source,relative in [(ROOT/PROFILE,'config/profile.json'),(ROOT/POLICY,'config/policy.json'),
                                (ROOT/f'{OBS}/alerts.yml','config/alerts.yml'),(ROOT/f'{OBS}/alerts.test.yml','config/alerts.test.yml')]: copy(source,relative)
        for name,path in [('prometheus',args.prometheus),('promtool',args.promtool)]: copy(path,f'bin/{name}',0o700)
        for name in ('LICENSE','NOTICE'):
            source = args.prometheus.parent/name
            if source.is_file(): copy(source,f'docs/licenses/prometheus-{name}')
        if (ROOT/'LICENSE').is_file(): copy(ROOT/'LICENSE','docs/licenses/Agat-LICENSE')
        (directory/'config/requirements.lock.txt').write_text(lock)
        def run(arguments,timeout=300):
            result = subprocess.run(arguments,capture_output=True,text=True,timeout=timeout)
            commands.append({'arguments':arguments,'returncode':result.returncode,'output':result.stdout+result.stderr})
            require(result.returncode == 0,'Bundle preparation command failed; see private evidence')
            return result.stdout
        run([str(python),'-m','venv',str(directory/'venv')])
        local_python = directory/'venv/bin/python'
        run([str(local_python),'-m','pip','install','--no-index','--find-links',str(args.wheelhouse.absolute()),'--require-hashes','--no-deps','-r',str(directory/'config/requirements.lock.txt')])
        run([str(local_python),'-m','pip','check'])
        code = 'import importlib.metadata,json,mlx.core as mx;mx.eval(mx.array([1,2])+1);print(json.dumps({d.metadata["Name"]:d.version for d in importlib.metadata.distributions()}))'
        installed = {normalized(k):v for k,v in json.loads(run([str(local_python),'-c',code])).items() if normalized(k) != 'pip'}
        require(installed == pins,'Prepared environment differs from exact dependency pins')
        config = directory/'config/prometheus.yml'
        config.write_text(f'global:\n  scrape_interval: 15s\n  evaluation_interval: 15s\nrule_files:\n  - alerts.yml\nscrape_configs:\n  - job_name: agat-decision\n    scrape_timeout: 5s\n    metrics_path: /metrics\n    static_configs:\n      - targets: ["127.0.0.1:{args.runtime_port}"]\n')
        run([str(directory/'bin/promtool'),'check','config',str(config)],30)
        run([str(directory/'bin/promtool'),'test','rules',str(directory/'config/alerts.test.yml')],30)
        jobs = [launch_agent(root=directory/'runtime',python=local_python,manifest=directory/'config/decider-2b.json',
                             policy=directory/'config/policy.json',log_dir=directory/'logs',port=args.runtime_port,inference_timeout_ms=5000),
                prometheus_agent(directory,directory/'bin/prometheus',args.monitor_port)]
        jobs[0]['EnvironmentVariables'].update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        for job in jobs:
            path = directory/'launchd'/f'{job["Label"]}.plist';write_launch_agent(path,job);path.chmod(0o600)
            for key in ('StandardOutPath','StandardErrorPath'):
                with Path(job[key]).open('xb'): pass
                Path(job[key]).chmod(0o600)
        require(all(sha256_file(ROOT/n) == h for n,h in sources.items()),'Deployment sources changed during preparation')
        verify_manifest(directory/'config/decider-2b.json')
        generated = {}
        for path in [*(directory/'config').iterdir(),*(directory/'launchd').iterdir()]:
            path.chmod(0o600);generated[path.relative_to(directory).as_posix()] = sha256_file(path)
        prepared = sealed({'schemaVersion':'agat.decision.resident-bundle.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                           'status':'prepared','qualification':'not_assessed','routingEnabled':False,'sourceCommit':commit,
                           'destination':str(directory),'profile':profile,'profileSha256':fingerprint(profile),'sourceFiles':sources,
                           'copiedFiles':copied,'generatedFiles':generated,'wheels':wheels,'installedDependencies':installed,'python':str(local_python),
                           'serviceConfigs':jobs,'limitations':['No job was registered or installed by preparation.',
                           'Base Homebrew Python remains an external managed dependency; a reboot/login was not exercised.']})
        write_new(directory/'deployment.json',prepared);(directory/'deployment.json').chmod(0o600)
    except (Exception,KeyboardInterrupt) as error:
        failure = {'type':type(error).__name__,'message':str(error)[:1000]}
    finally:
        signal.signal(signal.SIGTERM,previous_sigterm)
    report = sealed({'schemaVersion':'agat.decision.resident-preparation.v1','createdAt':datetime.now(timezone.utc).isoformat(),
                     'status':'prepared' if prepared else 'failed','qualification':'not_assessed','routingEnabled':False,
                     'destination':str(directory) if directory else None,'bundle':prepared,'commands':commands,'failure':failure})
    try:
        require(not args.output.exists() and args.output.absolute().resolve().is_relative_to((ROOT/'docs/private').resolve()),'Invalid private output')
        write_new(args.output,report);args.output.chmod(0o600)
    except (OSError,ValueError) as error:
        print(f'Cannot save private preparation evidence: {error}',file=sys.stderr);return 1
    print(f'Resident bundle {report["status"]}; private evidence: {args.output}',flush=True)
    return 0 if prepared else 1


if __name__ == '__main__': raise SystemExit(main())
