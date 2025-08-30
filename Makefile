SHELL := /bin/bash
VERSION ?= $(shell cat VERSION)

.PHONY: help build push helm-template helm-install test
help:
	@echo "Targets: build, push, helm-template, helm-install, test"

build:
	docker build -t meir25/lse-fetcher:$(VERSION) fetcher
	docker build -t meir25/lse-news:$(VERSION) news

push:
	docker push meir25/lse-fetcher:$(VERSION)
	docker push meir25/lse-news:$(VERSION)

helm-template:
	helm dependency update helm/lse-stack || true
	helm template lse helm/lse-stack -f helm/lse-stack/values.yaml --set image.tag=$(VERSION)

helm-install:
	kubectl create ns lse --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install lse helm/lse-stack -n lse -f helm/lse-stack/values.yaml --set image.tag=$(VERSION)

test:
	python -m pytest -q
