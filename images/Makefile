# images/Makefile — Build and push SandboxShift V1 runtime images.
#
# Usage:
#   make build-all           Build all three V1 runtime images locally
#   make build-python        Build only runtime-python:3.11
#   make build-node          Build only runtime-node:20
#   make build-multi         Build only runtime-multi:latest
#   make push-all            Push all three images to IMAGE_REGISTRY
#
# Override the registry for private ECR or GHCR:
#   IMAGE_REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com make push-all
#
# Note: push-all requires prior `podman login` (or equivalent) credential setup.
#
# --platform linux/amd64 is explicit — Chainguard images are multi-arch but
# AWS Fargate ECS tasks require linux/amd64.

IMAGE_REGISTRY ?= docker.io
IMAGE_PREFIX   ?= sandboxshift

PYTHON_TAG := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-python:3.11
NODE_TAG   := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-node:20
MULTI_TAG  := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-multi:latest

.PHONY: build-all build-python build-node build-multi push-all

build-python:
	podman build --platform linux/amd64 -t $(PYTHON_TAG) ./python

build-node:
	podman build --platform linux/amd64 -t $(NODE_TAG) ./node

build-multi:
	podman build --platform linux/amd64 -t $(MULTI_TAG) ./multi

build-all: build-python build-node build-multi

push-all: build-all
	podman push $(PYTHON_TAG)
	podman push $(NODE_TAG)
	podman push $(MULTI_TAG)
