FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Detroit
RUN apt-get update && apt-get install -y \
    tzdata \
    libkrb5-dev \
    python3 \
    python3-pip \
    net-tools \
    iputils-* \
    git-all \
    curl \
    wget
COPY requirements.txt requirements.txt
COPY requirements.yml requirements.yml
RUN pip3 install -r requirements.txt
RUN ansible-galaxy collection install -r requirements.yml --force
RUN echo 'alias python="python3"' >> ~/.bashrc
RUN echo 'alias pip="pip3"' >> ~/.bashrc
LABEL maintainer="Kevin Giusti <kgiusti3529@gmail.com>" \
      version="1.0"
