# Участие в разработке

Ошибки и предложения принимаются через [Issues](https://github.com/Solutions-of-Security/Agat/issues).
Для крупного изменения сначала опишите проблему, ожидаемое поведение и границы задачи.
Уязвимости сообщайте по [приватному каналу](./security.md).

## Подготовка

Рекомендуемый Node.js указан в `.nvmrc` — 24; CI использует Python 3.13.
Заявленный минимум: Node.js 22.13, Python 3.10. Для интеграций нужен Docker с Compose.

```bash
git clone https://github.com/Solutions-of-Security/Agat.git
cd Agat
npm ci
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r workers/requirements.txt
```

Активация выше предназначена для bash/zsh. Windows-пути виртуального окружения
и Docker networking требуют проверки на соответствующей системе.
Запустите `npm run dev:api` и `npm run dev:web` в отдельных терминалах.
Карта компонентов — в [README](./README.md#разработка-и-участие), архитектура — в [руководстве](./architecture.md).

## Проверки перед PR

```bash
npm run typecheck
npm test
npm run build
npm run docs:check
npx playwright install chromium
npm run test:browser
```

`docs:check` проверяет локальные Markdown-ссылки с учётом регистра имён,
тесты проверяющих scripts и актуальность каталога процессов. Внешние URL, якоря
внутри страниц и raw HTML этой проверкой не валидируются.

Браузерные тесты открывают production build на `127.0.0.1:8796`, создают временную
SQLite-базу и dry-run worker. Проверяют создание агента, запуск, согласование, появление результата
и сохранение после перезагрузки на desktop/mobile Chromium. LLM и рабочая база
не используются. Порт можно изменить через `AGAT_SMOKE_PORT`.

CI запускает интеграции на одноразовых Docker-сервисах:

```bash
npm run fleet:test-ha-postgres
npm run fleet:test-state-migration
npm run fleet:test-artifact-store
npm run fleet:test-region-loss-dr
```

Scripts создают собственные контейнеры и удаляют их при завершении. Не подставляйте
production database URLs. Реальная модель проверяется отдельно командой
`npm run test:ollama-rag`; требования — в [оглавлении](./index.md).
Проверки реального provider failover и восстановления остаются отдельными gates.

## Pull request

Используйте отдельную ветку и заполните PR template. Опишите проблему, поведение
после изменения и проверки. Для UI приложите фактические снимки desktop/mobile;
для API — пример запроса/ответа без секретов. Всю документацию храните в `/docs`.

Для `main` требуется PR и успешный check `required`, учитывающий основные тесты,
браузерный сценарий и все инфраструктурные jobs. Force push и удаление `main`
запрещены. При одном сопровождающем обязательное число сторонних approvals равно
нулю: CODEOWNERS направляет запрос review, но не требует одобрить собственный PR.
Нерешённые review discussions блокируют merge.

Соблюдайте [правила общения](./CODE_OF_CONDUCT.md). Каналы помощи — в [SUPPORT](./SUPPORT.md),
порядок выпуска — в [руководстве публикации](./github-publication.md).

## Исследовательские материалы

`docs/agent-process-priority-report` — самостоятельный исторический артефакт со своим
lockfile и правилами сопровождения. Он не входит в runtime платформы. При изменении
прочтите его локальный `AGENTS.md` и выполните собственные тесты/сборку.
Исторические отчёты не подтверждают прохождение текущих release gates.
