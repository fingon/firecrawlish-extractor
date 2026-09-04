IMAGE_NAME ?= firecrawlish-extractor:local
CONTAINER_RUNTIME ?= docker

.PHONY: lint test build image smoke

lint:
	uv run --locked prek run --all-files

test:
	uv run --locked pytest

build:
	uv build

image:
	$(CONTAINER_RUNTIME) build --tag $(IMAGE_NAME) .

smoke: image
	container_id=$$($(CONTAINER_RUNTIME) run --detach --publish 127.0.0.1:3002:3002 $(IMAGE_NAME)); \
	trap '$(CONTAINER_RUNTIME) rm --force "$$container_id" >/dev/null' EXIT; \
	for attempt in $$(seq 1 30); do \
		if curl --fail --silent http://127.0.0.1:3002/healthz; then \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	$(CONTAINER_RUNTIME) logs "$$container_id"; \
	exit 1
