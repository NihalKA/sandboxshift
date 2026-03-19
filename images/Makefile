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
#   Auto-detected from the Podman VM (macOS) or uname -m (Linux).
#   The Podman VM arch is what matters — NOT the macOS host arch.
#   On macOS, `podman machine init` defaults to amd64 even on Apple Silicon.
#
#   Override explicitly when needed:
#     PLATFORM=linux/amd64 make build-all   # for AWS Fargate / amd64 VM
#     PLATFORM=linux/arm64 make build-all   # for arm64 native VM
#
# Override the registry for private ECR or GHCR:
#   IMAGE_REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com make push-all

IMAGE_REGISTRY ?= docker.io
IMAGE_PREFIX   ?= sandboxshift

# Detect the Podman VM arch (macOS) or fall back to uname -m (Linux/CI).
# `podman machine info` outputs e.g. "MachineState: Running" and "Arch: amd64"
# grep + awk extracts the value; the shell returns empty string on failure.
_PODMAN_ARCH := $(shell podman machine info 2>/dev/null | grep -i "^  Arch:" | awk '{print $$2}')
ifneq ($(_PODMAN_ARCH),)
  # Running on macOS with a live Podman machine — use its arch.
  PLATFORM ?= linux/$(_PODMAN_ARCH)
else
  # Linux native or CI — use the host uname.
  _UNAME_M := $(shell uname -m)
  ifeq ($(_UNAME_M),x86_64)
    PLATFORM ?= linux/amd64
  else ifeq ($(_UNAME_M),aarch64)
    PLATFORM ?= linux/arm64
  else
    PLATFORM ?= linux/$(_UNAME_M)
  endif
endif

PYTHON_TAG := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-python:3.11
NODE_TAG   := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-node:20
MULTI_TAG  := $(IMAGE_REGISTRY)/$(IMAGE_PREFIX)/runtime-multi:latest

.PHONY: build-all build-python build-node build-multi push-all info

# Print detected platform — useful for confirming the right arch before building.
info:
	@echo "Detected PLATFORM: $(PLATFORM)"
	@echo "  (override with: PLATFORM=linux/amd64 make build-all)"

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
