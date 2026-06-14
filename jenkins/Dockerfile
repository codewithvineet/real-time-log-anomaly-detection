# Start from official Jenkins LTS
FROM jenkins/jenkins:lts

# Switch to root so we can install packages
USER root

# Install Docker CLI + kubectl + curl + python3
RUN apt-get update && apt-get install -y \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (client only — the daemon comes from the host via socket)
RUN curl -fsSL https://get.docker.com | sh

# Install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/kubectl

# Add jenkins user to the docker group so it can talk to the socket
# (group ID 999 is typical for docker on Linux; Mac Docker Desktop uses a different path)
RUN groupadd -f docker && usermod -aG docker jenkins

# Stay as root — this avoids Docker socket permission issues on Mac
# In a real production Jenkins you would NOT do this
USER root