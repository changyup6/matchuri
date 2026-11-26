FROM jenkins/jenkins:lts-jdk17

USER root

# Jenkins 공식 이미지에는 docker CLI가 없다.
# 파이프라인에서 호스트 Docker를 조종하려면 CLI가 필요하므로 얹어준다.
RUN apt-get update && apt-get install -y ca-certificates curl \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && chmod a+r /etc/apt/keyrings/docker.asc \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y docker-ce-cli docker-compose-plugin \
 && rm -rf /var/lib/apt/lists/*
