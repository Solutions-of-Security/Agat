# Цена изолированного embedding HTTP-транспорта

Дата: **27.09.2026**. Продолжение [общего deadline и отмены HTTP](./embedding-http-deadline.md). Это ограниченный синтетический loopback-профиль клиента; model inference, coordinator и PostgreSQL в нём не участвуют.

## План до измерения

Harness и независимый verifier закреплены commit `855ab3c` **до запуска**. [Plan](./evidence/2026-09-27/embedding-transport-profile/plan.json) хранит полный commit SHA, хеши исходников, Python/платформу, порядок и параметры всех фаз. Legacy client загружается из commit `f9a16ea`; общие support-модули byte-identical. Runtime текущего клиента соответствует этапу deadline, последующее исправление TLS fixture его не меняет.

Для каждого сочетания batch **1/32**, dimensions **768/4096**, concurrency **1/4** заранее задан порядок **direct → isolated → isolated → direct**, по восемь вызовов в блоке. В каждой из 16 сравниваемых групп получается 16 наблюдений. Ещё восемь warmup-вызовов исключены из сравнения. Сервер выдаёт детерминированные ненулевые конечные векторы по формуле из плана, с обратным порядком индексов; verifier заново восстанавливает каждый payload и каждый компонент. Прямой client и subprocess получают одинаковые response bytes для каждой формы.

Отдельные шесть фаз по восемь вызовов проверяют cancel при headers/body и deadline 300 мс при медленном body, с concurrency 1/4. Event ставится после получения запроса сервером, а endpoint остаётся открытым до disconnect. Это **48** отрицательных случаев. Полный опыт — **312 запросов**: 256 измеряемых успешных + 48 отмен/таймаутов + 8 warmup; 46 фаз, **20,809 с**, macOS arm64 / Python 3.14.3.

Используется монотонное время; setup и warmup отделены, все сырые наблюдения сохранены. [Python timeit](https://docs.python.org/3/library/timeit.html#timeit.Timer.repeat) отмечает влияние соседних процессов на timing и пользу повторов. Здесь сохраняются min/p50/p95/max для HTTP-опыта, а не выводится производственный SLO из короткого microbenchmark. При n=16 nearest-rank p95 равен максимуму; это не надёжная оценка хвоста большой нагрузки.

## Наблюдаемый результат

| Dimensions | Batch | Concurrency | Direct p50, мс | Isolated p50, мс | Isolated p95/max, мс |
|---:|---:|---:|---:|---:|---:|
| 768 | 1 | 1 | 0,805 | 75,801 | 78,432 |
| 768 | 1 | 4 | 4,238 | 80,251 | 88,010 |
| 768 | 32 | 1 | 10,013 | 88,460 | 106,317 |
| 768 | 32 | 4 | 48,420 | 112,008 | 180,600 |
| 4096 | 1 | 1 | 2,439 | 77,857 | 97,262 |
| 4096 | 1 | 4 | 9,310 | 83,364 | 98,975 |
| 4096 | 32 | 1 | 50,388 | 125,342 | 139,850 |
| 4096 | 32 | 4 | 250,831 | 319,201 | 474,703 |

Разница p50 в этих сочетаниях — **63,588–78,447 мс**. Новый транспорт имеет заметную стоимость запуска; ускорение не обнаружено и не заявляется. Это цена bounded lifetime, особенно заметная на почти мгновенном fake endpoint. Из результатов нельзя получить процент overhead для реальной модели без её измерения.

Во всех 32 explicit-cancel случаях возврат с reaping занимает **19,315–46,268 мс после установки Event**. 16 deadline-случаев заканчиваются через **301,785–311,984 мс от начала вызова** при budget 300 мс; дополнительное время отражает планирование и cleanup. Все **180** subprocess завершены, returncodes соответствуют исходу, stdin/stdout закрыты. Число FD до/после каждой фазы и всей серии — **6 → 6**. HTTP retries и потерянных/дублированных результатов нет.

RSS снимается через `ps` примерно каждые 50 мс; короткие пики могут быть пропущены. Максимальная наблюдённая сумма RSS helper-процессов — **109,72 МиБ**, parent+helpers — **366,39 МиБ**. Parent включает fixture, retained evidence, проверку векторов, allocator и кратковременное хранение уже завершённых Popen для наблюдения; это **не RSS обычного worker** и не лимит памяти. На шести фазах отмен idle RSS harness менялся от **256,22 до 256,27 МиБ**. Это ограниченное наблюдение без обнаруженного роста FD/живых процессов, не доказательство отсутствия всех memory leaks.

## Проверка и решение

[Сырые результаты](./evidence/2026-09-27/embedding-transport-profile/result.json) и [независимый replay](./evidence/2026-09-27/embedding-transport-profile/replay.json) проверяют полный план, SHA исторического кода, каждое тело запроса, порядок/значения векторов, количество HTTP-попыток, интервалы и фактическую конкурентность, outcome, child exit/pipe closure, FD и принадлежность PID в RSS samples. **11 тестов verifier** отклоняют неполные фазы, скрытый retry, согласованную подмену client/server input, неправильный vector/source SHA, незакрытые pipes, unreaped process, FD growth, перепутанные сроки и чужой PID.

```sh
python3 scripts/profile-embedding-transport.py --output docs/qualification/local-decisions/performance/evidence/<date>/<new-directory>
python3 scripts/verify-embedding-transport.py docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-transport-profile
python3 -m unittest discover -s scripts/test -p test_embedding_transport_verifier.py -v
npm run docs:check
```

Проверки документации: **12 Node + 176 Python PASS**, 1561 локальная ссылка, 188 Markdown-файлов и 13 шаблонов; architecture audit — **PASS**, 0 ошибок / 0 предупреждений. Фактическая максимальная конкурентность достигла заданных 1/4 во всех фазах.

Runtime и concurrency defaults этим этапом не меняются. Отказ от изоляции вернул бы уже воспроизведённые бесконечные ожидания; reusable subprocess потребует отдельного протокола и проверок разделения запросов/отмен. Следующий gate — парное измерение direct/isolated на установленной локальной embedding-модели с теми же synthetic inputs и batch 1/32, чтобы определить долю транспортной задержки в реальном model HTTP.
