# Настоящая модель после idle обычного worker

Дата: 28.09.2026. Дизайн зафиксирован до измерения на commit `fd4c4027efd624cdb806fbe73f44a0288dd3a326`. Продолжение [production opt-in idle timeout](./embedding-worker-idle-opt-in.md); runtime этого этапа не изменяется.

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

## Результат и границы вывода

Прогон на Apple M1 Max / 32 GiB, Python 3.14.3, Node 24.14.0 и Ollama 0.34.2 завершился за **61,811 с**. [План](./evidence/2026-09-28/embedding-worker-idle-model/plan.json), [исходные файлы и cleanup](./evidence/2026-09-28/embedding-worker-idle-model/result.json), [независимый replay](./evidence/2026-09-28/embedding-worker-idle-model/replay.json) сохранены. Восемь пар полных `worker.json.gz` / `store.json.gz` содержат наблюдаемые ответы и записи индекса; hashes перечислены в result.

Все **32/32** worker-ответа совпали с повторениями соответствующих входов точно; component difference и cosine distance — 0. Все **528/528** DB-векторов точно равны ответам своих вызовов. У каждого из 32 документов отдельный lease, единственный успешный completion и единственное ready event. Закрыты восемь worker и 12 helper, pipes/FD/threads восстановлены; owned PID после остановки не осталось, модель выгружена.

После idle у `retire` helper RSS равен нулю, старый процесс reaped до нового запроса. У `keep` единственный helper сохранился: sampled RSS **20,219–29,938 MiB**. Это RSS helper, не уникальная физическая память или размер весов; его диапазон не следует сравнивать как причинный эффект с прежними прогонами в другое время.

Latency второго burst в миллисекундах; каждая ячейка — **диапазон двух наблюдений** одного режима. Первый запрос и следующий тёплый вызов показаны отдельно.

| Batch | Позиция после idle | Keep, min…max | Retire, min…max |
|---:|---|---:|---:|
| 1 | Первый запрос | 42,400…71,079 | 203,186…231,232 |
| 1 | Следующий запрос | 29,898…35,150 | 24,638…25,513 |
| 32 | Первый запрос | 893,630…1146,466 | 982,555…1036,114 |
| 32 | Следующий запрос | 871,145…902,259 | 882,654…913,199 |

Цена возобновления видна, но из двух значений нельзя получить устойчивую оценку p95 или причинного startup overhead. При batch 32 диапазоны первых запросов перекрываются. Время здесь включает profile hooks и валидацию ответа обычного worker; coordinator polling и terminal COMMIT находятся за границами `embed`. Контроль сохраняет действующие defaults и явный выбор оператором idle timeout.

[12 mutation-тестов](../../../../scripts/test/test_embedding_worker_idle_verifier.py) прошли. Они отклоняют подмену модели/исходников/порогов, неизвестный COMMIT, потерю provenance, повтор lease, неправильное владение PID и cleanup. Отдельно проверена согласованная подмена одного ответа одновременно в probe, completion и DB с пересчётом hashes: парное сравнение с модельной reference всё равно её отвергает. Большие конечные компоненты с переполнением нормы также отклоняются. Verifier не импортирует текущий producer; исходники считываются из закреплённого Git commit.

[Smoke измерителя](../../../../scripts/test/test_embedding_idle_worker_probe.py) подтвердил четыре сочетания на реальном HTTP/worker/SQLite; 16 контролируемых вызовов, все сохранённые данные сверены. Журналы smoke, исходного отказа настройки harness, mutation tests и итоговых проверок находятся в [checks.json](./evidence/2026-09-28/embedding-worker-idle-model/checks.json). Модельные данные после измерения не исправлялись.

Следующий инженерный шаг — передача transport, request timeout и idle timeout через поставляемые Compose/ConfigMap. Сейчас Compose перечисляет environment явно и не передаёт эти три переменные из `.env`; Kubernetes worker и управляемые pool уже читают общий ConfigMap через `envFrom`. Использование существующего ConfigMap сохраняет один источник настройки. Изменение manifests не означает deployment; действующие кластеры/контейнеры этим экспериментом не менялись.

Итоговая проверка: benchmark прошёл отдельный TypeScript strict typecheck с настройками coordinator. `docs:check` — 12 Node + 316 Python tests, 1680 локальных ссылок в 202 Markdown-файлах, 13 process templates. Architecture audit плана — 0 ошибок и 0 предупреждений.
