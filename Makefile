.PHONY: install test run bench bench-mock load dashboard up down image
install:     ; pip install -r requirements-dev.txt
test:        ; pytest -q
run:         ; GREENROUTE_MOCK_BEDROCK=true uvicorn app.main:app --reload
bench:       ; python -m benchmark.run_benchmark
bench-mock:  ; GREENROUTE_MOCK_BEDROCK=true python -m benchmark.run_benchmark
load:        ; python scripts/loadgen.py --rps 2 --minutes 10
dashboard:   ; python scripts/build_dashboard.py
up:          ; docker compose up --build -d
down:        ; docker compose down
image:       ; docker build -t greenroute:0.1.0 .
