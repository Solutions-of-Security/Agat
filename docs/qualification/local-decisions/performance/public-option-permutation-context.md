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
