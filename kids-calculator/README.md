# kids-calculator

Máy tính toán thân thiện cho trẻ em — chạy trên GreenNode AgentBase.

## Tính năng

- Tính toán an toàn: `12 + 8`, `5 × 7`, `20 ÷ 4`
- Hỗ trợ tiếng Việt: `3 cộng 5`, `10 trừ 4`
- Giải thích từng bước: `giải thích 6 + 4`
- Chơi quiz toán: gõ `quiz` hoặc `quiz kho`

## Giao diện web

Mở trình duyệt sau khi chạy server:

**http://127.0.0.1:8080/**

Giao diện có bàn phím số lớn, khung chat, nút Giải thích và Quiz — thân thiện cho trẻ em.

## Chạy local

```bash
cd kids-calculator
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

Thử nhanh (API):

```bash
curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"message": "5 cộng 3"}'

curl -X POST http://127.0.0.1:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"message": "quiz"}'
```

Health check:

```bash
curl http://127.0.0.1:8080/health
```

## Deploy

Cần IAM credentials GreenNode. Sau khi cấu hình, dùng `/agentbase-deploy` để đưa agent lên AgentBase Runtime.
