# Резервное копирование PostgreSQL

Проверено 4 октября 2026 года. На production `my-personal` при read-only проверке отсутствовал `meken-backup.timer`. Код автоматизации подготовлен в репозитории; установка таймера и внешний bucket пока не выполнены. Пустой destination/recipient намеренно блокирует production backup. Подготовка скрипта не означает, что резервное копирование уже работает на сервере.

6 октября перед разрешённой выкаткой каталога сделана отдельная consistent серверная копия и фактическое восстановление всех 15 таблиц в изолированном PostgreSQL 17. Свежий retained directory: `/opt/meken/backups/retained/catalog-20261005-124213-rehearsal-3`. Проверены исходный inventory, ownership/default grants, `0006 → 0009`, restricted runtime и совместимый rollback image. Dump хранится закрыто на сервере, дополнительно создан encrypted OpenSSL CMS archive; private key остаётся на Mac. Production payload на Mac не передавался: автоматическая проверка отклонила эту передачу/расшифрование. Это разовый predeployment backup, а не установленное S3/age расписание; offsite backup и timer по-прежнему не настроены. [Подробности](DEPLOYMENT.md).

## Как устроена проверка

`scripts/meken-backup.py` выполняет следующие шаги:

1. Проверяет обязательные S3 destination/public age recipient, зависимости, отсутствие параллельного backup, свободный диск и накопившиеся неудачные попытки. По умолчанию требуется минимум 1 GiB и тройной размер исходной БД; при трёх `pending` новый dump не начинается.
2. Находит production DB через оба Compose-файла и `/opt/meken/shared/.env`, использует `meken_admin`. Секреты `.env` не копируются в архив и не выводятся в лог.
3. Открывает read-only PostgreSQL `REPEATABLE READ`, экспортирует snapshot, собирает перечень/число строк таблиц, columns/defaults, constraints/FK, indexes и Alembic revision. `pg_dump -Fc --compress=gzip:6 --snapshot` читает тот же snapshot, поэтому конкурентная запись API не создаёт ложное несовпадение счётчиков.
4. Восстанавливает dump в новом PostgreSQL-контейнере по точному image ID исходного контейнера. У него отдельный временный volume, `--network=none`, нет опубликованных портов и source mounts. `pg_restore --single-transaction --exit-on-error --no-owner --no-acl` и повторный inventory должны совпасть. Временные container/volume удаляются; ошибка cleanup блокирует успех.
5. Упаковывает dump и подробный inventory, шифрует `age` по публичному recipient, считает SHA256. Plaintext удаляется после появления encrypted payload. Production архив содержит `backup.tar.age`, `manifest.json`, `SHA256SUMS`, `COMPLETE.json`; до завершения он лежит в закрытом `pending`.
6. Загружает каждый файл в отдельный UUID prefix S3, скачивает обратно с проверкой TLS/checksum и сравнивает фактический SHA256. `COMPLETE.json` загружается последним. Только после проверки записывает локальный `UPLOADED.json` и атомарно переносит каталог в `daily` или `retained`.
7. После успешной свежей копии удаляет завершённые daily backups старше retention, по умолчанию 30 дней, локально и в том же S3 prefix. Не затрагивает другие objects, незавершённые/неизвестные каталоги и отдельно сохранённые `retained`.

Имена, число строк, схема и migration state проверяются; это не побайтовое сравнение каждого значения исходной таблицы. Файловый checksum защищает выгрузку/передачу. PostgreSQL restore проверяет создание schema/data/FK/indexes. Отдельный запуск приложения на восстановленной БД и проверка новых миграций нужны перед развёртыванием.

## Внешнее хранилище и ключ

Нужно заранее выбрать отдельное от `194.238.43.134` S3/S3-compatible хранилище, bucket и непустой prefix. Скрипт поддерживает **unversioned dedicated bucket**. При `Enabled`/`Suspended` versioning он останавливается: обычный `DeleteObject` оставляет старые версии пользовательских данных и не соблюдает retention. S3-compatible provider должен поддерживать AWS CLI v2 checksum upload/download и `get-bucket-versioning`.

Настроить lifecycle bucket для удаления незавершённых multipart uploads. Bucket не должен быть публичным. У credentials нужны только доступ к выделенному prefix (`GetObject`, `PutObject`, `DeleteObject`, `ListBucket` с prefix) и проверка versioning (`GetBucketVersioning`). Credentials для этого bucket не передаются мобильным клиентам.

`MEKEN_BACKUP_S3_ENDPOINT` допускает явный HTTPS origin без credentials/query/path. Endpoint, разрешающийся в localhost/production host, отклоняется. Для AWS CLI принудительно включён `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true`, поэтому случайный `AWS_ENDPOINT_URL`/profile endpoint не превращает локальный S3 в «внешний backup». Проверенный явный `--endpoint-url` остаётся разрешён. [AWS endpoint precedence](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-endpoints.html).

На отдельном доверенном компьютере создать age key:

```sh
umask 077
age-keygen -o meken-backup-identity.txt
age-keygen -y meken-backup-identity.txt
```

На сервер передаётся только public recipient. Private key нужно сохранить отдельно от сервера и проверить восстановление по нему на другом компьютере. Потеря private key делает encrypted dumps недоступными. `--verify-archive` позволяет провести такой drill; успешное шифрование по recipient само по себе не доказывает, что private key сохранён.

## Установка после выбора destination

Зависимости на сервере: Python 3.10+, Docker/Compose, `age`, **AWS CLI v2** в `/usr/bin` или стандартном systemd PATH. Version `pg_dump`/`pg_restore` берётся из PostgreSQL container, а не из случайного host client. Не повторять bootstrap production и не пересоздавать основной volume.

До установки задать destination/recipient/credentials по `infra/meken-backup.env.example`. Файл backup конфигурации и AWS credentials должны находиться в shared, права `0600`, owner root. Сервис читает credentials из env или явно заданного `AWS_SHARED_CREDENTIALS_FILE=/opt/meken/shared/backup-credentials`; `ProtectHome=true` закрывает неявные credentials из `/root/.aws`, а EC2 metadata credentials отключены. Значения со spaces заключать в quotes. `--config` читает assignments без shell evaluation.

После установки проверенных зависимостей, готового release и конфигурации:

```sh
sudo install -d -m 0700 /opt/meken/backups
sudo install -m 0600 infra/meken-backup.env.example /opt/meken/shared/backup.env
# Заполнить /opt/meken/shared/backup.env до выполнения следующей команды.
sudo python3 /opt/meken/current/scripts/meken-backup.py --config /opt/meken/shared/backup.env --keep

sudo install -m 0644 infra/meken-backup.service /etc/systemd/system/meken-backup.service
sudo install -m 0644 infra/meken-backup.timer /etc/systemd/system/meken-backup.timer
sudo systemd-analyze verify /etc/systemd/system/meken-backup.service /etc/systemd/system/meken-backup.timer
sudo systemctl daemon-reload
sudo systemctl start meken-backup.service
sudo systemctl status meken-backup.service --no-pager
sudo journalctl -u meken-backup.service --no-pager -n 50
sudo systemctl enable --now meken-backup.timer
sudo systemctl list-timers meken-backup.timer --all --no-pager
```

Сначала подтвердить `BACKUP SUCCESS` и выполнить отдельное offsite decrypt/restore; затем включать timer. Таймер запускается ежедневно в 03:15 UTC (08:15 Asia/Almaty) со случайной задержкой до 15 минут; `Persistent=true` выполняет пропущенный запуск после включения сервера. `--keep` создаёт отдельную свежую копию перед изменением схемы вне daily rotation.

## Проверка существующего encrypted archive

На отдельном компьютере скачать `backup.tar.age`, `manifest.json`, `SHA256SUMS`, `COMPLETE.json` из одного завершённого prefix. Проверить `sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`). Не объединять файлы разных копий. Установить PostgreSQL 17 image и `age`; ключ и work directory — private.

```sh
docker pull postgres:17-bookworm
install -d -m 0700 /private/tmp/meken-recovery
MEKEN_BACKUP_ROOT=/private/tmp/meken-recovery python3 scripts/meken-backup.py \
  --verify-archive /absolute/path/backup.tar.age \
  --identity /absolute/path/meken-backup-identity.txt \
  --restore-image postgres:17-bookworm
```

На Linux выбрать абсолютный закрытый путь вместо `/private/tmp/meken-recovery`. Скрипт извлекает только `database.dump` и `inventory.json`, отклоняет symlink/неожиданные archive members и снова сравнивает восстановленные counts/schema/migrations. Эта команда не открывает source DB и не принимает адрес/name production restore target. Восстановление самого сервиса после аварии — отдельная операция: в новую подготовленную БД с production ролями/grants, проверкой миграций и API; переключение выполняется после проверки. Существующие роли/секреты/Nginx/TLS не входят в single-database dump и остаются отдельно защищённой конфигурацией.

## Неудачные и отдельно сохранённые копии

При ошибке нет сообщения `BACKUP SUCCESS` и нет подтверждённого локального `UPLOADED.json`. Pending может содержать plaintext dump: права каталогов `0700`, файлов `0600`; не публиковать их через Nginx и не включать в Git. После подтверждённого восстановления/повторной копии оператор удаляет только конкретные устаревшие pending directories. Скрипт останавливается при достижении backlog limit, чтобы outage внешнего хранилища не заполнил диск новым dump каждый день.

`retained` не удаляется автоматически. После завершения окна отката удалить выбранную копию локально и в соответствующем S3 `retained` prefix, учитывая установленную политику данных. Срок отдельного хранения нужно назначить явно; не оставлять пользовательские данные бессрочно. Для daily задан максимум 90 дней, default 30. Не запускать rotation при clock/timezone проблемах или повреждении metadata без разбирательства. Ошибка rotation возвращает failed run даже если новая свежая копия уже загружена — её UUID каталог остаётся доступен для проверки.

## Локальные проверки кода

```sh
python3 scripts/test-backup.py
python3 scripts/test-backup.py --integration
python3 scripts/test-backup.py --integration --require-age
```

Одиннадцать safety tests проверяют обязательные destination/encryption, запрет случайного upload в local-only режиме, опасные пути/credentials, конфигурацию без shell execution, private permissions/symlinks, lock, backlog, блокирование унаследованного локального AWS endpoint, safe rotation, versioned bucket rejection и checksum mismatch. Integration использует настоящие одноразовые PostgreSQL 17 containers: 4 таблицы/7 строк, FK/indexes/migration state, конкурентную запись после export snapshot, восстановление согласованного snapshot и отклонение изменённого inventory. Также проходит полный `--local-only` pipeline с private files и явным `LOCAL REHEARSAL ONLY`.

С реальными `age`/`age-keygen` 1.3.2 выполнены шифрование, восстановление archive по отдельному временному private key, совпадение PostgreSQL inventory и отклонение повреждённого ciphertext. `--require-age` делает отсутствие encryption tools ошибкой проверки. Ключ тестовый и удалён вместе с изолированным окружением. Эти прогоны не подтверждают реальную S3 передачу, сохранность ключа владельца или работающее production расписание.

PostgreSQL гарантирует consistent `pg_dump` и поддерживает custom compressed archives/shared snapshots: [pg_dump 17](https://www.postgresql.org/docs/17/app-pgdump.html), [transaction snapshot](https://www.postgresql.org/docs/17/sql-set-transaction.html). Проверка передачи S3 использует официальные checksum options: [AWS CLI cp](https://docs.aws.amazon.com/cli/latest/reference/s3/cp.html). Шифрование: [age](https://github.com/FiloSottile/age).
