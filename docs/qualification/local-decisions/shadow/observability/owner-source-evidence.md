# Публичные источники о владельцах пилота

08.10.2026 MSK. Запрос поиска owners в интернете проверен по первичным GitHub
источникам. Публичный [репозиторий Agat](https://github.com/Solutions-of-Security/Agat)
принадлежит организации Solutions-of-Security. В
[CODEOWNERS на immutable main commit](https://github.com/Solutions-of-Security/Agat/blob/1902c849063c23a5b34ed62349a872d2258e3a35/docs/CODEOWNERS)
указан единственный public maintainer `@TitanUser`, правило для всех файлов.

REST contents API вернул blob `001b15a6c22a8d5ca6bb3f9a7a59abc0848e907d`;
decoded bytes совпали с Git object закреплённого commit. Другого CODEOWNERS
в `.github`/корне этого commit нет; codeowners/errors вернул пустой список.
Отдельный authenticated permission query подтвердил `admin` для TitanUser.
Этот endpoint требует авторизованного доступа и не выдаётся за анонимный
публичный источник. Raw responses/SHA сохранены privately; token не записывался.

[Правила GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
подтверждают размещение в `docs`, порядок выбора файла и назначение code review.
Поэтому **кандидат на сопровождение runtime/release — @TitanUser**.
Operational/on-call appointment и согласование targets этими сведениями не
установлены. Commits не подтверждают предметную квалификацию reviewer или
владение клиентским бизнес-сценарием.

| Потребность | Evidence и состояние |
|---|---|
| Runtime/release | CODEOWNERS/Git/API: кандидат @TitanUser найден; operational appointment не подтверждён |
| Business owner | Назначение в изученных repository/codeowner источниках не найдено |
| Review material | [129 публичных IT-вопросов](../../source-review/public-support/README.md), explicit CC BY-SA 4.0 и attribution; не трафик клиента Agat |
| Permitted project/process/version | [Prospective contract](./shadow-pilot-plan.md) требует конкретные IDs; реальный workflow не выбран по этим источникам |
| SLO / предметные reviewers | CODEOWNERS этого не устанавливает; human reviews 0 |

Рекомендация: рассматривать @TitanUser как подтверждённого public maintainer
для обсуждения runtime-роли, public corpus — как материал для blind review.
Назначение бизнес-владельца, разрешение клиентских данных и две предметные
human reviews остаются отдельными gates. Сообщений сопровождающему не
отправлялось. Blank config не заполнялся выдуманными IDs/ролью;
agreement/SLO/routing false, qualification not_assessed.
