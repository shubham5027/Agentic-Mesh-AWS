.PHONY: build deploy logs test dashboard

build:
	sam build

deploy:
	sam deploy --no-confirm-changeset

logs:
	sam logs -t --stack-name agentic-mesh

test:
	pytest tests/

dashboard:
	python -m http.server 8080 --directory dashboard
