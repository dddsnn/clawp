FROM ubuntu:26.04

RUN apt-get update && apt-get install -y \
    openssh-server \
    && apt-get clean
# Set SSH configuration to only allow public key authentication.
RUN sed -i 's/^.*PubkeyAuthentication.*$/PubkeyAuthentication yes/' \
        /etc/ssh/sshd_config \
    && sed -i 's/^.*PasswordAuthentication.*$/PasswordAuthentication no/'\
        /etc/ssh/sshd_config
EXPOSE 22
# -D starts SSH in the foreground so the container stays alive.
CMD ["/usr/sbin/sshd", "-D"]
