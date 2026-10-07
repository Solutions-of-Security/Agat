# Owner/SLO: предложение для shadow-пилота

Draft, 07.10.2026. Владельцы и targets ещё не подтверждены. Сейчас
shadow-only, routing false, not_assessed. После
инженерных Temporal/RAG, rollout, crash-loop и login collector gates нужны
владелец runtime/release, владелец бизнес-сценария и пользовательский поток.
Фактический новый boot/login пока не наблюдался.

Предлагаю начать с семи дней наблюдения реального shadow-потока на
проверенном M1 Max / 32 GiB, [sealed profile](../../performance/profiles/runtime-0.12.3-wired-4096.json), wired 4096 MiB,
input ≤ 2048 tokens, один inference slot. Начальный planning envelope —
один запрос в секунду, один активный запрос; это ограничение пилота для
обсуждения, не утверждение о существующем клиентском потоке.

| Показатель | Предлагаемый target | Граница измерения |
|---|---|---|
| Валидное исполнение shadow | ≥ 99% eligible attempts | Caller получил bound result ok/abstain в пределах согласованного caller timeout; busy, unavailable, timeout, late result и malformed response остаются в denominator |
| Local HTTP latency | ≥ 95% eligible attempts ≤ 5000 ms | От входа LocalDecisionClient.decide до возврата после response parsing и transport cleanup; failed/missing attempts остаются в accounting |
| Recovery guard | Каждый контролируемый native recovery ≤ 60 s | Подтверждённый отказ собственного child → matching ready и свежий scrape; это engineering guard, не customer SLO |

Eligible означает заранее соответствующий контракту/профилю реальный
shadow-вызов из выбранного workflow. Diagnostic/warmup/fixture calls
отдельны. Safe replay не является новым HTTP attempt. Нулевая denominator
и отсутствие caller records дают insufficient_data; метрики runtime не
позволяют отделить их сами. Deadline модели 5000 ms не является caller
deadline; предлагаемый pilot caller timeout — 10000 ms. Lease renewal и
запись observation не входят в local HTTP boundary и требуют отдельного
измерения полного workflow. Targets могут быть
изменены владельцем до фиксации пилота.

Самый длинный measured decider call полного shared soak — 3220,066 ms;
pooled phase p95 — до 800,630 ms. Четыре diagnostic calls crash gate
включали первый 4153,527 ms, recovery — 17918,213–26497,360 ms. Это
различные ограниченные инженерные нагрузки с повторяемыми inputs;
они не подтверждают предложенный SLO на клиентских задачах.

Runtime/release owner отвечает за profile/package, resource limits,
наблюдение, stop/rollback и разбор incident. Business owner фиксирует
сценарий, поток, цену ошибки, targets и exclusions. ML/QA далее организуют
два независимых human reviews, calibration и holdout; эта подпись не
заменяет их. Reviewer IDs и approvals остаются пустыми до прямого ответа.

При исчерпании budget или недостоверных данных закрывается расширение
shadow-пилота, разбирается отказ и при необходимости выключается shadow
или возвращается verified package. Primary/fallback остаются доступными
по прежнему контракту. Routing не включается этим операционным gate.

После ответов нужно закрепить scope/owner/targets до новых измерений,
сохранить caller-level evidence, проверять good/total, полноту и resets,
затем сопоставить результат с согласованными targets. Prometheus хранит
7 дней/256 MiB с мягкой size retention: самостоятельного 28-day SLO из
этой конфигурации нет.

Методическая основа: [Google SRE Implementing SLOs](https://sre.google/workbook/implementing-slos/) — stakeholder agreement,
good/total и error budget policy; [Service Level Objectives](https://sre.google/sre-book/service-level-objectives/) — явные границы
и связь с пользовательскими ожиданиями. Предложенные значения выбраны
для обсуждения по native evidence и ограничениям данного runtime.

Источники измерений: [shared soak](../../performance/wired-shared-soak-7200-0.12.3.md)
и [native crash gate](./resident-crash-loop.md). Они показывают ограниченные
engineering workloads; согласование user-facing SLO на них не выполнено.

[Controlled arrivals](../../performance/arrival-rate-4096.md), 07.10: два отдельных wired 4096 МиБ runtime на 2048 tokens дали 24/24 при 1 arrival/с, p95 874.733 мс; при 2 arrivals/с — 50% computed, с явными drops/busy. Краткий synthetic опыт добавляет данные к planning envelope; owners, real traffic, targets и burst policy по-прежнему не согласованы.
