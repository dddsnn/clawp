FROM ubuntu:26.04
ARG CLAWP_ROOT=/app/clawp_files
ARG UID=2000
ARG GID=2000

RUN apt-get update && apt-get install -y openssh-server && apt-get clean
# Set SSH configuration to only allow public key authentication.
RUN sed -i 's/^.*PubkeyAuthentication.*$/PubkeyAuthentication yes/' \
        /etc/ssh/sshd_config \
    && sed -i 's/^.*PasswordAuthentication.*$/PasswordAuthentication no/'\
        /etc/ssh/sshd_config
# Create the clawp user for the gateway with the same uid/gid to make sure no
# agent user is assigned the same one accidentally.
RUN groupadd --gid ${GID} clawp
RUN useradd --uid ${UID} --home-dir ${CLAWP_ROOT} --no-create-home \
        --shell /bin/bash --no-user-group --gid clawp clawp
# Install any extra packages.
ARG EXTRA_PACKAGES
RUN if [ -n "${EXTRA_PACKAGES}" ] ; then \
    apt-get update && apt-get install -y ${EXTRA_PACKAGES} && apt-get clean ; \
fi
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=serversideup/github-cli:latest /usr/local/bin/gh /usr/local/bin/
# Copy our scripts.
COPY command_lib.bash /scripts/lib/
COPY generate_keys.sh /scripts/
RUN chmod +x /scripts/generate_keys.sh
COPY init_permissions.bash /scripts/
RUN chmod +x /scripts/init_permissions.bash
COPY command_wrapper.bash /usr/local/bin/
RUN chmod +x /usr/local/bin/command_wrapper.bash
EXPOSE 22
# -D starts SSH in the foreground so the container stays alive.
CMD ["/usr/sbin/sshd", "-D"]
