Установить и настроить Keycloak, включить GitOps инфраструктуры и конфигурации через Argo CD.

Замените <ЗАПОЛНИТЬ: ...> и null реальными значениями. Для доступов укажите ссылки на сохранённые секреты.

ПАРАМЕТРЫ ЗАЯВКИ
```json
{
  "schemaVersion": 1,
  "requestId": "<ЗАПОЛНИТЬ: уникальный ID заявки, 8–80 латинских букв, цифр, подчёркиваний или дефисов>",
  "environment": "pilot",
  "owner": "<ЗАПОЛНИТЬ: ответственный и способ связи>",
  "target": {
    "clusterRef": "<ЗАПОЛНИТЬ: имя/ID целевого Kubernetes-кластера>",
    "namespace": "keycloak",
    "hostname": "<ЗАПОЛНИТЬ: DNS-имя Keycloak без https://>",
    "ingressClass": "<ЗАПОЛНИТЬ: имя IngressClass кластера>",
    "tlsSecretRef": "<ЗАПОЛНИТЬ: ссылка на TLS-сертификат Keycloak>",
    "clusterCredentialRef": "<ЗАПОЛНИТЬ: ссылка на сохранённый доступ к кластеру>"
  },
  "versions": {
    "keycloak": "<ЗАПОЛНИТЬ: совместимая версия keycloak в формате X.Y.Z>",
    "operator": "<ЗАПОЛНИТЬ: совместимая версия operator в формате X.Y.Z>",
    "configCli": "<ЗАПОЛНИТЬ: совместимая версия configCli в формате X.Y.Z>",
    "argoCd": "<ЗАПОЛНИТЬ: совместимая версия argoCd в формате X.Y.Z>"
  },
  "resources": {
    "replicas": 1,
    "cpuRequestMillicores": 1000,
    "cpuLimitMillicores": 2000,
    "memoryRequestMiB": 2048,
    "memoryLimitMiB": 3072
  },
  "database": {
    "host": "<ЗАПОЛНИТЬ: адрес PostgreSQL, доступный из кластера>",
    "port": 5432,
    "name": "keycloak",
    "credentialsSecretRef": "<ЗАПОЛНИТЬ: ссылка на логин/пароль PostgreSQL>",
    "tlsCaSecretRef": "<ЗАПОЛНИТЬ: ссылка на CA для TLS PostgreSQL>",
    "highAvailability": false,
    "backupDestinationRef": "<ЗАПОЛНИТЬ: ссылка на хранилище резервных копий>",
    "rpoMinutes": null,
    "rtoMinutes": null
  },
  "git": {
    "repoUrl": "<ЗАПОЛНИТЬ: HTTPS URL Git-репозитория без токена>",
    "branch": "main",
    "path": "environments/pilot/keycloak",
    "credentialRef": "<ЗАПОЛНИТЬ: ссылка на сохранённый доступ к Git>"
  },
  "gitops": {
    "controller": "argocd",
    "namespace": "argocd",
    "application": "keycloak-pilot",
    "credentialRef": "<ЗАПОЛНИТЬ: ссылка на сохранённый доступ к Argo CD>",
    "selfHeal": true,
    "pruneData": false,
    "configurationReconcileSeconds": 300
  },
  "identity": {
    "realm": "<ЗАПОЛНИТЬ: имя нового realm, не master и не agat>",
    "clients": [
      {
        "clientId": "<ЗАПОЛНИТЬ: ID приложения; добавьте отдельный объект для каждого client>",
        "type": "public-browser",
        "redirectUris": [
          "<ЗАПОЛНИТЬ: точный HTTPS callback приложения без wildcard>"
        ],
        "roles": [
          "<ЗАПОЛНИТЬ: роль приложения; [] если роли не требуются>"
        ]
      }
    ],
    "adminMfaRequired": true,
    "registrationAllowed": false,
    "passwordGrantAllowed": false
  },
  "acceptance": {
    "testClientId": "<ЗАПОЛНИТЬ: clientId браузерного приложения из identity.clients>",
    "testCredentialRef": "<ЗАПОЛНИТЬ: ссылка на тестовую учётную запись>",
    "callbackUrl": "<ЗАПОЛНИТЬ: HTTPS callback из redirectUris тестового клиента>",
    "peakLoginsPerSecond": null,
    "p95LoginMilliseconds": null,
    "allowedErrorRate": 0.01
  }
}
```

ПОЯСНЕНИЯ К ПАРАМЕТРАМ
Если инфраструктуры ещё нет, сначала подготовьте её; неизвестные адреса и доступы не придумывать. Пароли, токены и kubeconfig сюда не вставлять.
Предзаполнен pilot: 1 реплика, request 1 CPU / 2 GiB, limit 2 CPU / 3 GiB. Это стартовый бюджет для проверки нагрузкой. Для production-ha укажите минимум 2 реплики на разных узлах, HA PostgreSQL и измените Git path/Application под окружение.
Заполните числа вместо null: database.rpoMinutes — допустимая потеря данных в минутах; database.rtoMinutes — время восстановления в минутах; acceptance.peakLoginsPerSecond — пиковая частота входов; acceptance.p95LoginMilliseconds — предельный p95 входа в миллисекундах. allowedErrorRate=0.01 означает 1% ошибок.
В clients добавьте реальные приложения. type: public-browser, confidential-web или service. Для service используйте redirectUris: []; тестовый OIDC client должен быть браузерным. Закрепите совместимые версии, latest не использовать.

КРИТЕРИЙ ГОТОВНОСТИ
Keycloak доступен по HTTPS; вход, MFA администратора и роли проверены. Git содержит закреплённую конфигурацию без секретов. Argo CD синхронизирует инфраструктуру, отдельная reconciliation обновляет realm/clients. Подтверждены устранение drift, повторное применение без лишних изменений и восстановление из резервной копии. Недостающие параметры или проверки означают BLOCKED, а не успешную установку.
