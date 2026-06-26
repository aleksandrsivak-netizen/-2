# Деплой «Полёт вслепую» на Beget VPS/VDS

> **Важно.** Этому проекту нужен именно **VPS/VDS**, а не обычный PHP-хостинг Beget.
> Приложение использует Python, FastAPI, NumPy/SciPy, генерацию графиков и вычисления —
> на shared-хостинге без Docker и собственного Python это не запустится.

---

## 0. Что понадобится

- аккаунт Beget с доступом к разделу **VPS**;
- SSH-клиент (на Windows — встроенный `ssh`, PuTTY или Windows Terminal);
- репозиторий проекта (`git`) либо архив с файлами;
- (опционально) домен и доступ к DNS.

---

## 1. Создать VPS

В панели Beget → **VPS** → создать сервер.

Рекомендации для демо:

| Ресурс | Минимум | Комфортно |
|--------|---------|-----------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 2 GB | 4 GB |
| Диск | 20 GB SSD | 40 GB SSD |
| ОС | Ubuntu 22.04 / 24.04 | образ **с предустановленным Docker** |

Если есть готовый образ «Ubuntu + Docker» — берите его, шаг 3 можно пропустить.

---

## 2. Подключиться по SSH

```bash
ssh root@SERVER_IP
```

(пароль/ключ выдаёт панель Beget при создании сервера).

---

## 3. Установить Docker (если не предустановлен)

```bash
apt update
apt install -y ca-certificates curl gnupg

# официальный репозиторий Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Проверка:

```bash
docker --version
docker compose version
```

---

## 4. Загрузить проект

Через Git:

```bash
git clone YOUR_REPO_URL
cd blind-flight-web
```

Либо загрузить архив с локальной машины (выполнять **на своём ПК**):

```bash
scp -r ./blind-flight-web root@SERVER_IP:/root/
```

---

## 5. Создать .env

```bash
cp .env.example .env
# при необходимости отредактировать:  nano .env
```

---

## 6. Запустить проект

```bash
docker compose up -d --build
```

Сборка образа в первый раз занимает несколько минут (ставятся NumPy/SciPy/Matplotlib).

Статус контейнеров:

```bash
docker compose ps
```

---

## 7. Проверить

В браузере:

```
http://SERVER_IP
http://SERVER_IP/health
http://SERVER_IP/docs
```

Нажмите **«Запустить демонстрацию»** — должен выполниться основной сценарий
`/api/navigation/autonomous-demo`: появятся final position error, dead reckoning
error, improvement factor, confidence, режим навигации, сравнение траекторий,
облако частиц, confidence timeline, profile match и ссылки на скачивание.

> Если на сервере открыт фаервол — разрешите порт 80:
> ```bash
> ufw allow 80/tcp && ufw allow 22/tcp && ufw enable
> ```

---

## 8. Привязать домен

1. В DNS-зоне домена (панель Beget или регистратор) добавьте **A-запись**:
   `@` → `SERVER_IP`, при желании `www` → `SERVER_IP`.
2. Дождитесь обновления DNS (от нескольких минут до часов).
3. В `nginx/nginx.conf` замените:
   ```nginx
   server_name _;
   ```
   на:
   ```nginx
   server_name your-domain.ru www.your-domain.ru;
   ```
4. Перезапустите прокси:
   ```bash
   docker compose restart nginx
   ```

---

## 9. SSL / HTTPS

Три варианта по возрастанию надёжности:

**А. Временно HTTP** — для короткой хакатонной демонстрации достаточно `http://`.

**Б. Cloudflare (просто и быстро)** — направьте домен через Cloudflare, включите
режим SSL *Flexible/Full*. Сертификат выдаёт Cloudflare, на сервере менять ничего не нужно.

**В. Let's Encrypt через certbot (полноценный HTTPS).** Самый чистый способ —
поднять отдельный контейнер с certbot или установить certbot на хост и добавить в
`nginx.conf` секцию `listen 443 ssl;` с путями к сертификатам. Кратко на хосте:

```bash
apt install -y certbot
docker compose stop nginx                 # освободить :80
certbot certonly --standalone -d your-domain.ru -d www.your-domain.ru
# сертификаты появятся в /etc/letsencrypt/live/your-domain.ru/
```

Затем смонтируйте `/etc/letsencrypt` в контейнер nginx и добавьте 443-серверный блок
(см. документацию nginx). После — `docker compose up -d nginx`.

---

## 10. Логи и диагностика

```bash
docker compose logs -f backend      # логи API/ядра
docker compose logs -f nginx        # логи прокси
docker stats                        # потребление CPU/RAM
```

Healthcheck бэкенда:

```bash
curl -s http://localhost/health
```

---

## 11. Обновление версии

```bash
cd blind-flight-web
git pull
docker compose up -d --build
```

---

## 12. Остановка и очистка

```bash
docker compose down            # остановить
docker compose down -v         # остановить и удалить тома
docker system prune -af        # освободить место (осторожно — удалит неиспользуемые образы)
```

---

## 13. Частые проблемы

| Симптом | Причина | Решение |
|--------|---------|---------|
| `http://SERVER_IP` не открывается | закрыт порт 80 | `ufw allow 80/tcp` |
| `502 Bad Gateway` | бэкенд ещё собирается/упал | `docker compose logs -f backend` |
| Долгая первая сборка | компиляция NumPy/SciPy | это нормально, подождите |
| Нет места на диске | образы/кэш | `docker system prune -af` |
| Демонстрация работает, но без картинок | бэкенд не отдал артефакты | фронтенд покажет canvas-fallback автоматически |

---

Готово. Дашборд доступен по адресу сервера, а после привязки домена — по доменному имени.
