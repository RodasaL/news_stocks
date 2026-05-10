.PHONY: dev setup prod

# Instala dependências (Python venv + npm)
setup:
	python -m venv .venv
	.\.venv\Scripts\pip install -q -r backend\requirements.txt
	npm --prefix frontend install --silent

# Lança backend + frontend em paralelo
dev:
	npm run dev

# Build e lança com Docker
prod:
	docker-compose up --build
