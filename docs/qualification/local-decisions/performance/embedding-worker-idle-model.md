# Настоящая модель после idle обычного worker

Дата: 28.09.2026. Дизайн зафиксирован до измерения. Продолжение [production opt-in idle timeout](./embedding-worker-idle-opt-in.md); runtime этого этапа не изменяется.

Восемь фаз: batch 1/32 × keep/retire/retire/keep. В каждой работает отдельный обычный Python worker с одним слотом `session`, настоящий coordinator HTTP в измерительном Node-процессе и отдельная SQLite. Это индексирование документов через выдачу lease и terminal completion, а не прямой вызов transport. PostgreSQL-отказы проверены предыдущим этапом; этот опыт намеренно использует SQLite и не является сравнением БД.

Каждая фаза индексирует два документа, делает явный снимок, ждёт четыре секунды, затем индексирует ещё два документа с теми же двумя содержимыми. `keep` задаёт timeout 0, `retire` — 3 секунды. После каждого burst подтверждается завершение исполнения lease. Все документы дают по одному batch; для 32 chunks русский учебный префикс каждого 400-символьного фрагмента дополнен буквой `я`. Это искусственная нагрузка для проверки границы batch и сохранности вектора, не реальный корпус или проверка семантического качества.

План: 32 worker model calls, 528 сохранённых vectors, 8 завершённых worker и 12 reaped helpers. Перед фазами отдельный native embedding warmup из одного короткого текста, всего 33 модельных вызова. После завершения owned модель явно выгружается. Отдельный Ollama с установленной `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1` фиксируется в JSON-плане по manifest; окончательным источником identity служит закреплённый plan и metadata. Cloud выключен, parallel 1, context 2048, keep-alive 5 минут. Стандартный пользовательский Ollama не используется.

Полные vectors фиксируются на выходе `LocalModelClient.embed` и отдельно читаются из сохранённых chunks; данные сжимаются вне timed вызовов. Verifier независимо сверяет все значения, input и source SHA, provenance offsets, новый lease для каждой партии, ровно один terminal success и ready event на документ, PID до/после idle, закрытие pipes, FD и threads. Допуск между повторениями одной пары входов — component difference 1e-6 и cosine distance 1e-10; equality сохранённого DB-вектора с ответом данного вызова должна быть точной.

Локальная instrumentation отмечает начало/конец embed, рождение и retirement helper, lease и completion. Снимки RSS запрашиваются по отдельному control pipe после завершения burst. Контроллер stdin закрывается перед SIGTERM и присоединяется до итоговой проверки ресурсов. Его стоимость и profile hooks присутствуют в обеих фазах; измерение не является временем production без инструментации. Для второго burst первый и последующий вызовы разбираются отдельно. По два наблюдения на режим/позицию/batch — описательные величины, без уверенного percentile SLO.

Smoke до фиксации дизайна проходит четыре сочетания batch 1/32 × timeout 0/0,8 с на настоящем worker/HTTP/SQLite с контролируемыми vectors. Изначально harness указал недопустимый poll interval 0,1 с; он исправлен на штатный минимум 0,2 с до модельного прогона. Короткий idle smoke выбран больше интервала обычного polling, чтобы не подменять проверку межсерийного простоя retirement между любыми соседними lease.

```sh
python3 scripts/profile-embedding-worker-idle.py --output docs/qualification/local-decisions/performance/evidence/2026-09-28/embedding-worker-idle-model
python3 scripts/verify-embedding-worker-idle.py docs/qualification/local-decisions/performance/evidence/2026-09-28/embedding-worker-idle-model
```

Каждый повтор требует нового output-каталога. Общий бюджет Node-нагрузки — 240 секунд, phase budget — 60 секунд, embedding request — 30 секунд. Исходники producer, verifier, worker и coordinator должны соответствовать записанному commit до запуска.
