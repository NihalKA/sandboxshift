# images/Makefile — Build and push SandboxShift V1 runtime images.
#
# Usage:
#   make build-all           Build all three V1 runtime images locally
#   make build-python        Build only runtime-python:3.11
#   make build-node          Build only runtime-node:20
#   make build-multi         Build only runtime-multi:latest
#   make push-all            Push all three images to IMAGE_REGISTRY
#
# Platform:
#   Auto-detected from the running Podman daemon via:
#     podman info --format '{{.Host.Arch}}'
#   This returns the arch of the VM/daemon (e.g. amd64 or arm64) which is
#   what the built image must match — NOT the macOS host uname -m.
#
#   Override when needed:
#     PLATFORM=linux/amd64 make build-all   # for AWS Fargate
#     PLATFORM=linux/arm64 make build-all   # for arm64 VM
#
# Override the registry for private ECR or GHCR:
#   IMAGE_REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com make push-all

IMAGE_REGISTRY ?= docker.io
IMAGE_PREFIX   ?= sandboxshift

# Ask the running Podman daemon for its arch — this is the ground truth.
# Falls back to uname -m mapping if podman is not running (CI / Linux native).
_PODMAN_ARCH := $(shell podman info --format '{{.Host.Arch}}' 2>/dev/null)
ifneq ($(_PODMAN_ARCH),)
  PLATFORM ?= linux/$(_PODMAN_ARCH)
else
  _UNAME_M := $(shell uname -m)
  ifeq ($(_UNAME_M),x86_64)
    PLATFORM ?= linux/amd64
  else ifeq ($(_UNAME_M),aarch64)
    PLATFORM ?= linux/arm64
  else ifeq ($(_UNAME_M),arm64)
    PLATFORM ?= linux/arm64
  else
    PLATFORM ?= linux/$(_UNAME_M)
  endif
endif

PYTHON_TAG := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-python:3.11
NODE_TAG   := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-node:20
MULTI_TAG  := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-multi:latest

.PHONY: build-all build-python build-node build-multi push-all info

# Print detected platform — confirm this matches your Podman VM before building.
info:
	@echo "Podman daemon arch: $(_PODMAN_ARCH)"
	@echo "Building for PLATFORM: $(PLATFORM)"
	@echo ""
	@echo "To override: PLATFORM=linux/amd64 make build-all"

# --pull=always ensures the base image is always fetched for the correct
# platform and never reused from a stale wrong-arch local cache.
build-python:
	podman build --platform $(PLATFORM) --pull=always -t $(PYTHON_TAG) ./python

build-node:
	podman build --platform $(PLATFORM) --pull=always -t $(NODE_TAG) ./node

build-multi:
	podman build --platform $(PLATFORM) --pull=always -t $(MULTI_TAG) ./multi

build-all: build-python build-node build-multi

push-all: build-all
	podman push $(PYTHON_TAG)
	podman push $(NODE_TAG)
	podman push $(MULTI_TAG)
