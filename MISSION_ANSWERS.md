# Day 14 Lab - Mission Answers

> Môi trường chạy: macOS (Apple M1 Pro, 8 CPU, 16 GB RAM), Docker Engine 29.3.0 (Docker Desktop, 8 CPU / 8 GB), Python 3.11.18 trong virtualenv. Mọi output dưới đây được chép từ lệnh đã chạy ngày 2026-11-25.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"`. Push lên GitHub là lộ key và mật khẩu DB ngay.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Bằng chứng trong log khi chạy: `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`.
3. **Dùng `print()` thay vì logging có cấu trúc** (dòng 33, 34, 38): không có level, timestamp hay request id, nên log aggregator không parse được.
4. **Không có config management** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500` cố định trong code, không đổi được theo môi trường.
5. **Không có health check** (ghi chú dòng 41–42): gọi `GET /health` trả `404 Not Found`, nên platform không biết lúc nào cần restart.
6. **Bind `localhost`** (dòng 51): `host="localhost"`, trong container thì bên ngoài không gọi vào được.
7. **Port cố định** (dòng 52): `port=8000` không đọc từ `PORT`, trong khi Railway/Render inject `PORT`.
8. **`reload=True`** (dòng 53): chế độ dev (theo dõi file, chạy thêm process reloader) lại để trong code chạy production.
9. **Không xử lý shutdown**: không có lifespan hay SIGTERM handler, không dọn tài nguyên.

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log server in ra secret:

```text
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
```

Bản này **chạy được nhưng chưa production-ready**: lộ secret trong log, không có `/health`, chỉ nghe trên `localhost`.

Bản production (`cp .env.example .env && python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.2,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:22:00.049034+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"What is deployment?"}'
HTTP/1.1 200 OK
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","model":"gpt-4o-mini"}
```

Log server: `Uvicorn running on http://0.0.0.0:8000`. Khi gửi SIGTERM thì in `Shutting down → Waiting for application shutdown → Application shutdown complete`, exit code 0.

### Exercise 1.3: Comparison table

| Feature | Develop | Production | Why Important? |
|---------|---------|------------|----------------|
| Config | Hardcode trong code (`DEBUG`, `MAX_TOKENS`, key) | Đọc từ env qua `config.py` (`Settings` + `.env`) | Cùng một image chạy được ở dev/staging/prod; đổi cấu hình không cần sửa code |
| Secrets | `OPENAI_API_KEY`, `DATABASE_URL` có mật khẩu ghi trong code, còn bị `print` ra log | Lấy từ env, không log secret | Code lên Git là lộ secret, phải revoke key |
| Health check | Không có (`/health` → 404) | `/health` (liveness) + `/ready` (readiness) | Platform/LB biết khi nào restart container, khi nào ngừng route traffic |
| Logging | `print()` text tự do | JSON (`{"time":..,"level":..,"msg":..}`), log sự kiện chứ không log secret | Log aggregator parse, lọc, đếm được; không rò rỉ dữ liệu nhạy cảm |
| Shutdown | Đột ngột, không có lifespan | Lifespan + SIGTERM handler, uvicorn xử lý xong request đang chạy rồi mới thoát | Deploy hay scale down không cắt ngang request của user |
| Host / Port | `localhost:8000` cố định | `0.0.0.0` + `PORT` từ env | Chạy được trong container và trên cloud, nơi platform cấp `PORT` |
| Reload / debug | `reload=True` | `reload` chỉ bật khi `DEBUG=true` | Reloader tốn tài nguyên và không ổn định khi chạy production |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho phép frontend hợp lệ gọi API |

---

## Part 2: Docker

### Exercise 2.1: Dockerfile questions (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11`, bản Python đầy đủ (Debian kèm compiler, header…), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`).
3. **Vì sao COPY `requirements.txt` trước?** Để tận dụng layer cache. Layer `pip install` chỉ build lại khi `requirements.txt` đổi. Sửa code (`app.py`) chỉ làm build lại các layer COPY phía sau, không phải cài lại dependencies. Nếu `COPY . .` trước thì mỗi lần sửa code đều phải `pip install` lại.
4. **CMD vs ENTRYPOINT:** `ENTRYPOINT` là chương trình luôn chạy, còn `CMD` là lệnh hoặc tham số mặc định và bị ghi đè khi `docker run image <cmd>`. Có cả hai thì `CMD` thành tham số cho `ENTRYPOINT`. File này chỉ dùng `CMD ["python", "app.py"]` dạng exec form, nên `python` là PID 1 và nhận signal trực tiếp.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.6,"container":true}
```

### Exercise 2.3: Multi-stage build và Image size comparison

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user `appuser` (non-root), chỉ `COPY --from=builder /root/.local` cùng source code, có `HEALTHCHECK`, chạy `uvicorn` 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa compiler, apt cache, header hay pip cache. Base là `slim` thay cho Python đầy đủ (~1 GB). Chỉ những gì cần để **chạy** được copy sang.

```text
$ docker images my-agent
REPOSITORY   TAG        SIZE
my-agent     advanced   265MB
my-agent     develop    1.67GB
```

- Develop: **1670 MB** (`docker images`)
- Production: **265 MB** (`docker images`)
- Difference: **giảm 84.1 %** ((1670 − 265) / 1670)

Ghi chú: Docker Desktop ở máy này dùng containerd image store. `docker images` hiển thị dung lượng đã giải nén trên đĩa. `docker image inspect -f '{{.Size}}'` cho kích thước nén: develop 415 378 584 B (415.4 MB), advanced 57 515 544 B (57.5 MB), giảm 86.2 %.

### Exercise 2.4: Docker Compose stack

Sơ đồ (`02-docker/production/docker-compose.yml`):

```text
           client
             │  :80 (và :443)
             ▼
      ┌─────────────┐   upstream agent_backend (keepalive, limit_req 10r/s)
      │ nginx:alpine│──────────────┐
      └─────────────┘              ▼
                         ┌──────────────────┐
                         │ agent (FastAPI)  │  không expose port ra ngoài
                         │ uvicorn :8000    │
                         └──┬────────────┬──┘
              REDIS_URL     │            │   QDRANT_URL
                            ▼            ▼
                 ┌───────────────┐  ┌────────────────┐
                 │ redis:7-alpine│  │ qdrant v1.9.0  │
                 │ (volume)      │  │ (volume)       │
                 └───────────────┘  └────────────────┘
      tất cả nằm trong network bridge "internal"; agent depends_on redis + qdrant (service_healthy)
```

Các service được start và cách giao tiếp: client chỉ vào được qua Nginx (cổng 80). Nginx reverse-proxy tới `agent:8000` theo DNS nội bộ của Compose. Agent gọi `redis:6379` và `qdrant:6333` trong network `internal`. Redis và Qdrant không mở cổng ra host.

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                           PORTS
agent     k5-day14-docker-agent   Up 1 second (health: starting)   8000/tcp
nginx     nginx:alpine            Up 1 second                      0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 12 seconds (healthy)          6333-6334/tcp
redis     redis:7-alpine          Up 12 seconds (healthy)          6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":0.4,"version":"2.0.0","timestamp":"2026-11-25T08:29:51.814223"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Header `Server: nginx` và các security header (`X-Frame-Options`…) cho thấy request đã đi qua Nginx.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment (agent Part 6)

- URL: **https://nguyenlehainam-2a202603699-production.up.railway.app** (chi tiết lệnh deploy và kết quả test trong `DEPLOYMENT.md`)
- Screenshot: [screenshots/dashboard.png](screenshots/dashboard.png), [screenshots/running.png](screenshots/running.png), [screenshots/test.png](screenshots/test.png)

### So sánh `railway.toml` và `render.yaml` (06-lab-complete/)

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| Phạm vi | Chỉ cấu hình **một service** (build + deploy). Service khác như Redis tạo trên dashboard/CLI | **Blueprint (IaC)** khai báo cả hạ tầng: web service **và** Key Value (Redis) trong một file |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir: 06-lab-complete`, `dockerfilePath` (Render đọc từ gốc repo nên phải chỉ thư mục con) |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Render tự restart, không cấu hình trong file |
| Biến môi trường / secret | Không ghi trong file, đặt bằng `railway variables` hoặc dashboard | Có thể ghi biến thường (`value:`). Secret dùng `sync: false` (điền trên dashboard) hoặc `generateValue: true` |
| Nối Redis | `REDIS_URL` tham chiếu biến của service Redis (`${{Redis.REDIS_URL}}`) đặt trên dashboard/CLI | `fromService: {type: keyvalue, property: connectionString}`, nối tự động trong file |
| PORT | Railway inject `PORT`, Dockerfile `CMD` đọc `${PORT:-8000}` | Render cũng inject `PORT`, cùng Dockerfile nên không cần cấu hình thêm |

Lý do khác nhau: Railway tách *service config* (file) khỏi *project/env* (dashboard/CLI), nên file ngắn. Render dùng Blueprint để dựng lại **toàn bộ** stack từ Git, nên file phải khai báo cả Redis và cách nối biến.

### Exercise 3.3: Cloud Run CI/CD (đọc `cloudbuild.yaml` + `service.yaml`)

Pipeline Cloud Build có 4 bước chạy nối tiếp (`waitFor`):

1. **test**: dùng `python:3.11-slim` để `pip install` rồi chạy `pytest tests/`. Test fail thì dừng luôn.
2. **build**: build Docker image, gắn 2 tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push**: đẩy mọi tag lên Container Registry.
4. **deploy**: `gcloud run deploy ai-agent` với image theo `$COMMIT_SHA` (truy vết và rollback được) ở region `asia-southeast1`; min 1 / max 10 instance, 512 Mi, 1 CPU, timeout 60 s; secret (`OPENAI_API_KEY`) lấy từ Secret Manager qua `--set-secrets`.

`service.yaml` là bản khai báo Knative tương đương: autoscaling `minScale 1` / `maxScale 10`, `containerConcurrency 80`, liveness/startup probe gọi `/health`, secret `AGENT_API_KEY` và `OPENAI_API_KEY` lấy qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

- **Check ở đâu:** dependency `verify_api_key` (`app.py` dòng 39) dùng `APIKeyHeader(name="X-API-Key")`, được inject vào `/ask` bằng `Depends`.
- **Sai key thì sao:** thiếu header → **401** "Missing API key". Có header nhưng sai → **403** "Invalid API key". (Ở Part 6 mình trả 401 cho cả hai trường hợp theo hợp đồng của script chấm.)
- **Rotate key:** key đọc từ biến môi trường `AGENT_API_KEY`. Đổi biến trên platform rồi redeploy/restart, code không đổi. Muốn rotate không downtime thì cho phép tạm 2 key (cũ và mới) trong một khoảng chuyển tiếp.

Chạy `AGENT_API_KEY=secret-key-123 python app.py`:

```text
# Không có key
$ curl -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
$ curl -i -X POST http://localhost:8000/ask -H 'X-API-Key: wrong-key' -H 'Content-Type: application/json' -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Có key
$ curl -i -X POST http://localhost:8000/ask -H 'X-API-Key: secret-key-123' -H 'Content-Type: application/json' -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

(Ghi chú: app này đọc `question` từ JSON body. Gửi qua query string thì handler `body.get(...)` lỗi 500.)

### Exercise 4.2: JWT (`04-api-gateway/production`, tài khoản `student` / `demo123`)

Luồng JWT (`auth.py`): `POST /auth/token` kiểm tra username/password → `create_token()` ký HS256 payload `{sub, role, iat, exp(60 phút)}`. Mỗi request gửi `Authorization: Bearer <token>`. `verify_token()` kiểm chữ ký và hạn, trả `{username, role}` mà không cần tra DB.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H 'Content-Type: application/json' -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."}

# Không có token
$ curl -i -X POST http://localhost:8000/ask -H 'Content-Type: application/json' -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}
```

### Exercise 4.3: Rate limiting

- **Algorithm:** sliding window log. Mỗi user có một `deque` timestamp, xoá các mốc cũ hơn 60 s rồi đếm (`rate_limiter.py`).
- **Limit:** `rate_limiter_user` 10 req/phút, `rate_limiter_admin` 100 req/phút.
- **Bypass cho admin:** `app.py` chọn limiter theo role trong JWT (`limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`), nên admin (`teacher`) dùng hạn mức 100/phút.
- Hạn chế: state nằm trong RAM của process, nên nhiều worker/instance sẽ mỗi nơi đếm riêng. Part 6 chuyển sang Redis.

Vòng lặp 20 request với token của `student` (token đã che):

```text
$ for i in {1..20}; do curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "{\"question\":\"Test $i\"}"; done
Test 1  → HTTP/1.1 200 OK  {"usage":{"requests_remaining":9,...}}
Test 2  → HTTP/1.1 200 OK  {"usage":{"requests_remaining":8,...}}
...
Test 10 → HTTP/1.1 200 OK  {"usage":{"requests_remaining":0,"budget_remaining_usd":0.000179}}
Test 11 → HTTP/1.1 429 Too Many Requests
          x-ratelimit-limit: 10
          x-ratelimit-remaining: 0
          retry-after: 59
          {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
...
Test 20 → HTTP/1.1 429 Too Many Requests  retry-after: 59
```

Tổng: 10 lần `200 OK`, 10 lần `429 Too Many Requests` (request 11–20).

```text
$ curl -i http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
HTTP/1.1 200 OK
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":288,"cost_usd":0.000179,"budget_usd":1.0,"budget_remaining_usd":0.999821,"budget_used_pct":0.0}
```

### Exercise 4.4: Cost guard implementation

Rate limit bảo vệ **tốc độ** (chống spam, chống quá tải), cost guard bảo vệ **tiền**. Một user gửi ít request nhưng mỗi request rất dài vẫn có thể đốt hết budget, nên cần cả hai.

Cách mình làm ở Part 6 (`06-lab-complete/app/cost_guard.py`):

1. **Tính cost theo token:** `cost = input_tokens/1000 × 0.00015 + output_tokens/1000 × 0.0006` (giá gpt-4o-mini). Token ước lượng ~4 ký tự/token vì mock không trả usage.
2. **Lưu trong Redis, không trong RAM:** `INCRBYFLOAT cost:daily:<YYYY-MM-DD>` và `cost:monthly:<YYYY-MM>` trong một pipeline (MULTI), kèm `EXPIRE` 2 ngày / 32 ngày. Key theo ngày/tháng nên tự "reset" khi sang ngày/tháng mới, mọi worker/instance dùng chung, restart không mất.
3. **`check_budget()` trước khi gọi LLM:** `MGET` hai key. Nếu tổng ngày ≥ `DAILY_BUDGET_USD` hoặc tháng ≥ `MONTHLY_BUDGET_USD` thì `HTTPException(402 Payment Required)`.
4. **`record_cost()` sau khi gọi LLM:** cộng cost thực của request.

Đánh đổi: check trước rồi mới ghi nên khi nhiều request đồng thời, tổng có thể vượt budget một chút (tối đa bằng số request in-flight × cost/request). Với budget theo ngày thì chấp nhận được. Muốn chặt hơn thì "giữ chỗ" cost ước tính bằng `INCRBYFLOAT` trước khi gọi LLM rồi điều chỉnh sau.

Bản mẫu `04-api-gateway/production/cost_guard.py` thì để state trong RAM (`dict`), budget theo user $1/ngày, global $10/ngày (vượt global → 503), cảnh báo ở 80 %.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

- `/health` (liveness): trả 200 khi process còn sống, kèm uptime và % memory (psutil). Fail thì platform **restart** container.
- `/ready` (readiness): trả 200 khi `_is_ready` (đã qua startup, chưa vào shutdown), 503 khi chưa sẵn sàng. Fail thì load balancer **ngừng route traffic** tới instance nhưng không restart.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:30:52.750265+00:00","checks":{"memory":{"status":"ok","used_percent":83.2}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

Ở Part 6, `/health` không phụ thuộc Redis (Redis chết không làm restart vô ích), còn `/ready` ping Redis và trả 503 khi mất Redis. Script chấm đã kiểm cả hai.

### Exercise 5.2: Graceful shutdown

App đăng ký `signal.signal(SIGTERM, handle_sigterm)` chỉ để log. Uvicorn tự xử lý SIGTERM: ngừng nhận kết nối mới, chờ request in-flight (`timeout_graceful_shutdown=30`), chạy phần shutdown của lifespan rồi thoát.

Test: gửi request rồi `kill -TERM` ngay sau 50 ms:

```text
$ curl -X POST "http://localhost:8000/ask?question=Long%20task" &  ; kill -TERM $PID
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
HTTP 200 in 3.022071s
server exit code: 0
```

Log server: `Shutting down` → `POST /ask ... 200 OK` → `🔄 Graceful shutdown initiated...` → `✅ Shutdown complete` → `Finished server process`. Request đang chạy vẫn hoàn thành rồi process mới thoát với code 0.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history trong Redis (`session:<id>` bằng `SETEX` TTL 3600 s), có `INSTANCE_ID` để thấy instance nào phục vụ. State trong RAM (`conversation_history = {}`) hỏng khi scale vì mỗi instance có memory riêng: request sau rơi vào instance khác là mất history, instance restart là mất hết. Đưa state ra Redis thì mọi instance đọc/ghi cùng một nơi, instance nào cũng phục vụ được bất kỳ request nào.

Part 6 áp dụng tương tự: rate-limit window (sorted set) và cost (counter) đều nằm trong Redis. Script chấm restart agent và vẫn bị 429.

### Exercise 5.4: Load balancing (3 instance sau Nginx, cổng 8080, `POST /chat`)

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       IMAGE                    STATUS                    PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   Up 14 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   Up 14 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             Up 14 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           Up 25 seconds (healthy)   6379/tcp

# 10 request qua Nginx — instance phục vụ
   3 instance-5df3bf redis
   4 instance-645b9a redis
   3 instance-a5ac25 redis
```

Nginx phân phối round-robin, 10 request chia 3/4/3 cho 3 instance.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 8a1fa406-ac1d-4a10-92bb-8c9b1f7ae9e8

Request 1: [instance-a5ac25]   Q: What is Docker?
Request 2: [instance-5df3bf]   Q: Why do we need containers?
Request 3: [instance-a5ac25]   Q: What is Kubernetes?
Request 4: [instance-645b9a]   Q: How does load balancing work?
Request 5: [instance-5df3bf]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-5df3bf', 'instance-a5ac25', 'instance-645b9a'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu trong cùng một session rơi vào cả 3 instance khác nhau, nhưng history đọc lại đủ 10 message (5 user + 5 assistant) vì mọi instance ghi/đọc session ở Redis chứ không ở RAM.

Thử thêm (script không tự làm): dừng một instance rồi gọi tiếp cùng session:

```text
$ docker stop k5-day14-scaling-agent-1
HTTP 200 served_by instance-a5ac25
HTTP 200 served_by instance-645b9a
HTTP 200 served_by instance-a5ac25
HTTP 200 served_by instance-645b9a
$ curl http://localhost:8080/chat/8a1fa406-.../history  → count = 18
```

Một instance chết, các request vẫn 200 (2 instance còn lại phục vụ) và history vẫn còn: 10 message cũ + 4 × 2 message mới = 18. (10 request ở 5.4 không gửi `session_id` nên thuộc session khác.) Sau đó `docker compose -p k5-day14-scaling down`.

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy đầy đủ (có Docker) trong `06-lab-complete/`:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết: `Docker: image < 500 MB — 56 MB`; `SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0`; `docker stop tắt êm — exit code 0, 0.7s`; `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`; `state nằm trong Redis — restart agent vẫn bị 429`; `Redis mất kết nối → /ready 503 (còn /health vẫn 200)`.

Tự kiểm thêm: gửi **20 request đồng thời** với `RATE_LIMIT_PER_MINUTE=5` (2 worker) thì đúng **5 × 200, 15 × 429**. Rate limiter chạy check-và-ghi trong một Lua script nguyên tử nên không bị race giữa các thread/worker.

---

## AI log

Mình dùng **Claude Code (Claude Opus)** trong VS Code cho cả bài. Mọi output và số liệu ở trên lấy từ lệnh chạy thật trên máy mình, file JSON/CSV đã commit trong `reports/` và `benchmark-results/`.

| # | Hỏi / giao gì | Nhận gì | Mình tự sửa / tự kiểm thế nào |
|---|---|---|---|
| 1 | Đọc README/RULES/CHECKPOINTS/RUBRIC, lập kế hoạch làm CP1–CP8 | Kế hoạch theo từng checkpoint, lưu ý `-p` cho Compose, Python 3.11 venv | Đối chiếu với mục *Lưu ý khi làm bài (K5)* trong README |
| 2 | Viết code Part 6: `config.py`, `redis_client.py`, `auth.py`, `rate_limiter.py`, `cost_guard.py`, `main.py`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `railway.toml`, `render.yaml` | Code theo hợp đồng trong `06-lab-complete/README.md`: Redis sorted set cho rate limit, `INCRBYFLOAT` theo ngày/tháng cho cost, log JSON bằng `JsonFormatter`, `/ready` ping Redis, graceful shutdown dựa vào uvicorn + lifespan (không đè handler SIGTERM), Dockerfile 2 stage `python:3.11-slim`, user non-root, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000}"` | Chạy `check_production_ready.py --static-only` rồi bản đầy đủ: PASS 12/12. Không sửa script chấm |
| 3 | Nhờ một agent reviewer (Claude) review diff Part 6 | 1 lỗi Lớn: rate limiter check rồi mới ghi qua 2 pipeline nên race khi nhiều request đồng thời. Vài lỗi Nhỏ: access log uvicorn dạng text, `X-Request-ID` không giới hạn, thiếu cảnh báo khi `AGENT_API_KEY` rỗng | Sửa rate limiter thành Lua script nguyên tử, thêm `--no-access-log`, lọc `X-Request-ID` (≤ 64 ký tự, `[A-Za-z0-9-]`), log cảnh báo khi key rỗng. Test 20 request đồng thời, limit 5: đúng 5×200 + 15×429. Chạy lại script chấm: vẫn PASS 12/12 |
| 4 | Chạy Part 1, 2, 4, 5 và soạn câu trả lời | Lệnh chạy, output curl, bảng so sánh, sơ đồ | Toàn bộ output là của lần chạy thật. Kích thước image lấy từ `docker images`. Token JWT đã che |
| 5 | Docker Hub trả 429 (rate limit pull ẩn danh) | Pull `python:3.11-slim`, `python:3.11`, `nginx:1.28-alpine` từ `mirror.gcr.io` và `prom/prometheus:v3.16.0` từ `quay.io`, rồi `docker tag` về tên gốc | Image giống hệt bản Docker Hub (cùng nguồn chính thức), không đổi file compose |
| 6 | Part 7 stack: agent container lỗi `ModuleNotFoundError: uvicorn` | Nguyên nhân: Dockerfile tạo user với `-d /app` nên Python tìm user-site ở `/app/.local`, còn package copy vào `/home/agent/.local`. Sửa: thêm `ENV PYTHONUSERBASE=/home/agent/.local` | Rebuild, container `healthy`, `/ask` 200 qua Nginx |
| 7 | Part 7 benchmark scale: lần chạy đầu `docker compose run locust` tự scale agent về `replicas: 2` (vì `depends_on`) nên cả scale_1 lẫn scale_3 đều thực ra chạy 2 instance | Chạy lại với `docker compose run --no-deps`, đếm số container agent trước/sau mỗi lần chạy (1 và 3), kiểm phân phối tải qua Prometheus (3 instance ~2400 req mỗi cái) | CSV đã nộp là của lần chạy lại đúng số instance. Kết quả sai lần đầu đã xoá |
| 8 | Exercise 7.3: sửa `KNOWLEDGE_BASE` cho 2 câu sai (`security-02`, `scaling-03`) | Thêm 2 entry (JWT vs API key; Kubernetes HPA) vào `develop/utils/mock_llm.py`, chép sang `production/utils/mock_llm.py` | Eval lại với `--min-accuracy 1.0 --baseline reports/eval_v2.json`: 23/23, 0 regression, gate PASS. Không sửa `golden_dataset.jsonl` |
| 9 | Exercise 7.1: thêm metric `agent_question_length_chars` | Histogram với bucket (20…2000), observe trong `/ask` | `curl /metrics` thấy `agent_question_length_chars_count 1.0`, `_sum 40.0` |
| 10 | Chụp màn hình Grafana/Jaeger/Prometheus | Script Playwright headless mở đúng trang (trace theo `trace_id` lấy từ log JSON) | Đã xem lại từng ảnh: có tải, có span chậm, có event `exception`, trang Alerts có 2 alert FIRING |
| 11 | Soạn `BENCHMARK_REPORT.md`, `DEPLOYMENT.md`, file CI `.github/workflows/eval.yml` | Bản nháp từ số liệu trong JSON/CSV | Mọi con số đối chiếu lại với `reports/*.json` và `benchmark-results/*.csv` |
| 12 | Deploy Part 6 lên Railway (CP6) | Dùng Railway CLI: link project có sẵn, thêm Redis, đặt biến (`REDIS_URL=${{Redis.REDIS_URL}}`…), `railway up` deploy `06-lab-complete/` đè lên service cũ (bản Railpack thử trước đó) | 4 lệnh curl ở CP6 trả 200/200/401/200, `check_production_ready.py --public-url` PASS 12/12. Key chỉ nằm trên Railway, không có trong repo |
