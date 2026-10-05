# Отказ warmup полного совместного soak

05.10.2026 MSK. После возобновления пользователем работы [полный совместный
план](./shared-soak.md) на runtime 0.12.2 был запущен на merged main
`f0d321f39b9d7b405d8d5f888277316a9f405aed`. **Gate не выполнен:** до первого
измеряемого запроса decider вернул `inference_timeout`. Исходный план
7200 секунд / максимум 128 блоков и все failed artifacts сохранены;
никаких retries внутри прогона не было.

## Что произошло

Read-only resident preflight подтвердил ownership, точный bundle и профиль,
доступность decider и свежий scrape Prometheus. Первая primary warmup
генерация завершилась; следующий decider warmup превысил закреплённый
серверный deadline 5000 мс. Его HTTP wall time — **8123,971 мс**, включая
transport, планирование ОС и остановку дочернего процесса. Это не измерение
чистого GPU времени.

Первый блок сохранил две warmup строки и `request_failed`, без measured
фаз. Итог — **0 measured calls / 0 measured milliseconds**, два отдельных
warmup calls, один успешный и один ошибочный. Primary digest и первоначальный
профиль совпали с планом. Журнал, seals, SHA исторических source bytes и
полная привязка inputs перепроверены независимо. Полный verifier отклонил
launcher с `Launcher is incomplete or failed`, success artifact не создан.
[Публичная сводка](./evidence/2026-10-05/shared-soak-warmup-failure/result-summary.json)
содержит только исходы, числовые результаты и fingerprints.

Driver завершил три собственных временных процесса и сохранил десять
supervisor/metric samples. В его финальном log snapshot retirement event
ещё отсутствовал. **Последующее независимое наблюдение** зафиксировало
`decision.backend_retired`, reason `inference_timeout`, runtime exit 75,
child exit -15. Launchd запустил новый runtime с прежним точным профилем;
Prometheus PID не изменился. Позднее наблюдение сохранено отдельным
артефактом, исходный failed protocol не переписывается.

## Исправленная диагностика

Контроллер ранее проверял итоговую доступность health и primary **раньше**
строк с ошибками. После timeout и retirement health закономерно недоступен;
это заменяло первоначальный `request_failed` на `profile_changed`, хотя
изменение fingerprint не наблюдалось.

[Контроллер](../../../../scripts/lib/decision_shared_soak.py) теперь сохраняет
причину первого отказа из checkpoint блока прежде, чем рассматривает
последующие health/residence последствия. Gate остаётся failed. Изменение
не затрагивает serving source, deadline, модель, policy или профиль 0.12.2.

[Regression test](../../../../scripts/test/test_decision_shared_soak.py)
воспроизводит timeout в warmup и measured фазе, затем отдельно недоступный
health, изменившуюся primary identity и оба последствия вместе. Все шесть
сценариев падали до исправления и прошли после. Тест также подтверждает
сохранение checkpoint/journal и отсутствие следующего блока. Отдельный
контроль сохраняет отказ `profile_changed` при настоящем изменении health
profile без request fault. Полный целевой набор shared-load/soak — 44 tests,
PASS. `npm run docs:check` на native macOS прошёл: 566 Python tests
(четыре прежних opt-in skips), 12 Node tests, локальные ссылки и process
catalog. Sandbox-отказы socket/process tests сохранены отдельным логом;
успешный повтор использует реальный loopback и process access.

## Следующий gate

Повторить заранее закреплённый полный 7200-секундный план после native
recovery с сохранением исходного failed опыта. Новый output обязателен;
первый отказ снова останавливает нагрузку. Больше времени server inference
или иной memory policy требуют отдельного нового serving profile и
проверок; автоматически увеличивать timeout нельзя.

После отказа ОС сообщала значительные swap/compression counters. Они
сняты **после**, относятся ко всей машине и не устанавливают причинность.
[Apple](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)
описывает давление памяти через несколько показателей, включая swap rate,
wired и cached memory. [MLX unified memory](https://ml-explore.github.io/mlx/build/html/usage/unified_memory.html)
объясняет общий пул CPU/GPU. Это основание собирать системные показатели
вместе с process evidence в следующем опыте, а не объявлять память причиной
этого timeout. Источники проверены 05.10.2026 MSK.

Причина исторического внепланового exit 75, independent business data/reviews,
calibration/holdout и production SLO остаются открытыми. Routing выключен,
qualification — `not_assessed`.
