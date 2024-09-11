FROM gitpod/workspace-full:2024-09-05-09-30-51

# Install additional tools
RUN brew install tfenv awscli && \
    tfenv install 1.6.1