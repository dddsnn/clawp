FROM ubuntu:26.04
ARG CLAWP_ROOT=/app/clawp_files
ARG UID=2000
ARG GID=2000

RUN apt-get update && apt-get install -y \
    openssh-server \
    && apt-get clean
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
COPY generate_keys.sh /scripts/
COPY command_wrapper.bash /usr/local/bin/
COPY command_lib.bash /scripts/lib/
RUN chmod +x /usr/local/bin/command_wrapper.bash
EXPOSE 22
# -D starts SSH in the foreground so the container stays alive.
CMD ["/usr/sbin/sshd", "-D"]
