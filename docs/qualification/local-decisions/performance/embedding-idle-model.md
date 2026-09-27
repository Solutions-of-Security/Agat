# Модельный burst → idle → burst

Дата: 27.09.2026. Дизайн зафиксирован до измерения. Следующий этап после [idle retirement prototype](./embedding-idle-prototype.md).

Сравниваются production `EmbeddingSessionPool` (`keep`) и `IdleEmbeddingSessionPool` из `scripts/lib` (`retire`), оба используют текущий guarded helper. Модель `embeddinggemma:latest`, digest `85462619ee721b466c5927d109d4cb765861907d5417b9109caebc4e614679f1`, dimensions 768. Отдельный owned Ollama, cloud выключен, parallel 1, context 2048, keep-alive 5 минут. Model server не выгружается между фазами: проверяется стоимость helper, а не повторная загрузка весов.

Матрица batch 1/32 × concurrency 1/4, порядок keep/retire/retire/keep. Каждая фаза: восемь embedding-вызовов, четыре секунды простоя, ещё восемь вызовов. Idle timeout прототипа — три секунды, только для qualification. Перед матрицей два отдельных прогревочных вызова batch 1/32. Всего 258 model-вызовов, из них 256 измеряемых; ожидается 62 helper, включая два одноразовых warmup helper.

Первые запросы каждого actor после idle анализируются отдельно от последующих: медиана смешанного burst могла бы скрыть startup. Startup входит в deadline и клиентскую latency. Дополнительно фиксируются реальные границы transport request, чтобы проверять отсутствие перекрытия владения одним helper. Общая `LocalModelClient.embed` latency также содержит декодирование/валидацию vectors после возврата слота, поэтому её интервалы не используются как границы владения transport.

Одинаковые восемь наборов входов, закреплённые source hashes, полные сжатые vectors, input hashes, dimensions/finite checks, component tolerance 1e-6 и cosine tolerance 1e-10. Hashing/compression и запись файлов выполняются вне всех timed calls. До/после idle и после close фиксируются PID, reap/pipes, FD и RSS. Ни одного живого helper после idle у retire; keep должен повторно использовать прежние PID. Каждый PID принадлежит одному запросу transport в момент времени.

Эксперимент не оценивает качество модели, производственный timeout или SLO. Короткий ABBA на одном Mac не устраняет все эффекты очереди, CPU/теплового режима и модельной нагрузки. Production worker defaults не меняются.

## Отказы после повторного запуска

Отдельные lifecycle-тесты используют контролируемый HTTP-body: после подтверждённого idle reap следующий helper получает deadline либо cancellation, закрывает все pipes до ответа endpoint, затем третья явная попытка успешно обрабатывается новым helper. Это реальный transport/process тест, но модельный ответ в этих двух fault-сценариях является fixture. Сам benchmark измеряет успешные ответы настоящей модели.

## Воспроизведение

После commit измеряемых исходников:

```sh
python3 scripts/profile-embedding-idle-model.py --output docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-model
python3 scripts/verify-embedding-idle-model.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-model
python3 -m unittest discover -s scripts/test -p test_embedding_idle_pool.py -v
```

Повторный experiment требует нового каталога. После измерения owned model явно выгружается, сервер/runner останавливаются, отсутствие оставшихся PID проверяется. Стандартный локальный порт Ollama пользователя не используется.
