# Публикация и сопровождение GitHub

Основной README хранится в `/docs/README.md`; GitHub показывает его на главной странице.
Английская версия — `/docs/README.en.md`. Руководства, история релизов, screenshots
и исходники social preview также находятся в `/docs`.

## Карточка репозитория

Описание: `Run AI agents and visual workflows on your own infrastructure, with local LLMs, human approvals, and execution history.`

Website: `https://github.com/Solutions-of-Security/Agat/blob/main/docs/index.md`.
Topics: `ai-agents`, `local-first`, `self-hosted`, `local-llm`, `workflow-automation`,
`ollama`, `human-in-the-loop`, `rag`, `mcp`, `temporal`.

Карточка ссылки: [JPEG 1280 × 640](./assets/social-preview.jpg),
[исходник HTML](./assets/social-preview.html).
JPEG назначается через Settings → General → Social preview. Карточка содержит
название и назначение продукта; статус проверок берётся из badges и Actions.

## Правила main

Изменения проходят через PR и check `required`, объединяющий jobs `test`, `browser`
и все элементы матрицы `integration`. Правила применяются также к администраторам;
force push и удаление ветки запрещены. Число обязательных approvals — 0 при одном
сопровождающем; CODEOWNERS указывает текущего ответственного. Нерешённые обсуждения
должны быть закрыты перед merge. При расширении команды следует пересмотреть число approvals.

Dependabot проверяет npm/Python/GitHub Actions еженедельно, отдельный исторический
отчёт — ежемесячно. Обновления рассматриваются через PR и проходят применимые проверки.

## Выпуск

1. Выберите конкретный commit в `main` с успешным `required`. Для release с изменениями
   deployment/protocol дополнительно соберите evidence соответствующих gates из roadmap.
2. Согласуйте package version, tag и release notes в `docs/releases`. Номер версии
   не превращает preview в stable; статус отражает фактическую готовность.
3. Проверьте инструкции чистой установки, изменения схемы и порядок отката.
4. Создайте tag на проверенном SHA и GitHub Release с текстом из release notes.
   Для public preview установите флаг prerelease; для 1.7.0 используется tag `v1.7.0`.
5. Проверьте публичную страницу релиза, tag, ссылки и совпадение SHA с проверками.

Собранные контейнеры и provenance требуют отдельного подписанного release pipeline
и trust roots. До его появления установка документируется из исходников; наличие
Dockerfile не означает публикации готового контейнерного образа.
