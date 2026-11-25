# Deployment Information

## Public URL

https://nguyenlehainam-2a202603699-production.up.railway.app

## Platform

Railway. Deploy thư mục `06-lab-complete/` (agent Part 6) bằng `railway.toml` (builder `DOCKERFILE`, `healthcheckPath = "/health"`), kèm một service Redis trong cùng project.

## Cách deploy

```bash
cd 06-lab-complete
railway login
railway link -p NguyenLeHaiNam-2A202603699   # project đã tạo sẵn trên dashboard, có 1 service cùng tên
railway add --database redis                 # service Redis
railway variables --service NguyenLeHaiNam-2A202603699 --skip-deploys \
  --set 'REDIS_URL=${{Redis.REDIS_URL}}' \
  --set ENVIRONMENT=production \
  --set RATE_LIMIT_PER_MINUTE=10 \
  --set DAILY_BUDGET_USD=1.0 \
  --set MONTHLY_BUDGET_USD=10.0
# AGENT_API_KEY đã đặt trước trên dashboard, không ghi vào repo
railway up --service NguyenLeHaiNam-2A202603699 --ci   # build từ Dockerfile theo railway.toml
# Domain public đã có sẵn: nguyenlehainam-2a202603699-production.up.railway.app
```

## Test Commands

```bash
BASE=https://nguyenlehainam-2a202603699-production.up.railway.app
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (chạy ngày 2026-11-25, `AGENT_API_KEY` đọc từ `railway variables` vào biến shell, không in ra):

```
health 200
ready 200
ask-nokey 401
ask-key 200
```

`GET /health` trả `{"status":"ok","environment":"production"}`. Chạy thêm `python check_production_ready.py --public-url <URL trên>`: **PASS 12/12** (44/44 checks), trong đó có `Public URL: GET .../health → 200`.

## Environment Variables Set

Đặt trên Railway (service `NguyenLeHaiNam-2A202603699`), **không** ghi trong code hay repo:

- `PORT`: Railway tự cấp; Dockerfile `CMD` đọc `${PORT:-8000}`
- `REDIS_URL`: tham chiếu `${{Redis.REDIS_URL}}` của service Redis trong project
- `AGENT_API_KEY`: secret, chỉ đặt trên Railway
- `ENVIRONMENT=production`
- `RATE_LIMIT_PER_MINUTE=10`
- `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`
- `LOG_LEVEL`: mặc định `INFO`

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
