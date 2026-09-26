#!/usr/bin/env python3
"""Disposable pgvector compatibility probe; never connects to an existing database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.rag_corpus import file_sha, write_new
from scripts.lib.pgvector_probe import make_vectors, close_vectors, rank_vectors, verify_exact, inspect_hits

IMAGE = 'pgvector/pgvector:0.8.6-pg17-bookworm@sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f'
DATABASE = 'agat_pgvector_probe'
DIMENSIONS = 768
TOP_K = 20


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence_dir.resolve()
    if not evidence.is_relative_to(ROOT / 'docs') or evidence == ROOT / 'docs' or evidence.exists():
        raise ValueError('Use a new evidence directory under docs')
    # No endpoint override: the only target is the new, owned container, with no published port.
    name = f'agat-pgvector-probe-{uuid.uuid4().hex}'
    inputs = ['scripts/probe-pgvector.py', 'scripts/lib/pgvector_probe.py', 'scripts/lib/rag_corpus.py',
              'apps/coordinator/src/knowledge.ts', 'apps/coordinator/src/database.ts']
    hashes = {path: file_sha(ROOT / path) for path in inputs}
    env = {key: value for key, value in os.environ.items() if not key.startswith(('AGAT_', 'PG'))}
    env.update({'PGPASSWORD': 'disposable-probe-fixture'})
    checks, plans, observations = [], {}, {}
    errors = []
    cleanup = {'containerRemoved': False}
    evidence.mkdir(parents=True)
    write_new(evidence / 'plan.json', {
        'image': IMAGE, 'inputHashes': hashes, 'dimensions': DIMENSIONS, 'topK': TOP_K,
        'fixture': {'chosenProjectRows': 6001, 'excludedCollectionRows': 100, 'otherProjectRows': 400,
                    'generator': 'LCG32 seed 20260927; 16 nonzero coordinates padded to 768'},
        'queries': ['first-axis', 'second-axis', 'first-sixteen-axes'],
        'expected': ['exact SQL returns all scoped topK members on the synthetic corpus',
                     'float32 collapses distinct valid double vectors, including outside a rerank pool',
                     '4096D exact storage works; vector and halfvec HNSW reject 4096D',
                     'runtime RLS isolates projects; absent/reset scope returns no rows',
                     'ANN recall/count recorded, not assumed to equal exact search; no leaked hits'],
        'ann': {'efSearch': 40, 'iterativeScan': ['off', 'strict_order'], 'maxScanTuples': 20000,
                'forcedIndex': True, 'indexBuilds': 1},
        'limits': {'sqlTimeoutMs': 30000, 'indexBuildTimeoutMs': 120000, 'commandTimeoutSeconds': 180,
                   'containerMemoryBytes': 2147483648, 'containerCpuLimit': 2},
        'scope': 'synthetic compatibility probe, not the application search path, throughput or semantic quality',
        'host': {'system': platform.system(), 'release': platform.release(), 'architecture': platform.machine()},
    })

    def command(argv, *, content=None, expected_error=False, timeout=180):
        result = subprocess.run(argv, cwd=ROOT, env=env, input=content, capture_output=True,
                                text=True, timeout=timeout)
        if expected_error:
            if result.returncode == 0:
                raise AssertionError('Expected SQL error but command succeeded')
            return result.stderr.strip()
        if result.returncode != 0:
            raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr[-4000:]}')
        return result.stdout.strip()

    def sql(statement, *, runtime=False, project=None, settings='', expected_error=False, timeout_ms=30000):
        role = 'probe_runtime' if runtime else 'postgres'
        prefix = ''
        if project is not None or settings:
            # Values are hard-coded probe inputs, never user/database strings.
            assert project in (None, 'project-a', 'project-b')
            prefix = 'BEGIN; ' + (f"SET LOCAL agat.project_id = '{project}'; " if project else '') + settings
        body = prefix + statement + ('; ROLLBACK;' if prefix else '')
        return command(['docker', 'exec', '-i', '--env', 'PGPASSWORD', '--env',
                        f'PGOPTIONS=-c statement_timeout={timeout_ms}', name,
                        'psql', '--no-psqlrc', '--quiet', '--tuples-only', '--no-align', '--no-password',
                        '--host', '127.0.0.1', '--username', role, '--dbname', DATABASE,
                        '--set', 'ON_ERROR_STOP=1', '--set', 'VERBOSITY=verbose'],
                       content=body, expected_error=expected_error)

    def query(statement, **kwargs):
        return json.loads(sql(statement, **kwargs))

    def passed(label, **details):
        checks.append({'name': label, 'status': 'pass', **details})
        print(f'pass: {label}', flush=True)

    def rejected(label, statement, code, **kwargs):
        error = sql(statement, expected_error=True, **kwargs)
        if f'{code}:' not in error:
            raise AssertionError(f'{label}: unexpected SQL failure: {error}')
        passed(label, sqlState=code, error=error)

    def interrupted(_number, _frame):
        raise KeyboardInterrupt('Probe interrupted')

    signal.signal(signal.SIGTERM, interrupted)
    start = time.monotonic()
    try:
        command(['docker', 'run', '--detach', '--pull=never', '--name', name, '--network=none',
                 '--memory=2g', '--cpus=2', '--env', f'POSTGRES_DB={DATABASE}', '--env',
                 'POSTGRES_PASSWORD=disposable-probe-fixture', IMAGE])
        for _ in range(60):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '--host', '127.0.0.1',
                                    '--username', 'postgres', '--dbname', DATABASE],
                                   env=env, capture_output=True, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Postgres did not become ready')
        observations['image'] = json.loads(command(['docker', 'image', 'inspect', IMAGE, '--format',
                                                   '{{json .}}']))
        # Keep only immutable identity/platform, not an unrestricted Docker environment dump.
        observations['image'] = {key: observations['image'][key] for key in ('Id', 'RepoDigests', 'Architecture', 'Os')}
        sql("""
            CREATE EXTENSION vector;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
            CREATE ROLE probe_runtime LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
              NOREPLICATION PASSWORD 'disposable-probe-fixture';
            CREATE TABLE probe_vectors (id integer PRIMARY KEY, project_id text NOT NULL,
              collection_id text NOT NULL, embedding vector(768) NOT NULL);
            ALTER TABLE probe_vectors ENABLE ROW LEVEL SECURITY;
            ALTER TABLE probe_vectors FORCE ROW LEVEL SECURITY;
            CREATE POLICY project_scope ON probe_vectors TO probe_runtime
              USING (project_id = NULLIF(current_setting('agat.project_id', true), ''))
              WITH CHECK (project_id = NULLIF(current_setting('agat.project_id', true), ''));
            GRANT SELECT, INSERT, UPDATE, DELETE ON probe_vectors TO probe_runtime;
        """)
        observations['versions'] = query("""SELECT json_build_object('postgres', version(),
            'extension', (SELECT extversion FROM pg_extension WHERE extname = 'vector'))""")
        assert observations['versions']['extension'] == '0.8.6'
        role = query("""SELECT row_to_json(r) FROM (SELECT current_user, rolsuper, rolbypassrls,
            rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user) r""", runtime=True)
        assert role == {'current_user': 'probe_runtime', 'rolsuper': False, 'rolbypassrls': False,
                        'rolcreatedb': False, 'rolcreaterole': False}
        passed('runtime role has no owner/superuser/RLS bypass', role=role)

        close = close_vectors()
        observations['precision'] = {'query': [1, 0], 'candidates': close,
                                     'float64Ranking': rank_vectors(close, [1, 0], len(close))}
        values = ','.join(f"({row['id']}, '{json.dumps(row['vector'])}'::vector)" for row in close)
        sql(f'CREATE TABLE probe_close (id integer PRIMARY KEY, embedding vector(2)); INSERT INTO probe_close VALUES {values}')
        rounded = query("""SELECT json_agg(row_to_json(r) ORDER BY distance, id) FROM (
            SELECT id, embedding::text AS stored, embedding <=> '[1,0]'::vector AS distance
            FROM probe_close ORDER BY embedding <=> '[1,0]'::vector, id) r""")
        assert len({row['stored'] for row in rounded}) == 1
        assert observations['precision']['float64Ranking'][0]['id'] == 12
        assert rounded[0]['id'] != 12 and 12 not in [row['id'] for row in rounded[:8]]
        observations['precision']['pgvectorRanking'] = rounded
        passed('distinct float64 neighbors collapse in float32, including outside top8 rerank pool')

        sql('CREATE TABLE probe_dimensions (id integer, embedding vector)')
        vector4096 = json.dumps([1] * 4096)
        sql(f"INSERT INTO probe_dimensions VALUES (1, '{vector4096}'::vector), (2, '[1,0]'::vector)")
        dimensions = query(f"""SELECT json_build_object('dimensions', vector_dims(embedding),
            'distance', embedding <=> '{vector4096}'::vector) FROM probe_dimensions WHERE id = 1""")
        assert dimensions == {'dimensions': 4096, 'distance': 0}
        passed('mixed-dimension storage and 4096D exact comparison work', **dimensions)
        rejected('untyped mixed-dimension HNSW rejected', 'CREATE INDEX ON probe_dimensions USING hnsw (embedding vector_cosine_ops)', '22023')
        rejected('4096D vector HNSW rejected', 'CREATE INDEX ON probe_dimensions USING hnsw ((embedding::vector(4096)) vector_cosine_ops) WHERE id = 1', '54000')
        rejected('4096D halfvec HNSW rejected', 'CREATE INDEX ON probe_dimensions USING hnsw ((embedding::halfvec(4096)) halfvec_cosine_ops) WHERE id = 1', '54000')
        sql('CREATE INDEX ON probe_dimensions USING hnsw ((embedding::vector(2)) vector_cosine_ops) WHERE id = 2')
        passed('partial expression index supports a fixed dimension')

        vectors = make_vectors(DIMENSIONS)
        copy = 'COPY probe_vectors (id, project_id, collection_id, embedding) FROM STDIN;\n' + ''.join(
            f"{row['id']}\t{row['project']}\t{row['collection']}\t{json.dumps(row['vector'], separators=(',', ':'))}\n"
            for row in vectors) + '\\.\n'
        observations['fixtureSha256'] = hashlib.sha256(copy.encode()).hexdigest()
        sql(copy)
        sql('ANALYZE probe_vectors')
        count_sql = 'SELECT json_build_object(\'count\', count(*)) FROM probe_vectors'
        assert query(count_sql, runtime=True)['count'] == 0
        assert query(count_sql, runtime=True, project='project-a')['count'] == 6101
        assert query(count_sql, runtime=True, project='project-b')['count'] == 400
        assert query(count_sql + " WHERE collection_id = 'chosen'", runtime=True, project='project-a')['count'] == 6001
        assert query(count_sql + " WHERE project_id = 'project-b'", runtime=True, project='project-a')['count'] == 0
        assert query("BEGIN; SET LOCAL agat.project_id = 'project-a'; COMMIT; " + count_sql, runtime=True)['count'] == 0
        passed('RLS rejects absent/reset/foreign scope and retains 6001 selected candidates')
        # Use a valid vector so the policy itself, rather than type validation, rejects this write.
        rejected('valid cross-project INSERT rejected by RLS', f"INSERT INTO probe_vectors VALUES (9000, 'project-b', 'chosen', '{json.dumps(vectors[0]['vector'])}'::vector)",
                 '42501', runtime=True, project='project-a')
        changes = query("""WITH changed AS (DELETE FROM probe_vectors WHERE project_id = 'project-b' RETURNING id)
            SELECT json_build_object('count', count(*)) FROM changed""", runtime=True, project='project-a')
        assert changes['count'] == 0
        assert query(count_sql, runtime=True, project='project-b')['count'] == 400
        passed('foreign DELETE changes no rows')

        queries = [[1] + [0] * (DIMENSIONS - 1), [0, 1] + [0] * (DIMENSIONS - 2),
                   [1] * 16 + [0] * (DIMENSIONS - 16)]
        def select(vector):
            literal = json.dumps(vector, separators=(',', ':'))
            return f"""SELECT id, project_id AS project, collection_id AS collection,
                embedding <=> '{literal}'::vector AS distance FROM probe_vectors
                WHERE collection_id = 'chosen' ORDER BY embedding <=> '{literal}'::vector LIMIT {TOP_K}"""

        def search(vector, settings):
            statement = select(vector)
            rows = query(f'SELECT coalesce(json_agg(r ORDER BY distance, id), \'[]\'::json) FROM ({statement}) r',
                         runtime=True, project='project-a', settings=settings)
            plan = query('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + statement,
                         runtime=True, project='project-a', settings=settings)
            inspect_hits(rows, TOP_K)
            return rows, plan

        exact_settings = 'SET LOCAL enable_indexscan = off; SET LOCAL enable_bitmapscan = off; '
        observations['exact'] = []
        selected = [row for row in vectors if row['project'] == 'project-a' and row['collection'] == 'chosen']
        for index, vector in enumerate(queries):
            rows, plan = search(vector, exact_settings)
            expected = rank_vectors(selected, vector, TOP_K)
            verify_exact(rows, expected)
            observations['exact'].append({'query': index, 'hits': rows, 'float64Oracle': expected,
                'maxScoreError': max(abs(1 - row['distance'] - ref['score']) for row, ref in zip(rows, expected))})
            plans[f'exact-{index}'] = plan
        passed('three scoped exact topK sets match float64 oracle above 5000 candidates')

        sql('SET max_parallel_maintenance_workers = 0; CREATE INDEX probe_vectors_hnsw ON probe_vectors USING hnsw (embedding vector_cosine_ops); ANALYZE probe_vectors', timeout_ms=120000)
        observations['ann'] = []
        for mode in ('off', 'strict_order'):
            for index, vector in enumerate(queries):
                settings = f"SET LOCAL enable_seqscan = off; SET LOCAL hnsw.ef_search = 40; SET LOCAL hnsw.iterative_scan = {mode}; SET LOCAL hnsw.max_scan_tuples = 20000; "
                rows, plan = search(vector, settings)
                assert 'probe_vectors_hnsw' in json.dumps(plan), 'ANN measurement did not use HNSW'
                expected_ids = {row['id'] for row in observations['exact'][index]['hits']}
                recall = len(expected_ids.intersection(row['id'] for row in rows)) / TOP_K
                observations['ann'].append({'mode': mode, 'query': index, 'hits': rows,
                                             'count': len(rows), 'recallAt20AgainstPgvectorExact': recall})
                plans[f'ann-{mode}-{index}'] = plan
        passed('six forced HNSW queries preserve RLS/collection filtering; recall measured separately')
        for path, sha in hashes.items():
            assert file_sha(ROOT / path) == sha, f'Source changed during probe: {path}'
        passed('probe and baseline source hashes unchanged')
    except BaseException as error:
        errors.append(f'{type(error).__name__}: {error}')
    finally:
        try:
            command(['docker', 'rm', '--force', '--volumes', name], timeout=30)
            cleanup['containerRemoved'] = True
        except Exception as error:
            errors.append(f'cleanup: {error}')
        write_new(evidence / 'query-plans.json', plans)
        write_new(evidence / 'result.json', {'status': 'pass' if not errors else 'fail', 'planSha256': file_sha(evidence / 'plan.json'),
                  'checks': checks, 'observations': observations, 'errors': errors, 'cleanup': cleanup,
                  'elapsedSeconds': round(time.monotonic() - start, 3)})
    if errors:
        raise RuntimeError('; '.join(errors))


if __name__ == '__main__':
    main()
