# Benchmark & Observability Report — Day 14

> Mọi con số dưới đây lấy từ `reports/*.json` (load test, eval), `benchmark-results/scale_*_stats.csv` (Locust), Prometheus/Jaeger API và log JSON của agent ở lần chạy ngày 2026-11-25. Không có số nào ước lượng.

**Họ tên:** Nguyen Le Hai Nam
**Ngày chạy:** 2026-11-25
**Môi trường:** MacBook Pro Apple M1 Pro (8 CPU, 16 GB RAM), Docker Desktop (Engine 29.3.0, cấp 8 CPU / 8 GB), Python 3.11.18. Agent develop chạy trực tiếp trên máy (1 process uvicorn). Stack production chạy trong Docker, mỗi agent giới hạn 0.5 CPU / 256 MB.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Mock LLM mất ~120–180 ms + retrieval ~20 ms, p95 lúc bình thường đo được 216–238 ms (bảng 2.1, 2.2), nên 500 ms chừa hơn 2× headroom. Đây cũng là ngưỡng mặc định của `load_test.py`. Request chậm bất thường (~1.7 s) do upstream LLM được phép rơi vào 5 % còn lại. Alert hạ tầng (`AgentHighLatencyP95`) đặt ở 1 s để chỉ báo động khi vi phạm rõ ràng và kéo dài 2 phút. |
| Error rate `/ask` | < 2 % | Mock LLM có ~1 % lỗi upstream (`MOCK_ERROR_RATE=0.01`); đo được 0.33–2.00 %. 2 % ≈ gấp đôi mức nền, vượt nghĩa là có sự cố thật. Alert critical (`AgentHighErrorRate`) đặt ở 5 %. |
| Accuracy trên golden dataset | ≥ 85 % (gate CI); mục tiêu phát hành = 100 % và 0 regression | 85 % đủ chặn bản "không retrieval" như v1 (4.3 %). Sau khi sửa KB, v2 đạt 23/23, nên gate cuối dùng `--min-accuracy 1.0 --baseline` để không cho câu nào tụt. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency C --out reports/load_v2_cC.json`

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.13 | 193.9 | 229.7 | 1709.0 | 1.00 % | ✅ PASS |
| 10 | 36.04 | 192.7 | 224.7 | 1689.7 | 0.33 % | ✅ PASS |
| 50 | 119.3 | 196.2 | 235.6 | 1717.4 | 2.00 % | ✅ PASS (đúng ngưỡng 2.0 %) |
| 100 | 52.69 | 372.1 | 3620.2 | 3973.5 | 0.67 % | ❌ FAIL (p95) |

**Nhận xét:** Từ 1 → 10 → 50, RPS tăng gần tuyến tính (4.1 → 36 → 119; ×8.7 rồi ×3.3) trong khi p50 gần như không đổi (~193–196 ms). Agent là async: `await asyncio.sleep` mô phỏng LLM nên event loop không bị chặn và phục vụ song song được, throughput ≈ concurrency / latency. Ở mức 50, RPS đã thấp hơn lý thuyết (50 / 0.245 s ≈ 204) vì chỉ có 300 request nên phần khởi động/kết thúc chiếm tỉ lệ lớn (tổng 2.51 s). Tới **100 concurrency thì RPS giảm còn 52.7 và p50 tăng lên 372 ms, p95 3.6 s**. Một process uvicorn (1 worker, 1 CPU core) bắt đầu xếp hàng: accept kết nối, parse JSON, serialize, ghi metrics đều chạy trên cùng một event loop. Request chờ trong hàng đợi chứ không chờ LLM. Load generator cũng chạy trên cùng máy nên tranh CPU. Vì vậy throughput **không** tăng tuyến tính mãi: khi qua điểm bão hoà của một process, thêm concurrency chỉ làm latency tăng. Muốn đi xa hơn phải scale ngang (nhiều worker/instance).

### 2.2 Tail latency

Lệnh: khởi động lại v2 với `MOCK_SLOW_RATE=<x>`, rồi `load_test.py --requests 300 --concurrency 20 --out reports/load_v2_slow_<x>.json`

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 189.2 | 216.5 | 224.2 |
| 0.03 (mặc định) | 251.6 | 237.7 | 1705.0 |
| 0.1 | 374.0 | 1694.2 | 1715.8 |

**Nhận xét:** **p99 nhạy nhất.** Chỉ cần 3 % request chậm là p99 nhảy từ 224 ms lên 1705 ms (×7.6), trong khi avg chỉ tăng 189 → 252 ms (+33 %) và p95 gần như không đổi (217 → 238 ms), vì 3 % < 5 % nên request chậm chưa chạm tới p95. Lên 10 % request chậm thì p95 cũng nhảy lên 1694 ms (vi phạm SLO 500 ms), còn avg mới 374 ms, vẫn "trông ổn" so với ngưỡng. SLO không nên đặt theo average vì avg trộn nhiều request nhanh với ít request rất chậm: 1 người dùng trong 10 phải chờ 1.7 s mà avg vẫn dưới 400 ms. Percentile (p95/p99) mô tả trải nghiệm của nhóm người dùng tệ nhất, là thứ người dùng thực sự cảm nhận và phàn nàn.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | 100 % (23/23) |
| Avg tokens / câu | 57.6 | 109.3 | 116.7 |
| Tổng cost (USD) | 0.000411 | 0.000764 | 0.000817 |
| Cost / câu đúng (USD) | 0.00041055 | 0.00003639 | 0.00003553 |
| Quality gate | FAIL (gate 80 %) | PASS (gate 80 %) | PASS (gate 100 %, baseline `eval_v2.json`, 0 regression) |

File: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`. Lệnh cuối:
`python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, `Câu được cải thiện : 2`, `Câu bị regression : 0`, exit code 0.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| security-02 ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | Thêm entry KB kích hoạt bởi `jwt` / `api key` / `json web token`: API key là chuỗi bí mật cố định, không tự hết hạn; JWT là token tự chứa (user, role, `exp`) kèm **chữ ký** số, server chỉ cần kiểm chữ ký, token **hết hạn** sau thời gian ngắn |
| scaling-03 ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry KB kích hoạt bởi `hpa` / `kubernetes` / `autoscal` / `horizontal pod`: HPA tăng giảm số pod dựa trên **CPU**, **memory** hoặc **custom metrics** (request/giây, độ dài hàng đợi) |

Sửa ở `06-lab-complete/benchmark-observability/develop/utils/mock_llm.py` (thêm 2 entry cuối `KNOWLEDGE_BASE`, không sửa `golden_dataset.jsonl`), rồi tắt và bật lại v2. Chép y hệt sang `production/utils/mock_llm.py` để stack Docker cũng trả lời đúng. Trigger keyword được chọn để không trùng câu hỏi khác, nên không có regression.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

v2 thêm bước retrieval, nhét tài liệu vào prompt nên **tốn gấp ~1.9× token mỗi câu** (57.6 → 109.3) và **tổng cost gấp ~1.9×** (0.000411 → 0.000764). Latency cũng nhỉnh hơn: eval p50 169.3 → 195.9 ms, gồm ~20 ms retrieval và prompt dài hơn. Bù lại accuracy tăng từ 4.3 % lên 91.3 %. Tính theo **cost / câu đúng** thì v2 rẻ hơn **~11 lần** (0.00041 → 0.000036 USD). Đây là chỉ số đúng để so hai phiên bản: tiền trả cho câu trả lời sai là tiền bỏ đi. Sửa KB tăng thêm ~7 token/câu nhưng cost / câu đúng còn giảm tiếp (0.0000364 → 0.0000355).

**Job CI (Exercise 7.3):** `.github/workflows/eval.yml` chạy trên mỗi PR: bật agent v2 (`MOCK_ERROR_RATE=0` để không flaky), chạy
`python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.
`run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có câu regression so với baseline, nên step fail, job fail. Bật branch protection "Require status checks to pass" với check `quality-gate / eval` thì PR không merge được. (Chứng minh exit code: eval v1 in `Quality gate ... ❌ FAIL`, exit 1; eval v2_fixed exit 0.)

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` (agent ×2 + Nginx :8080 + Prometheus + Grafana + Jaeger + Locust). Unit test alert rule: `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png). Lúc chụp: Traffic 18.6 req/s, Error rate 1.08 %, Latency p95 296.35 ms, In-flight 6, Instances up 2.
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png). Span `chat mock-llm-small` có `error.type=upstream_unavailable`, event `exception` với `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`.
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png). Nhóm `agent-slo` hiển thị **FIRING (2)**: `AgentHighErrorRate` (value 0.203) và `AgentHighLatencyP95`.

**Trả lời Exercise 7.4** (Locust 30 user, 2 instance, đo bằng Prometheus lúc 08:51:44Z):

1. **p95 = 295 ms, avg = 241 ms.** p95 chỉ cao hơn avg ~55 ms nên phần lớn request đều đặn quanh 200–300 ms. Đuôi chậm (~1.7 s, 3 % request) không lộ ra ở p95 mà ở p99: trên panel Latency, đường p99 nằm ~1.8 s trong khi p50/p95/avg dưới 300 ms. Khoảng cách avg ↔ p99 chính là tail latency.
2. **Nginx chia đều:** `sum by (instance)(rate(http_requests_total{route="/ask"}[1m]))` = 9.27 req/s (172.26.0.4) và 9.38 req/s (172.26.0.8). Khi scale 3, ba instance nhận 2438 / 2380 / 2402 request trong 2 phút.
3. **Input tokens/s (1447) > output tokens/s (765)** vì input = system prompt + tài liệu retrieve (RAG) + câu hỏi, còn output là câu trả lời ngắn. v2 nhét context vào prompt nên input gấp ~1.9× output. Khi chuyển sang v1 (không retrieval) cả hai giảm còn 685 / 373 tokens/s.
4. `curl http://localhost:8080/metrics` → **HTTP 404**. Nginx cố ý không public `/metrics` ra ngoài, vì metrics lộ thông tin nội bộ (route, version, cost) và tốn tài nguyên khi bị bot gọi. Prometheus scrape trực tiếp từng agent trong network nội bộ `obs_net` qua `dns_sd_configs`, không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Tải nền: Locust headless 30 user (`-u 30 -r 5`), 2 instance agent. Thời gian đo bằng script poll `GET /api/v1/alerts` mỗi 2 s, tính từ lúc `docker compose up -d agent` (hoặc `docker pause`) trả về.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (pending +26.7 s → **firing +87.0 s**, value 0.203) | **87 s** (≈ 26 s để rate 1m vượt 5 % + `for: 1m`) | Golden Signals → Error rate; "Requests/s theo status code" (503 tăng); "LLM calls theo kết quả" (error) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (pending +20.7 s → **firing +141.4 s**) | **141 s** (`for: 2m`) | Latency /ask p50/p95/p99 (p95 vượt 1 s); LLM latency p95 theo model |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (pending +12.8 s → **firing +133.5 s**) | **134 s** (`for: 2m`) | Cost burn rate (USD/giờ): 2.41 → 34.36 USD/h (rate 1m, ~14×); p95 296 → 697 ms |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (pending +10.6 s → **firing +40.8 s**), `up{instance="172.26.0.4:8000"}=0` | **41 s** (≈ 10 s scrape + `for: 30s`) | Golden Signals → Instances up (2 → 1); Requests/s theo instance. `AgentHighErrorRate` **không** firing: request bị kẹt ở instance đang pause chỉ timeout tại Nginx, không vào metric của agent |
| E | `AGENT_VERSION=v1` | **Không có alert mới** (theo dõi 150 s) | — | Retrieval hit ratio mất, Tokens/s giảm (input 1413 → 685, output 747 → 373), bảng "Phiên bản đang chạy" đổi v2 → v1. Error rate 0.86 %, p95 198 ms: hạ tầng "xanh" |

Ghi chú trung thực:

- A và B được áp dụng **cùng lúc** (một lần sửa `.env` lúc 08:54:47Z), mỗi alert có mốc thời gian riêng như trên.
- `LLMCostBurnRateHigh` đã firing **từ trước thí nghiệm** (08:40:22Z) trong lúc chạy benchmark scale 100 user. Ngay cả tải nền 30 user với model nhỏ cũng đốt ~4.2 USD/h (đo 08:51:44Z), vượt ngưỡng 2 USD/h. Với C, mình đợi alert về `inactive` (sau khi trả `.env` về mặc định) rồi mới đổi model, nên mốc +133.5 s là do C. Ngưỡng 2 USD/h nên chỉnh theo traffic thật (ví dụ so với baseline × 3) để không báo động giả.
- Trong E, `LLMCostBurnRateHigh` vẫn hiện firing vì cửa sổ `rate(...[5m])` còn chứa chi phí model lớn của C. Đó không phải do E. `AgentInstanceDown` chỉ `pending` thoáng qua lúc recreate container (không firing).

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Lỗi **chất lượng** không làm metric hạ tầng đỏ: latency còn giảm, error rate không đổi. Cách bắt:

1. **Eval trước khi deploy (quality gate trong CI):** golden dataset + `run_eval.py --min-accuracy ... --baseline ...` chặn merge. v1 ra 4.3 % thì bị chặn ngay.
2. **Eval liên tục trên production (canary / synthetic):** định kỳ (vd 5 phút) gửi một mẫu câu trong golden dataset tới endpoint thật, xuất metric `agent_eval_accuracy` lên Prometheus và đặt alert khi giảm.
3. **Metric chất lượng gián tiếp:** retrieval hit ratio (rớt về 0 khi E), tokens/câu, độ dài câu trả lời, tỉ lệ câu trả lời "generic"; alert khi lệch mạnh so với baseline. Thêm cảnh báo khi `agent_info{version}` đổi ngoài lịch release.
4. **Tín hiệu từ người dùng:** thumbs up/down, tỉ lệ hỏi lại; LLM-as-judge chấm một mẫu ngẫu nhiên các trace.

### 4.3 Debug bằng trace

Trace chậm nhất trong 8 phút tải nền (chọn request `/ask` 200 có `ms` lớn nhất trong log JSON, rồi mở trong Jaeger):

- `trace_id`: `37ad251329b1f07b2eaa64d9d854369e`
- Tổng thời gian: **1795.5 ms** (span `POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (1772.5 ms, 98.7 %)**. `agent.retrieve_context` chỉ 20.6 ms. Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=74`, `gen_ai.usage.output_tokens=37`. Kết luận: chậm do upstream LLM (tail latency của provider), không phải do code agent hay retrieval.
- Dòng log tương ứng (`grep 37ad251329b1f07b2eaa64d9d854369e`):

```json
{"ts": "2026-11-25T08:51:57.137692+00:00", "level": "INFO", "service": "observable-agent", "instance": "a392472baa5f", "event": "llm_call", "trace_id": "37ad251329b1f07b2eaa64d9d854369e", "span_id": "f0b23d2715b093f8", "request_id": "48359bfd6cf6", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 1673.2}
{"ts": "2026-11-25T08:51:57.138092+00:00", "level": "INFO", "service": "observable-agent", "instance": "a392472baa5f", "event": "http_request", "trace_id": "37ad251329b1f07b2eaa64d9d854369e", "span_id": "f0b23d2715b093f8", "request_id": "48359bfd6cf6", "method": "POST", "route": "/ask", "status": 200, "ms": 1795.0}
```

Trace lỗi (ảnh `jaeger-error-trace.png`): `trace_id` `8ffab58ca36bfa8252323882e83d8623`, `POST /ask` 119.2 ms → 503. Span `chat mock-llm-small` 86.9 ms có status error và event `exception`. Log tương ứng:

```json
{"ts": "2026-11-25T08:51:58.593258+00:00", "level": "INFO", "service": "observable-agent", "instance": "e1a060734ccb", "event": "llm_error", "trace_id": "8ffab58ca36bfa8252323882e83d8623", "span_id": "844e0c40adbcfd6e", "request_id": "084adda6608b", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T08:51:58.594308+00:00", "level": "INFO", "service": "observable-agent", "instance": "e1a060734ccb", "event": "http_request", "trace_id": "8ffab58ca36bfa8252323882e83d8623", "span_id": "844e0c40adbcfd6e", "request_id": "084adda6608b", "method": "POST", "route": "/ask", "status": 503, "ms": 118.0}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh (mỗi lần kiểm tra số container agent trước và sau khi chạy):
`docker compose -p k5-day14-observability up -d --scale agent=N` rồi
`docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`

Số liệu: dòng `POST /ask` trong `benchmark-results/scale_N_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.80 | 200 | 240 | 1700 | 82 / 7139 (1.15 %) |
| 3 | 59.76 | 200 | 230 | 1700 | 72 / 7135 (1.01 %) |

**Giải thích kết quả:** Scale 1 → 3 **gần như không cải thiện**: RPS giữ nguyên ~59.8, p95 240 → 230 ms, p99 vẫn 1700 ms. Hai lý do:

1. **Tải bị giới hạn bởi mô hình người dùng, không phải năng lực server.** Locust là closed-loop: mỗi user chờ `between(0.5, 2)` s (trung bình 1.25 s) giữa các request. 100 user × 1 / (1.25 s + ~0.24 s) ≈ 67 req/s tổng, khớp với ~66 req/s đo được (aggregated). Một instance async đã phục vụ đủ mức này, nên thêm instance không tăng RPS.
2. **Nút thắt latency nằm ở LLM upstream.** Mỗi request ~200 ms là `await` mock LLM. Trace chậm cho thấy span LLM chiếm 98.7 % thời gian, và p99 ~1.7 s là tail latency của provider (`MOCK_SLOW_RATE`). Thêm instance không làm LLM nhanh hơn, nên p50/p99 không đổi. Failure ~1 % cũng là lỗi upstream giả lập, không phải do quá tải.

Scale ngang chỉ có ích khi instance bị bão hoà CPU/event loop, như ở mức concurrency 100 của bảng 2.1. Muốn thấy khác biệt thì phải tăng số user (hoặc giảm wait time) tới khi 1 instance (0.5 CPU) bão hoà. Muốn giảm tail latency thì phải xử lý ở tầng LLM: timeout + retry/hedging, cache, model nhanh hơn.

Ghi chú: ở lần chạy đầu, `docker compose run locust` (không có `--no-deps`) đã tự kéo service `agent` về `replicas: 2` khai báo trong compose, nên cả hai lần đo đều thành 2 instance. Mình phát hiện qua `docker compose ps`, xoá kết quả đó và chạy lại với `--no-deps`. Bảng trên là lần chạy lại: đúng 1 và 3 container, và Prometheus xác nhận 3 instance nhận 2438 / 2380 / 2402 request.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Average nói dối, percentile thì không.** 3 % request chậm làm p99 tăng ×7.6 trong khi avg chỉ +33 %. SLO và alert phải theo p95/p99, và benchmark phải đọc cả đuôi phân phối.
  2. **Đo phải kiểm chứng điều kiện đo.** Lần benchmark scale đầu sai vì công cụ (`compose run`) âm thầm đổi số instance. Mô hình tải (closed-loop có wait time) quyết định con số RPS nhiều hơn server. Trước khi kết luận cần kiểm tra số instance, phân phối tải (Prometheus) và giới hạn của load generator.
  3. **Metric, trace và log bổ sung cho nhau, và đều có điểm mù.** Metric báo "có chuyện" (alert sau 40–140 s), trace chỉ ra span nào chậm (LLM 98.7 %), log JSON có `trace_id` nối hai thứ đó. Nhưng lỗi chất lượng (E) và lỗi ở tầng LB (504 khi pause, không vào metric của agent) cần eval liên tục và giám sát từ bên ngoài.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Quality:** `agent_eval_accuracy` từ synthetic eval định kỳ, alert khi < 85 %; alert khi retrieval hit ratio rớt mạnh hoặc `agent_info{version}` đổi ngoài lịch release.
  - **Tầng ngoài:** metric/alert ở Nginx (tỉ lệ 502/504, upstream response time) và synthetic check `/health` + `/ask` từ bên ngoài, để bắt các lỗi agent không tự thấy (thí nghiệm D).
  - **Cost:** ngưỡng burn rate theo baseline (vd > 3× trung bình 7 ngày) thay vì số cố định 2 USD/h (đang báo giả ở tải nền); cost theo model và theo tenant; alert dự báo vượt budget tháng.
  - **SLO burn-rate alert nhiều cửa sổ** (1h/6h) cho error budget, thay vì ngưỡng tức thời.
  - **Saturation:** CPU/memory container, event-loop lag, hàng đợi request; alert khi in-flight vượt ngưỡng (dấu hiệu sắp xếp hàng như ở concurrency 100).
  - **Upstream LLM:** tỉ lệ 429/503 theo provider, p99 LLM latency, số lần retry/timeout.
