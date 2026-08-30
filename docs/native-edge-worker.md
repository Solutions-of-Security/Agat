# Native edge worker 1.6

Релиз добавляет два нативных outbound-only worker: Android service с llama.cpp и iOS app с Core ML. Оба регистрируются только после server-verified hardware attestation, получают отдельный device credential и поддерживают централизованный remote wipe. Web/PWA остаётся операторским control surface и не считается background inference runtime.

## Граница релиза

| Платформа | Inference | Выполнение | Attestation | Хранилище credential |
|---|---|---|---|---|
| Android 9+ / arm64 | pinned llama.cpp, Vulkan или CPU | user-started foreground service | Play Integrity Standard | AES-GCM key в Android Keystore; ciphertext в private preferences |
| iOS 17+ | Core ML с `computeUnits=.all`, Metal device обязателен | foreground loop и opportunistic `BGProcessingTask` | App Attest | `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` Keychain |

Native edge принимает только обычный agent stage с runtime `single/tool_loop_v1`, concurrency не выше четырёх и без MCP tools, HTTP activity или embedding job. Фактические клиенты публикуют concurrency `1`. Coordinator применяет эти ограничения независимо от заявленных клиентом capabilities, поэтому device credential не превращается в общий worker credential с доступом к secrets или side effects.

PWA не получает background inference. На iOS время запуска `BGProcessingTask` выбирает ОС, а не приложение; Apple прямо описывает background tasks как планируемую системой возможность, а не постоянный сервис. На Android foreground service требует видимое уведомление, явный пользовательский старт и соблюдение актуальных ограничений запуска. Источники: [Apple Background Tasks](https://developer.apple.com/documentation/backgroundtasks/choosing-background-strategies-for-your-app), [BGProcessingTaskRequest](https://developer.apple.com/documentation/backgroundtasks/bgprocessingtaskrequest), [Android foreground service changes](https://developer.android.com/develop/background-work/services/fgs/changes).

## Enrollment и trust

Native enrollment выключен по умолчанию. При включении coordinator требует HTTPS attestation broker, его bearer secret и точные application IDs обеих платформ.

```dotenv
AGAT_EDGE_ENABLED=true
AGAT_EDGE_ATTESTATION_MODE=broker
AGAT_EDGE_ATTESTATION_BROKER_URL=https://attestation.internal.example/v1/verify
AGAT_EDGE_ATTESTATION_BROKER_TOKEN=<secret-manager-value>
AGAT_EDGE_ATTESTATION_TIMEOUT_SECONDS=10
AGAT_EDGE_CHALLENGE_TTL_SECONDS=180
AGAT_EDGE_ANDROID_APPLICATION_ID=io.agat.edge
AGAT_EDGE_IOS_APPLICATION_ID=TEAMID.io.agat.edge
AGAT_EDGE_ALLOW_DEVELOPMENT_ATTESTATION=false
AGAT_EDGE_ANDROID_REQUIRED_VERDICTS=MEETS_DEVICE_INTEGRITY,PLAY_RECOGNIZED
AGAT_EDGE_IOS_REQUIRED_VERDICTS=APP_ATTEST_VALID
```

В Kubernetes secret должен находиться в `agat-secrets` под ключом `edge-attestation-broker-token`; остальные параметры задаются в `coordinator-configmap.yaml`. Не включайте `AGAT_EDGE_ENABLED`, пока secret, URL и оба application ID не заданы: production startup намеренно завершится ошибкой.

Enrollment проходит так:

1. Приложение отправляет bootstrap enrollment token, имя, platform и application ID только для получения challenge.
2. Coordinator создаёт 32-byte случайный challenge со сроком `30..600` секунд. SQLite хранит только SHA-256 challenge и его binding; raw challenge возвращается приложению один раз.
3. Клиент формирует точную UTF-8 строку:

   ```text
   agat-edge-attestation-v1
   <challenge-id>
   <challenge>
   <node-name>
   <application-id>
   <installation-or-app-attest-key-id>
   ```

4. Android передаёт base64url SHA-256 этой строки как Play Integrity `requestHash`; iOS передаёт SHA-256 строки как App Attest `clientDataHash`.
5. Challenge атомарно становится `claimed` до сетевой проверки. Повтор, смена имени/platform/application ID или истечение TTL дают fail-closed отказ.
6. Coordinator отправляет raw evidence только attestation broker. В SQLite и audit оно не сохраняется.
7. После строгой проверки broker возвращает короткоживущий verdict, привязанный к challenge hash, provider, application ID и key ID. Только тогда coordinator выдаёт device token.

### Контракт attestation broker

Запрос coordinator:

```json
{
  "schemaVersion": 1,
  "platform": "android",
  "provider": "play_integrity",
  "applicationId": "io.agat.edge",
  "keyId": "base64url-public-key-hash",
  "challengeId": "uuid",
  "challenge": "one-time-secret",
  "challengeSha256": "hex",
  "evidenceToken": "provider-token-or-attestation-object"
}
```

Успешный ответ:

```json
{
  "schemaVersion": 1,
  "valid": true,
  "platform": "android",
  "provider": "play_integrity",
  "applicationId": "io.agat.edge",
  "keyId": "base64url-public-key-hash",
  "challengeSha256": "hex",
  "hardwareBacked": true,
  "environment": "production",
  "issuedAt": "2026-08-30T10:00:00.000Z",
  "expiresAt": "2026-08-30T10:05:00.000Z",
  "verdicts": ["MEETS_DEVICE_INTEGRITY", "PLAY_RECOGNIZED"]
}
```

Coordinator запрещает redirects, ограничивает ответ broker 64 KiB, проверяет все binding fields, `hardwareBacked=true`, environment, обязательные verdicts и срок не больше 15 минут. HTTPS и bearer аутентифицируют broker, поэтому этот сервис является частью trusted computing base и должен иметь отдельный egress allowlist, rotation и audit.

Android broker обязан расшифровать Standard Integrity token через Google API, сравнить `requestHash`, package/application identity, licensing/app recognition и device verdicts. Standard requests имеют встроенную replay mitigation, но binding всё равно обязателен. См. [Standard Integrity flow](https://developer.android.com/google/play/integrity/standard) и [verdict reference](https://developer.android.com/google/play/integrity/verdicts).

iOS broker обязан проверить App Attest CBOR object, certificate chain до Apple App Attestation Root CA, nonce из authenticator data и `clientDataHash`, RP ID/application ID hash, key ID, AAGUID/environment и initial counter. Простого декодирования объекта или доверия полям клиента недостаточно. См. [Apple: Validating apps that connect to your server](https://developer.apple.com/documentation/devicecheck/validating-apps-that-connect-to-your-server).

## Android

Исходники находятся в `edge/android`. Production build требует Android SDK 36, NDK/CMake, JDK 17, numeric Google Cloud project number, host `glslc` из актуального Vulkan SDK/shaderc и проверенные source trees llama.cpp, SPIRV-Headers и Vulkan-Headers. NDK 27 содержит старый shaderc 2022.3 и не содержит `vulkan.hpp`, поэтому его одного недостаточно для этого релиза. Релиз закреплён на llama.cpp `b10686`, commit `3173a56471c1753650cd806694145ffd6dcace67` от 29 августа 2026; SPIRV-Headers `vulkan-sdk-1.4.357.0`, commit `29981f65241605e08b0ede4cfeb999fe3b723c6a`; Vulkan-Headers `vulkan-sdk-1.4.357.0`, commit `e3b1eec08173d6b825cd3ac88c885a63b621504a`. Список релизов и параметры Vulkan доступны в [официальном репозитории llama.cpp](https://github.com/ggml-org/llama.cpp/releases/), [build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md), [официальных SPIRV-Headers tags](https://github.com/KhronosGroup/SPIRV-Headers/tags) и [официальных Vulkan-Headers tags](https://github.com/KhronosGroup/Vulkan-Headers/tags).

```bash
git clone https://github.com/ggml-org/llama.cpp.git vendor/llama.cpp
git -C vendor/llama.cpp checkout b10686
test "$(git -C vendor/llama.cpp rev-parse HEAD)" = "3173a56471c1753650cd806694145ffd6dcace67"
git clone --branch vulkan-sdk-1.4.357.0 https://github.com/KhronosGroup/SPIRV-Headers.git vendor/SPIRV-Headers
test "$(git -C vendor/SPIRV-Headers rev-parse HEAD)" = "29981f65241605e08b0ede4cfeb999fe3b723c6a"
git clone --branch vulkan-sdk-1.4.357.0 https://github.com/KhronosGroup/Vulkan-Headers.git vendor/Vulkan-Headers
test "$(git -C vendor/Vulkan-Headers rev-parse HEAD)" = "e3b1eec08173d6b825cd3ac88c885a63b621504a"
cmake -S vendor/SPIRV-Headers -B vendor/SPIRV-Headers-build \
  -DCMAKE_INSTALL_PREFIX="$PWD/vendor/SPIRV-Headers-install"
cmake --build vendor/SPIRV-Headers-build --target install
gradle -p edge/android \
  -PagatLlamaCppDir="$PWD/vendor/llama.cpp" \
  -PagatSpirvHeadersDir="$PWD/vendor/SPIRV-Headers-install/share/cmake/SPIRV-Headers" \
  -PagatGlslcExecutable=/absolute/path/to/current/glslc \
  -PagatVulkanHeadersDir="$PWD/vendor/Vulkan-Headers/include" \
  -PagatCloudProjectNumber=123456789012 \
  -PagatEnableVulkan=true \
  :app:assembleRelease
```

Без `agatLlamaCppDir` проект собирает только fail-closed JNI stub; UI не разрешит enrollment рабочего узла. При заданном пути Gradle проверяет точный commit `3173a56471c1753650cd806694145ffd6dcace67` и отклоняет dirty/untracked source tree. Release artifact должен быть подписан обычным Android signing pipeline. Play Console должен связать package с Cloud project и разрешить используемый `specialUse` foreground-service subtype.

После установки:

1. Через **Import managed GGUF** выберите модель; приложение атомарно копирует файл не больше 64 GiB в private app storage.
2. Укажите HTTPS coordinator, уникальное имя узла, точное model name и Vulkan либо CPU.
3. Вставьте bootstrap enrollment token и нажмите **Attest & enroll**. Поле очищается до attestation и не сохраняется.
4. Запустите **Start visible worker**. Уведомление остаётся видимым всё время работы.

Vulkan — основной accelerator. CPU остаётся переносимым fallback. NNAPI только обнаруживается и публикуется как `legacy/deprecated` capability: Google объявил NNAPI deprecated начиная с Android 15, а текущий GGUF runtime не выдаётся за NNAPI backend. Для новых acceleration paths Google рекомендует иные API; источник: [Android NNAPI guide](https://developer.android.com/ndk/guides/neuralnetworks).

Во время generation отдельный control poll выполняется каждые пять секунд. При wipe JNI выставляет abort flag, проверяемый между tokens; CPU decode дополнительно использует llama.cpp abort callback. После остановки сервис удаляет credential key/ciphertext, installation key, config, managed GGUF и cache и подтверждает command сохранённым только в памяти старым token. [Pinned llama.cpp header](https://github.com/ggml-org/llama.cpp/blob/b10686/include/llama.h#L359-L363) документирует callback как CPU-only, поэтому Vulkan может завершить один уже начатый token decode перед остановкой.

## iOS

Core package и app находятся в `edge/ios`. Для app нужен полный Xcode, signing team с App Attest entitlement и XcodeGen 2.42+.

```bash
cd edge/ios
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test
xcodegen generate
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
  xcodebuild -project AgatEdge.xcodeproj -scheme AgatEdge \
  -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

Перед signing замените bundle ID `io.agat.edge` и `DEVELOPMENT_TEAM` в `project.yml`; coordinator `AGAT_EDGE_IOS_APPLICATION_ID` и поле приложения должны быть точным `TEAMID.bundle-id`. Debug использует App Attest development environment, Release — production. Production coordinator по умолчанию development verdict не принимает.

Импортируемая `.mlmodel`, `.mlpackage` или `.mlmodelc` копируется в Application Support. Модель должна иметь контракт:

- input `prompt`: `String`;
- input `max_tokens`: `Int64`;
- output `text`: `String`.

Engine требует доступный Metal device, загружает Core ML с `.all` compute units и ограничивает generation `1..512` tokens и output 1 MB. Core ML использует Metal Performance Shaders в своём execution stack: [Core ML documentation](https://developer.apple.com/documentation/CoreML).

Кнопка **Start while app is active** запускает предсказуемый foreground loop. `BGProcessingTask` требует сеть и питание, планируется не раньше 15 минут и выполняет не больше одной итерации, если iOS вообще предоставит окно. Это opportunistic дополнение, не SLA. In-flight Core ML prediction нельзя надёжно прервать внешним wipe API; coordinator уже блокирует token и результат, а локальное стирание выполняется сразу после возврата prediction/следующего heartbeat.

## Remote wipe

В разделе **Узлы** только `admin` видит доступное действие. UI требует причину и повторный ввод точного имени узла. API:

```http
POST /api/v1/nodes/:nodeId/remote-wipe
Authorization: Bearer <admin-jwt>
Content-Type: application/json

{ "reason": "INC-2041: lost device" }
```

Coordinator в одной транзакции переводит credential в `wipe_pending`, делает узел offline, повышает generation и принудительно истекает его agent/embedding leases. Новые lease, renew, events, completion, knowledge и MCP worker calls сразу получают отказ; pending token разрешён только для heartbeat/control и wipe acknowledgement. Просроченный stage возвращается в очередь обычным at-least-once recovery.

Устройство получает:

```json
{
  "schemaVersion": 1,
  "action": "wipe",
  "generation": 1,
  "requestedAt": "2026-08-30T10:00:00.000Z",
  "reason": "INC-2041: lost device"
}
```

После локального удаления клиент отправляет generation и два deletion flags. `credentialsDeleted` обязан быть `true`; затем coordinator заменяет token hash случайным недоступным значением, фиксирует `wiped/revokedAt` и больше не принимает старый token. Отозванный attestation key не может повторно оживить запись через новый bootstrap challenge.

Remote wipe не может физически стереть выключенное, уничтоженное или навсегда offline устройство. До его следующего исходящего запроса гарантируется server-side revoke и requeue, но локальная модель остаётся на storage. Для чувствительных моделей используйте OS data protection, MDM/device erase и короткий incident SLA; AGAT wipe является дополнительным, а не единственным контролем.

Audit events не содержат tokens/evidence и включают:

- `edge.enrollment.challenge_issued`;
- `edge.node.attested`;
- `edge.wipe.requested` с actor/reason/generation;
- `edge.wipe.acknowledged` с deletion flags.

## Проверка релиза

```bash
npm run typecheck
node --import tsx --test apps/coordinator/test/edge-workers.test.ts
npm test
npm run build
kubectl kustomize deploy/k8s/docker-desktop >/dev/null
plutil -lint edge/ios/App/Info.plist edge/ios/App/AgatEdge.entitlements
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift test --package-path edge/ios
```

Android release дополнительно проверяется `:app:testDebugUnitTest :app:assembleDebug` с реальным pinned llama.cpp tree и установленным SDK/NDK. iOS core проверяется `swift test`, а app — Xcode build на выбранной signing team и реальном устройстве: simulator не даёт production App Attest/Metal/device-integrity smoke test.
