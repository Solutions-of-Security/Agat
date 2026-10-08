# Prospective public option-order corpus

[Original plan](../../../local-decision-model-plan-2026-09-21.md) требует
проверять переносимость при изменении порядка вариантов. [ICLR 2024 paper](https://arxiv.org/abs/2309.03882v4)
описывает selection bias у исследованных LLM: позиция option ID может
влиять на распределение. Это основание для отдельной проверки нашего
checkpoint, не доказательство такого эффекта у него. На synthetic корпусе
Агат уже измерял [order sensitivity](./robustness.md); новый diagnostic
использует весь замороженный public development split.

[Profiler](../../../../scripts/profile-public-support-permutations.py)
создаёт case-blocked recipe: все циклические rotations, distinct reverse,
затем original repeat. В пяти cyclic orders каждый semantic candidate
занимает каждую позицию один раз. Repeat получает другой transport ID,
но сохраняет original input fingerprint; его результат поможет отдельно
увидеть разброс при неизменном prompt. Fixed order и dependent variants
не считаются независимыми observations или причинным экспериментом.

State, question, descriptions, IDs и abstain flags semantic options
сохраняются; меняется только порядок candidates и opaque transport ID.
Все source cases/groups остаются в inventory, длинные inputs не
усекаются. Exact tokenizer и wrapper пересчитывают каждую разновидность
отдельно; длина после перестановки не предполагается прежней. Для original
и repeat token counts/parts должны точно совпасть с frozen context.

Native child использует pinned 34-package Python 3.13.12 / arm64,
`local_files_only=True`, `trust_remote_code=False` и offline flags. Deep
model/tokenizer artifact checks, historical original sources и current
profiler sources проверяются до/после чтения. Weights для inference не
загружаются, predictions/model calls = 0. Сокращённый denominator,
transformed source, изменённые options или labels/authority отвергаются;
output directory exclusive/private (0700/0600).

Предлагаемый следующий serial diagnostic budget закреплён до inference:
caller 10000 ms, inference deadline 5000 ms, два warmup, общий 600 s,
retry 0, без primary companion. Это local robustness observation;
calibration/holdout не токенизируются, accuracy/human review и SLO не
выводятся из option agreement. Policy, weights и routing не меняются.

## Native preflight 08.10 MSK

Из committed `b75f6dd` profiler измерил весь corpus: **49 original cases /
44 original groups**, семь orders на каждый case, **343 variants**.
**322 eligible / 21 whole context-too-long**, tokens **188–9253**.
Original и repeat token parts/counts совпали с прежним context; каждый
semantic option занял каждую позицию в пяти cyclic orders.

Независимый audit выполнил **1941 checks**: full request preservation,
case/group/order bindings, balanced positions, whole token inventory,
raw artifact SHA, complete historical/current **40/102** source pins и
private permissions. Exact pinned checkpoint/profile/34 dependencies
сохранились; model calls, predictions, human labels и calibration/holdout
tokenization — **0**. [Allowlisted summary](./public-option-permutation-context-summary.json)
закрепляет independent raw SHA, seals и prospective budget.

Шесть новых targeted tests и полный объединённый набор **877 Python /
4 optional skips, 12 Node**, links/catalog прошли. Первоначальный сбой
synthetic fixture из-за несовпадающего profile SHA сохранён вместе с
исправленными tests; native tokenizer запускался после исправления.

Private ZIP **14 entries / 631 722 bytes**, SHA
`9899a9a32f3d41d4602cba60203355604f19b94d32bc41381c3b520bffcf594e`
сохранён в исходном workspace с 0600. Обе копии проверены по CRC, каждому
entry SHA/size и двум parent archive SHA. Native receipt/audit и оба
102-file committed source snapshots сохранены.
[Archive receipt](./public-option-permutation-context-archive-summary.json).
