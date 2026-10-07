# Meken для Android

Нативный клиент Kotlin / Jetpack Compose для Android 8.0+ (API 26). Использует общий `/v1` API: подбор с SSE, фильтры, сравнение до трёх квартир, карточки и источники, избранное, история, удаление данных. Карта открывается системным приложением без запроса геолокации и без ключа карт.

## Сборка

Нужны JDK 17, Android SDK 36 и Build Tools 35.0.0. Откройте эту папку в Android Studio или запустите:

```sh
cd android
./gradlew :core:test :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
./gradlew :app:assembleRelease :app:bundleRelease
```

Gradle 8.14.5 закреплён wrapper и официальным SHA-256 дистрибутива; скрипты и JAR wrapper взяты из того же релиза. Версии AGP, Kotlin, Compose BOM и библиотек указаны явно. `ANDROID_HOME` указывает на SDK, `JAVA_HOME` — на JDK 17. SDK-путь можно задать в игнорируемом `local.properties`. Без signing-переменных Release APK/AAB не подписаны и не готовы к публикации.

Зависимости проверены 4 октября 2026 года. Для SDK 36 закреплены последние совместимые стабильные Compose BOM `2026.06.01` (Compose `1.11.4`, Material 3 `1.4.0`), Lifecycle `2.10.0` и OkHttp `5.4.0`. Activity `1.13.0`, Coroutines `1.11.0`, Serialization `1.11.0`, Kotlin `2.3.20` и AndroidX Test `1.3.0` / Runner `1.7.0` обновлены до совместимых стабильных версий. [Матрица Kotlin/AGP](https://developer.android.com/build/kotlin-support) подтверждает поддержку Kotlin 2.3 в R8 из AGP 8.13.2; [Serialization 1.11](https://github.com/Kotlin/kotlinx.serialization/releases/tag/v1.11.0) основан на Kotlin 2.3.20.

Lint продолжает показывать напоминания о более новых Compose, Lifecycle и OkHttp. Это учтённое ограничение SDK 36: опубликованные AAR [Compose 1.12.1](https://dl.google.com/dl/android/maven2/androidx/compose/ui/ui-android/1.12.1/ui-android-1.12.1.aar), [Lifecycle 2.11.0](https://dl.google.com/dl/android/maven2/androidx/lifecycle/lifecycle-runtime-compose-android/2.11.0/lifecycle-runtime-compose-android-2.11.0.aar) и [OkHttp 5.5.0](https://repo.maven.apache.org/maven2/com/squareup/okhttp3/okhttp-android/5.5.0/okhttp-android-5.5.0.aar) требуют `minCompileSdk=37`. [Release notes Lifecycle](https://developer.android.com/jetpack/androidx/releases/lifecycle#2.11.0) дополнительно требуют обновление AGP при использовании Compose. Эти проверки остаются включёнными; переход на SDK 37 и AGP 9 требует отдельной проверки всей сборки.

Release всегда использует `https://194.238.43.134`. Локальный адрес меняется только через `mekenDebugApiBaseUrl`; он не попадает в Release. Попытка переопределить production через старое `mekenApiBaseUrl` другим адресом останавливает Release-сборку. Будущая смена домена требует явного изменения кода. Debug имеет отдельный пакет `kz.unknown.meken.debug`.

## Изолированная проверка

Для локального API на порту 8002:

```sh
./gradlew :app:assembleDebug -PmekenDebugApiBaseUrl=http://10.0.2.2:8002
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

HTTP разрешён только в Debug для `10.0.2.2`, `127.0.0.1`, `localhost`. Release использует системную проверку TLS без trust-all, пользовательских сертификатов и перенаправления авторизованных запросов.

## Подпись Google Play

Application ID: `kz.unknown.meken`, версия `1.0`, versionCode `1`. Задайте в защищённой среде все четыре переменные:

- `MEKEN_ANDROID_KEYSTORE_FILE` — абсолютный путь к upload keystore;
- `MEKEN_ANDROID_KEYSTORE_PASSWORD`;
- `MEKEN_ANDROID_KEY_ALIAS`;
- `MEKEN_ANDROID_KEY_PASSWORD`.

Ключ и пароли не сохраняются в репозитории. При наличии всех переменных Release подписывается upload key. Для обновлений увеличивайте versionCode. Play Console, Data Safety, публикация и физические устройства — отдельные шаги.

Создан отдельный локальный Meken upload key в игнорируемом `android/signing/` (каталог0700, файлы0600). Повторная подписанная сборка из корня проекта: `python3 scripts/build-android-release.py`. Helper читает закрытый release-signing.json, передаёт пароли через environment и не выводит их. `--generate-key --key-only` используется только при первом создании и отказывается перезаписывать существующий ключ. Сохраните ключ/credentials для будущих обновлений.

## Границы

- `core`: DTO, сериализация, SSE-парсер и immutable reducer без Android-зависимостей.
- `app/data`: OkHttp, координация ротации токенов, repository, атомарные зашифрованные файлы.
- `app/ui`: ViewModel / StateFlow / Compose с учётом lifecycle.
- Токены, незавершённый request и кеш избранного шифруются AES-GCM ключом Android Keystore в `noBackupFilesDir`. Записи привязаны к endpoint/пользователю и исключены из backup/device transfer.
- До POST сохраняются UUID и неизменное тело. После обрыва используются повтор с тем же ключом или replay sequence; неполный поток не считается успехом.
- Новый backend поддерживает код доступа: восстановление и независимые сессии одного профиля на iOS/Android. Профиль заменяется явно, данные не объединяются; ошибка не стирает старую сессию. Код показывается временно.
- История загружается страницами по стабильным IDs; ранние сообщения не заменяют текущие карточки/фильтры/replay cursor.
- Создание диалога сохраняет UUID, условия и первый turn до POST. Повтор на новом API не создаёт дубликат. На старом unsupported поле не отправляется; функции определяются capabilities.
- Кеш доступен для чтения без сети; ошибки не подменяются демонстрационными квартирами.

Результаты проверки: [MOBILE_ARCHITECTURE.md](../docs/MOBILE_ARCHITECTURE.md).

Opt-in read-only TLS проверка реального API на эмуляторе (только GET config): `./gradlew :app:connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.publicReadOnlySmoke=true -Pandroid.testInstrumentationRunnerArguments.class=kz.unknown.meken.PublicConfigReadOnlyTest`.
