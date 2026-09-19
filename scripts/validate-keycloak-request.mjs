import { readFileSync } from 'node:fs';
import Ajv from 'ajv';
import { requirementsSchema, semanticErrors } from './keycloak-gitops-pack.mjs';

if (!process.argv[2]) throw new Error('Укажите путь к заполненному JSON запроса');
const request = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const validate = new Ajv({ allErrors: true }).compile(requirementsSchema);
const structurallyValid = validate(request);
const errors = structurallyValid ? semanticErrors(request) : validate.errors.map(e => `${e.instancePath || '/'}: ${e.message}`);
console.log(JSON.stringify({ valid: errors.length === 0, errors, note: 'Проверка параметров. Готовность инфраструктуры, версия/совместимость и PASS реальных тестов проверяются отдельно.' }, null, 2));
process.exitCode = errors.length ? 1 : 0;
