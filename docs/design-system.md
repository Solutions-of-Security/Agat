# Дизайн-система и visual QA

## Артефакты

- Desktop-концепт: [`design/agat-dashboard-desktop.png`](./design/agat-dashboard-desktop.png)
- Desktop render 1600×1000: [`design/agat-dashboard-rendered-desktop.png`](./design/agat-dashboard-rendered-desktop.png)
- Mobile-концепт: [`design/agat-dashboard-mobile.png`](./design/agat-dashboard-mobile.png)
- Mobile render 430×932: [`design/agat-dashboard-rendered-mobile.png`](./design/agat-dashboard-rendered-mobile.png)
- Process editor desktop-концепт: [`design/agat-process-editor-desktop.png`](./design/agat-process-editor-desktop.png)
- Process editor desktop render 1586×992: [`design/agat-process-editor-rendered-desktop.png`](./design/agat-process-editor-rendered-desktop.png)
- Process editor mobile-концепт: [`design/agat-process-editor-mobile.png`](./design/agat-process-editor-mobile.png)
- Process editor mobile render 430×932: [`design/agat-process-editor-rendered-mobile.png`](./design/agat-process-editor-rendered-mobile.png)

Концепты созданы встроенным Image Gen как `ui-mockup`. UI реализован code-native: изображения не используются внутри приложения.

## Визуальное направление

Спокойный инженерный command center: near-black navy фон, открытые таблицы и rails, тонкие divider-линии, ivory text, electric lime для активного состояния, amber для ожидания и muted slate для вторичных данных.

Основные tokens:

| Token | Значение |
|---|---|
| Background | `#07111c` |
| Deep background | `#050c15` |
| Surface | `#0b1622` |
| Text | `#f4f1e8` |
| Muted | `#919aa5` |
| Border | `#263341` |
| Active | `#d8ff35` |
| Waiting | `#ffb51b` |
| Error | `#ff6b63` |
| Info | `#58d5c8` |

Typography: system grotesk stack для UI, system monospace для telemetry. Radius ограничен 5–8 px; большие «плавающие» карточки и glassmorphism не используются.

## Компоненты

- App shell: fixed desktop sidebar, sticky topbar, main run surface, contextual node rail.
- Queue: table на desktop, touch rows и tabs на mobile.
- Stage rail: горизонтальный desktop / вертикальный mobile.
- Resource policy: radio group с реальной API mutation.
- Approval: встроенная desktop-панель / фиксированная безопасная mobile-панель над bottom nav.
- Nodes: вертикальный rail / горизонтальная touch-карусель.
- New run: native dialog с формой и agent picker.
- Icons: единый SVG stroke 1.7, `currentColor`, 24×24 viewBox.

## Fidelity ledger

| Проверка | Концепт | Browser render | Результат |
|---|---|---|---|
| Copy и IA | Исходный dashboard АГАТ, пять nav items, центр управления, очередь, цепочка, policy, approval, узлы | Все обязательные строки и порядок областей сохранены | Совпадает |
| Desktop geometry | Sidebar 220 px, topbar 68 px, queue над большим workflow canvas, right rail | Проверено при 1600×1000; detail-панель доходит до нижней рабочей границы | Совпадает |
| Palette | Navy/charcoal, ivory, lime, amber, slate; без декоративных gradients | Те же роли и значения tokens, без image overlays | Совпадает |
| Typography | Grotesk hierarchy + mono telemetry | Явные размеры для nav, controls, table, logs и node metrics | Совпадает |
| Container model | Таблица, rails и один workflow surface; не bento/card wall | Таблица и открытые dividers сохранены; cards только для nodes | Совпадает |
| Mobile IA | Header, full-width create, 3 metrics, tabs, rows, vertical stages, approval, bottom nav | Проверено при 430×932; `scrollWidth === clientWidth` | Совпадает |
| Motion/state | Active pulse и progress rail | Pulse отключается через `prefers-reduced-motion` | Совпадает |
| Core workflow | Создать run, сменить policy, approve/reject | Пройден во встроенном браузере, изменения подтверждены API | Совпадает |

Above-the-fold copy diff: обязательные desktop и mobile строки присутствуют; лишнего marketing copy нет.

Осознанное отличие: mobile approval закреплён над bottom navigation, а не только расположен в потоке. Это сохраняет подтверждение в первом viewport и не скрывает критическое действие; underlying stage list остаётся прокручиваемым.

## Расширение 0.2

Версия 0.3 сохраняет исходные tokens и command-center shell, но делит платформу на шесть рабочих представлений:

- `Обзор` — live snapshot coordinator, очередь, готовность агентов, события и инфраструктура;
- `Агенты` — каталог, поиск, создание, редактирование, `single | langgraph`, совместимость и статистика;
- `Запуски` — очередь, история, stages, SSE и approvals;
- `Процессы` — визуальный граф, инспектор шага, публикация версии и реальные экземпляры;
- `Узлы` — fleet telemetry и команда подключения worker;
- `Модели` — фактическая доступность, capacity и связи с агентами.

Rendered QA исходного dashboard выполнен во встроенном Browser при 1440×950 и 430×932. Проверены навигация, создание/редактирование агента, preset в новом запуске, выполнение dry-run worker, обновление статистики по SSE, desktop/mobile first viewport и отсутствие горизонтального overflow (`scrollWidth === clientWidth`).

## Расширение 0.3: визуальные процессы

Process editor продолжает ту же систему, но вводит отдельный canvas-слой: React Flow рисует управляемые custom nodes/edges, а инспектор и таблица экземпляров остаются обычными React-компонентами. Концепты не встраиваются в приложение как изображения.

### Fidelity ledger process editor

| Проверка | Концепт | Browser render | Результат |
|---|---|---|---|
| IA desktop | Sidebar, command bar, process library, palette, canvas, inspector, instances | Все семь областей сохранены; размеры при 1586×992: library 190 px, canvas 886 px, inspector 290 px, instances 172 px | Совпадает |
| Graph grammar | Старт, агент, condition diamond, bounded loop, end; именованные ветки | Десять исполняемых типов, `да/нет`, `повтор/выход`, выбранный loop и явная обратная связь | Совпадает и расширено |
| Safety state | Максимум итераций и amber warning в инспекторе | Значение `2` и предупреждение находятся в первом desktop/mobile viewport | Совпадает |
| Palette | Navy/deep surfaces, ivory, lime selection, amber condition, violet agent, blue loop, red end | Цветовые роли и тонкие divider-линии сохранены без gradient/image overlay | Совпадает |
| Desktop geometry | Большой рабочий canvas и фиксированный правый inspector | Grid дефект найден на первом прогоне и исправлен; canvas больше не сжимается инспектором | Совпадает после исправления |
| Mobile IA | Одна command-строка, вертикальный граф, нижняя палитра и bottom sheet | 430×932: topbar 70 px, command bar 64 px, canvas 411 px, palette 70 px, sheet 317 px | Совпадает |
| Mobile overflow | Все controls должны оставаться в 430 px | `html` и `#root`: `scrollWidth === clientWidth === 430` | Совпадает |
| Core workflow | Создать, настроить loop, сохранить, опубликовать, запустить, открыть run | Пройден через UI; экземпляр показал `2 / 2`, четыре agent stages и журнал `итерация 1 → итерация 2 → выход` | Совпадает |
| Runtime state | Версия, текущий шаг, итерация и длительность реальны | UI получил их из SQLite/API/SSE; console warnings/errors отсутствуют | Совпадает |

Осознанное отличие desktop: ветка `нет` остаётся на верхнем уровне, а `да → Редактор → Цикл` вынесена ниже. Для реального smoothstep-routing это читается лучше линейного концепта и исключает пересечение подписей `нет/да` с обратной связью.

Осознанное отличие mobile: примерный граф компактно помещает шесть шагов над sheet вместо длинного прокручиваемого canvas из концепта; палитра при этом поддерживает все десять типов (`start`, `agent`, `http`, `transform`, `wait`, `approval`, `artifact`, `condition`, `loop`, `end`). При закрытии inspector canvas расширяется с 411 до 728 px; при открытии пересчитывается fit view, поэтому выбранный loop и весь маршрут остаются видимыми.
