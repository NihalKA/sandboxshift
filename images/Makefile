# images/Makefile — Build and push SandboxShift V1 runtime images.
#
# Usage:
#   make build-all           Build all three V1 runtime images locally (native arch)
#   make build-python        Build only runtime-python:3.11
#   make build-node          Build only runtime-node:20
#   make build-multi         Build only runtime-multi:latest
#   make push-all            Push all three images to IMAGE_REGISTRY
#
# Platform:
#   Defaults to the host native architecture so local images run without emulation.
#   Override to linux/amd64 when building for AWS Fargate (ECS requires x86_64):
#     PLATFORM=linux/amd64 make build-all
#     PLATFORM=linux/amd64 make push-all
#
# Override the registry for private ECR or GHCR:
#   IMAGE_REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com make push-all
#
# Note: push-all requires prior `podman login` (or equivalent) credential setup.

IMAGE_REGISTRY ?= docker.io
IMAGE_PREFIX   ?= sandboxshift

# Auto-detect host arch: arm64 on Apple Silicon, amd64 on x86_64 Linux.
# uname -m returns 'arm64' on macOS M-series and 'x86_64' on Intel/AMD.
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),x86_64)
  HOST_ARCH := amd64
else ifeq ($(UNAME_M),aarch64)
  HOST_ARCH := arm64
else
  HOST_ARCH := $(UNAME_M)
endif

PLATFORM ?= linux/$(HOST_ARCH)

PYTHON_TAG := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-python:3.11
NODE_TAG   := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-node:20
MULTI_TAG  := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-multi:latest

.PHONY: build-all build-python build-node build-multi push-all

build-python:
	podman build --platform $(PLATFORM) -t $(PYTHON_TAG) ./python

build-node:
	podman build --platform $(PLATFORM) -t $(NODE_TAG) ./node

build-multi:
	podman build --platform $(PLATFORM) -t $(MULTI_TAG) ./multi

build-all: build-python build-node build-multi

push-all: build-all
	podman push $(PYTHON_TAG)
	podman push $(NODE_TAG)
	podman push $(MULTI_TAG)
