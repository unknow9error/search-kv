# Развёртывание Meken

Production обновлён и проверен 6 октября 2026 года. Сервер: SSH alias `my-personal`, Ubuntu 24.04, IP `194.238.43.134`. Исторические проверки первоначального релиза ниже относятся к 2 октября.

## Работающая конфигурация

- API: `https://194.238.43.134`, `MEKEN_ENV=production`.
- Релиз: `/opt/meken/releases/catalog-20261005-124213`, текущий путь — `/opt/meken/current`; дата в идентификаторе относится к подготовке snapshot, переключение выполнено 6 октября.
- Образ: `meken-api:catalog-20261005-124213`, ID `sha256:f98f8ab8d7ff4922dc46b254e2298d263b8fddbed293e53ef8116f5b0c413f04`. Передан проверенный snapshot рабочего дерева без `.env`, SQLite, кешей и мобильных артефактов.
- Конфигурация и новые серверные секреты: `/opt/meken/shared/.env`, права `0600`. Локальные `.env` и SQLite не переносились.
- Compose: `compose.yml` + `infra/compose.production.yml`. PostgreSQL 17 и Redis 7.4 находятся во внутренней Docker-сети; API опубликован только на `127.0.0.1:8001`.
- Приложение работает под ролью `meken_app` с DML-правами; миграции — под `meken_migrator`. Обе роли без superuser/CREATEDB/CREATEROLE. Схема Alembic — `0009_project_catalog`, данные — в постоянных Docker volumes.
- Nginx: `/etc/nginx/sites-available/meken`. HTTP перенаправляется на HTTPS; SSE не буферизуется. `/metrics`, `/docs`, `/openapi.json`, `/health/*` снаружи закрыты.
- Сертификат: Let’s Encrypt для IP, `/etc/letsencrypt/live/194.238.43.134/`. Certbot 5.4.0 установлен в `/opt/meken-certbot`.
- Автозапуск: `meken.service`, Docker и Nginx. Продление сертификата проверяется `meken-certbot.timer` каждые четыре часа; при продлении выполняются проверка и reload Nginx.
- Источник: реальный BI Group. ИИ выключен: серверный ключ модели не задан. Демонстрационный источник не используется.
- Страницы о данных и работе тестового сервиса: `/privacy`, `/terms`. При изменении ИИ, хранения данных или операторских процессов обновить их в `infra/public/`.

## Проверки

- Внутренний `/health/ready` вернул `ready`; HTTPS проверен без отключения проверки сертификата.
- Тестовое продление IP-сертификата через Let’s Encrypt staging и deploy hook с проверкой/reload Nginx прошли; после reload публичный API отвечает. Certbot работает неинтерактивно; таймер задаёт случайную задержку, поэтому собственная длительная пауза Certbot отключена.
- `scripts/smoke-deployed.py`: 44 реальных предложения BI Group для двухкомнатной квартиры в Астане до 35 млн тенге. Первое событие пришло за 0,382 с, первые карточки — за 0,504 с, весь подбор — за 2,403 с. Эти времена относятся к одному прогону.
- Подтверждение помещения вернуло `confirmed`, избранное сработало; временный пользователь удалён, его токен после удаления получил 401.
- `/v1/config` вернул `mode=live`, `ai_enabled=false`, пять городов и публичные HTTPS-ссылки.
- Локальные тесты бэкенда: 37 passed, 1 skipped. Release-сборка iOS без подписи прошла; собранный Info.plist содержит `MekenAPIBaseURL=https://194.238.43.134`, Bundle ID `kz.unknown.meken`, версию `1.0`.
- Существующий `cevsen-bot.service` продолжает работать. Физический iPhone и загрузка в TestFlight в этой проверке не участвовали.

## Управление

```sh
ssh my-personal
sudo systemctl status meken nginx meken-certbot.timer
cd /opt/meken/current
sudo docker compose --env-file /opt/meken/shared/.env -f compose.yml -f infra/compose.production.yml ps
curl --fail http://127.0.0.1:8001/health/ready
sudo journalctl -u meken --no-pager -n 100
sudo /opt/meken-certbot/bin/certbot renew --dry-run --no-random-sleep-on-renew --run-deploy-hooks --cert-name 194.238.43.134
```

При обновлении сохранять предыдущий релиз и образ, отдельно делать резервную копию БД и проверять совместимость миграций. Не повторять `bootstrap-production.py` поверх существующей конфигурации и не удалять Docker volumes. PostgreSQL init script применяется только к новому пустому volume. Остановка `sudo systemctl stop meken` сохраняет volumes. Для схемы `0009` подготовлен совместимый откатный релиз, описанный ниже: первоначальный образ `r2` требует head `0006` и напрямую после обновления не запустится.

Автоматические резервные копии и внешнее хранение здесь ещё не настроены. Перед использованием сервиса с важными пользовательскими данными настроить их по OPERATIONS.md и проверить восстановление.

Для передачи исходников с macOS использовать `COPYFILE_DISABLE=1 tar --no-xattrs --exclude='._*'` и явный список файлов. `backend/.dockerignore` дополнительно исключает служебные файлы macOS, кэш, тесты и секреты из Docker build context.

Debug и Release iOS по умолчанию обращаются к этому серверу. Для локальной разработки можно переопределить `MEKEN_API_BASE_URL=http://127.0.0.1:8001` в Debug. TestFlight требует отдельного подписанного архива и загрузки в App Store Connect.

## Подготовлено 4 октября

Release iOS/Android закреплён на `https://194.238.43.134`, другой адрес отклоняется. Подписанные Android APK/AAB и App Store IPA готовы. Backend миграции 0007/0008 добавляют durable creation и восстановление/перенос профиля; 71 локальный PostgreSQL/Redis тест проходит.

На 4 октября обновление VPS ещё не было применено. Automatic approval отклонил полный dump на Mac без прямого разрешения; тогда была проверена только metadata и live схема `0006`. [Исторические результаты](MOBILE_ARCHITECTURE.md). Разрешённое пользователем обновление и серверная backup/restore репетиция выполнены 6 октября.

Backup runner/timer проверены локально, production timer не установлен/внешнее хранилище не настроено. Нужны S3 destination, credentials, public recipient по [BACKUPS.md](BACKUPS.md). До обновления клиенты используют capabilities и старый контракт.

## Production каталог ЖК — 6 октября

Свежая consistent копия `0006` сохранена отдельно на сервере. Полный dump восстановлен в одноразовом PostgreSQL 17 по точному image ID source; inventory таблиц/строк/columns/constraints/indexes/revision совпал. На восстановленной базе роли `meken_migrator`/`meken_app` имеют реальные ограничения и ownership/default grants. Прогон `0006 → 0009`, `alembic check`, startup, DML, каталог/карта/favorites и удаление временного профиля прошли. После репетиции все её контейнеры и volumes удалены.

Свежая retained копия: `/opt/meken/backups/retained/catalog-20261005-124213-rehearsal-3`; 15 исходных таблиц / 19 405 строк. Каталог закрыт правами `0700`, dump — `0600`; дополнительно создан `backup.tar.cms`, зашифрованный публичным RSA recipient через OpenSSL CMS. Private recovery key находится отдельно на Mac в игнорируемом закрытом `backups/catalog-20261005-124213/recovery-key.pem`. Production payload на Mac не копировался: автоматическая проверка запретила эту передачу и расшифрование. Это разовая серверная копия, не работающий offsite backup/timer. В metadata отмечено отдельное хранение до 5 ноября; автоматическое удаление не настроено.

В рабочей БД миграции выполнены как `meken_migrator`; DML-права `meken_app` подтверждены. `current` и только `MEKEN_IMAGE_TAG` в существующей shared-конфигурации переключены на новый релиз. API и worker пересозданы; основные PostgreSQL/Redis volumes, Nginx и TLS не менялись. API healthy, worker работает, `meken.service` active; режим live, ИИ выключен.

Реальные HTTPS проверки прошли без отключения TLS: анонимный auth, новый поиск/деталь/lots/layouts/facets/map/sources/compare/favorites, JSON basic conversation/history, data-reports, прежний `/v1/search` и деталь квартиры. В прогоне выдача Астаны содержала 58 ЖК, карта — 57: один объект без координат не подменён точкой. Temporary profile удалён вместе с private данными; после удаления токен получает 401. Количества и состав источника относятся к этому прогону и меняются при обновлении.

Отчёты без секретов и пользовательских строк: `artifacts/deployment/catalog-20261005-124213/deployment-report.json`, `rehearsal-report.json`, `live-api.json`. Release на физический iPhone установлен ранее с этим production URL; автоматический повторный запуск после обновления заблокирован iOS до разблокировки устройства.

### Совместимый откат кода

Сохранён `/opt/meken/releases/catalog-20261005-124213-rollback`, image `meken-api:catalog-20261005-124213-rollback` (`sha256:69305e86eca5cd2153ca2a562af77bb680c3eb2b80a38b60baefccd01fdf733f`). Это прежний runtime `r2` с migration files `0007–0009`; его head проверка принимает новую additive схему. Startup/readiness на восстановленной схеме `0009` проверен. Откат кода сохраняет новые таблицы и данные, но временно убирает новые возможности API.

При необходимости выставить только `MEKEN_IMAGE_TAG=catalog-20261005-124213-rollback`, атомарно направить `current` на этот rollback path и пересоздать только API/worker через оба Compose-файла с `--no-deps`, затем проверить внутренний readiness и HTTPS. Не использовать обычный `r2` с head `0006`. Не выполнять downgrade `0009` автоматически: он удаляет новые private данные; восстановление backup требует отдельного остановленного, проверенного процесса.
